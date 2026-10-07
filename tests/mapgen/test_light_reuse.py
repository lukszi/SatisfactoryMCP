"""The kept light: a run that draws the same surface installs the bake a run before it kept,
and anything the bake reads that changes bakes again.

docs/spatial-and-map.md section 29, "Kept light". Synthetic fixtures throughout.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mapgen.lighting import stage
from mapgen.lighting.bake import bake_light
from mapgen.lighting.light_tiles import work_array
from mapgen.lighting.stage import LIGHT_VERSION, Surface
from mapgen.render import kept_light, light
from mapgen.render.extras import RUN_CACHE_DIRS, remove_run_caches
from mapgen.render.kept_light import KEPT_LIGHT_DIR_NAME
from mapgen.render.light import LIGHT_CACHE_DIR_NAME, LightingRun
from mapgen.render.stream import RenderStream
from mapgen.tiles.cutter import TileStream

SIZE = 512

#: The digest of ``_pinned_bake`` at each ``LIGHT_VERSION``.
BAKE_PINS = {
    1: "sha256:a3ab7907b3b9572f99b2d05344d4b5c2022e26057a88babf5536424bc68745b6",
    2: "sha256:84d0ec30c193892e4c5de03ae904c2d3ccedf8900b166a8f91c836c0e41c9482",
}

Planes = tuple[np.ndarray, np.ndarray]


def _planes(bump: float = 0.0) -> Planes:
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    z = (40 * np.exp(-((xx - 256) ** 2 + (yy - 280) ** 2) / 4000.0)).astype(np.float32)
    z[100, 100] += bump
    land = np.ones((SIZE, SIZE), np.float32)
    land[:, :64] = 0.0
    return z, land


def _occluder(height: float = 60.0) -> Planes:
    top = np.full((SIZE, SIZE), np.nan, np.float32)
    top[250:262, 240:270] = height
    return top, np.where(np.isfinite(top), 255, 0).astype(np.uint8)


def _put(surface: Surface, planes: Planes, rows: int = 64, order: int = 1, threads: int = 1):
    z, land = planes
    tops = list(range(0, SIZE, rows))[::order]
    with ThreadPoolExecutor(threads) as pool:
        list(pool.map(lambda top: surface.put(top, z[top : top + rows], land[top : top + rows]),
                      tops))  # fmt: skip


def _digest(work: Path, planes: Planes, **drawn) -> str:
    surface = Surface(work, SIZE)
    _put(surface, planes, **drawn)
    digest = surface.digest()
    surface.close()
    return digest


class _Counted:
    """A module function, counted as the light run calls it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module, name: str) -> None:
        self.count, self.real = 0, getattr(module, name)
        monkeypatch.setattr(module, name, self)

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self.real(*args, **kwargs)


def _baked(tmp: Path, cache: Path | None, planes=None, occluder=None) -> None:
    """The light alone into ``tmp/out``, its surface drawn 64 rows at a time."""
    run = LightingRun(tmp / "scratch", SIZE, occluder, light_workers=1, cache_root=cache)
    z, land = planes or _planes()
    try:
        run.begin(tmp / "out")
        for top in range(0, SIZE, 64):
            run.surface.put(top, z[top : top + 64], land[top : top + 64])
            run.drawn(top + 64)
        run.finish(lambda: None)
    finally:
        run.close()


def _installed(tmp: Path, cache: Path | None, occluder=None):
    """One lit layer into ``tmp/out/r``: the light's ``_meta`` and the layer's tiles."""
    run = LightingRun(tmp / "scratch", SIZE, occluder, light_workers=1, cache_root=cache)
    z, land = _planes()
    sheet = np.full((SIZE, SIZE, 3), 128, np.uint8)
    try:
        with TileStream(Image, 1) as cutter:
            stream = RenderStream(cutter, ("painted",), (tmp / "out", "r"), SIZE, 6, run)
            for top in range(0, SIZE, 64):
                run.surface.put(top, z[top : top + 64], land[top : top + 64])
                stream.put(top, {"painted": sheet[top : top + 64]})
            stream.finish()
            stream.install("painted")
        return run.meta, _files(tmp / "out" / "r" / "painted")
    finally:
        run.close()


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).digest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _kept_meta(cache: Path) -> dict:
    return json.loads((cache / KEPT_LIGHT_DIR_NAME / "meta.json").read_text("utf-8"))["_meta"]


def test_a_run_that_draws_the_same_surface_installs_the_kept_light(tmp_path, monkeypatch):
    bakes, cache = _Counted(monkeypatch, light, "LightBake"), tmp_path / "cache"
    first_meta, first_tiles = _installed(tmp_path / "a", cache, _occluder())
    kept = cache / KEPT_LIGHT_DIR_NAME
    assert bakes.count == 1 and (kept / "terms.npy").is_file()
    assert not (tmp_path / "a" / "scratch" / LIGHT_CACHE_DIR_NAME).exists(), "scratch still goes"

    meta, tiles = _installed(tmp_path / "b", cache, _occluder())
    assert bakes.count == 1, "the second run baked nothing"
    assert meta == first_meta and meta["key"] == _kept_meta(cache)["key"]
    a, b = tmp_path / "a" / "out" / "r", tmp_path / "b" / "out" / "r"
    assert len(_files(a / "light")) == 11 and _files(b / "light") == _files(a / "light")
    assert tiles == first_tiles, "relit by the kept terms, to the byte"
    tile = Path("tiles") / "0" / "0_0.nrm.webp"
    assert os.path.samefile(a / "light" / tile, kept / tile), "a hard link, not a copy"
    assert os.path.samefile(b / "light" / tile, kept / tile)


def test_a_run_into_the_folder_that_holds_the_kept_light_leaves_it_be(tmp_path, monkeypatch):
    bakes, cache = _Counted(monkeypatch, light, "LightBake"), tmp_path / "cache"
    links = _Counted(monkeypatch, kept_light, "_link_tree")
    _baked(tmp_path / "a", cache)
    meta = tmp_path / "a" / "out" / "light" / "meta.json"
    before = meta.read_bytes()
    _baked(tmp_path / "a", cache)
    assert (bakes.count, links.count) == (1, 1), "kept once, installed never"
    assert meta.read_bytes() == before


#: Each change a test makes to what the bake reads, and the fields of the key it moves.
CHANGES = {
    "surface": {"surface"},
    "land": {"surface"},
    "occluder": {"occluder"},
    "no occluder": {"occluder", "occluder_cover", "occluder_layers"},
    "light version": {"light_version"},
    "light model": {"model"},
}


@pytest.mark.parametrize("change", CHANGES)
def test_whatever_the_bake_reads_that_changes_bakes_again(tmp_path, monkeypatch, change):
    bakes, cache = _Counted(monkeypatch, light, "LightBake"), tmp_path / "cache"
    _baked(tmp_path / "a", cache, occluder=_occluder())
    first = _kept_meta(cache)["key"]
    planes, occluder = _planes(), _occluder()
    if change == "surface":
        planes = _planes(bump=0.001)
    elif change == "land":
        planes[1][7, 200] = 0.5
    elif change == "occluder":
        occluder = _occluder(height=61.0)
    elif change == "no occluder":
        occluder = None
    elif change == "light version":
        monkeypatch.setattr(stage, "LIGHT_VERSION", LIGHT_VERSION + 1)
    else:
        axis = stage.light_axis()
        monkeypatch.setattr(stage, "light_axis", lambda: {**axis, "digest": "sha256:other"})
    _baked(tmp_path / "b", cache, planes, occluder)
    assert bakes.count == 2
    key = _kept_meta(cache)["key"]
    assert key["digest"] != first["digest"], "the new bake is the one kept"
    assert {name for name in key if key[name] != first[name]} - {"digest"} == CHANGES[change]


def test_a_keep_cut_short_is_baked_again_and_kept_whole(tmp_path, monkeypatch):
    bakes, cache = _Counted(monkeypatch, light, "LightBake"), tmp_path / "cache"
    for name, gone in (("a", None), ("b", "meta.json"), ("c", "terms.npy"), ("d", "tiles")):
        if gone is not None:
            path = cache / KEPT_LIGHT_DIR_NAME / gone
            path.unlink() if path.is_file() else shutil.rmtree(path)
        _baked(tmp_path / name, cache)
    assert bakes.count == 4
    assert _kept_meta(cache)["key"]["surface"] == _digest(tmp_path / "digest", _planes())
    _baked(tmp_path / "e", cache)
    assert bakes.count == 4


def test_a_keep_that_fails_leaves_the_run_its_own_terms(tmp_path, monkeypatch):
    def refuse(_source, _target):
        raise PermissionError("the cache folder is read-only")

    monkeypatch.setattr(kept_light, "_move", refuse)
    meta, tiles = _installed(tmp_path / "a", tmp_path / "cache")
    assert not (tmp_path / "cache" / KEPT_LIGHT_DIR_NAME / "meta.json").exists()
    alone, tiles_alone = _installed(tmp_path / "b", None)
    assert tiles == tiles_alone, "relit by the scratch's own terms"
    assert meta is not None and alone is not None and alone["key"] == meta["key"]


def test_a_run_without_a_cache_root_keeps_nothing_and_writes_the_key(tmp_path):
    _baked(tmp_path, None)
    key = json.loads((tmp_path / "out" / "light" / "meta.json").read_text("utf-8"))["_meta"]["key"]
    assert key["light_version"] == LIGHT_VERSION and key["size_px"] == SIZE
    assert key["surface"] == _digest(tmp_path / "digest", _planes())
    assert key["occluder"] is None and key["occluder_layers"] == []
    assert not list(tmp_path.rglob(KEPT_LIGHT_DIR_NAME))


def test_the_kept_light_goes_with_the_run_s_caches(tmp_path):
    assert KEPT_LIGHT_DIR_NAME in RUN_CACHE_DIRS
    (tmp_path / KEPT_LIGHT_DIR_NAME / "tiles").mkdir(parents=True)
    assert remove_run_caches(tmp_path) == [] and not (tmp_path / KEPT_LIGHT_DIR_NAME).exists()


def test_the_surface_digest_reads_the_stored_bytes_in_row_order_whoever_put_them(tmp_path):
    planes = _planes()
    serial = _digest(tmp_path / "serial", planes)
    assert _digest(tmp_path / "reversed", planes, order=-1) == serial
    assert _digest(tmp_path / "threads", planes, threads=4) == serial
    wide = (planes[0].astype(np.float64), planes[1].astype(np.float64))
    assert _digest(tmp_path / "wide", wide) == serial, "what is stored, not what came in"
    assert _digest(tmp_path / "bumped", _planes(bump=1e-3)) != serial
    land = planes[1].copy()
    land[3, 40] = 0.99
    assert _digest(tmp_path / "land", (planes[0], land)) != serial


def test_the_surface_stores_the_bytes_it_stored_before_it_was_digested(tmp_path):
    z, land = _planes()
    land[5, 50:60] = np.linspace(-0.5, 1.5, 10, dtype=np.float32)
    surface = Surface(tmp_path, SIZE)
    for top in range(0, SIZE, 64):
        dry = np.asfortranarray(land[top : top + 64])
        surface.put(top, z[top : top + 64].astype(np.float64), dry)
    assert np.array_equal(surface.z, z)
    assert np.array_equal(surface.land, np.round(np.clip(land, 0, 1) * 255).astype(np.uint8))
    surface.close()


def _pinned_bake(tmp_path: Path) -> str:
    """The bake's byte planes and lossless tiles for one fixed surface and crown."""
    surface = Surface(tmp_path / "work", SIZE)
    _put(surface, _planes())
    bake_light(surface, tmp_path / "out", 1, _occluder(), progress=False,
               occluder_layers=["painted"])  # fmt: skip
    digest = hashlib.sha256()
    for name in ("terms", "svfh", "landh", "hzq"):
        digest.update(np.ascontiguousarray(work_array(surface.directory, name, np.uint8, "r")))
    for tile in sorted((tmp_path / "out" / "light" / "tiles").rglob("*.nrm.webp")):
        with Image.open(tile) as image:
            digest.update(np.asarray(image.convert("RGBA")).tobytes())
    surface.close()
    return "sha256:" + digest.hexdigest()


def test_a_bake_that_writes_other_bytes_bumps_light_version(tmp_path):
    """A kept light is found by its key, so a bake that writes other bytes needs a new key."""
    assert _pinned_bake(tmp_path) == BAKE_PINS[LIGHT_VERSION], (
        "the bake writes other bytes from the same surface: bump stage.LIGHT_VERSION and pin "
        "the new digest here"
    )

"""A pass's bands cut as they settle: every tree the bytes of the whole sheet cut serially, the
light baked a block row at a time while the bands still come in, and a kept bake read while
the bands drawn so far are the ones it was baked from.

docs/map/renders.md section 42. Synthetic surfaces and sheets throughout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from mapgen.lighting import bake
from mapgen.lighting.bake import bake_light
from mapgen.lighting.light_tiles import work_array
from mapgen.lighting.stage import Surface
from mapgen.palette.lightparams import shader_light
from mapgen.render.draw.kept_light import KEPT_LIGHT_DIR_NAME
from mapgen.render.draw.light import UNLIT_DIR_NAME, LightingRun, crown_layers, relight_rows
from mapgen.render.draw.stream import RenderStream
from mapgen.tiles.cutter import TileStream
from mapgen.tiles.formats import GROUND_TILES
from satisfactory_mcp.core.gameassets.pyramid import install_pyramid
from tests.support.cut import cut_whole

Image = pytest.importorskip("PIL.Image")

LAYERS = ("terrain", "painted")
RECIPE = 6
TEXT = "tools/gen_map_renders.py, {layer} recipe 6, Lanczos"

Planes = tuple[np.ndarray, np.ndarray]


def _planes(size: int, bump_row: int | None = None) -> Planes:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) * (512 / size)
    z = (40 * np.exp(-((xx - 256) ** 2 + (yy - 280) ** 2) / 4000.0)).astype(np.float32)
    if bump_row is not None:
        z[bump_row, size // 2] += 0.5
    land = np.ones((size, size), np.float32)
    land[:, : size // 8] = 0.0
    return z, land


def _occluder(size: int) -> Planes:
    top = np.full((size, size), np.nan, np.float32)
    top[size // 2 : size // 2 + 12, size // 2 : size // 2 + 30] = 60.0
    return top, np.where(np.isfinite(top), 255, 0).astype(np.uint8)


def _sheet(size: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    base = np.stack([xx * 200, yy * 180, (xx + yy) * 90], axis=-1)
    return np.clip(base + rng.normal(0, 18, (size, size, 3)), 0, 255).astype(np.uint8)


def _digests(root: Path) -> dict[str, str]:
    """Every tile and light tile under ``root``: the PNG trees and the light's WebP pairs."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.suffix in (".png", ".webp")
    }


def _streamed(
    out: Path, size: int, planes: Planes | None, cache: Path | None = None, workers: int = 1,
    rows: int = 256,
):  # fmt: skip
    """Each layer's sheet put a band at a time, the surface captured first as the draw does:
    the trees each layer installed, and the light run's rows queued as each band came in."""
    light = None
    if planes is not None:
        light = LightingRun(out / "scratch", size, _occluder(size), light_workers=1,
                            cache_root=cache)  # fmt: skip
    queued = []
    try:
        with TileStream(Image, workers, threads=2) as cutter:
            stream = RenderStream(cutter, LAYERS, (out, "r"), size, RECIPE, light)
            for top in range(0, size, rows):
                if light is not None and planes is not None:
                    light.surface.put(top, planes[0][top : top + rows], planes[1][top : top + rows])
                bands = {layer: _sheet(size, k)[top : top + rows] for k, layer in enumerate(LAYERS)}
                stream.put(top, bands)
                queued.append(sorted(light.bake.rows) if light and light.bake else [])
            stream.finish()
            trees = {layer: stream.install(layer) for layer in LAYERS}
    finally:
        if light is not None:
            light.close()
    return trees, queued


def _reference(out: Path, size: int, planes: Planes | None) -> None:
    """The same layers cut whole and serially, relit by a bake of the whole surface."""
    terms = land = None
    if planes is not None:
        surface = Surface(out / "work", size)
        surface.put(0, *planes)
        bake_light(surface, out / "r", 1, _occluder(size), progress=False,
                   occluder_layers=crown_layers())  # fmt: skip
        terms = np.array(work_array(surface.directory, "terms", np.uint8, "r"))
        land = np.array(surface.land)
        surface.close()
    for k, layer in enumerate(LAYERS):
        directory = out / "r" / layer
        sheet = _sheet(size, k)
        if terms is not None and land is not None:
            cut_whole(sheet, directory / UNLIT_DIR_NAME, GROUND_TILES)
            sheet = relight_rows(sheet, terms, land, shader_light(layer))
        lit = Image.fromarray(sheet)
        text = TEXT.format(layer=layer)
        install_pyramid(lit, Image, directory, source=text)
        install_pyramid(lit, Image, directory, 512, source=text, dir_name="tiles@2x")


@pytest.mark.parametrize("workers", [1, 3])
def test_bands_cut_as_they_settle_are_the_whole_sheet_cut_serially(tmp_path, workers):
    trees, _ = _streamed(tmp_path / "a", 512, None, workers=workers, rows=96)
    _reference(tmp_path / "b", 512, None)
    a, b = _digests(tmp_path / "a" / "r"), _digests(tmp_path / "b" / "r")
    assert len(a) == 2 * (5 + 1) and a == b
    assert trees["painted"].unlit is None and trees["painted"].tiles["count"] == 5


def test_the_lit_trees_take_each_band_once_the_light_has_its_rows(tmp_path, monkeypatch):
    """Four block rows at 1024: the first is queued once its rows and halo are drawn, long
    before the last band, and every tree and light tile is the whole bake's and cut's."""
    monkeypatch.setattr(bake, "BLOCK_TILES", 1)
    planes = _planes(1024)
    trees, queued = _streamed(tmp_path / "a", 1024, planes, workers=2)
    _reference(tmp_path / "b", 1024, planes)
    assert queued == [[], [0], [0, 1], [0, 1, 2, 3]], "a block row bakes once its rows are in"
    a, b = _digests(tmp_path / "a" / "r"), _digests(tmp_path / "b" / "r")
    assert len([name for name in a if name.endswith(".png")]) == 2 * (21 + 5)
    assert len([name for name in a if "/unlit/" in name and name.endswith(".webp")]) == 2 * 21
    assert {name for name in a if name.startswith("light/tiles")} == {
        name for name in b if name.startswith("light/tiles")
    }
    assert a == b
    for layer in LAYERS:
        assert trees[layer].unlit is not None and trees[layer].unlit["count"] == 21


def test_a_kept_bake_is_read_while_the_bands_match_and_baked_again_from_the_first_that_does_not(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bake, "BLOCK_TILES", 1)
    made = []
    real = bake.LightBake.__init__

    def counted(self, *args, **kwargs):
        made.append(True)
        real(self, *args, **kwargs)

    monkeypatch.setattr(bake.LightBake, "__init__", counted)
    cache = tmp_path / "cache"
    _streamed(tmp_path / "first", 1024, _planes(1024), cache)
    assert len(made) == 1 and (cache / KEPT_LIGHT_DIR_NAME / "surface.json").is_file()

    _, queued = _streamed(tmp_path / "same", 1024, _planes(1024), cache)
    assert len(made) == 1 and queued[-1] == [], "the same surface baked nothing"
    assert _digests(tmp_path / "same" / "r") == _digests(tmp_path / "first" / "r")

    bumped = _planes(1024, bump_row=600)
    _, queued = _streamed(tmp_path / "bumped", 1024, bumped, cache)
    assert len(made) == 2
    assert queued[1] == [] and queued[2] == [0, 1], "baked from the band that differed"
    _streamed(tmp_path / "fresh", 1024, bumped, None)
    assert _digests(tmp_path / "bumped" / "r") == _digests(tmp_path / "fresh" / "r")
    assert _digests(tmp_path / "bumped" / "r") != _digests(tmp_path / "first" / "r")


def test_a_block_row_reads_to_its_end_and_twice_the_horizon_s_reach_past_it():
    block, reads = bake.block_rows(32768)
    assert block == 4096 and len(reads) == 8
    assert reads[0] == 4096 + 660 and reads[-2] == 7 * 4096 + 660 and reads[-1] == 32768
    assert bake.block_rows(2048) == (2048, [2048]), "one block: the bake waits for the draw"

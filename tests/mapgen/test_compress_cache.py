"""``python -m mapgen compress-cache``: a target on the source, interrupted runs, the report.

docs/spatial-and-map.md section 39. Synthetic raw caches in ``tmp_path``: a sidecar and three
600 px planes, three bands each. ``direct.cache`` and ``top.cache`` share their file names.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from mapgen import cli
from mapgen.bandstore import BandArray
from mapgen.cache import (
    BANDS_SUFFIX,
    CACHE_SIDECAR_NAME,
    DIRECT_CACHE_DIR_NAME,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    PLANE_DTYPES,
    STORAGE_BANDS,
    STORAGE_RAW,
    TOP_CACHE_DIR_NAME,
    cached_raster,
    raster_cache_stamp,
)
from mapgen.commands import compress_cache
from mapgen.commands.compress_cache import compress
from mapgen.common import Refusal

pytest.importorskip("zstandard")

SIZE = 600
STAMP = raster_cache_stamp(SIZE, 1, "b1")
DIRECT = (DIRECT_Z_NAME, DIRECT_COVERAGE_NAME, DIRECT_FAMILY_NAME)
NOTHING = "Nothing converted"


def raw_cache(folder: Path, seed: int = 0, names=DIRECT) -> dict[str, bytes]:
    folder.mkdir(parents=True, exist_ok=True)
    sidecar = {**STAMP, "storage": STORAGE_RAW}
    (folder / CACHE_SIDECAR_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    rng = np.random.default_rng(seed)
    for name in names:
        (rng.random((SIZE, SIZE)) * 200).astype(PLANE_DTYPES[name]).tofile(folder / name)
    return files(folder)


def files(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def kept_folder(tmp_path: Path) -> tuple[Path, dict[Path, dict[str, bytes]]]:
    """``kept/direct.cache`` and ``kept/top.cache``, raw, and every file's bytes."""
    kept = tmp_path / "kept"
    caches = (kept / DIRECT_CACHE_DIR_NAME, kept / TOP_CACHE_DIR_NAME)
    return kept, {cache: raw_cache(cache, seed) for seed, cache in enumerate(caches)}


def run(monkeypatch, capsys, *argv) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["mapgen"])
    code = cli.main(["compress-cache", *map(str, argv)])
    return code, capsys.readouterr().out


def link(kind: str, target: Path, at: Path) -> None:
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("directory junctions are a Windows feature")
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
        return
    try:
        at.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this account cannot make a directory symlink here: {exc}")


# ------------------------------------------------------------------ a target on the source


def _spellings(kept: Path, tmp_path: Path) -> dict[str, tuple[Path, Path]]:
    """``(dir, --to)`` pairs whose target resolves to a source, holds one or sits in one."""
    cache = kept / DIRECT_CACHE_DIR_NAME
    return {
        "the cache": (cache, cache),
        "the folder": (kept, kept),
        "the cache's parent": (cache, kept),
        "an ancestor": (cache, tmp_path),
        "inside the cache": (cache, cache / "copy"),
        "a cache inside the folder": (kept, cache),
        "dotted": (cache, kept / "x" / ".." / DIRECT_CACHE_DIR_NAME),
    }


@pytest.mark.parametrize(
    "spelling",
    ["the cache", "the folder", "the cache's parent", "an ancestor", "inside the cache",
     "a cache inside the folder", "dotted"],
)  # fmt: skip
def test_a_target_on_the_source_is_refused_and_the_source_kept(
    tmp_path, monkeypatch, capsys, spelling
):
    kept, before = kept_folder(tmp_path)
    source, to = _spellings(kept, tmp_path)[spelling]
    code, out = run(monkeypatch, capsys, source, "--to", to)
    assert code == 1 and NOTHING in out, out
    assert "left as it was" not in out
    assert {cache: files(cache) for cache in before} == before


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths are Windows'")
def test_a_target_spelled_in_another_case_is_the_source(tmp_path, monkeypatch, capsys):
    kept, before = kept_folder(tmp_path)
    cache = kept / DIRECT_CACHE_DIR_NAME
    code, out = run(monkeypatch, capsys, cache, "--to", str(cache).swapcase())
    assert code == 1 and NOTHING in out, out
    assert {c: files(c) for c in before} == before


def test_a_relative_target_is_resolved_before_it_is_compared(tmp_path, monkeypatch, capsys):
    kept, before = kept_folder(tmp_path)
    monkeypatch.chdir(kept)
    code, out = run(monkeypatch, capsys, DIRECT_CACHE_DIR_NAME, "--to", ".")
    assert code == 1 and NOTHING in out, out
    assert {c: files(c) for c in before} == before


@pytest.mark.parametrize("kind", ["junction", "symlink"])
@pytest.mark.parametrize("onto", ["the cache", "the folder", "a new folder in the cache"])
def test_a_target_linked_onto_the_source_is_refused(tmp_path, monkeypatch, capsys, kind, onto):
    kept, before = kept_folder(tmp_path)
    source = kept if onto == "the folder" else kept / DIRECT_CACHE_DIR_NAME
    link(kind, source, tmp_path / "out")
    to = tmp_path / "out" / ("copy" if onto.startswith("a new") else "")
    code, out = run(monkeypatch, capsys, source, "--to", to)
    assert code == 1 and NOTHING in out and "which is" in out, out
    assert {c: files(c) for c in before} == before


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_a_target_linked_onto_another_cache_of_the_run_is_refused(
    tmp_path, monkeypatch, capsys, kind
):
    """``out/direct.cache`` is ``kept/top.cache``: the planes share names, so it would be lost."""
    kept, before = kept_folder(tmp_path)
    (tmp_path / "out").mkdir()
    link(kind, kept / TOP_CACHE_DIR_NAME, tmp_path / "out" / DIRECT_CACHE_DIR_NAME)
    code, out = run(monkeypatch, capsys, kept, "--to", tmp_path / "out")
    assert code == 1 and NOTHING in out, out
    assert {c: files(c) for c in before} == before


def test_compress_refuses_a_target_on_its_source(tmp_path):
    kept, before = kept_folder(tmp_path)
    cache = kept / DIRECT_CACHE_DIR_NAME
    for target in (cache, kept, cache / "copy"):
        with pytest.raises(Refusal, match="the source"):
            compress(cache, target)
    assert {c: files(c) for c in before} == before


def test_a_target_holding_raw_planes_is_not_overwritten(tmp_path):
    source = tmp_path / "a" / DIRECT_CACHE_DIR_NAME
    raw_cache(source)
    other = tmp_path / "b" / DIRECT_CACHE_DIR_NAME
    before = raw_cache(other, seed=1)
    with pytest.raises(Refusal, match="raw planes"):
        compress(source, other)
    assert files(other) == before


# ----------------------------------------------------------------------------- the report


def test_the_report_says_where_the_bands_went_and_what_was_removed(tmp_path, monkeypatch, capsys):
    kept, before = kept_folder(tmp_path)
    out_dir = tmp_path / "out"
    code, out = run(monkeypatch, capsys, kept, "--to", out_dir)
    assert code == 0, out
    lines = out.splitlines()
    assert len(lines) == 2
    for line, cache in zip(lines, before, strict=True):
        assert str(out_dir / cache.name) in line and "source untouched" in line, line
    assert {c: files(c) for c in before} == before

    code, out = run(monkeypatch, capsys, kept)
    assert code == 0, out
    assert all("converted in place" in line and "raw planes removed" in line
               for line in out.splitlines())  # fmt: skip
    for cache, was in before.items():
        assert not any((cache / name).exists() for name in DIRECT)
        planes = cached_raster(cache, STAMP)
        assert np.asarray(planes[0]).tobytes() == was[DIRECT_Z_NAME]
        assert files(cache).keys() == files(out_dir / cache.name).keys()
        del planes

    code, out = run(monkeypatch, capsys, kept)
    assert code == 0 and out.count("already in the band store") == 2, out
    assert "removed" not in out


# ------------------------------------------------------------- failures and interruptions


def test_a_conversion_failing_partway_leaves_the_raw_cache_untouched(tmp_path, monkeypatch, capsys):
    cache = tmp_path / DIRECT_CACHE_DIR_NAME
    before = raw_cache(cache)
    real, done = compress_cache._convert_plane, []

    def full_disk(src, dst, size, dtype):
        real(src, dst, size, dtype)
        done.append(dst)
        if len(done) == 2:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(compress_cache, "_convert_plane", full_disk)
    code, out = run(monkeypatch, capsys, cache)
    assert code == 1 and "not converted" in out and "No space left" in out, out
    assert all(not path.exists() for path in done)
    assert files(cache) == before


def test_a_band_that_does_not_read_back_keeps_the_raw_cache(tmp_path, monkeypatch, capsys):
    cache = tmp_path / DIRECT_CACHE_DIR_NAME
    before = raw_cache(cache)

    class Flipped(BandArray):
        def __getitem__(self, key):
            band = np.array(super().__getitem__(key))
            if self.path.name == DIRECT_COVERAGE_NAME + BANDS_SUFFIX and key.start == 256:
                band[7, 7] ^= 1
            return band

    monkeypatch.setattr(compress_cache, "BandArray", Flipped)
    code, out = run(monkeypatch, capsys, cache)
    assert code == 1 and "band 1 does not match" in out, out
    assert files(cache) == before


def test_a_raw_plane_that_cannot_go_is_reported_and_goes_on_the_next_run(
    tmp_path, monkeypatch, capsys
):
    cache = tmp_path / DIRECT_CACHE_DIR_NAME
    before = raw_cache(cache)
    unlink = Path.unlink

    def held(self, missing_ok=False):
        if self.name == DIRECT_Z_NAME:
            raise PermissionError(13, "held by another process", str(self))
        return unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    code, out = run(monkeypatch, capsys, cache)
    assert code == 1, out
    assert "converted in place" in out and f"raw {DIRECT_Z_NAME} kept" in out, out
    assert "held by another process" in out and "untouched" not in out
    sidecar = json.loads((cache / CACHE_SIDECAR_NAME).read_text("utf-8"))
    assert sidecar["storage"] == STORAGE_BANDS
    assert sorted(p.name for p in cache.iterdir() if p.suffix != BANDS_SUFFIX) == sorted(
        [CACHE_SIDECAR_NAME, DIRECT_Z_NAME]
    )

    monkeypatch.setattr(Path, "unlink", unlink)
    code, out = run(monkeypatch, capsys, cache)
    assert code == 0 and "already in the band store" in out and "removed" in out, out
    assert not (cache / DIRECT_Z_NAME).exists()
    planes = cached_raster(cache, STAMP)
    assert np.asarray(planes[0]).tobytes() == before[DIRECT_Z_NAME]
    del planes


def interrupted(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """A cache whose run stopped after the sidecar named the band store, the raw still there."""
    source = tmp_path / "source" / DIRECT_CACHE_DIR_NAME
    before = raw_cache(source)
    cache = tmp_path / "cache" / DIRECT_CACHE_DIR_NAME
    compress(source, cache)
    for name in DIRECT:
        shutil.copyfile(source / name, cache / name)
    return cache, before


def test_an_interrupted_run_s_raw_planes_go_once_each_matches_its_bands(tmp_path):
    cache, before = interrupted(tmp_path)
    done = compress(cache)
    assert done["already"] and sorted(done["removed"]) == sorted(DIRECT) and not done["kept"]
    assert not any((cache / name).exists() for name in DIRECT)
    planes = (*cached_raster(cache, STAMP),)
    assert [np.asarray(p).tobytes() for p in planes] == [before[n] for n in DIRECT[:2]]
    del planes


def _corrupt_band(path: Path, k: int) -> None:
    offsets = BandArray(path, (SIZE, SIZE), np.float32)._offsets
    data = bytearray(path.read_bytes())
    data[(int(offsets[k]) + int(offsets[k + 1])) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("damage", ["corrupt band", "raw differs", "no band file", "short raw"])
def test_an_interrupted_run_s_raw_plane_is_kept_unless_it_matches(
    tmp_path, monkeypatch, capsys, damage
):
    cache, _before = interrupted(tmp_path)
    raw, bands = cache / DIRECT_Z_NAME, cache / (DIRECT_Z_NAME + BANDS_SUFFIX)
    if damage == "corrupt band":
        _corrupt_band(bands, 1)
    elif damage == "raw differs":
        data = bytearray(raw.read_bytes())
        data[SIZE * 300 * 4 + 5] ^= 0x40
        raw.write_bytes(bytes(data))
    elif damage == "no band file":
        bands.unlink()
    else:
        raw.write_bytes(raw.read_bytes()[:-4])
    kept = raw.read_bytes()
    code, out = run(monkeypatch, capsys, cache)
    assert code == 1 and f"raw {DIRECT_Z_NAME} kept" in out, out
    assert raw.read_bytes() == kept
    assert not any((cache / name).exists() for name in DIRECT[1:])


def test_a_plane_left_under_its_probe_name_stops_the_conversion(tmp_path):
    """Killed between ``held_open``'s two renames: converting the rest would orphan it."""
    cache = tmp_path / DIRECT_CACHE_DIR_NAME
    raw_cache(cache)
    (cache / DIRECT_FAMILY_NAME).rename(cache / (DIRECT_FAMILY_NAME + ".probe"))
    before = files(cache)
    with pytest.raises(Refusal, match="rename it back"):
        compress(cache)
    assert files(cache) == before


def test_band_files_beside_a_raw_cache_never_outlive_the_conversion(tmp_path):
    """Stale band files in a raw source or an old target: rewritten, or removed."""
    source = tmp_path / "kept" / DIRECT_CACHE_DIR_NAME
    raw_cache(source, names=DIRECT[:2])
    for name in DIRECT:
        (source / (name + BANDS_SUFFIX)).write_bytes(b"stale")
    before = files(source)
    target = tmp_path / "out" / DIRECT_CACHE_DIR_NAME
    target.mkdir(parents=True)
    (target / (DIRECT_FAMILY_NAME + BANDS_SUFFIX)).write_bytes(b"from an older --to")
    (target / CACHE_SIDECAR_NAME).write_text("{}", encoding="utf-8")

    assert compress(source, target)["planes"] == list(DIRECT[:2])
    assert files(source) == before
    want = sorted([CACHE_SIDECAR_NAME] + [n + BANDS_SUFFIX for n in DIRECT[:2]])
    assert sorted(files(target)) == want

    assert compress(source)["planes"] == list(DIRECT[:2])
    assert sorted(files(source)) == want
    z = cached_raster(source, STAMP)[0]
    assert np.asarray(z).tobytes() == before[DIRECT_Z_NAME]
    del z

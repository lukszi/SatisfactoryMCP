"""The parallel tile cutter: the serial cutter's bytes from rows as they come, a level resampled
once, the rows a sheet keeps, failures and memory.

docs/map/renders.md sections 17 and 42. Synthetic sheets throughout: a smooth field with noise,
so the PNGs are real PNGs and every Lanczos tap matters.
"""

from __future__ import annotations

import argparse
import hashlib
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from mapgen.tiles import cutter as cut
from mapgen.tiles import levels
from mapgen.tiles import pyramid as layer_pyramid
from mapgen.tiles.cutter import TileStream, TreeSpec
from satisfactory_mcp.core.gameassets.pyramid import PyramidError, install_pyramid

Image = pytest.importorskip("PIL.Image")


def _sheet(px: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:px, 0:px].astype(np.float32) / px
    base = np.stack([xx * 200, yy * 180, (xx + yy) * 90], axis=-1)
    return np.clip(base + rng.normal(0, 18, (px, px, 3)), 0, 255).astype(np.uint8)


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.png"))
    }


def _record(stats: dict) -> dict:
    return {key: value for key, value in stats.items() if key not in ("workers", "installed_by")}


def _runs(px: int, sizes: list[int]):
    """``px`` rows in runs of ``sizes``, repeated, the last cut to fit."""
    top, k = 0, 0
    while top < px:
        rows = min(sizes[k % len(sizes)], px - top)
        yield top, rows
        top, k = top + rows, k + 1


def test_levels_resampled_from_rows_as_they_come_are_one_resize_of_the_whole_sheet(monkeypatch):
    """Scales 2 to 128, strips of one to eight rows, rows in runs of 1 to 300: every halo,
    both edges and a run shorter than a halo are crossed."""
    monkeypatch.setattr(levels, "STRIP_BYTES", 4 * 2048 * 16)
    src = _sheet(2048)
    sides = [1024, 512, 256, 128, 64, 32, 16]
    rows = levels.SheetRows(Image, 2048, sides)
    got: dict[int, list[np.ndarray]] = {side: [] for side in [2048, *sides]}
    most = 0
    for top, count in _runs(2048, [1, 300, 7, 64, 129]):
        for side, out in rows.add(src[top : top + count]):
            got[side].append(out)
        most = max(most, sum(len(part) for _, part in rows.kept))
    assert rows.complete and not rows.kept
    assert np.array_equal(np.concatenate(got[2048]), src)
    for side in sides:
        whole = np.asarray(Image.fromarray(src).resize((side, side), Image.LANCZOS))
        assert np.array_equal(np.concatenate(got[side]), whole), side
    assert most < 1300, "the sheet was never kept whole"


def test_a_strip_needs_every_row_it_reads_and_a_level_must_divide_the_sheet():
    src = _sheet(256)
    with pytest.raises(ValueError, match="not all in the window"):
        levels.resample_rows(Image, src[100:], 100, 256, 64, 0, 8)
    assert levels.strip_rows(32768, 16384) == 512 and levels.strip_rows(2048, 256) == 2048
    with pytest.raises(PyramidError, match="does not divide"):
        levels.strip_rows(2048, 768)
    rows = levels.SheetRows(Image, 64, [32])
    with pytest.raises(ValueError, match="past the end"):
        list(rows.add(np.zeros((65, 64, 3), np.uint8)))


def _cut(stream: TileStream, out: Path, sheet: np.ndarray, sizes: list[int], dense_px: int):
    text = "test, Lanczos"
    tiles = TreeSpec("tiles", 256, text)
    dense = (TreeSpec("tiles@2x", 512, text), dense_px)
    cut_sheet = stream.sheet(out, sheet.shape[0], [tiles], dense)
    for top, count in _runs(sheet.shape[0], sizes):
        stream.put(cut_sheet, sheet[top : top + count])
    return stream.install(cut_sheet)


def _serial(out: Path, sheet: np.ndarray, dense_px: int) -> list[dict]:
    """The reference: ``install_pyramid`` of the sheet, and of its downscale at 512 px tiles."""
    image = Image.fromarray(sheet)
    stats = install_pyramid(image, Image, out, source="test, Lanczos")
    if dense_px < sheet.shape[0]:
        image = image.resize((dense_px, dense_px), Image.LANCZOS)
    dense = install_pyramid(image, Image, out, 512, source="test, Lanczos", dir_name="tiles@2x")
    return [stats, dense]


@pytest.mark.parametrize("workers", [1, 3])
def test_the_stream_writes_the_serial_cutters_bytes(tmp_path, monkeypatch, workers):
    """1x and @2x, the @2x top a level of the 1x and its levels resampled from that."""
    monkeypatch.setattr(cut, "free_ram_bytes", lambda: 1 << 40)  # the count, not the machine's
    sheet = _sheet(2048)
    serial = _serial(tmp_path / "a", sheet, 1024)
    with TileStream(Image, workers, threads=3) as stream:
        streamed = _cut(stream, tmp_path / "b", sheet, [256, 100, 300], 1024)
    assert [s["count"] for s in serial] == [85, 5]
    assert [_record(s) for s in streamed] == [_record(s) for s in serial]
    assert streamed[0]["workers"] == workers
    a, b = _digests(tmp_path / "a"), _digests(tmp_path / "b")
    assert len(a) == 90 and a == b


def test_a_tree_the_sheet_s_own_levels_hold_is_cut_from_them_once(tmp_path, monkeypatch):
    calls = []
    real = levels.resample_rows

    def counted(image_mod, window, first, px, side, r0, r1):
        calls.append(side)
        return real(image_mod, window, first, px, side, r0, r1)

    monkeypatch.setattr(levels, "resample_rows", counted)
    sheet = _sheet(1024)
    with TileStream(Image, 1) as stream:
        _cut(stream, tmp_path / "b", sheet, [256], 16384)
    assert sorted(set(calls)) == [256, 512] and len(calls) == 2, "@2x shares the 1x levels"
    _serial(tmp_path / "a", sheet, 16384)
    assert _digests(tmp_path / "b") == _digests(tmp_path / "a")


def test_a_failure_on_a_lane_stops_the_stream_and_is_raised(tmp_path):
    def broken(_rows):
        raise RuntimeError("a transform failed")

    for workers in (1, 2):
        with TileStream(Image, workers, threads=2) as stream:
            sheet = stream.sheet(tmp_path / str(workers), 512, [TreeSpec("tiles", 256, "t")])
            stream.put(sheet, _sheet(512)[:256], broken)
            with pytest.raises(RuntimeError, match="a transform failed"):
                for _ in range(50):
                    stream.put(sheet, np.zeros((0, 512, 3), np.uint8))
                    time.sleep(0.02)
                stream.install(sheet)
            assert not stream.blocks, "no shared row is left behind"


def test_an_encoder_failure_is_raised_by_install(tmp_path, monkeypatch):
    def refuse(_job):
        raise OSError("the disk is full")

    monkeypatch.setattr(cut, "encode_tile_row", refuse)
    with TileStream(Image, 1) as stream:
        sheet = stream.sheet(tmp_path, 512, [TreeSpec("tiles", 256, "t")])
        stream.put(sheet, _sheet(512))
        with pytest.raises(OSError, match="the disk is full"):
            stream.install(sheet)
        assert not stream.blocks


def test_a_sheet_cut_from_too_few_rows_is_refused(tmp_path):
    with TileStream(Image, 1) as stream:
        sheet = stream.sheet(tmp_path, 512, [TreeSpec("tiles", 256, "t")])
        stream.put(sheet, _sheet(512)[:300])
        with pytest.raises(PyramidError, match="cut from 300 rows"):
            stream.install(sheet)
        with pytest.raises(PyramidError, match="past the end"):
            sheet.post(_sheet(512)[:300])
    assert not (tmp_path / "tiles").exists(), "nothing is renamed into place"


def test_the_encoders_are_capped_by_memory_and_put_waits_for_rows_in_flight(tmp_path, monkeypatch):
    free = [cut.RAM_RESERVE + 3 * cut.WORKER_BYTES]
    monkeypatch.setattr(cut, "free_ram_bytes", lambda: free[0])
    with TileStream(Image, 24, threads=1) as stream:
        assert stream.workers == 3
    free[0] = 1 << 40
    monkeypatch.setattr(cut, "QUEUED_BYTES", 256 * 512 * 3)
    gate = threading.Event()

    def held(rows):
        gate.wait(10)
        return rows

    with TileStream(Image, 2, threads=1) as stream:
        sheet = stream.sheet(tmp_path, 512, [TreeSpec("tiles", 256, "t")])
        rows = _sheet(512)
        stream.put(sheet, rows[:256], held)
        threading.Timer(0.3, gate.set).start()
        started = time.monotonic()
        stream.put(sheet, rows[256:])
        assert time.monotonic() - started >= 0.25, "it waited for the first rows to go"
        stream.install(sheet)


def _pools(*argv: str) -> tuple[int | None, int]:
    parser = argparse.ArgumentParser()
    layer_pyramid.add_worker_flags(parser)
    return layer_pyramid.pool_sizes(parser.parse_args(list(argv)))


def test_workers_is_the_default_for_the_light_and_the_cut_and_each_flag_wins():
    """An old command line's ``--workers N`` still sizes both pools."""
    assert _pools() == (None, cut.CUT_WORKERS), "the bake counts its own when it starts"
    assert _pools("--workers", "2") == (2, 2)
    assert _pools("--workers", "2", "--light-workers", "6") == (6, 2)
    assert _pools("--workers", "2", "--cut-workers", "9") == (2, 9)
    assert _pools("--light-workers", "3", "--cut-workers", "1") == (3, 1)
    assert _pools("--workers", "0") == (1, 1)


def test_check_parallel_compares_a_whole_pyramid_and_says_so(tmp_path):
    check = layer_pyramid.check_parallel(_sheet(512), Image, tmp_path / "check", 2)
    assert check.byte_identical and check.differing_tiles == []
    assert (check.levels, check.tiles, check.workers) == ([0, 1], 5, 2)
    assert check.record()["byte_identical"] is True
    assert not (tmp_path / "check").exists()

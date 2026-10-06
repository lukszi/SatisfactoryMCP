"""The parallel tile cutter: the serial cutter's bytes, a level resampled once, holds and memory.

docs/spatial-and-map.md section 17, "Cutting in parallel". Synthetic sheets throughout: a
smooth field with noise, so the PNGs are real PNGs and every Lanczos tap matters.
"""

from __future__ import annotations

import argparse
import hashlib
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pytest

from mapgen.render import light
from mapgen.tiles import cutter as cut
from mapgen.tiles import pyramid as layer_pyramid
from satisfactory_mcp.core.gameassets.pyramid import PyramidError

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


def test_strips_resample_to_the_bytes_of_one_resize_of_the_whole_sheet(monkeypatch):
    """Scales 2 to 128, strips of one to eight rows: every halo and both edges are crossed."""
    monkeypatch.setattr(cut, "STRIP_BYTES", 4 * 1024 * 16)
    src = _sheet(1024)
    for side in (512, 256, 128, 64, 32, 16, 8):
        out = np.empty((side, side, 3), np.uint8)
        spans = cut.strip_spans(1024, side, 1024)
        assert len(spans) > 1
        for r0, r1 in spans:
            cut.resample_strip(Image, src, out, r0, r1)
        whole = np.asarray(Image.fromarray(src).resize((side, side), Image.LANCZOS))
        assert np.array_equal(out, whole), side


def test_strip_spans_cover_every_row_once_and_refuse_a_level_that_does_not_divide():
    spans = cut.strip_spans(32768, 16384, 32768)
    rows = [row for r0, r1 in spans for row in range(r0, r1)]
    assert rows == list(range(16384)) and len(spans) == 32
    assert cut.strip_spans(2048, 256, 2048) == [(0, 256)]
    with pytest.raises(PyramidError, match="does not divide"):
        cut.strip_spans(2048, 768, 2048)


def test_a_block_is_freed_by_its_last_release_and_never_held_again():
    block = cut.Block((4, 4, 3))
    block.hold()
    block.release()
    assert not block.freed and block.array is not None
    block.release()
    assert block.freed and block.array is None
    with pytest.raises(RuntimeError, match="freed block"):
        block.hold()
    block.free()  # a cutter closing frees whatever is left; twice is harmless


def test_a_level_two_trees_share_is_resampled_once(monkeypatch):
    calls = []
    real = cut.Cutter.resample

    def counted(self, source, side):
        calls.append(side)
        return real(self, source, side)

    monkeypatch.setattr(cut.Cutter, "resample", counted)
    with cut.Cutter(Image, 1, threads=2) as cutter, cutter.publish(_sheet(512)) as source:
        assert source.level(256) is source.level(256)
        assert source.derive(512) is source
        child = source.derive(256)
        assert child.top is source.level(256)
        assert child.level(128).ready.result(timeout=30) is child.level(128)
    assert calls == [256, 128]


def test_a_failed_strip_settles_its_level_only_after_every_strip_has_stopped(monkeypatch):
    """Settled early, the failure would free the level under the strips still writing it."""
    finished = []

    def strip(_image_mod, _src, _out, r0, _r1):
        if r0 == 0:
            raise RuntimeError("a strip failed")
        time.sleep(0.02)
        finished.append(r0)

    monkeypatch.setattr(cut, "STRIP_BYTES", 4 * 256 * 16)
    monkeypatch.setattr(cut, "resample_strip", strip)
    with cut.Cutter(Image, 1, threads=4) as cutter, cutter.publish(_sheet(256)) as source:
        level = source.level(128)
        with pytest.raises(RuntimeError, match="a strip failed"):
            level.ready.result(timeout=30)
        assert len(finished) == len(cut.strip_spans(256, 128, 256)) - 1
        assert not level.freed, "the source still holds it"


def test_the_encoders_are_capped_by_memory_and_a_block_waits_for_work_in_flight(monkeypatch):
    free = [cut.RAM_RESERVE + 3 * cut.WORKER_BYTES]
    monkeypatch.setattr(cut, "free_ram_bytes", lambda: free[0])
    with cut.Cutter(Image, 24, threads=1) as cutter:
        assert cutter.workers == 3
        free[0] = cut.RAM_RESERVE
        pending: Future = Future()
        cutter._track(pending)

        def finish():
            time.sleep(0.3)
            free[0] = 1 << 40
            pending.set_result(None)

        started = time.monotonic()
        threading.Thread(target=finish).start()
        block = cutter._block((64, 64, 3))
        assert pending.done() and time.monotonic() - started >= 0.25
        free[0] = 0
        assert cutter._block((64, 64, 3)) is not block, "nothing in flight: it goes ahead"
    with cut.Cutter(Image, 24, threads=1) as cutter:
        assert cutter.workers == 1


def test_the_parallel_layer_writes_the_serial_layers_bytes(tmp_path, monkeypatch):
    """1x and @2x, the @2x top the 1x level of its size and its z0 resampled from that."""
    monkeypatch.setattr(layer_pyramid, "RENDER_2X_PX", 1024)
    monkeypatch.setattr(cut, "STRIP_BYTES", 4 * 2048 * 64)
    sheet = _sheet(2048)
    serial = layer_pyramid.install_layer(sheet, Image, tmp_path / "a", "terrain", 1, 7, "r")
    parallel = layer_pyramid.install_layer(sheet, Image, tmp_path / "b", "terrain", 3, 7, "r")
    assert [s["count"] for s in serial[:2]] == [85, 5]
    for one, other in zip(serial[:2], parallel[:2], strict=True):
        assert _record(one) == _record(other)
    assert parallel[0]["workers"] == 3 and serial[0]["workers"] == 1
    a, b = _digests(tmp_path / "a" / "r" / "terrain"), _digests(tmp_path / "b" / "r" / "terrain")
    assert len(a) == 90 and a == b


def test_a_lit_layer_encodes_the_unlit_tree_while_the_sheet_is_relit(tmp_path, monkeypatch):
    """Three trees through one pool, the serial bytes, renamed unlit, then tiles, then @2x."""
    relit, renamed = [], []
    real_commit = cut.commit_tree

    def relight(sheet, _surface, _params):
        relit.append(True)
        np.subtract(255, sheet, out=sheet)

    def commit(stats, out_dir, dir_name):
        renamed.append(dir_name)
        return real_commit(stats, out_dir, dir_name)

    monkeypatch.setattr(light, "relight_in_place", relight)
    monkeypatch.setattr(cut, "commit_tree", commit)
    results = {}
    for name, workers in (("serial", 1), ("parallel", 2)):
        run = light.LightingRun.__new__(light.LightingRun)
        run.surface, run.meta, run.unlit = None, {"tiles": {}}, {}
        sheet = _sheet(512, seed=3)
        stats, dense, _ = run.install(sheet, Image, tmp_path / name, "terrain", workers, 7, "r")
        results[name] = (_record(stats), _record(dense), _record(run.unlit["terrain"]))
    assert relit == [True, True]
    assert renamed == [light.UNLIT_DIR_NAME, "tiles", "tiles@2x"]
    assert results["serial"] == results["parallel"]
    a = _digests(tmp_path / "serial" / "r" / "terrain")
    assert len(a) == 11 and a == _digests(tmp_path / "parallel" / "r" / "terrain")
    assert a["unlit/1/0_0.png"] != a["tiles/1/0_0.png"], "the unlit tree kept the unlit pixels"


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

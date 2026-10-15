"""One memory budget for a run's pools: the draw, the cut's encoders and the light share one
reading of the free memory. docs/map/renders.md section 43."""

from __future__ import annotations

import os

import pytest

from mapgen.lighting import stage
from mapgen.render.draw import drawpool
from mapgen.render.run import budget as run
from mapgen.render.run.budget import PoolAsks, run_budget
from mapgen.tiles import cutter as cut

GB = 10**9
LAYERS = ("terrain", "painted", "relief-dark")
SIZE = 32768


@pytest.fixture
def gpu_box(monkeypatch):
    """The machine of the failed run: 32 cores, the light on the GPU, 24 encoders."""
    monkeypatch.setattr(stage, "gpu_on", lambda: True)
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    monkeypatch.setattr(run, "CUT_WORKERS", 24)


def _booked(split: run.RunBudget) -> float:
    return split.held + split.draw_bytes + split.cut_bytes + split.light_bytes


def test_24_gb_free_is_booked_once_not_once_a_pool(gpu_box):
    split = run_budget(LAYERS, SIZE, PoolAsks(), free=24 * GB)
    assert (split.draw_threads, split.light_workers) != (8, 8)
    assert split.draw_threads == drawpool.DRAW_THREADS, "the draw is sized first"
    assert 1 <= split.light_workers < stage.LIGHT_GPU_WORKER_CAP
    assert _booked(split) <= 24 * GB
    assert _booked(split) + stage.light_worker_bytes() > 24 * GB, "the light takes what is left"


def test_50_gb_free_keeps_the_choice_each_pool_made_alone(gpu_box):
    split = run_budget(LAYERS, SIZE, PoolAsks(), free=50 * GB)
    assert (split.draw_threads, split.cut_workers) == (drawpool.DRAW_THREADS, 24)
    assert split.light_workers == stage.LIGHT_GPU_WORKER_CAP
    assert _booked(split) <= 50 * GB


def test_the_draw_leaves_room_for_one_light_process(gpu_box):
    alone = drawpool.draw_threads(None, LAYERS, SIZE, 8 * GB)
    split = run_budget(LAYERS, SIZE, PoolAsks(), free=8 * GB)
    assert split.draw_threads < alone
    assert split.light_workers == 1


def test_ample_memory_gives_every_pool_its_default(gpu_box):
    split = run_budget(LAYERS, SIZE, PoolAsks(), free=200 * GB)
    assert split.draw_threads == drawpool.DRAW_THREADS
    assert split.cut_workers == 24
    assert split.light_workers == stage.LIGHT_GPU_WORKER_CAP


def test_short_memory_leaves_each_pool_one(gpu_box):
    split = run_budget(LAYERS, SIZE, PoolAsks(), free=4 * GB)
    assert (split.draw_threads, split.cut_workers, split.light_workers) == (1, 1, 1)
    assert split.cut_bytes == 0, "one encoder cuts on the caller's thread"


def test_counts_the_flags_ask_for_win_and_are_booked(gpu_box):
    asks = PoolAsks(draw=8, cut=3, light=8)
    split = run_budget(LAYERS, SIZE, asks, free=24 * GB)
    assert (split.draw_threads, split.cut_workers, split.light_workers) == (8, 3, 8)
    assert split.light_bytes == 8 * stage.light_worker_bytes()
    assert split.cut_bytes == 3 * cut.WORKER_BYTES
    assert "-" in split.line(), "the line shows the overbooking"


def test_a_run_without_the_light_books_none(gpu_box):
    split = run_budget(LAYERS, SIZE, PoolAsks(lit=False), free=24 * GB)
    assert split.light_workers is None and split.light_bytes == 0
    assert "no light" in split.line()


def test_unknown_free_memory_leaves_the_defaults(gpu_box, monkeypatch):
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: None)
    monkeypatch.setattr(run, "free_ram_bytes", lambda: None)
    split = run_budget(LAYERS, SIZE, PoolAsks())
    assert split.free is None
    assert split.draw_threads == drawpool.DRAW_THREADS
    assert split.light_workers == stage.LIGHT_GPU_WORKER_CAP
    assert split.line().startswith("memory: free not known")


def test_the_line_names_every_share(gpu_box):
    line = run_budget(LAYERS, SIZE, PoolAsks(), free=24 * GB).line()
    for word in ("24.0 GB free", "held back", "left", "draw 8 thread(s)", "encoder(s)", "light"):
        assert word in line

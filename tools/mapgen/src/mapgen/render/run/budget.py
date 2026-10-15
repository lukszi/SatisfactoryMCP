"""One memory budget for a run's pools: the draw's threads, the cut's encoders and the light's
processes, sized from one reading of the free memory taken before the draw.

docs/map/renders.md section 43.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import NamedTuple

from mapgen.lighting.stage import light_worker_bytes, light_workers
from mapgen.pools import free_ram_bytes
from mapgen.render.draw.drawpool import PIECE_COLS, RESERVE_BYTES, draw_threads, pass_bytes
from mapgen.tiles.cutter import CUT_WORKERS, WORKER_BYTES

__all__ = ["PoolAsks", "RunBudget", "held_bytes", "run_budget"]


class PoolAsks(NamedTuple):
    """The counts the flags ask for, None where the budget decides; ``light`` False for a
    run without the light."""

    draw: int | None = None
    cut: int | None = None
    light: int | None = None
    lit: bool = True


class RunBudget(NamedTuple):
    """Each pool's count and the bytes it was booked, out of ``free`` (None where the
    platform does not say) less ``held``; ``light_workers`` None without the light."""

    free: int | None
    held: float
    draw_threads: int
    draw_bytes: float
    cut_workers: int
    cut_bytes: float
    light_workers: int | None
    light_bytes: float

    def line(self) -> str:
        """The split in one line, for the run's log."""
        if self.free is None:
            head = "memory: free not known"
        else:
            left = self.free - self.held - self.draw_bytes - self.cut_bytes - self.light_bytes
            head = f"memory: {_gb(self.free)} free, {_gb(self.held)} held back, {_gb(left)} left"
        light = "no light" if self.light_workers is None else (
            f"light {self.light_workers} worker(s) {_gb(self.light_bytes)}"
        )  # fmt: skip
        return (
            f"{head}; draw {self.draw_threads} thread(s) {_gb(self.draw_bytes)}, "
            f"cut {self.cut_workers} encoder(s) {_gb(self.cut_bytes)}, {light}"
        )


def held_bytes(size: int) -> float:
    """What the budget keeps back: one sheet's bytes, about the rows the cut and the light
    hold beside the draw, and ``RESERVE_BYTES``."""
    return size * size * 3 + RESERVE_BYTES


def run_budget(
    layers: Collection[str],
    size: int,
    asks: PoolAsks,
    columns: int = PIECE_COLS,
    free: int | None = None,
) -> RunBudget:
    """The pools of a pass over ``layers`` of a ``size`` sheet, from ``free`` bytes
    (``free_ram_bytes()`` when None), read once.

    One light process is set aside first, as the light needs one. Then the draw, from the
    headroom; the encoders from what it leaves; the light from the rest. A count a flag asks
    for is taken as asked (the draw's still no more than the whole headroom holds) and
    booked all the same.
    """
    free = free_ram_bytes() if free is None else free
    held = held_bytes(size)
    one_light = light_worker_bytes() if asks.lit else 0
    draw_free = free if free is None or asks.draw is not None else int(free - one_light)
    threads = draw_threads(asks.draw, layers, size, draw_free, columns)
    drawing = pass_bytes(layers, size, columns, threads)
    left = None if free is None else free - held - drawing
    cut = asks.cut
    if cut is None and left is not None:
        cut = min(CUT_WORKERS, int((left - one_light) // WORKER_BYTES))
    elif cut is None:
        cut = CUT_WORKERS
    cut = max(1, cut)
    cutting = cut * WORKER_BYTES if cut > 1 else 0
    workers, lighting = None, 0.0
    if asks.lit:
        room = None if left is None else max(0.0, left - cutting)
        workers = light_workers(asks.light, room)
        lighting = workers * light_worker_bytes()
    return RunBudget(free, held, threads, drawing, cut, cutting, workers, lighting)


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"

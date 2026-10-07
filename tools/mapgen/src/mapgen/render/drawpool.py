"""Drawing a pass's bands on threads: how many, and the pool that keeps their order.

The measurements behind the numbers: docs/spatial-and-map.md section 40.
"""

from __future__ import annotations

import argparse
import os
from collections import deque
from collections.abc import Callable, Collection, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from mapgen.pools import free_ram_bytes

__all__ = [
    "AHEAD",
    "BAND_BYTES",
    "DRAW_THREADS",
    "RESERVE_BYTES",
    "SEABED_BYTES",
    "add_draw_flags",
    "band_bytes",
    "bands_held",
    "draw_threads",
    "in_order",
]

T = TypeVar("T")
R = TypeVar("R")

#: Threads a pass is drawn on by default, fewer on a machine with fewer cores. The draw is
#: bound by memory bandwidth, and more threads than this were no faster.
DRAW_THREADS = 8

#: Items submitted per thread ahead of the one waited on.
AHEAD = 2

#: Memory one more band in flight takes, its decoded stored bands included, for a sheet
#: 32768 wide: the painted layer's, and every other layer's (None). Scaled by the width.
BAND_BYTES = {"painted": 3.4e9, None: 1.9e9}
BAND_BYTES_WIDTH = 32768

#: What a band of a pass that draws the painted layer and another adds: the second ground,
#: the meshes and the water over them under the other seabed rule. At 32768 wide.
SEABED_BYTES = 0.6e9

#: Memory left free for everything but the bands: the rest of the process and the machine.
RESERVE_BYTES = 2 << 30


def band_bytes(layers: Collection[str], size: int) -> float:
    """Memory one more band in flight of a pass over ``layers`` takes on a ``size`` sheet.

    The layers are painted in turn over one ground, so the dearest layer's band, and the
    second ground when the painted layer and another share the pass.
    """
    band = max(BAND_BYTES.get(layer, BAND_BYTES[None]) for layer in layers)
    if "painted" in layers and len(layers) > 1:
        band += SEABED_BYTES
    return band * size / BAND_BYTES_WIDTH


def draw_threads(
    requested: int | None, layers: Collection[str], size: int, free: int | None = None
) -> int:
    """Threads to draw a pass over ``layers`` of a ``size`` sheet on; at least one.

    ``requested``, or ``DRAW_THREADS`` but no more than the cores; then no more bands in flight
    than ``free`` bytes hold (``free_ram_bytes()`` when None) once one sheet and
    ``RESERVE_BYTES`` are set aside: the run's sheets are files (``render/sheets.py``).
    """
    want = min(DRAW_THREADS, os.cpu_count() or 1) if requested is None else requested
    free = free_ram_bytes() if free is None else free
    if free is None:
        return max(1, want)
    room = free - size * size * 3 - RESERVE_BYTES
    return max(1, min(want, int(room // band_bytes(layers, size))))


def bands_held(threads: int) -> int:
    """Bands of a band store the loop reads at once: those in flight, and a halo either side."""
    return AHEAD * threads + 2


def in_order(fn: Callable[[T], R], items: Iterable[T], threads: int) -> Generator[R, None, None]:
    """``fn`` over ``items`` on ``threads`` threads, the results yielded in the items' order.

    At most ``AHEAD * threads`` items are submitted past the one waited on. On one thread it
    is a plain loop on the caller's. A failure is raised when its turn comes, after the
    items not yet started are cancelled and the running ones finish.
    """
    if threads <= 1:
        yield from map(fn, items)
        return
    pending: deque[Future[R]] = deque()
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="draw") as pool:
        try:
            for item in items:
                pending.append(pool.submit(fn, item))
                if len(pending) >= AHEAD * threads:
                    yield pending.popleft().result()
            while pending:
                yield pending.popleft().result()
        finally:
            for future in pending:
                future.cancel()


def add_draw_flags(parser: argparse.ArgumentParser) -> None:
    """``--draw-threads``: how many bands of a layer are drawn at once."""
    parser.add_argument(
        "--draw-threads",
        type=int,
        default=None,
        help=(
            f"threads drawing the layers (default {DRAW_THREADS}, fewer on fewer cores; 1 "
            "draws the bands in turn). Fewer when free memory holds fewer bands in flight: "
            "about 1.9 GB each at full size, 3.4 GB with painted and 4 GB with painted and "
            "another layer. The tiles are the same bytes"
        ),
    )

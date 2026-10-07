"""Drawing a pass's pieces on threads: how wide, how many at once, and the pool that keeps
their order.

The measurements behind the numbers: docs/map/renders.md section 40.
"""

from __future__ import annotations

import argparse
import os
from collections import deque
from collections.abc import Callable, Collection, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from mapgen.jit import add_gpu_flag
from mapgen.pools import free_ram_bytes
from mapgen.render.stencils import piece_halo

__all__ = [
    "AHEAD",
    "DRAW_THREADS",
    "PIECE_BYTES",
    "PIECE_COLS",
    "RESERVE_BYTES",
    "SEABED_BYTES",
    "STORED_BAND_BYTES",
    "add_draw_flags",
    "bands_held",
    "draw_threads",
    "in_order",
    "pass_bytes",
    "piece_bytes",
]

T = TypeVar("T")
R = TypeVar("R")

#: Threads a pass is drawn on by default, fewer on a machine with fewer cores. The draw is
#: bound by memory bandwidth, and more threads than this were no faster.
DRAW_THREADS = 8

#: Output columns of a band drawn at a time (``--draw-columns``): a piece's arrays, 288 rows
#: by 544 columns with the halo, are 0.6 MB of float32 against 38 MB for a whole band. The
#: fastest of 256 to 2048 measured.
PIECE_COLS = 512

#: Items submitted per thread ahead of the one waited on.
AHEAD = 2

#: Memory one more piece in flight takes, for a piece ``PIECE_BYTES_WIDTH`` wide: the
#: painted layer's, and every other layer's (None). Scaled by the piece's width.
PIECE_BYTES = {"painted": 0.08e9, None: 0.045e9}
PIECE_BYTES_WIDTH = PIECE_COLS + 2 * piece_halo()

#: What a piece of a pass that draws the painted layer and another adds: the second ground,
#: the meshes and the water over them under the other seabed rule.
SEABED_BYTES = 0.015e9

#: One decoded stored band of every plane a pass reads, at 32768 wide: with the painted
#: layer, and without (None). Scaled by the sheet's width.
STORED_BAND_BYTES = {"painted": 0.16e9, None: 0.125e9}
SHEET_WIDTH = 32768

#: Memory left free for everything but the pieces: the rest of the process and the machine.
RESERVE_BYTES = 2 << 30


def piece_bytes(layers: Collection[str], width: int) -> float:
    """Memory one more piece in flight of a pass over ``layers`` takes, ``width`` columns
    wide with its halo.

    The layers are painted in turn over one ground, so the dearest layer's piece, and the
    second ground when the painted layer and another share the pass.
    """
    piece = max(PIECE_BYTES.get(layer, PIECE_BYTES[None]) for layer in layers)
    if "painted" in layers and len(layers) > 1:
        piece += SEABED_BYTES
    return piece * width / PIECE_BYTES_WIDTH


def pass_bytes(layers: Collection[str], size: int, columns: int, threads: int) -> float:
    """Memory a pass over ``layers`` of a ``size`` sheet takes on ``threads``, in pieces of
    ``columns``: the pieces in flight, and the stored bands decoded for them."""
    width = min(columns + 2 * piece_halo(), size)
    stored = STORED_BAND_BYTES["painted" if "painted" in layers else None]
    held = bands_held(threads, -(-size // columns))
    return threads * piece_bytes(layers, width) + held * stored * size / SHEET_WIDTH


def draw_threads(
    requested: int | None,
    layers: Collection[str],
    size: int,
    free: int | None = None,
    columns: int = PIECE_COLS,
) -> int:
    """Threads to draw a pass over ``layers`` of a ``size`` sheet on; at least one.

    ``requested``, or ``DRAW_THREADS`` but no more than the cores; then no more than ``free``
    bytes hold (``free_ram_bytes()`` when None) once one sheet's bytes and ``RESERVE_BYTES``
    are set aside: about what the cut beside the draw holds (``render/stream.py``).
    """
    want = min(DRAW_THREADS, os.cpu_count() or 1) if requested is None else requested
    free = free_ram_bytes() if free is None else free
    if free is None:
        return max(1, want)
    room = free - size * size * 3 - RESERVE_BYTES
    threads = max(1, want)
    while threads > 1 and pass_bytes(layers, size, columns, threads) > room:
        threads -= 1
    return threads


def bands_held(threads: int, pieces: int = 1) -> int:
    """Bands of a band store the loop reads at once, a band ``pieces`` pieces wide: those the
    pieces in flight span, and a halo either side."""
    return -(-(AHEAD * threads - 1) // pieces) + 3


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


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"at least 1, not {value}")
    return value


def add_draw_flags(parser: argparse.ArgumentParser) -> None:
    """``--draw-threads``, how many pieces are drawn at once, ``--draw-columns``, how wide, and
    ``--gpu``, where the kernels that have a CUDA twin run."""
    add_gpu_flag(parser)
    parser.add_argument(
        "--draw-threads",
        type=int,
        default=None,
        help=(
            f"threads drawing the layers (default {DRAW_THREADS}, fewer on fewer cores; 1 "
            "draws the pieces in turn). Fewer when free memory holds fewer pieces in flight: "
            "at the default width about 0.05 GB each, 0.08 GB with painted and 0.1 GB with "
            "painted and another layer, beside the decoded bands they share. The tiles are "
            "the same bytes"
        ),
    )
    parser.add_argument(
        "--draw-columns",
        type=_positive,
        default=PIECE_COLS,
        help=(
            f"output columns of a band drawn at a time (default {PIECE_COLS}). Narrower pieces "
            "take less memory a thread; the tiles are the same bytes at any width up to 16384"
        ),
    )

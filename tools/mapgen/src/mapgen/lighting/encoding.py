"""A block's tiles encoded on threads: libwebp lets go of the GIL while it encodes, so the
tiles of one block can encode side by side in its light process.

With ``--gpu`` the marches leave a light process little but the encode, which was then most
of a block's time; a block row's blocks share the cores between them, and the encode runs
beside the block's default-sun terms. Without it, one thread a block, in turn, as before. The
bytes are the same either way. docs/map/renders.md section 41, "On the GPU".
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Generator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager

from mapgen.jit import gpu_on
from mapgen.lighting.light_tiles import encode_tiles
from satisfactory_mcp.core.arrays import U8Grid

__all__ = ["ENCODE_BATCH", "ENCODE_THREADS", "encode_beside", "encode_threads", "encoded"]

#: The threads a block's tiles are encoded on at most, and the tiles a thread is handed at a
#: time: only these are made and held at once.
ENCODE_THREADS = 8
ENCODE_BATCH = 4


def encode_threads(row_blocks: int) -> int:
    """With ``--gpu``, the cores a block row leaves each of its ``row_blocks`` blocks, up to
    ``ENCODE_THREADS``; else one."""
    if not gpu_on():
        return 1
    return max(1, min(ENCODE_THREADS, (os.cpu_count() or 1) // max(1, row_blocks)))


def encoded(jobs: Iterator[tuple[str, U8Grid, U8Grid]], threads: int) -> int:
    """``light_tiles.encode_tiles`` of ``jobs`` on ``threads`` threads: the horizon bytes
    written."""
    if threads <= 1:
        return encode_tiles(jobs)
    written = 0
    with ThreadPoolExecutor(threads) as pool:
        while batch := list(itertools.islice(jobs, ENCODE_BATCH * threads)):
            written += sum(pool.map(encode_tiles, ([tile] for tile in batch)))
    return written


@contextmanager
def encode_beside(
    jobs: Iterator[tuple[str, U8Grid, U8Grid]], threads: int
) -> Generator[Future[int], None, None]:
    """``encoded(jobs, threads)``: on one thread, done before the block goes on; on more,
    beside what the block does in the ``with``, and done when it leaves."""
    if threads <= 1:
        done: Future[int] = Future()
        done.set_result(encode_tiles(jobs))
        yield done
        return
    with ThreadPoolExecutor(1) as beside:
        yield beside.submit(encoded, jobs, threads)

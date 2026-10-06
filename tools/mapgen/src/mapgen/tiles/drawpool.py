"""Drawing a layer's bands on threads: how many, and the pool that keeps their order.

The measurements behind the numbers: docs/spatial-and-map.md section 40.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

__all__ = [
    "AHEAD",
    "BAND_BYTES",
    "DRAW_THREADS",
    "RESERVE_BYTES",
    "add_draw_flags",
    "available_memory",
    "bands_held",
    "draw_threads",
    "in_order",
]

T = TypeVar("T")
R = TypeVar("R")

#: Threads a layer is drawn on by default, fewer on a machine with fewer cores. The draw is
#: bound by memory bandwidth, and more threads than this were no faster.
DRAW_THREADS = 8

#: Items submitted per thread ahead of the one waited on.
AHEAD = 2

#: Memory one more band in flight takes, its decoded stored bands included, for a sheet
#: 32768 wide: the painted layer's, and every other layer's (None). Scaled by the width.
BAND_BYTES = {"painted": 3.4e9, None: 1.9e9}
BAND_BYTES_WIDTH = 32768

#: Memory left free for everything but the bands: the rest of the process and the machine.
RESERVE_BYTES = 2 << 30


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("load", ctypes.c_uint32),
        ("total_phys", ctypes.c_uint64),
        ("avail_phys", ctypes.c_uint64),
        ("total_page", ctypes.c_uint64),
        ("avail_page", ctypes.c_uint64),
        ("total_virtual", ctypes.c_uint64),
        ("avail_virtual", ctypes.c_uint64),
        ("avail_extended", ctypes.c_uint64),
    ]


def available_memory() -> int | None:
    """Memory free for new work now, in bytes; None where it cannot be read.

    On Windows the lesser of free physical memory and the commit left: an array commits its
    whole size when it is allocated, so a full commit refuses it with RAM to spare.
    """
    if sys.platform == "win32":
        status = _MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(min(status.avail_phys, status.avail_page))
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def draw_threads(requested: int | None, layer: str, size: int, free: int | None = None) -> int:
    """Threads to draw ``layer`` of a ``size`` sheet on; at least one.

    ``requested``, or ``DRAW_THREADS`` but no more than the cores; then no more bands in flight
    than ``free`` bytes hold (``available_memory()`` when None) once the sheet and
    ``RESERVE_BYTES`` are set aside.
    """
    want = min(DRAW_THREADS, os.cpu_count() or 1) if requested is None else requested
    free = available_memory() if free is None else free
    if free is None:
        return max(1, want)
    band = BAND_BYTES.get(layer, BAND_BYTES[None]) * size / BAND_BYTES_WIDTH
    room = free - size * size * 3 - RESERVE_BYTES
    return max(1, min(want, int(room // band)))


def bands_held(threads: int) -> int:
    """Bands of a band store the loop reads at once: those in flight, and a halo either side."""
    return AHEAD * threads + 2


def in_order(fn: Callable[[T], R], items: Iterable[T], threads: int) -> Iterator[R]:
    """``fn`` over ``items`` on ``threads`` threads, the results yielded in the items' order.

    At most ``AHEAD * threads`` items are submitted past the one waited on. On one thread it
    is a plain loop on the caller's. A failure is raised when its turn comes, after the
    items not yet started are cancelled and the running ones finish.
    """
    if threads <= 1:
        yield from map(fn, items)
        return
    pending: deque = deque()
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
            f"threads drawing a layer (default {DRAW_THREADS}, fewer on fewer cores; 1 draws "
            "the bands in turn). Fewer when free memory holds fewer bands in flight: about "
            "1.9 GB each at full size, 3.4 GB for painted. The tiles are the same bytes"
        ),
    )

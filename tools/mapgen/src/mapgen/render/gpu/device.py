"""What the draw's CUDA kernels share: their sources, the grids they launch on, the device
memory they may hold, where each call ran, and a band's planes kept on the device.

Imported only when ``mapgen.jit.gpu_on()``. docs/map/renders.md section 41, "The draw on the GPU".
"""

from __future__ import annotations

import functools
import threading
from collections import Counter
from collections.abc import Callable
from typing import TypeVar

import cupy as cp
import numpy as np
from numpy.typing import NDArray

from mapgen.jit import cuda_kernel

__all__ = [
    "DRAW_DEVICE_BYTES",
    "FLAT_THREADS",
    "ON_CPU",
    "ROW_THREADS",
    "DeviceBand",
    "calls_line",
    "device_name",
    "flat_grid",
    "kernel",
    "on_device",
    "ran",
    "row_grid",
]

#: Threads of a block over pixels laid flat, and along a row of a 2-D launch.
FLAT_THREADS = 256
ROW_THREADS = 128

#: Device memory the draw's process may hold at once; past it a call runs on the CPU.
DRAW_DEVICE_BYTES = 2 << 30

#: Where a call is counted when the device had no memory for it and the CPU ran it.
ON_CPU = "cpu"

_PACKAGE = "mapgen.render.gpu"

_T = TypeVar("_T")
_calls: Counter[str] = Counter()
_counting = threading.Lock()


def kernel(source: str, name: str) -> cp.RawKernel:
    """The kernel ``name`` of ``render/gpu/<source>``, compiled once a process."""
    _limit_memory()
    return cuda_kernel(_PACKAGE, source, name)


@functools.cache
def _limit_memory() -> None:
    cp.get_default_memory_pool().set_limit(size=DRAW_DEVICE_BYTES)


@functools.cache
def device_name() -> str:
    name: object = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())["name"]
    return name.decode() if isinstance(name, bytes) else str(name)


def flat_grid(count: int) -> tuple[tuple[int], tuple[int]]:
    """Blocks of ``FLAT_THREADS`` over ``count`` items, the last one's spare threads idle."""
    return (max(1, -(-count // FLAT_THREADS)),), (FLAT_THREADS,)


def row_grid(rows: int, cols: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """A block of ``ROW_THREADS`` along a row, a row of blocks per output row."""
    return (max(1, -(-cols // ROW_THREADS)), max(1, rows)), (ROW_THREADS, 1)


def on_device(work: Callable[[], _T]) -> _T | None:
    """``work()``, counted where it ran: on the device, or None, for the CPU to run it, where
    the device has no memory for it."""
    try:
        done = work()
    except MemoryError:  # CuPy's OutOfMemoryError
        _count(ON_CPU)
        return None
    _count(device_name())
    return done


def _count(where: str) -> None:
    with _counting:
        _calls[where] += 1


def ran() -> dict[str, int]:
    """This process's calls since the last ``ran()``, by the device's name or ``ON_CPU``."""
    with _counting:
        counted = dict(_calls)
        _calls.clear()
    return counted


def calls_line(calls: dict[str, int]) -> str:
    """The log line of a ``--gpu`` draw: where its relight, FXAA and terrain calls ran."""
    on_cpu = calls.get(ON_CPU, 0)
    devices = ", ".join(f"{n:,} on {name}" for name, n in sorted(calls.items()) if name != ON_CPU)
    return (
        f"draw: relight, FXAA and terrain calls {devices or 'none on CUDA'}; {on_cpu:,} ran on "
        "the CPU, the device out of memory"
    )


class DeviceBand:
    """A band's planes on the device, each uploaded once however many kernels read it, and
    the planes those kernels leave there for the next; only what is asked for comes back."""

    def __init__(self) -> None:
        self._planes: dict[str, cp.ndarray[np.generic]] = {}

    def upload(self, name: str, host: NDArray[np.generic]) -> cp.ndarray[np.generic]:
        """``host`` on the device under ``name``, uploaded on the first ask only."""
        if name not in self._planes:
            self._planes[name] = cp.asarray(np.ascontiguousarray(host))
        return self._planes[name]

    def keep(self, name: str, plane: cp.ndarray[np.generic]) -> None:
        """A plane a kernel wrote, kept for the kernels after it."""
        self._planes[name] = plane

    def __getitem__(self, name: str) -> cp.ndarray[np.generic]:
        return self._planes[name]

    def __contains__(self, name: str) -> bool:
        return name in self._planes

    def download(self, name: str) -> NDArray[np.generic]:
        """The plane ``name`` back on the host."""
        return self._planes[name].get()

    def drop(self) -> None:
        """Every plane handed back to the device's pool."""
        self._planes.clear()

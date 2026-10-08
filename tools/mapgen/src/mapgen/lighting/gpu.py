"""The light's loops on the GPU: ``kernels.march`` and ``kernels.sky_view``, a thread a pixel.

Each takes its numba twin's arguments and gives its bits: ``gpu.cu`` does the same float32
operations in the same order, compiled without fused multiply-adds (``mapgen.jit``). A call
the device has no memory for runs the twin instead. Each call is counted by where it ran, for
the run's log (``ran``). Imported only when ``mapgen.jit.gpu_on()``. docs/map/renders.md
section 41, "On the GPU".
"""

from __future__ import annotations

import functools
from collections import Counter
from collections.abc import Callable
from typing import TypeVar

import cupy as cp
import numpy as np
from numpy.typing import NDArray

from mapgen.jit import ON_NUMBA, cuda_kernel
from mapgen.lighting import kernels
from mapgen.lighting.kernels import Offsets
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = [
    "ROW_THREADS",
    "STACK_BYTES",
    "count",
    "device",
    "grid",
    "march",
    "march_on_device",
    "on_device",
    "ran",
    "release",
    "sky_view",
]

#: Threads of a block, along a row: a block reads a run of contiguous memory.
ROW_THREADS = 128

_SOURCE = ("mapgen.lighting", "gpu.cu")

_ScalarT = TypeVar("_ScalarT", bound=np.generic)

#: The stack a thread reserves in a light process's context: the kernels here keep none, and
#: CUDA's 1 KB is reserved for every thread the device holds, 0.08 GB a process.
STACK_BYTES = 64
cp.cuda.runtime.deviceSetLimit(cp.cuda.runtime.cudaLimitStackSize, STACK_BYTES)

_calls: Counter[str] = Counter()


def on_device(
    call: Callable[..., None], twin: Callable[..., None], args: tuple[object, ...], *more: object
) -> None:
    """``call(*args)`` on the device, else, out of device memory, numba's ``twin`` with
    ``more`` arguments after them; counted where it ran."""
    try:
        call(*args)
        count(True)
    except MemoryError:  # CuPy's OutOfMemoryError
        twin(*args, *more)
        count(False)
    finally:
        release()


def count(on_device: bool, calls: int = 1) -> None:
    """``calls`` more that ran on the device, or on numba."""
    _calls[_device() if on_device else ON_NUMBA] += calls


def release() -> None:
    """The device memory this process holds and no longer uses, handed back."""
    cp.get_default_memory_pool().free_all_blocks()


def device(array: NDArray[_ScalarT]) -> cp.ndarray[_ScalarT]:
    """``array`` on the device."""
    return cp.asarray(array)


def march(
    solid: F32Grid,
    z: F32Grid,
    halo: int,
    bilinear: BoolMask,
    offsets: Offsets,
    scale: F32Grid,
    best: F32Grid,
) -> None:
    """``kernels.march``: ``best`` raised in place. Every array C-ordered."""
    on_device(_march, kernels.march, (solid, z, halo, bilinear, offsets, scale, best))


def sky_view(z: F32Grid, halo: int, offsets: Offsets, scale: F32Grid, out: F32Grid) -> None:
    """``kernels.sky_view``: ``out`` written in place; the offsets are ``(dirs, steps)``."""
    on_device(_sky_view, kernels.sky_view, (z, halo, offsets, scale, out))


def ran() -> dict[str, int]:
    """This process's calls since the last ``ran()``, by the device's name or ``ON_NUMBA``."""
    counted = dict(_calls)
    _calls.clear()
    return counted


@functools.cache
def _device() -> str:
    name: object = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())["name"]
    return name.decode() if isinstance(name, bytes) else str(name)


def _march(
    solid: F32Grid,
    z: F32Grid,
    halo: int,
    bilinear: BoolMask,
    offsets: Offsets,
    scale: F32Grid,
    best: F32Grid,
) -> None:
    on_solid = device(solid)
    on_z = on_solid if z is solid else device(z)
    on_best = cp.asarray(best)
    march_on_device(on_solid, on_z, halo, (bilinear, offsets, scale), on_best)
    best[...] = on_best.get()


def march_on_device(
    solid: cp.ndarray[np.float32],
    z: cp.ndarray[np.float32],
    halo: int,
    steps: tuple[BoolMask, Offsets, F32Grid],
    best: cp.ndarray[np.float32],
) -> None:
    """``kernels.march`` over planes on the device (``horizon.kernel_steps``): ``best``
    raised in place."""
    bilinear, offsets, scale = steps
    rows, cols = best.shape
    args = (
        solid,
        np.int64(solid.shape[1]),
        z,
        np.int64(z.shape[1]),
        np.int32(halo),
        cp.asarray(np.ascontiguousarray(bilinear, np.bool_)),
        *_steps(offsets),
        cp.asarray(scale),
        np.int32(scale.shape[0]),
        best,
        np.int32(rows),
        np.int32(cols),
    )
    cuda_kernel(*_SOURCE, "march")(*grid(rows, cols), args)


def _sky_view(z: F32Grid, halo: int, offsets: Offsets, scale: F32Grid, out: F32Grid) -> None:
    run = cuda_kernel(*_SOURCE, "sky_view")
    dirs, steps = offsets[0].shape
    rows, cols = out.shape
    on_out = cp.empty((rows, cols), np.float32)
    args = (
        device(z),
        np.int64(z.shape[1]),
        np.int32(halo),
        *_steps(offsets),
        cp.asarray(scale),
        np.int32(dirs),
        np.int32(steps),
        on_out,
        np.int32(rows),
        np.int32(cols),
    )
    run(*grid(rows, cols), args)
    out[...] = on_out.get()


def _steps(offsets: Offsets) -> tuple[cp.ndarray[np.int32] | cp.ndarray[np.float32], ...]:
    """The offsets on the device: whole pixels as int32, the fractions as they are."""
    iy, ix, fy, fx, gy, gx = offsets
    whole = tuple(cp.asarray(np.ascontiguousarray(part, np.int32)) for part in (iy, ix))
    return (*whole, *(cp.asarray(np.ascontiguousarray(part)) for part in (fy, fx, gy, gx)))


def grid(rows: int, cols: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """A block of ``ROW_THREADS`` along a row, a row of blocks per output row."""
    return (-(-cols // ROW_THREADS), rows), (ROW_THREADS, 1)

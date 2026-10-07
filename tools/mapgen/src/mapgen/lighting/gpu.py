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

import cupy as cp
import numpy as np

from mapgen.jit import ON_NUMBA, cuda_kernel
from mapgen.lighting import kernels
from mapgen.lighting.kernels import Offsets
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = ["ROW_THREADS", "march", "ran", "sky_view"]

#: Threads of a block, along a row: a block reads a run of contiguous memory.
ROW_THREADS = 128

_SOURCE = ("mapgen.lighting", "gpu.cu")

_calls: Counter[str] = Counter()


def march(
    solid: F32Grid, z: F32Grid, halo: int, bilinear: BoolMask, offsets: Offsets,
    scale: F32Grid, best: F32Grid, lo: F32Grid, hi: F32Grid, slabbed: bool,
) -> None:  # fmt: skip
    """``kernels.march``: ``best`` raised in place. Every array C-ordered, the slabs float32."""
    try:
        _march(solid, z, halo, bilinear, offsets, scale, best, lo, hi, slabbed)
        _calls[_device()] += 1
    except MemoryError:  # CuPy's OutOfMemoryError
        kernels.march(solid, z, halo, bilinear, offsets, scale, best, lo, hi, slabbed)
        _calls[ON_NUMBA] += 1
    finally:
        cp.get_default_memory_pool().free_all_blocks()


def sky_view(z: F32Grid, halo: int, offsets: Offsets, scale: F32Grid, out: F32Grid) -> None:
    """``kernels.sky_view``: ``out`` written in place; the offsets are ``(dirs, steps)``."""
    try:
        _sky_view(z, halo, offsets, scale, out)
        _calls[_device()] += 1
    except MemoryError:  # CuPy's OutOfMemoryError
        kernels.sky_view(z, halo, offsets, scale, out)
        _calls[ON_NUMBA] += 1
    finally:
        cp.get_default_memory_pool().free_all_blocks()


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
    solid: F32Grid, z: F32Grid, halo: int, bilinear: BoolMask, offsets: Offsets,
    scale: F32Grid, best: F32Grid, lo: F32Grid, hi: F32Grid, slabbed: bool,
) -> None:  # fmt: skip
    run = cuda_kernel(*_SOURCE, "march")
    on_solid = cp.asarray(solid)
    on_z = on_solid if z is solid else cp.asarray(z)
    on_best = cp.asarray(best)
    rows, cols = best.shape
    args = (
        on_solid, np.int64(solid.shape[1]), on_z, np.int64(z.shape[1]), np.int32(halo),
        cp.asarray(np.ascontiguousarray(bilinear, np.bool_)), *_steps(offsets), cp.asarray(scale),
        np.int32(scale.shape[0]), cp.asarray(lo), cp.asarray(hi), np.int64(lo.shape[1]),
        np.bool_(slabbed), on_best, np.int32(rows), np.int32(cols),
    )  # fmt: skip
    run(*_grid(rows, cols), args)
    best[...] = on_best.get()


def _sky_view(z: F32Grid, halo: int, offsets: Offsets, scale: F32Grid, out: F32Grid) -> None:
    run = cuda_kernel(*_SOURCE, "sky_view")
    dirs, steps = offsets[0].shape
    rows, cols = out.shape
    on_out = cp.empty((rows, cols), np.float32)
    args = (
        cp.asarray(z), np.int64(z.shape[1]), np.int32(halo), *_steps(offsets), cp.asarray(scale),
        np.int32(dirs), np.int32(steps), on_out, np.int32(rows), np.int32(cols),
    )  # fmt: skip
    run(*_grid(rows, cols), args)
    out[...] = on_out.get()


def _steps(offsets: Offsets) -> tuple[cp.ndarray[np.int32] | cp.ndarray[np.float32], ...]:
    """The offsets on the device: whole pixels as int32, the fractions as they are."""
    iy, ix, fy, fx, gy, gx = offsets
    whole = tuple(cp.asarray(np.ascontiguousarray(part, np.int32)) for part in (iy, ix))
    return (*whole, *(cp.asarray(np.ascontiguousarray(part)) for part in (fy, fx, gy, gx)))


def _grid(rows: int, cols: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """A block of ``ROW_THREADS`` along a row, a row of blocks per output row."""
    return (-(-cols // ROW_THREADS), rows), (ROW_THREADS, 1)

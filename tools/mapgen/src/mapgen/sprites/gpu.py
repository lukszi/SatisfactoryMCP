"""The sprite raster's per-sample kernels on the GPU: ``fill.top_hits`` and ``shade.shade``
as ``raster.cu``, a thread a sample.

Each kernel gives its reference's bits: the same float32 operations in the same order,
compiled without fused multiply-adds (``mapgen.jit``). A call the device has no memory for
runs the reference instead. Each call is counted by where it ran (``ran``). Imported only
when ``mapgen.jit.gpu_on()``. docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

import functools
from collections import Counter

import cupy as cp
import numpy as np

from mapgen.jit import REFERENCE, cuda_kernel
from mapgen.sprites import fill, shade
from mapgen.sprites.fill import Hits, Triangles
from mapgen.sprites.shade import Shading, ShadingTables
from satisfactory_mcp.core.arrays import I32Grid, U8Grid

__all__ = ["ROW_THREADS", "ran", "shade_samples", "top_hits"]

#: Threads of a block, along a row of samples.
ROW_THREADS = 128

_SOURCE = ("mapgen.sprites", "raster.cu")

_calls: Counter[str] = Counter()


def top_hits(
    tris: Triangles, texels: U8Grid, table: I32Grid, rows: int, cols: int, bin_side: int
) -> Hits:
    """``fill.top_hits`` on the device, binned ``bin_side`` samples a side."""
    try:
        found = _top_hits(tris, texels, table, rows, cols, bin_side)
        _calls[_device()] += 1
    except MemoryError:  # CuPy's OutOfMemoryError
        found = fill.top_hits(tris, texels, table, rows, cols)
        _calls[REFERENCE] += 1
    finally:
        cp.get_default_memory_pool().free_all_blocks()
    return found


def shade_samples(
    hits: Hits,
    tris: Triangles,
    tables: ShadingTables,
    origin_cm: tuple[float, float],
    sample_cm: float,
) -> Shading:
    """``shade.shade`` on the device."""
    try:
        found = _shade(hits, tris, tables, origin_cm, sample_cm)
        _calls[_device()] += 1
    except MemoryError:  # CuPy's OutOfMemoryError
        found = shade.shade(hits, tris, tables, origin_cm, sample_cm)
        _calls[REFERENCE] += 1
    finally:
        cp.get_default_memory_pool().free_all_blocks()
    return found


def ran() -> dict[str, int]:
    """This process's calls since the last ``ran()``, by the device's name or ``REFERENCE``."""
    counted = dict(_calls)
    _calls.clear()
    return counted


@functools.cache
def _device() -> str:
    name: object = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())["name"]
    return name.decode() if isinstance(name, bytes) else str(name)


def _grid(rows: int, cols: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """A block of ``ROW_THREADS`` along a row, a row of blocks per row of samples."""
    return (-(-cols // ROW_THREADS), rows), (ROW_THREADS, 1)


def _top_hits(
    tris: Triangles, texels: U8Grid, table: I32Grid, rows: int, cols: int, bin_side: int
) -> Hits:
    run = cuda_kernel(*_SOURCE, "top_hits")
    best = cp.empty((rows, cols), np.float32)
    which = cp.empty((rows, cols), np.int32)
    w1 = cp.empty((rows, cols), np.float32)
    w2 = cp.empty((rows, cols), np.float32)
    args = (
        cp.asarray(np.ascontiguousarray(tris.setup, np.float32)),
        cp.asarray(np.ascontiguousarray(tris.bounds, np.int32)),
        cp.asarray(np.ascontiguousarray(tris.bin_start, np.int32)),
        cp.asarray(np.ascontiguousarray(tris.bin_tris, np.int32)),
        np.int32(tris.bins_across),
        np.int32(bin_side),
        cp.asarray(np.ascontiguousarray(texels, np.uint8)),
        cp.asarray(np.ascontiguousarray(table, np.int32)),
        fill.ALPHA_CUT,
        np.int32(rows),
        np.int32(cols),
        best,
        which,
        w1,
        w2,
    )
    run(*_grid(rows, cols), args)
    return Hits(best.get(), which.get(), w1.get(), w2.get())


def _shade(
    hits: Hits,
    tris: Triangles,
    tables: ShadingTables,
    origin_cm: tuple[float, float],
    sample_cm: float,
) -> Shading:
    run = cuda_kernel(*_SOURCE, "shade")
    rows, cols = hits.tri.shape
    colour = cp.asarray(np.zeros((rows, cols, 3), np.float32))
    normal = cp.asarray(np.zeros((rows, cols, 3), np.float32))
    args = (
        cp.asarray(np.ascontiguousarray(tris.setup, np.float32)),
        cp.asarray(np.ascontiguousarray(tris.bounds, np.int32)),
        cp.asarray(np.ascontiguousarray(tables.attrs, np.float32)),
        cp.asarray(np.ascontiguousarray(hits.tri, np.int32)),
        cp.asarray(np.ascontiguousarray(hits.b1, np.float32)),
        cp.asarray(np.ascontiguousarray(hits.b2, np.float32)),
        cp.asarray(np.ascontiguousarray(hits.z, np.float32)),
        cp.asarray(np.ascontiguousarray(tables.albedo, np.float32)),
        cp.asarray(np.ascontiguousarray(tables.normals, np.float32)),
        cp.asarray(np.ascontiguousarray(tables.ints, np.int32)),
        cp.asarray(np.ascontiguousarray(tables.floats, np.float32)),
        np.float32(origin_cm[0]),
        np.float32(origin_cm[1]),
        np.float32(sample_cm),
        np.int32(rows),
        np.int32(cols),
        colour,
        normal,
    )
    run(*_grid(rows, cols), args)
    return Shading(colour.get(), normal.get())

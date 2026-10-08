"""The footprints' two loops on the GPU: ``reference.texel_marks`` and ``reference.pixel_land``,
a thread a texel or a pixel.

Each takes the reference's arguments and gives its bits: ``footprints.cu`` does the same
operations in the same order, compiled without fused multiply-adds (``mapgen.jit``). A call
the device has no memory for runs the reference instead. Imported only when
``mapgen.jit.gpu_on()``. docs/map/renders.md section 41, "On the GPU".
"""

from __future__ import annotations

import cupy as cp
import numpy as np
from numpy.typing import NDArray

from mapgen.jit import cuda_kernel
from mapgen.palette.water.footprints import reference
from mapgen.palette.water.footprints.reference import GROUPS, AxisCover, TexelPlanes
from satisfactory_mcp.core.arrays import BoolMask, I64Grid, U8Grid

__all__ = ["ROW_THREADS", "pixel_land", "texel_marks"]

#: Threads of a block, along a row: a block reads a run of contiguous memory.
ROW_THREADS = 128

_SOURCE = ("mapgen.palette.water.footprints", "footprints.cu")

#: ``TexelPlanes``' element types, as the kernel reads them.
_TEXEL_TYPES = (np.uint8, np.uint8, np.float32, np.int16)


def texel_marks(
    cls: U8Grid, z_cm: NDArray[np.float32], rows: AxisCover, cols: AxisCover, texels: TexelPlanes
) -> U8Grid:
    """``reference.texel_marks``."""
    try:
        return _texel_marks(cls, z_cm, rows, cols, texels)
    except MemoryError:  # CuPy's OutOfMemoryError
        return reference.texel_marks(cls, z_cm, rows, cols, texels)
    finally:
        cp.get_default_memory_pool().free_all_blocks()


def pixel_land(land: U8Grid, rows: I64Grid, cols: I64Grid, cls: U8Grid) -> BoolMask:
    """``reference.pixel_land``: only the texels the piece reads go to the device."""
    try:
        return _pixel_land(land, rows, cols, cls)
    except MemoryError:  # CuPy's OutOfMemoryError
        return reference.pixel_land(land, rows, cols, cls)
    finally:
        cp.get_default_memory_pool().free_all_blocks()


def _texel_marks(
    cls: U8Grid, z_cm: NDArray[np.float32], rows: AxisCover, cols: AxisCover, texels: TexelPlanes
) -> U8Grid:
    run = cuda_kernel(*_SOURCE, "texel_marks")
    out_rows, out_cols = texels.wet.shape
    length = (cls.shape[0], cls.shape[1])
    on_marks = cp.empty((out_rows, out_cols), np.uint8)
    args = (
        _up(cls, np.uint8),
        _up(z_cm, np.float32),
        np.int64(cls.shape[1]),
        *(_up(np.clip(part, 0, length[0]), np.int64) for part in rows),
        *(_up(np.clip(part, 0, length[1]), np.int64) for part in cols),
        _up(GROUPS, np.uint8),
        *(_up(plane, kind) for plane, kind in zip(texels, _TEXEL_TYPES, strict=True)),
        reference.NO_GROUND,
        reference.OCEAN_DM,
        reference.OCEAN_M,
        reference.REACH_M,
        on_marks,
        np.int32(out_rows),
        np.int32(out_cols),
    )
    run(*_grid(out_rows, out_cols), args)
    return on_marks.get()


def _pixel_land(land: U8Grid, rows: I64Grid, cols: I64Grid, cls: U8Grid) -> BoolMask:
    run = cuda_kernel(*_SOURCE, "pixel_land")
    r0, c0 = int(rows.min()), int(cols.min())
    slab = land[r0 : int(rows.max()) + 1, c0 : int(cols.max()) + 1]
    out_rows, out_cols = cls.shape
    on_out = cp.empty((out_rows, out_cols), np.bool_)
    args = (
        _up(slab, np.uint8),
        np.int64(slab.shape[1]),
        _up(rows - r0, np.int64),
        _up(cols - c0, np.int64),
        _up(cls, np.uint8),
        _up(GROUPS, np.uint8),
        on_out,
        np.int32(out_rows),
        np.int32(out_cols),
    )
    run(*_grid(out_rows, out_cols), args)
    return on_out.get()


def _up(plane: NDArray[np.generic], kind: type[np.generic]) -> cp.ndarray[np.generic]:
    """``plane`` on the device as ``kind``, C-ordered as the kernels index it."""
    return cp.asarray(np.ascontiguousarray(plane, kind))


def _grid(rows: int, cols: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """A block of ``ROW_THREADS`` along a row, a row of blocks per output row."""
    return (-(-cols // ROW_THREADS), rows), (ROW_THREADS, 1)

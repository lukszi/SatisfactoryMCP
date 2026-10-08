"""A coarser level's horizons, each texel from the 2 x 2 below it, averaged as shade.

The page shades a horizon at the sun path's elevation by a 6-degree soft edge. A mean of
degrees loses a thin shadow beside lit ground; the mean of the four shades, folded back as
``spans.bake.path_horizon`` folds a band, keeps it for a sun on the path. Pure float32
arrays; ``refold.cu`` is the CUDA twin, bit for bit, where ``mapgen.jit.gpu_on()``.
docs/map/light-and-crowns.md section 29, "Coarser levels".
"""

from __future__ import annotations

import functools

import numpy as np

from mapgen.jit import cuda_kernel, gpu_on
from mapgen.lighting.horizon import HORIZON_DIRS
from mapgen.lighting.model import SHADOW_SOFT_DEG
from mapgen.lighting.spans.march import path_elevation
from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["REFOLD_VALUES", "TREE_GROUPS", "path_elevations", "refold"]

#: About as many output values as the reference makes at a time, so its temporaries stay small.
REFOLD_VALUES = 1 << 20

#: The groups of tree cells after the ground's a refold takes: the crowns', the Titan trees'.
TREE_GROUPS = 2

#: Threads of a block, along a row of texels and their directions.
_ROW_THREADS = 128

_SOFT = np.float32(SHADOW_SOFT_DEG)
_ZERO, _QUARTER, _HALF, _ONE = (np.float32(v) for v in (0.0, 0.25, 0.5, 1.0))

Quad = tuple[F32Grid, F32Grid, F32Grid, F32Grid]


@functools.cache
def path_elevations() -> F32Grid:
    """The elevation each ground direction's band is folded at, as ``path_horizon`` takes it."""
    els = np.array(
        [path_elevation(k * 360.0 / HORIZON_DIRS) for k in range(HORIZON_DIRS)], np.float32
    )
    els.flags.writeable = False
    return els


def refold(fine: F32Grid, el: F32Grid, gpu: bool | None = None) -> F32Grid:
    """``fine`` is ``(2 rows, 2 cols, cells)`` degrees: ``el``'s directions, the ground's, then
    up to ``TREE_GROUPS`` more groups of them, the trees' alone. Returns ``(rows, cols,
    cells)``.

    A texel takes the mean shade at its direction's ``el``, refolded, else, all lit or all
    shaded, the mean of its degrees. A tree cell takes its own, except where the larger of it
    and the ground's shades more on average than the ground's: there it takes that, so the
    page's maximum of the two is the mean of the larger. ``gpu`` None follows the switch;
    False keeps a process that opens no CUDA context off the device.
    """
    rows, cols, cells = fine.shape[0] // 2, fine.shape[1] // 2, fine.shape[2]
    if fine.dtype != np.float32 or el.dtype != np.float32 or el.ndim != 1:
        raise TypeError("refold takes float32 degrees and a float32 elevation per direction")
    groups = [k * el.size for k in range(1, TREE_GROUPS + 2)]
    if fine.shape[:2] != (2 * rows, 2 * cols) or cells not in groups:
        raise ValueError(f"{fine.shape} is not 2 x 2 texels of {el.size} directions")
    if gpu_on() if gpu is None else gpu:
        try:
            return _on_gpu(fine, el)
        except MemoryError:  # CuPy's OutOfMemoryError: the reference has the same bits
            pass
    return _reference(fine, el)


def _reference(fine: F32Grid, el: F32Grid) -> F32Grid:
    dirs, rows, cols, cells = el.size, fine.shape[0] // 2, fine.shape[1] // 2, fine.shape[2]
    out = np.empty((rows, cols, cells), np.float32)
    step = max(1, REFOLD_VALUES // max(1, cols * cells))
    for top in range(0, rows, step):
        part = fine[2 * top : 2 * min(top + step, rows)]
        a, b, c, d = (part[i::2, j::2] for i in (0, 1) for j in (0, 1))
        ground, shaded = _mean_shade(
            (a[..., :dirs], b[..., :dirs], c[..., :dirs], d[..., :dirs]), el
        )
        done = slice(top, top + ground.shape[0])
        out[done, :, :dirs] = ground
        for first in range(dirs, cells, dirs):
            trees = slice(first, first + dirs)
            own, _ = _mean_shade((a[..., trees], b[..., trees], c[..., trees], d[..., trees]), el)
            quad = tuple(_larger(q[..., trees], q[..., :dirs]) for q in (a, b, c, d))
            over, raised = _mean_shade((quad[0], quad[1], quad[2], quad[3]), el)
            out[done, :, trees] = np.where(raised > shaded, over, own)
    return out


def _larger(a: F32Grid, b: F32Grid) -> F32Grid:
    return np.where(a > b, a, b)


def _shade(h: F32Grid, el: F32Grid) -> F32Grid:
    return np.minimum(np.maximum((h - el) / _SOFT + _HALF, _ZERO), _ONE)


def _mean_shade(quad: Quad, el: F32Grid) -> tuple[F32Grid, F32Grid]:
    """Four texels' horizon as a coarser texel stores it, and their mean shade."""
    a, b, c, d = quad
    shade = ((_shade(a, el) + _shade(b, el)) + (_shade(c, el) + _shade(d, el))) * _QUARTER
    mean = ((a + b) + (c + d)) * _QUARTER
    folded = el + _SOFT * (shade - _HALF)
    stored: F32Grid = np.where((shade > _ZERO) & (shade < _ONE), folded, mean)
    return stored, shade.astype(np.float32, copy=False)


def _on_gpu(fine: F32Grid, el: F32Grid) -> F32Grid:
    import cupy as cp

    run = cuda_kernel("mapgen.lighting", "refold.cu", "refold")
    rows, cols, cells = fine.shape[0] // 2, fine.shape[1] // 2, fine.shape[2]
    try:
        out = cp.empty((rows, cols, cells), np.float32)
        args = (
            cp.asarray(np.ascontiguousarray(fine)),
            cp.asarray(np.ascontiguousarray(el)),
            _SOFT,
            np.int32(el.size),
            np.int32(cells),
            out,
            np.int32(rows),
            np.int32(cols),
        )
        run((-(-cols * el.size // _ROW_THREADS), rows), (_ROW_THREADS, 1), args)
        return out.get()
    finally:
        cp.get_default_memory_pool().free_all_blocks()

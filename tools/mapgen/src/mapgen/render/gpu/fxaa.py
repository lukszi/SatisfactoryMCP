"""The arches' FXAA on the GPU: ``render/draw/archaa.py``'s ``arch_fxaa``, a thread a pixel.

The mask's dilation, the luma and the filter run on the device; only the columns each piece
spans come back to the host, which cuts the pieces as ``column_pieces`` does, so every pixel
is filtered inside its own piece as numpy filters it. Imported only when
``mapgen.jit.gpu_on()``. docs/map/renders.md section 43.
"""

from __future__ import annotations

import cupy as cp
import numpy as np
from numpy.typing import NDArray

from mapgen.render.draw.archaa import (
    MASK_DILATE_PX,
    PIECE_MARGIN,
    SUBPIX,
    THRESHOLD,
    THRESHOLD_MIN,
)
from mapgen.render.gpu.device import flat_grid, kernel, on_device, row_grid
from mapgen.terrain.archfill import column_pieces
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = ["arch_fxaa", "fxaa_rows"]

_SOURCE = "fxaa.cu"

#: Rec. 601 luma's weights, as ``archaa._luma`` multiplies them in.
_LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def arch_fxaa(rgb: U8Grid, cover: NDArray[np.generic], rows: slice) -> U8Grid | None:
    """``archaa.arch_fxaa(rgb, cover, rows)``; None where the device has no memory for it."""
    return on_device(lambda: fxaa_rows(cp.asarray(np.ascontiguousarray(rgb)), cover, rows).get())


def fxaa_rows(
    on_rgb: cp.ndarray[np.uint8], cover: NDArray[np.generic], rows: slice
) -> cp.ndarray[np.uint8]:
    """``rows`` of the band ``on_rgb``, already on the device, antialiased on the arches of
    ``cover``, there. Raises the device's ``MemoryError``."""
    h, w = on_rgb.shape[0], on_rgb.shape[1]
    top, stop, _step = rows.indices(h)
    count = stop - top
    keep = cp.empty((count, w), np.uint8)
    on_cover = cp.asarray(np.ascontiguousarray(np.asarray(cover, np.uint8)))
    grow = (on_cover, np.int32(h), np.int32(w), np.int32(top), np.int32(count))
    kernel(_SOURCE, "grow")(*row_grid(count, w), (*grow, np.int32(MASK_DILATE_PX), keep))
    column = cp.empty(w, np.uint8)
    kernel(_SOURCE, "any_rows")(*flat_grid(w), (keep, np.int32(count), np.int32(w), column))
    lo, hi = _piece_bounds(column.get(), w)
    if not (lo >= 0).any():
        return on_rgb[top : top + count].copy()
    lum = cp.empty((h, w), np.float32)
    pixels = h * w
    scale = np.float32(1.0 / 255.0)
    weights = cp.asarray(_LUMA)
    kernel(_SOURCE, "luma")(*flat_grid(pixels), (on_rgb, scale, weights, lum, np.int64(pixels)))
    out = cp.empty((count, w, 3), np.uint8)
    knobs = cp.asarray(_knobs())
    bounds = (cp.asarray(lo), cp.asarray(hi), knobs, out)
    head = (on_rgb, lum, np.int32(h), np.int32(w), np.int32(top), np.int32(count), keep)
    kernel(_SOURCE, "arch_fxaa")(*row_grid(count, w), (*head, *bounds))
    return out


def _piece_bounds(column: U8Grid, w: int) -> tuple[NDArray[np.int32], NDArray[np.int32]]:
    """Each column's piece ``[lo, hi)`` as ``column_pieces`` cuts them; ``lo`` -1 off them."""
    lo = np.full(w, -1, np.int32)
    hi = np.zeros(w, np.int32)
    for c0, c1 in column_pieces(column[None, :] > 0, PIECE_MARGIN):
        lo[c0:c1], hi[c0:c1] = c0, c1
    return lo, hi


def _knobs() -> F32Grid:
    """``arch_fxaa`` of ``fxaa.cu``'s numbers: FXAA's thresholds and sub-pixel strength, the
    average's twelfth, the contrast's floor and a byte's share."""
    return np.array(
        [THRESHOLD, THRESHOLD_MIN, SUBPIX, np.float32(1 / 12), 1e-6, np.float32(1.0 / 255.0)],
        np.float32,
    )

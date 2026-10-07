"""The sub-metre holes an arch's open mesh edges leave in its raster, filled.

Two rules, each bounded to holes under a metre across: an enclosed hole whose widest point
lies within ``FILL_HALF_WIDTH_M`` of the arch, and a pixel the arch covers from opposite
sides within ``FILL_RADIUS_M`` along a row, a column or a diagonal, repeated
``FILL_PASSES`` times. Why: docs/map/light-and-crowns.md section 29, "Arches as spans".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid

__all__ = [
    "FILL_HALF_WIDTH_M",
    "FILL_PASSES",
    "FILL_RADIUS_M",
    "FilledArches",
    "between",
    "fill_arch_holes",
    "specks",
]

#: Coverage this near on both sides closes a slit: 2 px at full size, up to 3 px wide.
FILL_RADIUS_M = 0.46
#: An enclosed hole whose farthest pixel is this near the arch: under a metre across.
FILL_HALF_WIDTH_M = 0.5
FILL_PASSES = 3

#: Along a row, a column and both diagonals.
_AXES = ((0, 1), (1, 0), (1, 1), (1, -1))
#: The widest box the filled heights are averaged over.
_MEAN_MAX_PX = 15


class FilledArches(NamedTuple):
    """The arch top and underside with their holes filled (cm, NaN where none), and the fill."""

    top: F32Grid
    under: F32Grid
    filled: BoolMask


def _shift(a: BoolMask, dy: int, dx: int) -> BoolMask:
    """``a`` moved by ``(dy, dx)``, False where it moved in from outside."""
    out = np.zeros_like(a)
    h, w = a.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def between(cover: BoolMask, radius: int) -> BoolMask:
    """Uncovered pixels with cover within ``radius`` on both sides along some axis."""
    out = np.zeros(cover.shape, bool)
    for dy, dx in _AXES:
        plus = np.zeros(cover.shape, bool)
        minus = np.zeros(cover.shape, bool)
        for k in range(1, radius + 1):
            plus |= _shift(cover, -k * dy, -k * dx)
            minus |= _shift(cover, k * dy, k * dx)
        out |= plus & minus
    return out & ~cover


def specks(cover: BoolMask, half_width_px: float) -> BoolMask:
    """Holes the cover encloses whose farthest pixel is within ``half_width_px`` of it."""
    holes: BoolMask = ndimage.binary_fill_holes(cover) & ~cover
    labels, count = ndimage.label(holes)
    if not count:
        return holes
    depth = ndimage.distance_transform_edt(holes)
    widest = np.asarray(ndimage.maximum(depth, labels, np.arange(count + 1)))
    thin = widest <= half_width_px
    thin[0] = False
    return thin[labels]


def _neighbour_mean(values: F32Grid, have: BoolMask, todo: BoolMask) -> F32Grid:
    """``todo`` filled from the mean of ``have`` in a box growing until none is left."""
    out: F64Grid = np.where(have, values, 0.0).astype(np.float64)
    known = have.copy()
    left = todo & ~known
    size = 3
    while left.any() and size <= _MEAN_MAX_PX:
        num = ndimage.uniform_filter(np.where(known, out, 0.0), size, mode="constant")
        den = ndimage.uniform_filter(known.astype(np.float64), size, mode="constant")
        got = left & (den > 1e-9)
        out[got] = num[got] / den[got]
        known |= got
        left &= ~got
        size += 2
    return out.astype(np.float32)


def fill_arch_holes(top_cm: F32Grid, under_cm: F32Grid, spacing_m: float) -> FilledArches:
    """``top_cm`` and ``under_cm`` with the sub-metre holes filled, on a grid ``spacing_m``
    apart; a grid too coarse for a hole of a pixel is returned as it is."""
    cover = np.isfinite(top_cm)
    radius = round(FILL_RADIUS_M / spacing_m)
    if radius < 1 or not cover.any():
        return FilledArches(top_cm, under_cm, np.zeros(cover.shape, bool))
    fill = specks(cover, FILL_HALF_WIDTH_M / spacing_m)
    slit = np.zeros(cover.shape, bool)
    for _ in range(FILL_PASSES):
        more = between(cover | fill | slit, radius)
        if not more.any():
            break
        slit |= more
    fill |= slit
    if not fill.any():
        return FilledArches(top_cm, under_cm, fill)
    top = np.where(fill, _neighbour_mean(top_cm, cover, fill), top_cm).astype(np.float32)
    under = np.where(fill, _neighbour_mean(under_cm, cover, fill), under_cm).astype(np.float32)
    return FilledArches(top, np.minimum(under, top), fill)

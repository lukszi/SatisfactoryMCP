"""The sampler's gathers and the crown stamps for numba.

Separable interpolation and PCHIP onto the output grid reproduce their numpy reference in
``sample`` bit for bit: per output pixel the same operations in the same order and at the
same precision, taps in the order numpy adds them. The source slab is read as it is stored,
no float32 copy of it is made. The crown stamps reproduce ``crown_stamp._stamp`` tree by tree,
from the placements ``crown_stamp`` works out in numpy, every operation float32 as there.
Imported only when ``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I32Grid, I64Grid

__all__ = ["clip_unit", "pchip", "raise_nan", "separable", "stamp"]

_ZERO = np.float32(0.0)
_HALF = np.float32(0.5)
_ONE = np.float32(1.0)
_TWO = np.float32(2.0)
_THREE = np.float32(3.0)

_Weights: TypeAlias = NDArray[np.floating]


@kernel
def separable(
    slab: NDArray[np.generic],
    nodata: int,
    holes: bool,
    weigh: bool,
    low: int,
    rows: tuple[I64Grid, _Weights],
    cols: tuple[I64Grid, _Weights],
) -> tuple[F32Grid, F32Grid]:
    """``sample.resample`` on the source rows ``slab`` (from ``low``): ``(sum, weight)``.

    ``holes`` says ``nodata`` marks texels without a value; ``weigh`` false leaves the weight
    a 1 x 1 zero, which is ``sample.sample_plain``.
    """
    (row_index, row_weight), (col_index, col_weight) = rows, cols
    width = col_index.shape[1]
    across = np.empty((slab.shape[0], width), np.float32)
    across_weight = np.zeros((slab.shape[0] if weigh else 1, width), np.float32)
    for r in range(slab.shape[0]):
        for j in range(width):
            value = weight = _ZERO
            for tap in range(col_index.shape[0]):
                raw, w = slab[r, col_index[tap, j]], col_weight[tap, j]
                known = _ONE if raw != nodata else _ZERO
                texel = np.float32(raw) * known if holes else np.float32(raw)
                value = np.float32(value + w * texel)
                weight = np.float32(weight + (w * known if holes else w))
            across[r, j] = value
            if weigh:
                across_weight[r, j] = weight
    total = np.zeros((row_index.shape[1], width), np.float32)
    total_weight = np.zeros((row_index.shape[1] if weigh else 1, width), np.float32)
    for i in range(row_index.shape[1]):
        for tap in range(row_index.shape[0]):
            picked, w = row_index[tap, i] - low, row_weight[tap, i]
            for j in range(width):
                total[i, j] += w * across[picked, j]
            if weigh:
                for j in range(width):
                    total_weight[i, j] += w * across_weight[picked, j]
    return total, total_weight


@helper
def _value(raw: np.generic, nodata: int) -> np.float32:
    """A texel as ``resample_pchip`` reads it: its value, or 0 where it has none."""
    return np.float32(raw) if raw != nodata else _ZERO


@helper
def _slope(left: np.float32, right: np.float32) -> np.float32:
    """``sample.pchip_slope`` at one pixel."""
    if left * right > _ZERO:
        return _TWO * left * right / (left + right)
    return _ZERO


@helper
def _hermite(
    p0: np.float32, p1: np.float32, p2: np.float32, p3: np.float32, t: np.float32
) -> np.float32:
    """``sample.pchip_1d`` at one pixel."""
    middle = p2 - p1
    d1 = _slope(p1 - p0, middle)
    d2 = _slope(middle, p3 - p2)
    t2 = t * t
    t3 = t2 * t
    return (
        (_TWO * t3 - _THREE * t2 + _ONE) * p1
        + (t3 - _TWO * t2 + t) * d1
        + (_THREE * t2 - _TWO * t3) * p2
        + (t3 - t2) * d2
    )


@kernel
def pchip(
    slab: NDArray[np.generic],
    nodata: int,
    low: int,
    rows: tuple[I64Grid, F32Grid],
    cols: tuple[I64Grid, F32Grid],
) -> tuple[F32Grid, BoolMask]:
    """``sample.resample_pchip`` on the source rows ``slab`` from ``low``: ``(values, whole)``."""
    (row_index, row_t), (col_index, col_t) = rows, cols
    width = col_index.shape[1]
    across = np.empty((slab.shape[0], width), np.float32)
    across_whole = np.empty((slab.shape[0], width), np.bool_)
    for r in range(slab.shape[0]):
        for j in range(width):
            t0, t1 = slab[r, col_index[0, j]], slab[r, col_index[1, j]]
            t2, t3 = slab[r, col_index[2, j]], slab[r, col_index[3, j]]
            across[r, j] = _hermite(
                _value(t0, nodata),
                _value(t1, nodata),
                _value(t2, nodata),
                _value(t3, nodata),
                col_t[j],
            )
            across_whole[r, j] = t0 != nodata and t1 != nodata and t2 != nodata and t3 != nodata
    total = np.empty((row_index.shape[1], width), np.float32)
    whole_out = np.empty((row_index.shape[1], width), np.bool_)
    for i in range(row_index.shape[1]):
        r0, r1 = row_index[0, i] - low, row_index[1, i] - low
        r2, r3 = row_index[2, i] - low, row_index[3, i] - low
        t = row_t[i]
        for j in range(width):
            total[i, j] = _hermite(across[r0, j], across[r1, j], across[r2, j], across[r3, j], t)
            whole_out[i, j] = (
                across_whole[r0, j]
                and across_whole[r1, j]
                and across_whole[r2, j]
                and across_whole[r3, j]
            )
    return total, whole_out


@helper
def clip_unit(x: np.floating) -> np.floating:
    """``np.clip(x, 0.0, 1.0)`` in ``x``'s precision: NaN stays NaN."""
    if np.isnan(x):
        return x
    if x <= _ZERO:
        return _ZERO
    return min(_ONE, x)


@helper
def raise_nan(top: np.floating, rise: np.floating) -> np.floating:
    """``np.maximum(top, rise)``: NaN when either is. ``gpu.cu``'s ``raise_to`` is its twin."""
    larger = rise if not rise <= top else top
    return top if np.isnan(top) else larger


@helper
def _corner(position: np.float32, size: int) -> tuple[int, int, np.float32]:
    """``texels._corners`` at one position: the texels either side, clamped, and the weight."""
    at = position - _HALF
    first = np.float32(np.floor(at))
    low = int(first)
    return min(max(low, 0), size - 1), min(max(low + 1, 0), size - 1), at - first


@helper
def _tile_texel(texels: F32Grid, tile: I32Grid, u: np.float32, v: np.float32, out: F32Grid) -> None:
    """``texels.sample_atlas`` of one tile ``(x, y, w, h)`` at ``(u, v)``, clamped, into
    ``out``."""
    y0, y1, fy = _corner(v, int(tile[3]))
    x0, x1, fx = _corner(u, int(tile[2]))
    y0, y1, x0, x1 = y0 + tile[1], y1 + tile[1], x0 + tile[0], x1 + tile[0]
    gx, gy = _ONE - fx, _ONE - fy
    for k in range(out.shape[0]):
        upper = texels[y0, x0, k] * gx + texels[y0, x1, k] * fx
        lower = texels[y1, x0, k] * gx + texels[y1, x1, k] * fx
        out[k] = upper * gy + lower * fy


@kernel
def stamp(
    texels: F32Grid,
    tiles: I32Grid,
    placed: tuple[I32Grid, F64Grid, F32Grid, I64Grid],
    centres: tuple[F64Grid, F64Grid],
    seen_from: np.float32,
    planes: tuple[F32Grid, F32Grid, F32Grid, F32Grid],
) -> None:
    """``crown_stamp._stamp`` for each tree in turn, into ``planes`` (cover, rgb, normal, top).

    ``texels`` and ``tiles`` are a ``crown_atlas.CrownAtlas``'s; ``placed`` a
    ``crown_stamp.Placements``' tile, centre, pose and box; ``seen_from`` the cover from
    which a crown has a top.
    """
    x_cm, y_cm = centres
    cover, rgb, normal, top = planes
    tile, centre, pose, box = placed
    t = np.empty(texels.shape[2], np.float32)
    for k in range(tile.shape[0]):
        cos, sin, ox, oy, texel = pose[k, 0], pose[k, 1], pose[k, 2], pose[k, 3], pose[k, 4]
        rise, z, opacity = pose[k, 5], pose[k, 6], pose[k, 7]
        for r in range(box[k, 0], box[k, 1]):
            dy = np.float32(y_cm[r] - centre[k, 1])
            for c in range(box[k, 2], box[k, 3]):
                dx = np.float32(x_cm[c] - centre[k, 0])
                u = (cos * dx + sin * dy - ox) / texel
                v = (cos * dy - sin * dx - oy) / texel
                _tile_texel(texels, tiles[tile[k]], u, v, t)
                a = min(max(t[7] * opacity, _ZERO), _ONE)
                keep = _ONE - a
                cover[r, c] = a + cover[r, c] * keep
                for j in range(3):
                    rgb[r, c, j] = t[j] * opacity + rgb[r, c, j] * keep
                nx = cos * t[3] - sin * t[4]
                ny = sin * t[3] + cos * t[4]
                normal[r, c, 0] = nx * opacity + normal[r, c, 0] * keep
                normal[r, c, 1] = ny * opacity + normal[r, c, 1] * keep
                normal[r, c, 2] = t[5] * opacity + normal[r, c, 2] * keep
                if a >= seen_from:
                    top[r, c] = max(top[r, c], z + t[6] / t[7] * rise)

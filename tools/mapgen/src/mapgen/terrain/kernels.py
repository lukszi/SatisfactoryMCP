"""The sampler's gathers and the crown stamps for numba.

Separable interpolation and PCHIP onto the output grid reproduce their numpy reference in
``sample`` bit for bit: per output pixel the same operations in the same order and at the
same precision, taps in the order numpy adds them. The source slab is read as it is stored,
no float32 copy of it is made. The crown stamps reproduce ``crown_stamp._stamp`` tree by tree,
from the placements ``crown_stamp`` works out in numpy. Imported only when
``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid

__all__ = ["clip_unit", "pchip", "raise_nan", "separable", "stamp"]

_ZERO = np.float32(0.0)
_ONE = np.float32(1.0)
_TWO = np.float32(2.0)
_THREE = np.float32(3.0)

_Weights: TypeAlias = NDArray[np.floating]


@kernel
def separable(
    slab: NDArray[np.generic], nodata: int, holes: bool, weigh: bool, low: int,
    rows: tuple[I64Grid, _Weights], cols: tuple[I64Grid, _Weights],
) -> tuple[F32Grid, F32Grid]:  # fmt: skip
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
    slab: NDArray[np.generic], nodata: int, low: int,
    rows: tuple[I64Grid, F32Grid], cols: tuple[I64Grid, F32Grid],
) -> tuple[F32Grid, BoolMask]:  # fmt: skip
    """``sample.resample_pchip`` on the source rows ``slab`` from ``low``: ``(values, whole)``."""
    (row_index, row_t), (col_index, col_t) = rows, cols
    width = col_index.shape[1]
    across = np.empty((slab.shape[0], width), np.float32)
    across_whole = np.empty((slab.shape[0], width), np.bool_)
    for r in range(slab.shape[0]):
        for j in range(width):
            t0, t1 = slab[r, col_index[0, j]], slab[r, col_index[1, j]]
            t2, t3 = slab[r, col_index[2, j]], slab[r, col_index[3, j]]
            across[r, j] = _hermite(_value(t0, nodata), _value(t1, nodata), _value(t2, nodata),
                                    _value(t3, nodata), col_t[j])  # fmt: skip
            across_whole[r, j] = t0 != nodata and t1 != nodata and t2 != nodata and t3 != nodata
    total = np.empty((row_index.shape[1], width), np.float32)
    whole_out = np.empty((row_index.shape[1], width), np.bool_)
    for i in range(row_index.shape[1]):
        r0, r1 = row_index[0, i] - low, row_index[1, i] - low
        r2, r3 = row_index[2, i] - low, row_index[3, i] - low
        t = row_t[i]
        for j in range(width):
            total[i, j] = _hermite(across[r0, j], across[r1, j], across[r2, j], across[r3, j], t)
            whole_out[i, j] = (across_whole[r0, j] and across_whole[r1, j]
                               and across_whole[r2, j] and across_whole[r3, j])  # fmt: skip
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
def _mip_bilinear(texels: F64Grid, first: int, width: int, x: float, y: float,
                  out: F64Grid) -> None:  # fmt: skip
    """``crown_stamp._bilinear`` at one point of the mip whose texels start at ``first``:
    ``x, y`` are its texel coordinates plus a half, ``width`` its row with the rim."""
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = x - x0, y - y0
    gx, gy = 1 - fx, 1 - fy
    at = first + int(y0) * width + int(x0)
    for k in range(out.shape[0]):
        upper = texels[at, k] * gx + texels[at + 1, k] * fx
        lower = texels[at + width, k] * gx + texels[at + width + 1, k] * fx
        out[k] = upper * gy + lower * fy


@kernel
def stamp(
    texels: F64Grid, mips: I64Grid, spans: I64Grid, poses: F64Grid,
    centres: tuple[F64Grid, F64Grid], seen_from: float,
    planes: tuple[F32Grid, F32Grid, F32Grid, F32Grid],
) -> None:  # fmt: skip
    """``crown_stamp._stamp`` for each tree in turn, into ``planes`` (cover, rgb, dome, top).

    ``texels`` and ``mips`` are a ``crown_stamp.MipAtlas``'s. Per tree, ``spans`` is ``(mip,
    r0, r1, c0, c1)`` and ``poses`` ``(cx, cy, cos, sin, ox * scale, oy * scale, texel,
    scale_z, axis_z, z)``; ``seen_from`` is the cover from which a crown has a top.
    """
    x_cm, y_cm = centres
    cover, rgb, dome, top = planes
    got = np.empty(texels.shape[1], np.float64)
    for t in range(spans.shape[0]):
        mip, r0, r1, c0, c1 = spans[t, 0], spans[t, 1], spans[t, 2], spans[t, 3], spans[t, 4]
        first, height, width = mips[mip, 0], mips[mip, 1], mips[mip, 2]
        cx, cy, cos, sin = poses[t, 0], poses[t, 1], poses[t, 2], poses[t, 3]
        ox, oy, texel = poses[t, 4], poses[t, 5], poses[t, 6]
        scale_z, axis_z, z = poses[t, 7], poses[t, 8], poses[t, 9]
        for r in range(r0, r1):
            py = y_cm[r] - cy
            for c in range(c0, c1):
                px = x_cm[c] - cx
                u = (cos * px + sin * py - ox) / texel
                v = (cos * py - sin * px - oy) / texel
                if not (-0.5 < u < width - 2 + 0.5 and -0.5 < v < height - 2 + 0.5):
                    continue
                _mip_bilinear(texels, first, width, u + 0.5, v + 0.5, got)
                a = clip_unit(got[0])
                keep = 1.0 - a
                cover[r, c] = a + cover[r, c] * keep
                for k in range(3):
                    rgb[r, c, k] = got[1 + k] + rgb[r, c, k] * keep
                dome[r, c] = raise_nan(dome[r, c], got[4] * scale_z)
                rise = got[5] * scale_z * axis_z if a >= seen_from else -np.inf
                top[r, c] = raise_nan(top[r, c], z + rise)

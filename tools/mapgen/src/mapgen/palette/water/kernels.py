"""The water painters for numba: the terrain and satellite styles' water and the relief's.

Each one reproduces its numpy reference bit for bit: per pixel the float32 operations of
``shore.water_composite`` and ``relief._water_over`` in their order, the mix by the water's
cover included. The ``exp`` and the power they read are worked out by numpy beforehand and
handed in. Imported only when ``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np

from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

__all__ = ["relief_water", "water_composite"]

_ZERO = np.float32(0.0)
_HALF = np.float32(0.5)
_ONE = np.float32(1.0)

#: The ``(H, W)`` planes of a band a kernel reads.
Planes: TypeAlias = tuple[F32Grid, ...]


@helper
def _clip(x: np.float32) -> np.float32:
    """``np.clip(x, 0.0, 1.0)`` on a float32: NaN stays NaN."""
    if np.isnan(x):
        return x
    if x <= _ZERO:
        return _ZERO
    return min(_ONE, x)


@kernel
def relief_water(
    land: F32Grid, planes: Planes, index: I64Grid, terms: Planes, whole: bool, most: float,
    style: Planes,
) -> F32Grid:  # fmt: skip
    """``relief._water`` on the pixels ``index`` of a band laid flat: ``land`` (pixels by 3)
    with the water mixed in.

    ``planes`` is ``(cover, ocean, tint, lit)``, flat too, ``terms`` ``exp(-depth_m /
    clarity)`` and ``clip(4 c (1 - c)) ** 1.5`` at ``index``, ``style`` ``(shallow, deep,
    stroke, knobs)`` with ``knobs`` ``(sunlit, FLAT_LIT, edge_alpha, stroke weight)``.
    ``whole``: ``index`` is every pixel, mixed as ``shore.wet_mix`` does past ``most`` of
    them touched.
    """
    cover, ocean, tint, lit = planes
    transmit, curve = terms
    shallow, deep, stroke, knobs = style
    sunlit, flat_lit, edge_alpha, stroke_weight = knobs[0], knobs[1], knobs[2], knobs[3]
    out = land.copy()
    unders = np.empty((index.shape[0], 3), np.float32)
    weights = np.empty(index.shape[0], np.float32)
    colour = np.empty(3, np.float32)
    touched = 0
    for i in range(index.shape[0]):
        p = int(index[i])
        t = tint[p]
        for k in range(3):
            colour[k] = shallow[k] * (_ONE - t) + deep[k] * t
        colour[0] *= _ONE - sunlit + sunlit * lit[p] / flat_lit
        fade = _ONE - transmit[i]
        opacity = ocean[p] * (edge_alpha + (_ONE - edge_alpha) * fade) + (_ONE - ocean[p])
        edge = curve[i] * stroke_weight
        for k in range(3):
            shown = colour[k] * (_ONE - edge) + stroke[k] * edge
            unders[i, k] = land[p, k] * (_ONE - opacity) + shown * opacity
        weights[i] = _clip(_clip(cover[p]) + _HALF * edge)
        touched += weights[i] != _ZERO
    every = not whole or touched > most * index.shape[0]
    for i in range(index.shape[0]):
        if every or weights[i] != _ZERO:
            p, keep = index[i], _ONE - weights[i]
            for k in range(3):
                out[p, k] = land[p, k] * keep + unders[i, k] * weights[i]
    return out


@kernel
def water_composite(
    land: F32Grid, planes: Planes, transmit: F32Grid, most: float, style: Planes,
) -> F32Grid:  # fmt: skip
    """``shore.water_composite`` over a band: sRGB 0..255.

    ``planes`` is ``(cover, depth, banks, shade, above_m, edge, depth_m, below_m, ocean)``,
    ``transmit`` ``exp(-optical depth / clarity)``. ``style`` is ``(shallow, deep, band tint,
    knobs)``, ``knobs`` ``(band m, edge_alpha, wet_darken, shade floor, shade range, stroke,
    foam strength, foam max depth, foam width, foam white)``; a band m, stroke or foam
    strength of 0 is none.
    """
    cover, depth, banks, shade, above_m, edge, depth_m, below_m, ocean = planes
    shallow, deep, band_tint, knobs = style
    band_m, edge_alpha, wet_darken, floor, spread = knobs[0], knobs[1], knobs[2], knobs[3], knobs[4]
    stroke, foam, foam_depth, foam_width, white = knobs[5], knobs[6], knobs[7], knobs[8], knobs[9]
    rows, cols = cover.shape
    touched = 0
    for r in range(rows):
        for c in range(cols):
            touched += cover[r, c] != _ZERO
    every = touched > most * cover.size
    out = np.empty(land.shape, np.float32)
    banded = np.empty(3, np.float32)
    under = np.empty(3, np.float32)
    for r in range(rows):
        for c in range(cols):
            bank = banks[r, c]
            for k in range(3):
                banded[k] = land[r, c, k]
            if band_m != _ZERO:
                reach = _clip(_ONE - above_m[r, c] / band_m)
                weight = reach * reach * bank
                for k in range(3):
                    banded[k] = banded[k] * (_ONE - weight + weight * band_tint[k])
            opacity = bank * (edge_alpha + (_ONE - edge_alpha) * (_ONE - transmit[r, c])) + (
                _ONE - bank
            )
            wet = _ONE - (_ONE - wet_darken) * bank
            t, light = depth[r, c], floor + spread * shade[r, c]
            for k in range(3):
                colour = (shallow[k] * (_ONE - t) + deep[k] * t) * light
                under[k] = banded[k] * wet * (_ONE - opacity) + colour * opacity
            mixed = every or cover[r, c] != _ZERO
            keep, foam_weight = _ONE - cover[r, c], _ZERO
            if foam != _ZERO:
                shallow_m = _clip(_ONE - depth_m[r, c] / foam_depth)
                shallow_m = shallow_m * _clip(_ONE - below_m[r, c] / foam_width)
                foam_weight = foam * shallow_m * cover[r, c] * ocean[r, c]
            for k in range(3):
                value = banded[k] * keep + under[k] * cover[r, c] if mixed else banded[k]
                if stroke != _ZERO:
                    value = value * (_ONE - stroke * edge[r, c])
                if foam != _ZERO:
                    value = value * (_ONE - foam_weight) + white * foam_weight
                out[r, c, k] = value
    return out

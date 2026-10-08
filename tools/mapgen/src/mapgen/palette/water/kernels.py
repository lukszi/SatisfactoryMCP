"""The water painters for numba: the terrain style's water and the relief's.

Each one reproduces its numpy reference bit for bit: per pixel the float32 operations of
``shore.water_composite`` and ``relief._water_over`` in their order, the mix by the water's
cover included. The ``exp`` and the power they read are worked out by numpy beforehand and
handed in. Imported only when ``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.jit import kernel
from mapgen.terrain.kernels import clip_unit
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

__all__ = [
    "CompositeKnobs",
    "CompositePlanes",
    "CompositeStyle",
    "ReliefKnobs",
    "ReliefPlanes",
    "ReliefStyle",
    "ReliefTerms",
    "relief_water",
    "water_composite",
]

_ZERO = np.float32(0.0)
_HALF = np.float32(0.5)
_ONE = np.float32(1.0)


class ReliefPlanes(NamedTuple):
    """The band's planes ``relief_water`` reads, laid flat."""

    cover: F32Grid
    ocean: F32Grid
    tint: F32Grid
    lit: F32Grid


class ReliefTerms(NamedTuple):
    """Worked out by numpy at the pixels the kernel works on: ``exp(-depth_m / clarity)``
    and the stroke's ``clip(4 c (1 - c)) ** 1.5``."""

    transmit: F32Grid
    curve: F32Grid


class ReliefKnobs(NamedTuple):
    """The relief water's numbers, float32."""

    sunlit: np.float32
    flat_lit: np.float32
    edge_alpha: np.float32
    stroke_weight: np.float32


class ReliefStyle(NamedTuple):
    """The relief water's colours and numbers."""

    shallow: F32Grid
    deep: F32Grid
    stroke: F32Grid
    knobs: ReliefKnobs


class CompositePlanes(NamedTuple):
    """The ``(H, W)`` planes of a band ``water_composite`` reads."""

    cover: F32Grid
    depth: F32Grid
    banks: F32Grid
    shade: F32Grid
    above_m: F32Grid
    edge: F32Grid
    depth_m: F32Grid
    below_m: F32Grid
    ocean: F32Grid


class CompositeKnobs(NamedTuple):
    """The shore's numbers, float32. A band, stroke or foam strength of 0 is none."""

    band_m: np.float32
    edge_alpha: np.float32
    wet_darken: np.float32
    shade_floor: np.float32
    shade_range: np.float32
    stroke: np.float32
    foam: np.float32
    foam_depth_m: np.float32
    foam_width_m: np.float32
    foam_white: np.float32


class CompositeStyle(NamedTuple):
    """The shore's colours and numbers."""

    shallow: F32Grid
    deep: F32Grid
    band_tint: F32Grid
    knobs: CompositeKnobs


@kernel
def relief_water(
    land: F32Grid,
    planes: ReliefPlanes,
    index: I64Grid,
    terms: ReliefTerms,
    whole: bool,
    most: float,
    style: ReliefStyle,
) -> F32Grid:
    """``relief._water`` on the pixels ``index`` of a band laid flat: ``land`` (pixels by 3)
    with the water mixed in. ``terms`` are at ``index``. ``whole``: ``index`` is every pixel,
    mixed as ``shore.wet_mix`` does past ``most`` of them touched.
    """
    cover, ocean, tint, lit = planes
    transmit, curve = terms
    shallow, deep, stroke, knobs = style
    sunlit, flat_lit, edge_alpha, stroke_weight = knobs
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
        weights[i] = clip_unit(clip_unit(cover[p]) + _HALF * edge)
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
    land: F32Grid, planes: CompositePlanes, transmit: F32Grid, most: float, style: CompositeStyle
) -> F32Grid:
    """``shore.water_composite`` over a band: sRGB 0..255. ``transmit`` is
    ``exp(-optical depth / clarity)``.
    """
    cover, depth, banks, shade, above_m, edge, depth_m, below_m, ocean = planes
    shallow, deep, band_tint, knobs = style
    band_m, edge_alpha, wet_darken = knobs.band_m, knobs.edge_alpha, knobs.wet_darken
    floor, spread, stroke = knobs.shade_floor, knobs.shade_range, knobs.stroke
    foam, foam_depth, foam_width = knobs.foam, knobs.foam_depth_m, knobs.foam_width_m
    white = knobs.foam_white
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
                reach = clip_unit(_ONE - above_m[r, c] / band_m)
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
                shallow_m = clip_unit(_ONE - depth_m[r, c] / foam_depth)
                shallow_m = shallow_m * clip_unit(_ONE - below_m[r, c] / foam_width)
                foam_weight = foam * shallow_m * cover[r, c] * ocean[r, c]
            for k in range(3):
                value = banded[k] * keep + under[k] * cover[r, c] if mixed else banded[k]
                if stroke != _ZERO:
                    value = value * (_ONE - stroke * edge[r, c])
                if foam != _ZERO:
                    value = value * (_ONE - foam_weight) + white * foam_weight
                out[r, c, k] = value
    return out

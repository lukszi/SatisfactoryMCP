"""The painted style's water for numba: the colour under the surface, mixed in by the cover.

It reproduces ``optics.mix_underwater`` bit for bit: per pixel the float32 operations of
``underwater`` and ``carpet_bed`` in their order, then the mix. Every ``exp`` they read is
worked out by numpy beforehand and handed in. Imported only when
``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import F32Grid, FloatGrid, I64Grid

__all__ = [
    "BandColours",
    "CarpetTerms",
    "OpaqueTerms",
    "SunkTerms",
    "UnderwaterExtras",
    "UnderwaterFlags",
    "UnderwaterKnobs",
    "WetTerms",
    "underwater",
]

_ZERO = np.float32(0.0)
_ONE = np.float32(1.0)


class BandColours(NamedTuple):
    """What the kernel reads of the band in place, a row per pixel: ``g``, the cover, the
    optics' tint, body and deep colour, and the crowns' colour (no rows without crowns)."""

    g: F32Grid
    cover: F32Grid
    tint: F32Grid
    body: F32Grid
    deep: F32Grid
    crown_rgb: F32Grid


class WetTerms(NamedTuple):
    """A row per pixel of the kernel's index: ``exp(-k depth)``, ``1 - floor`` and
    ``exp(-depth / deep tau)``."""

    bed_e: F32Grid
    keep: F32Grid
    open_e: F32Grid


class CarpetTerms(NamedTuple):
    """The seabed carpet, rows as ``WetTerms``': ``exp(-k above)``, its cover, its colour
    and the ocean's body and sky."""

    transmit: F32Grid
    cover: F32Grid
    colour: F32Grid
    body: F32Grid
    sky: F32Grid


class SunkTerms(NamedTuple):
    """The sunk crowns, rows as ``WetTerms``': ``exp(-k above)`` and ``alpha * sunk``."""

    transmit: F32Grid
    share: FloatGrid


class OpaqueTerms(NamedTuple):
    """The opaque water: ``exp(-depth / opaque tau)`` per row, and per class its sampled
    weight times its share, and its colour."""

    murk_e: F32Grid
    shares: F32Grid
    colours: F32Grid


class UnderwaterExtras(NamedTuple):
    """The terms the kernel reads where ``UnderwaterFlags`` say there are some."""

    carpet: CarpetTerms
    sunk: SunkTerms
    opaque: OpaqueTerms


class UnderwaterKnobs(NamedTuple):
    """The exposure, the bed's wetness and the sky's colour, float32."""

    exposure: np.float32
    bed: np.float32
    sky: F32Grid


class UnderwaterFlags(NamedTuple):
    """Which extras there are, and whether the index is every pixel of the band."""

    carpet: bool
    crowns: bool
    opaque: bool
    whole: bool


@helper
def _over(under: F32Grid, seen: F32Grid, share: np.float32) -> None:
    """``under * (1 - share) + seen * share``, in place."""
    for k in range(3):
        under[k] = under[k] * (_ONE - share) + seen[k] * share


@helper
def _through(
    colour: F32Grid,
    bed: np.float32,
    optics: tuple[F32Grid, F32Grid, F32Grid],
    e: F32Grid,
    keep: np.float32,
    out: F32Grid,
) -> None:
    """``underwater.through`` at one pixel: ``colour`` seen through the water whose
    ``exp(-k metres)`` is ``e``; ``optics`` is this pixel's ``(bed tint, body, sky)`` and
    ``keep`` its ``1 - floor``."""
    tint, body, sky = optics
    for k in range(3):
        transmit = e[k] * keep
        out[k] = colour[k] * bed * tint[k] * transmit + body[k] * (_ONE - transmit) + sky[k]


@kernel
def underwater(
    lit: F32Grid,
    index: I64Grid,
    band: BandColours,
    wet: WetTerms,
    extras: UnderwaterExtras,
    knobs: UnderwaterKnobs,
    flags: UnderwaterFlags,
    most: float,
) -> F32Grid:
    """``optics.mix_underwater``: ``lit`` (a band's pixels by 3) with the colour under the
    water mixed in at ``index``. ``band`` is read at ``index``, the rest a row per pixel of
    it. ``whole``: ``index`` is every pixel, mixed as ``shore.wet_mix`` does past ``most`` of
    them wet.
    """
    g, cover, tint, body, deep, crown_rgb = band
    bed_e, keep, open_e = wet
    carpet_e, carpet_cover, carpet_rgb, carpet_body, carpet_sky = extras.carpet
    crown_e, sunk = extras.sunk
    murk_e, opaque_share, opaque_rgb = extras.opaque
    has_carpet, has_crowns, has_opaque, whole = flags
    exposure, bed, sky = knobs
    touched = 0
    for i in range(index.shape[0]):
        touched += cover[index[i]] != _ZERO
    every = not whole or touched > most * index.shape[0]
    out = lit.copy()
    lit_g = np.empty(3, np.float32)
    under = np.empty(3, np.float32)
    seen = np.empty(3, np.float32)
    for i in range(index.shape[0]):
        p = index[i]
        if not every and cover[p] == _ZERO:
            continue
        here = (tint[p], body[p], sky)
        for k in range(3):
            lit_g[k] = g[p, k] * exposure
        _through(lit_g, bed, here, bed_e[i], keep[i], under)
        if has_carpet:
            for k in range(3):
                t = carpet_e[i, k]
                seen[k] = carpet_rgb[k] * t + carpet_body[k] * (_ONE - t) + carpet_sky[k]
            _over(under, seen, carpet_cover[i])
        if has_crowns:
            _through(crown_rgb[p], bed, here, crown_e[i], keep[i], seen)
            _over(under, seen, sunk[i])
        _over(under, deep[p], _ONE - open_e[i])
        if has_opaque:
            murk = _ONE - murk_e[i]
            for j in range(opaque_share.shape[0]):
                _over(under, opaque_rgb[j], opaque_share[j, i] * murk)
        for k in range(3):
            out[p, k] = lit[p, k] * (_ONE - cover[p]) + under[k] * cover[p]
    return out

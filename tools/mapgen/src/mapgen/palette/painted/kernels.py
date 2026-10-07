"""The painted style's water for numba: the colour under the surface, mixed in by the cover.

It reproduces ``optics.mix_underwater`` bit for bit: per pixel the float32 operations of
``underwater`` and ``carpet_bed`` in their order, then the mix. Every ``exp`` they read is
worked out by numpy beforehand and handed in. Imported only when
``mapgen.jit.kernels_on()``. docs/map/renders.md section 41.
"""

from __future__ import annotations

from typing import TypeAlias

import numpy as np

from mapgen.jit import helper, kernel
from satisfactory_mcp.core.arrays import F32Grid, I64Grid

__all__ = ["underwater"]

_ZERO = np.float32(0.0)
_ONE = np.float32(1.0)

#: Planes of a band, one row per pixel (or per opaque class), and colours.
Planes: TypeAlias = tuple[F32Grid, ...]


@helper
def _over(under: F32Grid, seen: F32Grid, share: np.float32) -> None:
    """``under * (1 - share) + seen * share``, in place."""
    for k in range(3):
        under[k] = under[k] * (_ONE - share) + seen[k] * share


@helper
def _through(
    colour: F32Grid, bed: np.float32, optics: Planes, e: F32Grid, keep: np.float32, out: F32Grid
) -> None:  # fmt: skip
    """``underwater.through`` at one pixel: ``colour`` seen through the water whose
    ``exp(-k metres)`` is ``e``; ``optics`` is this pixel's ``(bed tint, body, sky)`` and
    ``keep`` its ``1 - floor``."""
    tint, body, sky = optics
    for k in range(3):
        transmit = e[k] * keep
        out[k] = colour[k] * bed * tint[k] * transmit + body[k] * (_ONE - transmit) + sky[k]


@kernel
def underwater(
    lit: F32Grid, index: I64Grid, band: Planes, wet: Planes,
    extras: tuple[Planes, Planes, Planes], knobs: F32Grid,
    flags: tuple[bool, bool, bool, bool], most: float,
) -> F32Grid:  # fmt: skip
    """``optics.mix_underwater``: ``lit`` (a band's pixels by 3) with the colour under the
    water mixed in at ``index``.

    ``band``, read at ``index``: ``(g, cover, tint, body, deep, crown colour)``. ``wet``, one
    row per pixel of ``index``: ``(exp(-k depth), 1 - floor, exp(-depth / deep tau))``.
    ``extras``, read when their flags of ``flags`` (carpet, crowns, opaque, whole) are set:
    the carpet ``(exp(-k above), cover, colour, body, sky)``, the sunk crowns ``(exp(-k
    above), alpha * sunk)`` and the opaque water ``(exp(-depth / opaque tau), sampled weight
    * share per class, colour per class)``, rows as ``wet``'s. ``knobs`` is ``(exposure,
    bed, sky)``. ``whole``: ``index`` is every pixel, mixed as ``shore.wet_mix`` does past
    ``most`` of them wet.
    """
    g, cover, tint, body, deep, crown_rgb = band
    bed_e, keep, open_e = wet
    carpet_e, carpet_cover, carpet_rgb, carpet_body, carpet_sky = extras[0]
    crown_e, sunk = extras[1]
    murk_e, opaque_share, opaque_rgb = extras[2]
    has_carpet, has_crowns, has_opaque, whole = flags
    exposure, bed, sky = knobs[0], knobs[1], knobs[2:5]
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

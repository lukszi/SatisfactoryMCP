"""The canopy's own light: the crown occluder's trees lit by their own top, under their own
horizon and sky, where the style that draws them lays them over the ground.

Baked at the default sun into the painted layer's terms. docs/map/light-and-crowns.md
section 29, "The canopy's own light".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.lighting.horizon import normals
from mapgen.lighting.light_tiles import normal_byte
from mapgen.terrain.crown_stamp import DOME_SIGMA_M
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = [
    "CANOPY_RELIEF",
    "CANOPY_SMOOTH_M",
    "CanopyLight",
    "blend_canopy",
    "canopy_normal_bytes",
    "smoothing_margin",
]

#: The canopy top is smoothed over this before its slope is taken, as the painted crowns'
#: domes are, so the 1 m grid of the crown plane draws no facets.
CANOPY_SMOOTH_M = DOME_SIGMA_M

#: The share of the canopy top's relief its slope keeps: the painted crowns' ``dome_gain``.
CANOPY_RELIEF = 0.35


class CanopyLight(NamedTuple):
    """A block's canopy at the default sun: its share of each pixel, direct term and sky view."""

    share: F32Grid
    direct: F32Grid
    sky: F32Grid


def smoothing_margin(spacing_m: float) -> int:
    """The pixels past a window's one-pixel ring that ``canopy_normal_bytes`` reads."""
    return int(np.ceil(4 * CANOPY_SMOOTH_M / spacing_m)) + 1


def _smoothed(top: F32Grid, spacing_m: float) -> F32Grid:
    """``top`` blurred over its own pixels only, NaN where it is NaN."""
    have = np.isfinite(top)
    sigma = CANOPY_SMOOTH_M / spacing_m
    weight = ndimage.gaussian_filter(have.astype(np.float32), sigma, mode="nearest")
    filled = np.where(have, top, np.float32(0.0)).astype(np.float32)
    total = ndimage.gaussian_filter(filled, sigma, mode="nearest")
    return np.where(have, total / np.maximum(weight, np.float32(1e-6)), np.nan).astype(np.float32)


def canopy_normal_bytes(top: NDArray[np.floating], margin: int, spacing_m: float) -> U8Grid:
    """The canopy top's east and south normal as bytes, ``(h, w, 2)``, for a window of ``top``
    with ``margin`` pixels and a ring of one more on each side, its relief scaled by
    ``CANOPY_RELIEF``; flat where no canopy is."""
    smooth = _smoothed(np.asarray(top, np.float32), spacing_m)
    inner = smooth[margin : smooth.shape[0] - margin, margin : smooth.shape[1] - margin]
    east, south = normals(inner * np.float32(CANOPY_RELIEF), spacing_m)
    return np.stack([normal_byte(east), normal_byte(south)], -1)


def blend_canopy(direct: F32Grid, sky: F32Grid, canopy: CanopyLight) -> tuple[F32Grid, F32Grid]:
    """The ground's direct term and sky view with the canopy's laid over them by its share."""
    share = canopy.share
    lit = direct + share * (canopy.direct - direct)
    open_sky = sky + share * (canopy.sky - sky)
    return lit.astype(np.float32), open_sky.astype(np.float32)

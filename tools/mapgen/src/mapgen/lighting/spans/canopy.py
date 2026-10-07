"""The canopy's own light: the crown occluder's trees lit by their own top, under their own
horizon and sky, where the style that draws them lays them over the ground.

Baked at the default sun into the painted layer's terms. docs/map/light-and-crowns.md
section 29, "The canopy's own light".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.lighting.horizon import normals
from mapgen.lighting.light_tiles import (
    normal_byte,
    optional_array,
    padded_window,
    ring_rows,
    upsampled,
    work_array,
)
from mapgen.lighting.model import shaded_direct, sun_horizon
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.terrain.crown_stamp import DOME_SIGMA_M
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = [
    "CANOPY_RELIEF",
    "CANOPY_SMOOTH_M",
    "Canopy",
    "CanopyLight",
    "blend_canopy",
    "block_canopy",
    "canopy_normal_bytes",
    "canopy_rows",
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


class Canopy(NamedTuple):
    """What the canopy's own light reads: its top and cover planes, the surface, and its
    horizon toward the default sun and its sky view at half resolution, with a ring."""

    top: NDArray[np.float32]
    cover: NDArray[np.uint8] | None
    surface: NDArray[np.float32]
    horizon: F32Grid
    sky: F32Grid


def block_canopy(
    work: Path, block: tuple[int, int, int], cells: list[F32Grid], sky: F32Grid
) -> Canopy | None:
    """The canopy of ``block`` (first row, first column, side), None without an occluder or
    where none stands in it; ``cells`` are its horizons by direction, with a ring."""
    r0, c0, n = block
    top = optional_array(work, "occluder", np.float32)
    if top is None:
        return None
    cover = optional_array(work, "occluder_cover", np.uint8)
    core = (slice(r0, r0 + n), slice(c0, c0 + n))
    there = np.isfinite(top[core]) if cover is None else np.asarray(cover[core]) > 0
    if not there.any():
        return None
    surface = work_array(work, "z", np.float32, "r")
    return Canopy(top, cover, surface, sun_horizon(cells, DEFAULT_SUN[0]), sky)


def canopy_rows(
    canopy: Canopy, block: tuple[int, int, int], spacing_m: float, rows: slice
) -> CanopyLight:
    """The canopy's light on ``rows`` of the block: its share of each pixel where it stands
    above the surface, its direct term under its own smoothed top and horizons, and its sky
    view."""
    r0, c0, n = block
    a, b = r0 + rows.start, r0 + rows.stop
    top = np.asarray(canopy.top[a:b, c0 : c0 + n])
    if canopy.cover is None:
        share = np.isfinite(top).astype(np.float32)
    else:
        share = np.asarray(canopy.cover[a:b, c0 : c0 + n], np.float32) / np.float32(255.0)
    share *= top > np.asarray(canopy.surface[a:b, c0 : c0 + n])
    margin = smoothing_margin(spacing_m)
    reach = margin + 1
    top = padded_window(canopy.top, a - reach, b + reach, c0 - reach, c0 + n + reach, np.nan)
    slope = canopy_normal_bytes(top, margin, spacing_m)
    direct = shaded_direct(slope, upsampled(ring_rows(canopy.horizon, rows)), DEFAULT_SUN)
    sky = np.clip(upsampled(ring_rows(canopy.sky, rows)), 0, 1)
    return CanopyLight(share, direct, sky)

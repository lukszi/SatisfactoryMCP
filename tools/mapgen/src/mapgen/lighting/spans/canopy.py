"""The canopy's own light: the trees lit flat under their own horizon and sky, where the style
that draws them lays them over the ground.

Baked at the default sun into the painted layer's terms, as the page lights the trees apart from
the ground (section 29, "A porous canopy"): flat, for the sprites' relief is in their colour.
docs/map/light-and-crowns.md section 29, "The canopy's own light".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from mapgen.lighting.light_tiles import optional_array, ring_rows, upsampled, work_array
from mapgen.lighting.model import shaded_direct
from mapgen.lighting.sun import DEFAULT_SUN
from satisfactory_mcp.core.arrays import F32Grid

__all__ = ["Canopy", "CanopyLight", "blend_canopy", "block_canopy", "canopy_rows"]


class CanopyLight(NamedTuple):
    """A block's canopy at the default sun: its share of each pixel, direct term and sky view."""

    share: F32Grid
    direct: F32Grid
    sky: F32Grid


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
    work: Path, block: tuple[int, int, int], horizon: F32Grid, sky: F32Grid
) -> Canopy | None:
    """The canopy of ``block`` (first row, first column, side), None without an occluder or
    where none stands in it; ``horizon`` is its horizon toward the default sun, with a ring."""
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
    return Canopy(top, cover, surface, horizon, sky)


def canopy_rows(canopy: Canopy, block: tuple[int, int, int], rows: slice) -> CanopyLight:
    """The canopy's light on ``rows`` of the block: its share of each pixel where it stands
    above the surface, its direct term, flat under its horizon, and its sky view."""
    r0, c0, n = block
    a, b = r0 + rows.start, r0 + rows.stop
    top = np.asarray(canopy.top[a:b, c0 : c0 + n])
    if canopy.cover is None:
        share = np.isfinite(top).astype(np.float32)
    else:
        share = np.asarray(canopy.cover[a:b, c0 : c0 + n], np.float32) / np.float32(255.0)
    share *= top > np.asarray(canopy.surface[a:b, c0 : c0 + n])
    horizon = upsampled(ring_rows(canopy.horizon, rows))
    direct = shaded_direct((horizon.shape[0], horizon.shape[1]), horizon, DEFAULT_SUN)
    sky = np.clip(upsampled(ring_rows(canopy.sky, rows)), 0, 1)
    return CanopyLight(share, direct, sky)

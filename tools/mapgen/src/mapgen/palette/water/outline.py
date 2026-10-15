"""The open sea's shore past the landscape, drawn along its smoothed outline.

There the shore's ground and water are decided texel by texel; the heights beside it are
ramped through the sea's level so the drawn waterline is anti-aliased. docs/map/renders.md,
"The fill's shore".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.palette.scene import FloatGrid
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I16Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["SHORE_CLEAR_M", "SHORE_RISE_M", "SHORE_STEEP_M", "SHORE_UNDER_M", "shore_outline"]

#: Past the landscape the shore's height runs through the sea's level along its smoothed
#: outline, this many metres from all-sea to all-ground coverage (about half a metre a metre
#: across it), so the drawn waterline is anti-aliased.
SHORE_RISE_M = 2.5

#: The shore's ramp keeps a texel at least this far on its own side of the sea's level.
SHORE_CLEAR_M = 0.03

#: Dry ground at most this far under the sea's level beside it is the shore's ground.
SHORE_UNDER_M = 0.2

#: Ground this far over the sea, or a bed this far under it, is a cliff or deep water and is
#: left as drawn; from half of it the ramp fades out.
SHORE_STEEP_M = 6.0

#: ``shore_outline`` works in bands of this many rows.
_SHORE_BAND_ROWS = 512


def shore_outline(
    planes: tuple[FloatGrid, FloatGrid | None],
    sides: tuple[BoolMask, BoolMask],
    level_dm: I16Grid,
    sigma: float,
) -> int:
    """The open sea's shore past the landscape anti-aliased in ``planes``, ``(heights_dm,
    ground_dm)`` changed in place; the texels changed.

    ``sides`` is ``(ground, sea)``: dry fill and cliff ground, and the sea; ``sigma`` the
    outline's smoothing in texels. Within the smoothed outline's reach the height is drawn
    towards a ramp through the sea's level, ``SHORE_RISE_M`` from all-sea to all-ground
    coverage, tapering to the height as drawn at the reach's end; a cliff or deep water
    (``SHORE_STEEP_M``) is left as drawn. Ground ends over the sea's level and sea under it,
    so no texel changes side. In bands of rows.
    """
    ground, sea = sides
    reach = int(np.ceil(2.5 * sigma))
    near = ndimage.binary_dilation(ground, iterations=reach)
    near &= ndimage.binary_dilation(sea, iterations=reach)
    near &= ground | sea
    rows = np.flatnonzero(near.any(axis=1))
    if not len(rows):
        return 0
    pad, changed = reach + int(np.ceil(8.0 * sigma)), 0
    for start in range(int(rows[0]), int(rows[-1]) + 1, _SHORE_BAND_ROWS):
        stop = min(start + _SHORE_BAND_ROWS, int(rows[-1]) + 1)
        if not near[start:stop].any():
            continue
        box = slice(max(start - pad, 0), stop + pad)
        core = slice(start - box.start, stop - box.start)
        heights, ground_dm = planes
        band = (heights[box], None if ground_dm is None else ground_dm[box])
        masks = (ground[box], sea[box] & (level_dm[box] != hf.NODATA), near[box])
        drawn = _shore_band(band, masks, level_dm[box], sigma)
        changed += int(np.count_nonzero(drawn[0][core] != planes[0][start:stop]))
        for plane, moved in zip(planes, drawn):
            if plane is not None and moved is not None:
                plane[start:stop] = moved[core]
    return changed


def _shore_band(
    planes: tuple[FloatGrid, FloatGrid | None],
    masks: tuple[BoolMask, BoolMask, BoolMask],
    level_dm: I16Grid,
    sigma: float,
) -> tuple[FloatGrid, FloatGrid | None]:
    """``shore_outline`` over one band of rows: each plane as it is drawn after it."""
    ground, water, near = masks
    level = np.where(water, level_dm, 0).astype(np.float32)
    wet = ndimage.gaussian_filter(water.astype(np.float32), sigma)
    surface = ndimage.gaussian_filter(level, sigma) / np.maximum(wet, np.float32(1e-6))
    # Dry ground further under the sea's level is drawn as sea, and stays so.
    land = ground & (planes[0] >= surface - _dm(SHORE_UNDER_M))
    share = ndimage.gaussian_filter(land.astype(np.float32), sigma)
    share /= np.maximum(share + wet, np.float32(1e-6))
    ramp = surface + _dm(SHORE_RISE_M) * (share - np.float32(0.5))
    taper = _smoothstep((0.42 - np.abs(share - 0.5)) / 0.22)
    weight = np.where(near & (land | water), taper, np.float32(0.0))
    ashore = np.maximum(ramp, surface + _dm(SHORE_CLEAR_M))
    afloat = np.minimum(ramp, level - _dm(SHORE_CLEAR_M))
    steep = _dm(SHORE_STEEP_M)

    def moved(drawn: FloatGrid) -> FloatGrid:
        # Ground cut down, or a bed raised, only where it is not a cliff or deep water.
        up = np.where(drawn < ashore, 1.0, _smoothstep((surface + steep - drawn) / (0.5 * steep)))
        down = np.where(drawn > afloat, 1.0, _smoothstep((drawn - level + steep) / (0.5 * steep)))
        target = np.where(land, drawn + up * (ashore - drawn), drawn)
        target = np.where(water, drawn + down * (np.maximum(drawn, afloat) - drawn), target)
        return (drawn + weight * (target - drawn)).astype(drawn.dtype)

    heights, ground_dm = planes
    return moved(heights), None if ground_dm is None else moved(ground_dm)


def _dm(metres: float) -> np.float32:
    """Metres as float32 decimetres."""
    return np.float32(metres * hf.DM_PER_M)


def _smoothstep(t: FloatGrid) -> F32Grid:
    """``t`` clipped to [0, 1] and eased at both ends, float32."""
    s = np.clip(t, 0.0, 1.0).astype(np.float32)
    return (s * s * (np.float32(3.0) - np.float32(2.0) * s)).astype(np.float32)

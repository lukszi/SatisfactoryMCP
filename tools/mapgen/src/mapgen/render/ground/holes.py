"""Where the ground lattice drops into the field's fold, the drop follows a smoothed outline.

A fold texel standing well under the lattice beside it is a hole's rim: the two heights met
on the 1 m grid's texel boundary, so the drop drew a staircase. Near such a rim a piece reads
both sides on their own and switches where the fold's smoothed mask reads one half, as the
water's edge does (docs/map/renders.md section 25, "Hole rims").
"""

from __future__ import annotations

from typing import NamedTuple, TypeAlias

import numpy as np
from scipy import ndimage

from mapgen.terrain.sample import AxisTaps, PchipTaps, sample_plain, sample_surface
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I16Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["HOLE_RAMP", "HOLE_SIGMA_M", "HOLE_STEP_M", "HOLE_ZONE", "HoleRim", "hole_rim"]

#: A fold texel this many metres under a lattice texel beside it is a hole's rim.
HOLE_STEP_M = 5.0
#: The fold's mask is smoothed by a Gaussian of this sigma, in metres.
HOLE_SIGMA_M = 1.5
#: Texels from a rim within which the smoothed outline decides.
HOLE_ZONE = 3
#: Half the smoothed mask's span over which the two sides are blended, about 0.75 m.
HOLE_RAMP = 0.1

_Axis: TypeAlias = AxisTaps | PchipTaps
#: Texels a crop reaches past its taps: the Gaussian's radius, the zone, the rim and a stencil.
_MARGIN = 16


class HoleRim(NamedTuple):
    """A piece's pixels the outline decides, their ground, and which lie on the fold's side."""

    inside: BoolMask
    z_dm: F32Grid
    fold: BoolMask


def hole_rim(
    heights: I16Grid | F32Grid,
    ground: F32Grid,
    smooth: tuple[_Axis, _Axis],
    linear: tuple[AxisTaps, AxisTaps],
    spacing_m: float,
) -> HoleRim | None:
    """The ground near a hole's rim on a piece read by ``smooth`` and ``linear``, in dm; None
    where the piece reads none. ``heights`` is the fold, ``ground`` the lattice under the rocks.
    """
    rows, cols = _bounds(linear[0], heights.shape[0]), _bounds(linear[1], heights.shape[1])
    fold_dm = np.asarray(heights[rows, cols], np.float32)
    lattice = np.asarray(ground[rows, cols], np.float32)
    have = lattice != hf.NODATA
    fold = ~have & (fold_dm != hf.NODATA)
    if not fold.any() or not have.any():
        return None
    beside = ndimage.maximum_filter(np.where(have, lattice, -np.inf), 3)
    rim = fold & (beside - fold_dm > np.float32(HOLE_STEP_M * hf.DM_PER_M))
    if not rim.any():
        return None
    near = ndimage.binary_dilation(rim, iterations=HOLE_ZONE).astype(np.float32)
    lin = _shifted(linear, rows.start, cols.start)
    inside = sample_plain(near, lin) >= np.float32(1.0 - 1e-6)
    if not inside.any():
        return None
    share = ndimage.gaussian_filter(fold.astype(np.float32), HOLE_SIGMA_M / spacing_m)
    side = np.clip((sample_plain(share, lin) - 0.5) / (2 * HOLE_RAMP) + 0.5, 0.0, 1.0)
    smo = _shifted(smooth, rows.start, cols.start)
    nodata = hf.NODATA
    low_dm, low_missing = sample_surface(np.where(fold, fold_dm, nodata), smo, lin, nodata)
    top_dm, top_missing = sample_surface(np.where(have, lattice, nodata), smo, lin, nodata)
    side = np.where(low_missing, 0.0, np.where(top_missing, 1.0, side)).astype(np.float32)
    low_dm = np.where(low_missing, top_dm, low_dm)
    top_dm = np.where(top_missing, low_dm, top_dm)
    z_dm = (top_dm + side * (low_dm - top_dm)).astype(np.float32)
    inside &= ~(low_missing & top_missing)
    return HoleRim(inside, z_dm, side >= 0.5)


def _bounds(taps: _Axis, limit: int) -> slice:
    """The texels a piece's taps read along one axis, ``_MARGIN`` more either side."""
    index = taps.indices if isinstance(taps, PchipTaps) else taps[0]
    return slice(max(int(index.min()) - _MARGIN, 0), min(int(index.max()) + 1 + _MARGIN, limit))


def _shifted(taps: tuple[_Axis, _Axis], row0: int, col0: int) -> tuple[_Axis, _Axis]:
    """``taps`` reading a crop from ``(row0, col0)`` instead of the whole raster."""
    return (_shift(taps[0], row0), _shift(taps[1], col0))


def _shift(taps: _Axis, start: int) -> _Axis:
    if isinstance(taps, PchipTaps):
        return PchipTaps(taps.indices - start, taps.fraction)
    return (taps[0] - start, taps[1])

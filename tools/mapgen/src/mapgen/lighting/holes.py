"""No data in the captured surface: the void, which the light takes as open.

The capture stores NaN where the draw has no height (``render/surface.py``). Nothing there
blocks the sun or the sky, and a pixel over it takes the light of the nearest pixel that
has a height. docs/map/light-and-crowns.md section 29, "Edges of the light".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.lighting.light_tiles import downsample
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid, I64Grid

__all__ = ["OPEN_M", "Holes", "fill_holes", "find_holes", "half_heights", "opened"]

#: A height far below any ground, so no step of a march or of the sky view rises to it.
OPEN_M = np.float32(-1.0e4)


class Holes(NamedTuple):
    """A plane's pixels with no height, and the nearest pixel with one, None if none has."""

    mask: BoolMask
    nearest: tuple[I64Grid, I64Grid] | None


def find_holes(plane: NDArray[np.floating]) -> Holes | None:
    """Where ``plane`` is NaN and, for every pixel, the nearest that is not; None without NaN."""
    mask = np.isnan(plane)
    if not mask.any():
        return None
    if mask.all():
        return Holes(mask, None)
    _distance, (rows, cols) = ndimage.distance_transform_edt(mask, return_indices=True)
    return Holes(mask, (rows.astype(np.int64), cols.astype(np.int64)))


def fill_holes(plane: F32Grid, holes: Holes | None, empty: float) -> F32Grid:
    """``plane`` with each hole given its nearest pixel's value; ``empty`` where none has one."""
    if holes is None:
        return plane
    if holes.nearest is None:
        return np.full(plane.shape, np.float32(empty), np.float32)
    return np.ascontiguousarray(plane[holes.nearest])


def opened(plane: F32Grid) -> F32Grid:
    """``plane`` with its holes at ``OPEN_M``: they block nothing a march or the sky view reads."""
    return np.where(np.isnan(plane), OPEN_M, plane)


def half_heights(plane: F32Grid) -> FloatGrid:
    """``plane`` at half resolution: the mean of a 2 x 2 cell, of its pixels with a height
    when it has holes, so a hole shrinks to the cells that have none at all."""
    if not np.isnan(plane).any():
        return downsample(plane)
    return downsample(plane, how=np.nanmean)

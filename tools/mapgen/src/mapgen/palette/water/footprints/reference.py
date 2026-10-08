"""The footprints' two loops in numpy, the reference ``gpu`` gives the bits of.

``texel_marks`` folds rows of the mesh raster onto the field's 1 m texels and marks each texel
with the groups it draws and whether it is sea; ``pixel_land`` reads the land plane back at
each pixel of a piece. docs/map/painted.md section 27, "Whole footprints".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.palette.water.shore import MESH_REACH_M, OCEAN_LEVEL_M
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_SHELL, MESH_TERRACE
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I16Grid, I64Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "CORAL_GROUP",
    "GROUPS",
    "SEA_MARK",
    "TERRACE_GROUP",
    "AxisCover",
    "TexelPlanes",
    "axis_cover",
    "nearest_texel",
    "pixel_land",
    "texel_marks",
]

#: The footprint groups, each labelled on its own: coral and shells, and the terraces.
CORAL_GROUP, TERRACE_GROUP = 1, 2
#: A texel's mark beside its groups' bits: it is sea.
SEA_MARK = 4

NO_TOP = np.float32(-np.inf)
CM_PER_M = np.float32(100.0)
DM_PER_M = np.float32(hf.DM_PER_M)
OCEAN_M = np.float32(OCEAN_LEVEL_M)
OCEAN_DM = np.float32(OCEAN_LEVEL_M * hf.DM_PER_M)
REACH_M = np.float32(MESH_REACH_M)
NO_GROUND = np.float32(hf.NODATA)


def _groups() -> U8Grid:
    table = np.zeros(256, np.uint8)
    table[[MESH_CORAL, MESH_SHELL]] = CORAL_GROUP
    table[MESH_TERRACE] = TERRACE_GROUP
    return table


#: Each class code's group bit, 0 for a rock or no mesh.
GROUPS = _groups()


class AxisCover(NamedTuple):
    """The pixels along one axis that fold onto each texel, ``[start, stop)``; none where
    ``start >= stop``."""

    start: I64Grid
    stop: I64Grid


class TexelPlanes(NamedTuple):
    """The field's planes a texel is marked from: the water plane's wet texels, the ocean's
    reach, the drawn ground in dm (``hf.NODATA`` where none) and the water level in dm."""

    wet: U8Grid
    reach: U8Grid
    ground: F32Grid
    level: I16Grid

    def rows(self, part: slice) -> TexelPlanes:
        """The planes' rows ``part``."""
        return TexelPlanes(self.wet[part], self.reach[part], self.ground[part], self.level[part])


def nearest_texel(position: F64Grid) -> I64Grid:
    """The texel nearest each position along a field axis, as ``grid_position`` gives it."""
    return np.rint(position).astype(np.int64)


def axis_cover(position: F64Grid, half: float, texels: int) -> AxisCover:
    """Which pixels fold onto each of ``texels``: those whose cell, ``half`` a texel either side
    of ``position``, holds the texel's centre, and a pixel whose cell holds none on its nearest.

    A pixel finer than a texel lands on its nearest texel only, and a coarser one on every
    texel it covers, so a footprint stays joined on the 1 m grid at any size.
    """
    nearest = nearest_texel(position)
    lo = np.ceil(position - half).astype(np.int64)
    hi = np.floor(position + half).astype(np.int64)
    narrow = lo > hi
    lo, hi = np.where(narrow, nearest, lo), np.where(narrow, nearest, hi)
    each = np.arange(texels)
    return AxisCover(np.searchsorted(hi, each, "left"), np.searchsorted(lo, each, "right"))


def texel_marks(
    cls: U8Grid, z_cm: F32Grid, rows: AxisCover, cols: AxisCover, texels: TexelPlanes
) -> U8Grid:
    """Each texel's marks over a run of texel rows: the groups it draws, and ``SEA_MARK``.

    ``cls`` and ``z_cm`` are the mesh raster's rows the run folds, ``rows`` each texel row's
    rows of them and ``cols`` each texel column's columns. A texel draws a group where the
    group's highest top over it stands above its water level less ``MESH_REACH_M``, as a pixel
    is drawn in ``shore.composite_meshes``. It is sea where the water plane is wet, or where
    its ground is under the ocean's level within the ocean's reach, which the crossing rule
    draws as sea; that sea's level is the ocean's.
    """
    groups = np.where(np.isnan(z_cm), np.uint8(0), GROUPS[cls])
    wet = texels.wet != 0
    ground = texels.ground
    sea = wet | ((texels.reach != 0) & (ground != NO_GROUND) & (ground < OCEAN_DM))
    level = np.where(
        wet & (texels.level != hf.NODATA),
        texels.level.astype(np.float32) / DM_PER_M,
        np.where(sea, OCEAN_M, NO_TOP),
    )
    floor = level - REACH_M
    marks = np.where(sea, np.uint8(SEA_MARK), np.uint8(0))
    for group in (CORAL_GROUP, TERRACE_GROUP):
        top = np.where(groups == group, z_cm, NO_TOP)
        top = _highest(_highest(top, rows, 0), cols, 1)
        marks |= np.where(top / CM_PER_M > floor, np.uint8(group), np.uint8(0))
    return marks


def _highest(plane: F32Grid, cover: AxisCover, axis: int) -> F32Grid:
    """``plane``'s maximum over each of ``cover``'s ranges along ``axis``; -inf over none."""
    length = plane.shape[axis]
    pad = list(plane.shape)
    pad[axis] = 1
    padded = np.concatenate([plane, np.full(pad, NO_TOP, np.float32)], axis=axis)
    bounds = np.clip(np.stack([cover.start, cover.stop], axis=1).ravel(), 0, length)
    top = np.maximum.reduceat(padded, bounds, axis=axis)
    top = np.take(top, np.arange(0, bounds.size, 2), axis=axis)
    hollow = (cover.start >= cover.stop).reshape((-1, 1) if axis == 0 else (1, -1))
    return np.where(hollow, NO_TOP, top)


def pixel_land(land: U8Grid, rows: I64Grid, cols: I64Grid, cls: U8Grid) -> BoolMask:
    """Whether each pixel's footprint stands on land: its texel, ``rows`` by ``cols``, carries
    the bit of its class's group."""
    return (land[np.ix_(rows, cols)] & GROUPS[cls]) != 0

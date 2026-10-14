"""A light block's trees at half resolution, as the porous march and the canopy cells read them.

The occluder's planes (``render/draw/tree_occluder.py``) give each pixel the trees' top, their
cover and whether the top is the Titan canopy's. A half-resolution cell takes the cover-weighted
mean top, a leaf shell ``LEAF_SHELL_M`` under it, and the crowns' and the Titan canopy's optical
depth of their mean cover; it receives on that top where it is at least ``CANOPY_COVER_MIN``
covered, else on the ground. The canopy cells hold the ground's horizon received on those
receivers, marched at quarter resolution, where a tree covers the cell.
docs/map/light-and-crowns.md section 29, "A porous canopy".
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from scipy import ndimage

from mapgen.lighting.horizon import FADE_M, horizon_reach_px, received_horizon
from mapgen.lighting.light_tiles import downsample, optional_array, padded_window
from mapgen.lighting.spans.holes import half_heights
from mapgen.lighting.spans.porous import (
    CANOPY_COVER_MIN,
    CANOPY_FLOOR_DEG,
    LEAF_SHELL_M,
    PorousSurface,
    depth_of,
    porous_surface,
)
from satisfactory_mcp.core.arrays import BoolMask, F32Grid

__all__ = [
    "CANOPY_PAD_CELLS",
    "TITAN_BYTE",
    "CanopyPlanes",
    "TreePlanes",
    "canopy_cell",
    "canopy_planes",
    "tree_cells",
    "tree_planes",
    "trees_porous",
]

#: The occluder's Titan byte where the Titan canopy's top is a pixel's highest.
TITAN_BYTE = 255
#: Cells a canopy cell is stored past the trees' cover, so a bilinear read of it at a tree's
#: edge never meets a cell without one.
CANOPY_PAD_CELLS = 3

#: Rows of the window a block reads the trees for at a time, at full resolution.
_TREE_ROWS = 1024

_Window = tuple[int, int, int, int]


class TreePlanes(NamedTuple):
    """A window's trees at half resolution: the receivers, the leaf shell's underside and
    top (NaN where no tree), the crowns' and the Titan canopy's optical depth, and the mean
    cover."""

    receivers: F32Grid
    lo: F32Grid
    hi: F32Grid
    tau_crowns: F32Grid
    tau_titans: F32Grid
    cover: F32Grid


def tree_cells(
    z: F32Grid, z_half: F32Grid, top: F32Grid, cover: F32Grid, titan: BoolMask
) -> TreePlanes:
    """Full-resolution rows of the trees (``top`` metres, NaN where none, ``cover`` 0 to 1,
    ``titan``) over the drawn surface ``z``, as half-resolution cells on ``z_half``."""
    over = np.isfinite(top) & (top > z) & (cover > 0)
    share = np.where(over, cover, np.float32(0.0)).astype(np.float32)
    mean = downsample(share).astype(np.float32)
    weighted = downsample(np.where(over, share * top, np.float32(0.0))).astype(np.float32)
    have = mean > 0
    hi = np.where(have, weighted / np.where(have, mean, np.float32(1.0)), np.nan)
    hi = np.where(hi > z_half, hi, np.nan).astype(np.float32)
    lo = np.where(np.isfinite(hi), np.maximum(z_half, hi - np.float32(LEAF_SHELL_M)), np.nan)
    crowns = downsample(np.where(titan, np.float32(0.0), share)).astype(np.float32)
    titans = downsample(np.where(titan, share, np.float32(0.0))).astype(np.float32)
    canopy = (mean >= np.float32(CANOPY_COVER_MIN)) & np.isfinite(hi)
    receivers = np.where(canopy, hi, z_half).astype(np.float32)
    return TreePlanes(
        receivers,
        lo.astype(np.float32),
        hi,
        depth_of(crowns),
        depth_of(titans),
        np.where(np.isfinite(hi), mean, np.float32(0.0)).astype(np.float32),
    )


def tree_planes(
    work: Path, window: _Window, z_window: F32Grid, z_half: F32Grid
) -> TreePlanes | None:
    """The trees of the window at half resolution, or None without an occluder or where the
    window holds none. Rows off the sheet hold none."""
    occluder = optional_array(work, "occluder", np.float32)
    if occluder is None:
        return None
    covers = optional_array(work, "occluder_cover", np.uint8)
    titans = optional_array(work, "occluder_titan", np.uint8)
    r0, _r1, c0, c1 = window
    parts: list[TreePlanes] = []
    for row in range(0, z_window.shape[0], _TREE_ROWS):
        stop = min(row + _TREE_ROWS, z_window.shape[0])
        a, b = max(r0 + row, 0), min(r0 + stop, occluder.shape[0])
        shape = (stop - row, c1 - c0)
        top = np.full(shape, np.nan, np.float32)
        cover = np.zeros(shape, np.float32)
        titan = np.zeros(shape, bool)
        if a < b:
            rows = slice(a - r0 - row, b - r0 - row)
            top[rows] = padded_window(occluder, a, b, c0, c1, np.nan)
            cover[rows] = (
                np.isfinite(top[rows])
                if covers is None
                else padded_window(covers, a, b, c0, c1, 0.0) / np.float32(255.0)
            )
            if titans is not None:
                titan[rows] = padded_window(titans, a, b, c0, c1, 0.0) == TITAN_BYTE
        half = z_half[row // 2 : stop // 2]
        parts.append(tree_cells(z_window[row:stop], half, top, cover, titan))
    planes = TreePlanes(*(np.concatenate(group) for group in zip(*parts, strict=True)))
    return planes if np.isfinite(planes.hi).any() else None


def trees_porous(planes: TreePlanes) -> PorousSurface:
    """The trees as the porous march reads them."""
    return porous_surface(
        planes.receivers, planes.lo, planes.hi, planes.tau_crowns, planes.tau_titans
    )


class CanopyPlanes(NamedTuple):
    """What a canopy cell is marched from: the solid ground and the receivers at quarter
    resolution, padded so the march reaches, the quarter halo and the padding in half cells;
    and where the cells are stored, at half resolution."""

    solid: F32Grid
    receivers: F32Grid
    halo: int
    pad: int
    present: BoolMask


def canopy_planes(
    solid: F32Grid, planes: TreePlanes, halo: int, spacing_m: float
) -> CanopyPlanes:
    """The canopy cells' march over ``solid`` received on the trees' receivers, for a window
    of half-resolution cells with ``halo`` (``spacing_m`` apart) around the core."""
    reach = horizon_reach_px(2 * spacing_m)
    pad = max(2 * reach + 2 - halo, 0)
    pad += pad % 2
    present = ndimage.binary_dilation(planes.cover > 0, iterations=CANOPY_PAD_CELLS)
    ground, receivers = (
        half_heights(np.pad(plane, pad, mode="edge")).astype(np.float32)
        for plane in (solid, planes.receivers)
    )
    return CanopyPlanes(ground, receivers, reach, pad, present)


def canopy_cell(canopy: CanopyPlanes, halo: int, az_deg: float, spacing_m: float) -> F32Grid:
    """The ground's horizon toward ``az_deg`` received on the canopy, degrees, for the core
    and its ring inside ``halo``: at least ``CANOPY_FLOOR_DEG`` where stored, else 0."""
    deg = received_horizon(canopy.solid, canopy.receivers, canopy.halo, az_deg, 2 * spacing_m, FADE_M)
    return canopy_from_quarter(deg, canopy, halo)


def canopy_from_quarter(deg: F32Grid, canopy: CanopyPlanes, halo: int) -> F32Grid:
    """A quarter-resolution canopy horizon brought to the half-resolution core with its ring,
    floored where stored and 0 elsewhere."""
    half = ndimage.zoom(deg, 2, order=1, mode="nearest", grid_mode=True)
    start = halo - (2 * canopy.halo - canopy.pad)
    side = canopy.present.shape[0] - 2 * halo
    cut = half[start : start + side, start : start + side]
    present = canopy.present[halo : halo + side, halo : halo + side]
    floor = np.float32(CANOPY_FLOOR_DEG)
    return np.where(present, np.maximum(cut, floor), np.float32(0.0)).astype(np.float32)

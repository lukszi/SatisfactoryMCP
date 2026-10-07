"""Where the rebuilt lattice is left empty because the artwork draws void: its pits, and the
fill past its world rim. Imported by ``terrain.fill``; the numbers behind each constant are in
docs/map/renders.md section 26, "The open sea, the void and the pits" and "The fill past the
world's rim".
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from satisfactory_mcp.core.arrays import BoolMask

__all__ = [
    "PIT_FLOOR_M",
    "PIT_SHARE",
    "RIM_CORE_TEXELS",
    "RIM_REACH_TEXELS",
    "edge_labels",
    "pit_mask",
    "pits",
    "void_past_rim",
]

#: An interior no-data hole is a pit, left empty, when the artwork draws at least this share
#: of it as void: 0.88 to 0.95 for the crater and the abyss pits, 0.5 to 0.7 for a crack
#: under its white outline, 0.44 and less for the holes it draws as ground.
PIT_SHARE = 0.5

#: Ground lower than this is a pit's floor, not ground the map shows: the landscape's own
#: lowest height (-254 to -258 m) and the deepest abyss walls, 93% drawn as void.
PIT_FLOOR_M = -200.0

#: The void past the world's rim is the artwork's void that outlasts an erosion of this many
#: texels and reaches the grid's edge: the sheet's 3-pixel frame and the dark strokes inside
#: the world are thinner.
RIM_CORE_TEXELS = 4

#: That core is grown back through the void this far, so it reaches the rim line again.
RIM_REACH_TEXELS = 8


def edge_labels(labels: NDArray[np.integer]) -> NDArray[np.integer]:
    """The labels a labelled grid has on its outer rows and columns, 0 left out."""
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    return edge[edge > 0]


def pit_mask(nodata: BoolMask, void: BoolMask, floor: BoolMask | None = None) -> BoolMask:
    """The pits: each region of no data and ``floor`` ground the artwork draws as void over
    ``PIT_SHARE`` of. Of one that reaches the field's edge only the floor, as the rest is
    left empty anyway."""
    floor = np.zeros(nodata.shape, bool) if floor is None else floor
    labels, count = ndimage.label(nodata | floor)
    if not count:
        return np.zeros(nodata.shape, bool)
    share = ndimage.mean(void, labels, np.arange(1, count + 1))
    keep = np.concatenate([[False], share >= PIT_SHARE])
    edge = np.zeros(count + 1, bool)
    edge[edge_labels(labels)] = True
    return keep[labels] & (floor | ~edge[labels])


def pits(height_dm: NDArray[np.number], empty: BoolMask,
         void: BoolMask | None) -> tuple[BoolMask, BoolMask]:  # fmt: skip
    """The pits the artwork draws as void among the ``empty`` texels and the ground below
    ``PIT_FLOOR_M``, and that ground."""
    floor = ~empty & (height_dm <= np.float32(PIT_FLOOR_M * 10.0))
    pit = np.zeros(empty.shape, bool) if void is None else pit_mask(empty, void, floor)
    return pit, floor


def void_past_rim(void: BoolMask) -> BoolMask:
    """The artwork's void past the world's rim: its void at least ``RIM_CORE_TEXELS`` deep
    that reaches the grid's edge (as deep as that lets it), grown back through the void by
    ``RIM_REACH_TEXELS``.

    The growth stays inside the void, so it never crosses the rim's light line; a pit, or a
    dark stroke that meets the void through a gap in that line, is not reached far past it.
    """
    core_n = RIM_CORE_TEXELS
    core = ndimage.binary_erosion(void, iterations=core_n)
    labels, count = ndimage.label(core)
    if not count:
        return np.zeros(void.shape, bool)
    outer = np.isin(labels, edge_labels(labels[core_n:-core_n, core_n:-core_n]))
    del labels, core
    return ndimage.binary_dilation(outer, iterations=RIM_REACH_TEXELS, mask=void)

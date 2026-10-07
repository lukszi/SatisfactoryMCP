"""Small level steps inside one sheet of water, feathered into a ramp.

A box edge, or a joint between two river sections, puts a step of a few decimetres into a
level plane where the water is one sheet. Drawn as it stands, the depth, and with it the tone,
jumps along a straight line. docs/map/water.md section 33, "Box seams".
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

import numpy as np
import scipy.sparse as sp
from scipy import ndimage

from mapgen.palette.water.geodesic import geodesic_steps
from mapgen.terrain.solve import jacobi_cg
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I16Grid, I64Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BOX_SEAMS",
    "SeamFeather",
    "feather_box_seams",
    "feather_steps",
    "step_marks",
    "windows_around",
]

_CROSS = ndimage.generate_binary_structure(2, 1)

#: A step is a seam where the level runs on either side by at most this, the field's
#: decimetre halved, or by this share of the step, whichever is more.
_FLAT_M = 0.05
_RUN_SHARE = 0.25

#: The two 4-neighbour pairings of a grid: each texel with the one below, and on its right.
_PAIRS = (
    ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
    ((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
)


class SeamFeather(NamedTuple):
    """Which steps are seams, and how far they are feathered: steps over ``low_m`` and at
    most ``high_m`` between 4-neighbours, over ``reach`` texels either side, through
    neighbours at most ``run_m`` apart."""

    low_m: float
    high_m: float
    reach: int
    run_m: float


#: The field's box seams: steps of 0.2 to 1 m between flat sheets of water, feathered over
#: 12 m either side.
BOX_SEAMS = SeamFeather(low_m=0.15, high_m=1.0, reach=12, run_m=_FLAT_M)


def step_marks(level: F32Grid, valid: BoolMask, low_m: float, high_m: float) -> BoolMask:
    """The texels of ``valid`` beside a 4-neighbour of ``valid`` whose level differs by more
    than ``low_m`` and at most ``high_m``, and the step stands out from the level's run on to
    the next texel on both sides: a step between two sheets, not a sloped surface."""
    marks = np.zeros(valid.shape, bool)
    rows, cols = valid.shape
    for axis in (0, 1):
        n = rows if axis == 0 else cols
        before, a, b, after = (_cut(axis, k, n - 3 + k) for k in range(4))
        with np.errstate(invalid="ignore"):
            step = np.abs(level[b] - level[a])
            run = np.maximum(np.float32(_FLAT_M), step * np.float32(_RUN_SHARE))
            flat = (np.abs(level[a] - level[before]) <= run) & (
                np.abs(level[after] - level[b]) <= run
            )
        hit = valid[before] & valid[a] & valid[b] & valid[after] & flat
        hit &= (step > low_m) & (step <= high_m)
        marks[a] |= hit
        marks[b] |= hit
    return marks


def _cut(axis: int, start: int, stop: int) -> tuple[slice, slice]:
    """``[start, stop)`` along ``axis``, everything along the other."""
    along = slice(start, stop)
    return (along, slice(None)) if axis == 0 else (slice(None), along)


def feather_box_seams(level_dm: I16Grid, grades: U8Grid) -> tuple[I16Grid, int]:
    """The drawn level plane, decimetres, with ``BOX_SEAMS`` feathered, and how many texels
    changed."""
    valid = (grades != hf.WATER_DRY) & (level_dm != hf.NODATA)
    level = np.where(valid, level_dm / np.float32(hf.DM_PER_M), np.nan).astype(np.float32)
    out, _moved = feather_steps(level, valid, BOX_SEAMS)
    with np.errstate(invalid="ignore"):
        drawn = np.where(valid, np.round(out * np.float32(hf.DM_PER_M)), level_dm)
    feathered = drawn.astype(np.int16)
    return feathered, int(np.count_nonzero(feathered != level_dm))


def feather_steps(level: F32Grid, valid: BoolMask, feather: SeamFeather) -> tuple[F32Grid, int]:
    """``level`` with its seams feathered, and how many texels changed.

    Within ``reach`` of a seam, through ``valid``, the level is a screened membrane held to
    the level as it was over a third of the reach, so a step becomes a ramp. The membrane
    joins the two sides of a seam and 4-neighbours whose levels differ by at most ``run_m``:
    a fall's drop, or a slope steeper than that, is never smoothed over, and past the reach
    nothing changes. No texel moves by more than half of ``high_m``.
    """
    marks = step_marks(level, valid, feather.low_m, feather.high_m)
    out = level.copy()
    changed = 0
    for window in windows_around(marks, feather.reach):
        part, seams = valid[window], marks[window]
        free = seams | (geodesic_steps(seams, part, feather.reach) <= feather.reach)
        held = part & ndimage.binary_dilation(free, _CROSS)
        solved = _membrane(level[window], (held, free, seams), feather)
        moved = free & (solved != level[window])
        out[window][moved] = solved[moved]
        changed += int(moved.sum())
    return out, changed


def windows_around(marks: BoolMask, reach: int, cell: int = 64) -> Iterator[tuple[slice, slice]]:
    """Disjoint windows holding every texel within ``reach`` of ``marks``."""
    rows, cols = marks.shape
    coarse = np.pad(marks, ((0, -rows % cell), (0, -cols % cell)))
    coarse = coarse.reshape(coarse.shape[0] // cell, cell, -1, cell).any(axis=(1, 3))
    grown = ndimage.binary_dilation(coarse, np.ones((3, 3), bool), int(np.ceil(reach / cell)))
    while True:
        labels, _count = ndimage.label(grown, structure=np.ones((3, 3), bool))
        boxes = [box for box in ndimage.find_objects(labels) if box is not None]
        filled = np.zeros_like(grown)
        for box in boxes:
            filled[box] = True
        if (filled == grown).all():
            break
        grown = filled
    for r, c in boxes:
        yield (
            slice(r.start * cell, min(r.stop * cell, rows)),
            slice(c.start * cell, min(c.stop * cell, cols)),
        )


def _membrane(
    level: F32Grid, masks: tuple[BoolMask, BoolMask, BoolMask], feather: SeamFeather
) -> F32Grid:
    """``level`` on ``free`` replaced by the screened membrane ``feather_steps`` describes;
    the rest of ``valid`` holds it in place. ``masks`` is ``(valid, free, seams)``."""
    valid, free, seams = masks
    index = np.full(level.shape, -1, np.int64)
    index[valid] = np.arange(int(valid.sum()))
    src, dst = _joins(level, (valid, seams), index, feather)
    count = int(valid.sum())
    adjacency = sp.coo_matrix((np.ones(len(src)), (src, dst)), shape=(count, count))
    adjacency = (adjacency + adjacency.T).tocsr()
    degree = sp.diags(np.asarray(adjacency.sum(axis=1)).ravel())
    laplacian: sp.csr_matrix = (degree - adjacency).tocsr()
    values = level[valid].astype(np.float64)
    unknown = free[valid]
    screen = 9.0 / float(feather.reach * feather.reach)
    a_uu = (laplacian[unknown][:, unknown] + sp.eye(int(unknown.sum())) * screen).tocsr()
    rhs = screen * values[unknown] + _held_neighbours(src, dst, unknown, values)
    held = values[unknown]
    most = feather.high_m / 2.0
    values[unknown] = np.clip(jacobi_cg(a_uu, rhs, held, rtol=1e-6), held - most, held + most)
    out = level.copy()
    out[valid] = values.astype(np.float32)
    return out


def _held_neighbours(src: I64Grid, dst: I64Grid, unknown: BoolMask, values: F64Grid) -> F64Grid:
    """Per unknown texel, the sum of the levels of the held texels it is joined to: what they
    put into its row of the membrane, summed edge by edge in a fixed order."""
    sums = np.zeros(len(values), np.float64)
    for a, b in ((src, dst), (dst, src)):
        crossing = unknown[a] & ~unknown[b]
        np.add.at(sums, a[crossing], values[b[crossing]])
    return sums[unknown]


def _joins(
    level: F32Grid, masks: tuple[BoolMask, BoolMask], index: I64Grid, feather: SeamFeather
) -> tuple[I64Grid, I64Grid]:
    """The 4-neighbour pairs of ``valid`` the membrane joins, as ``index`` numbers them: levels
    at most ``run_m`` apart, or both beside a seam and at most ``high_m`` apart. ``masks`` is
    ``(valid, seams)``."""
    valid, seams = masks
    src: list[I64Grid] = []
    dst: list[I64Grid] = []
    for a, b in _PAIRS:
        with np.errstate(invalid="ignore"):
            step = np.abs(level[a] - level[b])
        across = seams[a] & seams[b] & (step <= feather.high_m)
        joined = valid[a] & valid[b] & ((step <= feather.run_m) | across)
        src.append(index[a][joined])
        dst.append(index[b][joined])
    return np.concatenate(src), np.concatenate(dst)

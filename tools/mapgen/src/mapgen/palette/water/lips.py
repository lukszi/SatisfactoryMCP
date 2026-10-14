"""Water a waterfall pours from stands at least at the fall's lip.

The perched re-levelling (``perched.perched_levels``) spans a body's shoreline, and where a
lake's box runs on over the cliff its fall drops from, that shoreline is the cliff and the
membrane sinks the lake to its bed. docs/map/water.md section 38, "Held at a lip".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.water.falls import FallRecord
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import F64Grid, I16Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["LIP_OCEAN_M", "LIP_UPSTREAM", "LipsMeta", "fall_lips", "hold_at_lips"]

#: Where the water feeding a lip is looked for: in from the lip as a share of the run of its
#: top modules, which the tool lays on that water, and across it as a share of its width.
LIP_UPSTREAM = (0.1, 0.25, 0.5, 0.75, 1.0)
_ACROSS = (-0.4, -0.2, 0.0, 0.2, 0.4)

#: A lip less than this above the ocean level is the ocean pouring off the world's edge.
LIP_OCEAN_M = 1.0

#: The columns of a lip's row (``fall_lips``): the lip, the unit vectors along and out of
#: it, its half width and its top modules' run.
_X, _Y, _Z, _UX, _UY, _NX, _NY, _HALF, _TOP = range(9)


class LipsMeta(TypedDict):
    """What ``hold_at_lips`` raised: the lips that held water up, and its texels."""

    lips: int
    texels: int


def fall_lips(records: Sequence[FallRecord] | None) -> F64Grid:
    """One row per fall: x, y, z, along, out, half width and top run, in metres."""
    rows = [
        (f["x"], f["y"], f["z"], *f["along"], *f["out"], f["width_m"] / 2, f["top_len_m"])
        for f in records or ()
    ]
    return np.array(rows, np.float64).reshape(-1, 9)


def _upstream(
    lip: F64Grid, field: hf.Field, shape: tuple[int, ...]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Texel rows and columns of ``shape`` under ``lip``'s top modules, upstream of it."""
    across = np.tile(_ACROSS, len(LIP_UPSTREAM)) * 2 * lip[_HALF]
    back = np.repeat(LIP_UPSTREAM, len(_ACROSS)) * lip[_TOP]
    x_m: F64Grid = lip[_X] + across * lip[_UX] - back * lip[_NX]
    y_m: F64Grid = lip[_Y] + across * lip[_UY] - back * lip[_NY]
    col = np.round((x_m * 100 - field.x0_cm) / field.spacing_cm).astype(np.int64)
    row = np.round((y_m * 100 - field.y0_cm) / field.spacing_cm).astype(np.int64)
    on = (col >= 0) & (col < shape[1]) & (row >= 0) & (row < shape[0])
    return row[on], col[on]


def hold_at_lips(
    field: hf.Field, before: I16Grid, level: I16Grid, lips: F64Grid | None
) -> tuple[I16Grid, LipsMeta]:
    """``level`` with water re-levelled below a fall's lip raised back to it, upstream of it.

    ``before`` is the box levels ``level`` was re-levelled from; nothing rises above them.
    Only a part the re-levelling lowered under the lip's top modules is raised, and only on
    the lip's upstream side: water past it keeps its membrane down the cliff.
    """
    lowered = (level != hf.NODATA) & (before != hf.NODATA) & (level < before)
    if lips is None or not len(lips) or not lowered.any():
        return level, {"lips": 0, "texels": 0}
    parts, _count = ndimage.label(lowered, structure=np.ones((3, 3), bool))
    boxes: list[tuple[slice, ...] | None] = ndimage.find_objects(parts)
    out = level.copy()
    held = 0
    for lip in lips[lips[:, _Z] >= OCEAN_LEVEL_M + LIP_OCEAN_M]:
        z_dm = np.int16(round(lip[_Z] * hf.DM_PER_M))
        row, col = _upstream(lip, field, level.shape)
        feeds = (parts[row, col] > 0) & (level[row, col] < z_dm) & (before[row, col] >= z_dm)
        raised = False
        for part in map(int, np.unique(parts[row, col][feeds])):
            window = boxes[part - 1]
            if window is None:
                continue
            (r0, r1), (c0, c1) = ((int(cut.start), int(cut.stop)) for cut in window)
            x_m = (field.x0_cm + np.arange(c0, c1) * field.spacing_cm) / 100 - lip[_X]
            y_m = (field.y0_cm + np.arange(r0, r1) * field.spacing_cm) / 100 - lip[_Y]
            upstream = x_m[None, :] * lip[_NX] + y_m[:, None] * lip[_NY] <= 0
            target = (parts[window] == part) & upstream & (out[window] < z_dm)
            out[window][target] = np.minimum(z_dm, before[window][target])
            raised |= bool(target.any())
        held += raised
    return out, {"lips": held, "texels": int(np.count_nonzero(out != level))}

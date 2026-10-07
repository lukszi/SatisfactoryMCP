"""Where the caves are: a safety flag beside the terrain field, never a cave floor.

The field holds one ground per (x, y), so a point in a cave reads the surface above it.
``tools/gen_world_heightmap.py --caves`` writes ``data/local/caves/`` from two signals in the
reader's own install; this module is their format and a reader over them. Absent is normal:
with no directory every answer is ``none`` (docs/map/heightfield.md section 23).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import numpy as np

from ....core.arrays import BoolMask, F64Grid, I64Grid, U8Grid
from ....core.jsontypes import JsonObject, JsonValue, as_float, as_int, require_object

__all__ = [
    "BELOW",
    "BIT_HULL",
    "BIT_MARKERS",
    "CAVE_VALUES",
    "DATA_NAME",
    "DIR_NAME",
    "INSIDE",
    "INSIDE_DEPTH_M",
    "META_NAME",
    "NONE",
    "CaveGrid",
    "CaveValue",
    "Caves",
    "load_caves",
    "note",
]

DIR_NAME = "caves"
DATA_NAME = "caves.npz"
META_NAME = "meta.json"

#: ``Reading.cave``: no cave known here; a cave lies under this (x, y); the point is in one.
CaveValue = Literal["none", "below", "inside"]
NONE: Final = "none"
BELOW: Final = "below"
INSIDE: Final = "inside"
CAVE_VALUES: tuple[CaveValue, ...] = (NONE, BELOW, INSIDE)

#: The mask's bits, which are the file format. Markers: cave decoration under the ground,
#: buffered. Hull: every cell a cave sound volume's plan touches; the 3D test runs only there.
BIT_MARKERS = 1
BIT_HULL = 2

#: A hint this far under every surface, over a flagged cell, is in the cave below it.
INSIDE_DEPTH_M = 3.0


def note(cave: CaveValue, surface_m: float | None) -> str | None:
    """The one line a height answer carries for a cave, or ``None`` for no cave."""
    above = "" if surface_m is None else f" (the surface above is {surface_m:.0f} m)"
    if cave == INSIDE:
        return f"in a cave: ground height unknown here{above}"
    if cave == BELOW:
        return "a cave lies under this point: the height given is the surface, not the cave floor"
    return None


@dataclass(frozen=True)
class CaveGrid:
    """The mask's cells: ``cell_cm`` squares from a world origin, ``height`` rows of ``width``."""

    x0_cm: float
    y0_cm: float
    cell_cm: float
    width: int
    height: int

    @classmethod
    def from_meta(cls, grid: JsonObject) -> CaveGrid:
        return cls(
            x0_cm=as_float(grid["x0_cm"]),
            y0_cm=as_float(grid["y0_cm"]),
            cell_cm=as_float(grid["cell_cm"]),
            width=as_int(grid["width"]),
            height=as_int(grid["height"]),
        )


@dataclass(frozen=True)
class Caves:
    """The 2D cell mask and the 3D sound-volume hulls, in world centimetres."""

    grid: CaveGrid
    mask: U8Grid
    #: Every hull's planes as rows ``(nx, ny, nz, d)``; hull ``i`` is ``starts[i]:starts[i+1]``.
    planes: F64Grid
    starts: I64Grid
    #: One world box per hull, ``(x0, y0, z0, x1, y1, z1)``.
    boxes: F64Grid

    def bits(self, x_cm: float, y_cm: float) -> int:
        """The mask cell holding a point: floored, since a cell is an area. 0 off the grid."""
        col = math.floor((x_cm - self.grid.x0_cm) / self.grid.cell_cm)
        row = math.floor((y_cm - self.grid.y0_cm) / self.grid.cell_cm)
        h, w = self.mask.shape
        if not (0 <= row < h and 0 <= col < w):
            return 0
        return int(self.mask[row, col])

    def flagged(self, xs_cm: F64Grid, ys_cm: F64Grid) -> BoolMask:
        """``(len(ys), len(xs))`` bools: whether a cave lies under each grid point."""
        h, w = self.mask.shape
        g = self.grid
        cols = np.floor((np.asarray(xs_cm, np.float64) - g.x0_cm) / g.cell_cm).astype(int)
        rows = np.floor((np.asarray(ys_cm, np.float64) - g.y0_cm) / g.cell_cm).astype(int)
        ok_c = (cols >= 0) & (cols < w)
        ok_r = (rows >= 0) & (rows < h)
        out = np.zeros((rows.size, cols.size), bool)
        out[np.ix_(ok_r, ok_c)] = self.mask[np.ix_(rows[ok_r], cols[ok_c])] != 0
        return out

    def in_hull(self, x_cm: float, y_cm: float, z_cm: float) -> bool:
        """Whether a 3D point is inside any cave sound volume."""
        b = self.boxes
        near = np.nonzero(
            (b[:, 0] <= x_cm)
            & (x_cm <= b[:, 3])
            & (b[:, 1] <= y_cm)
            & (y_cm <= b[:, 4])
            & (b[:, 2] <= z_cm)
            & (z_cm <= b[:, 5])
        )[0]
        point = np.array([x_cm, y_cm, z_cm, 1.0])
        for hull in near:
            planes = self.planes[self.starts[hull] : self.starts[hull + 1]]
            if (planes @ point <= 0.0).all():
                return True
        return False

    def classify(
        self,
        x_cm: float,
        y_cm: float,
        hint_z_cm: float | None = None,
        lowest_surface_m: float | None = None,
    ) -> CaveValue:
        """``none``, ``below`` or ``inside``. Without a hint never ``inside``."""
        bits = self.bits(x_cm, y_cm)
        flagged = bits != 0
        if hint_z_cm is not None:
            if bits & BIT_HULL and self.in_hull(x_cm, y_cm, hint_z_cm):
                return INSIDE
            if (
                flagged
                and lowest_surface_m is not None
                and hint_z_cm / 100.0 < lowest_surface_m - INSIDE_DEPTH_M
            ):
                return INSIDE
        return BELOW if flagged else NONE


def load_caves(directory: Path) -> Caves | None:
    """The cave masks in ``directory``, or ``None`` where there are none or they do not parse."""
    try:
        loaded: JsonValue = json.loads((directory / META_NAME).read_text(encoding="utf-8"))
        meta = require_object(loaded)
        grid = CaveGrid.from_meta(require_object(meta["grid"]))
        with np.load(directory / DATA_NAME, allow_pickle=False) as data:
            caves = Caves(
                grid=grid,
                mask=np.asarray(data["mask"], np.uint8),
                planes=np.asarray(data["planes"], np.float64),
                starts=np.asarray(data["starts"], np.int64),
                boxes=np.asarray(data["boxes"], np.float64),
            )
        if caves.mask.shape != (grid.height, grid.width):
            return None
        if caves.starts.size != caves.boxes.shape[0] + 1 or caves.planes.shape[1:] != (4,):
            return None
        return caves
    except (OSError, ValueError, TypeError, KeyError):
        return None

"""The world frame: the map square, the render sizes and the 1 m grid geometry."""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.arrays import F64Grid, I16Grid, I64Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BOUNDS_M",
    "FILL_RASTER_BOX_CM",
    "GRID_PX",
    "ORIGIN_X_CM",
    "ORIGIN_Y_CM",
    "RENDER_2X_PX",
    "RENDER_PX",
    "SPACING_CM",
    "Z6_TEXEL_M",
    "Z7_TEXEL_M",
    "grid_index",
    "grid_texel",
    "sample_grid",
]


_X0, _Y0, _X1, _Y1 = geo.MAP_SQUARE_M
#: The corners the sidecar pins, metres, game axes -- the in-game map square, which is also
#: ``DEFAULT_MAP_BOUNDS_M`` in the web API. Stated here so the sidecar carries them
#: explicitly instead of leaning on the server's default, and re-measured by ``calibrate``.
BOUNDS_M = {"x_min_m": _X0, "x_max_m": _X1, "y_min_m": _Y0, "y_max_m": _Y1}
#: ``(x0, x1, y0, y1)`` in cm: the in-game map square, which the fill raster spans.
FILL_RASTER_BOX_CM = (_X0 * 100, _X1 * 100, _Y0 * 100, _Y1 * 100)

#: The output grid. 7500 x 7500 at 1 m, vertex-aligned, over the in-game map square.
GRID_PX = 7500
SPACING_CM = 100.0
ORIGIN_X_CM = _X0 * 100
ORIGIN_Y_CM = _Y0 * 100

#: What a render is drawn at, z7 at the top of the 1x tree, and what its @2x tree is cut
#: from: the artwork's sheet size, the same frame (docs/spatial-and-map.md §20 says why z7).
RENDER_PX = SHEET_PX * 4
RENDER_2X_PX = SHEET_PX * 2
#: Metres of world per pixel at those two sizes.
Z7_TEXEL_M = (_X1 - _X0) / RENDER_PX
Z6_TEXEL_M = (_X1 - _X0) / RENDER_2X_PX


def grid_texel(x_cm: F64Grid, y_cm: F64Grid) -> tuple[I64Grid, I64Grid]:
    """The 1 m texel nearest each world point, ``(row, col)``, outside the grid or not.

    The same rounding ``Field.texel`` does on the other side, so the number this run
    validates on is the number the server answers with.
    """
    col = np.round((x_cm - ORIGIN_X_CM) / SPACING_CM).astype(np.int64)
    row = np.round((y_cm - ORIGIN_Y_CM) / SPACING_CM).astype(np.int64)
    return row, col


def grid_index(x_cm: F64Grid, y_cm: F64Grid) -> tuple[I64Grid, I64Grid]:
    """``grid_texel`` clipped into the grid: the edge texel for a point outside it."""
    row, col = grid_texel(x_cm, y_cm)
    return np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)


def sample_grid(height_dm: I16Grid, x_cm: F64Grid, y_cm: F64Grid) -> F64Grid:
    """Read the field at world coordinates, in metres, ``nan`` where it knows nothing."""
    row, col = grid_texel(x_cm, y_cm)
    on = (col >= 0) & (col < GRID_PX) & (row >= 0) & (row < GRID_PX)
    values = height_dm[np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on & (values != hf.NODATA), values.astype(np.float64) / 10.0, np.nan)

"""The world frame: the map square, the render sizes and the 1 m grid geometry."""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BASELINE_BOX_CM",
    "BOUNDS_M",
    "GRID_PX",
    "ORIGIN_X_CM",
    "ORIGIN_Y_CM",
    "RENDER_2X_PX",
    "RENDER_PX",
    "SPACING_CM",
    "Z6_TEXEL_M",
    "Z7_TEXEL_M",
    "sample_grid",
]


#: The corners the sidecar pins, metres, game axes -- the in-game map square, which is also
#: ``DEFAULT_MAP_BOUNDS_M`` in the web API. Stated here so the sidecar carries them
#: explicitly instead of leaning on the server's default, and re-measured by ``calibrate``.
_X0, _Y0, _X1, _Y1 = geo.MAP_SQUARE_M
BOUNDS_M = {"x_min_m": _X0, "x_max_m": _X1, "y_min_m": _Y0, "y_max_m": _Y1}


#: The raster's own box, metres of world per texel column. The in-game map square.
BASELINE_BOX_CM = (_X0 * 100, _X1 * 100, _Y0 * 100, _Y1 * 100)


#: The output grid. 7500 x 7500 at 1 m, vertex-aligned, over the in-game map square.
GRID_PX = 7500
SPACING_CM = 100.0
ORIGIN_X_CM = _X0 * 100
ORIGIN_Y_CM = _Y0 * 100


#: Metres of world per output pixel at the two candidate zoom levels, over the 7,500 m box.
Z6_TEXEL_M = 7500.0 / 16384
Z7_TEXEL_M = 7500.0 / 32768


def sample_grid(height_dm: np.ndarray, x_cm: np.ndarray, y_cm: np.ndarray) -> np.ndarray:
    """Read the field at world coordinates, in metres, ``nan`` where it knows nothing.

    The same rounding ``Field.texel`` does on the other side, so the number this run
    validates on is the number the server answers with.
    """
    col = np.round((x_cm - ORIGIN_X_CM) / SPACING_CM).astype(int)
    row = np.round((y_cm - ORIGIN_Y_CM) / SPACING_CM).astype(int)
    on = (col >= 0) & (col < GRID_PX) & (row >= 0) & (row < GRID_PX)
    values = height_dm[np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on & (values != hf.NODATA), values.astype(np.float64) / 10.0, np.nan)


#: What a render is drawn at: 32768 px over 7500 m is 0.229 m to the pixel, and z7 is the
#: top of the 1x tree. Derived from the artwork's sheet size rather than typed, because the
#: two are the same frame.
#:
#: z7 is **not** a claim that the 1 m field has more to say -- doubling the sampling of it
#: was measured twice to find no new world. It is a claim about the CLIENT: a browser shown
#: z6 at twice its scale upsamples bilinearly, and bilinear is C0, so the relief comes out
#: ruled into 0.458 m squares. z7 is the same surface evaluated by the same C1 kernel at
#: half the spacing. Over the direct regime it is new information as well, because there the
#: pixels are triangles, and ``_meta.render.two_regime.regimes`` says how much of the sheet
#: that was on the day it ran.
RENDER_PX = SHEET_PX * 4

#: What the @2x tree is cut from, one level shallower than the sheet: a 512 px z6 tree costs
#: as much as the whole 1x pyramid for pixels a hi-DPI client gets by asking for the 1x tile
#: one level deeper, which is what Leaflet's own retina path does.
RENDER_2X_PX = SHEET_PX * 2

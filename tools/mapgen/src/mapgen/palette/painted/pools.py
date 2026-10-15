"""The pools on the hot-spring terraces: the terrace's own surface seen through the hot-spring
water's optics at the pools' depth. docs/map/painted.md section 30, "The hot-spring terraces
and their pools".
"""

from __future__ import annotations

import numpy as np

from mapgen.colour import srgb_to_linear
from mapgen.palette.painted.calibration import exposure_gain
from mapgen.palette.painted.sea import surface_sky
from mapgen.palette.painted.shapes import FloatGrid, PaintedPalette

__all__ = ["POOL_DEPTH_M", "pool_colour"]

#: The water over a pool's floor: the terrace meshes' basins lie a median 0.3 to 1.0 m under
#: their pools' surfaces.
POOL_DEPTH_M = 0.8


def pool_colour(bed: FloatGrid, palette: PaintedPalette) -> FloatGrid:
    """``bed``, the terrace's ground colour before light, under ``POOL_DEPTH_M`` of the
    ``hot_spring`` water class, by the optics ``underwater`` draws a lake with, the water's
    own colours taken back to before the exposure; ``bed`` where the palette has no row."""
    row = palette.get("water_classes", {}).get("hot_spring")
    if row is None:
        return bed
    depth = np.float32(POOL_DEPTH_M)
    gain = exposure_gain(palette)
    k = np.asarray(row["k_per_m"], np.float32)
    transmit = np.exp(-k * depth) * np.float32(1.0 - row["turbidity"])
    tint = np.float32(palette["water"]["bed_wet"]) * np.asarray(row["bed_tint"], np.float32)
    body = srgb_to_linear(row["body"]).astype(np.float32) / gain
    sky = surface_sky(palette["water"]).astype(np.float32) / gain
    under = bed * tint * transmit + body * (1.0 - transmit) + sky
    deep = srgb_to_linear(row["deep"]).astype(np.float32) / gain
    far = np.float32(1.0 - np.exp(-depth / np.float32(row["deep_tau_m"])))
    return (under * (1.0 - far) + deep * far).astype(np.float32)

"""The light both layers share: the north-west sun and its hillshade.

Why each constant has its value: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from mapgen.lighting.sun import sun_vector
from satisfactory_mcp.core.arrays import F32Grid

__all__ = [
    "FLAT_SHADE",
    "FLAT_SUN_DOT",
    "SHADE_FLOOR",
    "SHADE_RANGE",
    "SUN_ALTITUDE_DEG",
    "SUN_AZIMUTH_DEG",
    "WATER_SHADE_FLOOR",
    "WATER_SHADE_RANGE",
    "flat_shade",
    "hillshade",
    "slope_degrees",
    "sun_dot",
]

#: North-west at 45 degrees; from anywhere else the eye inverts the valleys.
SUN_AZIMUTH_DEG = 315.0
SUN_ALTITUDE_DEG = 45.0

#: A fully shadowed slope keeps 45% of its own colour.
SHADE_FLOOR = 0.45
SHADE_RANGE = 0.55

#: Flat ground's sun term, and the hillshade it gets.
FLAT_SUN_DOT = float(np.sin(np.deg2rad(SUN_ALTITUDE_DEG)))
FLAT_SHADE = SHADE_FLOOR + SHADE_RANGE * FLAT_SUN_DOT

#: The hillshade touches water only a little: it is computed from the bed.
WATER_SHADE_FLOOR = 0.75
WATER_SHADE_RANGE = 0.25


def hillshade(z_m: NDArray[np.floating], spacing_m: float) -> NDArray[np.floating]:
    """North-west relief in [SHADE_FLOOR, SHADE_FLOOR + SHADE_RANGE]; rows run south."""
    return sun_dot(z_m, spacing_m) * SHADE_RANGE + SHADE_FLOOR


def flat_shade(shape: tuple[int, ...]) -> F32Grid:
    """What ``hillshade`` gives flat ground: the unlit colour's constant sun term."""
    return np.full(shape, FLAT_SHADE, np.float32)


def sun_dot(
    z_m: NDArray[np.floating],
    spacing_m: float,
    azimuth_deg: float = SUN_AZIMUTH_DEG,
    altitude_deg: float = SUN_ALTITUDE_DEG,
) -> NDArray[np.floating]:
    """The surface normal against the sun, by default the north-west one, clipped to [0, 1]."""
    light = np.array(sun_vector(azimuth_deg, altitude_deg), np.float32)
    d_south, d_east = np.gradient(z_m, spacing_m)
    lit = (-d_east * light[0] - d_south * light[1] + light[2]) / np.sqrt(
        d_east * d_east + d_south * d_south + 1.0
    )
    return np.clip(lit, 0.0, 1.0)


def slope_degrees(z_m: NDArray[np.floating], spacing_m: float) -> NDArray[np.floating]:
    """How steep the ground is, in degrees. The relief style's rock rule reads this."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    return np.degrees(np.arctan(np.hypot(d_east, d_south)))

"""The light both layers share: the north-west sun and its hillshade.

Why each constant has its value: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

import numpy as np

__all__ = [
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

#: The hillshade touches water only a little: it is computed from the bed.
WATER_SHADE_FLOOR = 0.75
WATER_SHADE_RANGE = 0.25


def hillshade(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """North-west relief in [SHADE_FLOOR, SHADE_FLOOR + SHADE_RANGE]; rows run south."""
    return sun_dot(z_m, spacing_m) * SHADE_RANGE + SHADE_FLOOR


def flat_shade(shape) -> np.ndarray:
    """What ``hillshade`` gives flat ground: the unlit colour's constant sun term."""
    flat = SHADE_FLOOR + SHADE_RANGE * np.sin(np.deg2rad(SUN_ALTITUDE_DEG))
    return np.full(shape, flat, np.float32)


def sun_dot(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """The surface normal against the north-west sun, clipped to [0, 1]."""
    azimuth = np.deg2rad(SUN_AZIMUTH_DEG)
    altitude = np.deg2rad(SUN_ALTITUDE_DEG)
    light = np.array(
        [
            np.cos(altitude) * np.sin(azimuth),  # east
            -np.cos(altitude) * np.cos(azimuth),  # south
            np.sin(altitude),  # up
        ],
        np.float32,
    )
    d_south, d_east = np.gradient(z_m, spacing_m)
    lit = (-d_east * light[0] - d_south * light[1] + light[2]) / np.sqrt(
        d_east * d_east + d_south * d_south + 1.0
    )
    return np.clip(lit, 0.0, 1.0)


def slope_degrees(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """How steep the ground is, in degrees. The satellite layer's rock rule reads this."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    return np.degrees(np.arctan(np.hypot(d_east, d_south)))

"""The light both layers share: the north-west sun, its hillshade, and the artwork borrow.

Why each constant has its value: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BORROW_CLAMP",
    "BORROW_DETAIL_SIGMAS",
    "BORROW_DETAIL_SIGMA_PX",
    "BORROW_DETAIL_SOFTEN_PX",
    "BORROW_FEATHER_M",
    "BORROW_GAIN",
    "BORROW_LUMA",
    "BORROW_PROVENANCE",
    "SHADE_FLOOR",
    "SHADE_RANGE",
    "SUN_ALTITUDE_DEG",
    "SUN_AZIMUTH_DEG",
    "WATER_SHADE_FLOOR",
    "WATER_SHADE_RANGE",
    "artwork_detail",
    "coarse_province",
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

#: The provinces coarser than the artwork: both cliff values, and fill. Not landscape.
BORROW_PROVENANCE = (*hf.PROV_CLIFF_VALUES, hf.PROV_FILL)

#: The borrow's fade across a province boundary, in metres.
BORROW_FEATHER_M = 6.0

#: High-pass sigma in artwork pixels: only detail finer than about 7 m comes across.
BORROW_DETAIL_SIGMA_PX = 8.0

#: Soften, then ``tanh`` at this many sigmas: borrow the shading, not the ink.
BORROW_DETAIL_SOFTEN_PX = 1.6
BORROW_DETAIL_SIGMAS = 1.2

#: How much reaches the picture, and how far it may push a pixel; picked by looking.
BORROW_GAIN = 0.30
BORROW_CLAMP = (0.74, 1.26)

#: Rec. 601 luma: the artwork's light crosses, its colour never does.
BORROW_LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def artwork_detail(sheet) -> tuple[np.ndarray, dict]:
    """The artwork's luminance high pass as int8 (67 MB, not 268), and its scaling."""
    rgb = np.asarray(sheet, np.float32)
    luma = rgb @ BORROW_LUMA
    high = luma - ndimage.gaussian_filter(luma, BORROW_DETAIL_SIGMA_PX, mode="nearest")
    high = ndimage.gaussian_filter(high, BORROW_DETAIL_SOFTEN_PX, mode="nearest")
    spread = float(high.std())
    detail = (np.tanh(high / max(spread * BORROW_DETAIL_SIGMAS, 1e-6)) * 127.0).astype(np.int8)
    return detail, {
        "role": (
            "the artwork sheet's own luminance minus its Gaussian blur, i.e. everything the "
            "drawn map says below about "
            f"{BORROW_DETAIL_SIGMA_PX * (BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / SHEET_PX:.1f}"
            " m and nothing above it"
        ),
        "sheet_px": SHEET_PX,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / SHEET_PX, 4),
        "high_pass_sigma_px": BORROW_DETAIL_SIGMA_PX,
        "soften_sigma_px": BORROW_DETAIL_SOFTEN_PX,
        "luma_weights": [float(value) for value in BORROW_LUMA],
        "measured_std": round(spread, 4),
        "tanh_knee_at_sigmas": BORROW_DETAIL_SIGMAS,
        "why_tanh": (
            "the artwork is a drawing and a drawing has strokes -- every rock formation is "
            "outlined in hard dark ink. A soft clip lets the mid-tones (the shading) through "
            "almost linearly and saturates the outliers (the ink), which is the difference "
            "between borrowing light and tracing lines"
        ),
        "stored_as": "int8, +-127 at full saturation",
    }


def coarse_province(field) -> tuple[np.ndarray, dict]:
    """Where the field is coarser than the artwork: a feathered uint8 mask at 1 m."""
    inside = np.isin(field._prov, BORROW_PROVENANCE)
    share = float(inside.mean())
    feather = ndimage.gaussian_filter(
        inside.astype(np.float32), BORROW_FEATHER_M * 100.0 / field.spacing_cm, mode="nearest"
    )
    return (np.clip(feather, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), {
        "provinces": [hf.PROV_NAMES[value] for value in BORROW_PROVENANCE],
        "share_of_the_field": round(100 * share, 2),
        "feather_m": BORROW_FEATHER_M,
        "role": (
            "1 where the field's own province is coarser than the artwork -- rasterised "
            "collision hulls, or a 3.9 m block raster -- 0 over the landscape layer, which "
            "is continuous geometry and keeps shading of its own, and a Gaussian ramp "
            "between them so the provenance byte is never itself drawn"
        ),
    }


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

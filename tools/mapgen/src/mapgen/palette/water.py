"""Inland water and the shallow coast drawn over the ground, shared by both drawn layers.

Why the feathers and the fallbacks are what they are: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "WATER_DEPTH_FULL_M",
    "WATER_EDGE_BLUR_M",
    "WATER_EDGE_M",
    "water_alpha",
    "water_depth_fraction",
    "water_over",
    "water_planes",
]

#: Water is tinted by depth, clipped here: past it more depth is not more colour.
WATER_DEPTH_FULL_M = 40.0

#: Shallower than this, water and ground are mixed, so a coast is not 1 m blocks.
WATER_EDGE_M = 0.9

#: The edge softened in space too, in metres of ground, for water against a cliff.
WATER_EDGE_BLUR_M = 0.73


def water_alpha(z_m, water_m, wet: np.ndarray, measured: np.ndarray, blur_px: float):
    """How much of each pixel is water, in [0, 1]: coverage, a depth feather, a space blur.

    ``wet`` is the share the channel calls water, ``measured`` the share with a measured
    depth; level-only texels get full alpha.
    """
    ramp_alpha = np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0)
    alpha = wet * (measured * ramp_alpha + (1.0 - measured))
    return ndimage.gaussian_filter(alpha, blur_px, mode="nearest")


def water_depth_fraction(z_m, water_m, measured: np.ndarray) -> np.ndarray:
    """How dark the water reads, in [0, 1]: measured depth where there is one, deep where not."""
    known = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)
    return measured * known + (1.0 - measured)


def water_over(rgb, depth, alpha, shade, shallow, deep):
    """Lay water over ground, tinted by its own depth and lit only a little."""
    tint = depth[..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def water_planes(field) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """The ``wet`` and ``measured`` 0/1 planes off ``waterq.u8.z``, and their source."""
    water = field._water_raster()
    if water is None:
        return None, None, "no water raster in this field; nothing is drawn as water"
    grades = field._water_quality_raster()
    if grades is None:
        wet = ((water != hf.NODATA) & (water > field._height_dm)).astype(np.uint8)
        return (
            wet,
            wet,
            (
                "no waterq.u8.z: this field predates the quality byte, so submersion falls "
                "back to a water surface standing above the ground, which is all such a "
                "field can say"
            ),
        )
    return (
        (grades != hf.WATER_DRY).astype(np.uint8),
        (grades == hf.WATER_MEASURED).astype(np.uint8),
        "waterq.u8.z: dry / depth measured against 1 m terrain / level known and depth not",
    )

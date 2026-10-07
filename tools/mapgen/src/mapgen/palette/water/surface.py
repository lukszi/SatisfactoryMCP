"""Inland water and the shallow coast drawn over the ground, shared by both drawn layers.

Why the feathers and the fallbacks are what they are: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

from typing import cast

import numpy as np
from scipy import ndimage

from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette.scene import FloatGrid, ReconciledWater, WaterPlanes, field_heights
from mapgen.palette.water.open_sea import Lattice, OpenSea, open_sea
from mapgen.palette.water.perched import WaterSurfaces, water_surfaces
from mapgen.palette.water.seams import feather_box_seams
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "WATER_DEPTH_FULL_M",
    "WATER_EDGE_BLUR_M",
    "WATER_EDGE_M",
    "drawn_water",
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


def drawn_water(
    field: hf.Field,
    kernel_only: bool,
    rivers: object,
    lattice: Lattice,
    artwork_water: U8Grid | None,
) -> tuple[WaterSurfaces, OpenSea | None, WaterPlanes | None]:
    """The run's water as every layer draws it: ``(surfaces, open sea or None, planes)``.

    ``palette.water.perched.water_surfaces``, then ``open_sea`` over the run's ``lattice``.
    ``--kernel-only`` (recipe 2) has neither, and keeps the page's sea past the data.
    ``rivers`` is the run's ``RiverWater`` or None. The level drawn, the open sea's or the
    surfaces', has its box seams feathered (``seams.feather_box_seams``); ``planes``, which
    the classes and the relief tint are read from, keep the levels as they were.
    """
    water = water_surfaces(field, kernel_only, cast(ReconciledWater | None, rivers))
    if kernel_only or water.level is None or water.grades is None:
        return water, None, water.planes
    if artwork_water is None:
        level, seams = feather_box_seams(water.level, water.grades)
        _announce_seams(seams)
        return water._replace(level=level), None, water.planes
    sea = open_sea(field, lattice, water.planes, artwork_water, OCEAN_LEVEL_M)
    meta = sea.meta
    print(
        f"  open sea: {meta['sea_over_no_data_texels']} texels of the artwork's sea over no "
        f"data, {meta['void_texels']} of void ({meta['pit_texels']} in pits); a bed under "
        f"{meta['bed_texels']} texels ({meta['fringe_texels']} under the void's fade, "
        f"{meta['toned_texels']} toned), {meta['blended_texels']} measured blended into it, "
        f"in {meta['seconds']}s"
    )
    level, seams = feather_box_seams(sea.level, sea.grades)
    _announce_seams(seams)
    meta["seam_texels_feathered"] = seams
    return water, sea._replace(level=level), sea.planes


def _announce_seams(texels: int) -> None:
    print(f"  box seams: {texels} texels of the drawn level feathered")


def water_alpha(
    z_m: FloatGrid, water_m: FloatGrid, wet: FloatGrid, measured: FloatGrid, blur_px: float
) -> FloatGrid:
    """How much of each pixel is water, in [0, 1]: coverage, a depth feather, a space blur.

    ``wet`` is the share the channel calls water, ``measured`` the share with a measured
    depth; level-only texels get full alpha.
    """
    ramp_alpha = np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0)
    alpha = wet * (measured * ramp_alpha + (1.0 - measured))
    return ndimage.gaussian_filter(alpha, blur_px, mode="nearest")


def water_depth_fraction(z_m: FloatGrid, water_m: FloatGrid, measured: FloatGrid) -> FloatGrid:
    """How dark the water reads, in [0, 1]: measured depth where there is one, deep where not."""
    known = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)
    return measured * known + (1.0 - measured)


def water_over(
    rgb: FloatGrid,
    depth: FloatGrid,
    alpha: FloatGrid,
    shade: FloatGrid,
    shallow: F32Grid,
    deep: F32Grid,
) -> FloatGrid:
    """Lay water over ground, tinted by its own depth and lit only a little."""
    tint = depth[..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def water_planes(field: hf.Field) -> tuple[U8Grid | None, U8Grid | None, str]:
    """The ``wet`` and ``measured`` 0/1 planes off ``waterq.u8.z``, and their source."""
    water = field.water_raster()
    if water is None:
        return None, None, "no water raster in this field; nothing is drawn as water"
    grades = field.water_quality_raster()
    if grades is None:
        wet = ((water != hf.NODATA) & (water > field_heights(field))).astype(np.uint8)
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

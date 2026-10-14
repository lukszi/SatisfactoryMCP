"""Inland water and the shallow coast drawn over the ground, shared by both drawn layers.

Why the feathers and the fallbacks are what they are: tools/mapgen/README.md, "Design notes".
"""

from __future__ import annotations

from typing import NamedTuple, cast

import numpy as np
from scipy import ndimage

from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette.scene import (
    FloatGrid,
    ReconciledWater,
    WaterPlanes,
    field_heights,
    field_water,
)
from mapgen.palette.water.lips import hold_at_lips
from mapgen.palette.water.open_sea import Lattice, OpenSea, open_sea
from mapgen.palette.water.perched import NoWaterPlanes, PerchedMeta, perched_levels, wet_holes
from mapgen.palette.water.seams import feather_box_seams
from mapgen.palette.water.shore import OCEAN_LEVEL_M, OCEAN_REACH_M, ocean_reach
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I16Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "WATER_DEPTH_FULL_M",
    "WATER_EDGE_BLUR_M",
    "WATER_EDGE_M",
    "WATER_REACH_M",
    "WATER_SOFT_LOW",
    "WATER_SOFT_SIGMA_M",
    "WATER_THICK",
    "WaterEdge",
    "WaterSurfaces",
    "drawn_water",
    "water_alpha",
    "water_depth_fraction",
    "water_edge",
    "water_over",
    "water_planes",
    "water_surfaces",
]

#: Water is tinted by depth, clipped here: past it more depth is not more colour.
WATER_DEPTH_FULL_M = 40.0

#: Shallower than this, water and ground are mixed, so a coast is not 1 m blocks.
WATER_EDGE_M = 0.9

#: The edge softened in space too, in metres of ground, for water against a cliff.
WATER_EDGE_BLUR_M = 0.73

#: Measured water carries its level this many metres past its mask, the artwork's 3.66 m
#: blocks, for the drawn ground to cut where it stands above it.
WATER_REACH_M = 3

#: The mask's outline is smoothed by a Gaussian of this sigma, in metres, and the water fades
#: out past it to where the smoothed mask reads ``WATER_SOFT_LOW``: 2.6 m past a straight edge.
WATER_SOFT_SIGMA_M = 1.5
WATER_SOFT_LOW = 0.04

#: A wet texel within 2 m of one whose smoothed mask reads this is in a body, and its cover
#: follows the smoothed outline; elsewhere it is a thin channel and keeps its full cover.
WATER_THICK = 0.9


class WaterEdge(NamedTuple):
    """Field water's edge on the 1 m grid: its level carried ``WATER_REACH_M`` past the mask
    (``hf.NODATA`` elsewhere), how much of each texel it may cover (0..255, the smoothed
    outline), and the mask and measured planes with the reach added."""

    level: I16Grid
    soft: U8Grid
    wet: U8Grid
    measured: U8Grid


def water_edge(
    level: I16Grid, wet: U8Grid, measured: U8Grid, keep_out: BoolMask | None = None
) -> WaterEdge:
    """Where field water may be drawn past its blocky mask (docs/map/water.md section 33,
    "Water edges"). Dry texels within ``WATER_REACH_M`` of measured water, outside
    ``keep_out`` (the river ribbons, which draw their own water there), take the highest
    level beside them and count as measured, so the drawn ground cuts the water's outline.
    """
    nodata = np.int16(hf.NODATA)
    free = wet == 0
    if keep_out is not None:
        free &= ~keep_out
    grown = np.where(measured != 0, level, nodata).astype(np.int16)
    for _ in range(WATER_REACH_M):
        grown = np.where(free & (grown == nodata), ndimage.maximum_filter(grown, 3), grown)
    reached = free & (grown != nodata)
    smooth = ndimage.gaussian_filter(wet.astype(np.float32), WATER_SOFT_SIGMA_M)
    t = np.clip((smooth - WATER_SOFT_LOW) / np.float32(0.5 - WATER_SOFT_LOW), 0.0, 1.0)
    outline = t * t * (3.0 - 2.0 * t)
    thick = ndimage.maximum_filter(smooth, 5) >= WATER_THICK
    soft = np.where(wet != 0, np.where(thick, outline, 1.0), np.where(reached, outline, 0.0))
    return WaterEdge(
        level=np.where(reached, grown, level).astype(np.int16),
        soft=np.round(soft * 255.0).astype(np.uint8),
        wet=((wet != 0) | reached).astype(np.uint8),
        measured=((measured != 0) | reached).astype(np.uint8),
    )


class WaterSurfaces(NamedTuple):
    """The water a render draws: the ocean reach, and the level and grade planes after the
    rivers took their boxes' water back, perched water was re-levelled and holes filled."""

    reach: U8Grid | None
    reach_meta: JsonObject
    level: I16Grid | None
    grades: U8Grid | None
    perched: PerchedMeta | NoWaterPlanes | None

    @property
    def planes(self) -> WaterPlanes | None:
        return None if self.level is None else (self.level, self.grades)


def water_surfaces(
    field: hf.Field,
    kernel_only: bool,
    rivers: ReconciledWater | None = None,
    lips: F64Grid | None = None,
) -> WaterSurfaces:
    """Recipe 6's ocean reach and the re-levelled water, both announced. ``rivers`` is the
    run's ``RiverWater``: its reconciled planes are re-levelled, so a spline's ribbon stands
    in for its box everywhere it speaks. ``lips`` (``lips.fall_lips``) hold up the water
    their falls pour from. ``--kernel-only`` draws neither."""
    if kernel_only:
        return WaterSurfaces(None, {}, None, None, None)
    reach, reach_meta = ocean_reach(field)
    print(
        f"  ocean shore at {OCEAN_LEVEL_M} m: {reach_meta.get('ocean_texels')} ocean "
        f"texels, {reach_meta.get('reach_texels')} within {OCEAN_REACH_M:g} m"
    )
    planes = None if rivers is None else (rivers.water_dm, rivers.grades)
    level, meta = perched_levels(field, planes)
    own = field.water_quality_raster()
    grades = own if rivers is None else rivers.grades
    if "bodies" in meta and level is not None and grades is not None:
        print(f"  perched water: {meta['bodies']} bodies, {meta['texels']} texels re-levelled")
        before = field_water(field)[0] if rivers is None else rivers.water_dm
        level, lips_meta = hold_at_lips(field, before, level, lips)
        meta["lips"] = lips_meta
        print(f"  held at a lip: {lips_meta['lips']} falls, {lips_meta['texels']} texels raised")
        dropped = None if rivers is None else (own != hf.WATER_DRY) & (grades == hf.WATER_DRY)
        level, grades, holes = wet_holes(field, before, level, grades, dropped)
        meta["holes"] = holes
        print(f"  holes in lakes: {holes['bodies']} bodies, {holes['texels']} texels re-wetted")
    return WaterSurfaces(reach, reach_meta, level, grades, meta)


def drawn_water(
    field: hf.Field,
    kernel_only: bool,
    rivers: object,
    lattice: Lattice,
    artwork_water: U8Grid | None,
    lips: F64Grid | None = None,
) -> tuple[WaterSurfaces, OpenSea | None, WaterPlanes | None]:
    """The run's water as every layer draws it: ``(surfaces, open sea or None, planes)``.

    ``water_surfaces``, then ``open_sea`` over the run's ``lattice``; ``lips`` as there.
    ``--kernel-only`` (recipe 2) has neither, and keeps the page's sea past the data.
    ``rivers`` is the run's ``RiverWater`` or None. The level drawn, the open sea's or the
    surfaces', has its box seams feathered (``seams.feather_box_seams``); ``planes``, which
    the classes and the relief tint are read from, keep the levels as they were.
    """
    water = water_surfaces(field, kernel_only, cast(ReconciledWater | None, rivers), lips)
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

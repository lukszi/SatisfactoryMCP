"""Recipe 6's two additions to the renders: the ocean's crisp shore and the render-only meshes.

The shore: near the sea, water coverage comes from the drawn surface crossing the ocean level,
antialiased to one pixel, instead of from the 3.66 m artwork mask. Rivers and lakes keep
recipe 5's rule. The meshes: coral, shells, CliffPillar_03 and rubble, which the artwork
draws as land and the heightfield leaves out, rasterised for the map only. docs/
map/painted.md section 27 has the measurements behind every constant here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple, TypedDict

import numpy as np
from scipy import ndimage

from mapgen.jit import kernels_on
from mapgen.palette.scene import FloatGrid, UnderwaterWater, WaterTerms
from mapgen.palette.schema import (
    FoamStyle,
    InlandShoreStyle,
    RiverShoreStyle,
    ShoreOptics,
    WetBandStyle,
)
from mapgen.palette.water.wet import cover_mix, float32_planes
from mapgen.terrain.render_meshes import MESH_ROCK
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "MESH_FULL_LIFT_M",
    "MESH_REACH_M",
    "OCEAN_LEVEL_BAND_M",
    "OCEAN_LEVEL_M",
    "OCEAN_REACH_M",
    "WET_MIX_MOST",
    "ShoreTerms",
    "add_foam",
    "blend_water",
    "blend_where",
    "composite_meshes",
    "inland_cover",
    "ocean_reach",
    "optical_depth",
    "seabed_keeps",
    "shore_terms",
    "water_composite",
    "wet_band",
    "wet_mix",
]

#: The sea surface the coast is drawn at, metres. The one number to change if the sandbars
#: turn out dry in game (-17.4 is what the artwork, the paint and the foliage agree on).
OCEAN_LEVEL_M = -17.0

#: Measured water counts as ocean when its level is this close to ``OCEAN_LEVEL_M``.
OCEAN_LEVEL_BAND_M = 0.5

#: How far past measured ocean water the crossing rule reaches, metres.
OCEAN_REACH_M = 48.0

#: A render-only mesh is drawn only where its top stands within this of the water surface or
#: above it, so seabed coral roots do not speckle the sea.
MESH_REACH_M = 0.6

#: The lift over which a pixel counts as wholly covered by a render-only mesh, metres.
MESH_FULL_LIFT_M = 0.25

#: Past this share of wet pixels in a band, ``wet_mix`` mixes them all: that is cheaper.
WET_MIX_MOST = 1 / 3


class ShoreTerms(TypedDict):
    """A water level crossing each pixel: its cover, depth and edge, and the distances across
    the ground to the waterline from above and below it."""

    cover: FloatGrid
    depth_m: FloatGrid
    edge: FloatGrid
    above_m: FloatGrid
    below_m: FloatGrid


#: A blend worked per pixel: the base, then planes with the same trailing channel axis.
PixelBlend = Callable[..., FloatGrid]

#: The renderer's raise-only lift of the ground by a mesh plane (``composite_top``).
MeshLift = Callable[[FloatGrid, FloatGrid, U8Grid], FloatGrid]


# ----------------------------------------------------------------------- the shore


def ocean_reach(field: hf.Field) -> tuple[U8Grid, JsonObject]:
    """1 where the crossing rule applies: within the reach of measured ocean water."""
    water = field.water_raster()
    grades = field.water_quality_raster()
    if water is None or grades is None:
        return np.zeros((field.height, field.width), np.uint8), {"absent": "no water planes"}
    level_m = water.astype(np.float32) / np.float32(hf.DM_PER_M)
    ocean = (
        (grades == hf.WATER_MEASURED)
        & (water != hf.NODATA)
        & (np.abs(level_m - OCEAN_LEVEL_M) <= OCEAN_LEVEL_BAND_M)
    )
    distance = ndimage.distance_transform_edt(~ocean) * (field.spacing_cm / 100.0)
    # Level-only water stands over the fill, whose raster holds the surface, not a bed.
    reach = ((distance <= OCEAN_REACH_M) & (grades != hf.WATER_LEVEL_ONLY)).astype(np.uint8)
    return reach, {
        "ocean_texels": int(ocean.sum()),
        "reach_texels": int(reach.sum()),
        "level_m": OCEAN_LEVEL_M,
        "level_band_m": OCEAN_LEVEL_BAND_M,
        "reach_m": OCEAN_REACH_M,
    }


def shore_terms(
    z_m: FloatGrid, spacing_m: float, level_m: FloatGrid | float = OCEAN_LEVEL_M
) -> ShoreTerms:
    """The crossing of a water level (the ocean's, or a river's per pixel) through each pixel:
    coverage, depth, the edge, and how far above the waterline a dry pixel is, in metres
    across the ground."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    grade = np.maximum(np.hypot(d_east, d_south), np.float32(1e-3))
    per_px = np.maximum(grade * spacing_m, np.float32(1e-3))
    depth = (np.asarray(level_m, np.float32) - z_m).astype(np.float32)
    return {
        "cover": np.clip(depth / per_px + 0.5, 0.0, 1.0),
        "depth_m": np.maximum(depth, 0.0),
        "edge": np.clip(1.0 - np.abs(depth) / per_px, 0.0, 1.0),
        "above_m": np.maximum(-depth, 0.0) / grade,
        "below_m": np.maximum(depth, 0.0) / grade,
    }


def blend_water(
    reach: FloatGrid | None,
    old_cover: FloatGrid,
    old_depth_fraction: FloatGrid,
    shore: ShoreTerms | None,
    full_m: float,
) -> WaterTerms:
    """Recipe 6's water where ``reach`` is 1 and recipe 5's where it is 0, blended between.

    Returns ``cover`` (water share of the pixel), ``depth`` (the tint fraction),
    ``depth_m`` (metres, for the optics), ``ocean`` (the reach), ``banks`` (where the shore
    optics apply: the ocean's reach, and the rivers once laid over) and ``edge`` (where the
    crossing passes through the pixel). ``river`` and ``river_below_m`` stay empty here.
    """
    zero = np.zeros_like(old_cover)
    no_river = np.full_like(old_cover, np.inf)
    if shore is None or reach is None:
        return {
            "river": zero,
            "river_below_m": no_river,
            "banks": zero,
            "cover": old_cover,
            "depth": old_depth_fraction,
            "ocean": zero,
            "edge": zero,
            "above_m": np.full_like(old_cover, np.inf),
            "below_m": np.full_like(old_cover, np.inf),
            "depth_m": old_depth_fraction * np.float32(full_m),
        }
    keep = 1.0 - reach
    return {
        "river": zero,
        "river_below_m": no_river,
        "banks": reach,
        "cover": reach * shore["cover"] + keep * old_cover,
        "depth": reach * np.clip(shore["depth_m"] / full_m, 0.0, 1.0) + keep * old_depth_fraction,
        "depth_m": reach * shore["depth_m"] + keep * old_depth_fraction * np.float32(full_m),
        "ocean": reach,
        "edge": reach * shore["edge"],
        "above_m": np.where(reach > 0, shore["above_m"], np.inf),
        "below_m": np.where(reach > 0, shore["below_m"], np.inf),
    }


def water_composite(
    land: FloatGrid,
    water: WaterTerms,
    shade: FloatGrid,
    optics: ShoreOptics,
    shallow: F32Grid,
    deep: F32Grid,
    shade_floor: float,
    shade_range: float,
) -> FloatGrid:
    """Ground and water in one pass, sRGB 0..255. Outside the ocean reach this is recipe 5.

    Over the sea the water's opacity rises from ``edge_alpha`` at the line to one with an
    exponential depth fade of e-folding ``clarity_m``, over ground darkened by ``wet_darken``.
    Optional, per style: ``wet_band`` darkens the sand just above the line and ``foam`` lays a
    faint line over the shallowest water.
    """
    if kernels_on():
        colours = _WaterColours(shallow, deep, shade, shade_floor, shade_range)
        compiled = _composite_compiled(land, water, colours, optics)
        if compiled is not None:
            return compiled
    land = wet_band(land, water, optics.get("wet_band"))
    banks = water.get("banks", water["ocean"])
    depth = optical_depth(water, optics.get("river"), optics.get("inland"))
    fade = 1.0 - np.exp(-depth / np.float32(optics["clarity_m"]))
    edge_alpha = np.float32(optics["edge_alpha"])
    opacity = (banks * (edge_alpha + (1.0 - edge_alpha) * fade) + (1.0 - banks))[..., None]
    wet = (1.0 - (1.0 - np.float32(optics["wet_darken"])) * banks)[..., None]
    tint = water["depth"][..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (shade_floor + shade_range * shade[..., None])
    under = land * wet * (1.0 - opacity) + colour * opacity
    rgb = wet_mix(land, under, water["cover"][..., None])
    stroke = np.float32(optics.get("stroke", 0.0))
    if stroke:
        rgb = rgb * (1.0 - stroke * water["edge"][..., None])
    return add_foam(rgb, water, optics.get("foam"), np.float32(255.0))


class _WaterColours(NamedTuple):
    """The water's shallow and deep colours, and the hillshade they are lit by with its
    floor and range."""

    shallow: F32Grid
    deep: F32Grid
    shade: FloatGrid
    shade_floor: float
    shade_range: float


def _composite_compiled(
    land: F32Grid, water: WaterTerms, colours: _WaterColours, optics: ShoreOptics
) -> F32Grid | None:
    """``water_composite`` by the kernel; the fade's ``exp`` is worked out here, by numpy.
    It reads the planes the style reads, no others. None, for the numpy painter, where one of
    them is not float32."""
    from mapgen.palette.water import kernels

    band, foam = optics.get("wet_band") or {}, optics.get("foam") or {}
    band_m, strength = band.get("m") or 0.0, foam.get("strength") or 0.0
    stroke = optics.get("stroke", 0.0)
    # The cover stands in for a plane the style does not read, which the kernel leaves unread.
    cover = water["cover"]
    planes = kernels.CompositePlanes(
        cover=cover,
        depth=water["depth"],
        banks=water.get("banks", water["ocean"]),
        shade=colours.shade,
        above_m=water["above_m"] if band_m else cover,
        edge=water["edge"] if stroke else cover,
        depth_m=water["depth_m"],
        below_m=water["below_m"] if strength else cover,
        ocean=water["ocean"],
    )
    depth = optical_depth(water, optics.get("river"), optics.get("inland"))
    transmit = np.exp(-depth / np.float32(optics["clarity_m"]))
    if not float32_planes(land, colours.shallow, colours.deep, *planes, transmit):
        return None
    tint = np.asarray(band["tint"] if band_m else (1.0, 1.0, 1.0), np.float32)
    froth = (foam["max_depth_m"], foam["width_m"]) if strength else (1.0, 1.0)
    white = np.float32(255.0) * np.float32(foam.get("white", 1.0))
    knobs = kernels.CompositeKnobs(
        band_m=np.float32(band_m),
        edge_alpha=np.float32(optics["edge_alpha"]),
        wet_darken=np.float32(optics["wet_darken"]),
        shade_floor=np.float32(colours.shade_floor),
        shade_range=np.float32(colours.shade_range),
        stroke=np.float32(stroke),
        foam=np.float32(strength),
        foam_depth_m=np.float32(froth[0]),
        foam_width_m=np.float32(froth[1]),
        foam_white=white,
    )
    style = kernels.CompositeStyle(colours.shallow, colours.deep, tint, knobs)
    return kernels.water_composite(land, planes, transmit, WET_MIX_MOST, style)


def blend_where(
    touched: BoolMask, most: float, blend: PixelBlend, base: FloatGrid, *planes: FloatGrid
) -> FloatGrid:
    """``blend(base, *planes)``, worked only on the ``touched`` pixels, where it may differ
    from ``base``, unless they are more than ``most`` of them. Every array has a trailing
    channel axis; docs/map/renders.md section 26, "Drawing less"."""
    picked = np.flatnonzero(touched)
    if picked.size > most * touched.size:
        return blend(base, *planes)
    rows = [
        np.take(plane.reshape(-1, plane.shape[-1]), picked, axis=0) for plane in (base, *planes)
    ]
    done = blend(*rows)
    out = base.astype(done.dtype, order="C")
    out.reshape(-1, done.shape[-1])[picked] = done
    return out


def wet_mix(land: FloatGrid, under: FloatGrid, cover: FloatGrid) -> FloatGrid:
    """``cover_mix(land, under, cover)``; ``cover`` has the trailing channel axis."""
    return blend_where(cover[..., 0] != 0, WET_MIX_MOST, cover_mix, land, under, cover)


def wet_band(land: FloatGrid, water: WaterTerms, band: WetBandStyle | None) -> FloatGrid:
    """Ground within ``band["m"]`` of the waterline, multiplied towards ``band["tint"]``."""
    if not band or not band.get("m"):
        return land
    reach = np.clip(1.0 - water["above_m"] / np.float32(band["m"]), 0.0, 1.0)
    weight = (reach * reach * water.get("banks", water["ocean"]))[..., None]
    return land * (1.0 - weight + weight * np.asarray(band["tint"], np.float32))


def optical_depth(
    water: UnderwaterWater, river: RiverShoreStyle | None, inland: InlandShoreStyle | None = None
) -> FloatGrid:
    """The depth the optics see: a river reads at least ``min_depth_m`` deep once ``bank_m``
    in from its waterline, so a shallow bed does not draw it as a pale path; inland field
    water reads at least ``inland``'s ``min_depth_m`` deep, for the same reason."""
    depth = water["depth_m"]
    if river and river.get("min_depth_m") and "river" in water:
        ramp = np.clip(water["river_below_m"] / np.float32(river["bank_m"]), 0.0, 1.0)
        floor = water["river"] * np.float32(river["min_depth_m"]) * ramp
        depth = np.maximum(depth, floor)
    if inland and inland.get("min_depth_m"):
        floor = _inland_share(water) * np.float32(inland["min_depth_m"])
        depth = np.maximum(depth, floor)
    return depth


def inland_cover(water: WaterTerms, inland: InlandShoreStyle | None) -> WaterTerms:
    """``water`` with inland field water fully covering its pixel once ``inland``'s ``edge_m``
    deep, where the depth feather (``surface.WATER_EDGE_M``) takes 0.9 m: a pool a few
    decimetres deep is drawn as water, not as its bed. The ocean's reach and the rivers keep
    their own cover."""
    if not inland or not inland.get("edge_m") or "wet" not in water:
        return water
    ramp = np.clip(water["depth_m"] / np.float32(inland["edge_m"]), 0.0, 1.0)
    floor = _inland_share(water) * water["wet"] * ramp
    return {**water, "cover": np.maximum(water["cover"], floor)}


def _inland_share(water: UnderwaterWater) -> FloatGrid:
    """The share of each pixel's water that is neither the ocean's reach nor a river's."""
    river = water["river"] if "river" in water else np.float32(0.0)
    return (1.0 - water["ocean"]) * (1.0 - river)


def add_foam(
    rgb: FloatGrid, water: WaterTerms, foam: FoamStyle | None, white: np.float32
) -> FloatGrid:
    """A faint line along the waterline, towards ``white``: water shallower than
    ``max_depth_m`` and within ``width_m`` of the line across the ground."""
    if not foam or not foam.get("strength"):
        return rgb
    shallow = np.clip(1.0 - water["depth_m"] / np.float32(foam["max_depth_m"]), 0.0, 1.0)
    shallow = shallow * np.clip(1.0 - water["below_m"] / np.float32(foam["width_m"]), 0.0, 1.0)
    weight = (np.float32(foam["strength"]) * shallow * water["cover"] * water["ocean"])[..., None]
    return rgb * (1.0 - weight) + white * np.float32(foam.get("white", 1.0)) * weight


def seabed_keeps(mesh_class_band: U8Grid, top_m: FloatGrid, water_level_m: FloatGrid) -> BoolMask:
    """Where a style that draws ground and water only keeps a mesh: on dry land, or a rock
    whose top stands above the surface."""
    dry = ~np.isfinite(water_level_m)
    level = np.where(dry, -np.inf, water_level_m)
    return dry | ((mesh_class_band == MESH_ROCK) & (top_m > level))


def composite_meshes(
    z_m: FloatGrid,
    mesh_z_cm: FloatGrid,
    mesh_class_band: U8Grid,
    water_level_m: FloatGrid,
    composite: MeshLift,
    seabed: bool = False,
) -> tuple[FloatGrid, FloatGrid, U8Grid]:
    """``z_m`` raised by the meshes standing near or above the water; and their weight.

    ``composite`` is the renderer's raise-only lift (``composite_top``). The weight is how
    much of the drawn surface is the mesh: 0 where it did not raise the ground. With
    ``seabed`` (the styles that draw ground and water only) nothing breaks the water's
    surface: under water the coral, shells and terraces are left to the seabed, and a rock
    stays only where its top stands above the surface.
    """
    level = np.where(np.isfinite(water_level_m), water_level_m, -np.inf)
    top_m = mesh_z_cm / np.float32(100.0)
    keep = (mesh_class_band > 0) & (top_m > level - MESH_REACH_M)
    if seabed:
        keep &= seabed_keeps(mesh_class_band, top_m, water_level_m)
    raised = composite(z_m, mesh_z_cm, keep.astype(np.uint8))
    weight = np.clip((raised - z_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    return raised, weight, np.where(keep, mesh_class_band, 0).astype(np.uint8)

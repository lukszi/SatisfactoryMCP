"""Recipe 6's two additions to the renders: the ocean's crisp shore and the render-only meshes.

The shore: near the sea, water coverage comes from the drawn surface crossing the ocean level,
antialiased to one pixel, instead of from the 3.66 m artwork mask. Rivers and lakes keep
recipe 5's rule. The meshes: coral, shells, CliffPillar_03 and rubble, which the artwork
draws as land and the heightfield leaves out, rasterised for the map only. docs/
spatial-and-map.md section 27 has the measurements behind every constant here.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "MESH_FULL_LIFT_M",
    "MESH_REACH_M",
    "OCEAN_LEVEL_BAND_M",
    "OCEAN_LEVEL_M",
    "OCEAN_REACH_M",
    "add_foam",
    "blend_water",
    "composite_meshes",
    "ocean_reach",
    "shore_terms",
    "water_composite",
    "wet_band",
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


# ----------------------------------------------------------------------- the shore


def ocean_reach(field) -> tuple[np.ndarray, dict]:
    """1 where the crossing rule applies: within the reach of measured ocean water."""
    water = field._water_raster()
    grades = field._water_quality_raster()
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


def shore_terms(z_m: np.ndarray, spacing_m: float) -> dict:
    """The crossing of the ocean level through each pixel: coverage, depth, the edge, and how
    far above the waterline a dry pixel is, in metres across the ground."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    grade = np.maximum(np.hypot(d_east, d_south), np.float32(1e-3))
    per_px = np.maximum(grade * spacing_m, np.float32(1e-3))
    depth = np.float32(OCEAN_LEVEL_M) - z_m
    return {
        "cover": np.clip(depth / per_px + 0.5, 0.0, 1.0),
        "depth_m": np.maximum(depth, 0.0),
        "edge": np.clip(1.0 - np.abs(depth) / per_px, 0.0, 1.0),
        "above_m": np.maximum(-depth, 0.0) / grade,
        "below_m": np.maximum(depth, 0.0) / grade,
    }


def blend_water(reach, old_cover, old_depth_fraction, shore: dict | None, full_m: float) -> dict:
    """Recipe 6's water where ``reach`` is 1 and recipe 5's where it is 0, blended between.

    Returns ``cover`` (water share of the pixel), ``depth`` (the tint fraction),
    ``depth_m`` (metres, for the optics), ``ocean`` (the reach) and ``edge`` (where the
    crossing passes through the pixel).
    """
    if shore is None:
        zero = np.zeros_like(old_cover)
        return {
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
        "cover": reach * shore["cover"] + keep * old_cover,
        "depth": reach * np.clip(shore["depth_m"] / full_m, 0.0, 1.0) + keep * old_depth_fraction,
        "depth_m": reach * shore["depth_m"] + keep * old_depth_fraction * np.float32(full_m),
        "ocean": reach,
        "edge": reach * shore["edge"],
        "above_m": np.where(reach > 0, shore["above_m"], np.inf),
        "below_m": np.where(reach > 0, shore["below_m"], np.inf),
    }


def water_composite(
    land, water: dict, shade, optics: dict, shallow, deep, shade_floor, shade_range
):
    """Ground and water in one pass, sRGB 0..255. Outside the ocean reach this is recipe 5.

    Over the sea the water's opacity rises from ``edge_alpha`` at the line to one with an
    exponential depth fade of e-folding ``clarity_m``, over ground darkened by ``wet_darken``.
    Optional, per style: ``wet_band`` darkens the sand just above the line and ``foam`` lays a
    faint line over the shallowest water.
    """
    land = wet_band(land, water, optics.get("wet_band"))
    ocean = water["ocean"]
    fade = 1.0 - np.exp(-water["depth_m"] / np.float32(optics["clarity_m"]))
    a0 = np.float32(optics["edge_alpha"])
    opacity = (ocean * (a0 + (1.0 - a0) * fade) + (1.0 - ocean))[..., None]
    wet = (1.0 - (1.0 - np.float32(optics["wet_darken"])) * ocean)[..., None]
    tint = water["depth"][..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (shade_floor + shade_range * shade[..., None])
    under = land * wet * (1.0 - opacity) + colour * opacity
    cover = water["cover"][..., None]
    rgb = land * (1.0 - cover) + under * cover
    stroke = np.float32(optics.get("stroke", 0.0))
    if stroke:
        rgb = rgb * (1.0 - stroke * water["edge"][..., None])
    return add_foam(rgb, water, optics.get("foam"), np.float32(255.0))


def wet_band(land, water: dict, band: dict | None):
    """Ground within ``band["m"]`` of the waterline, multiplied towards ``band["tint"]``."""
    if not band or not band.get("m"):
        return land
    reach = np.clip(1.0 - water["above_m"] / np.float32(band["m"]), 0.0, 1.0)
    weight = (reach * reach * water["ocean"])[..., None]
    return land * (1.0 - weight + weight * np.asarray(band["tint"], np.float32))


def add_foam(rgb, water: dict, foam: dict | None, white):
    """A faint line along the waterline, towards ``white``: water shallower than
    ``max_depth_m`` and within ``width_m`` of the line across the ground."""
    if not foam or not foam.get("strength"):
        return rgb
    shallow = np.clip(1.0 - water["depth_m"] / np.float32(foam["max_depth_m"]), 0.0, 1.0)
    shallow = shallow * np.clip(1.0 - water["below_m"] / np.float32(foam["width_m"]), 0.0, 1.0)
    weight = (np.float32(foam["strength"]) * shallow * water["cover"] * water["ocean"])[..., None]
    return rgb * (1.0 - weight) + white * np.float32(foam.get("white", 1.0)) * weight


def composite_meshes(z_m, mesh_z_cm, mesh_class_band, water_level_m, composite):
    """``z_m`` raised by the meshes standing near or above the water; and their weight.

    ``composite`` is the renderer's raise-only lift (``composite_top``). The weight is how
    much of the drawn surface is the mesh: 0 where it did not raise the ground.
    """
    level = np.where(np.isfinite(water_level_m), water_level_m, -np.inf)
    keep = (mesh_class_band > 0) & (mesh_z_cm / np.float32(100.0) > level - MESH_REACH_M)
    raised = composite(z_m, mesh_z_cm, keep.astype(np.uint8))
    weight = np.clip((raised - z_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    return raised, weight, np.where(keep, mesh_class_band, 0).astype(np.uint8)

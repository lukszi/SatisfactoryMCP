"""What stands on the painted ground: rock in its family's colour, the canopy over rock, and
the render-only meshes. docs/spatial-and-map.md sections 27, 30 and 31.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.rockfamily import FAMILIES
from mapgen.palette.calibration import display_to_ground, sampled_rgb
from mapgen.palette.colour import linear_from_oklab
from mapgen.terrain.rasters import MESH_CORAL
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SPECK_WATER",
    "canopy_over_rock",
    "family_cells",
    "family_tables",
    "family_targets",
    "mesh_surface",
    "rock_surface",
    "sunk_specks",
]

#: A coral pixel is narrower than the pixel when at least this share of its eight neighbours
#: is water: a coral head standing in the sea, which the max-Z raster widens to a pixel.
SPECK_WATER = 0.6


def family_tables(families: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """By family code: the tint relative to the families' median, the top layer, and whether
    there is one.

    The rock targets are calibrated on rock that already wears the common tint, so only a
    family's departure from it is applied; with one tint for all, rock stays on target.
    """
    n = len(FAMILIES)
    tint = np.ones((n, 3), np.float32)
    top = np.zeros((n, 3), np.float32)
    has_top = np.zeros(n, np.float32)
    tinted = []
    for name, entry in families.items():
        if name not in FAMILIES:
            continue
        code = FAMILIES.index(name)
        if entry.get("tint"):
            tint[code] = entry["tint"]
            tinted.append(code)
        if entry.get("top"):
            top[code] = entry["top"]
            has_top[code] = 1.0
    if tinted:
        tint[tinted] /= np.maximum(np.median(tint[tinted], axis=0), np.float32(1e-6))
    return tint, top, has_top


def family_cells(plane, shape: tuple[int, int], step_m: float) -> np.ndarray:
    """The family plane's code at each point of a ``step_m`` grid from the frame's corner: the
    code of the pixel the point falls in."""
    span_m = BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]
    picks = [
        np.clip(np.floor(np.arange(n) * step_m * size / span_m), 0, size - 1).astype(int)
        for n, size in zip(shape, plane.shape, strict=True)
    ]
    return np.asarray(plane[picks[0]][:, picks[1]])


def family_targets(base_lab, codes, targets: dict, palette: dict, min_cells: int):
    """Per family with a target, its rock on the rock grid, and the step measured for it.

    ``base_lab`` is the rock grid in OKLab before any target, ``codes`` the family under each
    cell. As for an area target, chroma and hue become the target's and the lightness moves
    by the step from the median of the family's own cells to the target's.
    """
    planes, measured = {}, {}
    for name, hex_colour in targets.items():
        own = codes == FAMILIES.index(name)
        if own.sum() < min_cells:
            continue
        target = display_to_ground(palette, hex_colour)
        lab = base_lab.copy()
        step = float(target[0] - np.median(base_lab[own][:, 0]))
        lab[..., 0] += np.float32(step)
        lab[..., 1:] = target[1:]
        rgb = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        planes[FAMILIES.index(name)] = [rgb[..., k].astype(np.float32) for k in range(3)]
        measured[name] = {"cells": int(own.sum()), "dL": round(step, 4)}
    return planes, measured


def rock_surface(rock_rgb, scene: dict, ground, sample_rock=None) -> np.ndarray:
    """Rock in its family's colour: the family's own target where it has one, else the area's
    rock in the family's tint, with the family's top layer on its up-facing faces."""
    if ground.rock_family is None:
        return rock_rgb
    band, *_sheet, spacing_m = scene["grid"]
    code = np.asarray(ground.rock_family[band])
    for which, planes in getattr(ground, "family_rock", {}).items():
        hit = (code == which)[..., None]
        if hit.any():
            rock_rgb = np.where(hit, sampled_rgb(planes, sample_rock), rock_rgb)
    rgb = rock_rgb * ground.family_tint[code]
    d_south, d_east = np.gradient(scene["z_m"], spacing_m)
    nz = 1.0 / np.sqrt(1.0 + d_east * d_east + d_south * d_south)
    lo, hi = ground.palette["rock_top"]["up"]
    up = ndimage.uniform_filter(np.clip((nz - lo) / (hi - lo), 0.0, 1.0), 3)
    weight = (up * ground.family_has_top[code])[..., None]
    return rgb * (1.0 - weight) + ground.family_top[code] * weight


def canopy_over_rock(g, canopy, rock, scene: dict, ground, sample, rgb=None):
    """The canopy laid over rock wherever the drawn surface is no higher than a crown top.

    ``rgb`` is the canopy colour already sampled onto the band; the ground's constant if None.
    """
    if ground.crown is None:
        return g
    crown_m = sample(ground.crown) / np.float32(hf.DM_PER_M)
    seen = (scene["z_m"] <= crown_m)[..., None]
    cover = canopy * rock * seen
    return g * (1.0 - cover) + (ground.canopy_rgb if rgb is None else rgb) * cover


def sunk_specks(scene: dict) -> dict:
    """The band's water with each coral speck standing in it drawn as that water.

    A speck is a coral pixel whose neighbours are mostly water: it takes their mean depth and
    full water cover, so the coral is seen as the bed under the water around it, as the
    seabed carpet is. Larger coral, and coral on land, keeps its own surface.
    """
    water = scene["water"]
    mesh_w = scene.get("mesh_weight")
    if mesh_w is None:
        return water
    coral = (scene["mesh_class"] == MESH_CORAL) & (mesh_w > 0)
    if not coral.any():
        return water
    cover = water["cover"]
    around = (ndimage.uniform_filter(cover, 3, mode="nearest") * 9 - cover) / 8
    speck = coral & (around >= SPECK_WATER)
    if not speck.any():
        return water
    wet_depth = cover * water["depth_m"]
    deep = (ndimage.uniform_filter(wet_depth, 3, mode="nearest") * 9 - wet_depth) / 8
    return {
        **water,
        "cover": np.where(speck, np.float32(1.0), cover),
        "depth_m": np.where(speck, deep / np.maximum(around, 1e-6), water["depth_m"]),
    }


def mesh_surface(g, area_rock, scene: dict, ground, sample_rock):
    """The render-only meshes over ``g``, each class in its own colour.

    Rocks take the area's rock, never the family of a cliff they happen to overlap. Coral
    under water is the seabed coral; ``scene["water"]`` is the water after ``sunk_specks``.
    """
    mesh_w = scene.get("mesh_weight")
    if mesh_w is None or not mesh_w.any():
        return g
    cls = scene["mesh_class"]
    colour = area_rock
    for which, rgb in ground.mesh_rgb.items():
        colour = np.where((cls == which)[..., None], sampled_rgb(rgb, sample_rock), colour)
    wet = np.where(cls == MESH_CORAL, np.clip(scene["water"]["cover"], 0.0, 1.0), 0.0)
    colour = colour + (ground.seabed_coral - colour) * wet[..., None]
    return g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]

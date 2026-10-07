"""What stands on the painted ground: rock in its family's colour, the canopy over rock, and
the render-only meshes. docs/spatial-and-map.md sections 27, 30 and 31.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import numpy.typing as npt
from scipy import ndimage

from mapgen.cache import Plane
from mapgen.colour import linear_from_oklab
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.palette.painted.calibration import display_to_ground, sampled_rgb
from mapgen.palette.painted.shapes import (
    BandWater,
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    RockFamilyEntry,
    Sampler,
)
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK
from mapgen.terrain.sample import patch_noise
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SPECK_WATER",
    "canopy_over_rock",
    "family_cells",
    "family_code",
    "family_tables",
    "family_targets",
    "mesh_surface",
    "rock_surface",
    "sunk_specks",
    "top_cover",
    "top_targets",
]

#: A coral pixel is narrower than the pixel when at least this share of its eight neighbours
#: is water: a coral head standing in the sea, which the max-Z raster widens to a pixel.
SPECK_WATER = 0.6


def family_code(name: str, block: str) -> int:
    """The code of the rock family a palette target names; a name that is none is refused."""
    if name not in FAMILIES:
        raise ValueError(f"calibration.{block} names {name!r}, which is not a rock family")
    return FAMILIES.index(name)


def family_tables(
    families: Mapping[str, RockFamilyEntry], palette: PaintedPalette | None = None
) -> tuple[FloatGrid, FloatGrid, FloatGrid]:
    """By family code: the tint relative to the families' median, the top layer, and whether
    there is one. With ``palette``, its ``calibration.tops`` replace their families' tops
    (``top_targets``).

    The rock targets are calibrated on rock that already wears the common tint, so only a
    family's departure from it is applied; with one tint for all, rock stays on target.
    """
    n = len(FAMILIES)
    tint = np.ones((n, 3), np.float32)
    top = np.zeros((n, 3), np.float32)
    has_top = np.zeros(n, np.float32)
    tinted: list[int] = []
    for name, entry in families.items():
        if name not in FAMILIES:
            continue
        code = FAMILIES.index(name)
        own_tint, own_top = entry.get("tint"), entry.get("top")
        if own_tint:
            tint[code] = own_tint
            tinted.append(code)
        if own_top:
            top[code] = own_top
            has_top[code] = 1.0
    if tinted:
        tint[tinted] /= np.maximum(np.median(tint[tinted], axis=0), np.float32(1e-6))
    if palette is not None:
        top = top_targets(top, palette["calibration"].get("tops", {}), palette)
    return tint, top, has_top


def family_cells(plane: Plane, shape: tuple[int, int], step_m: float) -> U8Grid:
    """The family plane's code at each point of a ``step_m`` grid from the frame's corner: the
    code of the pixel the point falls in."""
    span_m = BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]
    picks = [
        np.clip(np.floor(np.arange(n) * step_m * size / span_m), 0, size - 1).astype(int)
        for n, size in zip(shape, plane.shape, strict=True)
    ]
    return np.asarray(plane[picks[0]][:, picks[1]])


def family_targets(
    base_lab: FloatGrid,
    codes: U8Grid,
    targets: Mapping[str, str],
    palette: PaintedPalette,
    min_cells: int,
) -> tuple[dict[int, list[FloatGrid]], JsonObject]:
    """Per family with a target, its rock on the rock grid, and the step measured for it.

    ``base_lab`` is the rock grid in OKLab before any target, ``codes`` the family under each
    cell. As for an area target, chroma and hue become the target's and the lightness moves
    by the step from the median of the family's own cells to the target's.
    """
    planes: dict[int, list[FloatGrid]] = {}
    measured: JsonObject = {}
    for name, hex_colour in targets.items():
        code = family_code(name, "families")
        own = codes == code
        if own.sum() < min_cells:
            continue
        target = display_to_ground(palette, hex_colour)
        lab = base_lab.copy()
        step = float(target[0] - np.median(base_lab[own][:, 0]))
        lab[..., 0] += np.float32(step)
        lab[..., 1:] = target[1:]
        rgb = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        planes[code] = [rgb[..., k].astype(np.float32) for k in range(3)]
        measured[name] = {"cells": int(own.sum()), "dL": round(step, 4)}
    return planes, measured


def top_targets(
    top: npt.ArrayLike, targets: Mapping[str, str], palette: PaintedPalette
) -> FloatGrid:
    """The top layer table with each named family's display target, as ground colour, in
    place of its texture's mean."""
    table = np.array(top, np.float32)
    for name, hex_colour in targets.items():
        lab = display_to_ground(palette, hex_colour)
        table[family_code(name, "tops")] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return table


def _ramp(nz: FloatGrid, lo_hi: Sequence[float]) -> FloatGrid:
    lo, hi = lo_hi
    return ndimage.uniform_filter(np.clip((nz - lo) / (hi - lo), 0.0, 1.0), 3)


def top_cover(scene: PaintedScene, ground: PaintedSurface, code: U8Grid) -> FloatGrid:
    """The top layer's weight per pixel: the up-facing faces of a family with a top, and with
    ``rock_top.patches`` only in patches, more of them the flatter the face. The patches are
    ``patch_noise`` at each pixel's centre, so a point draws the same at any size or band."""
    _band, lo, _hi, c0, _c1, spacing_m = scene["grid"]
    d_south, d_east = np.gradient(scene["z_m"], spacing_m)
    nz = 1.0 / np.sqrt(1.0 + d_east * d_east + d_south * d_south)
    rule = ground.palette["rock_top"]
    weight = _ramp(nz, rule["up"]) * ground.family_has_top[code]
    patches = rule.get("patches")
    seen = weight > 0.0
    if patches is None or not seen.any():
        return weight
    rows, cols = np.nonzero(seen)
    noise = patch_noise((c0 + cols + 0.5) * spacing_m, (lo + rows + 0.5) * spacing_m,
                        patches["octaves_m"], patches["seed"])  # fmt: skip
    level = noise + patches["flat_gain"] * (_ramp(nz, patches["flat"])[seen] - 1.0)
    weight[seen] *= np.clip((level - patches["level"]) / patches["soft"] + 0.5, 0.0, 1.0)
    return weight


def rock_surface(
    rock_rgb: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample_rock: Sampler | None = None,
    code: U8Grid | None = None,
) -> FloatGrid:
    """Rock in its family's colour: the family's own target where it has one, else the area's
    rock in the family's tint, with the family's top layer on its up-facing faces (``top_cover``).
    ``code`` is the family per pixel; the direct pass's family plane on this band when None."""
    if code is not None:
        code = np.asarray(code)
    elif ground.rock_family is not None:
        code = np.asarray(ground.rock_family[scene["grid"][0]])
    else:
        return rock_rgb
    for which, planes in ground.family_rock.items():
        hit = (code == which)[..., None]
        if hit.any():
            rock_rgb = np.where(hit, sampled_rgb(planes, sample_rock), rock_rgb)
    rgb = rock_rgb * ground.family_tint[code]
    weight = top_cover(scene, ground, code)[..., None]
    return rgb * (1.0 - weight) + ground.family_top[code] * weight


def canopy_over_rock(
    g: FloatGrid,
    canopy: FloatGrid,
    rock: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample: Sampler,
    rgb: FloatGrid | None = None,
) -> FloatGrid:
    """The canopy laid over rock wherever the drawn surface is no higher than a crown top.

    ``rgb`` is the canopy colour already sampled onto the band; the ground's constant if None.
    """
    if ground.crown is None:
        return g
    crown_m = sample(ground.crown) / np.float32(hf.DM_PER_M)
    seen = (scene["z_m"] <= crown_m)[..., None]
    cover = canopy * rock * seen
    colour = sampled_rgb(ground.canopy_rgb, None) if rgb is None else rgb
    return g * (1.0 - cover) + colour * cover


def sunk_specks(scene: PaintedScene) -> BandWater:
    """The band's water with each coral speck standing in it drawn as that water.

    A speck is a coral pixel whose neighbours are mostly water: it takes their mean depth and
    full water cover, so the coral is seen as the bed under the water around it, as the
    seabed carpet is. Larger coral, and coral on land, keeps its own surface.
    """
    water = scene["water"]
    mesh_w, mesh_class = scene.get("mesh_weight"), scene.get("mesh_class")
    if mesh_w is None or mesh_class is None:
        return water
    coral = (mesh_class == MESH_CORAL) & (mesh_w > 0)
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


def mesh_surface(
    g: FloatGrid,
    area_rock: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample_rock: Sampler | None,
) -> FloatGrid:
    """The render-only meshes over ``g``, each class in its own colour.

    A rock wears its own family (``scene["mesh_family"]``) and that family's top layer, as a
    cliff does, never the family of a cliff it happens to overlap; without the plane it takes
    the area's rock. Coral under water is the seabed coral; ``scene["water"]`` is the water
    after ``sunk_specks``.
    """
    mesh_w, cls = scene.get("mesh_weight"), scene.get("mesh_class")
    if mesh_w is None or cls is None or not mesh_w.any():
        return g
    family = scene.get("mesh_family")
    colour = area_rock
    if family is not None and (cls == MESH_ROCK).any():
        colour = rock_surface(area_rock, scene, ground, sample_rock, family)
    for which, rgb in ground.mesh_rgb.items():
        colour = np.where((cls == which)[..., None], sampled_rgb(rgb, sample_rock), colour)
    wet = np.where(cls == MESH_CORAL, np.clip(scene["water"]["cover"], 0.0, 1.0), 0.0)
    colour = colour + (ground.seabed_coral - colour) * wet[..., None]
    return g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]

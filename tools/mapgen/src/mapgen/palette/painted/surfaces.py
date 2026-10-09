"""What stands on the painted ground: rock in its family's colour and look, the canopy over
rock, and the render-only meshes. docs/map/painted.md sections 27 and 30 and
docs/map/calibration.md section 31.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping

import numpy as np
import numpy.typing as npt

from mapgen.cache import Plane
from mapgen.colour import linear_from_oklab
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.palette.painted.calibration import (
    display_to_ground,
    sampled_rgb,
    scoped_planes,
    with_derived,
)
from mapgen.palette.painted.rock_look.reference import (
    KIND_ARCH,
    KIND_CLIFF,
    KIND_DESERT,
    KIND_LAYER,
)
from mapgen.palette.painted.rock_look.surface import (
    band_look,
    mixed,
    surface_normals,
    top_mask,
    top_rules,
)
from mapgen.palette.painted.shapes import (
    BandWater,
    ColourPlanes,
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    RockFamilyEntry,
    Sampler,
)
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK
from satisfactory_mcp.core.arrays import BoolMask, I32Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SPECK_WATER",
    "canopy_over_rock",
    "cliff_layer",
    "family_cells",
    "family_code",
    "family_tables",
    "family_targets",
    "layer_tops",
    "mesh_surface",
    "rock_surface",
    "sunk_specks",
    "top_targets",
]

#: The desert rock family: its material has no albedo texture and no top layer.
_DESERT = FAMILIES.index("desert")

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
    (``top_targets``), and give one to a family whose top texture the store could not read.

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
        tops = palette["calibration"].get("tops", {})
        top = top_targets(top, tops, palette)
        for name in tops:
            has_top[family_code(name, "tops")] = 1.0
    return tint, top, has_top


def family_cells(plane: Plane, shape: tuple[int, int], step_m: float) -> U8Grid:
    """The family plane's code at each point of a ``step_m`` grid from the frame's corner: the
    code of the pixel the point falls in."""
    span_m = BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]
    picks = [
        np.clip(np.floor(np.arange(n) * step_m * size / span_m), 0, size - 1).astype(int)
        for n, size in zip(shape, plane.shape, strict=True)
    ]
    return np.asarray(plane[picks[0]][:, picks[1]], np.uint8)


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


def layer_tops(
    families: Mapping[str, RockFamilyEntry],
    palette: PaintedPalette,
    area_weight: Callable[[Collection[str]], FloatGrid],
) -> dict[int, ColourPlanes]:
    """By family code, the top of each family whose top texture is a paint layer's (its tile
    folder names the layer), in that layer's display target and scoped by area as the layer
    is: one colour, or three planes on the rock grid. A family with a top target of its own
    (``calibration.tops``), or whose layer has no target, keeps ``family_tables``' top."""
    cal = with_derived(palette["calibration"])
    tops: dict[int, ColourPlanes] = {}
    for name, entry in families.items():
        texture, top = entry.get("top_texture"), entry.get("top")
        if name not in FAMILIES or not texture or not top or name in cal.get("tops", {}):
            continue
        layer = f"{texture.rsplit('/', 2)[-2]}_LayerInfo"
        scoped: list[tuple[FloatGrid, FloatGrid]] = []
        for area in cal.get("areas", []):
            if (hex_colour := area.get("layers", {}).get(layer)) is not None:
                scoped.append((area_weight(area["areas"]), _ground_rgb(palette, hex_colour)))
        known = cal["layers"].get(layer)
        if known is None and not scoped:
            continue
        default = _ground_rgb(palette, known) if known else np.asarray(top, np.float32)
        tops[FAMILIES.index(name)] = scoped_planes(default, scoped)
    return tops


def _ground_rgb(palette: PaintedPalette, hex_colour: str) -> FloatGrid:
    return np.clip(linear_from_oklab(display_to_ground(palette, hex_colour)), 0.0, 1.0)


def _mean3x3(a: FloatGrid) -> FloatGrid:
    """scipy's ``uniform_filter(a, 3)`` without its running sum, which drifts with where a
    row starts: down the rows, then along them, each mean of three taps summed in float64 in
    one order and rounded to ``a``'s type. Past the edge the edge texel stands in."""
    out = a
    for axis in (0, 1):
        n: int = out.shape[axis]
        pad = [(1, 1) if k == axis else (0, 0) for k in range(out.ndim)]
        wide = np.pad(out, pad, mode="edge").astype(np.float64)
        before, here, after = (np.take(wide, np.arange(k, k + n), axis) for k in range(3))
        out = (((before + here) + after) / 3.0).astype(a.dtype)
    return out


def _top_tiles(ground: PaintedSurface, code: U8Grid) -> I32Grid:
    """Each pixel's top layer's albedo tile in the look, -1 where its family has none."""
    tiles = np.full(len(FAMILIES), -1, np.int32)
    if ground.rock_look is not None:
        for which, tile in ground.rock_look.tops.items():
            if ground.family_has_top[which]:
                tiles[which] = tile
    return tiles[code]


def rock_surface(
    rock_rgb: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample_rock: Sampler | None = None,
    code: U8Grid | None = None,
    pick: BoolMask | None = None,
) -> FloatGrid:
    """Rock in its family's colour and look: the family's own target where it has one, else
    the area's rock in the family's tint, its top layer where the cliff master's mask puts it,
    textured by the look where the run has one at the ``pick``ed pixels (all when None).
    ``code`` is the family per pixel; the direct pass's family plane on this band when None,
    which an arch or boulder lifted over the cliff (``scene["top_weight"]``) does not wear: by
    its lift it takes the arches' rock (``_arch_rock``)."""
    area_rock, lifted = rock_rgb, None
    if code is not None:
        code = np.asarray(code)
    elif ground.rock_family is not None:
        code = np.asarray(ground.rock_family[scene["grid"][0]], np.uint8)
        lifted = scene.get("top_weight")
    else:
        return rock_rgb
    for which, planes in ground.family_rock.items():
        hit = (code == which)[..., None]
        if hit.any():
            rock_rgb = np.where(hit, sampled_rgb(planes, sample_rock), rock_rgb)
    rgb = rock_rgb * ground.family_tint[code]
    top = ground.family_top[code]
    for which, planes in ground.family_top_rgb.items():
        hit = (code == which)[..., None]
        if hit.any():
            top = np.where(hit, sampled_rgb(planes, sample_rock), top)
    look, has_top = ground.rock_look, ground.family_has_top[code]
    pick = np.ones(code.shape, bool) if pick is None else pick
    if look is None:
        up = surface_normals(scene["z_m"], scene["grid"][5])[..., 2]
        weight = (top_mask(up, *top_rules(None, code)) * has_top)[..., None]
        out = rgb * (1.0 - weight) + top * weight
    else:
        kind = np.where(code == _DESERT, KIND_DESERT, KIND_CLIFF).astype(np.uint8)
        got = band_look(look, scene, ground.palette, pick, (kind, _top_tiles(ground, code)))
        strength = ground.palette["rock_look"]
        weight = (top_mask(got.up, *top_rules(look, code)) * has_top)[..., None]
        body = rgb * mixed(got.body, strength["albedo"])
        layer = top * mixed(got.top, strength["albedo"])
        shade = mixed(got.shade, strength["shade"])[..., None]
        out = (body * (1.0 - weight) + layer * weight) * shade
    if lifted is None or not lifted.any():
        return out
    arch = _arch_rock(area_rock, scene, ground, pick & (lifted > 0.0), sample_rock)
    return out + (arch - out) * lifted[..., None]


def _arch_rock(
    area_rock: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    pick: BoolMask,
    sample_rock: Sampler | None = None,
) -> FloatGrid:
    """The arches and boulders: their own colour, by area where an area entry gives one, where
    the palette has it, else the area's rock, in the arches' rock texture where the run has the
    look."""
    own = ground.arch_rgb
    rock = (
        area_rock
        if own is None
        else np.broadcast_to(sampled_rgb(own, sample_rock), area_rock.shape)
    )
    look = ground.rock_look
    if look is None:
        return rock
    reads = (np.full(pick.shape, KIND_ARCH, np.uint8), np.full(pick.shape, -1, np.int32))
    got = band_look(look, scene, ground.palette, pick, reads)
    strength = ground.palette["rock_look"]
    shade = mixed(got.shade, strength["shade"])[..., None]
    return rock * mixed(got.body, strength["albedo"]) * shade


def cliff_layer(
    albedo: FloatGrid, scene: PaintedScene, ground: PaintedSurface, sample: Sampler
) -> FloatGrid:
    """The landscape's Cliff layer textured by its own material, by its paint weight: the
    detail finer than the ground's 1 m colour, and its normal maps' light."""
    look, plane = ground.rock_look, ground.cliff_layer
    if look is None or plane is None:
        return albedo
    strength = ground.palette["rock_look"]
    weight = sample(plane) * np.float32(strength["layer"] / 255.0)
    pick = weight > 0.0
    if not pick.any():
        return albedo
    reads = (np.full(pick.shape, KIND_LAYER, np.uint8), np.full(pick.shape, -1, np.int32))
    got = band_look(look, scene, ground.palette, pick, reads)
    factor = mixed(got.body, strength["albedo"]) * mixed(got.shade, strength["shade"])[..., None]
    return albedo * (1.0 + weight[..., None] * (factor - 1.0))


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
    around = (_mean3x3(cover) * 9 - cover) / 8
    speck = coral & (around >= SPECK_WATER)
    if not speck.any():
        return water
    wet_depth = cover * water["depth_m"]
    deep = (_mean3x3(wet_depth) * 9 - wet_depth) / 8
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

    A rock wears its own family (``scene["mesh_family"]``), its top layer and its look, as a
    cliff does, never the family of a cliff it happens to overlap; without the plane it takes
    the area's rock. Coral under water is the seabed coral; ``scene["water"]`` is the water
    after ``sunk_specks``.
    """
    mesh_w, cls = scene.get("mesh_weight"), scene.get("mesh_class")
    if mesh_w is None or cls is None or not mesh_w.any():
        return g
    family = scene.get("mesh_family")
    colour = area_rock
    rock = (cls == MESH_ROCK) & (mesh_w > 0.0)
    if family is not None and rock.any():
        colour = rock_surface(area_rock, scene, ground, sample_rock, family, rock)
    for which, rgb in ground.mesh_rgb.items():
        colour = np.where((cls == which)[..., None], sampled_rgb(rgb, sample_rock), colour)
    wet = np.where(cls == MESH_CORAL, np.clip(scene["water"]["cover"], 0.0, 1.0), 0.0)
    colour = colour + (ground.seabed_coral - colour) * wet[..., None]
    return g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]

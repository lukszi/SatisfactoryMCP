"""Trees laid over the finished painted pixel: the Titan forest's raster and per-tree crowns,
the crowns moved onto the species targets, the canopy targets and the named crown targets;
a crown under the water's surface goes to the bed instead. docs/map/painted.md section 30,
docs/map/calibration.md section 31 and docs/map/light-and-crowns.md section 36.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple, TypeAlias

import numpy as np
import numpy.typing as npt
from numpy.typing import NDArray

from mapgen.cache import TitanPlanes
from mapgen.colour import flat_light, linear_from_oklab, oklab, sky_sun_light, srgb_to_linear
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM
from mapgen.gamedata.vegetation.crown_sprites import SPRITE_M
from mapgen.lighting.hillshade import sun_dot
from mapgen.palette.painted.calibration import (
    display_to_crown,
    exposure_gain,
    sampled_rgb,
    scoped_planes,
    transfer_op,
    turned,
    weighted_median,
)
from mapgen.palette.painted.shapes import (
    CalibrationStyle,
    CrownLayer,
    CrownOp,
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    Sampler,
    TitanTreesStyle,
)
from mapgen.palette.schema import CrownStyle
from mapgen.terrain.crown_stamp import CrownSet, LitCrowns
from mapgen.terrain.render_meshes import TITAN_LEAVES, TITAN_TRUNK
from satisfactory_mcp.core.arrays import I64Grid
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "CANOPY_GREY",
    "GATE_CHROMA",
    "HUE_GATE_DEG",
    "IDENTITY_OP",
    "TARGET_GREY",
    "CrownCalibration",
    "TreeOver",
    "canopy_cover",
    "crown_calibration",
    "crown_lab",
    "crown_layer",
    "crown_ops",
    "crown_over",
    "folded_trees",
    "hue_gate",
    "lay_over",
    "lit_crowns",
    "moved_crowns",
    "named_targets",
    "over_crowns",
    "sample_titan",
    "species_colours",
    "species_targets",
    "titan_colours",
    "titan_layer",
    "titan_over",
]

#: A colour transfer as seven numbers: the lightness step, the (a, b) matrix row-major, and
#: the target's hue as a unit (a, b) vector.
IDENTITY_OP = np.array([0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0], np.float32)

#: A crown takes all of its scope's transfer within the first angle of the target's hue and
#: none past the second, so a target moves the trees of its own hue and leaves pink bamboo
#: and coral their own colours.
HUE_GATE_DEG = (20.0, 40.0)
GATE_CHROMA = 0.02
#: The chroma over which a gate opens, none at the first and all from the second: the
#: canopy targets' from grey up, a named crown target's only past the greys.
CANOPY_GREY = (0.0, GATE_CHROMA)
TARGET_GREY = (GATE_CHROMA, 0.025)

#: Every species' mips, finest first: cover, cover-weighted linear rgb, dome and top.
SpeciesLevels: TypeAlias = Sequence[Sequence[FloatGrid]]
#: A scope of the crown calibration: its weight plane (None for the trees no other scope
#: holds) and its target in OKLab.
CrownScope: TypeAlias = tuple[FloatGrid | None, FloatGrid]
#: Trees as laid over the finished pixel: their alpha and their linear colour.
TreeOver: TypeAlias = tuple[FloatGrid, FloatGrid]


class CrownCalibration(NamedTuple):
    """The crowns' ``(op, grey)`` transfers, what was measured, and the species' moved mips."""

    ops: list[CrownOp]
    measured: JsonObject
    levels: list[list[FloatGrid]]


def crown_lab(rgb: npt.ArrayLike, style: CrownStyle) -> FloatGrid:
    """A crown's linear colour as the calibration sees it: darkened, at the style's chroma."""
    lab = oklab(np.clip(np.asarray(rgb, np.float32) * np.float32(style["darkening"]), 1e-7, None))
    lab[..., 1:] *= np.float32(style["chroma"])
    return lab


def hue_gate(lab: FloatGrid, hue: FloatGrid, grey: tuple[float, float] = CANOPY_GREY) -> FloatGrid:
    """How much of a transfer each crown colour takes, by its hue's distance from ``hue``."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    along = lab[..., 1] * hue[..., 0] + lab[..., 2] * hue[..., 1]
    cos = along / np.maximum(chroma * np.hypot(hue[..., 0], hue[..., 1]), np.float32(1e-6))
    full, none = np.cos(np.radians(HUE_GATE_DEG)).astype(np.float32)
    gate = np.clip((cos - none) / (full - none), 0.0, 1.0)
    lo, hi = grey
    opens = np.clip((chroma - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0)
    return (gate * opens).astype(np.float32)


def moved_crowns(lab: FloatGrid, ops: Sequence[tuple[FloatGrid, tuple[float, float]]]) -> FloatGrid:
    """Crown colours after ``(op, grey)`` transfers, each gated on the colour before any."""
    out = lab.copy()
    a, b = lab[..., 1], lab[..., 2]
    for op, grey in ops:
        gate = hue_gate(lab, op[..., 5:7], grey)
        out[..., 0] += gate * op[..., 0]
        out[..., 1] += gate * ((op[..., 1] - 1.0) * a + op[..., 2] * b)
        out[..., 2] += gate * (op[..., 3] * a + (op[..., 4] - 1.0) * b)
    return out


def species_colours(levels: SpeciesLevels) -> tuple[FloatGrid, FloatGrid]:
    """Each species' cover-weighted linear colour and the ground its sprite hides, m²."""
    colours: list[FloatGrid] = []
    areas: list[float] = []
    for mips in levels:
        cover = mips[0][..., 0]
        total = float(cover.sum())
        colours.append(mips[0][..., 1:4].sum((0, 1)) / max(total, 1e-6))
        areas.append(total * SPRITE_M * SPRITE_M)
    return np.asarray(colours, np.float32), np.asarray(areas, np.float32)


def crown_ops(
    crowns: CrownSet,
    style: CrownStyle,
    cells: tuple[I64Grid, I64Grid],
    scopes: Sequence[CrownScope],
    min_trees: int,
    grey: tuple[float, float] = CANOPY_GREY,
    levels: SpeciesLevels | None = None,
) -> tuple[list[FloatGrid | None], JsonObject]:
    """One colour transfer per scope, from its trees' median crown colour to its target.

    ``cells`` is each record's ``(row, col)`` on the scope planes. A tree counts by the ground
    its crown hides times its hue gate, which opens over ``grey``; ``levels`` are the mips it
    is measured on, the crowns' own when None. Returns the ops, None for a scope with too few
    trees, and what was measured.
    """
    colours, areas = species_colours(crowns.levels if levels is None else levels)
    species = crowns.records["species"]
    lab = crown_lab(colours, style)[species]
    weight = areas[species] * crowns.records["scale"] ** 2
    rows, cols = cells
    claimed = np.zeros(len(species), np.float32)
    for plane, _target in scopes:
        if plane is not None:
            claimed += plane[rows, cols]
    ops: list[FloatGrid | None] = []
    measured: JsonObject = {}
    for i, (plane, target) in enumerate(scopes):
        hue = np.asarray(target[1:], np.float32) / max(float(np.hypot(*target[1:])), 1e-6)
        gate = hue_gate(lab, hue, grey)
        inside = ((plane[rows, cols] if plane is not None else 1.0 - claimed) >= 0.5) & (gate > 0.5)
        if inside.sum() < min_trees:
            ops.append(None)
            continue
        source = weighted_median(lab[inside], (weight * gate)[inside])
        step, matrix = transfer_op(source, target)
        ops.append(np.array([step, *matrix.ravel(), *hue], np.float32))
        measured[f"crowns@{i}"] = {
            "trees": int(inside.sum()),
            "dL": round(step, 4),
            "chroma_scale": round(float(np.hypot(*matrix[0])), 3),
        }
    return ops, measured


def species_targets(
    crowns: CrownSet, style: CrownStyle, targets: Mapping[str, str], palette: PaintedPalette
) -> tuple[list[list[FloatGrid]], JsonObject]:
    """Every species' mips with each named species moved onto its target, and what was
    measured: one step from its colour as drawn (``crown_lab``) to the target as a crown."""
    names = list(crowns.names)
    levels: list[list[FloatGrid]] = [list(mips) for mips in crowns.levels]
    colours, _areas = species_colours(levels)
    measured: JsonObject = {}
    for name, hex_colour in targets.items():
        if name not in names:
            continue
        k = names.index(name)
        target = display_to_crown(palette, hex_colour)
        step, matrix = transfer_op(crown_lab(colours[k], style), target)
        levels[k] = [_moved_level(level, step, matrix, style) for level in levels[k]]
        measured[f"species@{name}"] = {
            "trees": int((crowns.records["species"] == k).sum()),
            "dL": round(step, 4),
            "chroma_scale": round(float(np.hypot(*matrix[0])), 3),
        }
    return levels, measured


def named_targets(
    crowns: CrownSet,
    palette: PaintedPalette,
    targets: CalibrationStyle,
    cells: tuple[I64Grid, I64Grid],
    levels: list[list[FloatGrid]],
) -> tuple[list[list[FloatGrid]], JsonObject]:
    """``levels`` with each named crown target moved into the mips of the species its gate
    opens for, by that share, and what was measured. The gate reads each species' own colour,
    so a crown seen through another species' sparse one keeps its target."""
    style = palette["crowns"]
    measured: JsonObject = {}
    for name, hex_colour in targets.get("crowns", {}).items():
        target = display_to_crown(palette, hex_colour)
        (op,), named = crown_ops(
            crowns, style, cells, [(None, target)], targets["min_texels"], TARGET_GREY, levels
        )
        if op is None:
            continue
        gates = hue_gate(crown_lab(species_colours(levels)[0], style), op[5:7], TARGET_GREY)
        matrix = op[1:5].reshape(2, 2)
        for k in np.nonzero(gates > 0)[0]:
            g = gates[k]
            share = np.eye(2, dtype=np.float32) + g * (matrix - np.eye(2, dtype=np.float32))
            levels[k] = [_moved_level(level, float(g * op[0]), share, style) for level in levels[k]]
        measured[f"crowns@{name}"] = named["crowns@0"]
    return levels, measured


def _moved_level(level: FloatGrid, step: float, matrix: FloatGrid, style: CrownStyle) -> FloatGrid:
    """One mip with each texel's colour moved by a transfer in the crown's OKLab."""
    cover = level[..., :1]
    lab = crown_lab(level[..., 1:4] / np.maximum(cover, np.float32(1e-6)), style)
    lab[..., 0] += np.float32(step)
    lab[..., 1:] = turned(lab[..., 1], lab[..., 2], matrix) / np.float32(style["chroma"])
    moved = np.clip(linear_from_oklab(lab), 0.0, None) / np.float32(style["darkening"])
    out = level.copy()
    out[..., 1:4] = np.where(cover > 0, moved * cover, 0.0)
    return out


def crown_calibration(
    crowns: CrownSet,
    palette: PaintedPalette,
    targets: CalibrationStyle,
    grid: tuple[tuple[int, int], float],
    area_weight: Callable[[Sequence[str]], FloatGrid],
) -> CrownCalibration:
    """The species targets (``species_targets``) and the named crown targets
    (``named_targets``), both moved into the species' mips, then the canopy targets' op (one
    op, or seven coarse planes) measured on the moved mips.

    ``grid`` is the scope planes' ``(shape, step cm)``, ``area_weight(keys)`` an area entry's
    plane on it. An area entry's trees move to its canopy target, the rest to the global one.
    """
    style = palette["crowns"]
    records = crowns.records
    shape, step = grid
    rows = np.clip(((records["y"] - ORIGIN_Y_CM) // step).astype(np.int64), 0, shape[0] - 1)
    cols = np.clip(((records["x"] - ORIGIN_X_CM) // step).astype(np.int64), 0, shape[1] - 1)
    entries = [e for e in targets.get("areas", []) if "canopy" in e]
    scopes: list[CrownScope] = [
        (area_weight(e["areas"]), display_to_crown(palette, e["canopy"])) for e in entries
    ]
    if "canopy" in targets:
        scopes.append((None, display_to_crown(palette, targets["canopy"])))
    levels, moved = species_targets(crowns, style, targets.get("species", {}), palette)
    levels, named = named_targets(crowns, palette, targets, (rows, cols), levels)
    ops, measured = crown_ops(
        crowns, style, (rows, cols), scopes, targets["min_texels"], levels=levels
    )
    found: JsonObject = {**measured, **moved, **named}
    default = ops[-1] if "canopy" in targets and ops[-1] is not None else IDENTITY_OP
    scoped = [(w, op) for (w, _t), op in zip(scopes, ops, strict=True) if w is not None]
    taken = [(w, op) for w, op in scoped if op is not None]
    out: list[CrownOp] = [(scoped_planes(default, taken), CANOPY_GREY)]
    return CrownCalibration(out, found, levels)


def sample_titan(
    titan: TitanPlanes, sheet: tuple[int, int, int, int]
) -> tuple[FloatGrid, FloatGrid, NDArray[np.generic]] | None:
    """The Titan tree raster bilinear on this band: ``(z m, cover, class)`` or ``None``."""
    z_cm, cls, factor, row0, col0 = titan
    lo, hi, c0, c1 = sheet
    fr = (np.arange(lo, hi, dtype=np.float32) + 0.5) / factor - 0.5 - row0
    fc = (np.arange(c0, c1, dtype=np.float32) + 0.5) / factor - 0.5 - col0
    r_lo, r_hi = max(int(np.floor(fr[0])), 0), min(int(np.floor(fr[-1])) + 2, cls.shape[0])
    c_lo, c_hi = max(int(np.floor(fc[0])), 0), min(int(np.floor(fc[-1])) + 2, cls.shape[1])
    if r_lo >= r_hi or c_lo >= c_hi:
        return None
    cut = np.asarray(cls[r_lo:r_hi, c_lo:c_hi], np.uint8)
    if not cut.any():
        return None
    height = np.asarray(z_cm[r_lo:r_hi, c_lo:c_hi], np.float32) / np.float32(100.0)
    have = (cut > 0).astype(np.float32)
    r = np.clip(fr - r_lo, 0, cut.shape[0] - 1)
    c = np.clip(fc - c_lo, 0, cut.shape[1] - 1)
    r0, c0_ = (
        np.minimum(r.astype(np.int64), cut.shape[0] - 2),
        np.minimum(c.astype(np.int64), cut.shape[1] - 2),
    )
    r0, c0_ = np.maximum(r0, 0), np.maximum(c0_, 0)
    tr, tc = np.clip(r - r0, 0, 1)[:, None], np.clip(c - c0_, 0, 1)[None, :]
    cover = np.zeros((len(fr), len(fc)), np.float32)
    weighted = np.zeros_like(cover)
    for dr, wr in ((0, 1.0 - tr), (1, tr)):
        for dc, wc in ((0, 1.0 - tc), (1, tc)):
            rr = np.minimum(r0 + dr, cut.shape[0] - 1)[:, None]
            cc = np.minimum(c0_ + dc, cut.shape[1] - 1)[None, :]
            w = wr * wc * have[rr, cc]
            cover += w
            weighted += w * height[rr, cc]
    z = weighted / np.maximum(cover, 1e-6)
    nearest = cut[np.rint(r).astype(np.int64)[:, None], np.rint(c).astype(np.int64)[None, :]]
    return z, cover, nearest


def titan_colours(palette: PaintedPalette) -> dict[int, FloatGrid]:
    """The Titan trees' leaves and trunk in linear colour; black where the palette has none."""
    style: TitanTreesStyle = palette.get("titan_trees") or {}
    return {
        TITAN_LEAVES: srgb_to_linear(style.get("leaves", (0, 0, 0))),
        TITAN_TRUNK: srgb_to_linear(style.get("trunk", (0, 0, 0))),
    }


def titan_over(out: FloatGrid, scene: PaintedScene, ground: PaintedSurface) -> FloatGrid:
    """The Titan trees over the finished pixel at the style's opacity; 0 turns them off."""
    layer = titan_layer(scene, ground)
    return out if layer is None else lay_over(out, layer)


def titan_layer(scene: PaintedScene, ground: PaintedSurface) -> TreeOver | None:
    """The Titan trees as ``titan_over`` lays them; None where it lays none. Drawn unlit they
    stand flat, as the crowns do: the light lights them by their own top."""
    found = _titan_seen(scene, ground)
    if found is None:
        return None
    z_t, cover, cls, alpha = found
    surface = np.where(cover > 0, z_t, scene["z_m"])
    albedo = np.zeros((*surface.shape, 3), np.float32)
    for which, rgb in ground.titan_rgb.items():
        albedo = np.where((cls == which)[..., None], rgb, albedo)
    palette = ground.palette
    exposure = exposure_gain(palette)
    spacing_m = scene["grid"][5]
    flat = scene["ndl_flat"]
    ndl = np.full(surface.shape, flat) if scene.get("unlit") else sun_dot(surface, spacing_m)
    return alpha, albedo * flat_light(palette, ndl, flat) * exposure


def lay_over(out: FloatGrid, layer: TreeOver) -> FloatGrid:
    """``out`` with a layer of trees laid over it at its alpha, in linear light."""
    alpha, colour = layer
    return out * (1.0 - alpha[..., None]) + colour * alpha[..., None]


def folded_trees(shape: tuple[int, ...], layers: Sequence[TreeOver]) -> TreeOver:
    """``layers``, laid in turn, as one layer of the band's ``shape``: the alpha of their
    union, and the colour that, laid at it, gives what laying each in turn gives. 0 where
    none lies. docs/map/light-and-crowns.md section 36, "Trees apart"."""
    alpha = np.zeros(shape[:2], np.float32)
    premultiplied = np.zeros((*shape[:2], 3), np.float32)
    for layer_alpha, colour in layers:
        premultiplied = lay_over(premultiplied, (layer_alpha, colour))
        alpha = alpha * (1.0 - layer_alpha) + layer_alpha
    seen = alpha > 0
    colour = np.where(seen[..., None], premultiplied / np.where(seen, alpha, 1.0)[..., None], 0.0)
    return alpha, colour.astype(np.float32)


def canopy_cover(scene: PaintedScene, ground: PaintedSurface) -> FloatGrid | None:
    """How much of each pixel the crowns and the Titan trees hide, as the style draws them
    over everything else; None where there are neither."""
    crowns = scene.get("crowns")
    cover = None if crowns is None else np.clip(crowns["cover"], 0.0, 1.0)
    found = _titan_seen(scene, ground)
    if found is None:
        return cover
    alpha = found[3]
    return alpha if cover is None else 1.0 - (1.0 - cover) * (1.0 - alpha)


def _titan_seen(
    scene: PaintedScene, ground: PaintedSurface
) -> tuple[FloatGrid, FloatGrid, NDArray[np.generic], FloatGrid] | None:
    """The Titan trees over the band: ``(z m, cover, class, alpha)``, the alpha they are drawn
    at; None where the palette draws none or the band holds none."""
    style: TitanTreesStyle = ground.palette.get("titan_trees") or {}
    opacity = np.float32(style.get("opacity", 0.0))
    if ground.titan is None or not opacity:
        return None
    _band, lo, hi, c0, c1, _spacing_m = scene["grid"]
    found = sample_titan(ground.titan, (lo, hi, c0, c1))
    if found is None:
        return None
    z_t, cover, cls = found
    above = cover * (z_t >= scene["z_m"] - np.float32(0.5))
    return z_t, cover, cls, opacity * np.clip(above, 0.0, 1.0)


def crown_layer(
    crowns: LitCrowns,
    scene: PaintedScene,
    palette: PaintedPalette,
    ambient: float | np.float32,
    exposure: float | np.float32,
    ops: Sequence[tuple[FloatGrid, tuple[float, float]]] = (),
) -> CrownLayer:
    """The crowns of a band, lit by their own domes: ``alpha``, ``colour``, ``top_m``, and
    ``sunk``, the share of each pixel's crown that stands under the water's surface.

    A crown is hidden where the drawn surface stands above its top: a tree under an
    overhang, or rock the tree grows beside and below. ``ops`` are the calibration's
    ``(op, grey)`` transfers, each op seven numbers or seven planes on the band.
    """
    style = palette["crowns"]
    cover = crowns["cover"]
    top_m = np.nan_to_num(crowns["top_cm"], nan=-1e9) / np.float32(100.0)
    seen = top_m > scene["z_m"] - style["hidden_below_m"]
    water = scene["water"]
    below = scene["z_m"] + water["depth_m"] - top_m
    sunk = water["cover"] * np.clip(below / np.float32(style["waterline_m"]) + 0.5, 0.0, 1.0)
    lab = moved_crowns(crown_lab(crowns["rgb"] / np.maximum(cover, 1e-4)[..., None], style), ops)
    lab[..., 1:] *= np.float32(palette["chroma_gain"])
    colour = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    low, high = style["shade_clamp"]
    shade = np.clip(crowns["ndl"] / scene["ndl_flat"], low, high)
    light = sky_sun_light(palette["sky"], palette["sun"], np.float32(ambient), shade)
    return {
        "alpha": np.clip(cover, 0.0, 1.0) * np.float32(style["opacity"]) * seen,
        "colour": colour * light * exposure,
        "top_m": top_m,
        "sunk": sunk,
    }


def lit_crowns(
    scene: PaintedScene,
    ground: PaintedSurface,
    sample_rock: Sampler,
    exposure: float | np.float32,
) -> CrownLayer | None:
    """The band's ``crown_layer`` with the ground's calibration ops on it; None without crowns."""
    crowns, palette = scene.get("crowns"), ground.palette
    if crowns is None:
        return None
    ops = [(sampled_rgb(op, sample_rock), grey) for op, grey in ground.crown_ops]
    return crown_layer(crowns, scene, palette, np.float32(palette["ambient"]), exposure, ops)


def over_crowns(out: FloatGrid, crowns: CrownLayer) -> FloatGrid:
    """The crowns that stand out of the water, over the finished pixel and its water."""
    return lay_over(out, crown_over(crowns))


def crown_over(crowns: CrownLayer) -> TreeOver:
    """The crowns as ``over_crowns`` lays them: the share of each that stands out of the water."""
    return crowns["alpha"] * (1.0 - crowns["sunk"]), crowns["colour"]

"""Trees laid over the finished painted pixel: the Titan forest's raster and per-tree crowns,
the crowns moved onto the species targets, the canopy targets and the named crown targets;
a crown under the water's surface goes to the bed instead. docs/spatial-and-map.md sections
30, 31 and 36.
"""

from __future__ import annotations

import numpy as np

from mapgen.gamedata.crowns import SPRITE_M
from mapgen.lighting.hillshade import sun_dot
from mapgen.palette.calibration import (
    display_to_crown,
    sampled_rgb,
    transfer_op,
    weighted_median,
)
from mapgen.palette.colour import flat_light, linear_from_oklab, oklab, unit_luminance

__all__ = [
    "CANOPY_GREY",
    "GATE_CHROMA",
    "HUE_GATE_DEG",
    "IDENTITY_OP",
    "TARGET_GREY",
    "band_crowns",
    "crown_lab",
    "crown_layer",
    "crown_ops",
    "hue_gate",
    "moved_crowns",
    "over_crowns",
    "sample_titan",
    "species_colours",
    "species_targets",
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


def crown_lab(rgb, style: dict) -> np.ndarray:
    """A crown's linear colour as the calibration sees it: darkened, at the style's chroma."""
    lab = oklab(np.clip(np.asarray(rgb, np.float32) * np.float32(style["darkening"]), 1e-7, None))
    lab[..., 1:] *= np.float32(style["chroma"])
    return lab


def hue_gate(lab, hue, grey=CANOPY_GREY) -> np.ndarray:
    """How much of a transfer each crown colour takes, by its hue's distance from ``hue``."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    along = lab[..., 1] * hue[..., 0] + lab[..., 2] * hue[..., 1]
    cos = along / np.maximum(chroma * np.hypot(hue[..., 0], hue[..., 1]), np.float32(1e-6))
    full, none = np.cos(np.radians(HUE_GATE_DEG)).astype(np.float32)
    gate = np.clip((cos - none) / (full - none), 0.0, 1.0)
    lo, hi = grey
    opens = np.clip((chroma - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0)
    return (gate * opens).astype(np.float32)


def moved_crowns(lab, ops) -> np.ndarray:
    """Crown colours after ``(op, grey)`` transfers, each gated on the colour before any."""
    out = lab.copy()
    a, b = lab[..., 1], lab[..., 2]
    for op, grey in ops:
        gate = hue_gate(lab, op[..., 5:7], grey)
        out[..., 0] += gate * op[..., 0]
        out[..., 1] += gate * ((op[..., 1] - 1.0) * a + op[..., 2] * b)
        out[..., 2] += gate * (op[..., 3] * a + (op[..., 4] - 1.0) * b)
    return out


def species_colours(crowns) -> tuple[np.ndarray, np.ndarray]:
    """Each species' cover-weighted linear colour and the ground its sprite hides, m²."""
    colours, areas = [], []
    for levels in crowns.levels:
        cover = levels[0][..., 0]
        total = float(cover.sum())
        colours.append(levels[0][..., 1:4].sum((0, 1)) / max(total, 1e-6))
        areas.append(total * SPRITE_M * SPRITE_M)
    return np.asarray(colours, np.float32), np.asarray(areas, np.float32)


def crown_ops(
    crowns, style: dict, cells, scopes: list, min_trees: int, grey=CANOPY_GREY
) -> tuple[list, dict]:
    """One colour transfer per scope, from its trees' median crown colour to its target.

    ``cells`` is each record's ``(row, col)`` on the scope planes; ``scopes`` holds
    ``(weight plane or None, target OKLab)``, None for the trees no other scope holds. A tree
    counts by the ground its crown hides times its hue gate, which opens over ``grey``.
    Returns the ops, None for a scope with too few trees, and what was measured.
    """
    colours, areas = species_colours(crowns)
    species = crowns.records["species"]
    lab = crown_lab(colours, style)[species]
    weight = areas[species] * crowns.records["scale"] ** 2
    rows, cols = cells
    claimed = np.zeros(len(species), np.float32)
    for plane, _target in scopes:
        if plane is not None:
            claimed += plane[rows, cols]
    ops, measured = [], {}
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
        measured[f"crowns@{i}"] = {"trees": int(inside.sum()), "dL": round(step, 4),
                                   "chroma_scale": round(float(np.hypot(*matrix[0])), 3)}  # fmt: skip
    return ops, measured


def species_targets(crowns, style: dict, targets: dict, palette: dict) -> dict:
    """Each named species' crowns moved onto its own target wherever they grow, in place.

    The step runs from the species' own colour as drawn (``crown_lab``) to the target as a
    crown (``display_to_crown``) and moves every texel of its mips, so the hue-gated ops that
    follow see the moved colour. Returns what was measured, per species.
    """
    names = list(getattr(crowns, "names", ()))
    colours, _areas = species_colours(crowns)
    measured = {}
    for name, hex_colour in targets.items():
        if name not in names:
            continue
        k = names.index(name)
        target = display_to_crown(palette, hex_colour)
        step, matrix = transfer_op(crown_lab(colours[k], style), target)
        crowns.levels[k] = [_moved_level(level, step, matrix, style) for level in crowns.levels[k]]
        measured[f"species@{name}"] = {"trees": int((crowns.records["species"] == k).sum()),
                                       "dL": round(step, 4),
                                       "chroma_scale": round(float(np.hypot(*matrix[0])), 3)}  # fmt: skip
    return measured


def _moved_level(level, step: float, matrix, style: dict) -> np.ndarray:
    """One mip with each texel's colour moved by a transfer in the crown's OKLab."""
    cover = level[..., :1]
    lab = crown_lab(level[..., 1:4] / np.maximum(cover, np.float32(1e-6)), style)
    lab[..., 0] += np.float32(step)
    lab[..., 1:] = lab[..., 1:] @ matrix.T / np.float32(style["chroma"])
    moved = np.clip(linear_from_oklab(lab), 0.0, None) / np.float32(style["darkening"])
    out = level.copy()
    out[..., 1:4] = np.where(cover > 0, moved * cover, 0.0)
    return out


def sample_titan(titan, sheet) -> tuple | None:
    """The Titan tree raster bilinear on this band: ``(z m, cover, class)`` or ``None``."""
    z_cm, cls, factor, row0, col0 = titan
    lo, hi, c0, c1 = sheet
    fr = (np.arange(lo, hi, dtype=np.float32) + 0.5) / factor - 0.5 - row0
    fc = (np.arange(c0, c1, dtype=np.float32) + 0.5) / factor - 0.5 - col0
    r_lo, r_hi = max(int(np.floor(fr[0])), 0), min(int(np.floor(fr[-1])) + 2, cls.shape[0])
    c_lo, c_hi = max(int(np.floor(fc[0])), 0), min(int(np.floor(fc[-1])) + 2, cls.shape[1])
    if r_lo >= r_hi or c_lo >= c_hi:
        return None
    cut = np.asarray(cls[r_lo:r_hi, c_lo:c_hi])
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


def titan_over(out, scene: dict, ground) -> np.ndarray:
    """The Titan trees over the finished pixel at the style's opacity; 0 turns them off."""
    p = ground.palette
    opacity = np.float32((p.get("titan_trees") or {}).get("opacity", 0.0))
    if ground.titan is None or not opacity:
        return out
    _band, lo, hi, c0, c1, spacing_m = scene["grid"]
    found = sample_titan(ground.titan, (lo, hi, c0, c1))
    if found is None:
        return out
    z_t, cover, cls = found
    above = cover * (z_t >= scene["z_m"] - np.float32(0.5))
    surface = np.where(cover > 0, z_t, scene["z_m"])
    albedo = np.zeros(out.shape, np.float32)
    for which, rgb in ground.titan_rgb.items():
        albedo = np.where((cls == which)[..., None], rgb, albedo)
    exposure = np.float32(p["exposure"] * p["tone"]["gain"])
    lit = albedo * flat_light(p, sun_dot(surface, spacing_m), scene["ndl_flat"]) * exposure
    alpha = (opacity * np.clip(above, 0.0, 1.0))[..., None]
    return out * (1.0 - alpha) + lit * alpha


def crown_layer(crowns: dict, scene: dict, p: dict, ambient, exposure, ops=()) -> dict:
    """The crowns of a band, lit by their own domes: ``alpha``, ``colour``, ``top_m``, and
    ``sunk``, the share of each pixel's crown that stands under the water's surface.

    A crown is hidden where the drawn surface stands above its top: a tree under an
    overhang, or rock the tree grows beside and below. ``ops`` are the calibration's
    ``(op, grey)`` transfers, each op seven numbers or seven planes on the band.
    """
    style = p["crowns"]
    cover = crowns["cover"]
    top_m = np.nan_to_num(crowns["top_cm"], nan=-1e9) / np.float32(100.0)
    seen = top_m > scene["z_m"] - style["hidden_below_m"]
    water = scene["water"]
    below = scene["z_m"] + water["depth_m"] - top_m
    sunk = water["cover"] * np.clip(below / np.float32(style["waterline_m"]) + 0.5, 0.0, 1.0)
    lab = moved_crowns(crown_lab(crowns["rgb"] / np.maximum(cover, 1e-4)[..., None], style), ops)
    lab[..., 1:] *= np.float32(p["chroma_gain"])
    colour = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    shade = np.clip(crowns["ndl"] / scene["ndl_flat"], *style["shade_clamp"])
    light = (
        ambient * unit_luminance(p["sky"])
        + (1 - ambient) * unit_luminance(p["sun"]) * shade[..., None]
    )
    return {"alpha": np.clip(cover, 0.0, 1.0) * np.float32(style["opacity"]) * seen,
            "colour": colour * light * exposure, "top_m": top_m, "sunk": sunk}  # fmt: skip


def band_crowns(scene: dict, ground, sample_rock, exposure) -> dict | None:
    """The band's ``crown_layer`` with the ground's calibration ops on it; None without crowns."""
    crowns, p = scene.get("crowns"), ground.palette
    if crowns is None:
        return None
    ops = [(sampled_rgb(op, sample_rock), grey) for op, grey in getattr(ground, "crown_ops", ())]
    return crown_layer(crowns, scene, p, np.float32(p["ambient"]), exposure, ops)


def over_crowns(out, layer: dict):
    """The crowns that stand out of the water, over the finished pixel and its water."""
    alpha = (layer["alpha"] * (1.0 - layer["sunk"]))[..., None]
    return out * (1.0 - alpha) + layer["colour"] * alpha

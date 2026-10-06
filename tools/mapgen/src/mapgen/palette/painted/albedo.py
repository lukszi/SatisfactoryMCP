"""The paint store read and mixed into a ground albedo, with the bake patched over it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.colour import linear_from_oklab, linear_to_srgb, oklab, srgb_to_linear
from mapgen.gamedata.ground.bake import BAKE_NAME, STAMP_RING_MIN, stamp_windows
from mapgen.gamedata.ground.paint_store import META_NAME
from mapgen.gamedata.water.bodies import WATER_BODIES_NAME
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "GroundBake",
    "bake_table",
    "ground_albedo",
    "hidden_ground",
    "layer_table",
    "load_paint_meta",
    "load_water_bodies",
    "mix_layers",
    "paint_plane",
    "patch_stamps",
    "seam_blend",
]


def load_paint_meta(paint_dir: Path) -> dict | None:
    try:
        return json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def bake_table(meta: dict) -> dict[str, np.ndarray] | None:
    """The paint layers' albedo refitted to the bake, or ``None`` for a store without one."""
    fit = meta["albedo_linear"].get("layers_bake_fit")
    if not fit or BAKE_NAME not in meta["files"]:
        return None
    return {name: np.asarray(value, np.float32) for name, value in fit.items()}


def hidden_ground(ok: np.ndarray) -> np.ndarray:
    """Holes the bake's own cover encloses: landscape the game hides, a crater's pit or a
    cave's mouth, where the paint under it is never seen."""
    return ndimage.binary_fill_holes(ok) & ~ok


def layer_table(meta: dict, palette: dict) -> dict[str, np.ndarray]:
    """Linear albedo per paint layer, with the palette's WetSand correction applied."""
    darkening = np.float32(palette["albedo_darkening"])
    table = {
        name: np.asarray(value, np.float32) * darkening
        for name, value in meta["albedo_linear"]["layers"].items()
    }
    wet, sand = table.get("WetSand_LayerInfo"), table.get("Sand_LayerInfo")
    if wet is not None and sand is not None:
        lab = oklab(wet)
        lab[0] = oklab(sand)[0] * np.float32(palette["wet_sand_lightness_of_sand"])
        table["WetSand_LayerInfo"] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return table


def mix_layers(weights: dict[str, np.ndarray], table: dict[str, np.ndarray], shape) -> tuple:
    """Weight-normalised mix of the layers' albedos; and where any layer was painted."""
    acc = np.zeros((*shape, 3), np.float32)
    total = np.zeros(shape, np.float32)
    for name, weight in weights.items():
        if name not in table:
            continue
        w = weight.astype(np.float32) / np.float32(255.0)
        acc += w[..., None] * table[name]
        total += w
    have = total > 0
    return acc / np.maximum(total, 1e-6)[..., None], have


def seam_blend(rgb: np.ndarray, origins, size_px: int, palette: dict) -> tuple[np.ndarray, int]:
    """Soften paint steps that sit exactly on landscape-component edges.

    A component painted solid with one layer meets its neighbour in a straight 127 m line. The
    jump across every component edge is measured; where it is a step rather than a gradient,
    the colour is blended towards its own blur over ``seam_blend_m``.
    """
    rows, cols = rgb.shape[:2]
    edge = np.zeros((rows, cols), bool)
    for row, col in origins:
        r0, r1 = max(row, 0), min(row + size_px, rows)
        c0, c1 = max(col, 0), min(col + size_px, cols)
        if r0 >= r1 or c0 >= c1:
            continue
        for r in (row, row + size_px - 1):
            if 0 <= r < rows:
                edge[r, c0:c1] = True
        for c in (col, col + size_px - 1):
            if 0 <= c < cols:
                edge[r0:r1, c] = True
    tone = np.sqrt(np.clip(rgb, 0.0, 1.0))
    jump = np.zeros((rows, cols), np.float32)
    jump[:, 1:-1] = np.abs(tone[:, 2:] - tone[:, :-2]).max(-1)
    jump[1:-1, :] = np.maximum(jump[1:-1, :], np.abs(tone[2:] - tone[:-2]).max(-1))
    lo, hi = palette["seam_jump"]
    step = np.where(edge, np.clip((jump - lo) / (hi - lo), 0.0, 1.0), 0.0).astype(np.float32)
    sigma = float(palette["seam_blend_m"])
    weight = np.clip(ndimage.gaussian_filter(step, sigma) * np.sqrt(2 * np.pi) * sigma, 0.0, 1.0)
    out = rgb.copy()
    for k in range(3):
        soft = ndimage.gaussian_filter(rgb[..., k], sigma)
        out[..., k] = rgb[..., k] * (1.0 - weight) + soft * weight
    return out, int((step > 0).sum())


@dataclass(frozen=True)
class GroundBake:
    """The game's baked ground colour on the paint grid: linear RGB and where it exists."""

    linear: np.ndarray
    have: np.ndarray

    @classmethod
    def from_srgb(cls, rgb, have) -> GroundBake:
        """From 8-bit sRGB; black texels inside ``have`` are holes in the bake, not ground."""
        rgb = np.asarray(rgb)
        linear = np.empty(rgb.shape, np.float32)
        for k in range(3):
            linear[..., k] = srgb_to_linear(rgb[..., k])
        return cls(linear, np.asarray(have, bool) & (rgb.astype(np.uint16).sum(-1) >= 3))


def patch_stamps(rgb, ok, paint, nodes_m) -> int:
    """Over each node's stamp, in place on the sRGB bake ``rgb`` where ``ok``: the linear paint
    mix scaled by the median ratio of bake to paint on the ring where the bake comes back
    (``stamp_windows``). Returns the texels replaced outright."""
    replaced = 0
    for window, keep in stamp_windows(nodes_m, rgb.shape[:2]):
        bake, mix, have = srgb_to_linear(rgb[window]), paint[window], ok[window]
        ring = have & (keep > 0) & (keep < 1) & (mix.min(-1) > 0)
        ratio = np.ones(3, np.float32)
        if ring.sum() >= STAMP_RING_MIN:
            ratio = np.median(bake[ring] / mix[ring], axis=0)
        k = keep[..., None]
        patched = np.round(linear_to_srgb(bake * k + np.clip(mix * ratio, 0.0, 1.0) * (1 - k)))
        write = (have & (keep < 1))[..., None]
        rgb[window] = np.where(write, patched, rgb[window]).astype(np.uint8)
        replaced += int((have & (keep == 0)).sum())
    return replaced


def ground_albedo(paint, have, bake: GroundBake | None, feather_m: float) -> tuple:
    """The ground albedo source: the bake where it exists, else the paint mix.

    Returns ``(albedo, have, bake_weight)``; ``bake_weight`` is None without a bake.
    """
    if bake is None:
        return paint, have, None
    inside = bake.have.astype(np.float32)
    weight = np.clip(ndimage.gaussian_filter(inside, feather_m) * 2.0 - 1.0, 0.0, 1.0) * inside
    w = weight[..., None]
    return paint * (1.0 - w) + bake.linear.astype(np.float32) * w, have | bake.have, weight


def paint_plane(paint_dir: Path, meta: dict, name: str) -> np.ndarray:
    """One plane of the paint store, decoded to its recorded shape."""
    entry = meta["files"][name]
    shape = entry["shape"]
    flat_width = shape[1] * (shape[2] if len(shape) > 2 else 1)
    decode = hf.decode_i16 if entry.get("kind") == "i16" else hf.decode_u8
    grid = decode((paint_dir / name).read_bytes(), shape[0], flat_width)
    return grid.reshape(shape)


def load_water_bodies(paint_dir: Path, meta: dict) -> dict | None:
    """The store's water actors and hot-spring terraces; None for a store that predates them."""
    if WATER_BODIES_NAME not in meta.get("files", {}):
        return None
    return json.loads((paint_dir / WATER_BODIES_NAME).read_text(encoding="utf-8"))

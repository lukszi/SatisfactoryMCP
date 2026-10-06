"""Tree crowns drawn into the render's own grid, a band at a time, from the paint store.

Each tree stamps its species' sprite, turned and scaled, at the mip whose texel is closest
to the output pixel. Taller crowns are laid over lower ones. A species the render-only mesh
pass draws is no crown. docs/spatial-and-map.md section 36 describes the planes a band
returns.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.crowns import (
    CROWNS_NAME,
    MATERIAL_NONE,
    SPRITE_M,
    SPRITES_NAME,
    decode_records,
    decode_sprites,
)
from mapgen.terrain.rasters import is_render_only_foliage

__all__ = [
    "COVER_TOP_MIN",
    "DOME_SIGMA_M",
    "CrownSet",
    "crown_band",
    "load_crowns",
    "meshed_species",
    "sprite_levels",
]

#: Smoothing of a crown's top before it is lit: leaf cards are not a surface.
DOME_SIGMA_M = 0.75
#: Cover under which a texel says nothing about the crown's height.
COVER_TOP_MIN = 0.25
#: Channels of a mip: cover, cover-weighted linear rgb (3), dome height (m), top (cm).
_COVER, _RGB, _DOME, _TOP = 0, slice(1, 4), 4, 5


class CrownSet:
    """Records sorted by y, each tree's reach and crown lift, and every species' mips.

    ``mid_cm`` is a species' cover-weighted crown height, where a leaning tree's crown is
    shifted to; ``top_cm`` its highest texel, which orders the stamping.
    """

    def __init__(self, records, levels, origins, reach_cm, mid_cm, top_cm):
        self.records = records[np.argsort(records["y"], kind="stable")]
        self.levels, self.origins = levels, origins
        k, rec = self.records["species"], self.records
        lean = np.hypot(rec["axis_x"], rec["axis_y"])
        self.lift_cm = mid_cm[k] * rec["scale_z"]
        self.height_cm = rec["z"] + top_cm[k] * rec["scale_z"] * rec["axis_z"]
        self.reach_cm = reach_cm[k] * rec["scale"] + top_cm[k] * rec["scale_z"] * lean
        self.max_reach_cm = float(self.reach_cm.max()) if len(rec) else 0.0


def sprite_levels(sprite: dict, colours: list) -> list[np.ndarray]:
    """One species' mips: level 0 at ``SPRITE_M``, each next one twice as coarse."""
    cover = sprite["cover"].astype(np.float32) / 255.0
    slot = sprite["slot"]
    rgb = np.zeros((*cover.shape, 3), np.float32)
    fallback = np.array(next((c for c in colours if c is not None), (0.05, 0.08, 0.03)))
    for k in np.unique(slot):
        if k == MATERIAL_NONE:
            continue
        colour = colours[k] if k < len(colours) and colours[k] is not None else fallback
        rgb[slot == k] = colour
    top = np.where(cover >= COVER_TOP_MIN, sprite["top_cm"].astype(np.float32), 0.0)
    dome = ndimage.gaussian_filter(cover * top / 100.0, DOME_SIGMA_M / SPRITE_M)
    level = np.dstack([cover, rgb * cover[..., None], dome, top]).astype(np.float32)
    out = [level]
    while max(level.shape[:2]) > 2:
        h, w = level.shape[0] + level.shape[0] % 2, level.shape[1] + level.shape[1] % 2
        even = np.pad(level, ((0, h - level.shape[0]), (0, w - level.shape[1]), (0, 0)))
        blocks = even.reshape(h // 2, 2, w // 2, 2, -1)
        level = blocks.mean((1, 3))
        level[..., _TOP] = blocks[..., _TOP].max((1, 3))
        out.append(level)
    return [np.pad(level, ((1, 1), (1, 1), (0, 0))) for level in out]


def meshed_species(species: list[dict]) -> np.ndarray:
    """Per species, whether the render-only mesh pass draws it: the coral trees."""
    return np.array([is_render_only_foliage(e.get("mesh", "")) for e in species], bool)


def load_crowns(paint_dir: Path, meta: dict) -> CrownSet | None:
    """The crowns of a paint store, or ``None`` for a store written before they existed.

    The trees of a ``meshed_species`` are left out: their mesh is drawn, lit by its own top.
    """
    block = meta.get("crowns")
    if not block or CROWNS_NAME not in meta.get("files", {}):
        return None
    records = decode_records((paint_dir / CROWNS_NAME).read_bytes())
    species = block["species"]
    records = records[~meshed_species(species)[records["species"]]]
    sprites = decode_sprites(
        (paint_dir / SPRITES_NAME).read_bytes(), [entry["sprite"] for entry in species]
    )
    levels, origins, reach, mid, high = [], [], [], [], []
    for entry, sprite in zip(species, sprites, strict=True):
        colours = [m["linear"] for m in entry["materials"]]
        levels.append(sprite_levels(sprite, colours))
        weight = levels[-1][0][..., _COVER]
        mid.append(float((weight * levels[-1][0][..., _TOP]).sum() / max(weight.sum(), 1e-6)))
        high.append(float(levels[-1][0][..., _TOP].max()))
        x0, y0 = sprite["x0_cm"], sprite["y0_cm"]
        origins.append((x0, y0))
        ys, xs = np.nonzero(sprite["cover"])
        step = SPRITE_M * 100.0
        far = np.hypot(x0 + (xs + 0.5) * step, y0 + (ys + 0.5) * step)
        reach.append(float(far.max()) + 2 * step if len(far) else 0.0)
    return CrownSet(
        records, levels, origins, *(np.array(v, np.float32) for v in (reach, mid, high))
    )


def _bilinear(level: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A zero-bordered mip sampled at texel coordinates ``(u, v)`` inside its rim."""
    w = level.shape[1]
    flat = level.reshape(-1, level.shape[2])
    x, y = u + np.float32(0.5), v + np.float32(0.5)
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    at = y0.astype(np.intp) * w + x0.astype(np.intp)
    top = flat[at] * (1 - fx) + flat[at + 1] * fx
    bottom = flat[at + w] * (1 - fx) + flat[at + w + 1] * fx
    return top * (1 - fy) + bottom * fy


def crown_band(crowns: CrownSet, x0_cm, y0_cm, step_cm, rows, cols) -> dict:
    """One band of crowns on pixel centres: cover, linear colour, dome height, top.

    ``cover`` and ``rgb`` are composited front over back, tallest last; ``dome_m`` is the
    highest crown's smoothed height above its own base, for the light; ``top_cm`` is the
    highest crown top in world cm, ``nan`` where none stands.
    """
    cover = np.zeros((rows, cols), np.float32)
    rgb = np.zeros((rows, cols, 3), np.float32)
    dome = np.zeros((rows, cols), np.float32)
    top = np.full((rows, cols), -np.inf, np.float32)
    y_hi = y0_cm + rows * step_cm
    ys = crowns.records["y"]
    lo = np.searchsorted(ys, y0_cm - crowns.max_reach_cm)
    hi = np.searchsorted(ys, y_hi + crowns.max_reach_cm, side="right")
    picked = np.arange(lo, hi)
    reach, xs = crowns.reach_cm[picked], crowns.records["x"][picked]
    near = (ys[picked] + reach >= y0_cm) & (ys[picked] - reach <= y_hi)
    near &= (xs + reach >= x0_cm) & (xs - reach <= x0_cm + cols * step_cm)
    picked = picked[near]
    for i in picked[np.argsort(crowns.height_cm[picked], kind="stable")]:
        _stamp(crowns, i, x0_cm, y0_cm, step_cm, (cover, rgb, dome, top))
    return {
        "cover": cover,
        "rgb": rgb,
        "dome_m": dome,
        "top_cm": np.where(top > -np.inf, top, np.nan).astype(np.float32),
    }


def _stamp(crowns: CrownSet, i: int, x0_cm, y0_cm, step_cm, planes) -> None:
    cover, rgb, dome, top = planes
    rows, cols = cover.shape
    tree = crowns.records[i]
    reach_cm, lift_cm = float(crowns.reach_cm[i]), float(crowns.lift_cm[i])
    k = int(tree["species"])
    scale = float(tree["scale"])
    levels = crowns.levels[k]
    cx = float(tree["x"]) + lift_cm * float(tree["axis_x"])
    cy = float(tree["y"]) + lift_cm * float(tree["axis_y"])
    c0 = max(int(np.floor((cx - reach_cm - x0_cm) / step_cm)), 0)
    c1 = min(int(np.ceil((cx + reach_cm - x0_cm) / step_cm)) + 1, cols)
    r0 = max(int(np.floor((cy - reach_cm - y0_cm) / step_cm)), 0)
    r1 = min(int(np.ceil((cy + reach_cm - y0_cm) / step_cm)) + 1, rows)
    if c0 >= c1 or r0 >= r1:
        return
    texel_cm = SPRITE_M * 100.0 * scale
    lv = int(np.clip(np.round(np.log2(max(step_cm / texel_cm, 1.0))), 0, len(levels) - 1))
    level = levels[lv]
    texel = np.float32(texel_cm * (1 << lv))
    ox, oy = crowns.origins[k]
    px = (x0_cm - cx + (np.arange(c0, c1, dtype=np.float32) + 0.5) * step_cm)[None, :]
    py = (y0_cm - cy + (np.arange(r0, r1, dtype=np.float32) + 0.5) * step_cm)[:, None]
    yaw = np.radians(float(tree["yaw"]))
    cos, sin = np.float32(np.cos(yaw)), np.float32(np.sin(yaw))
    u = (cos * px + sin * py - ox * scale) / texel
    v = (cos * py - sin * px - oy * scale) / texel
    h, w = level.shape[0] - 2, level.shape[1] - 2
    inside = (u > -0.5) & (u < w + 0.5) & (v > -0.5) & (v < h + 0.5)
    if not inside.any():
        return
    got = _bilinear(level, u[inside], v[inside])
    a = np.clip(got[:, _COVER], 0.0, 1.0)
    window = (slice(r0, r1), slice(c0, c1))
    under = cover[window][inside]
    cover[window][inside] = a + under * (1.0 - a)
    rgb[window][inside] = got[:, _RGB] + rgb[window][inside] * (1.0 - a)[:, None]
    sz = float(tree["scale_z"])
    dome[window][inside] = np.maximum(dome[window][inside], got[:, _DOME] * sz)
    rise = np.where(a >= COVER_TOP_MIN, got[:, _TOP] * sz * float(tree["axis_z"]), -np.inf)
    top[window][inside] = np.maximum(top[window][inside], float(tree["z"]) + rise)

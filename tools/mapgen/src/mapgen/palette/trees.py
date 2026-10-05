"""Trees laid over the finished painted pixel: the Titan forest's raster and per-tree crowns.

docs/spatial-and-map.md sections 30 and 36.
"""

from __future__ import annotations

import numpy as np

from mapgen.lighting.hillshade import sun_dot
from mapgen.palette.colour import flat_light, linear_from_oklab, oklab, unit_luminance

__all__ = ["over_crowns", "sample_titan", "titan_over"]


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
    lit = albedo * flat_light(p, sun_dot(surface, spacing_m), scene["ndl_flat"]) * p["exposure"]
    alpha = (opacity * np.clip(above, 0.0, 1.0))[..., None]
    return out * (1.0 - alpha) + lit * alpha


def over_crowns(out, crowns: dict, scene: dict, p: dict, ambient, exposure) -> np.ndarray:
    """Tree crowns over everything below them, lit by their own domes.

    A crown is hidden where the drawn surface stands above its top: a tree under an
    overhang, or rock the tree grows beside and below.
    """
    style = p["crowns"]
    cover = crowns["cover"]
    seen = (
        np.nan_to_num(crowns["top_cm"], nan=-1e9) / 100.0 > scene["z_m"] - style["hidden_below_m"]
    )
    wet = scene["water"]["cover"] * np.float32(1.0 - style["over_water"])
    alpha = (np.clip(cover, 0.0, 1.0) * style["opacity"] * seen * (1.0 - wet))[..., None]
    colour = crowns["rgb"] / np.maximum(cover, 1e-4)[..., None] * np.float32(style["darkening"])
    lab = oklab(np.clip(colour, 1e-7, None))
    lab[..., 1:] *= np.float32(p["chroma_gain"] * style["chroma"])
    colour = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    shade = np.clip(crowns["ndl"] / scene["ndl_flat"], *style["shade_clamp"])
    light = (
        ambient * unit_luminance(p["sky"])
        + (1 - ambient) * unit_luminance(p["sun"]) * shade[..., None]
    )
    return out * (1.0 - alpha) + colour * light * exposure * alpha

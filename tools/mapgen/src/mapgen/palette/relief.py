"""The relief styles: one painter, a light and a dark palette.

An OKLab elevation ramp over dry land, soft biome tints, slope rock, a hillshade that keeps flat
ground at its ramp colour and shifts hue into shadow, then flat depth-tinted water. Every number
is in the style's palette file; docs/spatial-and-map.md section 28 explains them.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.palette.painted.ground import linear_from_oklab, linear_to_srgb
from mapgen.palette.styles import dry_land_range, ramp_position
from mapgen.palette.water.shore import wet_mix
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["LUT_STEPS", "ReliefGround", "lch", "ramp_lut", "relief_colours", "water_tint_plane"]

#: Entries in the ramp's lookup table: far finer than one 8-bit step of lightness.
LUT_STEPS = 1024

#: Flat ground's n.L under a 45 degree sun: the shade term is measured from it.
FLAT_LIT = float(np.sin(np.deg2rad(45.0)))


def lch(values) -> np.ndarray:
    """OKLCh ``(L, C, h degrees)`` to OKLab, for one colour or an ``(N, 3)`` table."""
    v = np.asarray(values, np.float32)
    h = np.deg2rad(v[..., 2])
    return np.stack([v[..., 0], v[..., 1] * np.cos(h), v[..., 1] * np.sin(h)], -1).astype(
        np.float32
    )


def ramp_lut(stops, steps: int = LUT_STEPS) -> np.ndarray:
    """The stops joined in OKLab and re-spaced so equal steps of t are equal OKLab steps."""
    lab = lch(stops)
    dense = np.linspace(0.0, 1.0, 4096) * (len(lab) - 1)
    low = np.clip(np.floor(dense).astype(int), 0, len(lab) - 2)
    frac = (dense - low)[:, None]
    path = lab[low] * (1 - frac) + lab[low + 1] * frac
    path = ndimage.gaussian_filter1d(path, 60, axis=0, mode="nearest")
    arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    arc /= arc[-1]
    u = np.linspace(0.0, 1.0, steps)
    return np.stack([np.interp(u, arc, path[:, k]) for k in range(3)], -1).astype(np.float32)


def water_tint_plane(field, water: dict, planes=None, heights_dm=None) -> np.ndarray | None:
    """How far each 1 m water texel is towards the deep colour, blurred, as uint8.

    Measured water by ``1 - exp(-depth / tau)``; level-only water at ``level_only``. The blur is
    normalised by the wet mask, so a shore takes the colour of the water beside it.
    ``planes`` is ``(level_dm, grades)`` as drawn and ``heights_dm`` the ground the run draws
    (with the open sea's bed, ``palette.water.open_sea``); the field's own when None.
    """
    surface, grades = planes or (field.water_raster(), field.water_quality_raster())
    if surface is None or grades is None:
        return None
    wet = (grades != hf.WATER_DRY).astype(np.float32)
    ground = field.height_dm if heights_dm is None else heights_dm
    depth = (surface.astype(np.float32) - ground) / np.float32(hf.DM_PER_M)
    tau = np.float32(water["tau_m"])
    tint = np.where(
        grades == hf.WATER_MEASURED,
        1.0 - np.exp(-np.maximum(depth, 0.0) / tau),
        np.float32(water["level_only"]),
    ).astype(np.float32)
    sigma = float(water["blur_m"]) * 100.0 / field.spacing_cm
    tint = ndimage.gaussian_filter(tint * wet, sigma, mode="nearest")
    tint /= np.maximum(ndimage.gaussian_filter(wet, sigma, mode="nearest"), 1e-3)
    return (np.clip(tint, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _biome_planes(palette: dict, biome: dict, area_names: list[str]) -> np.ndarray | None:
    """``(dL·w, a·w, b·w, w)`` per biome texel, blurred like the satellite's biome colours."""
    tints = palette["biome_tints"]
    if not tints:
        return None
    table = np.zeros((len(area_names), 4), np.float32)
    for index, name in enumerate(area_names):
        dl, c, h, w = tints.get(name, palette["biome_fallback"])
        a, b = lch([0.0, c, h])[1:]
        table[index] = (dl * w, a * w, b * w, w)
    planes = np.empty((*biome["area"].shape, 4), np.float16)
    sigma = float(palette["biome_blend_texels"])
    for k in range(4):
        planes[..., k] = ndimage.gaussian_filter(table[:, k][biome["area"]], sigma, mode="nearest")
    return planes


class ReliefGround:
    """What a relief style samples per band, built once per run."""

    def __init__(self, palette: dict, field, biome, area_names: list[str], water=None, ground=None):
        p = self.palette = palette
        self.ramp = dry_land_range(field, p["ramp_lo_pct"], p["ramp_hi_pct"])
        self.lut = ramp_lut(p["ramp_lch"])
        self.biome = None if biome is None else _biome_planes(p, biome, area_names)
        self.water = water_tint_plane(field, p["water"], water, ground)
        w, s, rock = p["water"], p["shade"], p["rock"]
        self.shallow, self.deep = lch(w["shallow_lch"]), lch(w["deep_lch"])
        self.stroke = lch(w["stroke_lch"])
        self.cool, self.warm = lch([0.0, *s["cool"]])[1:], lch([0.0, *s["warm"]])[1:]
        self.rock_ab = lch([0.0, rock["c"], rock["h"]])[1:]


def _lambert(z_m, spacing_m: float, azimuth: float, altitude: float) -> np.ndarray:
    """n.L for one sun; rows run south, as ``lighting.hillshade.sun_dot``."""
    az, alt = np.deg2rad(azimuth), np.deg2rad(altitude)
    light = (np.cos(alt) * np.sin(az), -np.cos(alt) * np.cos(az), np.sin(alt))
    d_south, d_east = np.gradient(z_m, spacing_m)
    lit = (-d_east * light[0] - d_south * light[1] + light[2]) / np.sqrt(
        d_east * d_east + d_south * d_south + 1.0
    )
    return np.clip(lit, 0.0, 1.0).astype(np.float32)


def _shade(lab, z_m, spacing_m: float, ground: ReliefGround, unlit: bool = False):
    """Flat ground unchanged; shadow darker, cooler and greyer, sunlit slopes a little warm."""
    p = ground.palette["shade"]
    if unlit:
        lit = np.full(z_m.shape, FLAT_LIT, np.float32)
    else:
        lit = sum(w * _lambert(z_m, spacing_m, az, alt) for az, alt, w in p["suns"])
    d = (lit - FLAT_LIT).astype(np.float32)
    if p["mode"] == "add":
        lab[..., 0] += np.float32(p["k"]) * d
    else:
        lab[..., 0] *= np.clip(1.0 + np.float32(p["k"]) * d, p["lo"], p["hi"])
    lab[..., 1:] *= (1.0 + np.float32(p["dechroma"]) * np.minimum(d, 0.0))[..., None]
    shadow = np.clip(-d / FLAT_LIT, 0.0, 1.0)[..., None]
    sun = np.clip(d / (1.0 - FLAT_LIT), 0.0, 1.0)[..., None]
    lab[..., 1:] += ground.cool * shadow + ground.warm * sun
    lab[..., 0] = np.clip(lab[..., 0], 0.0, p["l_max"])
    return lab, lit


def _slope_rock(lab, z_m, spacing_m: float, ground: ReliefGround):
    rock = ground.palette["rock"]
    d_south, d_east = np.gradient(z_m, spacing_m)
    slope = np.degrees(np.arctan(np.hypot(d_east, d_south)))
    r = np.clip((slope - rock["lo_deg"]) / (rock["hi_deg"] - rock["lo_deg"]), 0.0, 1.0)
    r = (r * r * (3.0 - 2.0 * r) * np.float32(rock["max"]))[..., None]
    target = np.concatenate(
        [
            lab[..., :1] + np.float32(rock["dl"]),
            np.broadcast_to(ground.rock_ab, lab[..., 1:].shape),
        ],
        -1,
    )
    return lab * (1.0 - r) + target * r


def relief_colours(scene: dict, ground: ReliefGround, sample, sample_biome) -> np.ndarray:
    """One band, sRGB 0..255. ``sample(plane)`` resamples a 1 m plane onto the band and
    ``sample_biome(plane)`` picks the biome raster's texel under each pixel."""
    p, z_m, spacing_m = ground.palette, scene["z_m"], scene["spacing_m"]
    lo, hi, cdf = ground.ramp
    t = np.nan_to_num(ramp_position(z_m, lo, hi, cdf, p["ramp_equalised"]))
    lab = ground.lut[np.clip(np.rint(t * (LUT_STEPS - 1)), 0, LUT_STEPS - 1).astype(np.int64)]
    if ground.biome is not None:
        tint = sample_biome(ground.biome).astype(np.float32)
        lab[..., 0] += tint[..., 0]
        lab[..., 1:] = lab[..., 1:] * (1.0 - tint[..., 3:]) + tint[..., 1:3]
    lab = _slope_rock(lab, z_m, spacing_m, ground)
    lab, lit = _shade(lab, z_m, spacing_m, ground, scene.get("unlit", False))
    borrow = scene["borrow"] - 1.0
    borrow = np.where(borrow < 0.0, borrow * np.float32(p["borrow_ink_damp"]), borrow)
    lab[..., 0] *= np.cbrt(1.0 + borrow)
    return linear_to_srgb(np.clip(linear_from_oklab(_water(lab, scene, ground, sample, lit)), 0, 1))


def _water(land, scene: dict, ground: ReliefGround, sample, lit) -> np.ndarray:
    """Water over the ground in OKLab: a flat tint by depth, a shore stroke, a sea-edge fade."""
    w, water = ground.palette["water"], scene["water"]
    shore = ground.palette["shore"]
    if ground.water is None:
        tint = water["depth"]
    else:
        tint = sample(ground.water) / np.float32(255.0)
    colour = ground.shallow * (1.0 - tint[..., None]) + ground.deep * tint[..., None]
    colour[..., 0] *= 1.0 - np.float32(w["lit"]) + np.float32(w["lit"]) * lit / FLAT_LIT
    ocean = water["ocean"]
    fade = 1.0 - np.exp(-water["depth_m"] / np.float32(shore["clarity_m"]))
    a0 = np.float32(shore["edge_alpha"])
    opacity = (ocean * (a0 + (1.0 - a0) * fade) + (1.0 - ocean))[..., None]
    cover = np.clip(water["cover"], 0.0, 1.0)
    edge = (np.clip(4.0 * cover * (1.0 - cover), 0.0, 1.0) ** 1.5) * np.float32(w["stroke"])
    colour = colour * (1.0 - edge[..., None]) + ground.stroke * edge[..., None]
    under = land * (1.0 - opacity) + colour * opacity
    return wet_mix(land, under, np.clip(cover + 0.5 * edge, 0.0, 1.0)[..., None])

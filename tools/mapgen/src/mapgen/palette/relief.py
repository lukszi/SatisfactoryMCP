"""The relief styles: one painter, a light and a dark palette.

An OKLab elevation ramp over dry land, soft biome tints, slope rock, a hillshade that keeps flat
ground at its ramp colour and shifts hue into shadow, then flat depth-tinted water. Every number
is in the style's palette file; docs/map/painted.md section 28 explains them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeAlias

import numpy as np
from scipy import ndimage

from mapgen.colour import linear_from_oklab, linear_to_srgb
from mapgen.gamedata.ground.biome import BiomeRaster
from mapgen.jit import kernels_on
from mapgen.lighting.hillshade import slope_degrees, sun_dot
from mapgen.palette.scene import FloatGrid, ReliefScene, WaterPlanes, field_heights
from mapgen.palette.schema import ReliefPalette, ReliefWaterStyle
from mapgen.palette.styles import dry_land_range, ramp_position
from mapgen.palette.water.shore import WET_MIX_MOST, wet_mix
from mapgen.palette.water.wet import WetPixels, float32_planes
from satisfactory_mcp.core.arrays import F16Grid, F32Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "LUT_STEPS",
    "BiomeSample",
    "ReliefGround",
    "TintSample",
    "oklab_from_lch",
    "ramp_lut",
    "relief_colours",
    "water_tint_plane",
]

#: Entries in the ramp's lookup table: far finer than one 8-bit step of lightness.
LUT_STEPS = 1024

#: Flat ground's n.L under a 45 degree sun: the shade term is measured from it.
FLAT_LIT = float(np.sin(np.deg2rad(45.0)))

#: The water tint plane read onto the band's pixels.
TintSample: TypeAlias = Callable[[U8Grid], FloatGrid]

#: The biome tint planes' texel under each of the band's pixels.
BiomeSample: TypeAlias = Callable[[F16Grid], FloatGrid]


def oklab_from_lch(values: Sequence[float] | Sequence[Sequence[float]]) -> F32Grid:
    """OKLCh ``(L, C, h degrees)`` to OKLab, for one colour or an ``(N, 3)`` table."""
    lch = np.asarray(values, np.float32)
    hue = np.deg2rad(lch[..., 2])
    return np.stack([lch[..., 0], lch[..., 1] * np.cos(hue), lch[..., 1] * np.sin(hue)], -1).astype(
        np.float32
    )


def ramp_lut(stops: Sequence[Sequence[float]], steps: int = LUT_STEPS) -> F32Grid:
    """The stops joined in OKLab and re-spaced so equal steps of t are equal OKLab steps."""
    lab = oklab_from_lch(stops)
    dense = np.linspace(0.0, 1.0, 4096) * (len(lab) - 1)
    low = np.clip(np.floor(dense).astype(int), 0, len(lab) - 2)
    frac = (dense - low)[:, None]
    path = lab[low] * (1 - frac) + lab[low + 1] * frac
    path = ndimage.gaussian_filter1d(path, 60, axis=0, mode="nearest")
    arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    arc /= arc[-1]
    even = np.linspace(0.0, 1.0, steps)
    return np.stack([np.interp(even, arc, path[:, k]) for k in range(3)], -1).astype(np.float32)


def water_tint_plane(
    field: hf.Field,
    water: ReliefWaterStyle,
    planes: WaterPlanes | None = None,
    heights_dm: FloatGrid | None = None,
) -> U8Grid | None:
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
    ground = field_heights(field) if heights_dm is None else heights_dm
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


def _biome_planes(
    palette: ReliefPalette, biome: BiomeRaster, area_names: list[str]
) -> F16Grid | None:
    """``(dL·w, a·w, b·w, w)`` per biome texel, blurred like the satellite's biome colours."""
    tints = palette["biome_tints"]
    if not tints:
        return None
    table = np.zeros((len(area_names), 4), np.float32)
    for index, name in enumerate(area_names):
        dl, chroma, hue, weight = tints.get(name, palette["biome_fallback"])
        a, b = oklab_from_lch([0.0, chroma, hue])[1:]
        table[index] = (dl * weight, a * weight, b * weight, weight)
    area = biome["area"]
    planes = np.empty((*area.shape, 4), np.float16)
    sigma = float(palette["biome_blend_texels"])
    for k in range(4):
        planes[..., k] = ndimage.gaussian_filter(table[:, k][area], sigma, mode="nearest")
    return planes


class ReliefGround:
    """What a relief style samples per band, built once per run."""

    def __init__(
        self,
        palette: ReliefPalette,
        field: hf.Field,
        biome: BiomeRaster | None,
        area_names: list[str],
        water: WaterPlanes | None = None,
        ground: FloatGrid | None = None,
    ) -> None:
        self.palette = palette
        self.ramp = dry_land_range(field, palette["ramp_lo_pct"], palette["ramp_hi_pct"])
        self.lut = ramp_lut(palette["ramp_lch"])
        self.biome = None if biome is None else _biome_planes(palette, biome, area_names)
        self.water = water_tint_plane(field, palette["water"], water, ground)
        water_style, shade, rock = palette["water"], palette["shade"], palette["rock"]
        self.shallow = oklab_from_lch(water_style["shallow_lch"])
        self.deep = oklab_from_lch(water_style["deep_lch"])
        self.stroke = oklab_from_lch(water_style["stroke_lch"])
        self.cool = oklab_from_lch([0.0, *shade["cool"]])[1:]
        self.warm = oklab_from_lch([0.0, *shade["warm"]])[1:]
        self.rock_ab = oklab_from_lch([0.0, rock["c"], rock["h"]])[1:]


def _shade(
    lab: FloatGrid, z_m: FloatGrid, spacing_m: float, ground: ReliefGround, unlit: bool = False
) -> tuple[FloatGrid, FloatGrid]:
    """Flat ground unchanged; shadow darker, cooler and greyer, sunlit slopes a little warm."""
    shade = ground.palette["shade"]
    if unlit:
        lit: FloatGrid = np.full(z_m.shape, FLAT_LIT, np.float32)
    else:
        lit = np.zeros(z_m.shape, np.float32)
        for azimuth, altitude, weight in shade["suns"]:
            lit = lit + weight * sun_dot(z_m, spacing_m, azimuth, altitude)
    excess = (lit - FLAT_LIT).astype(np.float32)
    if shade["mode"] == "add":
        lab[..., 0] += np.float32(shade["k"]) * excess
    else:
        lab[..., 0] *= np.clip(1.0 + np.float32(shade["k"]) * excess, shade["lo"], shade["hi"])
    lab[..., 1:] *= (1.0 + np.float32(shade["dechroma"]) * np.minimum(excess, 0.0))[..., None]
    shadow = np.clip(-excess / FLAT_LIT, 0.0, 1.0)[..., None]
    sun = np.clip(excess / (1.0 - FLAT_LIT), 0.0, 1.0)[..., None]
    lab[..., 1:] += ground.cool * shadow + ground.warm * sun
    lab[..., 0] = np.clip(lab[..., 0], 0.0, shade["l_max"])
    return lab, lit


def _slope_rock(
    lab: FloatGrid, z_m: FloatGrid, spacing_m: float, ground: ReliefGround
) -> FloatGrid:
    """Rock's colour taking over the ramp's on steep ground, smoothly between its two slopes."""
    rock = ground.palette["rock"]
    slope = slope_degrees(z_m, spacing_m)
    share = np.clip((slope - rock["lo_deg"]) / (rock["hi_deg"] - rock["lo_deg"]), 0.0, 1.0)
    share = (share * share * (3.0 - 2.0 * share) * np.float32(rock["max"]))[..., None]
    target = np.concatenate(
        [
            lab[..., :1] + np.float32(rock["dl"]),
            np.broadcast_to(ground.rock_ab, lab[..., 1:].shape),
        ],
        -1,
    )
    return lab * (1.0 - share) + target * share


def relief_colours(
    scene: ReliefScene, ground: ReliefGround, sample: TintSample, sample_biome: BiomeSample
) -> FloatGrid:
    """One band, sRGB 0..255. ``sample(plane)`` resamples a 1 m plane onto the band and
    ``sample_biome(plane)`` picks the biome raster's texel under each pixel."""
    palette, z_m, spacing_m = ground.palette, scene["z_m"], scene["spacing_m"]
    lo, hi, cdf = ground.ramp
    position = np.nan_to_num(ramp_position(z_m, lo, hi, cdf, palette["ramp_equalised"]))
    step = np.clip(np.rint(position * (LUT_STEPS - 1)), 0, LUT_STEPS - 1).astype(np.int64)
    lab = ground.lut[step]
    if ground.biome is not None:
        tint = sample_biome(ground.biome).astype(np.float32)
        lab[..., 0] += tint[..., 0]
        lab[..., 1:] = lab[..., 1:] * (1.0 - tint[..., 3:]) + tint[..., 1:3]
    lab = _slope_rock(lab, z_m, spacing_m, ground)
    lab, lit = _shade(lab, z_m, spacing_m, ground, scene.get("unlit", False))
    borrow = scene["borrow"] - 1.0
    borrow = np.where(borrow < 0.0, borrow * np.float32(palette["borrow_ink_damp"]), borrow)
    lab[..., 0] *= np.cbrt(1.0 + borrow)
    return linear_to_srgb(np.clip(linear_from_oklab(_water(lab, scene, ground, sample, lit)), 0, 1))


def _water(
    land: FloatGrid, scene: ReliefScene, ground: ReliefGround, sample: TintSample, lit: FloatGrid
) -> FloatGrid:
    """Water over the ground in OKLab, worked out on the wet pixels only (``water.wet``): the
    mix's weight is 0 wherever the cover is."""
    water = scene["water"]
    tint = water["depth"] if ground.water is None else sample(ground.water) / np.float32(255.0)
    planes = (water["cover"], water["depth_m"], water["ocean"], tint, lit)
    wet = WetPixels(water["cover"])
    if kernels_on() and float32_planes(land, *planes):
        return _water_compiled(land, planes, wet, ground)
    if wet.whole:
        return wet_mix(land, *_water_over(land, planes, ground))
    wet_land = wet.take(land)
    under, weight = _water_over(wet_land, tuple(wet.take(plane) for plane in planes), ground)
    return wet.mix(land, wet_land, under, weight)


def _water_over(
    land: FloatGrid, planes: tuple[FloatGrid, ...], ground: ReliefGround
) -> tuple[FloatGrid, FloatGrid]:
    """A flat tint by depth, a shore stroke and a sea-edge fade over ``land``, per pixel, and
    the weight it is mixed in by, with the trailing channel axis. ``planes`` is ``(cover,
    depth_m, ocean, tint, lit)``."""
    cover, depth_m, ocean, tint, lit = planes
    water_style, shore = ground.palette["water"], ground.palette["shore"]
    colour = ground.shallow * (1.0 - tint[..., None]) + ground.deep * tint[..., None]
    sunlit = np.float32(water_style["lit"])
    colour[..., 0] *= 1.0 - sunlit + sunlit * lit / FLAT_LIT
    fade = 1.0 - np.exp(-depth_m / np.float32(shore["clarity_m"]))
    edge_alpha = np.float32(shore["edge_alpha"])
    opacity = (ocean * (edge_alpha + (1.0 - edge_alpha) * fade) + (1.0 - ocean))[..., None]
    cover = np.clip(cover, 0.0, 1.0)
    edge = (np.clip(4.0 * cover * (1.0 - cover), 0.0, 1.0) ** 1.5) * np.float32(
        water_style["stroke"]
    )
    colour = colour * (1.0 - edge[..., None]) + ground.stroke * edge[..., None]
    under = land * (1.0 - opacity) + colour * opacity
    return under, np.clip(cover + 0.5 * edge, 0.0, 1.0)[..., None]


def _water_compiled(
    land: F32Grid, planes: tuple[F32Grid, ...], wet: WetPixels, ground: ReliefGround
) -> F32Grid:
    """``_water`` by the kernel, on the pixels it works on; the fade's ``exp`` and the stroke's
    power are worked out here, by numpy, on those pixels."""
    from mapgen.palette.water import kernels

    cover, depth_m, ocean, tint, lit = planes
    water_style, shore = ground.palette["water"], ground.palette["shore"]
    if wet.whole:
        index, depth, shares = np.arange(cover.size), depth_m.ravel(), cover.ravel()
    else:
        index, depth, shares = wet.index, wet.take(depth_m), wet.take(cover)
    transmit = np.exp(-depth / np.float32(shore["clarity_m"]))
    shares = np.clip(shares, 0.0, 1.0)
    # 4 c (1 - c) is +0 at a cover of 0 or 1, and so is its power: worked out elsewhere alone.
    curve = np.zeros(shares.shape, np.float32)
    part = ~((shares <= 0.0) | (shares >= 1.0))
    curve[part] = np.clip(4.0 * shares[part] * (1.0 - shares[part]), 0.0, 1.0) ** 1.5
    knobs = kernels.ReliefKnobs(
        sunlit=np.float32(water_style["lit"]),
        flat_lit=np.float32(FLAT_LIT),
        edge_alpha=np.float32(shore["edge_alpha"]),
        stroke_weight=np.float32(water_style["stroke"]),
    )
    style = kernels.ReliefStyle(ground.shallow, ground.deep, ground.stroke, knobs)
    flat = kernels.ReliefPlanes(*(plane.reshape(-1) for plane in (cover, ocean, tint, lit)))
    terms = kernels.ReliefTerms(transmit, curve)
    out = kernels.relief_water(
        land.reshape(-1, 3), flat, index, terms, wet.whole, WET_MIX_MOST, style
    )
    return out.reshape(land.shape)

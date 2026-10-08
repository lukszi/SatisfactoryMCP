"""One band of the game-painted style, drawn over ``ground.PaintedGround``.

The ground under its canopy, rock and meshes; a sky-and-sun light, an exposure gain with a soft
shoulder; Beer-Lambert water over a seabed with the coral carpet and sunk crowns; the crowns and
Titan trees over it all. docs/map/painted.md sections 27, 30 and 32, docs/map/calibration.md
section 31 and docs/map/light-and-crowns.md section 36.
"""

from __future__ import annotations

import numpy as np

from mapgen.colour import by_luminance, flat_light, linear_from_oklab, linear_to_srgb, oklab, tone
from mapgen.lighting.hillshade import FLAT_SUN_DOT, SUN_ALTITUDE_DEG, SUN_AZIMUTH_DEG, sun_dot
from mapgen.lighting.model import surface_direct
from mapgen.lighting.sun import sun_vector
from mapgen.palette.painted.calibration import exposure_gain, sampled_rgb
from mapgen.palette.painted.optics import mix_underwater
from mapgen.palette.painted.shapes import (
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    Sampler,
)
from mapgen.palette.painted.surfaces import (
    canopy_over_rock,
    cliff_layer,
    mesh_surface,
    rock_surface,
    sunk_specks,
)
from mapgen.palette.painted.trees import lit_crowns, over_crowns, titan_over
from mapgen.palette.styles import ramp_position
from mapgen.palette.water.shore import add_foam, inland_cover, seabed_keeps, wet_band
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid

__all__ = ["painted_colours", "painted_ndl"]

_ZERO, _ONE = np.float32(0.0), np.float32(1.0)


def painted_colours(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> FloatGrid:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    water = inland_cover(sunk_specks(scene), ground.palette["shore"].get("inland"))
    band: PaintedScene = {**scene, "water": water}
    g = _ground_colour(band, ground, sample, sample_rock)
    out = _lit_and_wet(g, band, ground, sample, sample_rock)
    return _toned(out, ground.palette)


def _ground_colour(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> FloatGrid:
    """The band's ground before light: paint with its layers' detail (else its Cliff layer's
    look) under canopy, rock, the meshes, then the style's chroma gain and altitude lift."""
    palette = ground.palette
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    detail, style = scene.get("detail"), palette.get("ground_detail")
    if detail is not None and style is not None:
        strength = np.float32(style["strength"])
        albedo = albedo * np.maximum(_ONE + strength * (detail.ratio - _ONE), _ZERO)
    else:
        albedo = cliff_layer(albedo, scene, ground, sample)
    kept = 1.0 if scene.get("crowns") is None else palette["crowns"]["canopy_kept"]
    gain = palette["canopy_gain"] * kept
    canopy = np.clip(sample(ground.canopy) / 255.0 * gain, 0.0, 1.0)[..., None]
    canopy_rgb = sampled_rgb(ground.canopy_rgb, sample_rock)
    g = albedo * (1.0 - canopy) + canopy_rgb * canopy
    area_rock = np.stack([sample_rock(plane) for plane in ground.rock], -1)
    rock = scene["rock_weight"][..., None]
    drawn = rock_surface(area_rock, scene, ground, sample_rock, pick=scene["rock_weight"] > 0)
    g = g * (1.0 - rock) + drawn * rock
    g = canopy_over_rock(g, canopy, rock, scene, ground, sample, canopy_rgb)
    g = mesh_surface(g, area_rock, scene, ground, sample_rock)

    lab = oklab(np.clip(g, 1e-7, None))
    lab[..., 1:] *= np.float32(palette["chroma_gain"])
    lo, hi, cdf = ground.ramp
    lab[..., 0] += np.float32(palette["altitude_lift"]) * ramp_position(
        scene["z_m"], lo, hi, cdf, palette["ramp_equalised"]
    )
    return np.clip(linear_from_oklab(lab), 0.0, 1.0)


def _lit_and_wet(
    g: FloatGrid,
    scene: PaintedScene,
    ground: PaintedSurface,
    sample: Sampler,
    sample_rock: Sampler,
) -> FloatGrid:
    """The ground lit and exposed, under its water, crowns and Titan trees, in linear light."""
    palette = ground.palette
    borrow = scene["borrow"]
    damp = np.float32(palette["borrow_ink_damp"])
    borrow = np.where(borrow < 1.0, 1.0 + (borrow - 1.0) * damp, borrow)
    light = flat_light(palette, scene["ndl"], scene["ndl_flat"])
    exposure = exposure_gain(palette)
    lit = g * light * (exposure * borrow)[..., None]

    water, shore = scene["water"], palette["shore"]
    crowns = lit_crowns(scene, ground, sample_rock, exposure)
    lit = wet_band(lit, water, shore.get("wet_band"))
    out = mix_underwater(lit, g, scene, ground, sample, sample_rock, exposure, crowns)
    stroke = np.float32(shore.get("stroke", 0.0))
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, shore.get("foam"), np.float32(1.0))
    if crowns is not None:
        out = over_crowns(out, crowns)
    return titan_over(out, scene, ground)


def _toned(out: FloatGrid, palette: PaintedPalette) -> FloatGrid:
    """Linear light through the style's luminance shoulder, the shader's tone, as sRGB 0..255."""
    curve = palette["tone"]
    return linear_to_srgb(by_luminance(out, tone, curve["knee"], curve["white"]))


def painted_ndl(
    z_m: FloatGrid,
    spacing_m: float,
    unlit: bool,
    meshes: tuple[FloatGrid | None, U8Grid | None, FloatGrid, BoolMask | None],
    bumps: F32Grid | None = None,
) -> FloatGrid:
    """The painted style's sun term, ``n.L`` against the flat ``sin 45``: the north-west
    hillshade when lit, with the ground's detail normal ``bumps`` added to the slope where
    there is one; flat when unlit. Unlit, a sea mesh only this style draws keeps the default
    sun on its top: the light, captured under the seabed rule, has water there. ``meshes``
    is ``(weight, kept class, water level, footprint on land)``.
    """
    if not unlit:
        return sun_dot(z_m, spacing_m) if bumps is None else _bumped_sun_dot(z_m, spacing_m, bumps)
    flat = np.full(z_m.shape, FLAT_SUN_DOT, np.float32)
    weight, kept, level, land = meshes
    if weight is None or kept is None:
        return flat
    sea = np.where((kept > 0) & ~seabed_keeps(kept, z_m, level, land), weight, np.float32(0.0))
    return flat * (1.0 + sea * (surface_direct(z_m, spacing_m) - 1.0))


def _bumped_sun_dot(z_m: FloatGrid, spacing_m: float, bumps: F32Grid) -> FloatGrid:
    """``hillshade.sun_dot`` of the surface whose normal has ``bumps`` (east and south) added
    to its own, renormalised, as the light adds them to its normal tiles."""
    light = np.array(sun_vector(SUN_AZIMUTH_DEG, SUN_ALTITUDE_DEG), np.float32)
    d_south, d_east = np.gradient(z_m, spacing_m)
    up = 1.0 / np.sqrt(d_east * d_east + d_south * d_south + 1.0)
    east, south = -d_east * up + bumps[..., 0], -d_south * up + bumps[..., 1]
    length = np.sqrt(east * east + south * south + up * up)
    return np.clip((east * light[0] + south * light[1] + up * light[2]) / length, 0.0, 1.0)

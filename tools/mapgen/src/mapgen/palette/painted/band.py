"""One band of the game-painted style, drawn over ``ground.PaintedGround``.

The ground under its canopy, rock and meshes; a sky-and-sun light, an exposure gain with a soft
shoulder; Beer-Lambert water over a seabed with the coral carpet and sunk crowns; the crowns and
Titan trees over it all. docs/spatial-and-map.md sections 27, 30 to 32 and 36.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import numpy as np

from mapgen.colour import LUMA, flat_light, linear_from_oklab, linear_to_srgb, oklab
from mapgen.lighting.hillshade import FLAT_SUN_DOT, sun_dot
from mapgen.lighting.model import surface_direct
from mapgen.palette.painted.calibration import exposure_gain, sampled_rgb, tone
from mapgen.palette.painted.optics import underwater
from mapgen.palette.painted.shapes import (
    FloatGrid,
    PaintedPalette,
    PaintedScene,
    PaintedSurface,
    Sampler,
)
from mapgen.palette.painted.surfaces import (
    canopy_over_rock,
    mesh_surface,
    rock_surface,
    sunk_specks,
)
from mapgen.palette.painted.trees import lit_crowns, over_crowns, titan_over
from mapgen.palette.styles import ramp_position
from mapgen.palette.water.shore import add_foam, seabed_keeps, wet_band, wet_mix
from satisfactory_mcp.core.arrays import U8Grid

__all__ = ["painted_colours", "painted_ndl"]


def painted_colours(
    scene: Mapping[str, object], ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> FloatGrid:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    # render.compose builds the scene as a plain dict.
    given = cast(PaintedScene, scene)
    band: PaintedScene = {**given, "water": sunk_specks(given)}
    g = _ground_colour(band, ground, sample, sample_rock)
    out = _lit_and_wet(g, band, ground, sample, sample_rock)
    return _toned(out, ground.palette)


def _ground_colour(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> FloatGrid:
    """The band's ground before light: paint under canopy, rock, the meshes, then the
    style's chroma gain and altitude lift."""
    palette = ground.palette
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    kept = 1.0 if scene.get("crowns") is None else palette["crowns"]["canopy_kept"]
    gain = palette["canopy_gain"] * kept
    canopy = np.clip(sample(ground.canopy) / 255.0 * gain, 0.0, 1.0)[..., None]
    canopy_rgb = sampled_rgb(ground.canopy_rgb, sample_rock)
    g = albedo * (1.0 - canopy) + canopy_rgb * canopy
    area_rock = np.stack([sample_rock(plane) for plane in ground.rock], -1)
    rock = scene["rock_weight"][..., None]
    g = g * (1.0 - rock) + rock_surface(area_rock, scene, ground, sample_rock) * rock
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
    light = flat_light(cast("dict[str, object]", palette), scene["ndl"], scene["ndl_flat"])
    exposure = exposure_gain(palette)
    lit = g * light * (exposure * borrow)[..., None]

    water, shore = scene["water"], palette["shore"]
    crowns = lit_crowns(scene, ground, sample_rock, exposure)
    lit = wet_band(lit, water, shore.get("wet_band"))
    under = underwater(g, scene, ground, sample, sample_rock, exposure, crowns)
    out = wet_mix(lit, under, water["cover"][..., None])
    stroke = np.float32(shore.get("stroke", 0.0))
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, shore.get("foam"), np.float32(1.0))
    if crowns is not None:
        out = over_crowns(out, crowns)
    return titan_over(out, scene, ground)


def _toned(out: FloatGrid, palette: PaintedPalette) -> FloatGrid:
    """Linear light through the style's luminance shoulder, as sRGB 0..255."""
    y = np.maximum(out @ LUMA, 1e-7)
    curve = palette["tone"]
    out = out * (tone(y, curve["knee"], curve["white"]) / y)[..., None]
    return linear_to_srgb(out)


def painted_ndl(
    z_m: FloatGrid,
    spacing_m: float,
    unlit: bool,
    surface: object | None,
    meshes: tuple[FloatGrid | None, U8Grid | None, FloatGrid],
) -> FloatGrid:
    """The painted style's sun term, ``n.L`` against the flat ``sin 45``: the north-west
    hillshade when lit, flat when unlit. Unlit, a sea mesh only this style draws keeps the
    default sun on its top while another layer captures the light (``surface`` None).
    ``meshes`` is ``(weight, kept class, water level)``.
    """
    if not unlit:
        return sun_dot(z_m, spacing_m)
    flat = np.full(z_m.shape, FLAT_SUN_DOT, np.float32)
    weight, kept, level = meshes
    if surface is not None or weight is None or kept is None:
        return flat
    sea = np.where((kept > 0) & ~seabed_keeps(kept, z_m, level), weight, np.float32(0.0))
    return flat * (1.0 + sea * (surface_direct(z_m, spacing_m) - 1.0))

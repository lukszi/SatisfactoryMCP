"""One band of the game-painted style, drawn over ``ground.PaintedGround``.

The ground under its canopy, rock and meshes; a sky-and-sun light, an exposure gain with a soft
shoulder; Beer-Lambert water over a seabed with the coral carpet and sunk crowns; the crowns and
Titan trees over it all, or apart from it (``painted_parts``). docs/map/painted.md sections 27,
30 and 32, docs/map/calibration.md section 31 and docs/map/light-and-crowns.md section 36.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.colour import by_luminance, flat_light, linear_from_oklab, linear_to_srgb, oklab, tone
from mapgen.lighting.hillshade import FLAT_SUN_DOT, sun_dot
from mapgen.lighting.model import surface_direct
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
    mesh_surface,
    rock_surface,
    sunk_specks,
)
from mapgen.palette.painted.trees import (
    TreeOver,
    crown_over,
    folded_trees,
    lay_over,
    lit_crowns,
    titan_layer,
)
from mapgen.palette.styles import ramp_position
from mapgen.palette.water.shore import add_foam, inland_cover, seabed_keeps, wet_band
from satisfactory_mcp.core.arrays import BoolMask, U8Grid

__all__ = ["PaintedParts", "painted_colours", "painted_ndl", "painted_parts"]


class PaintedParts(NamedTuple):
    """A band of the painted layer and its two parts, sRGB 0..255: the picture, the ground
    without the trees, and the trees' colour, with their ``alpha`` 0..1 (0 where none is)."""

    colour: FloatGrid
    ground: FloatGrid
    trees: FloatGrid
    alpha: FloatGrid


def painted_colours(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> FloatGrid:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    under, trees = _band(scene, ground, sample, sample_rock)
    return _toned(_with_trees(under, trees), ground.palette)


def painted_parts(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> PaintedParts:
    """``painted_colours`` and its parts at the trees, the crowns and the Titan trees laid
    last: the trees folded into one layer, which laid over the ground gives the picture.
    docs/map/light-and-crowns.md section 36, "Trees apart"."""
    under, trees = _band(scene, ground, sample, sample_rock)
    alpha, colour = folded_trees(under.shape, trees)
    palette, out = ground.palette, _with_trees(under, trees)
    # In the picture's float type, so a pixel no tree covers is its bytes.
    bare = _toned(under.astype(out.dtype, copy=False), palette)
    return PaintedParts(_toned(out, palette), bare, _toned(colour, palette), alpha)


def _band(
    scene: PaintedScene, ground: PaintedSurface, sample: Sampler, sample_rock: Sampler
) -> tuple[FloatGrid, list[TreeOver]]:
    """The band in linear light under its trees, and the trees laid over it in order."""
    water = inland_cover(sunk_specks(scene), ground.palette["shore"].get("inland"))
    band: PaintedScene = {**scene, "water": water}
    g = _ground_colour(band, ground, sample, sample_rock)
    return _lit_and_wet(g, band, ground, sample, sample_rock)


def _with_trees(under: FloatGrid, trees: list[TreeOver]) -> FloatGrid:
    out = under
    for layer in trees:
        out = lay_over(out, layer)
    return out


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
) -> tuple[FloatGrid, list[TreeOver]]:
    """The ground lit and exposed under its water, in linear light, and what is laid over it
    last: the crowns that stand out of the water, then the Titan trees."""
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
    trees = [] if crowns is None else [crown_over(crowns)]
    titan = titan_layer(scene, ground)
    return out, trees if titan is None else [*trees, titan]


def _toned(out: FloatGrid, palette: PaintedPalette) -> FloatGrid:
    """Linear light through the style's luminance shoulder, the shader's tone, as sRGB 0..255."""
    curve = palette["tone"]
    return linear_to_srgb(by_luminance(out, tone, curve["knee"], curve["white"]))


def painted_ndl(
    z_m: FloatGrid,
    spacing_m: float,
    unlit: bool,
    meshes: tuple[FloatGrid | None, U8Grid | None, FloatGrid, BoolMask | None],
) -> FloatGrid:
    """The painted style's sun term, ``n.L`` against the flat ``sin 45``: the north-west
    hillshade when lit, flat when unlit. Unlit, a sea mesh only this style draws keeps the
    default sun on its top: the light, captured under the seabed rule, has water there.
    ``meshes`` is ``(weight, kept class, water level, footprint on land)``.
    """
    if not unlit:
        return sun_dot(z_m, spacing_m)
    flat = np.full(z_m.shape, FLAT_SUN_DOT, np.float32)
    weight, kept, level, land = meshes
    if weight is None or kept is None:
        return flat
    sea = np.where((kept > 0) & ~seabed_keeps(kept, z_m, level, land), weight, np.float32(0.0))
    return flat * (1.0 + sea * (surface_direct(z_m, spacing_m) - 1.0))

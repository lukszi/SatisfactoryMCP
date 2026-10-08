"""The light model: Lambert, faded cast shadows and a sky-view term, applied to unlit colour.

The page's shader runs the same arithmetic per pixel; this module is its reference, the
baked fallback, and the provenance the light axis records. docs/map/light-and-crowns.md
section 29 says why each constant has its value.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.colour import (
    by_luminance,
    colour_at,
    linear_to_srgb_unit,
    number_at,
    sky_sun_light,
    srgb_unit_to_linear,
    tone,
    unit_luminance,
    untone,
)
from mapgen.lighting.hillshade import sun_dot
from mapgen.lighting.horizon import (
    FADE_M,
    HORIZON_DIRS,
    OCCLUDER_FADE_M,
    SKY_RADIUS_M,
    decode_horizon,
)
from mapgen.lighting.spans.march import span_block
from mapgen.lighting.sun import DEFAULT_SUN, NOON_HOUR, Sun, sun_vector
from mapgen.lighting.undersides import CROWN_UNDERSIDE, LEAF_LOW_SHARE, TITAN_SLAB_M
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.versions import LIGHTS
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "AO_CELL",
    "AO_DEPTH",
    "AO_SCALES_M",
    "AO_STRENGTH",
    "AO_WEIGHTS",
    "CROWN_CELL",
    "DIRECT_SCALE",
    "HORIZON_CELLS",
    "HZ_CELLS",
    "HZ_GUTTER_PX",
    "LIGHT_ID",
    "NORMALISE_MIN_EL_DEG",
    "SHADOW_FILL",
    "SHADOW_FLOOR",
    "SHADOW_FLOOR_KNEE",
    "SHADOW_SOFT_DEG",
    "TITAN_CELL",
    "TITAN_FADE_M",
    "Horizons",
    "LightBlock",
    "LightParams",
    "LightSpace",
    "apply_terms",
    "direct_term",
    "light_axis",
    "light_params",
    "model_block",
    "relight",
    "shaded_direct",
    "sun_cells",
    "sun_horizon",
    "surface_direct",
]

LIGHT_ID = "sun"

#: The atlas's first crown cell, first Titan tree cell and the ambient occlusion cell: the
#: ground's horizons, then the crowns' and the Titan trees' each alone, then what the trees
#: take off the sky light. Cells per tile, and the horizons among them.
CROWN_CELL = HORIZON_DIRS
TITAN_CELL = 2 * HORIZON_DIRS
AO_CELL = 3 * HORIZON_DIRS
HORIZON_CELLS = AO_CELL
HZ_CELLS = AO_CELL + 1

#: The Titan trees' fade: the ground's, for a canopy tens of metres up.
TITAN_FADE_M = FADE_M

#: Ambient occlusion (``lighting/occlusion.py``): the half-widths of the boxes it reads,
#: metres, and their weights; how many half-widths over a pixel a box's mean occludes it
#: fully; and the most of the sky it takes.
AO_SCALES_M = (1.0, 3.0, 8.0)
AO_WEIGHTS = (0.3, 0.4, 0.3)
AO_DEPTH = 1.5
AO_STRENGTH = 0.55

#: Each atlas cell sits in a border of its own edge texels this wide, so a lossy codec's blur
#: across the cell's edge lands on copies (section 29, "What is written").
HZ_GUTTER_PX = 16

#: Below this elevation the sun term is normalised as if the sun stood here.
NORMALISE_MIN_EL_DEG = 35.0

#: Degrees over which a horizon goes from lit to shadowed.
SHADOW_SOFT_DEG = 6.0

#: The share of the sun's Lambert term a cast shadow keeps: sky and bounce from the sun's side.
SHADOW_FILL = 0.35

#: The darkest light, reached through a soft knee rather than a hard clip.
SHADOW_FLOOR = 0.36
SHADOW_FLOOR_KNEE = 0.1

#: The stored direct term is ``direct * DIRECT_SCALE`` as a byte.
DIRECT_SCALE = 127.0

#: Horizon planes in degrees, one per atlas cell: a ``(cells, h, w)`` stack or a sequence.
Horizons: TypeAlias = F32Grid | Sequence[F32Grid]

#: Where a style multiplies its light in: linear light under its tone curve, or sRGB.
LightSpace: TypeAlias = Literal["linear", "srgb"]

#: A light block as it arrives: ``shader_light``'s, or one read back from a sidecar.
LightBlock: TypeAlias = Mapping[str, object]


class LightParams(TypedDict):
    """``shader_light``'s block, checked; a tone knee of 1 is no tone curve."""

    space: LightSpace
    ambient: float
    sky: list[float]
    sun: list[float]
    tone_knee: float
    tone_white: float
    crowns: bool


def light_params(block: LightBlock) -> LightParams:
    """``block`` read key by key; ``TypeError`` names the first value of the wrong kind."""
    space: LightSpace
    if block["space"] == "linear":
        space = "linear"
    elif block["space"] == "srgb":
        space = "srgb"
    else:
        raise TypeError(f"space is {block['space']!r}, not linear or srgb")
    return {
        "space": space,
        "ambient": number_at(block, "ambient"),
        "sky": colour_at(block, "sky"),
        "sun": colour_at(block, "sun"),
        "tone_knee": number_at(block, "tone_knee"),
        "tone_white": number_at(block, "tone_white"),
        "crowns": bool(block.get("crowns")),
    }


def model_block() -> JsonObject:
    """Every constant the shader reads, as the light pyramid's ``meta.json`` carries it."""
    return {
        "model": "lambert+shadow+fill+sky",
        "dirs": HORIZON_DIRS,
        "fade_m": list(FADE_M),
        "occluder_fade_m": list(OCCLUDER_FADE_M),
        "sky_radius_m": SKY_RADIUS_M,
        "normalise_min_el": NORMALISE_MIN_EL_DEG,
        "shadow_soft_deg": SHADOW_SOFT_DEG,
        "shadow_fill": SHADOW_FILL,
        "shadow_floor": SHADOW_FLOOR,
        "shadow_floor_knee": SHADOW_FLOOR_KNEE,
        "default_sun": list(DEFAULT_SUN),
        "default_hour": NOON_HOUR,
        "hz_cells": HZ_CELLS,
        "crown_cell": CROWN_CELL,
        "titan_cell": TITAN_CELL,
        "ao_cell": AO_CELL,
        "hz_gutter": HZ_GUTTER_PX,
        "hz_encoding": (
            "atlas of 8 cells a row, each in a border of its edge texels hz_gutter wide: the "
            "ground's horizons, the crowns' alone, the Titan trees' alone, u8 = 255 * sqrt(deg "
            "/ 90), received on the canopy top; then the trees' ambient occlusion, u8 = 255 * o"
        ),
        "nrm_encoding": "RGBA: east and south normal as (v + 1) / 2, sky view, land weight",
        "spans": span_block(),
        "trees": {
            "crown_underside": f"per species: under {LEAF_LOW_SHARE} of its leaf area",
            "crown_underside_unknown": CROWN_UNDERSIDE,
            "titan_slab_m": TITAN_SLAB_M,
            "titan_fade_m": list(TITAN_FADE_M),
        },
        "ao": {
            "scales_m": list(AO_SCALES_M),
            "weights": list(AO_WEIGHTS),
            "depth": AO_DEPTH,
            "strength": AO_STRENGTH,
        },
    }


def light_axis() -> JsonObject:
    """The fourth provenance axis: which light model the pyramid was baked for."""
    block = model_block()
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":")).encode("utf-8")
    light = LIGHTS[LIGHT_ID]
    return {
        "id": LIGHT_ID,
        "version": light["version"],
        "label": light["label"],
        **block,
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
    }


def _either_side(az: float) -> tuple[int, int, np.float32]:
    f = (az % 360.0) / (360.0 / HORIZON_DIRS)
    i0 = int(np.floor(f)) % HORIZON_DIRS
    return i0, (i0 + 1) % HORIZON_DIRS, np.float32(f - np.floor(f))


def _toward(hz_deg: Horizons, az: float, first: int = 0) -> F32Grid:
    """The horizon toward ``az``, between the two stored directions either side of it."""
    i0, i1, w = _either_side(az)
    before: F32Grid = hz_deg[first + i0] * (1 - w)
    after: F32Grid = hz_deg[first + i1] * w
    return before + after


def sun_cells(az: float) -> tuple[int, int, int, int]:
    """The cells the default sun's terms read for a sun at ``az``: two of the ground's, two of
    the trees' (the bake's own: crowns and Titan trees together over the ground)."""
    i0, i1, _w = _either_side(az)
    return i0, i1, CROWN_CELL + i0, CROWN_CELL + i1


def direct_term(
    nrm_u8: U8Grid,
    hz_deg: Horizons | None,
    sun: Sun,
    shadows: bool = True,
    crowns: bool = False,
    filtered: tuple[BoolMask, F32Grid] | None = None,
) -> F32Grid:
    """``ndl * (1 - shadow * (1 - fill)) / sin(max(el, 35))`` per pixel.

    ``hz_deg`` is ``(cells, h, w)``: the ground's ``HORIZON_DIRS`` horizons, then, when
    ``crowns`` and the atlas has them, the trees', which shade where they stand higher. A
    sequence of as many planes does too; only the sun's directions are read. ``filtered`` is
    ``(use, shade)`` on the normals' grid: where ``use``, the shadow is ``shade``, filtered
    per cell beside a span (``span_bake.default_shade``), not read off the horizon.
    """
    hz = None
    if shadows and hz_deg is not None:
        hz = sun_horizon(hz_deg, sun[0], crowns)
        shape = nrm_u8.shape[:2]
        if hz.shape != shape:
            hz = ndimage.zoom(hz, np.array(shape) / np.array(hz.shape), order=1)
    return shaded_direct(nrm_u8, hz, sun, filtered)


def sun_horizon(hz_deg: Horizons, az: float, crowns: bool = False) -> F32Grid:
    """The horizon toward ``az``; with ``crowns``, the crown and Titan tree cells the planes
    hold where they stand higher."""
    hz = _toward(hz_deg, az)
    for first in (CROWN_CELL, TITAN_CELL) if crowns else ():
        if len(hz_deg) >= first + HORIZON_DIRS:
            hz = np.maximum(hz, _toward(hz_deg, az, first))
    return hz


def shaded_direct(
    nrm_u8: U8Grid, hz: F32Grid | None, sun: Sun, filtered: tuple[BoolMask, F32Grid] | None = None
) -> F32Grid:
    """``direct_term`` for the horizon toward the sun on the normals' own grid, None for none,
    and ``filtered`` on that grid too."""
    az, el = sun
    nx = nrm_u8[..., 0].astype(np.float32) / 127.5 - 1
    ny = nrm_u8[..., 1].astype(np.float32) / 127.5 - 1
    nz = np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1))
    lx, ly, lz = sun_vector(az, el)
    ndl = np.maximum(nx * lx + ny * ly + nz * lz, 0.0)
    shade = np.zeros_like(ndl)
    if hz is not None:
        shade = np.clip((hz - el) / SHADOW_SOFT_DEG + 0.5, 0, 1)
        if filtered is not None:
            shade = np.where(filtered[0], filtered[1], shade)
    return (ndl * (1 - shade * (1 - SHADOW_FILL)) * _sun_gain(el)).astype(np.float32)


def _sun_gain(el: float) -> np.float32:
    """``1 / sin(max(el, NORMALISE_MIN_EL_DEG))`` as the float32 the shader's ``uInvNorm`` is."""
    return np.float32(1.0 / max(np.sin(np.radians(el)), np.sin(np.radians(NORMALISE_MIN_EL_DEG))))


def surface_direct(z_m: NDArray[np.floating], spacing_m: float, sun: Sun = DEFAULT_SUN) -> F32Grid:
    """``direct_term`` of a height raster without shadows: Lambert toward ``sun``, flat is 1."""
    ndl = sun_dot(np.asarray(z_m, np.float32), spacing_m, *sun)
    return (ndl * _sun_gain(sun[1])).astype(np.float32)


def apply_terms(
    rgb_u8: U8Grid,
    svf: NDArray[np.floating],
    direct: NDArray[np.floating],
    land: NDArray[np.floating],
    params: LightBlock,
) -> U8Grid:
    """Unlit colour times the light, in the style's own space. Arrays in [0, 1] except rgb."""
    light = light_params(params)
    ambient = np.float32(light["ambient"])
    sky, sun = unit_luminance(light["sky"]), unit_luminance(light["sun"])
    flat = sky_sun_light(light["sky"], light["sun"], ambient)
    rel = (ambient * sky * svf[..., None] + (1 - ambient) * sun * direct[..., None]) / flat
    k = SHADOW_FLOOR_KNEE
    rel = 0.5 * (rel + SHADOW_FLOOR + np.sqrt((rel - SHADOW_FLOOR) ** 2 + k * k))
    rel = 1 + (rel - 1) * land[..., None]
    c = rgb_u8.astype(np.float32) / 255.0
    if light["space"] == "linear":
        knee, white = light["tone_knee"], light["tone_white"]
        base = by_luminance(srgb_unit_to_linear(c), untone, knee, white)
        out = linear_to_srgb_unit(by_luminance(base * rel, tone, knee, white))
    else:
        out = np.clip(c * rel, 0, 1)
    return np.round(out * 255).astype(np.uint8)


def relight(
    rgb_u8: U8Grid,
    nrm_u8: U8Grid,
    hz_u8: U8Grid | None,
    sun: Sun,
    params: LightBlock,
    shadows: bool = True,
    sky: bool = True,
) -> U8Grid:
    """The shader's answer in numpy: unlit colour, the light tile, a sun.

    ``params`` is ``shader_light``'s; its ``crowns`` says whether the trees' horizons and
    their ambient occlusion count. ``hz_u8`` is ``(cells, h, w)``, on the normals' grid or
    coarser.
    """
    light = light_params(params)
    horizons = None if hz_u8 is None else hz_u8[:HORIZON_CELLS]
    hz_deg = decode_horizon(horizons) if (shadows and horizons is not None) else None
    direct = direct_term(nrm_u8, hz_deg, sun, shadows, light["crowns"])
    svf = nrm_u8[..., 2].astype(np.float32) / 255.0 if sky else np.ones(direct.shape, np.float32)
    if sky and light["crowns"] and hz_u8 is not None and len(hz_u8) > AO_CELL:
        ao = hz_u8[AO_CELL].astype(np.float32) / np.float32(255.0)
        if ao.shape != svf.shape:
            ao = ndimage.zoom(ao, np.array(svf.shape) / np.array(ao.shape), order=1)
        svf = svf * (np.float32(1.0) - ao)
    land = nrm_u8[..., 3].astype(np.float32) / 255.0
    return apply_terms(rgb_u8, svf, direct, land, light)

"""The light model: Lambert, faded cast shadows and a sky-view term, applied to unlit colour.

The page's shader runs the same arithmetic per pixel; this module is its reference, the
baked fallback, and the provenance the light axis records. docs/spatial-and-map.md
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
from mapgen.lighting.horizon import (
    FADE_M,
    HORIZON_DIRS,
    OCCLUDER_FADE_M,
    SKY_RADIUS_M,
    decode_horizon,
)
from mapgen.lighting.sun import DEFAULT_SUN, NOON_HOUR, Sun, sun_vector
from satisfactory_mcp.core.arrays import F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.versions import LIGHTS
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "DIRECT_SCALE",
    "HZ_CELLS",
    "LIGHT_ID",
    "NORMALISE_MIN_EL_DEG",
    "SHADOW_FILL",
    "SHADOW_FLOOR",
    "SHADOW_FLOOR_KNEE",
    "SHADOW_SOFT_DEG",
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
    "sun_cells",
    "surface_direct",
]

LIGHT_ID = "sun"

#: Atlas cells per tile: the ground's horizons, then the crowns'.
HZ_CELLS = 2 * HORIZON_DIRS

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
        "crown_cell": HORIZON_DIRS,
        "hz_encoding": (
            "atlas of 8 x 8 cells, u8 = 255 * sqrt(deg / 90): the ground's horizons, then the "
            "crowns' where they stand above the ground's, else 0"
        ),
        "nrm_encoding": "RGBA: east and south normal as (v + 1) / 2, sky view, land weight",
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
    """The cells ``direct_term`` reads for a sun at ``az``: two of the ground's, two crowns'."""
    i0, i1, _w = _either_side(az)
    return i0, i1, HORIZON_DIRS + i0, HORIZON_DIRS + i1


def direct_term(
    nrm_u8: U8Grid, hz_deg: Horizons | None, sun: Sun, shadows: bool = True, crowns: bool = False
) -> F32Grid:
    """``ndl * (1 - shadow * (1 - fill)) / sin(max(el, 35))`` per pixel.

    ``hz_deg`` is ``(cells, h, w)``: the ground's ``HORIZON_DIRS`` horizons, then, when
    ``crowns`` and the atlas has them, the crowns', which shade where they stand higher. A
    sequence of as many planes does too; only the ``sun_cells`` are read.
    """
    az, el = sun
    nx = nrm_u8[..., 0].astype(np.float32) / 127.5 - 1
    ny = nrm_u8[..., 1].astype(np.float32) / 127.5 - 1
    nz = np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1))
    lx, ly, lz = sun_vector(az, el)
    ndl = np.maximum(nx * lx + ny * ly + nz * lz, 0.0)
    shade = np.zeros_like(ndl)
    if shadows and hz_deg is not None:
        hz = _toward(hz_deg, az)
        if crowns and len(hz_deg) >= HZ_CELLS:
            hz = np.maximum(hz, _toward(hz_deg, az, HORIZON_DIRS))
        if hz.shape != ndl.shape:
            hz = ndimage.zoom(hz, np.array(ndl.shape) / np.array(hz.shape), order=1)
        shade = np.clip((hz - el) / SHADOW_SOFT_DEG + 0.5, 0, 1)
    inv = 1.0 / max(np.sin(np.radians(el)), np.sin(np.radians(NORMALISE_MIN_EL_DEG)))
    return (ndl * (1 - shade * (1 - SHADOW_FILL)) * inv).astype(np.float32)


def surface_direct(z_m: NDArray[np.floating], spacing_m: float, sun: Sun = DEFAULT_SUN) -> F32Grid:
    """``direct_term`` of a height raster without shadows: Lambert toward ``sun``, flat is 1."""
    d_south, d_east = np.gradient(np.asarray(z_m, np.float32), spacing_m)
    lx, ly, lz = sun_vector(*sun)
    ndl = (lz - d_east * lx - d_south * ly) / np.sqrt(d_east * d_east + d_south * d_south + 1.0)
    inv = 1.0 / max(np.sin(np.radians(sun[1])), np.sin(np.radians(NORMALISE_MIN_EL_DEG)))
    return (np.maximum(ndl, 0.0) * inv).astype(np.float32)


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

    ``params`` is ``shader_light``'s; its ``crowns`` says whether the crown horizons count.
    """
    light = light_params(params)
    hz_deg = decode_horizon(hz_u8) if (shadows and hz_u8 is not None) else None
    direct = direct_term(nrm_u8, hz_deg, sun, shadows, light["crowns"])
    svf = nrm_u8[..., 2].astype(np.float32) / 255.0 if sky else np.ones(direct.shape, np.float32)
    land = nrm_u8[..., 3].astype(np.float32) / 255.0
    return apply_terms(rgb_u8, svf, direct, land, light)

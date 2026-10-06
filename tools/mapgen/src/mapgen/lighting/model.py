"""The light model: Lambert, faded cast shadows and a sky-view term, applied to unlit colour.

The page's shader runs the same arithmetic per pixel; this module is its reference, the
baked fallback, and the provenance the light axis records. docs/spatial-and-map.md
section 29 says why each constant has its value.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
from scipy import ndimage

from mapgen.lighting.horizon import (
    FADE_M,
    HORIZON_DIRS,
    OCCLUDER_FADE_M,
    SKY_RADIUS_M,
    decode_horizon,
)
from mapgen.lighting.sun import DEFAULT_SUN, NOON_HOUR, sun_vector
from satisfactory_mcp.core.gameassets.versions import LIGHTS

__all__ = [
    "DIRECT_SCALE",
    "HZ_CELLS",
    "LIGHT_ID",
    "NORMALISE_MIN_EL_DEG",
    "SHADOW_FILL",
    "SHADOW_FLOOR",
    "SHADOW_FLOOR_KNEE",
    "SHADOW_SOFT_DEG",
    "apply_terms",
    "direct_term",
    "light_axis",
    "model_block",
    "relight",
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


def model_block() -> dict:
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


def light_axis() -> dict:
    """The fourth provenance axis: which light model the pyramid was baked for."""
    block = model_block()
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "id": LIGHT_ID,
        "version": LIGHTS[LIGHT_ID]["version"],
        "label": LIGHTS[LIGHT_ID]["label"],
        **block,
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
    }


def _unit(rgb) -> np.ndarray:
    rgb = np.asarray(rgb, np.float32)
    return rgb / np.float32(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])


def _s2l(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _l2s(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _tone(y, knee: float, white: float):
    """The painted style's luminance shoulder (``palette.painted.tone``); knee 1 is none."""
    if knee >= 1.0:
        return y
    span = 1.0 - knee
    x = np.maximum(y - knee, 0.0) / span
    top = (white - knee) / span
    return np.where(y > knee, knee + span * x * (1 + x / (top * top)) / (1 + x), y)


def _untone(o, knee: float, white: float):
    if knee >= 1.0:
        return o
    span = 1.0 - knee
    u = np.clip((o - knee) / span, 0.0, 0.999)
    a, b = 1.0 / ((white - knee) / span) ** 2, 1.0 - u
    return np.where(o > knee, knee + span * (np.sqrt(b * b + 4 * a * u) - b) / (2 * a), o)


def _by_luminance(c, curve, knee: float, white: float):
    y = np.maximum(c @ _LUMA, 1e-7)
    return c * (curve(y, knee, white) / y)[..., None]


def _toward(hz_deg, az: float, first: int = 0) -> np.ndarray:
    """The horizon toward ``az``, between the two stored directions either side of it."""
    f = (az % 360.0) / (360.0 / HORIZON_DIRS)
    i0 = int(np.floor(f)) % HORIZON_DIRS
    w = np.float32(f - np.floor(f))
    return hz_deg[first + i0] * (1 - w) + hz_deg[first + (i0 + 1) % HORIZON_DIRS] * w


def direct_term(nrm_u8, hz_deg, sun, shadows: bool = True, crowns: bool = False) -> np.ndarray:
    """``ndl * (1 - shadow * (1 - fill)) / sin(max(el, 35))`` per pixel.

    ``hz_deg`` is ``(cells, h, w)``: the ground's ``HORIZON_DIRS`` horizons, then, when
    ``crowns`` and the atlas has them, the crowns', which shade where they stand higher.
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
        if crowns and hz_deg.shape[0] >= HZ_CELLS:
            hz = np.maximum(hz, _toward(hz_deg, az, HORIZON_DIRS))
        if hz.shape != ndl.shape:
            hz = ndimage.zoom(hz, np.array(ndl.shape) / np.array(hz.shape), order=1)
        shade = np.clip((hz - el) / SHADOW_SOFT_DEG + 0.5, 0, 1)
    inv = 1.0 / max(np.sin(np.radians(el)), np.sin(np.radians(NORMALISE_MIN_EL_DEG)))
    return (ndl * (1 - shade * (1 - SHADOW_FILL)) * inv).astype(np.float32)


def apply_terms(rgb_u8, svf, direct, land, params: dict) -> np.ndarray:
    """Unlit colour times the light, in the style's own space. Arrays in [0, 1] except rgb."""
    amb = np.float32(params["ambient"])
    sky, sun = _unit(params["sky"]), _unit(params["sun"])
    flat = amb * sky + (1 - amb) * sun
    rel = (amb * sky * svf[..., None] + (1 - amb) * sun * direct[..., None]) / flat
    k = SHADOW_FLOOR_KNEE
    rel = 0.5 * (rel + SHADOW_FLOOR + np.sqrt((rel - SHADOW_FLOOR) ** 2 + k * k))
    rel = 1 + (rel - 1) * land[..., None]
    c = rgb_u8.astype(np.float32) / 255.0
    if params["space"] == "linear":
        knee, white = float(params["tone_knee"]), float(params["tone_white"])
        base = _by_luminance(_s2l(c), _untone, knee, white)
        out = _l2s(_by_luminance(base * rel, _tone, knee, white))
    else:
        out = np.clip(c * rel, 0, 1)
    return np.round(out * 255).astype(np.uint8)


def relight(rgb_u8, nrm_u8, hz_u8, sun, params, shadows: bool = True, sky: bool = True):
    """The shader's answer in numpy: unlit colour, the light tile, a sun.

    ``params`` is ``shader_light``'s; its ``crowns`` says whether the crown horizons count.
    """
    hz_deg = decode_horizon(hz_u8) if (shadows and hz_u8 is not None) else None
    direct = direct_term(nrm_u8, hz_deg, sun, shadows, bool(params.get("crowns")))
    svf = nrm_u8[..., 2].astype(np.float32) / 255.0 if sky else np.ones(direct.shape, np.float32)
    land = nrm_u8[..., 3].astype(np.float32) / 255.0
    return apply_terms(rgb_u8, svf, direct, land, params)

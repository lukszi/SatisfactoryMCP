"""The relight on the GPU: ``render/draw/light.py``'s ``relight_rows``, a thread a pixel.

``relight.cu`` does ``lighting.model.apply_terms``'s float32 operations in its order, with its
constants worked out here as numpy works them out. The two transcendentals of the linear
space are numpy's own: sRGB to linear is read from a table of its 256 values, and linear to
an sRGB byte from the steps numpy's ``pow`` was measured to take over every float32 between
the dark knee and one (``srgb_steps``). Imported only when ``mapgen.jit.gpu_on()``.
docs/map/renders.md section 41, "The draw on the GPU".
"""

from __future__ import annotations

import functools

import cupy as cp
import numpy as np

from mapgen.colour import (
    LUMA,
    linear_to_srgb_unit,
    sky_sun_light,
    srgb_unit_to_linear,
    unit_luminance,
)
from mapgen.lighting.model import SHADOW_FLOOR, SHADOW_FLOOR_KNEE, LightBlock, light_params
from mapgen.render.gpu.device import flat_grid, kernel, on_device
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = ["DARK_KNEE", "relight_rows", "srgb_steps"]

#: Where ``linear_to_srgb_unit`` turns from its linear toe to the power curve.
DARK_KNEE = np.float32(0.0031308)

#: float32 values a step of the byte search reads at a time.
_STEP_CHUNK = 1 << 22

_SOURCE = "relight.cu"


def relight_rows(
    rgb: U8Grid, terms: U8Grid, land: U8Grid, params: LightBlock, channels: tuple[int, int]
) -> U8Grid | None:
    """``relight_rows`` of these rows by ``terms``' ``channels`` (direct, sky); None where the
    device has no memory for them, or, in linear light, where numpy's ``pow`` is not
    monotonic here (``srgb_steps``): the CPU relights them then."""
    light = light_params(params)
    steps = srgb_steps() if light["space"] == "linear" else None
    if light["space"] == "linear" and steps is None:
        return None
    return on_device(lambda: _relight(rgb, terms, land, params, channels, steps))


def _relight(
    rgb: U8Grid,
    terms: U8Grid,
    land: U8Grid,
    params: LightBlock,
    channels: tuple[int, int],
    steps: F32Grid | None,
) -> U8Grid:
    pixels = rgb.shape[0] * rgb.shape[1]
    which, sky = channels
    on_out = cp.empty(rgb.shape, np.uint8)
    head = (
        cp.asarray(np.ascontiguousarray(rgb)),
        cp.asarray(np.ascontiguousarray(terms)),
        np.int32(terms.shape[-1]),
        np.int32(which),
        np.int32(sky),
        cp.asarray(np.ascontiguousarray(land)),
        cp.asarray(_light_constants(params)),
    )
    if steps is None:
        kernel(_SOURCE, "relight_srgb")(*flat_grid(pixels), (*head, on_out, np.int64(pixels)))
    else:
        shape = (cp.asarray(_tone_constants(params)), cp.asarray(_to_linear()), cp.asarray(steps))
        run = kernel(_SOURCE, "relight_linear")
        run(*flat_grid(pixels), (*head, *shape, on_out, np.int64(pixels)))
    return on_out.get()


def _light_constants(params: LightBlock) -> F32Grid:
    """``Light`` of ``relight.cu``: ambient times the sky, the sun's share, the flat light,
    the shadow floor and its knee squared, as ``apply_terms`` works them out."""
    light = light_params(params)
    ambient = np.float32(light["ambient"])
    sky, sun = unit_luminance(light["sky"]), unit_luminance(light["sun"])
    flat = sky_sun_light(light["sky"], light["sun"], ambient)
    knee = SHADOW_FLOOR_KNEE * SHADOW_FLOOR_KNEE
    tail = np.array([SHADOW_FLOOR, knee], np.float32)
    return np.concatenate([ambient * sky, (1 - ambient) * sun, flat, tail]).astype(np.float32)


def _tone_constants(params: LightBlock) -> F32Grid:
    """``Tone`` of ``relight.cu``: ``colour.tone`` and ``untone``'s numbers as float32, the
    knee at infinity where there is no curve; ``by_luminance``'s floor and weights; and
    ``linear_to_srgb_unit``'s toe."""
    light = light_params(params)
    knee, white = light["tone_knee"], light["tone_white"]
    if knee >= 1.0:
        shape = [np.inf, 1.0, 1.0, 1.0, 1.0]
    else:
        span = 1.0 - knee
        a = 1.0 / ((white - knee) / span) ** 2
        top = (white - knee) / span
        shape = [knee, span, 4 * a, 2 * a, top * top]
    return np.array([*shape, 0.999, 1e-7, *LUMA, DARK_KNEE, 12.92], np.float32)


@functools.cache
def _to_linear() -> F32Grid:
    """``srgb_unit_to_linear`` of each byte's ``c``, as ``apply_terms`` reads it."""
    c = np.arange(256, dtype=np.float32) / 255.0
    return np.ascontiguousarray(srgb_unit_to_linear(c), np.float32)


@functools.cache
def srgb_steps() -> F32Grid | None:
    """For each byte ``k``, the least float32 ``c`` past ``DARK_KNEE`` that ``apply_terms``
    rounds to ``k`` or more; None where those bytes ever fall as ``c`` rises.

    Every float32 from ``DARK_KNEE`` to one is put through ``linear_to_srgb_unit`` and the
    round, 70 million of them: the bytes rise with ``c``, so a byte is the count of steps at
    or below ``c``, less one, and the GPU reads numpy's ``pow`` without calling one.
    """
    first = int(np.float32(DARK_KNEE).view(np.uint32)) + 1
    last = int(np.float32(1.0).view(np.uint32))
    steps = np.full(256, np.inf, np.float32)
    seen = -1
    for start in range(first, last + 1, _STEP_CHUNK):
        bits = np.arange(start, min(start + _STEP_CHUNK, last + 1), dtype=np.uint32)
        c = bits.view(np.float32)
        byte = np.round(linear_to_srgb_unit(c) * 255).astype(np.int64)
        if byte[0] < seen or np.any(np.diff(byte) < 0):
            return None
        for k in range(seen + 1, int(byte[-1]) + 1):
            steps[k] = c[np.searchsorted(byte, k)]
        seen = int(byte[-1])
    return steps

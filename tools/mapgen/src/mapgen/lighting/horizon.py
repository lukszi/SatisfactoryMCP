"""Faded horizons per compass direction, the shadow they cast, and the light under it.

The horizons are sun-independent: a pyramid stores them once and the viewer's sun picks two
directions. ``occluder`` is the hook for anything standing on the ground (tree crowns): it is
max-folded into the surface the horizons are marched over. Why each value: the README's
"Horizons and tree shadows".
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "DIRS",
    "FADE_M",
    "FINE_M",
    "HZ_MAX_DEG",
    "LIGHT_FLOOR",
    "NORMALISE_MIN_EL_DEG",
    "OCCLUDER_FADE_M",
    "SHADOW_SOFT_DEG",
    "decode_horizon",
    "direction_pair",
    "encode_horizon",
    "fade_weight",
    "faded_horizon",
    "faded_horizons",
    "halo_px",
    "light",
    "march_steps",
    "shadow",
    "surface",
]

#: Compass directions stored, 11.25 degrees apart.
DIRS = 32

#: A blocker counts fully to 40 m away and not at all past 150 m.
FADE_M = (40.0, 150.0)

#: Crowns are porous and their far shadow diffuse: they fade sooner than rock.
OCCLUDER_FADE_M = (25.0, 80.0)

#: Every texel is visited out to here; beyond it the march strides 3% of the distance.
FINE_M = 44.0
STEP_GROWTH = 0.03

#: The byte is ``255 * sqrt(deg / 90)``: finer where low suns need it.
HZ_MAX_DEG = 90.0

#: Half-width of the penumbra the shader draws, in degrees of sun elevation.
SHADOW_SOFT_DEG = 2.0

#: The darkest light anything gets: shadow stays readable as ground.
LIGHT_FLOOR = 0.38

#: Lambert is normalised by sin(elevation), clamped here so low suns do not blow out.
NORMALISE_MIN_EL_DEG = 35.0


def fade_weight(distance_m: float, fade: tuple[float, float] = FADE_M) -> float:
    near, far = fade
    return float(np.clip((far - distance_m) / (far - near), 0.0, 1.0))


def march_steps(step_m: float, fade: tuple[float, float] = FADE_M) -> list[tuple[float, float]]:
    """``(distance in texels, fade weight / distance in metres)`` for every march step."""
    out, t = [], 1.0
    fine, reach = FINE_M / step_m, fade[1] / step_m
    while t < reach:
        weight = fade_weight(t * step_m, fade)
        if weight > 0:
            out.append((t, weight / (t * step_m)))
        t += 1.0 if t < fine else max(1.0, t * STEP_GROWTH)
    return out


def halo_px(step_m: float) -> int:
    """Texels of margin a core needs on every side: the march's reach plus the bilinear tap."""
    return int(np.ceil(max(FADE_M[1], OCCLUDER_FADE_M[1]) / step_m)) + 2


def surface(z_m: np.ndarray, occluder: np.ndarray | None = None) -> np.ndarray:
    """The ground with whatever stands on it; ``nan`` in ``occluder`` means nothing."""
    z = np.asarray(z_m, np.float32)
    return z if occluder is None else np.fmax(z, np.asarray(occluder, np.float32))


def _march(z, here, core, azimuth_deg, step_m, fade, best) -> None:
    rows, cols = core
    r0, r1, c0, c1 = rows.start, rows.stop, cols.start, cols.stop
    az = np.deg2rad(azimuth_deg)
    d_row, d_col = -np.cos(az), np.sin(az)
    for t, gain in march_steps(step_m, fade):
        oy, ox = d_row * t, d_col * t
        iy, ix = int(np.floor(oy)), int(np.floor(ox))
        fy, fx = np.float32(oy - iy), np.float32(ox - ix)
        a = z[r0 + iy : r1 + iy, c0 + ix : c1 + ix]
        b = z[r0 + iy : r1 + iy, c0 + ix + 1 : c1 + ix + 1]
        c = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix : c1 + ix]
        e = z[r0 + iy + 1 : r1 + iy + 1, c0 + ix + 1 : c1 + ix + 1]
        there = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy
        np.maximum(best, (there - here) * np.float32(gain), out=best)


def faded_horizon(
    z_m: np.ndarray,
    core: tuple[slice, slice],
    azimuth_deg: float,
    step_m: float,
    occluder=None,
    receiver=None,
) -> np.ndarray:
    """Degrees of the faded horizon toward ``azimuth_deg`` (0 north, 90 east) over ``core``.

    The ground blocks under ``FADE_M``; the ground with ``occluder`` on it blocks under
    ``OCCLUDER_FADE_M``. Receivers are ``receiver`` (core-shaped) when given, else the top.
    """
    ground = np.asarray(z_m, np.float32)
    top = surface(ground, occluder)
    here = top[core] if receiver is None else np.asarray(receiver, np.float32)
    best = np.zeros(here.shape, np.float32)
    _march(ground, here, core, azimuth_deg, step_m, FADE_M, best)
    if occluder is not None:
        _march(top, here, core, azimuth_deg, step_m, OCCLUDER_FADE_M, best)
    return np.degrees(np.arctan(best)).astype(np.float32)


def faded_horizons(
    z_m: np.ndarray,
    core: tuple[slice, slice],
    step_m: float,
    dirs: int = DIRS,
    occluder=None,
    receiver=None,
) -> np.ndarray:
    """``(rows, cols, dirs)`` horizons in degrees, direction ``k`` at ``k * 360 / dirs``."""
    return np.stack(
        [
            faded_horizon(z_m, core, k * 360.0 / dirs, step_m, occluder, receiver)
            for k in range(dirs)
        ],
        axis=-1,
    )


def encode_horizon(degrees: np.ndarray) -> np.ndarray:
    return np.round(np.sqrt(np.clip(degrees / HZ_MAX_DEG, 0.0, 1.0)) * 255.0).astype(np.uint8)


def decode_horizon(byte: np.ndarray) -> np.ndarray:
    q = np.asarray(byte, np.float32) / 255.0
    return q * q * np.float32(HZ_MAX_DEG)


def direction_pair(azimuth_deg: float, dirs: int = DIRS) -> tuple[int, int, float]:
    """The two stored directions either side of the sun, and the weight of the second."""
    f = (azimuth_deg % 360.0) / (360.0 / dirs)
    i0 = int(np.floor(f)) % dirs
    return i0, (i0 + 1) % dirs, float(f - np.floor(f))


def shadow(horizons: np.ndarray, azimuth_deg: float, elevation_deg: float) -> np.ndarray:
    """Share of the sun hidden, from ``(rows, cols, dirs)`` horizons; the shader's own rule."""
    i0, i1, w = direction_pair(azimuth_deg, horizons.shape[-1])
    hz = horizons[..., i0] * (1 - w) + horizons[..., i1] * w
    return np.clip((hz - elevation_deg) / SHADOW_SOFT_DEG + 0.5, 0.0, 1.0)


def light(
    ndl: np.ndarray,
    shade: np.ndarray,
    sky_view: np.ndarray | float,
    ambient: float,
    elevation_deg: float,
) -> np.ndarray:
    """Light against flat open ground: sky plus sun, floored at ``LIGHT_FLOOR``."""
    norm = np.sin(np.deg2rad(max(elevation_deg, NORMALISE_MIN_EL_DEG)))
    lit = ambient * np.asarray(sky_view) + (1 - ambient) * ndl * (1 - shade) / norm
    return np.maximum(lit, LIGHT_FLOOR).astype(np.float32)

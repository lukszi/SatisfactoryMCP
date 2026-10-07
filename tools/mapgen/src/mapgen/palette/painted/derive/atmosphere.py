"""The engine's default SkyAtmosphere, single scattering: the sun through it and the sky's light.

Rayleigh, Mie and ozone at the engine's default coefficients and scale heights; the numbers
are listed in docs/map/calibration.md section 31.
"""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.arrays import F64Grid

__all__ = ["sky_irradiance", "sun_transmittance"]

_R0, _TOP = 6360.0, 60.0
_RAY = np.array([0.175287, 0.409607, 1.0]) * 0.0331
_RAY_H, _MIE_S, _MIE_A, _MIE_H, _MIE_G = 8.0, 0.003996, 0.000444, 1.2, 0.8
_OZONE = np.array([0.345561, 1.0, 0.045188]) * 0.001881


def _density(h: F64Grid) -> tuple[F64Grid, F64Grid, F64Grid]:
    """Rayleigh and Mie scattering and total extinction per km at heights ``h`` (km)."""
    h = np.asarray(h)[..., None]
    ray = np.exp(-h / _RAY_H) * _RAY
    mie = np.exp(-h / _MIE_H) * _MIE_S
    ozone = np.clip(1 - np.abs(h - 25.0) / 15.0, 0, None) * _OZONE
    return ray, mie, ray + np.exp(-h / _MIE_H) * (_MIE_S + _MIE_A) + ozone


def _ray_length(h0: float | F64Grid, mu: float) -> float | F64Grid:
    r = _R0 + h0
    b, c = r * mu, r * r - (_R0 + _TOP) ** 2
    return -b + np.sqrt(np.maximum(b * b - c, 0))


def _optical(h0: float, mu: float, n: int = 256) -> F64Grid:
    length = _ray_length(h0, mu)
    t = (np.arange(n) + 0.5) / n * length
    r = _R0 + h0
    return _density(np.sqrt(r * r + t * t + 2 * r * t * mu) - _R0)[2].sum(0) * (length / n)


def sun_transmittance(elevation_deg: float) -> F64Grid:
    """The sun's transmittance through the atmosphere to the ground."""
    return np.exp(-_optical(0.0, float(np.sin(np.radians(elevation_deg)))))


def sky_irradiance(elevation_deg: float, nz: int = 24, nphi: int = 48, n: int = 96) -> F64Grid:
    """Horizontal sky irradiance per unit sun illuminance, single scattering."""
    sun = np.radians(elevation_deg)
    sun_x, sun_z = float(np.cos(sun)), float(np.sin(sun))
    k = 3 / (8 * np.pi) * (1 - _MIE_G**2) / (2 + _MIE_G**2)
    out = np.zeros(3)
    for mu in (np.arange(nz) + 0.5) / nz:
        length = float(_ray_length(0.0, float(mu)))
        dt = length / n
        ts = (np.arange(n) + 0.5) / n * length
        hs = np.sqrt(_R0 * _R0 + ts * ts + 2 * _R0 * ts * mu) - _R0
        ray, mie, ext = _density(hs)
        to_camera = np.exp(-(np.cumsum(ext, 0) * dt - ext * dt / 2))
        to_sun = np.array([np.exp(-_optical(float(h), sun_z, 64)) for h in hs])
        for phi in (np.arange(nphi) + 0.5) / nphi * 2 * np.pi:
            c = float(np.sqrt(1 - mu * mu) * np.cos(phi)) * sun_x + float(mu) * sun_z
            phase_r = 3 / (16 * np.pi) * (1 + c * c)
            phase_m = k * (1 + c * c) / (1 + _MIE_G**2 - 2 * _MIE_G * c) ** 1.5
            scattered = (to_camera * to_sun * (ray * phase_r + mie * phase_m)).sum(0)
            out += scattered * dt * mu * (1.0 / nz) * (2 * np.pi / nphi)
    return out

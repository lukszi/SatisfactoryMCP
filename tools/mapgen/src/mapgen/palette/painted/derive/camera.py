"""The camera model: a game albedo as a daylight screenshot of flat ground would measure it.

    measured = discount(OKLab(filmic(grade(E * albedo * I))))

``I`` is the noon sun through the engine's default sky (``atmosphere``), ``E`` the histogram
auto-exposure over the lit bake, and the film curve and grade ``tonemap``'s. Every constant is
the engine's or the screenshot method's; docs/map/calibration.md section 31 lists them. Every
product is written out in a fixed order, never BLAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from mapgen.colour import linear_from_oklab, linear_to_srgb, oklab, srgb_to_linear
from mapgen.palette.painted.derive.atmosphere import sky_irradiance, sun_transmittance
from mapgen.palette.painted.derive.tonemap import MID_GREY, RGB, Gains, dot3, tonemap
from satisfactory_mcp.core.arrays import F64Grid

__all__ = [
    "Light",
    "delta_e",
    "discount",
    "exposure",
    "hex_of_lab",
    "hex_of_linear",
    "illuminant",
    "lab_of_hex",
    "measured_lab",
]

REC709 = np.array([0.2126, 0.7152, 0.0722])


@dataclass(frozen=True)
class Light:
    """One lighting state: the sun's colour and lux, its elevation, the sky and the grade."""

    name: str
    sun_colour: RGB
    sun_lux: float
    elevation_deg: float
    sky_luminance_factor: RGB
    gains: Gains | None = None


@lru_cache(maxsize=8)
def _sky_terms(elevation_deg: float) -> tuple[F64Grid, F64Grid]:
    return sun_transmittance(elevation_deg), sky_irradiance(elevation_deg)


def illuminant(light: Light) -> F64Grid:
    """Linear RGB irradiance on flat ground per unit albedo: direct sun plus the sky."""
    t_sun, sky = _sky_terms(light.elevation_deg)
    direct = np.sin(np.radians(light.elevation_deg)) * t_sun
    total = direct + sky * np.asarray(light.sky_luminance_factor)
    return light.sun_lux / np.pi * np.asarray(light.sun_colour) * total


def exposure(
    bake_linear: NDArray[np.floating], light: Light, bias_ev: float, band_pct: tuple[float, float]
) -> tuple[float, float]:
    """Histogram auto-exposure over the lit bake, and the band's mean luminance."""
    y = dot3(bake_linear * illuminant(light), REC709)
    lo, hi = np.percentile(y, band_pct)
    band = float(y[(y >= lo) & (y <= hi)].mean())
    return MID_GREY * 2.0**bias_ev / band, band


def discount(lab: NDArray[np.floating]) -> F64Grid:
    """The screenshot method's own measurement: L x0.95 capped at 0.86, C x0.9."""
    out = np.array(lab, np.float64)
    out[..., 0] = np.minimum(out[..., 0] * 0.95, 0.86)
    out[..., 1:] *= 0.9
    return out


def measured_lab(
    albedo_linear: NDArray[np.floating] | list[float], light: Light, gain: float
) -> F64Grid:
    """What a screenshot of flat, lit ground of this albedo measures, in OKLab, under the
    exposure ``gain``."""
    scene = gain * np.asarray(albedo_linear, np.float64) * illuminant(light)
    display = tonemap(scene, light.gains)
    return discount(oklab(np.clip(display, 1e-9, 1.0)))


def lab_of_hex(hex_colour: str) -> F64Grid:
    rgb = [int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)]
    return oklab(srgb_to_linear(rgb)).astype(np.float64)


def _hex(rgb: NDArray[np.floating]) -> str:
    return "#" + "".join(f"{v:02x}" for v in np.clip(np.round(rgb), 0, 255).astype(int))


def hex_of_lab(lab: NDArray[np.floating]) -> str:
    return _hex(linear_to_srgb(np.clip(linear_from_oklab(lab), 0.0, 1.0)))


def hex_of_linear(linear: NDArray[np.floating] | list[float]) -> str:
    return _hex(linear_to_srgb(np.clip(np.asarray(linear, np.float32), 0.0, 1.0)))


def delta_e(a: NDArray[np.floating], b: NDArray[np.floating]) -> float:
    """OKLab distance x100."""
    d = np.asarray(a, np.float64) - np.asarray(b, np.float64)
    return 100.0 * float(np.sqrt((d[0] * d[0] + d[1] * d[1]) + d[2] * d[2]))

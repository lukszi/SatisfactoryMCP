"""The camera model: a game albedo as a daylight screenshot of flat ground would measure it.

    measured = discount(OKLab(filmic(grade(E * albedo * I))))

``I`` is the noon sun through the engine's default sky, ``E`` the histogram auto-exposure over
the lit bake. Every constant is the engine's or the screenshot method's; docs/map/calibration.md
section 31 lists them. Every product is written out in a fixed order, never BLAS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.colour import linear_from_oklab, linear_to_srgb, oklab, srgb_to_linear

__all__ = [
    "MID_GREY",
    "Gains",
    "Light",
    "delta_e",
    "discount",
    "exposure",
    "hex_of_lab",
    "hex_of_linear",
    "illuminant",
    "lab_of_hex",
    "measured_lab",
    "sky_irradiance",
    "sun_transmittance",
    "tonemap",
]

F64: TypeAlias = NDArray[np.float64]
RGB: TypeAlias = tuple[float, float, float]
#: ``ColorCorrectAll`` gains: shadows, midtones, highlights.
Gains: TypeAlias = tuple[RGB, RGB, RGB]

MID_GREY = 0.18
REC709 = np.array([0.2126, 0.7152, 0.0722])


def _m(*values: float) -> F64:
    return np.array(values, np.float64).reshape(3, 3)


def _mul(*matrices: F64) -> F64:
    """The product of 3 x 3 matrices, left to right, each entry summed in a fixed order."""
    out = matrices[0]
    for b in matrices[1:]:
        a, out = out, np.empty((3, 3))
        for i in range(3):
            out[i] = (a[i, 0] * b[0] + a[i, 1] * b[1]) + a[i, 2] * b[2]
    return out


def _apply(c: NDArray[np.floating], m: F64) -> F64:
    """``c @ m.T``, each channel summed in a fixed order."""
    out = np.empty(np.shape(c), np.float64)
    for i in range(3):
        out[..., i] = (c[..., 0] * m[i, 0] + c[..., 1] * m[i, 1]) + c[..., 2] * m[i, 2]
    return out


def _dot(c: NDArray[np.floating], w: F64) -> F64:
    """``c @ w`` over the last axis, in a fixed order."""
    return np.asarray((c[..., 0] * w[0] + c[..., 1] * w[1]) + c[..., 2] * w[2], np.float64)


def _inv(m: F64) -> F64:
    """A 3 x 3 inverse by its adjugate."""
    adj = np.empty((3, 3))
    for i in range(3):
        for j in range(3):
            r = [k for k in range(3) if k != j]
            c = [k for k in range(3) if k != i]
            sign = -1.0 if (i + j) % 2 else 1.0
            adj[i, j] = sign * (m[r[0], c[0]] * m[r[1], c[1]] - m[r[0], c[1]] * m[r[1], c[0]])
    return adj / ((m[0, 0] * adj[0, 0] + m[0, 1] * adj[1, 0]) + m[0, 2] * adj[2, 0])


# -- UE5's filmic tonemapper -------------------------------------------------------------------

_SRGB_XYZ = _m(0.4124564, 0.3575761, 0.1804375, 0.2126729, 0.7151522, 0.0721750,
               0.0193339, 0.1191920, 0.9503041)  # fmt: skip
_XYZ_SRGB = _m(3.2409699419, -1.5373831776, -0.4986107603, -0.9692436363, 1.8759675015,
               0.0415550574, 0.0556300797, -0.2039769589, 1.0569715142)  # fmt: skip
_XYZ_AP1 = _m(1.6410233797, -0.3248032942, -0.2364246952, -0.6636628587, 1.6153315917,
              0.0167563477, 0.0117218943, -0.0082844420, 0.9883948585)  # fmt: skip
_AP1_XYZ = _m(0.6624541811, 0.1340042065, 0.1561876870, 0.2722287168, 0.6740817658,
              0.0536895174, -0.0055746495, 0.0040607335, 1.0103391003)  # fmt: skip
_XYZ_AP0 = _m(1.0498110175, 0.0, -0.0000974845, -0.4959030231, 1.3733130458, 0.0982400361,
              0.0, 0.0, 0.9912520182)  # fmt: skip
_AP0_XYZ = _m(0.9525523959, 0.0, 0.0000936786, 0.3439664498, 0.7281660966, -0.0721325464,
              0.0, 0.0, 1.0088251844)  # fmt: skip
_D65_D60 = _m(1.01303, 0.00610531, -0.014971, 0.00769823, 0.998165, -0.00503203,
              -0.00284131, 0.00468516, 0.924507)  # fmt: skip
_D60_D65 = _m(0.987224, -0.00611327, 0.0159533, -0.00759836, 1.00186, 0.00533002,
              0.00307257, -0.00509595, 1.08168)  # fmt: skip
_WIDE_XYZ = _m(0.5441691, 0.2395926, 0.1666943, 0.2394656, 0.7021530, 0.0583814,
               -0.0023439, 0.0361834, 1.0552183)  # fmt: skip
_BLUE = _m(0.9404372683, -0.0183068787, 0.0778696104, 0.0083786969, 0.8286599939,
           0.1629613092, 0.0005471261, -0.0008833746, 1.0003362486)  # fmt: skip
_SRGB_AP1 = _mul(_XYZ_AP1, _D65_D60, _SRGB_XYZ)
_AP1_SRGB = _mul(_XYZ_SRGB, _D60_D65, _AP1_XYZ)
_AP0_AP1 = _mul(_XYZ_AP1, _AP0_XYZ)
_AP1_AP0 = _mul(_XYZ_AP0, _AP1_XYZ)
_AP1_Y = _AP1_XYZ[1]
_EXPAND = _mul(_XYZ_AP1, _WIDE_XYZ, _AP1_SRGB)
_BLUE_AP1 = _mul(_AP0_AP1, _BLUE, _AP1_AP0)
_BLUE_INV_AP1 = _mul(_AP0_AP1, _inv(_BLUE), _AP1_AP0)

#: The film curve's defaults: slope, toe, shoulder, black and white clip.
_SLOPE, _TOE, _SHOULDER, _BLACK, _WHITE = 0.88, 0.55, 0.26, 0.0, 0.04


def _smoothstep(a: float, b: float, x: NDArray[np.floating]) -> F64:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _glow_and_red(ap1: F64) -> F64:
    """The ACES glow and red modifier, in AP0."""
    ap0 = _apply(ap1, _AP1_AP0)
    lo, hi = ap0.min(-1), ap0.max(-1)
    sat = (np.maximum(hi, 1e-10) - np.maximum(lo, 1e-10)) / np.maximum(hi, 1e-2)
    r, g, b = ap0[..., 0], ap0[..., 1], ap0[..., 2]
    yc = (b + g + r + 1.75 * np.sqrt(np.maximum(b * (b - g) + g * (g - r) + r * (r - b), 0))) / 3
    x = (sat - 0.4) / 0.2
    t = np.maximum(1 - np.abs(0.5 * x), 0)
    gain = 0.05 * 0.5 * (1 + np.sign(x) * (1 - t * t))
    ratio = 0.08 / np.maximum(yc, 1e-10) - 0.5
    glow = np.where(yc <= 2 / 3 * 0.08, gain, np.where(yc >= 2 * 0.08, 0.0, gain * ratio))
    ap0 = ap0 * (1 + glow)[..., None]
    r, g, b = ap0[..., 0], ap0[..., 1], ap0[..., 2]
    hue = np.degrees(np.arctan2(np.sqrt(3) * (g - b), 2 * r - g - b))
    hue = np.where((r == g) & (g == b), 0.0, np.where(hue < 0, hue + 360, hue))
    centred = np.where(hue > 180, hue - 360, hue)
    weight = _smoothstep(0, 1, 1 - np.abs(2 * centred / 135.0)) ** 2
    ap0 = ap0.copy()
    ap0[..., 0] = ap0[..., 0] + weight * sat * (0.03 - ap0[..., 0]) * (1 - 0.82)
    return ap0


def _film(ap1: F64) -> F64:
    """UE's ``FilmToneMap`` from AP1 to AP1, default curve."""
    w = np.maximum(_apply(_glow_and_red(ap1), _AP0_AP1), 0)
    w = _dot(w, _AP1_Y)[..., None] * 0.04 + w * 0.96
    toe_s, sh_s = 1 + _BLACK - _TOE, 1 + _WHITE - _SHOULDER
    bt = (MID_GREY + _BLACK) / toe_s - 1
    toe_m = math.log10(MID_GREY) - 0.5 * math.log((1 + bt) / (1 - bt)) * (toe_s / _SLOPE)
    straight_m = (1 - _TOE) / _SLOPE - toe_m
    shoulder_m = _SHOULDER / _SLOPE - straight_m
    lc: F64 = np.log10(np.maximum(w, 1e-10))
    straight: F64 = _SLOPE * (lc + straight_m)
    toe: F64 = -_BLACK + 2 * toe_s / (1 + np.exp(-2 * _SLOPE / toe_s * (lc - toe_m)))
    shoulder: F64 = 1 + _WHITE - 2 * sh_s / (1 + np.exp(2 * _SLOPE / sh_s * (lc - shoulder_m)))
    toe_c = np.where(lc < toe_m, toe, straight)
    sh_c = np.where(lc > shoulder_m, shoulder, straight)
    t = np.clip((lc - toe_m) / (shoulder_m - toe_m), 0, 1)
    if shoulder_m < toe_m:  # UE's own guard; true for the default curve
        t = 1 - t
    t = (3 - 2 * t) * t * t
    tone = toe_c + (sh_c - toe_c) * t
    return np.maximum(_dot(tone, _AP1_Y)[..., None] * 0.07 + tone * 0.93, 0)


def _grade(ap1: F64, gains: Gains) -> F64:
    """UE's ``ColorCorrectAll`` gains only: shadows below luma 0.09, highlights from 0.5 to 1."""
    shadows, mids, highs = (np.asarray(g, np.float64) for g in gains)
    luma = _dot(ap1, _AP1_Y)
    ws = 1 - _smoothstep(0, 0.09, luma)
    wh = _smoothstep(0.5, 1.0, luma)
    return ap1 * (shadows * ws[..., None] + mids * (1 - ws - wh)[..., None] + highs * wh[..., None])


def tonemap(scene_linear: F64, gains: Gains | None = None) -> F64:
    """Scene-linear sRGB to display-linear sRGB through UE5's default film curve."""
    ap1 = _apply(np.asarray(scene_linear, np.float64), _SRGB_AP1)
    luma = _dot(ap1, _AP1_Y)
    chroma = ap1 / np.maximum(luma, 1e-10)[..., None]
    off = (chroma - 1) ** 2
    amount = (1 - 2 ** (-4 * ((off[..., 0] + off[..., 1]) + off[..., 2]))) * (
        1 - 2 ** (-4 * luma**2)
    )
    ap1 = ap1 + (_apply(ap1, _EXPAND) - ap1) * amount[..., None]
    if gains:
        ap1 = _grade(ap1, gains)
    ap1 = ap1 + (_apply(ap1, _BLUE_AP1) - ap1) * 0.6
    ap1 = _film(ap1)
    ap1 = ap1 + (_apply(ap1, _BLUE_INV_AP1) - ap1) * 0.6
    return np.maximum(_apply(ap1, _AP1_SRGB), 0)


# -- the engine's default SkyAtmosphere, single scattering -------------------------------------

_R0, _TOP = 6360.0, 60.0
_RAY = np.array([0.175287, 0.409607, 1.0]) * 0.0331
_RAY_H, _MIE_S, _MIE_A, _MIE_H, _MIE_G = 8.0, 0.003996, 0.000444, 1.2, 0.8
_OZONE = np.array([0.345561, 1.0, 0.045188]) * 0.001881


def _density(h: F64) -> tuple[F64, F64, F64]:
    """Rayleigh and Mie scattering and total extinction per km at heights ``h`` (km)."""
    h = np.asarray(h)[..., None]
    ray = np.exp(-h / _RAY_H) * _RAY
    mie = np.exp(-h / _MIE_H) * _MIE_S
    ozone = np.clip(1 - np.abs(h - 25.0) / 15.0, 0, None) * _OZONE
    return ray, mie, ray + np.exp(-h / _MIE_H) * (_MIE_S + _MIE_A) + ozone


def _ray_length(h0: float | F64, mu: float) -> float | F64:
    r = _R0 + h0
    b, c = r * mu, r * r - (_R0 + _TOP) ** 2
    return -b + np.sqrt(np.maximum(b * b - c, 0))


def _optical(h0: float, mu: float, n: int = 256) -> F64:
    length = _ray_length(h0, mu)
    t = (np.arange(n) + 0.5) / n * length
    r = _R0 + h0
    return _density(np.sqrt(r * r + t * t + 2 * r * t * mu) - _R0)[2].sum(0) * (length / n)


def sun_transmittance(elevation_deg: float) -> F64:
    """The sun's transmittance through the atmosphere to the ground."""
    return np.exp(-_optical(0.0, float(np.sin(np.radians(elevation_deg)))))


def sky_irradiance(elevation_deg: float, nz: int = 24, nphi: int = 48, n: int = 96) -> F64:
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


# -- the model ---------------------------------------------------------------------------------


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
def _sky_terms(elevation_deg: float) -> tuple[F64, F64]:
    return sun_transmittance(elevation_deg), sky_irradiance(elevation_deg)


def illuminant(light: Light) -> F64:
    """Linear RGB irradiance on flat ground per unit albedo: direct sun plus the sky."""
    t_sun, sky = _sky_terms(light.elevation_deg)
    direct = np.sin(np.radians(light.elevation_deg)) * t_sun
    total = direct + sky * np.asarray(light.sky_luminance_factor)
    return light.sun_lux / np.pi * np.asarray(light.sun_colour) * total


def exposure(
    bake_linear: NDArray[np.floating], light: Light, bias_ev: float, band_pct: tuple[float, float]
) -> tuple[float, float]:
    """Histogram auto-exposure over the lit bake, and the band's mean luminance."""
    y = _dot(bake_linear * illuminant(light), REC709)
    lo, hi = np.percentile(y, band_pct)
    band = float(y[(y >= lo) & (y <= hi)].mean())
    return MID_GREY * 2.0**bias_ev / band, band


def discount(lab: NDArray[np.floating]) -> F64:
    """The screenshot method's own measurement: L x0.95 capped at 0.86, C x0.9."""
    out = np.array(lab, np.float64)
    out[..., 0] = np.minimum(out[..., 0] * 0.95, 0.86)
    out[..., 1:] *= 0.9
    return out


def measured_lab(albedo_linear: NDArray[np.floating] | list[float], light: Light, e: float) -> F64:
    """What a screenshot of flat, lit ground of this albedo measures, in OKLab."""
    scene = e * np.asarray(albedo_linear, np.float64) * illuminant(light)
    display = tonemap(scene, light.gains)
    return discount(oklab(np.clip(display, 1e-9, 1.0)))


def lab_of_hex(hex_colour: str) -> F64:
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

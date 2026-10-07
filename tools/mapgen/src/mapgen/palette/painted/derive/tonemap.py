"""UE5's filmic tonemapper with its default curve, and the 3 x 3 colour algebra it runs on.

Every product is written out in a fixed order, never BLAS. The matrices and the curve's
constants are the engine's; docs/map/calibration.md section 31 lists them.
"""

from __future__ import annotations

import math
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F64Grid

__all__ = ["MID_GREY", "RGB", "Gains", "apply3", "dot3", "tonemap"]

RGB: TypeAlias = tuple[float, float, float]
#: ``ColorCorrectAll`` gains: shadows, midtones, highlights.
Gains: TypeAlias = tuple[RGB, RGB, RGB]

MID_GREY = 0.18


def _matrix(*rows: RGB) -> F64Grid:
    return np.array(rows, np.float64)


def _mul(*matrices: F64Grid) -> F64Grid:
    """The product of 3 x 3 matrices, left to right, each entry summed in a fixed order."""
    out = matrices[0]
    for b in matrices[1:]:
        a, out = out, np.empty((3, 3))
        for i in range(3):
            out[i] = (a[i, 0] * b[0] + a[i, 1] * b[1]) + a[i, 2] * b[2]
    return out


def apply3(c: NDArray[np.floating], m: F64Grid) -> F64Grid:
    """``c @ m.T``, each channel summed in a fixed order."""
    out = np.empty(np.shape(c), np.float64)
    for i in range(3):
        out[..., i] = (c[..., 0] * m[i, 0] + c[..., 1] * m[i, 1]) + c[..., 2] * m[i, 2]
    return out


def dot3(c: NDArray[np.floating], w: F64Grid) -> F64Grid:
    """``c @ w`` over the last axis, in a fixed order."""
    return np.asarray((c[..., 0] * w[0] + c[..., 1] * w[1]) + c[..., 2] * w[2], np.float64)


def _inv(m: F64Grid) -> F64Grid:
    """A 3 x 3 inverse by its adjugate."""
    adj = np.empty((3, 3))
    for i in range(3):
        for j in range(3):
            r = [k for k in range(3) if k != j]
            c = [k for k in range(3) if k != i]
            sign = -1.0 if (i + j) % 2 else 1.0
            adj[i, j] = sign * (m[r[0], c[0]] * m[r[1], c[1]] - m[r[0], c[1]] * m[r[1], c[0]])
    return adj / ((m[0, 0] * adj[0, 0] + m[0, 1] * adj[1, 0]) + m[0, 2] * adj[2, 0])


_SRGB_XYZ = _matrix(
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_XYZ_SRGB = _matrix(
    (3.2409699419, -1.5373831776, -0.4986107603),
    (-0.9692436363, 1.8759675015, 0.0415550574),
    (0.0556300797, -0.2039769589, 1.0569715142),
)
_XYZ_AP1 = _matrix(
    (1.6410233797, -0.3248032942, -0.2364246952),
    (-0.6636628587, 1.6153315917, 0.0167563477),
    (0.0117218943, -0.0082844420, 0.9883948585),
)
_AP1_XYZ = _matrix(
    (0.6624541811, 0.1340042065, 0.1561876870),
    (0.2722287168, 0.6740817658, 0.0536895174),
    (-0.0055746495, 0.0040607335, 1.0103391003),
)
_XYZ_AP0 = _matrix(
    (1.0498110175, 0.0, -0.0000974845),
    (-0.4959030231, 1.3733130458, 0.0982400361),
    (0.0, 0.0, 0.9912520182),
)
_AP0_XYZ = _matrix(
    (0.9525523959, 0.0, 0.0000936786),
    (0.3439664498, 0.7281660966, -0.0721325464),
    (0.0, 0.0, 1.0088251844),
)
_D65_D60 = _matrix(
    (1.01303, 0.00610531, -0.014971),
    (0.00769823, 0.998165, -0.00503203),
    (-0.00284131, 0.00468516, 0.924507),
)
_D60_D65 = _matrix(
    (0.987224, -0.00611327, 0.0159533),
    (-0.00759836, 1.00186, 0.00533002),
    (0.00307257, -0.00509595, 1.08168),
)
_WIDE_XYZ = _matrix(
    (0.5441691, 0.2395926, 0.1666943),
    (0.2394656, 0.7021530, 0.0583814),
    (-0.0023439, 0.0361834, 1.0552183),
)
_BLUE = _matrix(
    (0.9404372683, -0.0183068787, 0.0778696104),
    (0.0083786969, 0.8286599939, 0.1629613092),
    (0.0005471261, -0.0008833746, 1.0003362486),
)
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


def _smoothstep(a: float, b: float, x: NDArray[np.floating]) -> F64Grid:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _glow_and_red(ap1: F64Grid) -> F64Grid:
    """The ACES glow and red modifier, in AP0."""
    ap0 = apply3(ap1, _AP1_AP0)
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


def _film(ap1: F64Grid) -> F64Grid:
    """UE's ``FilmToneMap`` from AP1 to AP1, default curve."""
    w = np.maximum(apply3(_glow_and_red(ap1), _AP0_AP1), 0)
    w = dot3(w, _AP1_Y)[..., None] * 0.04 + w * 0.96
    toe_s, sh_s = 1 + _BLACK - _TOE, 1 + _WHITE - _SHOULDER
    bt = (MID_GREY + _BLACK) / toe_s - 1
    toe_m = math.log10(MID_GREY) - 0.5 * math.log((1 + bt) / (1 - bt)) * (toe_s / _SLOPE)
    straight_m = (1 - _TOE) / _SLOPE - toe_m
    shoulder_m = _SHOULDER / _SLOPE - straight_m
    lc: F64Grid = np.log10(np.maximum(w, 1e-10))
    straight: F64Grid = _SLOPE * (lc + straight_m)
    toe: F64Grid = -_BLACK + 2 * toe_s / (1 + np.exp(-2 * _SLOPE / toe_s * (lc - toe_m)))
    shoulder: F64Grid = 1 + _WHITE - 2 * sh_s / (1 + np.exp(2 * _SLOPE / sh_s * (lc - shoulder_m)))
    toe_c = np.where(lc < toe_m, toe, straight)
    sh_c = np.where(lc > shoulder_m, shoulder, straight)
    t = np.clip((lc - toe_m) / (shoulder_m - toe_m), 0, 1)
    if shoulder_m < toe_m:  # UE's own guard; true for the default curve
        t = 1 - t
    t = (3 - 2 * t) * t * t
    tone = toe_c + (sh_c - toe_c) * t
    return np.maximum(dot3(tone, _AP1_Y)[..., None] * 0.07 + tone * 0.93, 0)


def _grade(ap1: F64Grid, gains: Gains) -> F64Grid:
    """UE's ``ColorCorrectAll`` gains only: shadows below luma 0.09, highlights from 0.5 to 1."""
    shadows, mids, highs = (np.asarray(g, np.float64) for g in gains)
    luma = dot3(ap1, _AP1_Y)
    ws = 1 - _smoothstep(0, 0.09, luma)
    wh = _smoothstep(0.5, 1.0, luma)
    return ap1 * (shadows * ws[..., None] + mids * (1 - ws - wh)[..., None] + highs * wh[..., None])


def tonemap(scene_linear: F64Grid, gains: Gains | None = None) -> F64Grid:
    """Scene-linear sRGB to display-linear sRGB through UE5's default film curve."""
    ap1 = apply3(np.asarray(scene_linear, np.float64), _SRGB_AP1)
    luma = dot3(ap1, _AP1_Y)
    chroma = ap1 / np.maximum(luma, 1e-10)[..., None]
    off = (chroma - 1) ** 2
    amount = (1 - 2 ** (-4 * ((off[..., 0] + off[..., 1]) + off[..., 2]))) * (
        1 - 2 ** (-4 * luma**2)
    )
    ap1 = ap1 + (apply3(ap1, _EXPAND) - ap1) * amount[..., None]
    if gains:
        ap1 = _grade(ap1, gains)
    ap1 = ap1 + (apply3(ap1, _BLUE_AP1) - ap1) * 0.6
    ap1 = _film(ap1)
    ap1 = ap1 + (apply3(ap1, _BLUE_INV_AP1) - ap1) * 0.6
    return np.maximum(apply3(ap1, _AP1_SRGB), 0)

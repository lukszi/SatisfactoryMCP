"""Colour spaces every unit shares: sRGB, linear light and OKLab, the tone curve, the flat light.

Björn Ottosson's OKLab matrices. A leaf, so the lighting model and the painters read one copy.
Every sum of a pixel's channels is elementwise in one fixed order, never BLAS
(docs/map/renders.md section 40, "Fixed-order sums").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid

__all__ = [
    "LUMA",
    "Colours",
    "ToneCurve",
    "by_luminance",
    "colour_at",
    "flat_light",
    "linear_from_oklab",
    "linear_to_srgb",
    "linear_to_srgb_unit",
    "luminance",
    "number_at",
    "oklab",
    "sky_sun_light",
    "srgb_to_linear",
    "srgb_unit_to_linear",
    "through_matrix",
    "tone",
    "unit_luminance",
    "untone",
    "weighted_channels",
]

#: A colour, a list of them, or an array of them with the channels last.
Colours: TypeAlias = NDArray[np.number] | Sequence[float] | Sequence[Sequence[float]]

#: A luminance curve: ``(luminance, knee, white)`` to luminance.
ToneCurve: TypeAlias = Callable[[NDArray[np.floating], float, float], NDArray[np.floating]]


#: OKLab, Björn Ottosson's matrices.
_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    np.float32,
)
#: Their float32 inverses, as LAPACK gave them, written out so no library computes them.
_M1_INV = np.array(
    [
        [4.076742, -3.307712, 0.23096998],
        [-1.2684381, 2.6097577, -0.3413194],
        [-0.0041960552, -0.7034187, 1.7076147],
    ],
    np.float32,
)
_M2_INV = np.array(
    [
        [1.0, 0.39633778, 0.21580376],
        [1.0, -0.105561346, -0.06385417],
        [1.0, -0.089484185, -1.2914855],
    ],
    np.float32,
)

#: Rec. 709 luminance weights.
LUMA: F32Grid = np.array([0.2126, 0.7152, 0.0722], np.float32)


def weighted_channels(c: NDArray[np.floating], weights: F32Grid) -> NDArray[np.floating]:
    """``c``'s three channels times ``weights``, summed as ``(c0 w0 + c1 w1) + c2 w2``.

    Each product and sum rounds to ``c``'s float type on its own, with no fused multiply-add,
    so a pixel's bits depend on nothing but its own channels.
    """
    total = c[..., 0] * weights[0]
    total = total + c[..., 1] * weights[1]
    return total + c[..., 2] * weights[2]


def luminance(c: NDArray[np.floating]) -> NDArray[np.floating]:
    """The Rec. 709 luminance of linear colour ``c``, in ``c``'s float type."""
    return weighted_channels(c, LUMA)


def _planes(c: NDArray[np.floating]) -> NDArray[np.floating]:
    """Channels first, contiguous: the products below then read whole planes."""
    return np.moveaxis(c, -1, 0).copy()


def _mixed(planes: NDArray[np.floating], matrix: F32Grid) -> NDArray[np.floating]:
    """Each row of ``matrix`` over the channel planes, summed as ``weighted_channels`` does."""
    out = np.empty(planes.shape, np.result_type(planes, matrix))
    term = np.empty(planes.shape[1:], out.dtype)
    for i, row in enumerate(matrix):
        total = out[i, ...]
        np.multiply(planes[0, ...], row[0], out=total)
        total += np.multiply(planes[1, ...], row[1], out=term)
        total += np.multiply(planes[2, ...], row[2], out=term)
    return out


def _interleaved(planes: NDArray[np.floating]) -> NDArray[np.floating]:
    return np.ascontiguousarray(np.moveaxis(planes, 0, -1))


def through_matrix(c: NDArray[np.floating], matrix: F32Grid) -> NDArray[np.floating]:
    """``c @ matrix.T`` without BLAS: each output channel a ``weighted_channels`` of a row."""
    return _interleaved(_mixed(_planes(c), matrix))


def srgb_unit_to_linear(c: NDArray[np.floating]) -> NDArray[np.floating]:
    """sRGB on a 0..1 scale to linear light, in ``c``'s own float type."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb_unit(values: Colours) -> NDArray[np.floating]:
    """Linear light to sRGB on a 0..1 scale, clipped to [0, 1], in the input's float type."""
    c = np.clip(values, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def srgb_to_linear(values: Colours) -> F32Grid:
    """sRGB bytes, 0..255, to linear light."""
    c = np.asarray(values, np.float32) / np.float32(255.0)
    return srgb_unit_to_linear(c).astype(np.float32)


def linear_to_srgb(values: Colours) -> F32Grid:
    """Linear light to sRGB on a 0..255 scale, as floats."""
    return (linear_to_srgb_unit(values) * 255.0).astype(np.float32)


def oklab(linear: Colours) -> F32Grid:
    cone = np.cbrt(_mixed(_planes(np.asarray(linear, np.float32)), _M1))
    return _interleaved(_mixed(cone, _M2)).astype(np.float32, copy=False)


def linear_from_oklab(lab: Colours) -> F32Grid:
    cone = _mixed(_planes(np.asarray(lab, np.float32)), _M2_INV) ** 3
    return _interleaved(_mixed(cone, _M1_INV)).astype(np.float32, copy=False)


def unit_luminance(colour: Colours) -> F32Grid:
    c = np.asarray(colour, np.float32)
    return (c / luminance(c)).astype(np.float32, copy=False)


def tone(y: NDArray[np.floating], knee: float, white: float) -> NDArray[np.floating]:
    """The luminance shoulder: identity below ``knee``, then Reinhard to ``white``; 1 is none."""
    if knee >= 1.0:
        return y
    span = 1.0 - knee
    x = np.maximum(y - knee, 0.0) / span
    top = (white - knee) / span
    return np.where(y > knee, knee + span * x * (1 + x / (top * top)) / (1 + x), y)


def untone(o: NDArray[np.floating], knee: float, white: float) -> NDArray[np.floating]:
    """``tone`` inverted, up to the curve's flat top."""
    if knee >= 1.0:
        return o
    span = 1.0 - knee
    u = np.clip((o - knee) / span, 0.0, 0.999)
    a, b = 1.0 / ((white - knee) / span) ** 2, 1.0 - u
    return np.where(o > knee, knee + span * (np.sqrt(b * b + 4 * a * u) - b) / (2 * a), o)


def by_luminance(
    c: NDArray[np.floating], curve: ToneCurve, knee: float, white: float
) -> NDArray[np.floating]:
    """``curve`` applied to the luminance of linear colour ``c``, the chromaticity kept."""
    y = np.maximum(luminance(c), 1e-7)
    return c * (curve(y, knee, white) / y)[..., None]


def sky_sun_light(
    sky: Colours, sun: Colours, ambient: np.float32, shade: NDArray[np.floating] | None = None
) -> NDArray[np.floating]:
    """Sky plus sun, each of unit luminance: ``ambient`` of it the sky, the rest the sun times
    ``shade``. With no shade, or a shade of 1, it is the flat-ground light."""
    sun_term = (1 - ambient) * unit_luminance(sun)
    if shade is not None:
        sun_term = sun_term * shade[..., None]
    return ambient * unit_luminance(sky) + sun_term


def number_at(block: Mapping[str, object], key: str) -> float:
    """A palette's or a light block's number, read as JSON holds it."""
    value = block[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} is {value!r}, not a number")
    return float(value)


def colour_at(block: Mapping[str, object], key: str) -> list[float]:
    """A palette's or a light block's colour, a list of numbers, read as JSON holds it."""
    value = block[key]
    items: list[object] = value if isinstance(value, list) else []
    channels = [float(c) for c in items if isinstance(c, int | float)]
    if not isinstance(value, list) or len(channels) != len(items):
        raise TypeError(f"{key} is {value!r}, not a colour")
    return channels


def flat_light(
    p: Mapping[str, object],
    ndl: NDArray[np.floating],
    ndl_flat: np.float32 | NDArray[np.floating],
) -> NDArray[np.floating]:
    """Sky plus sun of the palette ``p``, equal to one on flat ground."""
    ambient = np.float32(number_at(p, "ambient"))
    return sky_sun_light(colour_at(p, "sky"), colour_at(p, "sun"), ambient, ndl / ndl_flat)

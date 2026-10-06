"""Colour spaces every unit shares: sRGB, linear light and OKLab, the tone curve, the flat light.

Björn Ottosson's OKLab matrices. A leaf, so the lighting model and the painters read one copy.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid
from satisfactory_mcp.core.jsontypes import JsonValue

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
    "number_at",
    "oklab",
    "sky_sun_light",
    "srgb_to_linear",
    "srgb_unit_to_linear",
    "tone",
    "unit_luminance",
    "untone",
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
_M1_INV: F32Grid = np.linalg.inv(_M1).astype(np.float32)
_M2_INV: F32Grid = np.linalg.inv(_M2).astype(np.float32)

#: Rec. 709 luminance weights.
LUMA: F32Grid = np.array([0.2126, 0.7152, 0.0722], np.float32)


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
    cone: F32Grid = np.cbrt(np.asarray(linear, np.float32) @ _M1.T)
    return cone @ _M2.T


def linear_from_oklab(lab: Colours) -> F32Grid:
    cone: F32Grid = (np.asarray(lab, np.float32) @ _M2_INV.T) ** 3
    return cone @ _M1_INV.T


def unit_luminance(colour: Colours) -> F32Grid:
    c = np.asarray(colour, np.float32)
    luminance: F32Grid | np.float32 = c @ LUMA
    return c / luminance


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
    y = np.maximum(c @ LUMA, 1e-7)
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


def number_at(block: Mapping[str, JsonValue], key: str) -> float:
    """A palette's or a light block's number, read as JSON holds it."""
    value = block[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} is {value!r}, not a number")
    return float(value)


def colour_at(block: Mapping[str, JsonValue], key: str) -> list[float]:
    """A palette's or a light block's colour, a list of numbers, read as JSON holds it."""
    value = block[key]
    channels = (
        [float(c) for c in value if isinstance(c, int | float)] if isinstance(value, list) else []
    )
    if not isinstance(value, list) or len(channels) != len(value):
        raise TypeError(f"{key} is {value!r}, not a colour")
    return channels


def flat_light(
    p: Mapping[str, JsonValue],
    ndl: NDArray[np.floating],
    ndl_flat: np.float32 | NDArray[np.floating],
) -> NDArray[np.floating]:
    """Sky plus sun of the palette ``p``, equal to one on flat ground."""
    ambient = np.float32(number_at(p, "ambient"))
    return sky_sun_light(colour_at(p, "sky"), colour_at(p, "sun"), ambient, ndl / ndl_flat)

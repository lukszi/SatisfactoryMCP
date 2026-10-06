"""Colour spaces the painted styles share: sRGB, linear light and OKLab, and the flat light.

Björn Ottosson's OKLab matrices. Kept apart from the painters so the tree overlays and the
ground can both use them.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "LUMA",
    "flat_light",
    "linear_from_oklab",
    "linear_to_srgb",
    "oklab",
    "srgb_to_linear",
    "unit_luminance",
]

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
_M1_INV = np.linalg.inv(_M1).astype(np.float32)
_M2_INV = np.linalg.inv(_M2).astype(np.float32)

#: Rec. 709 luminance weights.
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def srgb_to_linear(values) -> np.ndarray:
    c = np.asarray(values, np.float32) / np.float32(255.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(values) -> np.ndarray:
    c = np.clip(values, 0.0, 1.0)
    return (
        np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * 255.0
    ).astype(np.float32)


def oklab(linear) -> np.ndarray:
    return np.cbrt(np.asarray(linear, np.float32) @ _M1.T) @ _M2.T


def linear_from_oklab(lab) -> np.ndarray:
    return (np.asarray(lab, np.float32) @ _M2_INV.T) ** 3 @ _M1_INV.T


def unit_luminance(colour) -> np.ndarray:
    c = np.asarray(colour, np.float32)
    return c / (c @ LUMA)


def flat_light(p: dict, ndl, ndl_flat) -> np.ndarray:
    """Sky plus sun, equal to one on flat ground."""
    ambient = np.float32(p["ambient"])
    return (
        ambient * unit_luminance(p["sky"])
        + (1 - ambient) * unit_luminance(p["sun"]) * (ndl / ndl_flat)[..., None]
    )

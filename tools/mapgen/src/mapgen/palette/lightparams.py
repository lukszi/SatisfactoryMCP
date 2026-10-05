"""What the page's shader reads from a style to light its unlit colour.

The painted style lights in linear light under its luminance tone curve; terrain and
satellite multiply their hillshade into sRGB, which is ``SHADE_FLOOR`` as the ambient share
of the flat-ground light and no tone curve. docs/spatial-and-map.md section 29.
"""

from __future__ import annotations

import math

from mapgen.lighting.hillshade import SHADE_FLOOR, SHADE_RANGE, SUN_ALTITUDE_DEG
from mapgen.palette.styles import PAINTED_PALETTE

__all__ = ["shader_light"]


def shader_light(layer: str) -> dict:
    """``{space, ambient, sky, sun, tone_knee, tone_white}`` for one layer; knee 1 is none."""
    if layer == "painted":
        p = PAINTED_PALETTE
        return {
            "space": "linear",
            "ambient": float(p["ambient"]),
            "sky": [float(v) for v in p["sky"]],
            "sun": [float(v) for v in p["sun"]],
            "tone_knee": float(p["tone"]["knee"]),
            "tone_white": float(p["tone"]["white"]),
        }
    flat = SHADE_FLOOR + SHADE_RANGE * math.sin(math.radians(SUN_ALTITUDE_DEG))
    return {
        "space": "srgb",
        "ambient": round(SHADE_FLOOR / flat, 6),
        "sky": [1.0, 1.0, 1.0],
        "sun": [1.0, 1.0, 1.0],
        "tone_knee": 1.0,
        "tone_white": 1.0,
    }

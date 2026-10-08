"""What the page's shader reads from a style to light its unlit colour.

The painted style lights in linear light under its luminance tone curve; terrain multiplies
its hillshade into sRGB, which is ``SHADE_FLOOR`` as the ambient share of the flat-ground
light and no tone curve, and the relief is relit the same way. Only a style that draws the
tree crowns reads their shadows (``crowns``). docs/map/light-and-crowns.md section 29.
"""

from __future__ import annotations

from mapgen.lighting.hillshade import FLAT_SHADE, SHADE_FLOOR
from mapgen.palette.styles import PAINTED_PALETTE
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["shader_light"]


def shader_light(layer: str) -> JsonObject:
    """``{space, ambient, sky, sun, tone_knee, tone_white, crowns}`` for one layer.

    A knee of 1 is no tone curve.
    """
    if layer == "painted":
        palette = PAINTED_PALETTE
        return {
            "space": "linear",
            "ambient": float(palette["ambient"]),
            "sky": [float(v) for v in palette["sky"]],
            "sun": [float(v) for v in palette["sun"]],
            "tone_knee": float(palette["tone"]["knee"]),
            "tone_white": float(palette["tone"]["white"]),
            "crowns": True,
        }
    return {
        "space": "srgb",
        "ambient": round(SHADE_FLOOR / FLAT_SHADE, 6),
        "sky": [1.0, 1.0, 1.0],
        "sun": [1.0, 1.0, 1.0],
        "tone_knee": 1.0,
        "tone_white": 1.0,
        "crowns": False,
    }

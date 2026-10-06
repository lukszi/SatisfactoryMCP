"""The paint store on disk: its folder, file names and grid."""

from __future__ import annotations

from mapgen.common import LOCAL_DIR
from mapgen.gamedata.vegetation import crown_sprites as crown_data

__all__ = [
    "CANOPY_NAME",
    "CROWN_NAME",
    "GRID",
    "META_NAME",
    "PAINT_DIR",
    "PAINT_DIR_NAME",
    "PIGMENT_NAME",
    "WEIGHT_PREFIX",
    "WEIGHT_SUFFIX",
]


PAINT_DIR_NAME = "paint"
META_NAME = "meta.json"
CANOPY_NAME = "canopy.u8.z"
CROWN_NAME = crown_data.CROWN_TOP_NAME
PIGMENT_NAME = "pigment.rgb.u8.z"
WEIGHT_PREFIX = "w."
WEIGHT_SUFFIX = ".u8.z"

#: The output grid: the heightfield's, 1 m, vertex-aligned on the render frame.
GRID = 7500

#: Where the painted style's paint layers live: an extracted input, tools/gen_paint_layers.py.
PAINT_DIR = LOCAL_DIR / "paint"

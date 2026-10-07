"""The paint store on disk: its folder, file names and grid."""

from __future__ import annotations

from mapgen.common import LOCAL_DIR
from mapgen.gamedata.frame import GRID_PX
from mapgen.gamedata.vegetation.crown_sprites import CROWN_TOP_NAME

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
CROWN_NAME = CROWN_TOP_NAME
PIGMENT_NAME = "pigment.rgb.u8.z"
WEIGHT_PREFIX = "w."
WEIGHT_SUFFIX = ".u8.z"

#: The store's grid: the heightfield's, 1 m, vertex-aligned on the render frame.
GRID = GRID_PX

#: Where the paint command writes the store, and where the painted style reads it.
PAINT_DIR = LOCAL_DIR / PAINT_DIR_NAME

"""The void as a piece of a band draws it, once for every layer, and the land weight the light
reads off it.

docs/map/renders.md section 26, "The open sea, the void and the pits", and
docs/map/light-and-crowns.md section 29, "The land weight".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.terrain.sample import Taps, reads_nothing, sample_plain
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, FloatGrid

__all__ = ["DrawnVoid", "drawn_void", "land_weight"]


class DrawnVoid(NamedTuple):
    """The void over a piece as ``palette.styles.with_void`` blends it, each plane in [0, 1]:
    how much of a pixel it hides, how deep into it the pixel is, whether it is a pit, and
    its rim line."""

    cover: FloatGrid
    falloff: FloatGrid
    pit: FloatGrid
    rim: FloatGrid

    @property
    def ground(self) -> FloatGrid:
        """The share of each pixel the drawn ground keeps under the void and its rim."""
        return (1.0 - self.cover) * (1.0 - self.rim)


def drawn_void(
    missing: BoolMask,
    sea: OpenSea | None,
    linear: Taps,
    rock: F32Grid | None,
    z_m: FloatGrid,
) -> DrawnVoid | None:
    """The open sea's void planes over a piece, None where it draws nothing: without the open
    sea, or with no void under the piece and no pixel without data.

    The cover and rim are kept off the rocks a pixel's ``rock`` coverage holds where they
    stand out of the sea; a pixel without data is all void.
    """
    if sea is None:
        return None
    if not missing.any() and all(reads_nothing(p, linear) for p in (sea.void.cover, sea.void.rim)):
        return None
    cover, falloff, pit, rim = (sample_plain(p, linear) / np.float32(255.0) for p in sea.void)
    if rock is not None:
        # A rock deep in the void, under the sea's level, is the void's, as the artwork has it.
        standing = rock * np.clip(z_m - np.float32(OCEAN_LEVEL_M) + 0.5, 0.0, 1.0)
        cover, rim = cover * (1.0 - standing), rim * (1.0 - standing)
    cover = np.where(missing, np.float32(1.0), np.clip(cover, 0.0, 1.0))
    return DrawnVoid(cover, falloff, pit, rim)


def land_weight(missing: BoolMask, water_cover: FloatGrid, void: DrawnVoid | None) -> FloatGrid:
    """The share of each pixel the light lights: the ground's, under neither the water nor the
    void as drawn. The void is not water; its soft edge and rim fade the light out as they
    fade the ground."""
    dry = np.where(missing, 0.0, 1.0 - water_cover)
    return dry if void is None else dry * void.ground

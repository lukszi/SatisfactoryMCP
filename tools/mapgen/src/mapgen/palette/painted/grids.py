"""Rasters resampled onto the paint's 1 m grid: biome indices, land cells, coarse shares."""

from __future__ import annotations

import numpy as np

from mapgen.palette.painted.rock_grid import ROCK_GRID_M
from mapgen.palette.painted.shapes import BiomeGrid, FieldPlanes, FloatGrid
from mapgen.palette.water.shore import OCEAN_LEVEL_BAND_M, OCEAN_LEVEL_M
from satisfactory_mcp.core.arrays import BoolMask, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["biome_grid", "fine_share", "land_cells"]


def biome_grid(biome: BiomeGrid, rows: int, cols: int) -> U8Grid:
    """The biome raster's index under every texel of the 1 m grid. Nearest."""
    width = biome["width"]
    r = np.clip(((np.arange(rows) + 0.5) * width / rows).astype(np.int64), 0, width - 1)
    c = np.clip(((np.arange(cols) + 0.5) * width / cols).astype(np.int64), 0, width - 1)
    return biome["area"][np.ix_(r, c)]


def land_cells(field: FieldPlanes, shape: tuple[int, int]) -> BoolMask | None:
    """Ground the sea does not cover, on a grid of ``shape`` over the field, nearest; None for a
    field without water planes. Water at the ocean's level is sea whatever its grade, and so is
    the void off the landscape."""
    water, grades = field.water_raster(), field.water_quality_raster()
    if water is None or grades is None:
        return None
    height = field.height_dm
    if height is None:
        return None
    rows = np.minimum(np.arange(shape[0]) * field.height // shape[0], field.height - 1)
    cols = np.minimum(np.arange(shape[1]) * field.width // shape[1], field.width - 1)
    pick = np.ix_(rows, cols)
    level = water[pick].astype(np.float32) / np.float32(hf.DM_PER_M)
    sea = (grades[pick] != hf.WATER_DRY) & (water[pick] != hf.NODATA)
    sea &= np.abs(level - OCEAN_LEVEL_M) <= OCEAN_LEVEL_BAND_M
    return (height[pick] != hf.NODATA) & ~sea


def fine_share(coarse: FloatGrid, rows: int, cols: int) -> U8Grid:
    """A coarse 0..1 weight on the 1 m grid as a 0..255 share, nearest."""
    r = np.minimum(np.arange(rows) // ROCK_GRID_M, coarse.shape[0] - 1)
    c = np.minimum(np.arange(cols) // ROCK_GRID_M, coarse.shape[1] - 1)
    return np.round(coarse[np.ix_(r, c)] * 255.0).astype(np.uint8)

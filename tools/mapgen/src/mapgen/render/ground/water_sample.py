"""The water a piece of a band samples: its surface, level and cover, and the terms every
layer's painter draws it with (docs/map/renders.md section 26 and docs/map/water.md)."""

from __future__ import annotations

from typing import NamedTuple, TypeAlias

import numpy as np

from mapgen.palette.scene import WaterTerms
from mapgen.palette.water.open_sea import OpenSea
from mapgen.palette.water.shore import blend_water, shore_terms
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_alpha, water_depth_fraction
from mapgen.terrain.sample import AxisTaps, reads_nothing, sample_coverage, sample_plain
from mapgen.terrain.sample import sample_surface as sample_raster
from satisfactory_mcp.core.arrays import FloatGrid, I16Grid, U8Grid
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["WaterPlanes", "band_water_terms", "sample_water_surface"]

#: A band's taps: its rows' and its columns'.
_GridTaps: TypeAlias = tuple[AxisTaps, AxisTaps]


class WaterPlanes(NamedTuple):
    """The water a band samples on the field's grid: its level and the wet and measured planes."""

    level: I16Grid
    wet: U8Grid
    measured: U8Grid


def band_water_terms(
    z_m: FloatGrid,
    water_m: FloatGrid,
    wet: FloatGrid,
    measured: FloatGrid,
    blur_px: float,
    reach: U8Grid | None,
    linear: _GridTaps,
    spacing_m: float,
) -> WaterTerms:
    """Recipe 5's water, and within ``reach`` of the sea the ocean's crossing rule. ``wet``
    rides along for the rivers: past the last wet texel, the edge's blur is no water."""
    old_cover = water_alpha(z_m, water_m, wet, measured, blur_px)
    old_depth = water_depth_fraction(z_m, water_m, measured)
    if reach is None:
        terms = blend_water(None, old_cover, old_depth, None, WATER_DEPTH_FULL_M)
    else:
        terms = blend_water(
            sample_coverage(reach, linear),
            old_cover,
            old_depth,
            shore_terms(z_m, spacing_m),
            WATER_DEPTH_FULL_M,
        )
    terms["wet"] = wet
    return terms


def sample_water_surface(
    z_m: FloatGrid,
    water: WaterPlanes | None,
    sea: OpenSea | None,
    smooth: _GridTaps,
    linear: _GridTaps,
) -> tuple[FloatGrid, FloatGrid, FloatGrid, FloatGrid]:
    """One band's water surface, level (NaN where none), wet cover and measured share.

    With the open sea, the wet cover counts only the share of a pixel that is not void, so
    the void's edge is never drawn as land.
    """
    if water is None:
        wet = measured = np.zeros(z_m.shape, np.float32)
        return z_m, np.full(z_m.shape, np.nan, np.float32), wet, measured
    water_dm, water_missing = sample_raster(water.level, smooth, linear, hf.NODATA)
    water_m = water_dm / np.float32(hf.DM_PER_M)
    level_m = np.where(water_missing, np.nan, water_m)
    wet = sample_coverage(water.wet, linear)
    measured = sample_coverage(water.measured, linear) / np.where(wet <= 0.0, 1.0, wet)
    if sea is not None and not reads_nothing(sea.void.cover, linear):
        land = 1.0 - sample_plain(sea.void.cover, linear) / np.float32(255.0)
        wet = np.clip(wet / np.maximum(land, np.float32(1e-3)), 0.0, 1.0)
    return water_m, level_m, wet, np.clip(measured, 0.0, 1.0)

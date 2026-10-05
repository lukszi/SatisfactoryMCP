"""Perched water: a box level standing above its own banks is re-read from the shoreline.

docs/spatial-and-map.md section 28. Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.palette.perched import (  # noqa: E402
    PERCHED_EXCESS_M,
    perched_levels,
    relevel,
    spill_share,
)
from mapgen.palette.shore import OCEAN_LEVEL_M  # noqa: E402
from mapgen.tiles.compose import render_layer  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402

ROWS, COLS, MID, HALF_WIDTH = 160, 120, 60, 8
BOX_TOP_DM = 120


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        _water_raster=lambda: water_dm,
        _water_quality_raster=lambda: grades,
        _height_dm=height_dm,
        x0_cm=0.0,
        y0_cm=0.0,
        spacing_cm=100.0,
    )


def _river():
    """A channel running downhill from 10 m to -6 m through a plain 3 m above its bed, levelled
    at its box's top (12 m) along its whole length, the way a sloped river's box levels it."""
    rows, cols = np.mgrid[0:ROWS, 0:COLS]
    bed_m = 10.0 - 0.1 * rows
    ground_m = bed_m + np.minimum(0.35 * np.abs(cols - MID), 3.0)
    wet = np.abs(cols - MID) <= HALF_WIDTH
    height = np.rint(ground_m * hf.DM_PER_M).astype(np.int16)
    water = np.where(wet, BOX_TOP_DM, hf.NODATA).astype(np.int16)
    grades = np.where(wet, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades, height


def test_a_sloped_river_follows_its_banks_and_never_rises_above_its_box():
    water, grades, height = _river()
    out, meta = perched_levels(_field(water, grades, height))
    wet = grades == hf.WATER_MEASURED
    assert meta["bodies"] == 1 and meta["texels"] > 0
    assert (out[wet] <= BOX_TOP_DM).all(), "never above the box top"
    assert (out[~wet] == water[~wet]).all(), "dry texels untouched"
    depth_m = (out.astype(np.float32) - height) / hf.DM_PER_M
    downstream = wet & (np.arange(ROWS)[:, None] > 100)
    before_m = (water.astype(np.float32) - height) / hf.DM_PER_M
    assert before_m[downstream].min() > 8.0, "the fixture reproduces the deep sheet"
    assert depth_m[downstream].max() <= 3.5, "a river in a 3 m channel is at most that deep"
    assert depth_m[downstream].min() >= 0.0
    # The surface runs downhill with the bed rather than standing flat.
    centre = out[:, MID].astype(np.float32)
    assert centre[150] < centre[110] - 30


def test_a_lake_below_its_banks_and_the_ocean_are_returned_byte_for_byte():
    rows, cols = np.mgrid[0:ROWS, 0:COLS]
    radius = np.hypot(rows - 80, cols - 60)
    height = np.rint((0.08 * radius**2 - 5.0) * hf.DM_PER_M).astype(np.int16)
    lake = radius <= 10
    water = np.where(lake, 25, hf.NODATA).astype(np.int16)
    grades = np.where(lake, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    # The ocean, beside ground far below its level: the artwork's dark deep water.
    water[:, :10] = round(OCEAN_LEVEL_M * hf.DM_PER_M)
    grades[:, :10] = hf.WATER_MEASURED
    height[:, :20] = -800
    out, meta = perched_levels(_field(water, grades, height))
    assert np.array_equal(out, water)
    assert meta["bodies"] == 0


def test_water_the_mask_stops_short_of_is_not_a_perched_level():
    """A lake drawn smaller than its water: dry ground metres under the level beside it, and
    banks rising above the level a few metres out. The box level is right and stays."""
    rows, cols = np.mgrid[0:ROWS, 0:COLS]
    radius = np.hypot(rows - 80, cols - 60)
    height = np.rint(0.5 * (radius - 12.0) * hf.DM_PER_M).astype(np.int16)
    lake = radius <= 6
    water = np.where(lake, 30, hf.NODATA).astype(np.int16)
    grades = np.where(lake, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    out, _meta = perched_levels(_field(water, grades, height))
    assert np.array_equal(out, water)


def test_the_hand_over_is_continuous_and_bounded_by_the_box():
    band = PERCHED_EXCESS_M * hf.DM_PER_M
    level = 500.0
    excess = np.linspace(-10.0, 4 * band, 401, dtype=np.float32)
    got = relevel(level, level - excess)
    assert (got <= level).all()
    assert np.allclose(got[excess <= band], level)
    assert np.allclose(got[excess >= 2 * band], (level - excess)[excess >= 2 * band])
    assert np.abs(np.diff(got)).max() <= 2 * np.abs(np.diff(excess)).max() + 1e-4


def test_the_spill_ring_counts_banks_well_below_the_level_only():
    body = np.zeros((80, 80), bool)
    body[30:50, 30:50] = True
    high = np.where(body, np.nan, 100.0).astype(np.float32)
    assert spill_share(body, high, 80.0, 1.0) == 0.0
    low = np.where(body, np.nan, 0.0).astype(np.float32)
    assert spill_share(body, low, 80.0, 1.0) == 1.0
    unknown = np.full(body.shape, np.nan, np.float32)
    assert spill_share(body, unknown, 80.0, 1.0) == 0.0


def test_a_box_over_lower_water_is_bounded_by_that_water():
    """A higher body's box laid over part of a lake: the piece is bounded by the lake around
    it, not by dry land, and drops to the lake's level."""
    rows, cols = np.mgrid[0:ROWS, 0:COLS]
    height = np.full((ROWS, COLS), -50, np.int16)
    lake = (np.abs(rows - 80) < 60) & (np.abs(cols - 60) < 50)
    height[~lake] = 100
    water = np.where(lake, 0, hf.NODATA).astype(np.int16)
    piece = (np.abs(rows - 80) < 10) & (np.abs(cols - 60) < 10)
    water[piece] = 200
    grades = np.where(lake, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    out, meta = perched_levels(_field(water, grades, height))
    assert meta["bodies"] == 1
    assert (out[piece] == 0).all()
    assert np.array_equal(out[~piece], water[~piece])


def test_the_renderer_takes_the_relevelled_raster():
    assert "water_level" in inspect.signature(render_layer).parameters

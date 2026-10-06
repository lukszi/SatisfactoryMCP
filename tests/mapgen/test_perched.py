"""Perched water: a box level standing above its own banks is re-read from the shoreline,
and a lake's box reaching past its lip over the basin below.

docs/spatial-and-map.md section 38. Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.palette.water.perched import (
    HOLE_DEPTH_MAX_M,
    LIP_DROP_M,
    PERCHED_EXCESS_M,
    perched_levels,
    relevel,
    spill_share,
    water_surfaces,
    wet_holes,
)
from mapgen.palette.water.rivers import water_sources
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.render.compose import render_layer
from satisfactory_mcp.domain.spatial import heightfield as hf

ROWS, COLS, MID, HALF_WIDTH = 160, 120, 60, 8
BOX_TOP_DM = 120


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        water_raster=lambda: water_dm,
        water_quality_raster=lambda: grades,
        height_dm=height_dm,
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


def _lake_with_holes():
    """A lake at 10 m on a bed at 7 m, banks at 15 m, and what the artwork leaves dry in it:
    a dark middle 8 m deep, an arch 10 m wide from shore to shore, an island above the
    water, and a sandbar half a metre under it. The water east of the arch stands 1 m
    higher. Ocean on the west, with a dry patch of its own."""
    n = 200
    height = np.full((n, n), 150, np.int16)
    lake = np.zeros((n, n), bool)
    lake[40:160, 40:160] = True
    height[lake] = 70
    height[:, :20] = -400
    water = np.where(lake, 100, hf.NODATA).astype(np.int16)
    water[:, 70:160][lake[:, 70:160]] = 110
    parts = {
        "middle": (slice(90, 110), slice(90, 110), 20),
        "arch": (slice(40, 160), slice(60, 70), 70),
        "island": (slice(120, 140), slice(120, 140), 120),
        "sandbar": (slice(50, 56), slice(120, 150), 105),
    }
    for rows, cols, ground in parts.values():
        height[rows, cols] = ground
        water[rows, cols] = hf.NODATA
    water[:, :20] = round(OCEAN_LEVEL_M * hf.DM_PER_M)
    water[95:105, 5:15] = hf.NODATA
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades, height, {k: (r, c) for k, (r, c, _g) in parts.items()}


def test_the_holes_the_artwork_leaves_in_a_lake_are_wetted_at_its_surface():
    water, grades, height, parts = _lake_with_holes()
    level, got, meta = wet_holes(_field(water, grades, height), water, water, grades)
    assert (got[parts["middle"]] == hf.WATER_MEASURED).all(), "the dark middle"
    assert (level[parts["middle"]] == 110).all(), "at its own body's surface"
    arch = got[parts["arch"]] == hf.WATER_MEASURED
    assert arch[10:-10].all() and arch.mean() > 0.95, "water under the arch, bar its very ends"
    assert (level[parts["arch"]][arch] == 100).all(), "two bodies over one gap: the lower one"
    assert (got[parts["island"]] == hf.WATER_DRY).all(), "ground above the water stays dry"
    assert (got[parts["sandbar"]] == hf.WATER_DRY).all(), "and so does a sandbar awash"
    assert (got[:, :20] == grades[:, :20]).all(), "the ocean is never touched"
    filled = got != grades
    assert np.array_equal(level[~filled], water[~filled]), "everything else byte for byte"
    assert meta["texels"] == int(filled.sum()) and meta["bodies"] == 2
    assert grades[parts["middle"]].max() == hf.WATER_DRY, "the input is not written"


def test_a_gap_open_to_lower_ground_or_too_deep_is_no_hole():
    """A notch where the bank falls away beside the lake is where its water would run to;
    a pit deeper than a lake middle is a cliff foot. Neither is wetted."""
    water, grades, height, _parts = _lake_with_holes()
    notch = (slice(95, 105), slice(150, 160))
    height[:, 160:] = 30
    height[notch] = 30
    water[notch] = hf.NODATA
    pit = (slice(130, 136), slice(90, 96))
    height[pit] = 110 - round((HOLE_DEPTH_MAX_M + 1) * hf.DM_PER_M)
    water[pit] = hf.NODATA
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    _level, got, _meta = wet_holes(_field(water, grades, height), water, water, grades)
    assert (got[notch] == hf.WATER_DRY).all()
    assert (got[pit] == hf.WATER_DRY).all()


def test_the_water_a_render_draws_carries_the_wetted_holes():
    water, grades, height, parts = _lake_with_holes()
    field = _field(water, grades, height)
    reach = (np.zeros(grades.shape, np.uint8), {"ocean_texels": 0, "reach_texels": 0})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("mapgen.palette.water.perched.ocean_reach", lambda _field: reach)
        got = water_surfaces(field, False)
    assert (got.grades[parts["middle"]] == hf.WATER_MEASURED).all()
    assert (got.level[parts["middle"]] == 110).all()
    assert got.perched["holes"]["texels"] == int((got.grades != grades).sum()) > 0


def test_water_the_river_reconcile_dropped_stays_dropped():
    water, grades, height, parts = _lake_with_holes()
    dropped = np.zeros_like(grades, dtype=bool)
    dropped[parts["arch"]] = True
    _level, got, _meta = wet_holes(_field(water, grades, height), water, water, grades, dropped)
    assert (got[parts["arch"]] == hf.WATER_DRY).all()
    assert (got[parts["middle"]] == hf.WATER_MEASURED).all()


def test_the_renderer_takes_the_relevelled_raster():
    assert "water_level" in inspect.signature(render_layer).parameters


def test_the_rivers_reconciled_water_is_what_gets_relevelled():
    """Where a spline speaks the river reconcile has dropped the box's water already, so the
    membrane only spans what it left; the field's own planes are not read."""
    water, grades, height = _river()
    kept = grades.copy()
    kept[:100] = hf.WATER_DRY
    left = np.where(kept == hf.WATER_DRY, hf.NODATA, water).astype(np.int16)
    rivers = SimpleNamespace(water_dm=left, grades=kept)
    field = _field(water, grades, height)
    reach = (np.zeros((ROWS, COLS), np.uint8), {"ocean_texels": 0, "reach_texels": 0})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("mapgen.palette.water.perched.ocean_reach", lambda _field: reach)
        got = water_surfaces(field, False, rivers)
    assert got.grades is kept
    assert (got.level[:100] == hf.NODATA).all(), "dropped river water stays dropped"
    assert (got.level[100:][kept[100:] == hf.WATER_MEASURED] < BOX_TOP_DM).any()
    plane, wet, _measured = water_sources(field, rivers, got.level)
    assert plane is got.level and not wet[:100].any()
    assert water_surfaces(field, True, rivers).planes is None


# ------------------------------------------------- a lake's box reaching past its lip


FALL_ROWS, FALL_COLS = 160, 200
BOX_DM, SWAMP_DM = 948, -167
LAKE = (slice(20, 140), slice(20, 100))
BASIN = (slice(40, 120), slice(100, 140))
SWAMP = (slice(30, 130), slice(140, 190))


def _fall(swamp=True):
    """The wide fall at (1784, 559) in miniature: a lake 5.8 m deep under its box at 94.8 m,
    high cliffs all round, and past its lip the basin 113 m below, which the box also covers.
    The basin opens east onto a swamp at -16.7 m, or with ``swamp`` off onto dry ground
    standing half a metre above its bed."""
    height = np.full((FALL_ROWS, FALL_COLS), 1100, np.int16)
    water = np.full((FALL_ROWS, FALL_COLS), hf.NODATA, np.int16)
    height[LAKE], water[LAKE] = 890, BOX_DM
    height[BASIN], water[BASIN] = -181, BOX_DM
    height[SWAMP] = -190 if swamp else -176
    if swamp:
        water[SWAMP] = SWAMP_DM
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return water, grades, height


def test_the_basin_below_a_lip_takes_the_level_of_the_water_it_joins():
    water, grades, height = _fall()
    out, meta = perched_levels(_field(water, grades, height))
    assert (out[BASIN] == SWAMP_DM).all(), "the basin stands at the swamp's level"
    assert (out[LAKE] == BOX_DM).all(), "the lake above the lip keeps its box level"
    assert (out[SWAMP] == SWAMP_DM).all()
    assert meta["bodies"] == 1 and meta["largest"][0]["below_a_drop"]
    assert meta["bodies_cut_at_a_drop"] == 1


def test_the_whole_body_would_not_spill_so_the_basin_is_judged_on_its_own():
    """Before the cut the lake's high banks outvoted the basin's swamp: nothing moved."""
    water, grades, height = _fall()
    flat = height.copy()
    flat[BASIN] = 880
    out, meta = perched_levels(_field(water, grades, flat))
    assert np.array_equal(out, water)
    assert meta["bodies"] == 0 and meta["bodies_cut_at_a_drop"] == 0


def test_a_basin_joining_no_water_takes_its_own_low_shoreline():
    water, grades, height = _fall(swamp=False)
    out, _meta = perched_levels(_field(water, grades, height))
    basin = out[BASIN].astype(np.int32)
    assert (basin <= -176).all() and (basin >= -181).all(), "between its bed and its banks"
    assert (out[LAKE] == BOX_DM).all()


def test_a_pit_in_a_lake_whose_level_is_right_is_left_alone():
    """An underwater cliff: the deep side is cut off by the drop, but every bank it has is
    the lake at the same level, so it is no basin below a fall."""
    height = np.full((FALL_ROWS, FALL_COLS), 1100, np.int16)
    water = np.full((FALL_ROWS, FALL_COLS), hf.NODATA, np.int16)
    height[LAKE], water[LAKE] = 900, BOX_DM
    pit = (slice(60, 100), slice(40, 80))
    height[pit] = BOX_DM - round(3 * LIP_DROP_M * hf.DM_PER_M)
    grades = np.where(water != hf.NODATA, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    out, meta = perched_levels(_field(water, grades, height))
    assert np.array_equal(out, water)
    assert meta["bodies"] == 0 and meta["bodies_cut_at_a_drop"] == 1

"""Water in the terrain field: a second surface, never a correction to the ground.

Rows 4 and 5 of ``build_field`` are a measured lake and an open sea standing below the fill
raster's ground; the nearest-water search answers a distance and a level, never a capacity.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.heightfields import FAKE_SPACING, FAKE_X0, FAKE_Y0, build_field


def test_water_is_a_second_surface_and_never_a_correction_to_the_ground(tmp_path):
    """The channel says a lake stands here; the ground stays exactly where the field put it.

    Measured, not stylistic: the generator's own docstring records that gating terrain on
    this channel made the field worse (nodes trim90 0.93 against 0.77), so a reading under
    water reports both numbers and moves neither.
    """
    field = hf.load_field(build_field(tmp_path))
    under = field.texel_reading(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert under.z_m == -15.0, "the ground was moved to the water surface"
    assert under.water_m == 2.0
    assert under.submerged is True
    assert under.water_depth_m == 17.0
    assert field.texel_reading(FAKE_X0, FAKE_Y0).submerged is False


def test_a_depth_the_field_cannot_measure_is_None_and_never_a_zero(tmp_path):
    """The open ocean, which is where every plausible version of this goes wrong.

    Row 5 is water at -17.0 m over a ground the FILL layer puts at -15.0 m -- the real
    arrangement over most of this world's sea, where a 3.9 m-quantised raster rounds above
    a surface it is nowhere near. Two things must hold and neither is automatic. The texel
    is submerged, which ``water_m > z_m`` denies. And its depth is ``None``, because
    ``-17.0 - -15.0`` is a number nobody measured and ``max(..., 0)`` would print it as a
    perfectly reasonable-looking 0.0 m of water.
    """
    field = hf.load_field(build_field(tmp_path))
    sea = field.texel_reading(FAKE_X0, FAKE_Y0 + 5 * FAKE_SPACING)
    assert sea.water_m == -17.0
    assert sea.z_m == -15.0, "the ground was moved to meet the water"
    assert sea.water_quality == hf.WATER_LEVEL_ONLY
    assert sea.submerged is True, "a sea surface below the fill raster read as dry land"
    assert sea.water_depth_m is None, "an unmeasured depth was reported as a number"
    assert sea.depth_known is False


def test_a_field_written_before_the_quality_byte_reads_exactly_as_it_used_to(tmp_path):
    """Missing is not dry. Without ``waterq.u8.z`` the old comparison is all there is.

    So the lake still reads as water and the sea still reads as land -- which is the bug
    that raster was added to fix, and is the honest behaviour for a field that predates it.
    """
    field = hf.load_field(build_field(tmp_path, quality=False))
    lake = field.texel_reading(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert lake.water_m == 2.0 and lake.submerged is True
    assert lake.water_depth_m == 17.0
    sea = field.texel_reading(FAKE_X0, FAKE_Y0 + 5 * FAKE_SPACING)
    assert sea.water_m == -17.0 and sea.submerged is False


def test_a_field_without_a_water_channel_still_answers(tmp_path):
    """Water is decoded separately and only if asked, so its absence costs one attribute."""
    field = hf.load_field(build_field(tmp_path, water=False))
    reading = field.texel_reading(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert reading.z_m == -15.0
    assert reading.water_m is None and reading.submerged is False


def test_the_nearest_water_is_a_distance_and_a_surface(tmp_path):
    """Row 4 is 4 m north of row 0 and its surface is 2.0 m, both read off the raster."""
    field = hf.load_field(build_field(tmp_path))
    near = field.nearest_water(0.0, FAKE_Y0, 500.0)
    assert near.distance_m == pytest.approx(4.0)
    assert near.level_m == pytest.approx(2.0)
    assert near.quality == hf.WATER_MEASURED
    assert near.covered_pct == pytest.approx(100.0 * 6 * 7 / (11 * 11), abs=0.1)


def test_no_water_in_range_says_how_much_of_the_range_it_actually_saw(tmp_path):
    """ "None within 300 m" over a box that ran off the grid is a weaker claim than it
    sounds, and only ``covered_pct`` carries the difference."""
    field = hf.load_field(build_field(tmp_path))
    near = field.nearest_water(0.0, FAKE_Y0, 300.0)
    assert near.distance_m is None
    assert near.covered_pct < 100.0


def test_a_field_with_no_water_plane_answers_nothing_rather_than_nowhere(tmp_path):
    """Two different silences: this field cannot speak about water at all, which must not
    read as "there is none nearby"."""
    field = hf.load_field(build_field(tmp_path, water=False))
    assert field.nearest_water(0.0, FAKE_Y0, 500.0) is None


def test_without_a_quality_plane_the_ocean_row_reads_as_dry(tmp_path):
    """The fallback's known blind spot, pinned where it is visible: row 5's sea surface
    stands BELOW the fill raster's ground, so a comparison-only field walks past it to the
    pond a metre away instead."""
    graded = hf.load_field(build_field(tmp_path / "graded"))
    assert graded.nearest_water(0.0, FAKE_Y0 + 5 * FAKE_SPACING, 500.0).level_m == pytest.approx(
        -17.0
    )
    blind = hf.load_field(build_field(tmp_path / "blind", quality=False))
    walked_past = blind.nearest_water(0.0, FAKE_Y0 + 5 * FAKE_SPACING, 500.0)
    assert walked_past.distance_m == pytest.approx(1.0)
    assert walked_past.level_m == pytest.approx(2.0)

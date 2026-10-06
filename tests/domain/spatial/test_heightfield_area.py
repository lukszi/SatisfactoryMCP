"""Reading the terrain field over an AREA: a build decision is about a pad, not a point.

The area reader and the point reader must not hold different opinions about one texel.
"""

from __future__ import annotations

import numpy as np
import pytest

from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.heightfields import (
    FAKE_SPACING,
    FAKE_W,
    FAKE_X0,
    FAKE_Y0,
    build_field,
    build_shaped_field,
)


def _whole_field(field: hf.Field) -> hf.Area:
    last_x = field.x0_cm + (field.width - 1) * field.spacing_cm
    last_y = field.y0_cm + (field.height - 1) * field.spacing_cm
    return field.window(field.x0_cm, field.y0_cm, last_x, last_y)


def test_an_area_agrees_with_the_point_reader_texel_for_texel(tmp_path):
    """The seam. Two readers over one raster is two chances to be wrong about no-data or
    submersion, and the area reader vectorises rules the point reader spells out."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)

    zs, wet, blind = [], 0, 0
    for row in range(field.height):
        for col in range(field.width):
            reading = field.at(
                field.x0_cm + col * field.spacing_cm, field.y0_cm + row * field.spacing_cm
            )
            if reading is None:
                blind += 1
                continue
            zs.append(reading.z_m)
            wet += reading.submerged

    total = field.width * field.height
    assert area.texels == len(zs)
    assert area.nodata_pct == pytest.approx(100.0 * blind / total, abs=0.05)
    assert area.submerged_pct == pytest.approx(100.0 * wet / total, abs=0.05)
    assert area.z_min_m == pytest.approx(min(zs))
    assert area.z_max_m == pytest.approx(max(zs))


def test_a_rectangle_off_the_grid_is_all_no_data_and_never_None(tmp_path):
    """Silence about a pad is an answer. ``None`` would make "nothing is known there" and
    "you asked wrong" the same result, and a caller cannot tell those apart afterwards."""
    field = hf.load_field(build_field(tmp_path))
    area = field.window(500_000.0, 500_000.0, 500_100.0, 500_100.0)
    assert area.nodata_pct == 100.0
    assert area.texels == 0
    assert area.z_range_m is None and area.roughness_m is None


def test_a_pad_hanging_off_the_edge_reports_the_part_nobody_measured(tmp_path):
    """The percentages are over the REQUESTED rectangle. Over the clipped one instead, a
    pad 90% off the map would report a confident 0% no-data about its last strip."""
    field = hf.load_field(build_field(tmp_path))
    last_x = FAKE_X0 + (FAKE_W - 1) * FAKE_SPACING
    area = field.window(last_x - FAKE_SPACING, FAKE_Y0, last_x + 8 * FAKE_SPACING, FAKE_Y0)
    assert area.requested_texels == 10
    assert area.texels == 2
    assert area.nodata_pct == pytest.approx(80.0)


def test_slope_and_roughness_are_different_questions(tmp_path):
    """A clean ramp is steep and perfectly buildable; a lumpy flat is neither steep nor
    pleasant. One "flatness" number would call them the same, which is the whole reason
    the two are published apart."""
    ramp = np.tile(np.arange(64, dtype=np.float64) * 0.5, (64, 1))
    field = hf.load_field(build_shaped_field(tmp_path / "ramp", ramp))
    smooth = _whole_field(field)
    assert smooth.slope_mean_deg == pytest.approx(26.6, abs=0.2)
    assert smooth.roughness_m == pytest.approx(0.0, abs=0.02)

    rng = np.random.default_rng(20260805)
    lumpy = rng.normal(0.0, 3.0, size=(64, 64))
    field = hf.load_field(build_shaped_field(tmp_path / "lumpy", lumpy))
    rough = _whole_field(field)
    assert rough.roughness_m == pytest.approx(3.0, abs=0.3)
    assert rough.slope_mean_deg > smooth.slope_mean_deg


def test_a_big_window_decimates_and_says_so(tmp_path):
    """Decimation cannot see detail finer than its new spacing, so a caller comparing two
    areas has to be able to see that one of them was subsampled."""
    field = hf.load_field(build_shaped_field(tmp_path, np.zeros((400, 400))))
    whole = field.window(0.0, 0.0, 39_900.0, 39_900.0, max_texels=10_000)
    assert whole.stride == 4
    assert whole.requested_texels == 400 * 400
    assert whole.nodata_pct == pytest.approx(0.0, abs=0.5)


def test_water_below_the_ground_is_measured_against_the_DRY_ground(tmp_path):
    """A pond 15 m below a plateau rim, against the pad median, would read as a drop of
    nearly nothing -- because on a mostly-flooded pad the pad median IS the pond bed."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)
    # The dry rows are 12.3, 245.6 and -7.8 m, median 12.3; every row median is -7.8,
    # because two of the five valid rows are the flooded ones. Measured against the pad
    # median the drop would be -0.3 m -- a plateau rim reported as level with the water.
    assert area.water_below_ground_m == pytest.approx(12.3 - (-7.5), abs=0.05)
    # And the limit the same field exposes: two water bodies at 2.0 m and -17.0 m give a
    # median between them that is neither. One rectangle, one lake, is the supported case.
    assert area.water_level_m == pytest.approx(-7.5)


def test_an_area_states_which_layer_answered_so_a_reader_can_distrust_it(tmp_path):
    """The fill layer is quantised to 3.9 m. A roughness of 2 m over mostly-fill ground is
    below its own error bar, and only the provenance mix says so."""
    field = hf.load_field(build_field(tmp_path))
    area = _whole_field(field)
    assert area.provenance_pct[hf.PROV_LANDSCAPE] == pytest.approx(100.0 * 2 / 6, abs=0.05)
    assert area.coarse_pct == pytest.approx(100.0 * 2 / 6, abs=0.05)

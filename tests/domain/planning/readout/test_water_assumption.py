"""The one planning input with no data behind it: how many Water Extractors a site holds.

The cap was silent, and silence was the bug -- a whole-map ``max_mw`` takes every one of the
200 it is allowed and wants 246, so the answer was shaped by an assumption that never named
itself. It still is an assumption, because submerged area is not an extractor count, and the
tests here pin both halves: the plan now SAYS what it assumed and whether that assumption is
binding, and where it is sited it says what the terrain actually measures instead.

The field is synthetic, in ``tmp_path``: the real raster is gitignored and no test may
need one.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from satisfactory_mcp.domain.planning.report import build_plan_report
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.presenters.text.plan_factory import render_plan_factory

#: A plan that pumps and is nowhere near the cap, and one that hits it exactly. Both are
#: measured on the committed projection, so neither needs the reader's own save.
ALUMINIUM = dict(objective="max_item", target_item="Aluminum Ingot", exports=["Aluminum Ingot"])
WHOLE_MAP_POWER = dict(objective="max_mw", exports=["MW"])

#: The synthetic world: 2 km square at 10 m, land at +100 m, and everything west of
#: x = -300 m under water whose surface is -17.0 m -- within a metre of the -17.36 m this
#: save's own 23 pumps measure, so the join to `water_volumes` has something to recognise.
GRID = 200
SPACING_CM = 1000.0
ORIGIN_CM = -100_000.0
LAND_M = 100.0
LAKE_BED_M = -30.0
LAKE_SURFACE_M = -17.0
SHORE_X_M = -300.0


@pytest.fixture
def field_at(tmp_path, monkeypatch):
    """Point the loader at a synthetic field and return where its shoreline is."""
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    xs = ORIGIN_CM / 100.0 + np.arange(GRID) * SPACING_CM / 100.0
    lake = np.broadcast_to(xs <= SHORE_X_M, (GRID, GRID))

    height = np.where(lake, LAKE_BED_M * 10, LAND_M * 10).astype(np.int16)
    prov = np.full((GRID, GRID), hf.PROV_LANDSCAPE, np.uint8)
    wet = np.where(lake, int(LAKE_SURFACE_M * 10), hf.NODATA).astype(np.int16)
    grade = np.where(lake, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)

    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(height))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    (directory / hf.WATER_NAME).write_bytes(hf.encode_i16(wet))
    (directory / hf.WATER_QUALITY_NAME).write_bytes(hf.encode_u8(grade))
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": GRID,
                    "height": GRID,
                    "spacing_cm": SPACING_CM,
                    "x0_cm": ORIGIN_CM,
                    "y0_cm": ORIGIN_CM,
                },
                "nodata": hf.NODATA,
                "provenance": {"1": {"name": "landscape", "accuracy_m": 0.2}},
            }
        ),
        encoding="utf-8",
    )
    # `field_dir`, not `config.data_dir`: the node and region tables live under the same
    # root, and moving that wholesale takes the planner's own inputs with it.
    monkeypatch.setattr(hf, "field_dir", lambda local_dir=None: directory)
    return directory


def water_note(g, st, plan_kwargs, **site) -> str:
    """The one line plan_factory prints about water, for a plan over the fixture world."""
    report = build_plan_report(g, st, dict(plan_kwargs), objective=plan_kwargs["objective"], **site)
    out = render_plan_factory(
        g,
        st,
        report,
        objective=plan_kwargs["objective"],
        only_free_nodes=False,
        limit=2,
    )
    return next(line for line in out.splitlines() if "Water Extractor(s)" in line)


# ------------------------------------------------- what the constant actually binds


def test_the_default_cap_binds_a_whole_map_power_plan_and_the_plan_says_so(game, state):
    """Not a hypothetical: the cap is 200, this solve takes all 200, and 400 buys 246 pumps
    and about 7.9 GW more. The constant's own comment used to claim it never bound."""
    report = build_plan_report(game, state, dict(WHOLE_MAP_POWER), objective="max_mw")
    assert report.water_pumps == 200
    assert report.water_cap == 200
    assert report.water_binding

    freed = build_plan_report(
        game, state, dict(WHOLE_MAP_POWER, water_extractors=400), objective="max_mw"
    )
    assert freed.water_pumps > report.water_pumps
    assert freed.prepared.solution.net_mw > report.prepared.solution.net_mw

    note = water_note(game, state, WHOLE_MAP_POWER)
    assert "BINDING" in note
    assert "WATER_EXTRACTOR_CAP_ASSUMED" in note


def test_a_plan_the_cap_does_not_bind_still_names_the_number_it_assumed(game, state):
    """Aluminium is the canonical water-hungry plan and wants 21 pumps, so the cap is
    merely present. Present and silent was the old behaviour, and it is what let a reader
    take 200 for a measured capacity."""
    report = build_plan_report(game, state, dict(ALUMINIUM), objective="max_item")
    assert report.water_pumps == 21
    assert not report.water_binding

    note = water_note(game, state, ALUMINIUM)
    assert "ASSUMED, not measured" in note
    assert "200" in note
    assert "NOT SITED" in note


def test_a_cap_the_caller_measured_is_never_called_an_assumption(game, state):
    note = water_note(game, state, dict(ALUMINIUM, water_extractors=4))
    assert "the number you measured and passed" in note
    assert "ASSUMED, not measured" not in note


# ------------------------------------------------- what a site buys


def test_a_sited_plan_measures_the_water_rather_than_assuming_it(game, state, field_at):
    """The pad is 300 m inland of a lake 117 m below it, and every one of those numbers is
    read off the terrain rather than declared."""
    note = water_note(game, state, ALUMINIUM, site_at="0,0")
    assert "MEASURED" in note
    assert "the 200x200 m pad is dry" in note
    assert "nearest standing water is 300 m away" in note
    assert "-17.0 m" in note
    assert "117.0 m below its dry ground" in note
    # Joined to the save's own pumps, which measure sea level independently of the field.
    assert "this world's sea level" in note


def test_a_pad_standing_in_the_water_reports_the_share_and_refuses_to_invent_a_drop(
    game, state, field_at
):
    """An all-submerged pad has no rim left to measure a drop against, and the pad MEDIAN
    is the lake bed there -- using it says the water stands 13 m above the ground."""
    note = water_note(game, state, ALUMINIUM, site_at="-600,0")
    assert "100% of the 200x200 m pad stands under water at -17.0 m" in note
    assert "keeps no dry ground to measure a drop against" in note


def test_a_site_with_no_water_in_range_says_so_instead_of_going_quiet(game, state, field_at):
    """The actionable half of a negative measurement: the water has to come from further
    out, which is a pipe run the plan does not otherwise mention."""
    note = water_note(game, state, ALUMINIUM, site_at="400,0")
    assert "no standing water within 500 m of the 200x200 m pad" in note
    assert "piped in from further out" in note


def test_a_site_the_field_cannot_place_is_reported_and_does_not_take_the_plan_down(game, state):
    report = build_plan_report(
        game, state, dict(ALUMINIUM), objective="max_item", site_at="nowhere at all"
    )
    assert report.prepared.ok
    assert report.prepared.request.site is None
    assert any("site_at:" in e for e in report.prepared.request.site_errors)


# ------------------------------------------------- what is deliberately NOT derived


def test_the_terrain_never_moves_the_extractor_count(game, state, field_at):
    """The line between a measurement and a placement claim, pinned. A pad wholly under
    water and a pad 300 m from any produce the SAME plan: how many pumps a body of water
    holds needs shoreline geometry, clearance and overlap, and none of the three is in a
    height field. Deriving a count from `submerged_pct` is the confidently-wrong answer
    this measurement exists to avoid, so it must stay unbuildable by accident."""
    dry = build_plan_report(game, state, dict(ALUMINIUM), objective="max_item", site_at="400,0")
    wet = build_plan_report(game, state, dict(ALUMINIUM), objective="max_item", site_at="-600,0")

    assert dry.site_water.pad.submerged_pct == 0.0
    assert wet.site_water.pad.submerged_pct == 100.0
    assert dry.water_cap == wet.water_cap
    assert dry.water_pumps == wet.water_pumps
    # And the plan id, which is what makes two responses provably about one plan, is blind
    # to the site for the same reason.
    assert dry.prepared.request.plan_id == wet.prepared.request.plan_id


def test_a_machine_with_no_terrain_field_says_it_did_not_measure(
    game, state, tmp_path, monkeypatch
):
    """The ordinary case on a clone: the raster is gitignored, and "I did not look" must
    not come out sounding like "there is no water here"."""
    monkeypatch.setattr(hf, "field_dir", lambda local_dir=None: tmp_path / "absent")
    note = water_note(game, state, ALUMINIUM, site_at="0,0")
    assert "no terrain field, so nothing was measured" in note
    assert "no standing water" not in note

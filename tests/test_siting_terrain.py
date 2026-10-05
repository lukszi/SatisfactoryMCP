"""A site's z from the heightfield: the terrain fills a missing height and never overrides one.

Runs on the small layered fixture tile from ``test_heightfield`` -- a 7x6 m ramp with one rock
texel and a bare-terrain plane under it -- so no real field is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from satisfactory_mcp.domain.planning import siting
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.test_heightfield import build_layered_field


@pytest.fixture
def field(tmp_path):
    return hf.load_field(build_layered_field(tmp_path))


def _state(*positions):
    """A world holding machines at the given save positions (cm)."""
    records = [{"cls": "Build_X", "pos": list(p)} for p in positions]
    return SimpleNamespace(_all_records=lambda: records, player_position=lambda: None)


def test_a_bare_coordinate_gets_the_terrain_median_under_its_pad(field):
    sit = siting.resolve_plan_site(_state(), "3,2", "2x2", terrain_field=field)
    assert sit.z_m == pytest.approx(3.0)
    assert sit.z_source == "terrain"
    t = sit.terrain
    assert (t["surface"], t["provenance"]) == ("ground", "landscape")
    assert (t["z_min_m"], t["z_max_m"]) == (2.0, 50.0)
    assert t["ambiguous_pct"] == pytest.approx(11.1, abs=0.1)
    assert t["ambiguous"], "a ninth of the pad is a rock top"
    assert sit.to_dict()["z_source"] == "terrain"
    assert "terrain z 3m" in sit.terrain_line()
    assert sit.describe().startswith("origin 3,2,3m (terrain z, ground) (from 3,2)")


def test_a_typed_z_always_wins_and_the_terrain_rides_beside_it(field):
    sit = siting.resolve_plan_site(_state(), "3,2,40", "2x2", terrain_field=field)
    assert (sit.z_m, sit.z_source) == (40.0, "given")
    assert sit.terrain["z_m"] == pytest.approx(3.0)
    assert sit.terrain["hint_from"] == "given"


def test_a_point_without_a_footprint_reads_bilinear_and_the_hint_picks_the_floor(field):
    under = siting.terrain_z(field, 3.0, 2.0, hint_m=4.0, hint_from="you")
    assert (under["surface"], under["z_m"]) == ("terrain", pytest.approx(3.0, abs=0.01))
    on_top = siting.terrain_z(field, 3.0, 2.0)
    assert (on_top["surface"], on_top["z_m"], on_top["ambiguous"]) == ("ground", 50.0, True)
    assert on_top["bare_m"] == pytest.approx(3.0, abs=0.01)


def test_what_stands_on_the_pad_is_the_hint_when_nothing_else_gives_one(field):
    st = _state((300.0, 200.0, 5000.0))
    sit = siting.resolve_plan_site(st, "3,2", "2x2", terrain_field=field)
    assert sit.terrain["hint_from"] == "built"
    assert sit.terrain["hint_m"] == 50.0


def test_no_field_and_no_data_are_none_with_a_reason(field):
    absent = siting.resolve_plan_site(_state(), "3,2", "2x2", terrain_field=None)
    assert absent.z_m is None and absent.z_source == ""
    assert "gen_world_heightmap" in absent.terrain["reason"]
    off = siting.resolve_plan_site(_state(), "500,500", "2x2", terrain_field=field)
    assert off.z_m is None
    assert off.terrain["reason"] == "outside the map"


def test_a_dragged_pad_gets_its_z_and_a_stated_one_is_kept(field):
    dragged = {"origin_m": [3.0, 2.0, None], "footprint_m": [2.0, 2.0], "footprint_source": "given"}
    out = siting.with_terrain_z(_state(), dragged, field)
    assert out["origin_m"][2] == pytest.approx(3.0)
    assert out["z_source"] == "terrain"
    moved = {**out, "origin_m": [1.0, 2.0, out["origin_m"][2]]}
    again = siting.with_terrain_z(_state(), moved, field)
    assert again["origin_m"][2] == pytest.approx(1.0), "a terrain z follows the pad"
    typed = {**dragged, "origin_m": [3.0, 2.0, 12.5], "z_source": "given"}
    assert siting.with_terrain_z(_state(), typed, field)["origin_m"][2] == 12.5


def test_an_old_record_without_z_source_still_parses():
    plan = SimpleNamespace(siting={"origin_m": [1.0, 2.0, 3.0], "footprint_m": [4.0, 4.0]})
    sit = siting.parse(plan)
    assert (sit.z_m, sit.z_source, sit.terrain) == (3.0, "", None)
    assert sit.terrain_line() is None

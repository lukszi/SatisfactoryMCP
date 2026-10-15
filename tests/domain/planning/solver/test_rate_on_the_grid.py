"""A rate request with no goal and no exports is on the grid: it draws power, builds none.

docs/planning.md §8.1 (the default goal).
"""

from __future__ import annotations

import re

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools.planning._requests import exports_for

pytestmark = pytest.mark.integration

RATE = {"export_minimums": {"Computer": 2}}


@pytest.fixture(autouse=True)
def _reference_world(use_world, projection, game):
    use_world(lambda: WorldState(projection=projection, game=game))


def _figure(out: str, name: str) -> float:
    found = re.search(rf"\b{name}=(-?[\d.]+)", out)
    assert found, out[:600]
    return float(found.group(1))


def _plan_id(out: str) -> str:
    found = re.search(r"\[plan ([0-9a-f]+)", out)
    assert found, out[:300]
    return found.group(1)


def test_only_a_goalless_rate_with_no_exports_gets_its_items_as_exports():
    assert exports_for(None, None, None, {"Computer": 2}) == ["Computer"]
    assert exports_for(None, None, ["MW"], {"Computer": 2}) == ["MW"]
    assert exports_for("max_mw", None, None, {"Computer": 2}) is None
    assert exports_for(None, "some plan", None, {"Computer": 2}) is None
    assert exports_for(None, None, None, {"MW": 100}) is None
    # Power asked for alongside an item stays an export: that plant powers itself.
    assert exports_for(None, None, None, {"Computer": 2, "MW": 100}) == ["Computer", "MW"]


def test_computers_with_no_exports_draw_from_the_grid():
    out = srv.plan_factory(**RATE)
    assert out.startswith("# min_raw_power over "), out[:300]
    net, drawn = _figure(out, "net_MW"), _figure(out, "grid_import_MW")
    # No generator: the plan's whole draw comes from the grid, and nothing is exported as MW.
    assert net < 0
    assert drawn == pytest.approx(-net, abs=0.01)
    assert "exports: Computer=2\n" in out
    assert "plan draws from the existing grid" in out
    assert _plan_id(out) == _plan_id(srv.plan_factory(exports=["Computer"], **RATE))


def test_diff_vs_save_checks_the_draw_against_the_headroom():
    out = srv.diff_vs_save(**RATE)
    assert _figure(out, "target_MW") < 0
    assert re.search(r"headroom_MW=[\d.]+ .*fits=(yes|NO)", out), out[:600]
    assert _plan_id(out) == _plan_id(srv.plan_factory(**RATE))


def test_an_explicit_max_mw_request_still_builds_power():
    out = srv.plan_factory(objective="max_mw", **RATE)
    assert out.startswith("# max_mw over ")
    assert _figure(out, "grid_import_MW") == 0
    assert _figure(out, "MW") > 0


def test_an_explicit_export_list_is_untouched():
    out = srv.plan_factory(exports=["MW"], **RATE)
    assert out.startswith("# min_raw_power over ")
    assert _figure(out, "grid_import_MW") == 0
    assert _figure(out, "net_MW") >= 0

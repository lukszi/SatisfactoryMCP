"""``plan_from_factory`` over the reference world's named factories (docs/planning.md §8.5o)."""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.stored.planlog import PlanLog
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp import app
from tests.support.reference_world import FIXTURE_WORLD


@pytest.fixture
def named(labelled, projection, game, use_world, monkeypatch):
    monkeypatch.setattr(app, "save_token", lambda st: "sav:test")
    use_world(lambda: WorldState(projection=projection, game=game))
    return labelled


def test_the_tool_stores_a_plan_and_lists_how_it_differs(named):
    out = srv.plan_from_factory(factory="tier 1&2")
    assert out.startswith('# plan "tier 1&2 as built" v1')
    assert "solves=yes" in out
    assert "Modular Engine: built 1 Manufacturer at 250%" in out
    assert "forget_plan name='tier 1&2 as built' undoes it" in out
    plan = PlanLog(FIXTURE_WORLD).find("tier 1&2 as built")
    assert plan is not None and plan.factory == "tier 1&2"


def test_a_factory_that_solves_back_says_so(named):
    out = srv.plan_from_factory(factory="biofuel setup")
    assert "solves to the build" in out


def test_an_unknown_factory_makes_no_plan(named):
    out = srv.plan_from_factory(factory="no such factory")
    assert out.startswith("! no factory named 'no such factory'")
    assert out.endswith("; no plan made")
    assert PlanLog(FIXTURE_WORLD).heads() == []

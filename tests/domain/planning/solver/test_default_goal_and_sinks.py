"""A rate request solves for least raw, then least power; solid byproducts are sunk and listed.

docs/planning.md §8.1 (the default goal) and §8.2 (sunk byproducts).
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.solver.lp import RAW_TIE_SLACK
from satisfactory_mcp.domain.planning.solver.prepare import prepare
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools.planning._requests import goal_for

pytestmark = pytest.mark.integration

COMPUTERS = {"exports": ["Computer"], "export_minimums": {"Computer": 2}}
#: Fuel with every recipe that eats its Polymer Resin banned: the resin has to be sunk.
FUEL = {
    "exports": ["Fuel"],
    "export_minimums": {"Fuel": 100},
    "exclude_recipes": ["Alternate: Diluted Fuel", "Alternate: Heavy Oil Residue"],
}


@pytest.fixture(autouse=True)
def _reference_world(use_world, projection, game):
    use_world(lambda: WorldState(projection=projection, game=game))


def test_a_rate_request_without_a_goal_is_least_raw_then_power():
    assert goal_for(None, None, {"Computer": 2}) == "min_raw_power"
    assert goal_for(None, None, {"MW": 100}) == "max_mw"
    assert goal_for(None, None, None) == "max_mw"


def test_an_explicit_goal_and_a_recalled_plan_keep_theirs():
    assert goal_for("min_machines", None, {"Computer": 2}) == "min_machines"
    # The recalled plan's stored goal applies; the default must not override it.
    assert goal_for(None, "some plan", {"Computer": 2}) == "max_mw"


@pytest.mark.parametrize("request_", [COMPUTERS, {"export_minimums": {"Computer": 2}}])
def test_computers_at_two_a_minute_solve_under_the_default_goal(request_):
    out = srv.plan_factory(**request_)
    assert out.startswith("# min_raw_power over "), out[:300]
    assert "Computer=2" in out


def test_an_explicit_goal_still_wins():
    out = srv.plan_factory(objective="min_machines", **COMPUTERS)
    assert out.startswith("# min_machines over ")


def test_the_power_tie_break_stays_within_the_raw_slack(game, state):
    """Least raw first; power is only traded against raw within RAW_TIE_SLACK."""
    least_raw = prepare(game, state, {**COMPUTERS, "objective": "min_raw"}).solution
    tied = prepare(game, state, {**COMPUTERS, "objective": "min_raw_power"}).solution
    assert least_raw is not None and tied is not None
    assert tied.objective_value <= least_raw.objective_value * (1 + RAW_TIE_SLACK) + 1e-4
    # Measured: 108.9 MW against 110.1 MW drawn from the grid for the same two Computers.
    assert tied.net_mw > least_raw.net_mw


def test_a_solid_byproduct_is_sunk_and_listed(game, state):
    out = srv.plan_factory(**FUEL)
    assert "sunk: Polymer Resin=75" in out
    assert "# sunk byproducts: " in out
    assert "Polymer Resin\t75/min\t1\t30" in out
    assert "plan sinks 1 byproduct(s): 1 AWESOME Sink(s), 30 MW, counted" in out

    sol = prepare(game, state, {**FUEL, "objective": "min_raw_power"}).solution
    assert sol is not None
    built = sum(row["machines"] for row in sol.processes)
    assert sol.machines_total == pytest.approx(built + 1, abs=1e-6)


def test_explain_byproducts_lists_the_sunk_byproducts():
    out = srv.explain_byproducts(**FUEL)
    assert "sunk byproducts, kept for a later use: Polymer Resin 75/min (1 sink(s), 30 MW)" in out


def test_the_web_plan_lists_the_sunk_byproducts(game, state):
    from satisfactory_mcp.domain.planning.readout.summary import solve_summary

    out = solve_summary(game, state, {**FUEL, "objective": "min_raw_power"})
    assert out["feasible"] is True
    assert any(
        w.startswith("sunk byproducts, kept for a later use: Polymer Resin")
        for w in out["warnings"]
    )


def test_a_fluid_byproduct_is_never_offered_a_sink():
    """Sinks are on by default, so the probe never suggests them; a fluid cannot go there."""
    out = srv.plan_factory(
        objective="min_machines",
        exports=["Plastic"],
        export_minimums={"Plastic": 60},
        only_recipes=["Recipe_Plastic_C"],
    )
    notes = [line for line in out.splitlines() if line.startswith("! byproduct with no outlet")]
    assert notes, out
    assert "Heavy Oil Residue" in notes[0]
    assert "allow_sinks" not in notes[0]
    assert "cannot go to an AWESOME Sink" in notes[0]

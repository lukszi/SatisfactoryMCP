"""Extractor overclocking, the shared node cap, and the power/mw spellings."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.optimize import (
    Scenario,
    extractor_processes,
    normalise_objective,
    solve,
)
from satisfactory_mcp.domain.planning.scenario import build_scenario
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
OIL_NODES = {("Build_OilPump_C", CRUDE, "normal"): 4}


# ------------------------------------------------------ extractor clocks


def test_extractor_clocks_apply_only_to_extractors(game, state):
    req = build_scenario(
        game,
        state,
        sources=list(REFERENCE_FIELD),
        extractor_clocks=[1.0, 2.5],
    )
    sol = solve(req.scenario)
    assert sol.ok
    fast = [p for p in sol.processes if p["kind"] == "extractor" and p["clock"] > 1.0 + 1e-9]
    assert fast, "expected the solver to take the overclocked node mode"
    # Production machines were never offered anything above 100%.
    for p in sol.processes:
        if p["kind"] == "recipe":
            assert p["clock"] <= 1.0 + 1e-9, p["label"]


def test_overclocking_nodes_raises_output_because_nodes_are_the_cap(game, state):
    """A node set is fixed, so running it faster is the only way to get more from it.
    This is the whole reason the option exists."""
    kwargs = dict(sources=list(REFERENCE_FIELD), objective="max_mw", exports=["MW"])
    base = solve(build_scenario(game, state, **kwargs).scenario)
    fast = solve(
        build_scenario(game, state, extractor_clocks=[1.0, 1.5, 2.0, 2.5], **kwargs).scenario
    )
    assert base.ok and fast.ok
    assert fast.net_mw > base.net_mw


def test_clock_modes_share_one_node_cap(game):
    """THE trap in this feature. Offering a node set at two clocks creates two
    columns; capping each separately would let the solver mine every node twice."""
    sc = Scenario(
        game=game,
        recipes=[],
        objective="max_item",
        target_item=CRUDE,
        exports=(CRUDE,),
        extractor_nodes=dict(OIL_NODES),
        extractor_clocks=(1.0, 2.5),
        grid_import_mw=1e6,
    )
    sol = solve(sc)
    assert sol.ok
    built = sum(p["machines"] for p in sol.processes if p["kind"] == "extractor")
    assert built <= 4, f"used {built} pumps for 4 nodes"
    # And the answer is the honest maximum: 4 nodes at 250%.
    pump = game.buildings["Build_OilPump_C"]
    assert sol.objective_value == pytest.approx(4 * pump.extract_rate("normal", 2.5))


def test_a_clock_beyond_the_buildings_maximum_is_not_offered(game):
    """max_clock already accounts for power shard slots; asking for more must not
    invent a mode the game cannot build."""
    sc = Scenario(
        game=game,
        recipes=[],
        extractor_nodes=dict(OIL_NODES),
        extractor_clocks=(1.0, 99.0),
    )
    modes = {p.clock for p in extractor_processes(sc)}
    assert 99.0 not in modes
    assert 1.0 in modes


def test_extractor_clocks_change_the_plan_id(game, state):
    a = build_scenario(game, state, sources=list(REFERENCE_FIELD))
    b = build_scenario(game, state, sources=list(REFERENCE_FIELD), extractor_clocks=[1.0, 2.5])
    assert a.plan_id != b.plan_id


# -------------------------------------------------------- power aliases


@pytest.mark.parametrize(
    "spelling", ["max_power", "maximise_power", "maximize_power", "max_watts", "MAX_POWER"]
)
def test_power_spellings_mean_max_mw(spelling):
    assert normalise_objective(spelling) == "max_mw"


@pytest.mark.parametrize("spelling", ["min_mw", "minimise_power", "minimize_power"])
def test_mw_spellings_mean_min_power(spelling):
    assert normalise_objective(spelling) == "min_power"


def test_canonical_names_pass_through():
    for name in ("max_mw", "max_item", "min_raw", "min_machines", "min_power"):
        assert normalise_objective(name) == name


def test_scenario_normalises_on_construction(game):
    """Downstream code compares objective strings, so exactly one spelling may reach
    it -- otherwise max_power would fall through to the unknown-objective branch."""
    sc = Scenario(game=game, recipes=[], objective="max_power")
    assert sc.objective == "max_mw"


@pytest.mark.parametrize("word", ["MW", "mw", "power", "Power"])
def test_power_is_accepted_as_an_export_in_any_spelling(game, state, word):
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD), exports=[word])
    from satisfactory_mcp.domain.planning.optimize import MW

    assert MW in req.scenario.exports

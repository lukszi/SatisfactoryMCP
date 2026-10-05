"""The payback follow-ups F1a-F6a: docs/planner-payback-horizon_contract.md §10."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import prices
from satisfactory_mcp.domain.planning.optimize import Scenario, machine_mw, solve
from satisfactory_mcp.domain.planning.planlog import PlanLog
from satisfactory_mcp.domain.planning.prices import tiers_path as real_tiers_path
from satisfactory_mcp.domain.planning.scenario import build_scenario

CONSTRUCTOR = "Build_ConstructorMk1_C"
REFINERY = "Build_OilRefinery_C"


# ------------------------------------------------------------------ F2a: shared tier memory


@pytest.fixture
def tier_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    monkeypatch.setattr(prices, "tiers_path", real_tiers_path)
    return real_tiers_path("W")


def test_the_tier_memory_lives_beside_the_plan_log(tier_file):
    assert tier_file.parent == PlanLog.dir_for("W")
    assert tier_file.name == "tiers.json"


def test_a_second_process_keeps_the_tier_the_first_one_stored(game, tier_file):
    first = game.buildings[CONSTRUCTOR].build_cost[0]
    line = (CONSTRUCTOR, first.item)
    plenty = {first.item: 20 * first.amount * 16}
    near = {first.item: 20 * first.amount * 6}
    assert prices.material_tiers(game, plenty, {}, world="W")[line] == 0.25
    stored = json.loads(tier_file.read_text(encoding="utf-8"))
    assert stored["schema"] == prices.TIER_SCHEMA
    assert stored["tiers"][f"{CONSTRUCTOR}|{first.item}"] == 0.25
    # Nothing in process memory: the band is read back from the file, as another process
    # reading the same world would.
    assert prices.material_tiers(game, near, {}, world="W")[line] == 0.25
    assert prices.material_tiers(game, near, {}, world="")[line] == 0.5


def test_an_unreadable_tier_file_starts_afresh(game, tier_file):
    first = game.buildings[CONSTRUCTOR].build_cost[0]
    tier_file.parent.mkdir(parents=True)
    tier_file.write_text("{not json", encoding="utf-8")
    tiers = prices.material_tiers(game, {}, {}, world="W")
    assert tiers[(CONSTRUCTOR, first.item)] == 4.0
    assert json.loads(tier_file.read_text(encoding="utf-8"))["schema"] == prices.TIER_SCHEMA


def test_a_tier_file_from_a_newer_version_is_not_overwritten(game, tier_file):
    tier_file.parent.mkdir(parents=True)
    newer = json.dumps({"schema": prices.TIER_SCHEMA + 1, "tiers": {}})
    tier_file.write_text(newer, encoding="utf-8")
    prices.material_tiers(game, {}, {}, world="W")
    assert tier_file.read_text(encoding="utf-8") == newer


# ------------------------------------------------------------------ F1a: power goals priced by the horizon

SPIRE = {
    "objective": "max_mw",
    "sources": ["region:Spire Coast"],
    "exports": ["MW", "Plastic", "Rubber"],
    "export_minimums": {"Plastic": 600.0, "Rubber": 250.0},
    "extractor_clocks": [1.0, 1.5, 2.0, 2.5],
    "exclude_recipes": ["Turbofuel", "Alternate: Compacted Coal", "Coal-Powered Generator"],
}


def test_a_machine_costs_its_points_over_the_horizon_in_mw(game):
    sc = Scenario(game=game, recipes=[], build_points={REFINERY: 9000.0}, power_price=250.0)
    assert machine_mw(sc, REFINERY) == 5.0
    sc.payback_hours = 10.0
    assert machine_mw(sc, REFINERY) == pytest.approx(9000 / 2500)
    assert machine_mw(sc, "Build_Unknown_C") == 5.0
    sc.power_price = 0.0
    assert machine_mw(sc, REFINERY) == 5.0


def test_zero_hours_keeps_the_flat_machine_price(game, state):
    flat = solve(build_scenario(game, state, **SPIRE).scenario)
    priced = solve(build_scenario(game, state, payback_hours=0, power_price=999, **SPIRE).scenario)
    assert flat.processes == priced.processes and flat.net_mw == pytest.approx(28252.5, abs=0.1)
    assert round(flat.machines_total) == 286


def test_a_horizon_prices_the_power_goal_in_build_points(game, state):
    def used(hours):
        sol = solve(build_scenario(game, state, payback_hours=hours, **SPIRE).scenario)
        return sol, {p["label"] for p in sol.processes}

    plain, before = used(0.0)
    short, after = used(1.0)
    # The 32 Blenders cost 146,162 points each (Heavy Modular Frames are scarce here): at 1 h
    # that is ~580 MW a machine, so the packaged route through Refineries replaces them.
    assert "Alternate: Diluted Fuel" in before and "Alternate: Diluted Fuel" not in after
    assert "Alternate: Diluted Packaged Fuel" in after
    assert short.net_mw < plain.net_mw

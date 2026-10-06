"""Hard-drive advice: the counterfactual deltas, and the baseline they hang off.

The advisor's headline number is printed next to an invitation to compare it with
plan_factory. These tests pin that it IS plan_factory's number, and that the
non-obvious verdicts the tool exists to produce survive the change.
"""

from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.core.gamedata.model import Recipe
from satisfactory_mcp.domain.planning import advisor
from satisfactory_mcp.domain.planning.optimize import MW, Scenario, Solution, solve
from satisfactory_mcp.domain.planning.scenario import build_scenario, resolve_item
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.select import select_nodes
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration

NORTH = ["north"]

TURBO_HEAVY_FUEL = "Schematic_Alternate_TurboHeavyFuel_C"
TURBO_BLEND_FUEL = "Schematic_Alternate_TurboBlendFuel_C"
STEEL_ROD = "Schematic_Alternate_SteelRod_C"
COATED_CABLE = "Schematic_Alternate_CoatedCable_C"
CHARCOAL = "Schematic_Alternate_Coal1_C"
#: Blocked behind Schematic_8-x, and carrying mTechTier 0 while it is blocked.
TURBO_PRESSURE_MOTOR = "Schematic_Alternate_TurboPressureMotor_C"

#: The two recipes that make Schematic_Alternate_Turbo*Fuel_C a three-recipe unlock.
PACKAGED_TURBOFUEL = ("Recipe_PackagedTurboFuel_C", "Recipe_UnpackageTurboFuel_C")


def _plan_factory_net_mw(game, state, sources) -> float:
    """What plan_factory prints for `objective=max_mw, exports=[MW]` on this basket."""
    return solve(
        build_scenario(game, state, objective="max_mw", sources=sources, exports=["MW"]).scenario
    ).net_mw


# ---------------------------------------------------------------- the baseline


def test_baseline_is_the_number_plan_factory_reports(game, state):
    """The advisor prints its baseline beside a suggestion to compare with
    plan_factory. If this drifts, the two tools report different quantities under the
    same name -- the advisor once said 171,882 MW for the north where plan_factory
    said 92,269, because the advisor fed raw materials in as free raw_caps."""
    ev = advisor.evaluate_candidates(state, [], NORTH)
    assert ev.baseline["net_mw"] == pytest.approx(_plan_factory_net_mw(game, state, NORTH))


def test_advisor_scenarios_extract_rather_than_conjure_raw_material(game, state):
    """Every scenario must carry extractor processes and NO free raw_caps. A raw
    variable has no extractor, so it charges no extraction power and no node count
    limits it -- the plan it prices could not be built."""
    for obj in advisor.standard_objectives():
        sc = advisor._request(state, NORTH, obj).scenario
        assert sc.raw_caps == {}, f"{obj.key} reintroduced free raw material"
        assert sc.extractor_nodes, f"{obj.key} has no extractors, so raw material is free"


def test_min_machines_objectives_inherit_the_grid_import_allowance(state):
    """build_scenario derives grid_import_mw from the export set. Restating that rule
    here would let it drift: a min_machines solve with no import allowance is forced
    to be self-powered and silently reports a much larger machine count."""
    by_key = {
        o.key: advisor._request(state, NORTH, o).scenario for o in advisor.standard_objectives()
    }
    assert by_key["net_mw"].grid_import_mw is None  # MW is exported, so no import
    assert by_key["min_machines_for_plastic"].grid_import_mw == 1e6


def test_free_raw_caps_inflate_the_baseline_by_most_of_a_factor(game, state):
    """The bug, reproduced. Same nodes, same recipes, but as raw_caps instead of
    extractors: extraction power vanishes and the answer nearly doubles. If this
    stops holding, the fix has stopped doing anything."""
    table = nodes_mod.load_nodes()
    sel = select_nodes(NORTH, table.nodes, resolve_resource=lambda q: resolve_item(game, q))
    rows = nodes_mod.annotate(sel.nodes, game, state.projection, state.unlocked_building_ids)
    caps: dict[str, float] = {}
    for r in rows:
        if r["kind"] == "node" and r["rate"] > 0 and r["reachable"]:
            caps[r["resource"]] = caps.get(r["resource"], 0.0) + r["rate"]
    caps["Desc_Water_C"] = 24000.0
    free = solve(
        Scenario(
            game=game,
            recipes=[r.cls for r in state.unlocked_recipes("part")],
            objective="max_mw",
            exports=(MW,),
            raw_caps=caps,
            allow_sinks=True,
            buildings_available=state.unlocked_building_ids,
        )
    )
    assert free.net_mw > 1.25 * _plan_factory_net_mw(game, state, NORTH)


def test_basket_is_described_the_way_plan_factory_describes_sources(game, state):
    """Same scope, same words. A bespoke phrasing ("resource basket ... at 100%
    clock") reads as a different quantity even when the number matches."""
    ev = advisor.evaluate_candidates(state, [], NORTH)
    expected = build_scenario(game, state, sources=NORTH).selection.description
    assert ev.basket == expected


def test_a_misspelled_source_refuses_instead_of_scoring_an_empty_scope(state):
    """A typo'd region selects no nodes -- select_nodes never widens (§7.3, in
    docs/spatial-and-map.md) -- but build_scenario still grants water pumps, so the empty
    scope SOLVES: feasible, net_mw 0.0, and therefore a 0 delta on every option. Without
    the refusal the user reads "neither option is worth anything" and never learns they
    misspelled a region."""
    sc = advisor._request(state, ["region:Nowhereland"], advisor.standard_objectives()[0]).scenario
    assert {k[0] for k in sc.extractor_nodes} <= {"Build_WaterPump_C"}
    empty = solve(sc)
    assert empty.ok and empty.net_mw == 0.0, "the silent answer is a confident zero, not an error"
    with pytest.raises(ValueError, match="no sources selected"):
        advisor.evaluate_candidates(state, [COATED_CABLE], ["region:Nowhereland"])


# ---------------------------------------------------------------- the verdicts


def test_turbofuel_alternate_is_worth_zero_power(state):
    """Hard drive 9. Diluted Fuel is already unlocked and dominates every turbofuel
    route, so both options must read 0 MW. This non-obvious answer is the whole
    reason the tool solves instead of consulting a tier list -- if turbofuel starts
    looking good, the baseline has stopped including what the player owns."""
    ev = advisor.evaluate_candidates(state, [TURBO_HEAVY_FUEL, STEEL_ROD], NORTH)
    by_name = {v.schematic: v for v in ev.verdicts}
    assert by_name[TURBO_HEAVY_FUEL].deltas["net_mw"] == pytest.approx(0.0, abs=1e-3)
    assert by_name[STEEL_ROD].deltas["net_mw"] == pytest.approx(0.0, abs=1e-3)
    assert ev.verdicts[0].schematic == STEEL_ROD


def test_coated_cable_is_only_visible_on_its_own_output(state):
    """Hard drive 34. Coated Cable makes Cable, which no fixed battery of power and
    plastic objectives can see, so without the own-output objective it scores 0 on
    everything and looks worthless."""
    ev = advisor.evaluate_candidates(state, [COATED_CABLE, CHARCOAL], NORTH)
    cable = next(v for v in ev.verdicts if v.schematic == COATED_CABLE)
    assert cable.own_output_item == "Cable"
    assert cable.deltas["own_output_machines"] > 15
    assert cable.deltas["net_mw"] == pytest.approx(0.0, abs=1e-3)
    assert ev.verdicts[0].schematic == COATED_CABLE


def test_charcoal_is_reported_as_granting_nothing_new(state):
    """The unlock gate is mAvailableRecipes, not the purchased-schematic set:
    Charcoal already arrived via the Compacted Coal tree. Scoring it as a fresh
    unlock would recommend spending a hard drive on a recipe the player has."""
    ev = advisor.evaluate_candidates(state, [CHARCOAL], NORTH)
    v = ev.verdicts[0]
    assert v.new_recipes == []
    assert "unlocks no new recipe for this save" in v.notes


def test_unmet_dependency_is_gated_on_schematic_dependencies_not_tier(game, state):
    """24 of 109 alternates are blocked behind milestone schematics. mTechTier cannot
    be the gate -- tier 0 appears on both sides of the line -- so a tier-based check
    would offer the player a recipe they cannot research."""
    ev = advisor.evaluate_candidates(state, [TURBO_PRESSURE_MOTOR], NORTH)
    assert ev.verdicts[0].dependency_missing
    assert not game.schematics[TURBO_PRESSURE_MOTOR].tier
    reachable_at_the_same_tier = [
        s
        for s in game.schematics.values()
        if s.type == "EST_Alternate" and not s.tier and state.dependencies_met(s.cls)[0]
    ]
    assert reachable_at_the_same_tier


def test_counterfactual_adds_every_recipe_of_a_three_recipe_schematic(
    game, projection, monkeypatch
):
    """Two alternate schematics bundle three recipes. Adding only the headline one
    prices a plan that cannot package its own turbofuel, so the candidate is judged
    on a recipe set the player would never actually receive."""
    proj = copy.deepcopy(projection)
    prog = proj["progression"]
    prog["available_recipes"] = [
        r for r in prog["available_recipes"] if r not in PACKAGED_TURBOFUEL
    ]
    st = WorldState(projection=proj, game=game)
    assert len(st._schematic_recipes(game.schematics[TURBO_BLEND_FUEL])) == 3

    seen: list[set[str]] = []

    def _capture(sc: Scenario) -> Solution:
        seen.append(set(sc.recipes))
        return Solution(
            status="optimal",
            objective_value=0.0,
            net_mw=0.0,
            processes=[],
            raw_used={},
            exports={},
            sunk={},
            machines_total=0.0,
        )

    monkeypatch.setattr(advisor, "solve", _capture)
    advisor.evaluate_candidates(st, [TURBO_BLEND_FUEL], NORTH)
    wanted = {"Recipe_Alternate_TurboBlendFuel_C", *PACKAGED_TURBOFUEL}
    assert any(wanted <= s for s in seen), "counterfactual dropped part of the schematic"


def test_a_candidates_new_building_is_offered_to_the_solver(game, state, monkeypatch):
    """A recipe is not worthless for needing a machine that is unlocked but unbuilt.
    Withholding the building makes the candidate infeasible and reports a None delta
    where the honest answer is a delta plus a "build one first" note."""
    blender_recipe = next(r for r in game.recipes.values() if r.machine == "Build_Blender_C")
    assert advisor._needed_buildings(state, [blender_recipe.cls]) == {"Build_Blender_C"}
    sc = advisor._request(state, NORTH, advisor.standard_objectives()[0]).scenario
    captured: list[Scenario] = []
    monkeypatch.setattr(advisor, "solve", lambda scenario: captured.append(scenario))
    advisor._solve_with(sc, state, [blender_recipe.cls])
    assert "Build_Blender_C" in captured[0].buildings_available
    assert blender_recipe.cls in captured[0].recipes


def test_own_output_objective_falls_back_to_nothing_without_a_product(game):
    """A schematic granting no recipe has no product to measure, and asking for
    "300/min of nothing" would raise rather than report the honest verdict."""
    assert advisor._own_output_objective(game, []) is None


def test_own_output_objective_targets_the_highest_throughput_product(game):
    """The primary product is what the recipe is FOR. Picking a byproduct instead --
    Heavy Oil Residue for the base Plastic recipe -- measures the wrong thing."""
    plastic: Recipe = game.recipes["Recipe_Plastic_C"]
    result = advisor._own_output_objective(game, [plastic])
    assert result is not None
    assert result[1] == "Desc_Plastic_C"

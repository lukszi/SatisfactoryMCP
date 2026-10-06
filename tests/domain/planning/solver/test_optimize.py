"""Optimizer correctness: the guards, and the byproduct rule that motivates it all."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.optimize import (
    MW,
    Process,
    Scenario,
    build_processes,
    free_lunch_audit,
    solve,
)

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
WATER = "Desc_Water_C"
PLASTIC = "Desc_Plastic_C"


def _plastic(game, recipes, exports, sinks=True, crude=300.0):
    return solve(
        Scenario(
            game=game,
            recipes=recipes,
            objective="max_item",
            target_item=PLASTIC,
            raw_caps={CRUDE: crude, WATER: 1e6},
            exports=exports,
            allow_sinks=sinks,
            grid_import_mw=1e5,
        )
    )


def test_free_lunch_audit_returns_exactly_zero(game, state):
    """Strip every matter source; MW must be exactly 0. A non-zero result means some
    cycle creates matter from nothing."""
    ok, value = free_lunch_audit(
        Scenario(
            game=game,
            recipes=[r.cls for r in state.unlocked_recipes("part")],
            buildings_available=state.unlocked_building_ids,
        )
    )
    assert ok, f"free lunch of {value} MW"


def test_duplicate_process_id_is_fatal(game, monkeypatch):
    """A duplicate pid merges two columns and yields a plausible, mass-balanced,
    WRONG answer -- exactly the bug that made one miner produce both coal and
    sulfur. It must raise, not warn."""
    import satisfactory_mcp.domain.planning.optimize as opt

    dupe = Process(pid="same", kind="recipe", label="a", rates={}, mw=0.0)
    monkeypatch.setattr(opt, "recipe_processes", lambda sc: [dupe, dupe])
    monkeypatch.setattr(opt, "extractor_processes", lambda sc: [])
    monkeypatch.setattr(opt, "generator_processes", lambda sc: [])
    with pytest.raises(AssertionError, match="duplicate process id"):
        build_processes(Scenario(game=game, recipes=[]))


def test_byproduct_with_no_consumer_makes_the_plan_infeasible(game):
    """Recipe_Plastic_C emits Heavy Oil Residue. HOR is a fluid, so it cannot be
    sunk; with no consumer the only feasible answer is zero plastic."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC,))
    assert sol.objective_value == pytest.approx(0.0, abs=1e-6)


def test_pretending_a_byproduct_is_exportable_is_the_naive_bug(game):
    """This is what a net>=0 formulation computes: 200 plastic in 10 machines, with
    nothing consuming 100 m3/min of residue. In game the pipe fills and it stalls."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC, "Desc_HeavyOilResidue_C"))
    assert sol.objective_value == pytest.approx(200.0)
    assert sol.machines_total == pytest.approx(10.0)


def _equivalents(sol):
    """Throughput in machine-equivalents -- the exact ratio, before it is rendered
    as whole machines at a derived clock."""
    return sum(p["machine_equivalents"] for p in sol.processes)


def test_correct_formulation_needs_more_machines(game):
    """Consuming the residue for real costs 17% more buildings than the naive answer,
    and routing it to a solid sink costs 25% more."""
    consumed = _plastic(
        game, ["Recipe_Plastic_C", "Recipe_ResidualFuel_C"], (PLASTIC, "Desc_LiquidFuel_C")
    )
    assert consumed.objective_value == pytest.approx(200.0)
    assert _equivalents(consumed) == pytest.approx(11.667, abs=1e-2)
    assert consumed.machines_total == 12  # whole buildings

    sunk = _plastic(game, ["Recipe_Plastic_C", "Recipe_PetroleumCoke_C"], (PLASTIC,))
    assert sunk.objective_value == pytest.approx(200.0)
    assert _equivalents(sunk) == pytest.approx(12.5, abs=1e-2)
    assert sunk.sunk.get("Desc_PetroleumCoke_C", 0) > 0
    assert any("AWESOME Sink" in w for w in sunk.warnings)


def test_fractional_result_is_rendered_as_whole_machines_at_a_clock(game):
    """A 11.667 machine-equivalent result is 12 machines at 97.2%, not a rounding
    error. That is how the game is actually played, and it keeps ratios exact."""
    sol = _plastic(
        game, ["Recipe_Plastic_C", "Recipe_ResidualFuel_C"], (PLASTIC, "Desc_LiquidFuel_C")
    )
    for p in sol.processes:
        assert isinstance(p["machines"], int)
        assert p["machines"] >= 1
        # Whole machines at the derived clock reproduce the exact throughput.
        # Tolerance is display rounding only: both figures are rounded independently
        # for output, so exact equality would test the formatting, not the maths.
        assert p["machines"] * p["clock"] == pytest.approx(p["machine_equivalents"], abs=1e-3)
        # Never over 100%: ceil() means the derived clock only ever trims down.
        assert 0 < p["clock"] <= 1.0 + 1e-9
        # And it is the tightest whole-machine count for that throughput.
        assert p["machines"] - 1 < p["machine_equivalents"] + 1e-6


def test_derived_clock_power_never_exceeds_what_the_solve_promised(game):
    """Linear power is what the LP optimises; below 100% clock the true c**1.32 draw
    is lower, so the reported figure is conservative rather than optimistic."""
    sol = _plastic(
        game, ["Recipe_Plastic_C", "Recipe_ResidualFuel_C"], (PLASTIC, "Desc_LiquidFuel_C")
    )
    for p in sol.processes:
        if p["mw"] < 0:  # a consumer
            assert p["mw"] >= p["mw_linear"] - 1e-6, p["label"]


def _max_mw(game, clocks=(1.0,), machine_cost_mw=5.0):
    return solve(
        Scenario(
            game=game,
            recipes=["Recipe_Alternate_HeavyOilResidue_C", "Recipe_ResidualFuel_C"],
            objective="max_mw",
            exports=(MW,),
            raw_caps={CRUDE: 600.0, WATER: 1e5},
            clocks=tuple(clocks),
            machine_cost_mw=machine_cost_mw,
        )
    )


def test_default_clocks_offer_no_spreading_mode(game):
    """Ratio underclocking is automatic; sub-100% MODES are opt-in, because they
    answer a different question (spend buildings to save power)."""
    sol = _max_mw(game)
    assert sol.ok
    assert not any("clock MODE" in w for w in sol.warnings)


def test_spreading_is_allowed_when_asked_for_and_is_announced(game):
    """The user asked for underclocking to be allowed but penalised, not banned."""
    plain = _max_mw(game)
    spread = _max_mw(game, clocks=(0.5, 1.0))
    assert spread.ok and plain.ok
    # More power, more buildings -- and the tradeoff is stated, not hidden.
    assert spread.net_mw >= plain.net_mw - 1e-6
    if spread.net_mw > plain.net_mw + 1e-6:
        assert spread.machines_total > plain.machines_total
        assert any("clock MODE" in w for w in spread.warnings)


def test_pricing_machines_suppresses_marginal_spreading(game):
    """With machines priced high enough, a thin power gain is no longer worth the
    buildings -- which is the whole point of pricing rather than banning."""
    cheap = _max_mw(game, clocks=(0.5, 1.0), machine_cost_mw=0.0)
    dear = _max_mw(game, clocks=(0.5, 1.0), machine_cost_mw=10_000.0)
    assert cheap.ok and dear.ok
    assert dear.machines_total <= cheap.machines_total


def test_logistics_reports_lines_not_a_cap(game):
    """Throughput is reported, never constrained: parallel lines are legal, so the
    useful output is how many belts or pipes the plan implies."""
    sol = _plastic(
        game, ["Recipe_Plastic_C", "Recipe_ResidualFuel_C"], (PLASTIC, "Desc_LiquidFuel_C")
    )
    assert sol.logistics
    by_item = {entry["item"]: entry for entry in sol.logistics}
    crude = by_item["Desc_LiquidOil_C"]
    assert crude["carrier"] == "pipe"  # fluid
    assert crude["lines"] >= 1
    plastic = by_item[PLASTIC]
    assert plastic["carrier"] == "belt"  # solid
    assert plastic["rate"] == pytest.approx(200.0)


def test_machine_counts_are_minimal(game):
    """Without the phase-2 lexicographic pass, any larger machine count is equally
    optimal and the solver returned counts of 1e12."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC, "Desc_HeavyOilResidue_C"))
    assert sol.machines_total < 100


def test_power_plant_cannot_import_grid_power(game):
    """A plant exporting MW must be self-contained, or max_mw is unbounded."""
    sol = solve(
        Scenario(
            game=game,
            recipes=["Recipe_Plastic_C"],
            objective="max_mw",
            exports=(MW,),
            raw_caps={CRUDE: 300},
            grid_import_mw=1e9,  # requested, but must be ignored
        )
    )
    assert sol.grid_import_mw == pytest.approx(0.0)


def test_recycled_plastic_rubber_cycle_is_handled(game, state):
    """These two recipes form a genuine 2-cycle and the player has both, which is why
    recursive chain expansion is unsound and an LP is required."""
    recycled_p = game.recipes["Recipe_Alternate_Plastic_1_C"]
    recycled_r = game.recipes["Recipe_Alternate_RecycledRubber_C"]
    assert state.has_recipe(recycled_p.cls) and state.has_recipe(recycled_r.cls)
    assert recycled_p.rate_of("Desc_Rubber_C") < 0 < recycled_p.rate_of(PLASTIC)
    assert recycled_r.rate_of(PLASTIC) < 0 < recycled_r.rate_of("Desc_Rubber_C")
    # The LP terminates and finds a finite optimum despite the cycle.
    sol = _plastic(
        game,
        [
            recycled_p.cls,
            recycled_r.cls,
            "Recipe_Alternate_HeavyOilResidue_C",
            "Recipe_Alternate_DilutedFuel_C",
        ],
        (PLASTIC,),
    )
    assert sol.ok
    assert 0 < sol.objective_value < 1e6

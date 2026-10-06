"""bom: the flattened bill, and the reasons it is an LP solve rather than a tree walk.

The hand-check is the point of this file. A bill is arithmetic a player can do on
paper, so if the tool and the paper disagree the tool is wrong -- and the only way
to hold it to that is to pin the chain with only_recipes and assert the exact
number.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.bom import build_bom, live_processes
from satisfactory_mcp.domain.planning.optimize import Solution
from satisfactory_mcp.presenters.text.bom import render_bom

pytestmark = pytest.mark.integration

IRON_ORE = "Desc_OreIron_C"
CRUDE = "Desc_LiquidOil_C"
WATER = "Desc_Water_C"

#: The textbook Reinforced Iron Plate chain, with every alternate excluded so the
#: answer is arithmetic and not an optimum.
BASE_CHAIN = [
    "Recipe_IronPlateReinforced_C",
    "Recipe_IronPlate_C",
    "Recipe_Screw_C",
    "Recipe_IronRod_C",
    "Recipe_IngotIron_C",
]


@pytest.fixture(scope="module")
def rip(game, state):
    return build_bom(game, state, "Reinforced Iron Plate", qty=10, only_recipes=BASE_CHAIN)


def _row(bom, name):
    return next(r for r in bom.rows if r.name == name)


# ----------------------------------------------------------- the hand-check


def test_the_base_iron_plate_chain_matches_the_hand_calculation(rip):
    """10 Reinforced Iron Plate/min needs 6 Iron Plate + 12 Screws each, i.e.
    60 Plate and 120 Screws; 60 Plate is 90 Iron Ingot, 120 Screws is 30 Iron Rod
    is 30 Iron Ingot, and 120 Iron Ingot is 120 Iron Ore. Twelve ore per plate,
    done by hand and then by the solver."""
    assert rip.ok
    assert rip.raw == {IRON_ORE: pytest.approx(120.0)}

    assert _row(rip, "Iron Plate").made == pytest.approx(60.0)
    assert _row(rip, "Screws").made == pytest.approx(120.0)
    assert _row(rip, "Iron Rod").made == pytest.approx(30.0)
    assert _row(rip, "Iron Ingot").made == pytest.approx(120.0)
    assert _row(rip, "Reinforced Iron Plate").made == pytest.approx(10.0)


def test_the_machine_counts_match_the_hand_calculation(rip):
    """Whole buildings at a derived clock: 120 ingot/min over a 30/min Smelter is
    4, 120 Screws over a 40/min Constructor is 3, 10 plates over a 5/min Assembler
    is 2. 78 MW is 4x4 + 8x4 + 2x15."""
    assert _row(rip, "Iron Ingot").machines == 4
    assert _row(rip, "Iron Plate").machines == 3
    assert _row(rip, "Screws").machines == 3
    assert _row(rip, "Iron Rod").machines == 2
    assert _row(rip, "Reinforced Iron Plate").machines == 2
    assert rip.machines == 14
    assert abs(rip.mw) == pytest.approx(78.0, abs=0.01)


def test_the_bill_scales_linearly(game, state):
    """An LP solution is a ray, so twice the export is exactly twice the bill. A
    figure that drifted here would mean the target rate was leaking into a
    non-linear term."""
    doubled = build_bom(game, state, "Reinforced Iron Plate", qty=20, only_recipes=BASE_CHAIN)
    assert doubled.raw[IRON_ORE] == pytest.approx(240.0)


def test_every_row_names_the_recipe_that_made_it(rip):
    """Alternates move these numbers by 4x, so a bill that does not say which
    recipe it assumed is not checkable."""
    for row in rip.rows:
        assert row.recipes, row.name
    assert _row(rip, "Iron Ingot").recipes == ("Iron Ingot",)
    assert _row(rip, "Iron Ingot").recipe_ids == ("Recipe_IngotIron_C",)
    assert _row(rip, "Iron Ore").recipes == ("RAW",)


def test_alternates_change_the_answer_materially(game, state, rip):
    """Left to choose, this save's unlocked alternates route Reinforced Iron Plate
    through Stitched Iron Plate and the Pure ingot recipes and spend 26.92 Iron Ore
    where the base chain spends 120 -- paid for with Copper Ore and Water. This is
    why the recipe column is not decoration."""
    free = build_bom(game, state, "Reinforced Iron Plate", qty=10)
    assert free.ok
    assert free.raw[IRON_ORE] < rip.raw[IRON_ORE] / 4
    assert free.alternates, "the LP took alternates the pinned chain could not"
    assert set(free.raw) > {IRON_ORE}, "it buys the iron saving with other resources"


# --------------------------------------------------------------- the cycle


def test_a_two_cycle_is_solved_and_named_rather_than_expanded(game, state):
    """Recycled Plastic (30 Rubber + 30 Fuel -> 60 Plastic) and Recycled Rubber
    (30 Plastic + 30 Fuel -> 60 Rubber) are both unlocked on this save, so a
    recursive expansion has no correct depth limit. The LP has no depth: for 60
    Plastic/min it builds 75.56 Plastic of capacity and recirculates 15.56 back
    into the Rubber line. That gross figure is right and looks wrong, so it is
    labelled."""
    plastic = build_bom(game, state, "Plastic", qty=60)
    assert plastic.ok
    assert plastic.loops == [("Plastic", "Rubber")]

    row = _row(plastic, "Plastic")
    assert row.made == pytest.approx(75.56, abs=0.01)
    assert row.used == pytest.approx(15.56, abs=0.01)
    assert row.made - row.used == pytest.approx(60.0, abs=0.01)
    assert "production loop" in render_bom(plastic)


def test_the_cyclic_bill_balances_against_hand_arithmetic(game, state):
    """The loop is not an excuse for an unverifiable number. 60 Plastic/min via
    Recycled Plastic needs 37.78 Rubber and 37.78 Fuel; the Rubber comes back as
    31.11 from Recycled Rubber (eating 15.56 Plastic and 15.56 Fuel) plus 6.67
    from Residual Rubber, which needs 13.33 Polymer Resin. 53.33 Fuel is 26.67
    Heavy Oil Residue through Diluted Fuel, and 26.67 HOR is 20 Crude Oil through
    Alt Heavy Oil Residue -- which is where the 13.33 Polymer Resin comes from."""
    plastic = build_bom(game, state, "Plastic", qty=60)
    assert _row(plastic, "Rubber").made == pytest.approx(37.78, abs=0.01)
    assert _row(plastic, "Fuel").made == pytest.approx(53.33, abs=0.01)
    assert _row(plastic, "Heavy Oil Residue").made == pytest.approx(26.67, abs=0.01)
    assert _row(plastic, "Polymer Resin").made == pytest.approx(13.33, abs=0.01)
    assert plastic.raw[CRUDE] == pytest.approx(20.0, abs=0.01)
    assert plastic.raw[WATER] == pytest.approx(66.67, abs=0.01)


def test_a_byproduct_row_names_the_recipe_it_falls_out_of(game, state):
    """Polymer Resin is made by nothing: it is the other half of Alt Heavy Oil
    Residue. Charging machines to it would double-count the Refinery, and leaving
    the recipe blank made it read as arriving from nowhere."""
    plastic = build_bom(game, state, "Plastic", qty=60)
    resin = _row(plastic, "Polymer Resin")
    assert resin.recipes == ("Alternate: Heavy Oil Residue",)
    assert resin.machines == 0
    assert sum(r.machines for r in plastic.rows) == plastic.machines


# ------------------------------------------------------- water and degeneracy


def test_water_is_priced_last_or_the_crude_figure_is_nearly_3x_wrong(game, state):
    """A bare min_raw sums every resource with weight one and therefore trades
    crude against water. Measured on 60 Plastic/min: unweighted it returns
    56.25 Crude Oil and no water at all; with water priced last it returns
    20 Crude Oil and 66.67 Water. Water is effectively unlimited on this map, so
    the unweighted answer overstates the scarce input by 2.8x."""
    plastic = build_bom(game, state, "Plastic", qty=60)
    assert plastic.solves == 2, "the tie-break is two solves, not one"
    assert plastic.raw[CRUDE] == pytest.approx(20.0, abs=0.01)
    assert plastic.raw[CRUDE] < 56.25 / 2.5


def test_the_degeneracy_is_labelled_never_presented_as_the_number(rip):
    """§8.7 (docs/planning.md): a degenerate min_raw component must be labelled as one of
    several optima or given a documented tie-break. This does both, and says so."""
    out = render_bom(rip)
    assert "degenerate" in out
    assert "only_recipes" in out, "the escape hatch to an exact answer is named"


# ------------------------------------------------------------ solver noise


def test_a_process_carrying_no_flow_is_not_reported_as_a_building():
    """A degenerate vertex leaves columns at ~1e-6 machine-equivalents. ceil()
    turns one into a whole Smelter with a recipe name against it, so the bill
    claimed two routes to Iron Ingot when the flow ran entirely through one."""
    sol = Solution(
        status="optimal",
        objective_value=0.0,
        net_mw=0.0,
        processes=[
            {"pid": "r:real", "machine_equivalents": 1.25, "machines": 2},
            {"pid": "r:noise", "machine_equivalents": 1e-6, "machines": 1},
        ],
        raw_used={},
        exports={},
        sunk={},
        machines_total=3.0,
    )
    assert [p["pid"] for p in live_processes(sol)] == ["r:real"]


def test_a_tiny_bill_is_not_filtered_away():
    """The noise floor is relative, so a genuinely small plan survives it. An
    absolute floor would delete a bill for 0.1/min entirely."""
    sol = Solution(
        status="optimal",
        objective_value=0.0,
        net_mw=0.0,
        processes=[{"pid": "r:small", "machine_equivalents": 1e-5, "machines": 1}],
        raw_used={},
        exports={},
        sunk={},
        machines_total=1.0,
    )
    assert len(live_processes(sol)) == 1


# --------------------------------------------------------------- edge cases


def test_a_raw_resource_is_its_own_bill(game, state):
    """Solving would demand a recipe that MAKES Iron Ore and report "infeasible",
    which is a true statement about the wrong question."""
    ore = build_bom(game, state, "Iron Ore", qty=60)
    assert ore.status == "raw"
    assert ore.raw == {IRON_ORE: 60.0}
    assert "raw resource" in render_bom(ore)


def test_an_unknown_item_and_a_non_positive_rate_are_refused(game, state):
    with pytest.raises(ValueError, match="no item matching"):
        build_bom(game, state, "Unobtanium")
    with pytest.raises(ValueError, match="rate"):
        build_bom(game, state, "Plastic", qty=0)


def test_qty_is_a_rate_and_the_response_says_so(rip):
    assert "/min" in render_bom(rip).splitlines()[0]


def test_the_response_fits_the_context_budget(game, state):
    out = srv.bom(item="Plastic", qty=60)
    assert len(out) < 4000, f"bom returned {len(out)} chars"

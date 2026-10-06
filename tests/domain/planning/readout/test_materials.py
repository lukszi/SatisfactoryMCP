"""What a plan costs to BUILD.

The startup re-frame split running cost from construction cost, and only the first had ever
been measured. This is the second: the price tag you want before starting, as opposed to
`diff_vs_save`'s shopping list for what is left once you have.

The two are deliberately different computations and the tests below pin that they stay
different, because collapsing them would silently answer the wrong question -- a delta
cannot tell you what a plant costs, and a total cannot tell you what to go and make next.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.materials import FOUNDATION_ID, build_materials, cost_of
from satisfactory_mcp.domain.planning.prepare import prepare
from tests.support.reference_world import REFERENCE_MAX_MW_ARGS

pytestmark = pytest.mark.integration


@pytest.fixture
def plan(game, state):
    return prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))


# ------------------------------------------------------------ the arithmetic


def test_a_building_costs_what_the_dump_says(game):
    """Data, never a table in this file. A game update moves these and nothing here
    should need editing."""
    one = cost_of(game, "Build_GeneratorFuel_C", 1)
    assert one.parts["Desc_Motor_C"] == 15
    assert one.parts["Desc_Rubber_C"] == 50
    many = cost_of(game, "Build_GeneratorFuel_C", 488)
    assert many.parts["Desc_Rubber_C"] == 488 * 50
    assert many.items == 488 * 160


def test_an_unpriced_building_is_named_rather_than_costed_at_zero(game):
    """A bill silently missing a building reads as complete, which is the worst way to be
    wrong about a price."""
    missing = cost_of(game, "Build_NoSuchThing_C", 3)
    assert missing.unpriced
    assert not missing.parts
    bill = build_materials(game, [{"building_id": "Build_NoSuchThing_C", "machines": 3}])
    assert bill.unpriced == ["Build_NoSuchThing_C"]
    assert any("LOWER bound" in n for n in bill.notes)


def test_whole_machines_are_charged_not_machine_equivalents(game):
    """You place buildings, not fractions of them. Charging the LP's fractional column
    would understate every line."""
    bill = build_materials(
        game, [{"building_id": "Build_GeneratorFuel_C", "machines": 3, "clock": 0.5}]
    )
    assert bill.machines == 3
    assert next(x for x in bill.lines if x.item == "Desc_Rubber_C").needed == 150


def test_the_bill_totals_across_buildings_that_share_a_part(game):
    """Motors come from generators, blenders and refineries at once, and the interesting
    number is the sum with the culprits named."""
    bill = build_materials(
        game,
        [
            {"building_id": "Build_GeneratorFuel_C", "machines": 10},
            {"building_id": "Build_Blender_C", "machines": 10},
        ],
    )
    motor = next(x for x in bill.lines if x.item == "Desc_Motor_C")
    assert motor.needed == 10 * 15 + 10 * 20
    assert set(motor.wanted_by) == {"Fuel-Powered Generator", "Blender"}


def test_lines_are_ordered_by_how_much_is_needed(game, plan):
    bill = build_materials(game, plan.solution.processes)
    amounts = [x.needed for x in bill.lines]
    assert amounts == sorted(amounts, reverse=True)


# ------------------------------------------------------------ foundations


def test_foundations_are_costed_and_are_not_a_machine(game, plan):
    """The number nobody had. Foundations are not machines, so no build table counts
    them, and at 5 Concrete each they outweigh most of the machine bill."""
    without = build_materials(game, plan.solution.processes)
    with_deck = build_materials(game, plan.solution.processes, foundations=6529)
    assert with_deck.foundations == 6529
    concrete = next(x for x in with_deck.lines if x.item == "Desc_Cement_C")
    assert concrete.needed == 6529 * 5
    assert all(x.item != "Desc_Cement_C" for x in without.lines) or concrete.needed > next(
        (x.needed for x in without.lines if x.item == "Desc_Cement_C"), 0
    )
    assert with_deck.machines == without.machines, "a foundation is not a machine"


def test_the_deck_is_charged_per_floor_not_per_site(game):
    """`Layout.foundations` is the PEAK floor, which sizes the ground you need because
    floors stack. Concrete is poured for every floor, so charging the peak would
    understate the deck by however many storeys the stack has."""
    out = srv.plan_layout(show="materials", limit=30, **REFERENCE_MAX_MW_ARGS)
    peak = int(
        next(x for x in out.split() if x.startswith("peak_floor_foundations=")).split("=")[1]
    )
    charged = int(next(x for x in out.split() if x.startswith("foundations=")).split("=")[1])
    assert charged > peak


# ------------------------------------------------------------ stock


def test_stock_decides_what_is_short(game):
    bill = build_materials(
        game,
        [{"building_id": "Build_GeneratorFuel_C", "machines": 10}],
        stock={"Desc_Rubber_C": 100.0},
    )
    rubber = next(x for x in bill.lines if x.item == "Desc_Rubber_C")
    assert rubber.needed == 500 and rubber.held == 100 and rubber.short == 400
    assert not rubber.covered
    assert not bill.affordable
    assert bill.shortfall[0].short >= bill.shortfall[-1].short


def test_holding_enough_makes_a_line_covered(game):
    bill = build_materials(
        game,
        [{"building_id": "Build_ConveyorPoleStackable_C", "machines": 1}],
        stock={k: 1e9 for k in ("Desc_IronPlate_C", "Desc_IronRod_C", "Desc_Cement_C")},
    )
    assert bill.affordable or bill.unpriced


# ------------------------------------------------------------ the tool


def test_the_tool_prints_a_bill(game):
    out = srv.plan_layout(show="materials", limit=20, **REFERENCE_MAX_MW_ARGS)
    assert not out.startswith("! ")
    assert "item\tneed\thave\tshort\tfor" in out
    assert "Concrete" in out
    assert "distinct_parts=" in out


def test_it_says_what_you_are_short_of(game):
    out = srv.plan_layout(show="materials", limit=20, **REFERENCE_MAX_MW_ARGS)
    assert "short of" in out or "afford every part" in out


def test_it_distinguishes_itself_from_the_diff_cost_table(game):
    """They answer different questions and a reader who conflates them will think the
    plant is cheaper than it is -- the diff prices only what is LEFT to place."""
    out = srv.plan_layout(show="materials", limit=20, **REFERENCE_MAX_MW_ARGS)
    assert "NOT the same question as diff_vs_save" in out


def test_belts_and_pipes_are_refused_rather_than_estimated(game):
    """Their cost is per metre and there is no route. A length guessed here would be the
    largest invented number in the project."""
    out = srv.plan_layout(show="materials", limit=20, **REFERENCE_MAX_MW_ARGS)
    assert "belts and pipes are NOT costed" in out
    assert "Conveyor Belt" not in out.split("item\tneed")[1]


def test_it_points_at_bom_rather_than_flattening_to_ore(game):
    """Recycled Plastic and Recycled Rubber are a real 2-cycle, so a tree walk here has no
    correct depth limit. The LP in `bom` is the only honest expansion."""
    out = srv.plan_layout(show="materials", limit=20, **REFERENCE_MAX_MW_ARGS)
    assert "not ore" in out
    assert "bom" in out


def test_the_other_views_still_work(game):
    """materials is an addition; the modes callers already use must be untouched."""
    for mode in ("floors", "blocks", "buses", "trunks"):
        out = srv.plan_layout(show=mode, limit=6, **REFERENCE_MAX_MW_ARGS)
        assert not out.startswith("! "), mode
        assert FOUNDATION_ID not in out

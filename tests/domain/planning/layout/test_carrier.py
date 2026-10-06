"""Belts, pipes and line counts — one implementation, previously three.

`layout` had `_carrier` and `_lines_for`, `optimize._logistics` inlined both, and
`Item.unit` had already centralised the unit string all three re-derived from `is_fluid`.

The tell was the epsilon: both copies computed `ceil(rate / capacity - 1e-9)`, a fudge
nobody arrives at independently. It is tested here directly, because it is the one part of
this that is not obvious and the one part a rewrite would drop.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.solver.carrier import Carrier, carrier_for
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

BELT, PIPE = 780.0, 600.0


# ------------------------------------------------------------- which carrier


def test_a_fluid_goes_by_pipe_and_a_solid_by_belt(game):
    crude = carrier_for(game, "Desc_LiquidOil_C", BELT, PIPE)
    plate = carrier_for(game, "Desc_IronPlate_C", BELT, PIPE)
    assert (crude.kind, crude.capacity, crude.fluid) == ("pipe", PIPE, True)
    assert (plate.kind, plate.capacity, plate.fluid) == ("belt", BELT, False)


def test_the_unit_comes_from_the_item_not_from_a_literal(game):
    """`Item.unit` is the authority and existed the whole time; all three copies
    re-derived the same string from is_fluid instead."""
    for item in ("Desc_LiquidOil_C", "Desc_NitrogenGas_C", "Desc_IronPlate_C"):
        assert carrier_for(game, item, BELT, PIPE).unit == game.items[item].unit


def test_an_unknown_item_is_belted_rather_than_piped(game):
    """It still has to be carried somehow, and a belt is the answer that never silently
    promotes something to a pipe."""
    unknown = carrier_for(game, "Desc_NoSuchThing_C", BELT, PIPE)
    assert unknown.kind == "belt"
    assert unknown.unit == "/min"


# ------------------------------------------------------------- how many lines


def test_an_exact_multiple_does_not_round_up():
    """The reason the epsilon exists. 1,560 over a 780/min belt is exactly two lines, and
    binary rounding turns it into three without the fudge."""
    belt = Carrier("belt", "/min", 780.0)
    assert belt.lines_for(1560.0) == 2
    assert belt.lines_for(780.0) == 1
    assert belt.lines_for(2340.0) == 3


def test_a_hair_over_capacity_does_round_up():
    """The epsilon must not be so large that it swallows a real overflow."""
    belt = Carrier("belt", "/min", 780.0)
    assert belt.lines_for(780.1) == 2
    assert belt.lines_for(1560.1) == 3


def test_any_positive_rate_needs_at_least_one_line():
    belt = Carrier("belt", "/min", 780.0)
    assert belt.lines_for(0.0001) == 1
    assert belt.lines_for(0.0) == 1


def test_a_zero_capacity_carrier_answers_one_rather_than_none():
    """Where the two old copies had already drifted: layout said 1, optimize said None.
    Unreachable -- capacity comes from a tier lookup with a non-zero fallback -- but a
    divergence inside duplicated code is a bug waiting for the day it becomes reachable.
    Resolved toward 1, because the count feeds block splitting and None would need a
    guard at every use."""
    assert Carrier("belt", "/min", 0.0).lines_for(100.0) == 1


# ------------------------------------------------------------- both callers agree


def test_the_optimizer_and_the_layout_report_the_same_lines(game, state):
    """They are the two places that had their own copy. Same plan, same items, so any
    disagreement is the duplication having grown back."""
    from satisfactory_mcp.domain.planning.layout.schematic import build_layout
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )
    sol = prepared.solution
    lay = build_layout(game, sol)
    by_item = {b.item: b for b in lay.buses}
    checked = 0
    for entry in sol.logistics:
        bus = by_item.get(entry["item"])
        if bus is None:
            continue
        assert entry["carrier"] == bus.carrier, entry["item"]
        assert entry["unit"] == bus.unit, entry["item"]
        checked += 1
    assert checked > 5

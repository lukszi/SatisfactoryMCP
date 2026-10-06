"""Totals over a subset of a solved plan.

The primitive behind the shard bill, and the inner loop a commissioning planner needs.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.prepare import prepare
from satisfactory_mcp.domain.planning.slice import slice_of
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

SPIRE = dict(sources=list(REFERENCE_FIELD), objective="max_mw", exports=["MW"])


@pytest.fixture(scope="module")
def plan(game, state):
    return prepare(game, state, dict(SPIRE)), game


def test_the_whole_plan_reproduces_the_solvers_own_totals(plan):
    """The identity that makes every other figure trustworthy:
    solution.net_mw == sum(mw_linear) - sink_mw, exactly."""
    prepared, game = plan
    whole = slice_of(prepared, game)
    assert whole.machines == prepared.solution.machines_total
    assert whole.net_mw_linear - whole.sink_mw == pytest.approx(prepared.solution.net_mw, abs=0.01)


def test_exact_power_is_never_worse_than_the_solver_promised(plan):
    """mw_linear is proportional to machine-equivalents; mw is the figure after whole
    machines are placed at a derived clock. clock**exponent is convex, so N machines
    below 100% draw LESS than the linear estimate -- 9.4 MW to the good on this plan.
    Headroom checks use the exact number for exactly this reason."""
    prepared, game = plan
    whole = slice_of(prepared, game)
    assert whole.net_mw >= prepared.solution.net_mw - 0.01


def test_a_partial_slice_never_claims_the_sink(plan):
    """An AWESOME Sink is charged per belt line of sunk material and no column owns it,
    so a subset cannot attribute it. Prorating would invent a number."""
    prepared, game = plan
    part = slice_of(prepared, game, keep=lambda p: p["kind"] == "generator")
    assert part.sink_mw == 0.0
    assert slice_of(prepared, game).sink_mw > 0.0


def test_slices_partition_the_plan(plan):
    """Machines and linear power must add back up, or a commissioning schedule built
    from slices would not sum to the plan it came from."""
    prepared, game = plan
    kinds = {p["kind"] for p in prepared.solution.processes}
    parts = [slice_of(prepared, game, keep=lambda p, k=k: p["kind"] == k) for k in kinds]
    whole = slice_of(prepared, game)
    assert sum(x.machines for x in parts) == whole.machines
    assert sum(x.net_mw_linear for x in parts) == pytest.approx(whole.net_mw_linear, abs=0.01)


def test_generators_generate_and_the_rest_draw(plan):
    prepared, game = plan
    gens = slice_of(prepared, game, keep=lambda p: p["kind"] == "generator")
    assert gens.generation_mw > 0 and gens.draw_mw == 0
    rest = slice_of(prepared, game, keep=lambda p: p["kind"] != "generator")
    assert rest.draw_mw > 0 and rest.generation_mw == 0


# ------------------------------------------------------------- shard bill


def test_a_shard_raises_the_maximum_clock_not_the_clock(game, state):
    """The arithmetic that is easy to get wrong by hand, and was: a shard adds 0.5 to
    MAX clock, so 150% costs one and 250% costs three. Assuming three apiece overstates
    a mixed plan -- 192 instead of 109 on the reported one."""
    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW", "Plastic", "Rubber"],
            export_minimums={"Plastic": 2000, "Rubber": 300},
            extractor_clocks=[1, 1.5, 2, 2.5],
            water_extractors=64,
        ),
    )
    bill = slice_of(prepared, game)
    by_clock = {round(r.clock, 3): r.each for r in bill.shard_rows}
    assert by_clock[2.5] == 3
    assert any(each == 1 for clock, each in by_clock.items() if clock < 2.0)
    assert bill.shards == 109


def test_a_machine_at_100_percent_needs_no_shard(plan):
    prepared, game = plan
    bill = slice_of(prepared, game)
    assert all(r.clock > 1.0 for r in bill.shard_rows)


# ------------------------------------------------------------ sloop slots


def test_only_buildings_that_can_be_boosted_count_as_capacity(plan):
    """Generators and extractors carry sloop slots with can_boost False, so boost_for
    returns 1.0. Counting them would advertise a doubling the solver cannot deliver --
    the first version reported a Fuel Generator block at "1x output", which is nonsense
    dressed as a recommendation."""
    prepared, game = plan
    bill = slice_of(prepared, game)
    for row in bill.sloop_rows:
        assert row.boost > 1.0, row.label
        building_ids = {p["building_id"] for p in bill.processes if p["label"] == row.label}
        assert all(game.buildings[b].can_boost for b in building_ids if b in game.buildings)
    assert bill.unboostable_slots > 0, "this plan has generators, which have slots"


def test_sloops_are_reported_never_spent(plan):
    """Process.sloops stays 0: the plan does not place them. Reporting capacity is not
    the same as planning to use it."""
    prepared, _ = plan
    assert all(p["sloops"] == 0 for p in prepared.solution.processes)

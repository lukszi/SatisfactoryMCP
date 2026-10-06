"""Somersloops as a plan BUDGET rather than a reported curiosity.

The solver has carried `sloop_budget`, `Process.sloops` and `boost_for` since the
formulation was written, and nothing ever set them, so every plan silently ran at zero
sloops while the readout printed how many slots were going spare. The gap here is not
arithmetic: it is that the scarcest resource in the game was unreachable from the tool.

The part worth pinning is that they are spread, not stacked. Output is LINEAR in sloops
and power is QUADRATIC in the resulting boost, so a binding budget always prefers one
sloop in many machines over a full machine.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.readout.slice import slice_of
from satisfactory_mcp.domain.planning.solver.optimize import build_processes, solve
from satisfactory_mcp.domain.planning.solver.prepare import prepare
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario

pytestmark = pytest.mark.integration

SPIRE = dict(
    objective="max_mw",
    sources=list(REFERENCE_FIELD),
    exports=["MW"],
    extractor_clocks=[1.0, 1.5, 2.0, 2.5],
)


def _plan(game, state, **kw):
    return prepare(game, state, {**SPIRE, **kw})


# ------------------------------------------------------------ the game rule


def test_a_sloop_buys_output_linearly_and_costs_power_quadratically(game):
    """The whole reason spreading beats stacking, read from the building rather than
    assumed. 2x output for 4x power is the documented cap."""
    b = game.buildings["Build_Blender_C"]
    assert b.can_boost and b.sloop_slots == 4
    boosts = [b.boost_for(n) for n in range(b.sloop_slots + 1)]
    assert boosts == [1.0, 1.25, 1.5, 1.75, 2.0]
    # Power is boost ** boost_power_exponent, so full sloops cost exactly 4x.
    assert b.power_at(1.0, b.sloop_slots) == pytest.approx(4.0 * b.power_at(1.0, 0))
    # Marginal output per sloop is flat; marginal power is not.
    gains = [boosts[n + 1] - boosts[n] for n in range(4)]
    assert len({round(g, 9) for g in gains}) == 1
    costs = [b.power_at(1.0, n + 1) - b.power_at(1.0, n) for n in range(4)]
    assert costs == sorted(costs) and costs[0] < costs[-1]


# ------------------------------------------------------------ the budget


def test_zero_is_the_default_and_spends_nothing(game, state):
    """Somersloops are finite on the whole map. A plan that assumed them would be
    unbuildable in a way no other default is."""
    prepared = _plan(game, state)
    assert prepared.ok
    assert prepared.request.scenario.sloop_budget == 0
    assert slice_of(prepared, game).sloops_used == 0


def test_a_budget_is_spent_and_never_exceeded_by_the_solver(game, state):
    for budget in (4, 16, 64):
        prepared = _plan(game, state, sloops=budget)
        assert prepared.ok
        used = sum(p["sloops"] * p["machine_equivalents"] for p in prepared.solution.processes)
        assert used <= budget + 1e-6, budget


def test_a_budget_buys_power(game, state):
    """If it did not, there would be nothing to decide."""
    base = _plan(game, state).solution.net_mw
    with_sloops = _plan(game, state, sloops=64).solution.net_mw
    assert with_sloops > base


def test_sloops_are_spread_across_machines_not_stacked_into_them(game, state):
    """The behaviour that all-or-nothing options got wrong. Under a binding budget the
    solver puts one sloop in many machines, because output is linear in sloops while
    power is quadratic in the boost they produce."""
    prepared = _plan(game, state, sloops=16)
    rows = [p for p in prepared.solution.processes if p["sloops"]]
    assert rows
    assert min(p["sloops"] for p in rows) == 1
    # Many machines at one sloop, not few at full.
    assert sum(p["machines"] for p in rows) >= 16


def test_spreading_beats_filling_at_the_same_budget(game, state):
    """Measured, not argued. Restricting the solver to 0-or-full -- what it offered
    before -- gives a strictly worse plan for the same 16 sloops."""
    prepared = _plan(game, state, sloops=16)
    full_only = replace(
        prepared.request.scenario,
        excluded_pids=frozenset(
            p.pid
            for p in build_processes(prepared.request.scenario)
            if p.sloops and p.sloops < (game.buildings[p.building].sloop_slots if p.building else 0)
        ),
    )
    restricted = solve(full_only)
    assert restricted.ok
    assert prepared.solution.net_mw > restricted.net_mw


# ------------------------------------------------------------ the bill


def test_the_slice_separates_slots_spent_from_slots_going_spare(game, state):
    """One is a bill and the other is a suggestion. Conflating them would report 662
    somersloops needed for a plan that spends none."""
    idle = slice_of(_plan(game, state), game)
    assert idle.sloop_slots > 0 and idle.sloops_used == 0
    assert not idle.sloop_used_rows

    spending = slice_of(_plan(game, state, sloops=16), game)
    assert spending.sloops_used == 16
    assert spending.sloop_used_rows


def test_a_used_row_quotes_the_boost_it_actually_runs_at(game, state):
    """Not the building's maximum. A Refinery with 1 of 2 slots makes 1.5x, and printing
    the 2x it could reach would overstate the plan's own output."""
    bill = slice_of(_plan(game, state, sloops=16), game)
    for row in bill.sloop_used_rows:
        assert row.boost == pytest.approx(
            game.buildings[
                next(
                    p["building_id"]
                    for p in bill.processes
                    if p["label"] == row.label and p["sloops"] == row.slots_each
                )
            ].boost_for(row.slots_each)
        )
        assert row.boost < 2.0 or row.slots_each == row.total // row.machines


def test_generators_are_still_never_counted_as_capacity(game, state):
    """can_boost is False on them, so boost_for returns 1.0 whatever is slotted. They
    stay in unboostable_slots however the budget is set."""
    for budget in (0, 64):
        bill = slice_of(_plan(game, state, sloops=budget), game)
        assert bill.unboostable_slots > 0
        assert all(r.boost > 1.0 for r in bill.sloop_rows + bill.sloop_used_rows)


# ------------------------------------------------------------ what is held


def test_held_sloops_come_from_everywhere_they_can_be_spent(game, state):
    """Same pooling as shards: carried, storage containers and the Dimensional Depot.

    The place names are the buckets `stock` spends, and 'storage' is one of them while
    'crate' is not: the crates on the ground are recoverable but excluded, so a place
    named for them here would claim spendable sloops in a bucket nothing spends.
    """
    budget = state.sloop_budget()
    assert budget["free"] == sum(budget["by_place"].values())
    assert set(budget["by_place"]) <= {"carried", "storage", "depot"}


def test_mercer_spheres_are_never_counted_as_somersloops(game, state):
    """They share the WAT prefix and do nothing for production."""
    budget = state.sloop_budget()
    assert budget["item"] == "Desc_WAT1_C"
    assert budget["mercer_spheres"] > 0
    assert budget["free"] != budget["free"] + budget["mercer_spheres"]


def test_committed_sloops_are_measured_not_guessed(game, state):
    """This module once claimed slotted sloops were unreadable. They are not: they sit in
    InventoryPotential, the SAME component as Power Shards, which the sidecar already
    read. The wrong conclusion came from probing a save taken before the research, where
    no somersloop existed anywhere -- absence of a value read as absence of a field.

    This fixture settles it: one Manufacturer holds 4 sloops, so `committed` is 4 read off
    the slots, `owned` is 11 free plus those 4, and the flag says the component was readable
    rather than that the answer happened to be zero. The earlier fixture had none slotted,
    which is exactly the state that made the wrong conclusion look supported."""
    budget = state.sloop_budget()
    assert budget["committed_measured"] is True
    assert budget["committed"] == 4
    assert [h["sloops"] for h in budget["holders"]] == [4]
    assert budget["free"] == 11
    assert budget["owned"] == budget["free"] + budget["committed"]


def test_a_slotted_sloop_is_counted_and_agrees_with_the_saved_boost(game, state):
    """The count comes from the slot contents, and `mPendingProductionBoost` gives an
    independent check: a Manufacturer with all 4 slots filled reports 2.0x, which is exactly
    boost_for(4). Two fields written by different parts of the game agreeing is what says the
    slot inventory is being read as slots and not as some other inventory."""
    budget = state.sloop_budget()
    assert budget["committed"], "the fixture must keep at least one slotted sloop"
    for holder in budget["holders"]:
        assert holder["sloops"] >= 1
        assert holder["boost_in_save"] is not None
        assert holder["boost"] == pytest.approx(float(holder["boost_in_save"]))


# ------------------------------------------------------------ the tool


def _bill_line(out: str) -> str:
    """The spend bill, selected by what it says rather than by being first.

    It stopped being the first somersloop line once the research gate was added, and the
    gate belongs above it: a bill for sloops you cannot yet place is the less urgent half.
    """
    return next(x for x in out.splitlines() if "somersloops:" in x)


def test_the_tool_prints_a_bill_when_it_spends(game):
    out = srv.plan_factory(sloops=16, limit=3, **SPIRE)
    line = _bill_line(out)
    assert "16 spent" in line
    assert "You hold" in line
    # Committed sloops are context, never added to what can pay for the plan.
    assert "free" in line


def test_the_tool_says_short_when_the_budget_exceeds_what_is_held(game):
    out = srv.plan_factory(sloops=64, limit=3, **SPIRE)
    assert "SHORT by" in _bill_line(out)


def test_a_sloop_budget_warns_when_the_research_is_missing(game, live):
    """Spending sloops needs Production Amplifier researched, and the save carries no flag
    for it -- it is derived from the purchased schematics. Planning ahead of the research
    is legitimate, so this warns rather than refusing, but staying silent would print a
    plan that cannot be built as shown."""
    out = srv.plan_factory(sloops=16, limit=3, **SPIRE)
    if live.has_capability("production_boost"):
        assert "NOT RESEARCHED" not in out
        return
    line = next(x for x in out.splitlines() if "NOT RESEARCHED" in x)
    assert "Production Amplifier" in line
    assert "Somersloop" in line and "SAM Fluctuator" in line


def test_no_budget_means_no_research_warning(game):
    """The gate is about SPENDING them. A plan that spends none is buildable today."""
    out = srv.plan_factory(limit=3, **SPIRE)
    assert "NOT RESEARCHED" not in out


def test_spending_none_still_points_at_the_argument(game):
    """The old note said "nothing here plans sloops", which stopped being true."""
    out = srv.plan_factory(limit=3, **SPIRE)
    line = _bill_line(out)
    assert "none used" in line
    assert "sloops=" in line


def test_the_budget_changes_the_plan_id(game, state):
    """Two plans that differ only in sloops must not be able to claim the same id --
    that is the whole promise plan_id makes."""
    a = build_scenario(game, state, **SPIRE)
    b = build_scenario(game, state, sloops=16, **SPIRE)
    assert a.plan_id != b.plan_id


def test_plan_layout_takes_the_same_budget(game):
    """plan_layout dropping a solve-shaping argument is exactly how it once schematised
    a different plant than the one it was asked to draw."""
    from satisfactory_mcp.domain.planning.stored.store import PLAN_ARGS

    assert "sloops" in PLAN_ARGS
    out = srv.plan_layout(sloops=16, limit=3, **SPIRE)
    assert not out.startswith("! ")

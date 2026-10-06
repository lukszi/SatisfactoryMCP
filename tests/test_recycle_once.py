"""Running a recipe cycle once, without banning it.

"The Recycled recipes are wanted, just not recursively" is a request `exclude_recipes`
cannot express: banning the recipe also bans the useful single pass. `recycle_once` names
a set of processes that may run but must not feed each other -- consumption of a looped
item inside the set is capped by production of it OUTSIDE the set.

That is exactly one pass, with no pass counting anywhere. A literal "twice round" would
need the cycle unrolled into indexed copies with its items split per pass, which is a
different formulation and is deliberately not pretended at.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.optimize import build_processes, solve
from satisfactory_mcp.domain.planning.scenario import build_scenario
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

PLASTIC, RUBBER = "Desc_Plastic_C", "Desc_Rubber_C"

#: The saved plan the tool test reads. Named once, so the skip predicate and the call it
#: guards cannot drift apart.
REFERENCE_PLAN = "spire-coast-full"


@pytest.fixture
def coupled(game, state):
    """A plan that WANTS the cycle: both Recycled recipes allowed, plastic demanded."""
    return build_scenario(
        game,
        state,
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW", "Plastic", "Rubber"],
        export_minimums={"Plastic": 2000, "Rubber": 300},
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=64,
    ).scenario


def _looped(sc):
    return frozenset(p.pid for p in build_processes(sc) if "Recycled" in p.label)


def test_unconstrained_the_loop_feeds_itself(coupled):
    """The behaviour that is correct and not always wanted: plastic is made ENTIRELY
    inside the loop and some of it goes straight back in, with Residual Plastic never
    running."""
    sol = solve(coupled)
    assert sol.ok
    inside = sum(
        -r["rates"][PLASTIC]
        for r in sol.processes
        if "Recycled" in r["label"] and r["rates"].get(PLASTIC, 0) < 0
    )
    outside = sum(
        r["rates"][PLASTIC]
        for r in sol.processes
        if "Recycled" not in r["label"] and r["rates"].get(PLASTIC, 0) > 0
    )
    assert inside > 0
    assert outside == 0


def test_once_forces_the_feedstock_to_come_from_outside(coupled):
    sol = solve(replace(coupled, recycle_once=_looped(coupled)))
    assert sol.ok
    inside = sum(
        -r["rates"][PLASTIC]
        for r in sol.processes
        if "Recycled" in r["label"] and r["rates"].get(PLASTIC, 0) < 0
    )
    outside = sum(
        r["rates"][PLASTIC]
        for r in sol.processes
        if "Recycled" not in r["label"] and r["rates"].get(PLASTIC, 0) > 0
    )
    assert inside <= outside + 1e-6, "the loop may not eat its own output"
    assert outside > 0, "something outside the loop must supply the single pass"


def test_the_recipes_still_run(coupled):
    """The whole point of not using exclude_recipes: the single pass is wanted."""
    sol = solve(replace(coupled, recycle_once=_looped(coupled)))
    running = {r["label"] for r in sol.processes if r["machines"]}
    assert any("Recycled Plastic" in x for x in running)
    assert any("Recycled Rubber" in x for x in running)


def test_it_costs_something_and_the_cost_is_the_answer(coupled):
    """Measured at -2.7% on the reference plan. Refusing to recurse is a trade, and
    quoting the number is what makes it a decision rather than a preference."""
    free = solve(coupled)
    once = solve(replace(coupled, recycle_once=_looped(coupled)))
    assert once.ok and free.ok
    assert once.net_mw < free.net_mw
    assert once.net_mw > free.net_mw * 0.9


def test_naming_the_loop_is_the_design(game, state, coupled):
    """Detecting cycles automatically finds 24 items on this recipe set, because every
    package/unpackage pair is one -- Water to Packaged Water and back. Constraining all
    of them makes any plan infeasible, so only the caller can say which loop they mean."""
    procs = build_processes(coupled)
    edges: dict[str, set[str]] = {}
    for p in procs:
        ins = [i for i, v in p.rates.items() if v < 0 and not i.startswith("__")]
        outs = [i for i, v in p.rates.items() if v > 0 and not i.startswith("__")]
        for i in ins:
            edges.setdefault(i, set()).update(outs)
    assert "Desc_Water_C" in edges.get("Desc_PackagedWater_C", set())
    assert "Desc_PackagedWater_C" in edges.get("Desc_Water_C", set())


# ------------------------------------------------------------ the tool


def test_the_tool_takes_a_pattern(planned):
    """The skip is decided by looking for the plan, not by reading the tool's answer.

    ``if free.startswith("! ") or once.startswith("! ")`` skipped on ANY refusal, and
    ``recycle_once`` has refusals of its own -- a pattern matching nothing is one, and the
    test two functions down exists because it must be. So a regression that made the pattern
    stop matching would have turned this test into a skip announcing that the plan was not
    saved, on a machine where it plainly was. A skip claims something about the machine; this
    one now asks the machine, the same way ``test_modules.py`` does, and the refusals are
    asserted against instead of tolerated.
    """
    kw = dict(
        plan=REFERENCE_PLAN,
        sources=list(REFERENCE_FIELD),
        exclude_recipes=["Turbofuel", "Alternate: Compacted Coal", "Coal-Powered Generator"],
        export_minimums={"Plastic": 2000, "Rubber": 300},
        limit=2,
    )
    free = srv.plan_factory(**kw)
    once = srv.plan_factory(recycle_once=["Recycled"], **kw)
    assert not free.startswith("! "), free
    assert not once.startswith("! "), once

    def mw(out: str) -> float:
        return float(next(x for x in out.split() if x.startswith("net_MW=")).split("=")[1])

    assert mw(once) < mw(free)


def test_a_pattern_matching_nothing_is_refused(game, state):
    """Same rule as exclude_recipes: a silently ignored argument would return a plan that
    does the very thing it was told not to."""
    req = build_scenario(
        game,
        state,
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        recycle_once=["No Such Recipe"],
    )
    assert any("nothing matches" in e for e in req.recipe_errors)


def test_it_changes_the_plan_id(game, state):
    kw = dict(objective="max_mw", sources=list(REFERENCE_FIELD), exports=["MW"])
    plain = build_scenario(game, state, **kw)
    once = build_scenario(game, state, recycle_once=["Recycled"], **kw)
    assert plain.plan_id != once.plan_id

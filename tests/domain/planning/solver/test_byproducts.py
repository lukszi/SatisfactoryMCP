"""The byproduct diagnostic: does it name the right item, for the right reason?

The reference case throughout is the design spec's own oil finding -- northern crude
with power as the only export. Every crude->fuel route this save has unlocked emits
Polymer Resin, resin only terminates in Plastic or Rubber, and the Recycled pair
creates both rather than absorbing them. Get any step of that wrong and the tool
confidently sends the player after the wrong fix.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning import byproducts
from satisfactory_mcp.presenters.text import byproducts as byproducts_text

pytestmark = pytest.mark.integration

RESIN = "Desc_PolymerResin_C"
ALUMINA = "Desc_AluminaSolution_C"

#: Northern crude, power only, nothing allowed into a Sink -- the plan the user got
#: stuck on, and the one plan_factory answers with a bare zero.
_CRUDE = dict(
    objective="max_mw",
    sources=["north", "resource:Crude Oil"],
    exports=["MW"],
    allow_sinks=False,
)


@pytest.fixture(scope="module")
def crude(game, state):
    return byproducts.analyse(game, state, **_CRUDE)


def test_the_northern_crude_blocker_is_named_with_its_rate(crude):
    """The whole point of the tool. plan_factory reports 0 MW and no rows, which
    reads as 'crude is worthless'; the real answer is one item with no outlet, and
    the plan is worth ~58 GW the moment it has one."""
    assert [b.name for b in crude.blockers] == ["Polymer Resin"]
    assert crude.blockers[0].rate == pytest.approx(1320.0, rel=1e-3)
    assert crude.base_value == pytest.approx(0.0, abs=1e-6)
    assert crude.open_value > 50_000


def test_a_blocker_is_confirmed_by_a_solve_not_by_a_missing_consumer(crude):
    """Polymer Resin has two consumers in this save's own recipe set and is stuck
    anyway. A 'produced but never consumed' test -- the obvious implementation --
    finds nothing here and reports the plan as fine."""
    top = crude.blockers[0]
    assert top.allowed_consumers == 2
    assert [o.name for o in top.unlocked_outlets] == ["Residual Plastic", "Residual Rubber"]
    assert top.confirmed


def test_a_reachable_chain_is_not_proof_of_an_outlet(crude):
    """The converse error. Plastic has a grounded chain out of it -- Empty Canister,
    Packaged Liquid Biofuel, a Biomass Burner -- so a chain search calls it absorbed
    and clears resin with it. Nothing in a crude scope can supply that biofuel, and
    the LP proves it: opening Plastic is still worth tens of gigawatts."""
    fix = next(f for f in crude.blockers[0].fixes if f.label.endswith("Plastic"))
    assert fix.kind == "export"
    assert fix.gain and fix.value > 50_000


def test_the_recycled_pair_is_reported_as_a_closed_loop_that_creates(crude):
    """The trap this tool exists to make legible: Recycled Plastic and Recycled
    Rubber consume each other's product, so they LOOK like the outlet for a resin
    surplus. No non-negative combination of the two reduces either -- the pair nets
    both upward out of Fuel. Advising them would be actively wrong."""
    loop = crude.blockers[0].loop
    assert loop is not None
    assert loop.items == ("Plastic", "Rubber")
    assert loop.recipes == ("Alternate: Recycled Plastic", "Alternate: Recycled Rubber")
    assert loop.absorbs_nothing
    assert loop.net_creates


def test_unlocked_and_locked_outlets_are_separated_with_how_to_get_them(crude):
    """'You already own the fix' and 'you need a hard drive' are different answers.
    Collapsing them into one consumer list makes the tool unusable for deciding what
    to do next."""
    top = crude.blockers[0]
    assert all(o.source == "" for o in top.unlocked_outlets)
    locked = top.locked_outlets
    assert [o.name for o in locked] == ["Alternate: Polyester Fabric"]
    assert locked[0].source.startswith("MAM research:")


def test_a_fix_that_gains_nothing_is_reported_rather_than_dropped(crude):
    """Polyester Fabric would consume resin and is worth exactly zero here, because
    it needs Mycelia this scope has no source for. Dropping worthless fixes leaves
    the reader assuming an unlock might still be the answer; ranking them last says
    so outright."""
    fixes = crude.blockers[0].fixes
    unlock = next(f for f in fixes if f.kind == "unlock")
    assert not unlock.gain
    assert fixes.index(unlock) == len(fixes) - 1  # ranked last, not hidden


def test_solids_get_a_sink_fix_and_it_is_priced_in_belts_and_megawatts(crude):
    """Resin is solid with 12 sink points, so the AWESOME Sink is legal -- but it is
    a real belt and 30 MW per Sink, and a fix that hides that cost is a fix the
    player only discovers after building it."""
    sink = next(f for f in crude.blockers[0].fixes if f.kind == "sink")
    assert sink.gain
    assert "2 belt(s)" in sink.detail and "60 MW" in sink.detail


def test_probe_count_is_bounded_by_the_budget(game, state):
    """Every probe is a full MILP. Without a budget the fix search grows with the
    consumer count and a diagnostic turns into a minute of solving."""
    small = byproducts.analyse(game, state, max_probes=2, **_CRUDE)
    # base + relaxed probe + at most max_probes budgeted ones (+ no no-sink probe,
    # since sinks are already off here).
    assert small.solves <= 4
    assert small.solves < byproducts.analyse(game, state, max_probes=8, **_CRUDE).solves


# ------------------------------------------------------------------ fluids


def test_a_fluid_dead_end_is_never_offered_a_sink(game, state):
    """Docs.json says Alumina Solution can be discarded for points. It cannot: the
    AWESOME Sink has a conveyor input. Offering 'allow_sinks=true' for a fluid would
    be advice that silently stalls the line in game."""
    rep = byproducts.analyse(
        game,
        state,
        objective="max_item",
        target_item="Silica",
        sources=["all"],
        exports=["Silica"],
        allow_sinks=False,
    )
    top = next(b for b in rep.blockers if b.item == ALUMINA)
    assert top.is_fluid and not top.sinkable
    assert not any(f.kind == "sink" for f in top.fixes)


def test_a_fluid_dead_end_gets_the_packaging_route(game, state):
    """Packaging is the only disposal a fluid has, so it has to be in the answer --
    and the packaged form is what carries the sink points, not the fluid."""
    rep = byproducts.analyse(
        game,
        state,
        objective="max_item",
        target_item="Silica",
        sources=["all"],
        exports=["Silica"],
        allow_sinks=False,
    )
    top = next(b for b in rep.blockers if b.item == ALUMINA)
    assert top.packaging is not None
    packed = game.items[top.packaging.products[0]]
    assert not packed.is_fluid and packed.sinkable


# ------------------------------------------------------------------ no blocker


def test_a_sink_only_byproduct_is_a_finding_not_a_footnote(game, state):
    """With sinks allowed the same plan is feasible and says nothing at all -- while
    resting entirely on 1320/min of resin going into a Sink. Take sinking away and
    it is worth zero, which is exactly what a player who does not want a Sink farm
    needs told."""
    rep = byproducts.analyse(
        game, state, objective="max_mw", sources=["north", "resource:Crude Oil"], exports=["MW"]
    )
    assert not rep.blockers
    assert rep.sink_only.get("Polymer Resin") == pytest.approx(1320.0, rel=1e-3)
    assert rep.no_sink_value == pytest.approx(0.0, abs=1e-6)


def test_an_item_the_caller_asks_about_is_not_dressed_up_as_the_blocker(game, state):
    """Focusing on Heavy Oil Residue must not price 'fixes' for it: those probes open
    entirely different routes and would be reported under this item's name. The
    honest answer to 'what eats HOR' is the consumer list."""
    rep = byproducts.analyse(game, state, item="Heavy Oil Residue", **_CRUDE)
    top = rep.blockers[0]
    assert not top.confirmed
    assert top.fixes == []
    assert len(top.unlocked_outlets) > 1


def test_an_item_the_working_plan_already_consumes_is_never_called_stuck(game, state):
    """Every item balance is an equality, so anything a FEASIBLE plan produces is
    already consumed by it exactly -- it has an outlet by construction. The relaxed
    probe cannot see that: under a minimising objective, dumping an intermediate
    always beats processing it, so the probe exports Heavy Oil Residue here. Trusting
    it reports a working 203 MW plan as 'STUCK ... nothing here rescues it'."""
    kw = dict(
        objective="min_power",
        sources=["north", "resource:Crude Oil"],
        exports=["Plastic"],
        export_minimums={"Plastic": 100},
        allow_sinks=False,
    )
    rep = byproducts.analyse(game, state, **kw)
    assert rep.base_value is not None and rep.base_value > 0  # the plan works
    assert rep.blockers == []
    assert "no dead-end byproduct" in byproducts_text.explain(game, state, **kw)


def test_the_lead_line_never_contradicts_the_line_under_it(game, state):
    """Five recipes in this scope make Heavy Oil Residue, and the second line of the
    response names one of them. A bare rate==0 test opens with 'not produced in this
    scope' directly above it, and a reader who catches the contradiction has no
    reason to believe the rest of the answer."""
    text = byproducts_text.explain(game, state, item="Heavy Oil Residue", **_CRUDE)
    first, second = text.splitlines()[:2]
    assert "not produced in this scope" not in first
    assert "none is left over" in first
    assert "Alternate: Heavy Oil Residue" in second
    # The genuinely absent item still gets the blunt answer.
    assert "not produced in this scope" in byproducts_text.explain(
        game, state, item="Nitrogen Gas", **_CRUDE
    )


def test_an_item_nothing_in_the_save_consumes_says_so(game, state):
    """Nitrogen Gas has six consumers in the game and none of them unlocked here.
    'Zero outlets you own' and 'zero outlets exist' are different answers and the
    response must not merge them."""
    rep = byproducts.analyse(game, state, item="Nitrogen Gas", **_CRUDE)
    top = rep.blockers[0]
    assert top.allowed_consumers == 0
    assert top.outlets and not top.unlocked_outlets
    assert "nothing you own consumes it" in byproducts_text.explain(
        game, state, item="Nitrogen Gas", **_CRUDE
    )


# ------------------------------------------------------------------ rendering


def test_the_response_leads_with_the_finding_and_stays_compact(game, state):
    """Context budget is the binding constraint, and a diagnostic that opens with a
    table has buried its own answer."""
    text = byproducts_text.explain(game, state, **_CRUDE)
    lines = text.splitlines()
    assert lines[0].startswith("# STUCK: Polymer Resin")
    assert "58369" in lines[0]  # what it is worth once fixed, on the first line
    assert len(text) < 1200
    assert len(lines) <= 12


def test_gain_direction_follows_the_objective():
    """A sign error here ranks every fix backwards, and for min_machines it would
    recommend the most expensive option with a straight face."""
    assert byproducts._improved("max_mw", 0.0, 10.0)
    assert not byproducts._improved("max_mw", 10.0, 0.0)
    assert byproducts._improved("min_machines", 10.0, 5.0)
    assert not byproducts._improved("min_machines", 5.0, 10.0)
    # Infeasible is worse than any number, whichever way the objective points.
    assert byproducts._improved("min_raw", None, 100.0)
    assert not byproducts._improved("max_mw", 0.0, None)

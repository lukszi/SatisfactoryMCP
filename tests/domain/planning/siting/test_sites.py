"""Cutting one plan into named sites, and what crosses between them.

The planner who asked for a joint multi-site SOLVER concluded, after building the
three-module oil plant by hand, that the solver was the wrong shape:

    The thing that actually bit me wasn't optimisation across sites -- it was accounting
    across sites.

Their split was preference-driven, not physical: only the rig is forced coastal, by water
being drawable at sea level. A joint LP with no per-site cap would collapse all three into
one, and that collapse would be CORRECT, since nothing in the model prices distance.

So this tests the half with a defensible answer: declare the partition, report it, and
fail loudly when it is incomplete -- which is the exact error the hand reconciliation made.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.layout.site_partition import partition
from satisfactory_mcp.domain.planning.solver.prepare import prepare
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("planned")]

RIG = ["Heavy Oil Residue", "Diluted Fuel", "Water Extractor", "Oil Extractor"]
HALL = ["Fuel-Powered Generator"]
RESIN = ["Residual Plastic", "Residual Rubber"]
THREE = {"A-rig": RIG, "B-hall": HALL, "C-resin": RESIN}


@pytest.fixture
def decoupled(game, planned):
    kwargs = dict(planned.plans.find("spire-coast-full").kwargs())
    kwargs["sources"] = list(REFERENCE_FIELD)
    return prepare(game, planned, kwargs)


# ------------------------------------------------------- the regression case


def test_the_decoupled_plan_still_solves_to_its_known_answer(decoupled):
    """The planner verified 99,729.62 MW / 787 buildings on autosave_2. Anything that
    moves this invalidates every interface number below it."""
    assert decoupled.solution.net_mw == pytest.approx(99_729.62, abs=0.5)
    assert decoupled.solution.machines_total == pytest.approx(787, abs=0.5)


def test_the_interface_table_matches_the_hand_built_one(decoupled, game):
    """Reproduces the planner's table B exactly: 9,200 Fuel on 16 pipes, 2,300 Polymer
    Resin on 3 belts, 1,100 Water on 2 pipes."""
    sp = partition(decoupled, game, THREE)
    assert sp.ok
    by = {(i.source, i.target, i.name): i for i in sp.interfaces}
    fuel = by[("A-rig", "B-hall", "Fuel")]
    assert fuel.rate == pytest.approx(9_200, rel=0.01) and fuel.lines == 16
    resin = by[("A-rig", "C-resin", "Polymer Resin")]
    assert resin.rate == pytest.approx(2_300, rel=0.01) and resin.lines == 3
    assert resin.carrier == "belt"
    water = by[("A-rig", "C-resin", "Water")]
    assert water.rate == pytest.approx(1_100, rel=0.02) and water.lines == 2


def test_the_coupled_variant_opens_the_fuel_return(game, planned, decoupled):
    """The single interface that defines the architecture. Decoupled it is ZERO; coupled
    it is ~1,150 on 2 pipes, and the hall's share drops from 16 pipes to 14."""
    kw = dict(planned.plans.find("spire-coast-full").kwargs())
    kw["sources"] = list(REFERENCE_FIELD)
    kw["exclude_recipes"] = [x for x in kw["exclude_recipes"] if "Recycled" not in x]
    kw["export_minimums"] = {"Plastic": 2000, "Rubber": 300}
    coupled = prepare(game, planned, kw)
    assert coupled.solution.net_mw == pytest.approx(83_470.97, abs=0.5)

    spec = {**THREE, "C-resin": [*RESIN, "Recycled Plastic", "Recycled Rubber"]}
    sp = partition(coupled, game, spec)
    by = {(i.source, i.target, i.name): i for i in sp.interfaces}
    assert by[("A-rig", "C-resin", "Fuel")].rate == pytest.approx(1_150, rel=0.05)
    assert by[("A-rig", "C-resin", "Fuel")].lines == 2
    assert by[("A-rig", "B-hall", "Fuel")].lines == 14

    # And the decoupled plan has no such interface at all -- the zero IS the design.
    plain = partition(decoupled, game, THREE)
    assert not any(i.name == "Fuel" and i.target == "C-resin" for i in plain.interfaces)


# ------------------------------------------------------- the error it catches


def test_an_incomplete_partition_fails_loudly(decoupled, game):
    """The planner's step-3 error: generator count computed from the rig's whole fuel
    output, assuming all of it reached the hall. Leaving a consumer unassigned is how that
    happens, so it must not be quiet."""
    sp = partition(decoupled, game, {"A-rig": RIG, "B-hall": HALL})
    assert not sp.ok
    assert sp.unassigned
    assert any("match no site" in n for n in sp.notes)
    assert any("Residual" in n for n in sp.notes)


def test_a_process_claimed_twice_is_reported_not_resolved(decoupled, game):
    """A machine is in one place. First-wins would hide the ambiguity behind a plausible
    table."""
    sp = partition(decoupled, game, {"X": ["Residual"], "Y": ["Residual Rubber"]})
    assert sp.contested
    assert not sp.ok
    assert any("is in one place" in n for n in sp.notes)


def test_the_tool_says_when_the_table_is_complete(game):
    out = srv.plan_layout(
        plan="spire-coast-full", show="sites", sites=THREE, sources=list(REFERENCE_FIELD)
    )
    assert "every process is assigned to exactly one site" in out
    assert "A-rig\t->\tB-hall\tFuel" in out


def test_the_tool_refuses_without_a_spec(game):
    out = srv.plan_layout(plan="spire-coast-full", show="sites")
    assert "needs sites=" in out
    assert "exclude_recipes" in out


# ------------------------------------------------------- honesty about the split


def test_a_shared_flow_is_split_by_share_and_says_so(game):
    """The LP gives net balances and never who fed whom, so an exact producer-consumer
    pairing would be invented -- the same reason a layout models a bus."""
    out = srv.plan_layout(plan="spire-coast-full", show="sites", sites=THREE)
    assert "split between consumers by SHARE" in out


def test_site_power_excludes_the_sink_charge(decoupled, game):
    """It is charged per belt line of sunk material and belongs to the plan, not to a
    site. Prorating it would invent an attribution."""
    sp = partition(decoupled, game, THREE)
    assert all(s.slice.sink_mw == 0.0 for s in sp.sites)
    out = srv.plan_layout(plan="spire-coast-full", show="sites", sites=THREE)
    assert "excludes the AWESOME Sink charge" in out


# ------------------------------------------------------- a site that matched nothing


def test_an_empty_site_is_reported_not_silent(decoupled, game):
    """A site whose every pattern hits nothing must fail loudly: nothing it should
    contain reaches the interface table, which is exactly how a supplier goes missing
    from a hand reconciliation."""
    sp = partition(decoupled, game, {"hall": ["Nuclear Pasta"], "rest": [*RIG, "Residual"]})
    assert not sp.ok
    assert sp.empty == ["hall"]
    assert any("matched NO process" in n for n in sp.notes)
    # And it says what a pattern actually matches, since that is the misconception.
    assert any("never any other item" in n for n in sp.notes)


def test_a_site_can_be_keyed_on_the_power_it_produces(decoupled, game):
    """The failure that cost a caller the entire fuel interface: they keyed a generator
    site on the item it produces (MW), which matched no process LABEL. The site came
    back empty, its 460 generators fell into `unassigned`, and the 9,200 m3/min fuel
    flow -- the one number the whole three-building split turns on -- was missing from
    the table. Power is a product a site can be defined by, in the spellings exports
    already accepts."""
    for pattern in ("MW", "mw", "power", "Power"):
        sp = partition(decoupled, game, {"hall": [pattern], "rest": [*RIG, "Residual"]})
        assert sp.ok, (pattern, sp.notes)
        hall = next(s for s in sp.sites if s.name == "hall")
        assert hall.machines == 460, pattern
        fuel = next(i for i in sp.interfaces if i.name == "Fuel" and i.target == "hall")
        assert fuel.rate == pytest.approx(9_200, rel=0.01), pattern


def test_a_power_token_is_exact_not_a_substring(decoupled, game):
    """ "power" as a substring would also claim every "Fuel-Powered Generator" LABEL --
    redundantly today, and wrongly the day a non-generator label contains the word. The
    token means "the generators", never "anything mentioning power"."""
    sp = partition(decoupled, game, {"hall": ["power"], "also-hall": ["Fuel-Powered Generator"]})
    # Every generator is CONTESTED between the two spellings -- proof the token matched
    # the same machines the label does, rather than a superset grown by substring.
    assert sp.contested
    assert all("hall" in owners and "also-hall" in owners for _, owners in sp.contested)


def test_head_order_respects_the_site_partition(decoupled, game):
    """order_floors_by="head" used to optimise ONE merged stack: over this three-site
    plan it fused rig, hall and resin plant into a single tower and priced the fluid
    lift accordingly, where the right answer stacks each site by itself. Declared sites
    are separate buildings, so the floors are ordered within each independently and the
    whole-plan riser count is the SUM of the per-site counts."""
    from satisfactory_mcp.domain.planning.layout.head import fluid_head
    from satisfactory_mcp.domain.planning.layout.schematic import build_layout
    from satisfactory_mcp.domain.planning.layout.service import _layout_by_site

    fused = build_layout(game, decoupled.solution, order_floors_by="head")
    merged, per_site = _layout_by_site(
        game,
        decoupled.solution,
        THREE,
        belt_ipm=780.0,
        pipe_m3min=600.0,
        max_floor_foundations=0,
        order_floors_by="head",
    )

    def pumps(lay):
        return sum(d["pumps"] for d in fluid_head(lay, 50.0))

    assert pumps(merged) < pumps(fused)
    assert pumps(merged) == sum(pumps(sub) for _, sub in per_site)
    # A relabelling, never a re-solve: every machine and every tile survives the split.
    assert merged.machines == fused.machines
    assert {b.key for b in merged.blocks} == {b.key for b in fused.blocks}
    assert merged.total_foundations == fused.total_foundations
    # Every floor is owned by a declared site -- this partition is complete, so nothing
    # falls into the trailing "(unassigned)" stack.
    assert {f.site for f in merged.floors} == set(THREE)


def test_an_incomplete_partition_still_lays_out_every_block(decoupled, game):
    """Unclaimed processes land in a visible "(unassigned)" stack rather than vanishing
    -- a block dropped here would silently shrink the materials bill."""
    from satisfactory_mcp.domain.planning.layout.schematic import build_layout
    from satisfactory_mcp.domain.planning.layout.service import _layout_by_site

    merged, per_site = _layout_by_site(
        game,
        decoupled.solution,
        {"A-rig": RIG, "B-hall": HALL},
        belt_ipm=780.0,
        pipe_m3min=600.0,
        max_floor_foundations=0,
        order_floors_by="head",
    )
    assert dict(per_site).keys() == {"A-rig", "B-hall", "(unassigned)"}
    whole = build_layout(game, decoupled.solution)
    assert merged.machines == whole.machines


def test_the_tool_reports_per_site_stacks(game):
    out = srv.plan_layout(
        plan="spire-coast-full",
        sites=THREE,
        order_floors_by="head",
        sources=list(REFERENCE_FIELD),
    )
    # One height per building, never one summed tower.
    assert "stacks=A-rig" in out
    assert "stack_height=" not in out
    assert "sites are SEPARATE buildings" in out
    # The floor table names the site each deck belongs to.
    assert "site\tfloor\tkind" in out


def test_a_dead_pattern_is_named(decoupled, game):
    """A site can match SOMETHING while one of its patterns matches nothing -- a typo in
    a four-pattern list would otherwise never surface."""
    sp = partition(decoupled, game, {"hall": [*HALL, "Nuclear Pasta"], "rest": [*RIG, "Residual"]})
    assert ("hall", "Nuclear Pasta") in sp.dead_patterns
    assert any("matches nothing in this plan" in n for n in sp.notes)


def test_a_generator_site_works_by_label_or_by_class(decoupled, game):
    """Both spellings a caller would reach for, so the fix is not one magic string."""
    for pattern in ("Fuel-Powered Generator", "Build_GeneratorFuel_C"):
        sp = partition(decoupled, game, {"hall": [pattern], "rest": [*RIG, "Residual"]})
        hall = next(s for s in sp.sites if s.name == "hall")
        assert hall.machines == 460, pattern
        assert any(i.name == "Fuel" and i.target == "hall" for i in sp.interfaces), pattern

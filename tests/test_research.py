"""MAM research, and reading a capability out of a save.

Production amplification -- the research that lets a Somersloop enter a machine at all --
gates `plan_factory(sloops=)`. Probing a save from before the research found no key
containing "Boost", "Amplif" or "Sloop" anywhere in its 44,307 objects, and this module
first concluded the game records no flag. **It does.**
`BP_UnlockSubsystem_C.mIsBuildingProductionBoostUnlocked` appears the moment the research
completes; UE omits a SaveGame property still at its default, so absent means false.
"Not in this file" and "no such field" are different claims, and only the first was
evidence.

So the flag is authoritative when present, with the purchased-schematic set as fallback --
which is not redundant, because a projection written before schema 10 extracted the flag
looks exactly like a world that never did the research.

The tests below pin that, and that the planner refuses to be silent when a plan spends a
capability the player has not got.

They read the `live` fixture rather than `state` where they judge a tool's OUTPUT, and here
that is not a preference: the tools read the newest save on this machine, so judging their
answers against the committed projection would be comparing two different worlds.

THE THREE TESTS ABOUT THE LOCKED STATE BUILD THAT STATE, and this is the point of the
`locked` fixture below. They used to branch on `live.has_capability("production_boost")` --
one with `pytest.skip`, two with a bare `return` -- and every one of those branches is now
taken: the reference world researched Production Amplifier, and the committed projection has
carried `mIsBuildingProductionBoostUnlocked: True` since it was re-cut at schema 11. So the
three tests that exist to check what the tools say when a capability is LOCKED had not
executed a single one of their assertions in a long time, and the two written with `return`
did not even leave a skip in the report to say so. A test that reports success without
asserting anything is worse than a missing test, because it is counted.

The state a save cannot supply on this machine is therefore constructed: the flag turned off
and the gating schematic removed from the purchased set, which is exactly the pair of things
the game writes when the research has not been done. Nothing else is touched -- the recipes
stay unlocked, because that is a different gate and the plan still has to be computable for
the warning to be about anything.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp import server as srv
from satisfactory_mcp.core.gamedata.constants import CAPABILITY_SCHEMATICS
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration

#: The capability every gate test below is about, and the one the register has a flag for.
BOOST = "production_boost"


def _unresearched(game, projection: dict) -> WorldState:
    """The same world with Production Amplifier not yet researched.

    Both halves are needed and they are not redundant: `has_capability` reads the unlock flag
    when the projection carries one and falls back to the purchased-schematic set when it does
    not, so clearing only one of them leaves the other answering "researched". Clearing both
    is what a save from before the research actually looks like -- UE omits a SaveGame property
    still at its default, so the flag is absent rather than false, and the schematic is simply
    not in the purchased list.

    Nothing else is touched. The available-recipe set in particular is left alone, because it
    is a different gate: the plan still has to be computable for the warning to be about
    anything, and a state with no recipes would pass these tests for the wrong reason.
    """
    copy = deepcopy(projection)
    copy.setdefault("unlock_flags", {}).pop("mIsBuildingProductionBoostUnlocked", None)
    progression = copy.setdefault("progression", {})
    gate = CAPABILITY_SCHEMATICS[BOOST]
    progression["purchased_schematics"] = [
        s for s in progression.get("purchased_schematics") or () if s != gate
    ]
    st = WorldState(projection=copy, game=game)
    assert not st.has_capability(BOOST), (
        "the constructed state still reports the capability as researched -- "
        "has_capability now reads something this helper does not clear"
    )
    assert st.research_gate(BOOST) is not None, "a locked capability must have a gate to clear"
    return st


@pytest.fixture
def locked(game, live, use_world) -> WorldState:
    """This machine's world, un-researched, and every tool pointed at it.

    Built from `live` rather than from the committed projection because the tools it feeds
    print a PLAN, and a plan is judged against the world the reader is playing. One world for
    every tool, because `mam_research` and `plan_factory` have to be answering about the same
    world for their two halves of the same warning to line up.
    """
    st = _unresearched(game, live.projection)
    use_world(st)
    return st


@pytest.fixture
def locked_fixture(game, projection) -> WorldState:
    """The committed projection, un-researched. No save and no monkeypatch needed.

    The fourth dead branch in this file used the `state` fixture and skipped when its gate came
    back `None` -- which it always does now: the committed projection has carried
    `mIsBuildingProductionBoostUnlocked: True` since it was re-cut at schema 11, so the test
    that prices a gate had no gate to price. Nothing about pricing needs the newest save, so
    this one is built from the fixture and the test runs everywhere the suite does.
    """
    return _unresearched(game, projection)


# ------------------------------------------------------- the register


def test_every_gate_names_a_schematic_that_exists(game):
    """A typo here would silently report a capability as permanently locked."""
    assert CAPABILITY_SCHEMATICS
    for capability, cls in CAPABILITY_SCHEMATICS.items():
        assert cls in game.schematics, capability
        assert game.schematics[cls].type == "EST_MAM"


def test_production_boost_is_gated_by_production_amplifier(game):
    schematic = game.schematics[CAPABILITY_SCHEMATICS["production_boost"]]
    assert schematic.name == "Production Amplifier"
    costs = {game.item_name(f.item): f.amount for f in schematic.cost}
    assert costs == {"Somersloop": 1, "SAM Fluctuator": 100, "Circuit Board": 50}


# ------------------------------------------------------- reading it from a save


def test_the_schematic_fallback_answers_when_the_flag_is_absent(game, state):
    """A projection written before schema 10 has no unlock flags at all, because nothing
    extracted them then. Falling back to the purchased set is what keeps an older cached
    projection answering correctly instead of reporting every capability locked.

    The flags are stripped here rather than assumed missing. They used to be missing -- the
    committed fixture was schema 5 -- so this test passed without exercising the fallback,
    and regenerating the fixture at schema 11 turned it into a test of the flag path that
    `test_the_flag_wins_when_the_projection_carries_one` already covers."""
    projection = deepcopy(state.projection)
    projection.pop("unlock_flags", None)
    older = WorldState(projection=projection, game=game)
    for capability, cls in CAPABILITY_SCHEMATICS.items():
        assert older.has_capability(capability) == (cls in older.purchased_schematic_ids)


def test_the_flag_wins_when_the_projection_carries_one(game, live):
    """It is what the game itself checks, so it outranks the inference."""
    flags = live.projection.get("unlock_flags") or {}
    if "mIsBuildingProductionBoostUnlocked" not in flags:
        pytest.skip("live projection predates schema 10")
    assert live.has_capability("production_boost") is bool(
        flags["mIsBuildingProductionBoostUnlocked"]
    )


def test_an_unknown_capability_is_locked_rather_than_crashing(game, state):
    assert state.has_capability("no_such_capability") is False
    assert state.research_gate("no_such_capability") is None


def test_a_researched_capability_has_no_gate(game, state):
    """`research_gate` returns None once done, so a truthy result always means 'still in
    the way' and a caller needs no second check."""
    for capability in CAPABILITY_SCHEMATICS:
        if state.has_capability(capability):
            assert state.research_gate(capability) is None


def test_a_gate_prices_itself_against_spendable_stock(locked_fixture):
    """What a gate says: the schematic, the bill, and what the player already holds.

    The stock is the LOCKED state's own, which is the same stock the researched one has --
    turning the flag off does not spend anything -- so the last loop is still comparing the
    gate's numbers against the world's, and not against numbers this test invented.
    """
    gate = locked_fixture.research_gate(BOOST)
    assert gate["schematic_name"] == "Production Amplifier"
    assert gate["cost"] and all("need" in r and "have" in r for r in gate["cost"])
    assert gate["affordable"] == (not gate["short"])
    stock = locked_fixture.stock()
    for row in gate["cost"]:
        assert row["have"] == stock.get(row["item"], 0.0)


# ------------------------------------------------------- the planner gate


PLAN_KW = dict(
    sources=list(REFERENCE_FIELD),
    objective="max_mw",
    exports=["MW"],
    extractor_clocks=[1, 1.5, 2, 2.5],
    limit=2,
)


def test_spending_sloops_without_the_research_is_called_out(locked):
    """The same class of check as the unlocked recipe set: a plan using a locked
    capability is not a plan. It warns rather than refusing, because planning ahead of
    cheap research is legitimate — but silence would print an unbuildable plan."""
    out = srv.plan_factory(sloops=16, **PLAN_KW)
    assert "PRODUCTION AMPLIFIER IS NOT RESEARCHED" in out
    assert "not buildable as printed" in out
    # And it says what to do about it, with the bill.
    assert "SAM Fluctuator" in out


def test_the_same_plan_is_silent_once_the_research_is_done(game, live):
    """The other side of the branch this file used to be stuck on, kept as its own test.

    Reading the real world rather than a constructed one, because "the warning does not fire
    when it should not" is a claim about the ordinary case and the ordinary case is whatever
    is on this machine. It skips on the state instead of asserting the opposite of the test
    above, so the two never both pass by being the same assertion twice.
    """
    if not live.has_capability(BOOST):
        pytest.skip("this machine's save has not researched Production Amplifier")
    assert "NOT RESEARCHED" not in srv.plan_factory(sloops=16, **PLAN_KW)


def test_the_gate_only_fires_when_sloops_are_actually_budgeted(locked):
    """It is about SPENDING them. A plan that spends none is buildable today, and a
    warning there would be noise on every single call.

    Run against the LOCKED world now, which is the only world where it says anything: on a
    machine that has done the research, "no warning" was true for the wrong reason and the
    test could not have failed.
    """
    assert "NOT RESEARCHED" not in srv.plan_factory(**PLAN_KW)


# ------------------------------------------------------- the tool


def test_mam_research_lists_outstanding_nodes(game):
    out = srv.mam_research()
    assert not out.startswith("! ")
    assert "status\tresearch\tcapability" in out
    assert "outstanding" in out


def test_it_marks_which_research_gates_a_capability(locked):
    """The point of the column: 'LOCKS production_boost' is why a reader should care about
    that row rather than treating the MAM as a pile of optional recipes."""
    # Narrowed with `search` rather than a big limit: Limit is schema-capped at 25 and
    # there are 120 MAM nodes, so the row would fall off the bottom of an unfiltered call.
    out = srv.mam_research(show="all", query="Production Amplifier")
    assert "LOCKS production_boost" in out


def test_a_locked_capability_gets_a_note_with_its_bill(locked):
    """The note under the table, which is where a reader who did not search for the row
    finds out that the capability they are planning around is not theirs yet.

    This is the test that used to `return` rather than skip when the machine had done the
    research: it reported PASS having asserted nothing at all.
    """
    out = srv.mam_research()
    line = next(x for x in out.splitlines() if "production_boost is NOT researched" in x)
    assert "Production Amplifier" in line
    assert "Somersloop" in line


def test_affordable_narrows_to_what_can_be_done_now(game):
    everything = srv.mam_research(show="all", limit=80)
    ready = srv.mam_research(show="affordable", limit=80)
    assert "DONE" in everything
    # Status is the FIRST column of a data row. Matching the bare word would hit the
    # "short by" column HEADER, which is always present and says nothing about the rows.
    statuses = {r.split("\t")[0] for r in ready.splitlines() if "\t" in r}
    assert "READY" in statuses
    assert statuses <= {"status", "READY", "BLOCKED"}, statuses


def test_an_unknown_status_lists_the_choices(game):
    assert "all, todo, affordable" in srv.mam_research(show="bogus")


def test_search_filters_by_name(game):
    out = srv.mam_research(show="all", query="amplifier")
    rows = [x for x in out.splitlines() if "\t" in x][1:]
    assert rows
    assert all("mplifier" in r for r in rows)


def test_the_cost_note_names_the_buckets_the_check_actually_reads(game):
    """It used to say costs were checked against crates, which is the one bucket schema 19
    deliberately took OUT of spendable stock. A note that contradicts the arithmetic under
    it is worse than no note: it invites the reader to count a death crate's contents as
    money in hand."""
    out = srv.mam_research()
    note = next(x for x in out.splitlines() if x.startswith("! cost is checked"))
    assert "carried, storage containers and the Dimensional Depot" in note
    assert "neither do the crates on the ground" in note


# ------------------------------------------------- which tree, and what is already running


@pytest.fixture
def constructed(game, projection, use_world):
    """A copy of the committed projection the caller may edit, wired into the tool.

    The reference world has every MAM tree open and nothing under research, which is
    exactly the state in which the two filters below cannot be observed -- so the states
    they are about are built rather than waited for.
    """

    def build(**research) -> WorldState:
        copy = deepcopy(projection)
        copy.setdefault("research", {}).update(research)
        st = WorldState(projection=copy, game=game)
        use_world(st)
        return st

    return build


def _rows(out: str) -> list[list[str]]:
    return [line.split("\t") for line in out.splitlines() if "\t" in line][1:]


def test_every_mam_node_is_placed_in_a_tree(game, state):
    """The register is a prefix table over class ids, because Docs.json ships no research
    trees. A game patch that adds a MAM node under a new prefix must fail here: unplaced
    nodes are silently exempt from the tree filter, which is the failure that reads as
    'available' for research the player cannot start."""
    mam = [cls for cls, s in game.schematics.items() if s.type == "EST_MAM"]
    assert mam
    unplaced = [cls for cls in mam if state.research.tree_of(cls) is None]
    assert unplaced == []


def test_the_saves_own_tree_list_holds_no_tree_this_register_cannot_name(state):
    """The other direction, and the reason the hard-drive tree is absent from the register
    rather than forgotten: it is the one unlocked tree whose nodes are EST_Alternate
    schematics won from drives, which no MAM view lists."""
    named = set(state.research.TREE_PREFIXES) | {"BPD_ResearchTree_HardDrive_C"}
    assert state.research.unlocked_trees <= named


def test_a_node_in_an_unopened_tree_is_not_called_ready(constructed, state):
    """The P0 this closes: with the tree shut, the game offers no way to start the node,
    and the tool said READY on the strength of the bill alone."""
    open_trees = sorted(state.research.unlocked_trees)
    shut = [t for t in open_trees if t != "BPD_ResearchTree_XMas_C"]
    constructed(unlocked_trees=shut)
    # show=todo, so the FICSMAS nodes this world already finished are out of it: a
    # finished node is finished whatever its tree says now.
    out = srv.mam_research(show="todo", query="FICSMAS", limit=25)
    statuses = {row[0] for row in _rows(out)}
    assert statuses == {"TREE SHUT"}, statuses
    assert "TREE SHUT means" in out

    # And with the tree open again, the same rows go back to being ordinary work.
    constructed(unlocked_trees=open_trees)
    reopened = {row[0] for row in _rows(srv.mam_research(show="todo", query="FICSMAS"))}
    assert reopened and "TREE SHUT" not in reopened


def test_a_shut_tree_is_never_offered_as_affordable(constructed, state):
    """show=affordable answers 'what can I do right now', so a node behind a closed tree
    belongs out of it however cheap it is."""
    constructed(unlocked_trees=[])
    out = srv.mam_research(show="affordable", limit=25)
    assert _rows(out) == []


def test_research_already_under_way_says_so_and_says_how_long(constructed, game):
    """`ongoing` carries seconds remaining and nothing read it, so a node the player had
    already paid for and started read as outstanding work to plan around."""
    running = "Research_Sulfur_RocketFuel_C"
    constructed(ongoing=[{"schematic": running, "seconds_left": 420.0}])
    name = game.schematics[running].name
    out = srv.mam_research(show="all", query=name)
    (row,) = _rows(out)
    assert row[0] == "RUNNING 420s"
    assert "RUNNING is research already under way" in out
    # Paid for and started: not something the player can go and do now.
    assert name not in srv.mam_research(show="affordable", limit=25)


def test_an_older_projection_says_it_cannot_judge_the_trees(game, projection, use_world):
    """Absent is not empty. A projection cut before the key existed cannot tell a shut tree
    from an open one, and guessing either way would be an invented answer -- so it reports
    every node as before and says why."""
    copy = deepcopy(projection)
    copy["research"].pop("unlocked_trees", None)
    st = WorldState(projection=copy, game=game)
    use_world(st)
    assert not st.research.knows_trees
    assert not st.research.tree_locked("Research_XMas_1_C")
    assert "predates the unlocked-tree list" in srv.mam_research()

"""The HUB milestone ladder, and the ladder both progression tools now walk.

`mam_research` has answered "what is left, what does it cost, can I afford it" for the MAM
since the surface existed; the HUB half of the same question had no tool at all, and the
only thing any tool said about milestones was the count string `world_summary` prints. So
these tests are as much about the two views agreeing as about the numbers: one
`SchematicLadder`, one set of status words, one stock pool.

The measured expectations are from the committed projection, which is a world at 30 of 42
milestones with tiers 7-9 outstanding. They read the `state` fixture rather than `live`
because a ladder priced against whatever save is newest cannot pin a number.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.progression.ladder import SchematicLadder
from satisfactory_mcp.interfaces.mcp.tools import progression as progression_tools

pytestmark = pytest.mark.integration


@pytest.fixture
def ladder(state) -> SchematicLadder:
    return SchematicLadder(game=state.game, unlocks=state.unlocks, inventory=state.inventory)


@pytest.fixture
def tool(state, monkeypatch):
    """The tool pointed at the committed projection, not at this machine's save.

    Patched in the module that calls it, for the reason `test_research.py` records: `_state`
    is looked up where it is used, so patching `app._state` would leave this module holding
    the original.
    """
    monkeypatch.setattr(
        progression_tools, "_state", lambda save=None, world=None, as_of=None: state
    )
    return progression_tools.milestones


# ------------------------------------------------------------------ the ladder


def test_the_bill_is_priced_against_the_same_stock_mam_research_uses(ladder, state):
    """One pool, not two. `stock()` is carried + storage + Depot and deliberately excludes
    machine buffers, so a milestone cannot read as affordable because the parts are sitting
    on a belt."""
    stock = state.stock()
    for rung in ladder.rungs("EST_Milestone"):
        for m in rung.missing:
            assert m.have == stock.get(m.item, 0.0)
            assert m.short_by > 0


def test_hoverpack_is_affordable_and_nuclear_power_is_short_exactly_50_supercomputer(ladder, state):
    """The two measured rows. Tier 7 Hoverpack is READY on this world -- 100 Alclad
    Aluminum Sheet, 100 Heavy Modular Frame, 100 Computer and 250 Motor are all in stock --
    and Tier 8 Nuclear Power is short of one item by one number. Both are worth pinning:
    the first is the answer the player wants ("go and buy this now") and the second is the
    shortfall arithmetic, which is what makes the answer actionable rather than a list.
    """
    rungs = {r.schematic.name: r for r in ladder.rungs("EST_Milestone")}

    hoverpack = rungs["Hoverpack"]
    assert hoverpack.status == "READY"
    assert hoverpack.missing == ()

    nuclear = rungs["Nuclear Power"]
    assert nuclear.status == "short"
    assert [(state.game.item_name(m.item), m.short_by) for m in nuclear.missing] == [
        ("Supercomputer", 50.0)
    ]


def test_a_purchased_milestone_is_done_and_the_count_matches_the_tier_table(ladder, state):
    """Cross-check against the other view of the same fact: `progression()` counts
    purchased EST_Milestone schematics per tier and this walks them one by one, so a
    disagreement means one of the two is reading a different set."""
    rungs = ladder.rungs("EST_Milestone")
    assert len(rungs) == 42
    assert sum(1 for r in rungs if r.done) == 30
    by_tier = state.progression()["milestones_by_tier"]
    assert by_tier[7] == "3/5"
    assert sum(1 for r in rungs if r.done and r.schematic.tier == 7) == 3


def test_the_mam_view_of_the_same_ladder_is_unchanged_by_it(ladder, state):
    """The refactor's own check. `mam_research` walks EST_MAM through this class now, so
    its rows must still be the MAM tree in name order priced against the same stock -- less
    the nine retired nodes the dump still ships (eight "Discontinued" and "SPWN", plus one
    with no name at all)."""
    mam = ladder.rungs("EST_MAM")
    assert len(mam) == 111
    names = [r.schematic.name for r in mam]
    assert names == sorted(names, key=str.casefold)
    assert all(r.schematic.type == "EST_MAM" for r in mam)


def test_prerequisites_outrank_the_bill(ladder):
    """A BLOCKED row is not a shopping list: an unmet prerequisite wins over an unpaid
    bill, because buying the parts is not the next move.

    Asserted on the alternates, because neither of the other two types can show it on this
    world: no milestone in Docs.json carries a dependency of any kind, and every MAM
    prerequisite here is already purchased. 24 of the 109 alternates are blocked, and what
    blocks them is a rung of one of the two ladders this file is about -- 22 milestones and
    the MAM nodes Rocket Fuel and Ionized Fuel.
    """
    blocked = [r for r in ladder.rungs("EST_Alternate") if r.blocked_by and not r.done]
    assert len(blocked) == 24
    assert all(r.status == "BLOCKED" for r in blocked)
    rungs = ladder.rungs("EST_Milestone") + ladder.rungs("EST_MAM")
    assert {name for r in blocked for name in r.blocked_by} <= {r.schematic.name for r in rungs}


# ------------------------------------------------------------------ the tool


def test_the_tool_leads_with_what_can_be_bought_now(tool):
    out = tool()
    assert "affordable right now: T7 Hoverpack" in out
    rows = [r.split("\t") for r in out.splitlines() if "\t" in r]
    assert rows[0][:3] == ["status", "tier", "milestone"]
    hoverpack = next(r for r in rows if r[2] == "Hoverpack")
    assert hoverpack[0] == "READY"
    assert hoverpack[4] == ""  # nothing in the short by column


def test_the_shortfall_is_printed_in_full_rather_than_truncated(tool):
    """The answer to "what am I short of" is the one cell that must never be cut: a
    reader who sees three of four missing items builds the wrong factory."""
    row = next(r for r in tool().splitlines() if r.startswith("short\t9\tPeak Efficiency"))
    for item in ("Time Crystal", "Ficsite Trigon", "Alclad Aluminum Sheet", "Iron Plate"):
        assert item in row.split("\t")[4]


def test_tier_narrows_the_view_and_an_empty_tier_says_which_exist(tool):
    tiers = {r.split("\t")[1] for r in tool(show="all", tier=8).splitlines() if "\t" in r}
    assert tiers == {"tier", "8"}
    assert "Tiers are 1-9" in tool(tier=99)


def test_affordable_hides_everything_with_a_bill_outstanding(tool):
    statuses = {
        r.split("\t")[0] for r in tool(show="affordable", limit=25).splitlines() if "\t" in r
    }
    assert statuses == {"status", "READY"}


def test_an_unknown_status_lists_the_choices(tool):
    assert "all, todo, affordable" in tool(show="bogus")


def test_the_aliases_the_rest_of_the_surface_uses_work_here_too(tool):
    """`show=` and `query=` are the settled spellings for a view and a name filter, and a
    client that learned them on mam_research must not get a parse error here."""
    assert "Hoverpack" in tool(show="all", query="hoverpack")
    assert "Hoverpack" in tool(query="hoverpack")


def test_it_says_that_ready_is_about_the_bill_and_not_about_the_tier_being_open(tool):
    """The one thing this data cannot answer, said out loud. Tiers are opened by Space
    Elevator deliveries and no milestone schematic carries that dependency, so a T9 row
    reading READY would otherwise be a promise the game will refuse."""
    note = next(x for x in tool().splitlines() if x.startswith("! READY is about the bill"))
    assert "phase_requirements" in note

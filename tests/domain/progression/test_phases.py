"""Space Elevator phase requirements: the stored phase, the frozen cost table, deliveries.

The save's per-phase cost table is DEPRECATED and frozen, and still bills the player for a
phase finished long ago, so each test pins the counter-example that corrected the code.
"""

from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration


def _with(state: WorldState, **progression) -> WorldState:
    """A copy of the state with progression fields overridden."""
    projection = copy.deepcopy(state.projection)
    projection["progression"].update(progression)
    return WorldState(projection=projection, game=state.game)


# ------------------------------------------------------------------ the EGP mapping


def test_endgame_maps_to_phase_3_which_is_the_one_anchor_that_is_measured(state):
    """THE anchor. Docs.json does not ship the UFGGamePhase assets, so nothing joins
    the deprecated EGP_* enum to GP_Project_Assembly_Phase_N there, and the save's own
    legacy mGamePhase scalar is absent (= EGP_NA = "we have migrated the save").

    What DOES join them is two epochs of this world. At 180-244 h play the save read
    mTargetGamePhase = Phase_3 with mTargetGamePhasePaidOffCosts =
    {Desc_SpaceElevatorPart_2_C: 2500} -- exactly one item settled. The EGP_EndGame row
    of the deprecated table describes those same three items with that same one item at
    zero remaining, and nothing can be paid into a phase that was never the target.
    """
    assert WorldState.EGP_TO_PHASE["EGP_EndGame"] == "GP_Project_Assembly_Phase_3"

    row = next(r for r in state.phase_requirements()["phases"] if r["egp"] == "EGP_EndGame")
    # Part_2 = Versatile Framework, the one item the live paid-off record accounted for.
    assert row["complete"] == ["Desc_SpaceElevatorPart_2_C"]
    assert set(row["outstanding"]) == {
        "Desc_SpaceElevatorPart_4_C",
        "Desc_SpaceElevatorPart_5_C",
    }


def test_the_four_stored_keys_are_contiguous_in_enum_order(state):
    """The other three phases are derived, not measured: EGamePhase is declared
    EarlyGame 0 < MidGame 1 < LateGame 2 < EndGame 3 < FoodCourt 4 in the shipped
    header, and the save stores exactly the four keys after EarlyGame. Ordering only
    carries the mapping if that block really is contiguous, so pin it -- a game patch
    that stores EGP_EarlyGame or skips a key must fail here rather than silently
    shift every phase number by one."""
    stored = list(state.projection["progression"]["phase_costs_remaining"])
    assert stored == ["EGP_MidGame", "EGP_LateGame", "EGP_EndGame", "EGP_FoodCourt"]
    phases = [WorldState.EGP_TO_PHASE[k] for k in stored]
    assert phases == [f"GP_Project_Assembly_Phase_{n}" for n in (1, 2, 3, 4)]


def test_an_unmapped_key_is_reported_rather_than_dropped(state):
    """EGP_Victory and EGP_EarlyGame exist in the enum and are not in the table. If a
    patch starts storing one, showing three rows where the save has four would be worse
    than showing a row labelled unmapped -- a silently missing phase reads as a phase
    with nothing outstanding."""
    st = _with(
        state,
        phase_costs_remaining={
            **state.projection["progression"]["phase_costs_remaining"],
            "EGP_Victory": {"Desc_SpaceElevatorPart_9_C": 7},
        },
    )
    rows = st.phase_requirements()["phases"]
    victory = next(r for r in rows if r["egp"] == "EGP_Victory")
    assert victory["phase"] is None
    assert victory["stale"] == "unmapped"
    assert victory["outstanding"] == {"Desc_SpaceElevatorPart_9_C": 7}


# ------------------------------------------------- the deprecated table is frozen


def test_a_completed_phase_still_bills_the_player_and_is_flagged_stale(state):
    """THE correction, and the reason this tool exists at all.

    mGamePhaseCosts is byte-identical across all 29 parseable saves of this world, 180 h
    to 316 h -- a span that includes the session where mCurrentGamePhase advanced
    Phase_2 -> Phase_3. Completing an entire Space Elevator phase moved nothing in it.
    So it still shows 500 Modular Engine and 100 Adaptive Control Unit outstanding on
    Phase 3, which the player has already delivered. Any planner that reads those
    amounts as work remaining plans the wrong factory.
    """
    req = state.phase_requirements()
    assert req["current_phase"] == "GP_Project_Assembly_Phase_3"

    phase3 = next(r for r in req["phases"] if r["phase"] == "GP_Project_Assembly_Phase_3")
    assert phase3["outstanding"] == {
        "Desc_SpaceElevatorPart_4_C": 500,  # Modular Engine
        "Desc_SpaceElevatorPart_5_C": 100,  # Adaptive Control Unit
    }
    # Not filtered away -- shown, and labelled as not to be believed.
    assert phase3["stale"] == "stale"


def test_the_untouched_target_row_is_the_only_one_marked_usable(state):
    """Frozen does not mean wrong for every row. Nothing has been delivered toward
    Phase 4 (mTargetGamePhasePaidOffCosts is absent, i.e. empty), so its snapshot has
    never had a chance to drift and still equals the full cost. That is a checkable
    condition, not an assumption, and it is the one row a planner may use."""
    req = state.phase_requirements()
    assert req["paid_off_target"] == {}
    trust = {r["phase"]: r["stale"] for r in req["phases"]}
    assert trust["GP_Project_Assembly_Phase_4"] == "usable"
    assert [p for p, t in trust.items() if t == "usable"] == ["GP_Project_Assembly_Phase_4"]


# ------------------------------------------- the target row after a delivery
#
# NONE of this is reachable from a real save on this machine and that is the whole reason
# these fixtures are built by hand: mTargetGamePhasePaidOffCosts is EMPTY in every one of
# the reference world's 29 parseable saves, so the subtraction below would otherwise
# ship untested and the question it answers -- "what does Phase 4 still need" on the day
# after the next elevator run -- is one the player asks constantly.


def test_a_delivery_into_the_target_is_subtracted_rather_than_invalidating_the_row(state):
    """THE correction for this feature. The row used to flip to stale on the first
    delivery and stay there, so the one question the tool exists for died permanently at
    the moment the player started answering it.

    The frozen snapshot of an untouched phase IS its full cost, and the live record says
    what has gone in, so the remainder is the subtraction of the two. Assembly Director
    System is settled here and Nuclear Pasta is a quarter paid.
    """
    st = _with(
        state,
        paid_off_target={
            "Desc_SpaceElevatorPart_7_C": 4000,  # the whole 4000, so it is done
            "Desc_SpaceElevatorPart_9_C": 250,  # 250 of 1000
        },
    )
    phase4 = next(
        r for r in st.phase_requirements()["phases"] if r["phase"] == "GP_Project_Assembly_Phase_4"
    )
    assert phase4["stale"] == "derived"
    assert phase4["outstanding"] == {
        "Desc_SpaceElevatorPart_6_C": 4000,
        "Desc_SpaceElevatorPart_8_C": 1000,
        "Desc_SpaceElevatorPart_9_C": 750,
    }
    # The settled item leaves the bill and joins the done list rather than lingering at 0.
    assert "Desc_SpaceElevatorPart_7_C" in phase4["complete"]
    assert phase4["snapshot"]["Desc_SpaceElevatorPart_7_C"] == 4000


def test_only_the_target_row_is_ever_subtracted(state):
    """Deliveries reach the target phase and nothing else -- PayOffOnTargetGamePhase --
    so no other row has a live counter to take off. Phase 3 keeps billing its frozen 500
    Modular Engine even while the live record shows Phase 4 deliveries."""
    st = _with(state, paid_off_target={"Desc_SpaceElevatorPart_4_C": 500})
    rows = {r["phase"]: r for r in st.phase_requirements()["phases"]}
    phase3 = rows["GP_Project_Assembly_Phase_3"]
    assert phase3["stale"] == "stale"
    assert phase3["outstanding"]["Desc_SpaceElevatorPart_4_C"] == 500
    assert phase3["paid_applied"] == {}


def test_a_snapshot_that_demonstrably_froze_after_a_delivery_is_not_subtracted(state):
    """The soundness condition, and it is checkable rather than assumed. A full cost has
    no item at zero in it, so EGP_EndGame's settled Versatile Framework proves that row
    froze after a delivery -- subtracting the live record from it would charge the same
    delivery twice. Point the target at Phase 3 and the answer is stale, not derived."""
    st = _with(
        state,
        target_phase="GP_Project_Assembly_Phase_3",
        paid_off_target={"Desc_SpaceElevatorPart_2_C": 2500},
    )
    phase3 = next(
        r for r in st.phase_requirements()["phases"] if r["phase"] == "GP_Project_Assembly_Phase_3"
    )
    assert phase3["stale"] == "stale"
    assert phase3["outstanding"] == {
        "Desc_SpaceElevatorPart_4_C": 500,
        "Desc_SpaceElevatorPart_5_C": 100,
    }


def test_paying_in_more_than_the_row_still_bills_for_is_the_other_proof(state):
    """The second half of the same check. Nothing can be delivered beyond a phase's cost,
    so a live record larger than the frozen remainder means the remainder is not the cost.
    4001 against a 4000 row is one unit over and enough."""
    st = _with(state, paid_off_target={"Desc_SpaceElevatorPart_7_C": 4001})
    phase4 = next(
        r for r in st.phase_requirements()["phases"] if r["phase"] == "GP_Project_Assembly_Phase_4"
    )
    assert phase4["stale"] == "stale"
    assert phase4["outstanding"]["Desc_SpaceElevatorPart_7_C"] == 4000


def test_the_tool_says_the_subtraction_happened_and_what_it_is_worth(state, monkeypatch):
    """A number whose provenance is not printed is a number the reader cannot weigh.
    `_state` is patched in the module that calls it -- patching app._state would leave
    the tool holding the original -- because this world cannot be reached from a save."""
    from satisfactory_mcp.interfaces.mcp.tools import progression as progression_tools

    st = _with(state, paid_off_target={"Desc_SpaceElevatorPart_7_C": 2500})
    monkeypatch.setattr(progression_tools, "_state", lambda save=None, world=None, as_of=None: st)
    out = progression_tools.phase_requirements()
    assert "\tderived\t" in out
    assert "1500 Assembly Director System" in out
    note = next(x for x in out.splitlines() if x.startswith("! stale=derived"))
    assert "Only the TARGET row is ever subtracted" in note
    assert "LOWER bound" in note


def test_a_fully_delivered_target_reads_as_nothing_left_to_see(state):
    """Every item covered, so the derived remainder is empty. It stays labelled derived
    rather than complete: derived is a LOWER bound, and if the snapshot had itself frozen
    after an earlier delivery there would still be something owed."""
    st = _with(
        state,
        paid_off_target={
            "Desc_SpaceElevatorPart_6_C": 4000,
            "Desc_SpaceElevatorPart_7_C": 4000,
            "Desc_SpaceElevatorPart_8_C": 1000,
            "Desc_SpaceElevatorPart_9_C": 1000,
        },
    )
    phase4 = next(
        r for r in st.phase_requirements()["phases"] if r["phase"] == "GP_Project_Assembly_Phase_4"
    )
    assert phase4["outstanding"] == {}
    assert phase4["stale"] == "derived"
    assert len(phase4["complete"]) == 4


def test_absent_paid_off_record_is_empty_not_missing(state):
    """UE omits empty SaveGame TArrays, so a projection with no paid_off_target at all
    means nothing delivered -- a real answer. Defaulting it to None and rendering a
    "-" would hide that Phase 4 is untouched, which is exactly the fact that makes its
    snapshot usable."""
    projection = copy.deepcopy(state.projection)
    projection["progression"].pop("paid_off_target", None)
    st = WorldState(projection=projection, game=state.game)
    assert st.phase_requirements()["paid_off_target"] == {}


# ------------------------------------------------------ what the tool prints about it


@pytest.fixture
def tool(state, monkeypatch):
    """`phase_requirements` answering about the committed projection.

    The tool reads the newest save on the machine, so judging its numbers against the
    frozen fixture means pointing it at the fixture -- the same trick the research tests
    use, for the same reason.
    """
    from satisfactory_mcp.interfaces.mcp.tools import progression as progression_tools

    monkeypatch.setattr(
        progression_tools, "_state", lambda save=None, world=None, as_of=None: state
    )
    return progression_tools.phase_requirements


def test_the_phase_table_says_what_is_held_and_what_is_missing(tool):
    """The join that was 150 lines away in the same file. "What is Phase 3 still short of"
    was one `stock.get` per row from being answerable, and the answer is not the
    outstanding column: this world holds 1200 Modular Engine against the 500 that row
    still bills for, and is short only the Adaptive Control Units.
    """
    out = tool()
    assert "items\thave\tshort by" in out
    row = next(r for r in out.splitlines() if r.startswith("GP_Project_Assembly_Phase_3"))
    assert "1200 Modular Engine + 10 Adaptive Control Unit" in row
    assert row.endswith("90 Adaptive Control Unit")


def test_an_item_none_of_which_is_held_is_left_out_of_have(tool):
    """Phase 4 is untouched and nothing it wants is in stock, so `have` is a dash rather
    than four zeroes: a column of zeroes is the same information spelled out at four times
    the width, in the column a reader scans for what they have got."""
    row = next(r for r in tool().splitlines() if r.startswith("GP_Project_Assembly_Phase_4"))
    columns = row.split("\t")
    assert columns[-2] == "-"
    assert columns[-1].startswith("4000 Assembly Director System")


def test_the_summary_answers_can_i_deliver_the_target_now(tool):
    """The question the tool exists for, answered in the header instead of left to the
    reader to compute across two columns."""
    assert "target_deliverable_now=no, short on 4 of 4 item(s)" in tool()


# ------------------------------------------------------------- phases and HUB tiers


def test_with_no_live_phase_an_all_zero_row_is_not_complete(state):
    """An old save carries the frozen per-phase record and no phase pointer. Its zeros are
    not deliveries, so nothing may be called complete on them."""
    projection = copy.deepcopy(state.projection)
    progression = projection["progression"]
    progression["game_phase"] = ""
    progression["target_phase"] = ""
    progression["phase_costs_remaining"] = {
        egp: dict.fromkeys(costs, 0) for egp, costs in progression["phase_costs_remaining"].items()
    }
    st = WorldState(projection=projection, game=state.game)
    assert all(r["stale"] != "complete" for r in st.phase_requirements()["phases"])


def test_each_delivered_phase_opens_two_hub_tiers_and_phase_4_the_last():
    from satisfactory_mcp.domain.progression.phases import opened_tier, opening_phase

    assert [opened_tier(f"GP_Project_Assembly_Phase_{n}") for n in range(6)] == [2, 4, 6, 8, 9, 9]
    assert opened_tier(None) is None and opened_tier("") is None
    assert [opening_phase(t) for t in range(1, 10)] == [0, 0, 1, 1, 2, 2, 3, 3, 4]

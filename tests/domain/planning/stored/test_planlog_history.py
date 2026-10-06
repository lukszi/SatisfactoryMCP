"""Undo, forget and restore: every way back is a new commit on top of the log.

docs/planner_slice_contract.md §5 is what these pin.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.stored.planlog import (
    Actor,
    AlreadyUndone,
    Forgotten,
    InvalidOp,
    NameTaken,
    Outdated,
    describe_commit,
    inverse,
)
from tests.support.plan_log import BOLTED, CHAT, PAGE, site_value


def test_undo_is_a_new_inverse_commit_and_merges_with_later_edits(plans, plan):
    plans.push(plan, 1, [{"op": "add", "field": "banned", "member": BOLTED}], actor=CHAT)
    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    pushed = plans.undo(plan, 2, 2, actor=PAGE)
    assert pushed.rev == 4 and plans.commits(plan)[-1].undoes == 2
    assert pushed.state.args.banned == [] and pushed.state.args.sloops == 4
    assert describe_commit(plans.commits(plan)[-1]) == f"v4 page: undo v2 (−banned {BOLTED})"

    with pytest.raises(AlreadyUndone) as caught:
        plans.undo(plan, 4, 2, actor=PAGE)
    assert caught.value.by == 4

    redo = plans.undo(plan, 4, 4, actor=PAGE)
    assert redo.state.args.banned == [BOLTED]
    again = plans.undo(plan, 5, 2, actor=PAGE)
    assert again.state.args.banned == []


def test_undoing_something_changed_again_since_is_outdated(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=CHAT)
    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 6}], actor=PAGE)
    with pytest.raises(Outdated):
        plans.undo(plan, 3, 2, actor=CHAT)


def test_undo_twice_walks_back_like_a_stack(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 6}], actor=PAGE)
    assert plans.undo(plan, 3, 3, actor=PAGE).state.args.sloops == 4
    assert plans.undo(plan, 4, 2, actor=PAGE).state.args.sloops == 0
    assert plans.undo(plan, 5, 5, actor=PAGE).state.args.sloops == 4
    assert plans.undo(plan, 6, 4, actor=PAGE).state.args.sloops == 6


def test_a_redone_commit_still_blocks_undoing_what_it_changed(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 6}], actor=CHAT)
    plans.undo(plan, 3, 3, actor=CHAT)
    plans.undo(plan, 4, 4, actor=CHAT)
    with pytest.raises(Outdated):
        plans.undo(plan, 5, 2, actor=PAGE)


def test_an_undone_pair_does_not_hide_a_later_change(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 6}], actor=PAGE)
    plans.undo(plan, 3, 3, actor=PAGE)
    plans.push(plan, 4, [{"op": "set", "field": "sloops", "value": 8}], actor=CHAT)
    with pytest.raises(Outdated):
        plans.undo(plan, 5, 2, actor=PAGE)


def test_undo_restores_the_exact_previous_value_of_every_kind(plans, plan):
    ops = [
        {"op": "set", "field": "target_item", "value": "Wire"},
        {"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 20},
        {"op": "put", "field": "supplied", "item": "Steel Beam", "value": 20},
        {"op": "del", "field": "export_minimums", "item": "Heavy Modular Frame"},
        {"op": "remove", "field": "exports", "member": "Heavy Modular Frame"},
        {"op": "add", "field": "extractor_clocks", "member": 2.5},
        {"op": "site", "value": site_value(3.0)},
        {"op": "rename", "name": "renamed"},
    ]
    before = plans.state(plan).to_dict()
    plans.push(plan, 1, ops, actor=PAGE)
    plans.undo(plan, 2, 2, actor=PAGE)
    after = plans.state(plan).to_dict()
    for d in (before, after):
        d.pop("rev")
    assert after == before


def test_v1_cannot_be_undone(plans, plan):
    with pytest.raises(InvalidOp):
        plans.undo(plan, 1, 1, actor=PAGE)


def test_inverse_of_a_record_is_nothing():
    assert inverse([{"op": "record", "field": "plan_id", "value": "x"}]) == []


def test_a_forgotten_plan_refuses_edits_until_the_forget_is_undone(plans, plan):
    plans.push(plan, 1, [{"op": "forget"}], actor=CHAT)
    assert plans.heads() == [] and plans.find("north hmf") is None
    assert plans.find("north hmf", include_forgotten=True).key == plan
    with pytest.raises(Forgotten) as caught:
        plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 1}], actor=PAGE)
    assert caught.value.rev == 2
    plans.undo(plan, 2, 2, actor=PAGE)
    assert plans.find("north hmf").key == plan


def test_a_forgotten_name_is_free_again(plans, plan):
    plans.push(plan, 1, [{"op": "forget"}], actor=CHAT)
    other = plans.create("north hmf", {}, actor=PAGE)
    assert plans.find("north hmf").key == other.key
    with pytest.raises(NameTaken):
        plans.undo(plan, 2, 2, actor=PAGE)
    pushed = plans.push(
        plan, 2, [{"op": "restore"}, {"op": "rename", "name": "north hmf 2"}], actor=PAGE
    )
    assert not pushed.state.forgotten and pushed.state.name == "north hmf 2"


def test_rename_refuses_a_live_name(plans, plan):
    plans.create("south", {}, actor=PAGE)
    with pytest.raises(NameTaken):
        plans.push(plan, 1, [{"op": "rename", "name": "SOUTH"}], actor=PAGE)


def test_restore_to_makes_the_head_equal_an_old_version(plans, plan):
    plans.push(
        plan,
        1,
        [
            {"op": "set", "field": "sloops", "value": 4},
            {"op": "add", "field": "banned", "member": "A"},
        ],
        actor=PAGE,
    )
    plans.push(
        plan,
        2,
        [{"op": "rename", "name": "renamed"}, {"op": "site", "value": site_value(1.0)}],
        actor=PAGE,
    )
    pushed = plans.restore_to(plan, 3, 1, actor=CHAT)
    got, want = pushed.state.to_dict(), plans.state(plan, 1).to_dict()
    got.pop("rev"), want.pop("rev")
    assert got == want
    assert plans.commits(plan)[-1].note == "restore v1"
    assert (plans.root / plan / "snap" / "4.json").is_file()


def test_push_at_head_needs_no_base(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    pushed = plans.push_at_head(
        plan, [{"op": "set", "field": "factory", "value": "coast"}], actor=Actor("system")
    )
    assert pushed.state.factory == "coast" and pushed.rev == 3

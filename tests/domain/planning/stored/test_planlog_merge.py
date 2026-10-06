"""Merge rule M1: disjoint edits on one base both land, real conflicts write nothing.

docs/planner_slice_contract.md §4 is what these pin.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.planlog import (
    Outdated,
    diff_args,
)
from tests.support.plan_log import BOLTED, CHAT, PAGE, head_bytes, site_value


def test_disjoint_edits_on_one_base_both_land(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    pushed = plans.push(plan, 1, [{"op": "add", "field": "banned", "member": BOLTED}], actor=CHAT)
    assert pushed.rev == 3 and pushed.merged_over == [2]
    assert pushed.state.args.sloops == 4 and pushed.state.args.banned == [BOLTED]
    assert pushed.text("north hmf") == (
        "merged onto v2 (you were on v1) -> now v3; others changed: v2 sloops 0→4 (page)"
    )
    assert plans.commits(plan)[-1].merged_over == [2]


def test_a_set_member_and_a_map_entry_merge_per_member(plans, plan):
    plans.push(
        plan,
        1,
        [
            {"op": "add", "field": "banned", "member": "A"},
            {"op": "put", "field": "supplied", "item": "Steel Beam", "value": 5},
        ],
        actor=PAGE,
    )
    pushed = plans.push(
        plan,
        1,
        [
            {"op": "add", "field": "banned", "member": "B"},
            {"op": "put", "field": "supplied", "item": "Wire", "value": 9},
        ],
        actor=CHAT,
    )
    assert pushed.state.args.banned == ["A", "B"]
    assert pushed.state.args.supplied == {"Steel Beam": 5.0, "Wire": 9.0}


def test_the_same_edit_on_both_sides_is_clean_and_dropped(plans, plan):
    plans.push(
        plan,
        1,
        [
            {"op": "add", "field": "banned", "member": BOLTED},
            {"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 15},
        ],
        actor=PAGE,
    )
    pushed = plans.push(
        plan,
        1,
        [
            {"op": "add", "field": "banned", "member": BOLTED},
            {"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 15.0},
        ],
        actor=CHAT,
    )
    assert pushed.noop and len(pushed.dropped) == 2 and plans.head_rev(plan) == 2


CONFLICTS = {
    "same scalar": (
        [{"op": "set", "field": "sloops", "value": 4}],
        [{"op": "set", "field": "sloops", "value": 2}],
    ),
    "same map entry": (
        [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 15}],
        [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 12}],
    ),
    "put against del": (
        [{"op": "del", "field": "export_minimums", "item": "Heavy Modular Frame"}],
        [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 12}],
    ),
    "add against remove": (
        [{"op": "remove", "field": "banned", "member": BOLTED}],
        [{"op": "add", "field": "banned", "member": BOLTED}],
    ),
    "required against banned": (
        [{"op": "add", "field": "banned", "member": BOLTED}],
        [{"op": "add", "field": "required", "member": BOLTED}],
    ),
    "banned against required": (
        [{"op": "add", "field": "required", "member": BOLTED}],
        [{"op": "add", "field": "banned", "member": BOLTED}],
    ),
    "site against site": (
        [{"op": "site", "value": site_value(1.0)}],
        [{"op": "site", "value": site_value(2.0)}],
    ),
    "rename against rename": (
        [{"op": "rename", "name": "south hmf"}],
        [{"op": "rename", "name": "east hmf"}],
    ),
    "anything against forget": (
        [{"op": "forget"}],
        [{"op": "set", "field": "notes", "value": "hi"}],
    ),
    "forget against anything": (
        [{"op": "set", "field": "notes", "value": "hi"}],
        [{"op": "forget"}],
    ),
}


@pytest.mark.parametrize("case", sorted(CONFLICTS))
def test_each_real_conflict_is_outdated_and_writes_nothing(plans, plan, case):
    theirs, mine = CONFLICTS[case]
    if case == "add against remove":
        plans.push(plan, 1, [{"op": "add", "field": "banned", "member": BOLTED}], actor=PAGE)
    base = plans.head_rev(plan)
    plans.push(plan, base, theirs, actor=PAGE)
    before = head_bytes(plans, plan)
    with pytest.raises(Outdated) as caught:
        plans.push(
            plan, base, mine + [{"op": "set", "field": "belt_ipm", "value": 480}], actor=CHAT
        )
    assert head_bytes(plans, plan) == before, "nothing is applied partially"
    out = caught.value
    assert out.head == base + 1 and out.base_rev == base
    assert [c.rev for c in out.since] == [base + 1]
    assert out.conflicts and out.state.rev == base + 1
    assert out.conflicts[0].theirs_rev == base + 1


def test_outdated_reads_as_the_chat_wording(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=PAGE)
    plans.push(
        plan,
        2,
        [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 15}],
        actor=PAGE,
    )
    with pytest.raises(Outdated) as caught:
        plans.push(
            plan,
            1,
            [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 12}],
            actor=CHAT,
        )
    lines = caught.value.text("north hmf").splitlines()
    assert lines == [
        '! outdated: plan "north hmf" is at v3; you wrote against v1. Nothing was applied.',
        "conflicts: rate Heavy Modular Frame: you 12, page set 15 in v3",
        "since v1: v2 page: sloops 0→4 · v3 page: rate Heavy Modular Frame 10→15/min",
        're-read (list_plans name="north hmf") and push again with base_rev=3',
    ]


def test_push_args_diffs_against_the_base_not_the_head(plans, plan):
    plans.push(
        plan, 1, [{"op": "add", "field": "sources", "member": "region:Grass Fields"}], actor=PAGE
    )
    pushed = plans.push_args(
        plan,
        1,
        {
            "objective": "min_machines",
            "exports": ["Heavy Modular Frame"],
            "export_minimums": {"Heavy Modular Frame": 10},
            "exclude_recipes": [BOLTED],
        },
        actor=CHAT,
    )
    assert pushed.state.args.sources == ["region:Grass Fields"]
    assert pushed.state.args.banned == [BOLTED]
    assert pushed.merged_over == [2]


def test_diff_args_compares_clocks_as_printed():
    assert diff_args({"clocks": [1, 2.5]}, {"clocks": [1.0, 2.5]}) == []
    ops = diff_args({"sources": ["a"], "sloops": 2}, {"sources": ["b"]})
    assert {"op": "add", "field": "sources", "member": "b"} in ops
    assert {"op": "remove", "field": "sources", "member": "a"} in ops
    assert {"op": "set", "field": "sloops", "value": 0} in ops
    assert diff_args({"sloops": 2}, {"sources": ["b"]}, partial=True) == [
        {"op": "add", "field": "sources", "member": "b"}
    ]


def test_a_stamp_lands_in_the_same_commit_and_a_bad_one_unstamps(plans, plan):
    pushed = plans.push(
        plan,
        1,
        [{"op": "set", "field": "sloops", "value": 1}],
        actor=PAGE,
        stamp=lambda s: {"plan_id": f"id{s.args.sloops}", "provenance": {"selectors": []}},
    )
    assert [op["op"] for op in pushed.applied] == ["set", "record", "record"]
    assert plans.state(plan).plan_id == "id1"

    def broken(_state):
        raise ValueError("bad selector")

    plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE, stamp=broken)
    assert plans.state(plan).plan_id == ""
    assert plans.state(plan).args.sloops == 2


def test_records_never_conflict(plans, plan):
    plans.push(
        plan,
        1,
        [{"op": "set", "field": "notes", "value": "a"}],
        actor=PAGE,
        stamp=lambda s: {"plan_id": "x"},
    )
    pushed = plans.push(
        plan,
        1,
        [{"op": "set", "field": "sloops", "value": 3}],
        actor=CHAT,
        stamp=lambda s: {"plan_id": "y"},
    )
    assert pushed.state.plan_id == "y"

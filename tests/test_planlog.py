"""The plan log: commits, snapshots, replay, merge rule M1, undo and the legacy migration.

docs/planner_slice_contract.md §3-§5 is what these pin. No game data is needed anywhere.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.planning import planlog
from satisfactory_mcp.domain.planning.planlog import (
    Actor,
    AlreadyUndone,
    BaseRevRequired,
    Forgotten,
    InvalidOp,
    NameTaken,
    Outdated,
    PlanArgs,
    PlanLog,
    UnknownPlan,
    describe_commit,
    describe_op,
    diff_args,
    inverse,
)

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
BOLTED = "Recipe_Alternate_BoltedFrame_C"


@pytest.fixture
def plans(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    return PlanLog("W")


@pytest.fixture
def plan(plans):
    return plans.create(
        "north hmf",
        {
            "objective": "min_machines",
            "exports": ["Heavy Modular Frame"],
            "export_minimums": {"Heavy Modular Frame": 10},
        },
        actor=CHAT,
    ).key


def _head_bytes(plans: PlanLog, key: str) -> bytes:
    return (plans.root / key / "ops.jsonl").read_bytes()


# ------------------------------------------------------------------ shape


def test_a_created_plan_is_v1_with_every_field_present(plans, plan):
    state = plans.state(plan)
    assert len(plan) == 8 and int(plan, 16) >= 0
    assert state.rev == 1 and plans.head_rev(plan) == 1
    assert state.name == "north hmf"
    assert state.args.export_minimums == {"Heavy Modular Frame": 10.0}
    assert state.args.allow_sinks is True and state.args.banned == []
    assert set(state.to_dict()) == {
        "key",
        "rev",
        "name",
        "forgotten",
        "notes",
        "factory",
        "created",
        "plan_id",
        "provenance",
        "siting",
        "args",
    }
    assert (plans.root / plan / "snap" / "1.json").is_file()


def test_kwargs_drop_defaults_and_spell_banned_as_exclude_recipes():
    args = PlanArgs.from_dict(
        {"objective": "max_mw", "banned": ["Recycled"], "sources": [], "logistics_items": ["x"]}
    )
    assert args.kwargs() == {"exclude_recipes": ["Recycled"]}
    assert PlanArgs.from_dict({"exclude_recipes": ["A"]}).banned == ["A"]


def test_a_second_live_plan_cannot_take_a_name_in_any_case(plans, plan):
    with pytest.raises(NameTaken):
        plans.create("NORTH HMF", {}, actor=PAGE)


@pytest.mark.parametrize(
    "op",
    [
        {"op": "set", "field": "sloops", "value": True},
        {"op": "set", "field": "sloops", "value": -1},
        {"op": "set", "field": "objective", "value": "most_fun"},
        {"op": "set", "field": "machine_cost_mw", "value": float("nan")},
        {"op": "set", "field": "banned", "value": []},
        {"op": "add", "field": "objective", "member": "x"},
        {"op": "put", "field": "sources", "item": "x", "value": 1},
        {"op": "put", "field": "export_minimums", "item": "x", "value": "fast"},
        {"op": "rename", "name": "  "},
        {"op": "restore"},
        {"op": "create", "name": "again", "state": {}},
        {"op": "teleport"},
    ],
)
def test_an_invalid_op_is_refused_and_nothing_is_written(plans, plan, op):
    before = _head_bytes(plans, plan)
    with pytest.raises(InvalidOp):
        plans.push(plan, 1, [op], actor=PAGE)
    assert _head_bytes(plans, plan) == before


def test_base_rev_must_name_a_version_that_exists(plans, plan):
    with pytest.raises(InvalidOp):
        plans.push(plan, 2, [{"op": "set", "field": "sloops", "value": 1}], actor=PAGE)
    with pytest.raises(InvalidOp):
        plans.push(plan, 0, [{"op": "set", "field": "sloops", "value": 1}], actor=PAGE)
    with pytest.raises(BaseRevRequired):
        plans.push_args(plan, None, {}, actor=CHAT)


def test_an_unknown_key_is_an_unknown_plan(plans, plan):
    with pytest.raises(UnknownPlan):
        plans.state("deadbeef")


def test_a_noop_appends_nothing(plans, plan):
    before = _head_bytes(plans, plan)
    pushed = plans.push(
        plan,
        1,
        [
            {"op": "set", "field": "objective", "value": "min_machines"},
            {"op": "add", "field": "exports", "member": "Heavy Modular Frame"},
            {"op": "remove", "field": "banned", "member": BOLTED},
            {"op": "del", "field": "supplied", "item": "Steel Beam"},
        ],
        actor=PAGE,
    )
    assert pushed.noop and pushed.rev == 1 and len(pushed.dropped) == 4
    assert _head_bytes(plans, plan) == before
    assert pushed.text("north hmf") == 'nothing changed: plan "north hmf" is still v1'


def test_was_is_filled_by_the_store_from_the_head(plans, plan):
    pushed = plans.push(
        plan,
        1,
        [
            {
                "op": "put",
                "field": "export_minimums",
                "item": "Heavy Modular Frame",
                "value": 15,
                "was": 999,
            }
        ],
        actor=PAGE,
    )
    assert pushed.applied == [
        {
            "op": "put",
            "field": "export_minimums",
            "item": "Heavy Modular Frame",
            "value": 15.0,
            "was": 10.0,
        }
    ]


# ---------------------------------------------------------- snapshots, replay


def _random_ops(rng: random.Random) -> list[dict]:
    pick = rng.randrange(8)
    if pick == 0:
        return [{"op": "set", "field": "sloops", "value": rng.randrange(6)}]
    if pick == 1:
        return [
            {
                "op": "put",
                "field": "export_minimums",
                "item": rng.choice("ABC"),
                "value": rng.randrange(1, 40),
            }
        ]
    if pick == 2:
        return [{"op": "del", "field": "export_minimums", "item": rng.choice("ABC")}]
    if pick == 3:
        return [
            {
                "op": rng.choice(["add", "remove"]),
                "field": "banned",
                "member": rng.choice(["R1", "R2", "R3"]),
            }
        ]
    if pick == 4:
        return [
            {
                "op": rng.choice(["add", "remove"]),
                "field": "clocks",
                "member": rng.choice([1, 1.5, 2.0]),
            }
        ]
    if pick == 5:
        return [{"op": "site", "value": rng.choice([None, {"x_m": rng.randrange(99)}])}]
    if pick == 6:
        return [{"op": "set", "field": "notes", "value": f"n{rng.randrange(5)}"}]
    return [{"op": "set", "field": "objective", "value": rng.choice(planlog.OBJECTIVES)}]


def test_replay_from_a_snapshot_equals_a_full_replay(plans, plan):
    rng = random.Random(7)
    while plans.head_rev(plan) < 130:
        plans.push(plan, plans.head_rev(plan), _random_ops(rng), actor=PAGE)
    snaps = plans.root / plan / "snap"
    assert sorted(int(p.stem) for p in snaps.glob("*.json")) == [1, 50, 100]
    with_snaps = [plans.state(plan, r).to_dict() for r in range(1, 131)]
    shutil.rmtree(snaps)
    full = [plans.state(plan, r).to_dict() for r in range(1, 131)]
    assert with_snaps == full


def test_a_corrupt_snapshot_falls_back_to_an_older_one(plans, plan):
    for n in range(60):
        plans.push(
            plan,
            plans.head_rev(plan),
            [{"op": "set", "field": "sloops", "value": n % 7 + 1}],
            actor=PAGE,
        )
    expected = plans.state(plan).to_dict()
    (plans.root / plan / "snap" / "50.json").write_text("{not json", encoding="utf-8")
    assert plans.state(plan).to_dict() == expected


def test_a_torn_final_line_is_ignored_and_then_truncated(plans, plan):
    ops = plans.root / plan / "ops.jsonl"
    with open(ops, "ab") as handle:
        handle.write(b'{"rev": 2, "base_rev": 1, "ops": [{"op"')
    assert plans.head_rev(plan) == 1
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    lines = ops.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 3
    assert [json.loads(line)["rev"] for line in lines[:-1]] == [1, 2]


# ------------------------------------------------------------------- merge


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
        [{"op": "site", "value": {"x_m": 1.0}}],
        [{"op": "site", "value": {"x_m": 2.0}}],
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
    before = _head_bytes(plans, plan)
    with pytest.raises(Outdated) as caught:
        plans.push(
            plan, base, mine + [{"op": "set", "field": "belt_ipm", "value": 480}], actor=CHAT
        )
    assert _head_bytes(plans, plan) == before, "nothing is applied partially"
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


# --------------------------------------------------------------------- undo


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


def test_undo_restores_the_exact_previous_value_of_every_kind(plans, plan):
    ops = [
        {"op": "set", "field": "target_item", "value": "Wire"},
        {"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 20},
        {"op": "put", "field": "supplied", "item": "Steel Beam", "value": 20},
        {"op": "del", "field": "export_minimums", "item": "Heavy Modular Frame"},
        {"op": "remove", "field": "exports", "member": "Heavy Modular Frame"},
        {"op": "add", "field": "extractor_clocks", "member": 2.5},
        {"op": "site", "value": {"x_m": 3.0}},
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


# -------------------------------------------------------------- lifecycle


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
        [{"op": "rename", "name": "renamed"}, {"op": "site", "value": {"x_m": 1.0}}],
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


# ------------------------------------------------------------------- words


def test_describe_op_uses_the_contract_words():
    assert (
        describe_op({"op": "set", "field": "objective", "was": "max_mw", "value": "min_machines"})
        == "objective max_mw→min_machines"
    )
    assert describe_op({"op": "set", "field": "notes", "was": "", "value": "x"}) == "notes changed"
    assert (
        describe_op(
            {"op": "put", "field": "export_minimums", "item": "HMF", "was": None, "value": 15.0}
        )
        == "+rate HMF 15/min"
    )
    assert describe_op({"op": "del", "field": "export_minimums", "item": "HMF"}) == "−rate HMF"
    assert (
        describe_op(
            {"op": "put", "field": "supplied", "item": "Steel Beam", "was": None, "value": 120.0}
        )
        == "supplied Steel Beam 0→120/min"
    )
    assert (
        describe_op({"op": "remove", "field": "sources", "member": "region:Grass Fields"})
        == "−sources region:Grass Fields"
    )
    assert describe_op({"op": "site", "was": None, "value": {"x": 1}}) == "site set"
    assert describe_op({"op": "site", "was": {"x": 0}, "value": {"x": 1}}) == "site moved"
    assert describe_op({"op": "site", "was": {"x": 0}, "value": None}) == "site cleared"
    assert describe_op({"op": "rename", "was": "a", "name": "b"}) == 'renamed "a"→"b"'
    assert describe_op({"op": "record", "field": "plan_id", "value": "x"}) == ""


def test_recipe_members_read_as_names_once_a_namer_is_set(monkeypatch):
    op = {"op": "add", "field": "banned", "member": "Recipe_UnpackageOilResidue_C"}
    assert describe_op(op) == "+banned Recipe_UnpackageOilResidue_C"
    monkeypatch.setattr(
        planlog, "_namer", [lambda: {"Recipe_UnpackageOilResidue_C": "Unpackage Heavy Oil Residue"}]
    )
    assert describe_op(op) == "+banned Unpackage Heavy Oil Residue"
    assert describe_op({**op, "member": "Recycled"}) == "+banned Recycled"
    assert describe_op({**op, "field": "sources", "member": "Recipe_UnpackageOilResidue_C"}) == (
        "+sources Recipe_UnpackageOilResidue_C"
    )


def test_actor_words():
    assert CHAT.display() == "Claude Code"
    assert Actor("chat", "claude-ai").display() == "Claude Desktop"
    assert Actor("chat", "cursor").display() == "cursor"
    assert Actor("chat").display() == "chat"
    assert Actor("migration").display() == "migrated"


# ---------------------------------------------------------------- migration


def _legacy(tmp_path: Path, world: str = "W") -> Path:
    path = tmp_path / "plans" / f"{world}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "version": 4,
                "world_id": world,
                "session_name": "s",
                "plans": [
                    {
                        "name": "spire",
                        "args": {
                            "objective": "max_mw",
                            "sources": ["box:1,2,3,4"],
                            "exclude_recipes": ["Recycled"],
                            "sloops": 2,
                            "retired_knob": 7,
                        },
                        "notes": "typed by hand",
                        "plan_id": "abc12345",
                        "factory": "coast",
                        "created": "x.sav",
                        "provenance": {"selectors": [{"selector": "box:1,2,3,4", "count": 3}]},
                        "siting": {"x_m": 1.0, "y_m": 2.0},
                    },
                    {
                        "name": "old",
                        "args": {"objective": "min_power"},
                        "notes": "",
                        "plan_id": "",
                        "factory": "",
                        "created": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_legacy_plans_migrate_once_without_losing_any(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()

    plans = PlanLog("W")
    marker = json.loads((plans.root / "migrated.json").read_text(encoding="utf-8"))
    assert marker["schema"] == 1 and marker["from"] == "W.json"
    assert sorted(marker["keys"]) == ["old", "spire"]

    spire = plans.find("spire")
    assert spire.rev == 1 and spire.key == marker["keys"]["spire"]
    assert spire.args.banned == ["Recycled"]
    assert spire.kwargs() == {
        "sources": ["box:1,2,3,4"],
        "exclude_recipes": ["Recycled"],
        "sloops": 2,
    }
    assert (spire.notes, spire.factory, spire.created, spire.plan_id) == (
        "typed by hand",
        "coast",
        "x.sav",
        "abc12345",
    )
    assert spire.provenance["selectors"][0]["count"] == 3
    assert spire.siting == {"x_m": 1.0, "y_m": 2.0}
    assert plans.commits(spire.key)[0].actor.kind == "migration"
    assert (plans.root / spire.key / "snap" / "1.json").is_file()
    assert legacy.read_bytes() == before, "the legacy file is never modified"

    view = plans.view()
    assert [p.name for p in view.plans] == ["spire", "old"]
    assert view.find("spi").key == spire.key and view.plans[0].rev == 1

    assert PlanLog("W").migrate() == {}
    assert len(PlanLog("W").keys()) == 2


def test_a_migration_interrupted_before_its_marker_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    _legacy(tmp_path)
    plans = PlanLog("W")
    (plans.root / "migrated.json").unlink()
    again = PlanLog("W")
    assert len(again.keys()) == 2
    assert (again.root / "migrated.json").is_file()


def test_no_legacy_file_means_nothing_to_migrate(plans):
    assert plans.migrate() == {} and not (plans.root / "migrated.json").exists()


def test_the_migration_backs_up_the_legacy_file_and_stamps_its_version(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    monkeypatch.setattr(planlog.schema, "writer_version", lambda: "9.8.7")
    legacy = _legacy(tmp_path)
    before = legacy.read_bytes()
    plans = PlanLog("W")
    backup = plans.root / "backup-v9.8.7" / "W.json"
    assert backup.read_bytes() == before
    marker = json.loads((plans.root / "migrated.json").read_text(encoding="utf-8"))
    assert marker["version"] == "9.8.7"


def test_a_legacy_file_from_a_newer_schema_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    legacy = _legacy(tmp_path)
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    legacy.write_text(json.dumps({**raw, "schema": planlog.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(planlog.schema.NewerSchema, match="newer version"):
        PlanLog("W")
    root = PlanLog.dir_for("W")
    assert not (root / "migrated.json").exists()
    assert not list(root.glob("*/ops.jsonl")) and not list(root.glob("backup-*"))


def test_a_log_migrated_by_a_newer_schema_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    _legacy(tmp_path)
    marker = PlanLog("W").root / "migrated.json"
    raw = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**raw, "schema": planlog.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(planlog.schema.NewerSchema):
        PlanLog("W")


def test_every_spelling_of_power_is_stored_as_mw(tmp_path, monkeypatch):
    """The migration once stored the solver's ``__MW__``, and the page's "+MW" then added
    a second power export beside it."""
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    legacy = _legacy(tmp_path)
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    raw["plans"][0]["args"]["exports"] = ["__MW__", "Plastic"]
    raw["plans"][0]["args"]["export_minimums"] = {"power": 50, "Plastic": 920}
    legacy.write_text(json.dumps(raw), encoding="utf-8")
    plans = PlanLog("W")
    spire = plans.find("spire")
    assert spire.args.exports == ["MW", "Plastic"]
    assert spire.args.export_minimums == {"MW": 50.0, "Plastic": 920.0}
    pushed = plans.push(
        spire.key, 1, [{"op": "add", "field": "exports", "member": "MW"}], actor=PAGE
    )
    assert pushed.noop and plans.find("spire").args.exports == ["MW", "Plastic"]
    plans.push(spire.key, 1, [{"op": "remove", "field": "exports", "member": "power"}], actor=PAGE)
    assert plans.find("spire").args.exports == ["Plastic"]
    assert all(planlog.is_power(x) for x in ("MW", "mw", " Power ", "__MW__"))
    assert not planlog.is_power("Plastic")


def test_an_older_log_that_stored_the_solver_spelling_replays_as_mw(plans):
    key = plans.create("rig", {"exports": ["Plastic"]}, actor=CHAT).key
    ops = plans._ops(key)
    lines = ops.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["ops"][0]["state"]["args"]["exports"] = ["__MW__", "Plastic"]
    ops.write_text(json.dumps(first) + "\n", encoding="utf-8")
    shutil.rmtree(plans.root / key / "snap")
    assert plans.state(key).args.exports == ["MW", "Plastic"]


def test_free_name_is_the_one_check_for_a_plan_name(plans, plan):
    assert plans.free_name("  coast  ") == "coast"
    assert plans.free_name("NORTH HMF", plan) == "NORTH HMF"
    with pytest.raises(NameTaken):
        plans.free_name("NORTH HMF")
    with pytest.raises(InvalidOp, match="cannot be blank"):
        plans.free_name("   ")


# ------------------------------------------------------------ two processes


_WRITER = """
import sys
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
key, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
log = PlanLog("W")
for i in range(n):
    log.push(key, 1, [{"op": "add", "field": "sources", "member": f"{tag}-{i}"}],
             actor=Actor("chat", tag))
"""


def test_two_processes_writing_at_once_lose_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    key = PlanLog("W").create("shared", {}, actor=PAGE).key
    env = {
        **os.environ,
        "SATISFACTORY_USER_DATA": str(tmp_path),
        "PYTHONPATH": str(Path(planlog.__file__).resolve().parents[3]),
    }
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, key, tag, "25"], env=env)
        for tag in ("a", "b")
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    log = PlanLog("W")
    assert log.head_rev(key) == 51
    assert [c.rev for c in log.commits(key)] == list(range(1, 52))
    assert sorted(log.state(key).args.sources) == sorted(
        [f"a-{i}" for i in range(25)] + [f"b-{i}" for i in range(25)]
    )


def test_a_large_rate_reads_as_a_number_not_an_exponent():
    op = {
        "op": "put",
        "field": "export_minimums",
        "item": "Plastic",
        "value": 9201000.0,
        "was": 2.5,
    }
    assert planlog.describe_op(op) == "rate Plastic 2.5→9,201,000/min"

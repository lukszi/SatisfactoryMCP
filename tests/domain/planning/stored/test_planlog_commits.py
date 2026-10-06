"""The plan log's commits: their shape, the refusals, snapshots, replay and their words.

docs/planner_slice_contract.md §3-§5 is what these pin. No game data is needed anywhere.
"""

from __future__ import annotations

import json
import random
import shutil

import pytest

from satisfactory_mcp.domain.planning.stored import planlog
from satisfactory_mcp.domain.planning.stored.planlog import (
    Actor,
    BaseRevRequired,
    InvalidOp,
    NameTaken,
    PlanArgs,
    UnknownPlan,
    describe_op,
)
from tests.support.plan_log import BOLTED, CHAT, PAGE, head_bytes, site_value

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
        "headroom_mw",
    }
    assert state.headroom_mw is None
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
    before = head_bytes(plans, plan)
    with pytest.raises(InvalidOp):
        plans.push(plan, 1, [op], actor=PAGE)
    assert head_bytes(plans, plan) == before


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
    before = head_bytes(plans, plan)
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
    assert head_bytes(plans, plan) == before
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
        return [{"op": "site", "value": rng.choice([None, site_value(rng.randrange(99))])}]
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


def test_factory_takes_a_name_or_a_sentinel_and_says_so(plans, plan):
    for value, words in (
        ("oil setup", "count as built: oil setup"),
        ("/world", "count as built: whole world"),
        ("/none", "count as built: nothing yet"),
        ("", "count as built: found automatically"),
    ):
        state = plans.state(plan)
        pushed = plans.push(
            plan, state.rev, [{"op": "set", "field": "factory", "value": value}], actor=PAGE
        )
        assert pushed.state.factory == value
        assert describe_op({"op": "set", "field": "factory", "was": "x", "value": value}) == words
    with pytest.raises(InvalidOp):
        plans.push(
            plan,
            plans.state(plan).rev,
            [{"op": "set", "field": "factory", "value": "/all"}],
            actor=PAGE,
        )


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


def test_a_large_rate_reads_as_a_number_not_an_exponent():
    op = {
        "op": "put",
        "field": "export_minimums",
        "item": "Plastic",
        "value": 9201000.0,
        "was": 2.5,
    }
    assert planlog.describe_op(op) == "rate Plastic 2.5→9,201,000/min"

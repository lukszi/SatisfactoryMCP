"""The startup headroom a plan stores: its bounds, merges, undo and words (P4)."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.planning.stored.plan_args import InvalidOp
from satisfactory_mcp.domain.planning.stored.planlog import Outdated, describe_op, inverse
from tests.support.plan_log import CHAT, PAGE


def _headroom(value):
    return {"op": "set", "field": "headroom_mw", "value": value}


@pytest.mark.parametrize("value", [0, -1, 1_000_001, "2000", True, float("inf")])
def test_headroom_must_be_a_positive_number_up_to_a_million(plans, plan, value):
    with pytest.raises(InvalidOp, match="headroom_mw"):
        plans.push(plan, 1, [_headroom(value)], actor=PAGE)
    assert plans.head_rev(plan) == 1


def test_headroom_is_a_plan_scalar_outside_plan_id(plans, plan):
    before = plans.state(plan)
    pushed = plans.push(plan, 1, [_headroom(2000)], actor=PAGE, stamp=lambda s: {"plan_id": "x"})
    assert pushed.state.headroom_mw == 2000.0 and pushed.state.args == before.args
    assert pushed.applied[0] == {**_headroom(2000.0), "was": None}
    assert plans.push(plan, 2, [_headroom(1_000_000)], actor=PAGE).state.headroom_mw == 1e6
    cleared = plans.push(plan, 3, [_headroom(None)], actor=PAGE)
    assert cleared.state.headroom_mw is None
    assert "headroom_mw" not in plans.state(plan).kwargs()


def test_headroom_conflicts_only_with_itself(plans, plan):
    plans.push(plan, 1, [_headroom(2000)], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.push(plan, 1, [_headroom(3000)], actor=PAGE)
    assert caught.value.conflicts[0].key == "headroom_mw"
    assert caught.value.conflicts[0].text().startswith("startup headroom: you 3,000 MW, ")
    assert "set 2,000 MW in v2" in caught.value.conflicts[0].text()
    assert plans.push(plan, 1, [_headroom(2000)], actor=PAGE).noop
    merged = plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 2}], actor=PAGE)
    assert merged.state.headroom_mw == 2000.0 and merged.state.args.sloops == 2


def test_headroom_undoes_to_its_previous_value(plans, plan):
    plans.push(plan, 1, [_headroom(2000)], actor=PAGE)
    plans.push(plan, 2, [_headroom(500)], actor=PAGE)
    assert inverse(plans.commits(plan)[2].ops) == [_headroom(2000.0)]
    assert plans.undo(plan, 3, 3, actor=PAGE).state.headroom_mw == 2000.0
    restored = plans.restore_to(plan, 4, 1, actor=PAGE)
    assert restored.state.headroom_mw is None
    assert plans.restore_to(plan, 5, 3, actor=PAGE).state.headroom_mw == 500.0


def test_measured_then_given_then_two_undos_lands_on_the_default(plans, plan):
    plans.push(plan, 1, [_headroom(6370)], actor=PAGE)
    plans.push(plan, 2, [_headroom(2000)], actor=PAGE)
    assert plans.undo(plan, 3, 3, actor=PAGE).state.headroom_mw == 6370.0
    assert plans.undo(plan, 4, 2, actor=PAGE).state.headroom_mw is None


def test_a_refused_undo_back_to_the_default_names_it_not_none(plans, plan):
    plans.push(plan, 1, [_headroom(500)], actor=PAGE)
    plans.push(plan, 2, [_headroom(700)], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.undo(plan, 3, 2, actor=PAGE)
    text = caught.value.conflicts[0].text()
    assert text.startswith("startup headroom: you save default, ") and "none" not in text


def test_headroom_in_words():
    assert describe_op({**_headroom(2000.0), "was": None}) == "startup headroom 2,000 MW"
    assert describe_op({**_headroom(None), "was": 2000.0}) == "startup headroom: save default"
    assert describe_op({**_headroom(1234.5), "was": None}) == "startup headroom 1,234.5 MW"


def test_an_older_snapshot_without_headroom_reads_none(plans, plan):
    snap = plans.root / plan / "snap" / "1.json"
    raw = json.loads(snap.read_text(encoding="utf-8"))
    del raw["state"]["headroom_mw"]
    snap.write_text(json.dumps(raw), encoding="utf-8")
    assert plans.state(plan).headroom_mw is None
    raw["state"]["headroom_mw"] = "junk"
    snap.write_text(json.dumps(raw), encoding="utf-8")
    assert plans.state(plan, 1).headroom_mw is None


def test_a_cleared_headroom_reads_save_default_in_a_conflict(plans, plan):
    plans.push(plan, 1, [_headroom(400)], actor=CHAT)
    plans.push(plan, 2, [_headroom(500)], actor=CHAT)
    with pytest.raises(Outdated) as caught:
        plans.push(plan, 2, [_headroom(None)], actor=PAGE)
    assert caught.value.conflicts[0].text().startswith("startup headroom: you save default, ")


def test_a_boolean_is_named_in_json_words(plans, plan):
    with pytest.raises(InvalidOp, match="must be a number, not true"):
        plans.push(plan, 1, [_headroom(True)], actor=PAGE)

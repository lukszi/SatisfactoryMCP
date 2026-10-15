"""Graph node positions in the plan log: written, read back, reset, merged and undone.

docs/planner_slice_contract.md §3 (``place``, ``layout``) is what these pin.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.stored.plan_args import InvalidOp
from satisfactory_mcp.domain.planning.stored.planlog import PlanLog
from tests.support.plan_log import CHAT, PAGE

ORE = "p:x:Build_MinerMk2_C:Desc_OreIron_C"
PLATE = "p:Recipe_IronPlate_C"


def _place(node: str, x: float | None, y: float = 0.0) -> dict:
    return {"op": "place", "node": node, "value": None if x is None else [x, y]}


def test_a_place_is_stored_read_back_and_worded(plans, plan):
    before = plans.state(plan)
    pushed = plans.push(plan, 1, [_place(ORE, 10, 20), _place(PLATE, -5.5)], actor=PAGE)
    assert pushed.rev == 2
    assert plans.state(plan).node_positions == {ORE: [10.0, 20.0], PLATE: [-5.5, 0.0]}
    assert PlanLog("W").state(plan).node_positions == {ORE: [10.0, 20.0], PLATE: [-5.5, 0.0]}
    assert plans.state(plan, 1).node_positions == {}
    assert plans.commits(plan)[-1].text() == "v2 page: layout: 2 nodes moved"
    assert pushed.state.kwargs() == before.kwargs()
    assert pushed.state.plan_id == before.plan_id and pushed.state.args == before.args


def test_the_same_place_again_is_a_noop_and_none_forgets_a_node(plans, plan):
    plans.push(plan, 1, [_place(ORE, 1, 2)], actor=PAGE)
    assert plans.push(plan, 2, [_place(ORE, 1, 2)], actor=PAGE).noop
    pushed = plans.push(plan, 2, [_place(ORE, None)], actor=PAGE)
    assert pushed.rev == 3 and pushed.state.node_positions == {}


def test_a_reset_clears_the_layout_and_undo_brings_it_back(plans, plan):
    plans.push(plan, 1, [_place(ORE, 1, 2), _place(PLATE, 3, 4)], actor=PAGE)
    reset = plans.push(plan, 2, [{"op": "reset_layout"}], actor=PAGE)
    assert reset.state.node_positions == {}
    assert plans.commits(plan)[-1].ops[0]["op"] == "layout"
    assert plans.commits(plan)[-1].text() == "v3 page: layout reset"
    undone = plans.undo(plan, 3, 3, actor=PAGE)
    assert undone.state.node_positions == {ORE: [1.0, 2.0], PLATE: [3.0, 4.0]}


def test_moves_on_one_base_merge_and_never_conflict(plans, plan):
    plans.push(plan, 1, [{"op": "set", "field": "sloops", "value": 4}], actor=CHAT)
    plans.push(plan, 1, [_place(ORE, 1, 1)], actor=PAGE)
    pushed = plans.push(plan, 1, [_place(ORE, 9, 9), _place(PLATE, 2, 2)], actor=PAGE)
    assert pushed.rev == 4 and pushed.merged_over == [2, 3]
    assert pushed.state.node_positions == {ORE: [9.0, 9.0], PLATE: [2.0, 2.0]}
    assert pushed.state.args.sloops == 4


def test_a_restore_takes_the_layout_back_too(plans, plan):
    plans.push(plan, 1, [_place(ORE, 1, 1)], actor=PAGE)
    plans.push(plan, 2, [_place(ORE, 5, 5)], actor=PAGE)
    restored = plans.restore_to(plan, 3, 2, actor=PAGE)
    assert restored.state.node_positions == {ORE: [1.0, 1.0]}


@pytest.mark.parametrize(
    "op",
    [
        {"op": "place", "node": "", "value": [0, 0]},
        {"op": "place", "node": ORE, "value": [0]},
        {"op": "place", "node": ORE, "value": [0, float("inf")]},
        {"op": "place", "node": ORE, "value": [True, 0]},
        {"op": "layout", "value": [1, 2]},
    ],
)
def test_a_bad_position_is_refused(plans, plan, op):
    with pytest.raises(InvalidOp):
        plans.push(plan, 1, [op], actor=PAGE)
    assert plans.head_rev(plan) == 1

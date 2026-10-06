"""The alternates drawer's arithmetic (docs/planner-p3_contract.md §5.3): ops, deltas and order."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.analysis import swaps
from satisfactory_mcp.domain.planning.readout import summary
from satisfactory_mcp.domain.planning.stored import manage
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanArgs, PlanLog

WORLD = "X2faPVKjX06VaRzClNv5KQ"
CHAT = Actor("chat", "claude-code", 4242)
RIP = "Reinforced Iron Plate"
ITEM = "Desc_IronPlateReinforced_C"
HMF = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
STANDARD = "Recipe_IronPlateReinforced_C"
BOLTED = "Recipe_Alternate_ReinforcedIronPlate_1_C"
STITCHED = "Recipe_Alternate_ReinforcedIronPlate_2_C"
ADHERED = "Recipe_Alternate_AdheredIronPlate_C"


@pytest.fixture
def plans():
    return PlanLog(WORLD)


def _state(plans, args):
    made = plans.create("rip", args, actor=CHAT)
    return plans.state(made.key)


def _by_id(result):
    return {o["recipe_id"]: o for o in result["options"]}


def test_require_drops_other_required_for_the_item_and_frees_a_literal_ban(state, game, plans):
    args = {**HMF, "required": [BOLTED], "banned": [STANDARD]}
    head = _state(plans, args)
    options = _by_id(swaps.swap_deltas(game, state, head, ITEM))
    standard = options[STANDARD]
    assert standard["require_ops"] == [
        {"op": "add", "field": "required", "member": STANDARD},
        {"op": "remove", "field": "required", "member": BOLTED},
        {"op": "remove", "field": "banned", "member": STANDARD},
    ]
    assert standard["ban_ops"] == [{"op": "add", "field": "banned", "member": STANDARD}]
    assert standard["free_ops"] == [{"op": "remove", "field": "banned", "member": STANDARD}]
    assert standard["status"] == "banned" and standard["banned_by"] == STANDARD
    bolted = options[BOLTED]
    assert bolted["status"] == "required" and bolted["required"]
    assert bolted["ban_ops"] == [
        {"op": "add", "field": "banned", "member": BOLTED},
        {"op": "remove", "field": "required", "member": BOLTED},
    ]
    assert bolted["free_ops"] == [{"op": "remove", "field": "required", "member": BOLTED}]


def test_the_delta_is_result_delta_of_the_two_solves(state, game, plans):
    head = _state(plans, HMF)
    option = _by_id(swaps.swap_deltas(game, state, head, ITEM))[STANDARD]
    before = summary.solve_summary(game, state, head.kwargs())
    raw = head.args.to_dict()
    raw["required"] = [STANDARD]
    after = summary.solve_summary(game, state, PlanArgs.from_dict(raw).kwargs())
    assert option["delta"] == manage.result_delta(before, after)
    assert option["solved"] and option["delta"]["comparable"]
    in_use = next(o for o in swaps.swap_deltas(game, state, head, ITEM)["options"] if o["in_use"])
    assert in_use["delta"]["text"] == "no change in the result" and in_use["delta"]["machines"] == 0


def test_a_pattern_ban_is_not_solved_and_offers_no_ban_or_free(state, game, plans):
    head = _state(plans, {**HMF, "banned": ["Stitched"]})
    option = _by_id(swaps.swap_deltas(game, state, head, ITEM))[STITCHED]
    assert option["status"] == "banned" and option["banned_by"] == "Stitched"
    assert option["solved"] is False and option["delta"] is None
    assert option["ban_ops"] == [] and option["free_ops"] == []


def test_an_exact_name_ban_does_not_ban_recipes_whose_names_contain_it(state, game, plans):
    head = _state(plans, {**HMF, "banned": ["Rubber"]})
    options = _by_id(swaps.swap_deltas(game, state, head, "Desc_Rubber_C"))
    assert options["Recipe_Rubber_C"]["banned_by"] == "Rubber"
    for rid in ("Recipe_ResidualRubber_C", "Recipe_Alternate_RecycledRubber_C"):
        assert options[rid]["banned"] is False and options[rid]["solved"] is True
        assert options[rid]["delta"] is not None and options[rid]["ban_ops"]
    made = plans.create("plate", {**HMF, "banned": ["Iron Plate"]}, actor=CHAT)
    plate = _by_id(swaps.swap_deltas(game, state, plans.state(made.key), "Desc_IronPlate_C"))
    assert plate["Recipe_Alternate_CoatedIronPlate_C"]["banned"] is False


def test_a_pattern_wins_over_a_literal_ban_of_the_same_recipe(state, game, plans):
    recycled = "Recipe_Alternate_RecycledRubber_C"
    head = _state(plans, {**HMF, "banned": [recycled, "Recycled"]})
    option = _by_id(swaps.swap_deltas(game, state, head, "Desc_Rubber_C"))[recycled]
    assert option["banned_by"] == "Recycled" and option["solved"] is False
    assert option["ban_ops"] == [] and option["free_ops"] == []


def test_locked_recipes_are_listed_as_spoilers_or_hidden(state, game, plans):
    head = _state(plans, HMF)
    shown = swaps.swap_deltas(game, state, head, ITEM)
    locked = _by_id(shown)[ADHERED]
    assert locked["status"] == "locked" and locked["spoiler"] and locked["unlocked"] is False
    assert locked["delta"] is None and locked["granted_by"]
    hidden = swaps.swap_deltas(game, state, head, ITEM, spoilers=False)
    assert ADHERED not in _by_id(hidden) and hidden["hidden"] == 1
    assert hidden["text"].endswith("(1 in use, 1 locked hidden)")
    assert shown["text"] == f"recipes for {RIP} in “rip” v1: 4 (1 in use, 1 locked)"


def test_options_are_ordered_by_status_never_by_delta(state, game, plans):
    head = _state(plans, {**HMF, "banned": [STITCHED]})
    result = swaps.swap_deltas(game, state, head, ITEM)
    order = [o["status"] for o in result["options"]]
    assert order == sorted(order, key=swaps.STATUS_ORDER.index)
    available = [o for o in result["options"] if o["status"] == "available"]
    assert [o["alternate"] for o in available] == sorted(o["alternate"] for o in available)
    assert result["head_feasible"] and result["head_machines"] > 0
    assert result["key"] == head.key and result["rev"] == 1 and result["item"] == ITEM


def test_makers_are_first_product_recipes_else_every_maker(game):
    from satisfactory_mcp.core.gamedata import search

    assert {r.cls for r in swaps.primary_makers(game, ITEM)} == {
        STANDARD,
        BOLTED,
        STITCHED,
        ADHERED,
    }
    byproduct = next(
        item
        for item in sorted(game.items)
        if (found := search.makers_of(game, item))
        and all(r.products[0].item != item for r in found)
    )
    assert swaps.primary_makers(game, byproduct) == search.makers_of(game, byproduct)
    mixed = next(
        item
        for item in sorted(game.items)
        if len({r.products[0].item == item for r in search.makers_of(game, item)}) == 2
    )
    assert all(r.products[0].item == mixed for r in swaps.primary_makers(game, mixed))

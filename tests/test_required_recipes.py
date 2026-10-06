"""``required`` recipes (G4): one recipe makes its item and every other recipe for it is out.

docs/planner_slice_contract.md §6. Built on the fixture save, so the recipes are real.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from satisfactory_mcp.domain.planning.solver.prepare import prepare
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario

pytestmark = pytest.mark.integration


def _main(game, rid):
    products = game.recipes[rid].products
    return products[0].item if products else None


@pytest.fixture(scope="module")
def rivals(game, state):
    """An item two or more unlocked part recipes make as their main product."""
    by_item = defaultdict(list)
    for recipe in state.unlocked_recipes("part"):
        by_item[_main(game, recipe.cls)].append(recipe.cls)
    item, rids = next((i, r) for i, r in sorted(by_item.items(), key=str) if i and len(r) >= 2)
    return item, rids


def test_a_required_recipe_excludes_every_other_recipe_for_its_item(game, state, rivals):
    _item, rids = rivals
    chosen, *others = rids
    request = build_scenario(game, state, required=[chosen])
    assert request.required == [chosen]
    assert chosen in request.scenario.recipes
    assert not set(others) & set(request.scenario.recipes)
    assert request.recipe_errors == []


def test_a_recipe_making_the_item_as_a_byproduct_is_untouched(game, state, rivals):
    item, rids = rivals
    request = build_scenario(game, state, required=[rids[0]])
    side = [
        r.cls
        for r in state.unlocked_recipes("part")
        if _main(game, r.cls) != item and any(f.item == item for f in r.products[1:])
    ]
    assert set(side) <= set(request.scenario.recipes)


def test_an_exact_display_name_is_accepted(game, state, rivals):
    _item, rids = rivals
    name = game.recipes[rids[0]].name
    named = [r for r in game.recipes.values() if r.name.casefold() == name.casefold()]
    if len(named) != 1:
        pytest.skip("that display name is shared")
    assert build_scenario(game, state, required=[name]).required == [rids[0]]


def test_two_required_recipes_for_one_item_both_stay(game, state, rivals):
    _item, rids = rivals
    request = build_scenario(game, state, required=rids[:2])
    assert set(rids[:2]) <= set(request.scenario.recipes)


def test_unknown_locked_and_banned_entries_are_refused_by_name(game, state, rivals):
    _item, rids = rivals
    unlocked = {r.cls for r in state.unlocked_recipes("part")}
    locked = next(
        rid for rid, r in game.recipes.items() if r.kind == "part" and rid not in unlocked
    )
    name = game.recipes[rids[0]].name
    request = build_scenario(
        game,
        state,
        required=["Recipe_NoSuchThing_C", locked, rids[0]],
        exclude_recipes=[name],
    )
    assert request.required == []
    errors = " | ".join(request.recipe_errors)
    assert "'Recipe_NoSuchThing_C' is not a recipe" in errors
    assert f"{game.recipes[locked].name!r} is not unlocked in this save" in errors
    assert f"required {name!r} is banned by {name!r}" in errors


def test_required_enters_the_plan_id_only_when_it_is_used(game, state, rivals):
    _item, rids = rivals
    assert build_scenario(game, state).plan_id == build_scenario(game, state, required=[]).plan_id
    assert (
        build_scenario(game, state).plan_id
        != build_scenario(game, state, required=rids[:1]).plan_id
    )


def test_an_infeasible_plan_under_required_says_which_are_in_force(game, state, rivals):
    item, rids = rivals
    prepared = prepare(
        game,
        state,
        {
            "objective": "min_machines",
            "exports": [item],
            "export_minimums": {item: 1e9},
            "required": rids[:1],
        },
        diagnose=False,
    )
    assert prepared.failure is not None
    names = game.recipes[rids[0]].name
    assert f"required in force: {names} -- removing one may make this feasible" in (
        prepared.failure.notes
    )

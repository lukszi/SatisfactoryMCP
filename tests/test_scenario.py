"""The shared scenario construction path, and recipe allow/deny.

build_scenario is the single translation from tool arguments to a solvable Scenario.
Everything here pins behaviour that, if it broke, would let two tools describe
different factories for the same arguments -- the exact failure the module exists to
prevent.
"""

from __future__ import annotations

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp.domain.planning.solver.optimize import MW, solve
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario, match_recipes

pytestmark = pytest.mark.integration

RECYCLED_PLASTIC = "Recipe_Alternate_Plastic_1_C"
RECYCLED_RUBBER = "Recipe_Alternate_RecycledRubber_C"


@pytest.fixture(scope="module")
def pool(state):
    return [r.cls for r in state.unlocked_recipes("part")]


# ------------------------------------------------------------- matching


def test_class_id_matches_exactly(game, pool):
    assert match_recipes(game, RECYCLED_PLASTIC, pool) == [RECYCLED_PLASTIC]


def test_display_name_matches_exactly(game, pool):
    assert match_recipes(game, "Alternate: Recycled Plastic", pool) == [RECYCLED_PLASTIC]


def test_substring_matches_every_recipe_in_a_loop(game, pool):
    """Banning half a two-recipe loop leaves the loop intact and the ban useless, so
    a substring must take all matches, not the first."""
    hits = set(match_recipes(game, "Recycled", pool))
    assert hits == {RECYCLED_PLASTIC, RECYCLED_RUBBER}


def test_matching_is_case_insensitive(game, pool):
    assert set(match_recipes(game, "recycled", pool)) == set(match_recipes(game, "RECYCLED", pool))


def test_no_match_returns_empty_rather_than_guessing(game, pool):
    assert match_recipes(game, "Nonexistent Recipe", pool) == []


# ------------------------------------------------------------ exclusion


def test_excluded_recipes_leave_the_scenario(game, state):
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD), exclude_recipes=["Recycled"])
    assert RECYCLED_PLASTIC not in req.scenario.recipes
    assert RECYCLED_RUBBER not in req.scenario.recipes
    assert set(req.excluded) == {"Alternate: Recycled Plastic", "Alternate: Recycled Rubber"}
    assert not req.recipe_errors


def test_a_ban_that_matches_nothing_is_reported_never_ignored(game, state):
    """A silently dropped ban returns a plan happily using the recipe the user meant
    to forbid -- worse than refusing, because it looks like compliance."""
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD), exclude_recipes=["Nope"])
    assert req.recipe_errors
    assert "Nope" in req.recipe_errors[0]
    assert req.excluded == []


def test_exclusion_actually_changes_the_solve(game, state):
    """The point of the feature: the banned route must not come back."""
    kwargs = dict(
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW", "Desc_Plastic_C", "Desc_Rubber_C"],
        export_minimums={"Desc_Plastic_C": 300.0, "Desc_Rubber_C": 300.0},
    )
    with_loop = solve(build_scenario(game, state, **kwargs).scenario)
    without = solve(build_scenario(game, state, exclude_recipes=["Recycled"], **kwargs).scenario)
    assert with_loop.ok and without.ok
    used = {p["recipe"] for p in without.processes}
    assert RECYCLED_PLASTIC not in used
    assert RECYCLED_RUBBER not in used
    # Banning a route the solver wanted must cost something, or the ban was a no-op.
    assert without.net_mw < with_loop.net_mw


def test_an_exact_name_bans_only_that_recipe(game, state):
    """Exact match short-circuits the substring pass, and must: "Plastic" is the
    literal name of Recipe_Plastic_C, so without this rule there would be no way to
    target a single recipe whose name is a substring of others."""
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD), exclude_recipes=["Plastic"])
    assert req.excluded == ["Plastic"]
    assert "Recipe_Plastic_C" not in req.scenario.recipes
    # The family survives, because the user named one recipe exactly.
    assert RECYCLED_PLASTIC in req.scenario.recipes


def test_excluding_every_producer_makes_the_target_unreachable(game, state):
    """Better to return nothing than to quietly return a plan that ignores the ban."""
    producers = [r.cls for r in game.producers_of("Desc_Plastic_C", "part")]
    req = build_scenario(
        game,
        state,
        objective="max_item",
        target_item="Desc_Plastic_C",
        exports=["Desc_Plastic_C"],
        sources=list(REFERENCE_FIELD),
        exclude_recipes=producers,
    )
    assert not (set(producers) & set(req.scenario.recipes))
    sol = solve(req.scenario)
    assert sol.objective_value == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------- whitelist


def test_only_recipes_restricts_to_the_named_set(game, state):
    req = build_scenario(
        game, state, sources=list(REFERENCE_FIELD), only_recipes=["Heavy Oil Residue", "Diluted"]
    )
    names = {game.recipes[r].name for r in req.scenario.recipes}
    assert names
    assert all("Heavy Oil Residue" in n or "Diluted" in n for n in names), names


def test_only_recipes_then_exclude_applies_in_order(game, state):
    """exclude narrows whatever only_recipes kept, so the two compose."""
    req = build_scenario(
        game,
        state,
        sources=list(REFERENCE_FIELD),
        only_recipes=["Diluted"],
        exclude_recipes=["Packaged"],
    )
    names = {game.recipes[r].name for r in req.scenario.recipes}
    assert "Alternate: Diluted Fuel" in names
    assert "Alternate: Diluted Packaged Fuel" not in names


# ---------------------------------------------------------------- exports


def test_an_unknown_export_is_named_rather_than_returning_infeasible(game, state):
    """THE correction. An unresolvable token used to become the raw string, entering
    the LP as an item no process makes and no balance row can satisfy -- so the plan
    came back as a bare INFEASIBLE with nothing pointing at the typo."""
    req = build_scenario(game, state, sources=list(REFERENCE_FIELD), exports=["Plastik"])
    assert req.export_errors
    assert "Plastik" in req.export_errors[0]
    assert "Plastik" not in req.scenario.exports


def test_an_unknown_export_minimum_is_named_too(game, state):
    """A floor keyed to a nonexistent item is unsatisfiable by construction, which is
    the same bare INFEASIBLE one step further along."""
    req = build_scenario(
        game, state, sources=list(REFERENCE_FIELD), export_minimums={"Plastik": 300.0}
    )
    assert req.export_errors
    assert "export_minimums" in req.export_errors[0]
    assert not req.scenario.export_minimums


def test_a_minimum_on_power_resolves_to_the_power_pseudo_item(game, state):
    """Aliases have to apply on both sides. A minimum keyed "MW" that never matched
    __MW__ was a floor the LP silently ignored."""
    req = build_scenario(
        game, state, sources=list(REFERENCE_FIELD), exports=["MW"], export_minimums={"MW": 500.0}
    )
    assert req.scenario.export_minimums == {MW: 500.0}
    assert not req.export_errors


def test_exports_replace_the_default_and_that_is_load_bearing(game, state):
    """Kept as replace, not extend, for a measurable reason: exporting MW also forbids
    drawing from the existing grid, so silently appending it would turn every item
    plan into a self-powered one -- a different question from the one asked."""
    items_only = build_scenario(game, state, sources=list(REFERENCE_FIELD), exports=["Plastic"])
    assert MW not in items_only.scenario.exports
    assert items_only.scenario.grid_import_mw == 1e6

    with_power = build_scenario(
        game, state, sources=list(REFERENCE_FIELD), exports=["MW", "Plastic"]
    )
    assert MW in with_power.scenario.exports
    # None here means "no import allowed", which solve() enforces as a 0 MW cap.
    assert with_power.scenario.grid_import_mw is None


# ---------------------------------------------------------------- plan id


def test_plan_id_changes_when_a_recipe_is_banned(game, state):
    """The id must track anything that changes the solve, or diff_vs_save could
    diff against a plan the user never saw."""
    a = build_scenario(game, state, sources=list(REFERENCE_FIELD))
    b = build_scenario(game, state, sources=list(REFERENCE_FIELD), exclude_recipes=["Recycled"])
    assert a.plan_id != b.plan_id


def test_plan_id_is_stable_for_identical_arguments(game, state):
    a = build_scenario(game, state, sources=list(REFERENCE_FIELD))
    b = build_scenario(game, state, sources=list(REFERENCE_FIELD))
    assert a.plan_id == b.plan_id

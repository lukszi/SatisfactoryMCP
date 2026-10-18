"""The game mode as the server reads it, and the parts rounding (save-projection.md §6.18)."""

from __future__ import annotations

from typing import Any, cast

from satisfactory_mcp.core.saveio.extract import gamemode as extracted
from satisfactory_mcp.domain.world import gamemode

TEST_SAVE: Any = {
    "node_randomization": "strict",
    "node_purity": "all_normal",
    "seed": 160943561,
    "energy_cost": 0.75,
    "parts_cost": 0.75,
    "space_parts_cost": 0.75,
}


def test_the_server_default_is_the_extractors():
    assert gamemode.DEFAULT == extracted.DEFAULT_GAME_MODE


def test_a_projection_without_the_record_reads_as_the_default():
    assert gamemode.mode_of(None) == gamemode.DEFAULT
    assert gamemode.mode_of(cast("Any", {})) == gamemode.DEFAULT
    assert gamemode.energy_cost(cast("Any", {"game_mode": TEST_SAVE})) == 0.75


def test_a_default_game_says_nothing_and_a_changed_one_names_what_changed():
    assert gamemode.summary(gamemode.DEFAULT) is None
    assert gamemode.summary(TEST_SAVE) == (
        "game mode: nodes random, purity average · power x0.75 · parts x0.75"
        " · space elevator parts x0.75"
    )
    assert gamemode.summary({**gamemode.DEFAULT, "parts_cost": 2.0}) == "game mode: parts x2"


def test_a_part_rounds_to_a_whole_one_and_never_to_none():
    assert gamemode.scaled_part(8, 0.75) == 6
    assert gamemode.scaled_part(2, 0.75) == 2, "1.5 rounds up"
    assert gamemode.scaled_part(1, 0.25) == 1
    assert gamemode.scaled_part(5, 1.0) == 5


def _flows(flows):
    return {f.item: (f.amount, f.per_min) for f in flows}


def test_parts_scale_a_production_recipes_ingredients_and_not_its_products(game):
    cheap = gamemode.scaled_game(game, 0.75).recipes["Recipe_IronPlate_C"]
    plain = game.recipes["Recipe_IronPlate_C"]
    # 3 ingots x 0.75 = 2.25 rounds to 2 per 6 s cycle; the 2 plates stay 2.
    assert _flows(cheap.ingredients) == {"Desc_IronIngot_C": (2.0, 20.0)}
    assert cheap.products == plain.products


def test_a_fluid_ingredient_rounds_in_whole_litres(game):
    plain = game.recipes["Recipe_Plastic_C"]
    cheap = gamemode.scaled_game(game, 0.75).recipes["Recipe_Plastic_C"]
    oil = "Desc_LiquidOil_C"
    assert _flows(plain.ingredients)[oil][0] == 3.0
    assert _flows(cheap.ingredients)[oil] == (2.25, 22.5), "2,250 litres need no rounding"
    assert cheap.products == plain.products


def test_build_recipes_are_left_to_cost_of(game):
    scaled = gamemode.scaled_game(game, 0.75)
    builds = [cls for cls, r in game.recipes.items() if r.kind == "building"]
    assert builds
    assert all(scaled.recipes[cls] is game.recipes[cls] for cls in builds)
    assert scaled.buildings is game.buildings


def test_scaling_is_one_object_per_factor_and_never_compounds(game):
    assert gamemode.scaled_game(game, 1.0) is game
    cheap = gamemode.scaled_game(game, 0.75)
    assert gamemode.scaled_game(game, 0.75) is cheap
    assert gamemode.scaled_game(cheap, 0.75) is cheap
    assert gamemode.scaled_game(cheap, 1.0) is game
    dear = gamemode.scaled_game(cheap, 2.0)
    assert dear.recipes["Recipe_IronPlate_C"].ingredients[0].amount == 6.0


def test_a_shown_table_scales_build_parts_and_hands_the_planner_its_own(game):
    world: Any = {"game_mode": TEST_SAVE}
    shown = gamemode.shown_game(game, world)
    planned = gamemode.game_for(game, world)
    build = game.recipes["Recipe_ConstructorMk1_C"]
    assert _flows(build.ingredients)["Desc_Cable_C"][0] == 8.0
    assert _flows(shown.recipes[build.cls].ingredients)["Desc_Cable_C"][0] == 6.0
    assert planned.recipes[build.cls] is build, "cost_of scales build parts for the planner"
    assert shown.recipes["Recipe_IronPlate_C"] == planned.recipes["Recipe_IronPlate_C"]
    assert gamemode.shown_game(planned, world) is shown
    assert gamemode.game_for(shown, world) is planned, "never scaled twice"
    assert gamemode.shown_game(game, cast("Any", {})) is game


def test_the_parts_note_names_the_multiplier_and_is_silent_on_vanilla():
    assert gamemode.parts_note(None) is None
    assert gamemode.parts_note(cast("Any", {})) is None
    note = gamemode.parts_note(cast("Any", {"game_mode": TEST_SAVE}))
    assert note is not None and "parts x0.75" in note

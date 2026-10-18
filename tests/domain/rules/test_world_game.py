"""A world's game data under its game mode (save-projection.md §6.18)."""

from __future__ import annotations

from typing import Any, cast

from satisfactory_mcp.core.saveio.extract import gamemode as extracted
from satisfactory_mcp.domain import rules
from satisfactory_mcp.domain.rules.scaling import scaled_amount

CHEAP: Any = {
    "node_randomization": "strict",
    "node_purity": "all_normal",
    "seed": 160943561,
    "energy_cost": 0.75,
    "parts_cost": 0.75,
    "space_parts_cost": 0.75,
}
WORLD: Any = {"game_mode": CHEAP}


def _flows(flows):
    return {f.item: (f.amount, f.per_min) for f in flows}


def test_the_server_default_is_the_extractors():
    assert rules.DEFAULT == extracted.DEFAULT_GAME_MODE


def test_a_projection_without_the_record_reads_as_the_default():
    assert rules.mode_of(None) == rules.DEFAULT
    assert rules.mode_of(cast("Any", {})) == rules.DEFAULT


def test_a_default_game_says_nothing_and_a_changed_one_names_what_changed():
    assert rules.summary(rules.DEFAULT) is None
    assert rules.summary(CHEAP) == (
        "game mode: nodes random, purity average · power x0.75 · parts x0.75"
        " · space elevator parts x0.75"
    )
    assert rules.summary({**rules.DEFAULT, "parts_cost": 2.0}) == "game mode: parts x2"


def test_a_part_rounds_to_a_whole_one_and_never_to_none():
    assert scaled_amount(8, 0.75) == 6
    assert scaled_amount(2, 0.75) == 2, "1.5 rounds up"
    assert scaled_amount(1, 0.25) == 1
    assert scaled_amount(5, 1.0) == 5


def test_a_vanilla_world_gets_the_vanilla_object(game):
    assert rules.world_game(game, None) is game
    assert rules.world_game(game, cast("Any", {})) is game
    assert rules.factors_of(game).vanilla
    assert rules.scaling_note(game) is None


def test_parts_scale_ingredients_and_never_products(game):
    world = rules.world_game(game, WORLD)
    plain = game.recipes["Recipe_IronPlate_C"]
    cheap = world.recipes["Recipe_IronPlate_C"]
    # 3 ingots x 0.75 = 2.25 rounds to 2 per 6 s cycle; the 2 plates stay 2.
    assert _flows(cheap.ingredients) == {"Desc_IronIngot_C": (2.0, 20.0)}
    assert cheap.products == plain.products


def test_a_fluid_ingredient_rounds_in_whole_litres(game):
    cheap = rules.world_game(game, WORLD).recipes["Recipe_Plastic_C"]
    assert _flows(cheap.ingredients)["Desc_LiquidOil_C"] == (2.25, 22.5)


def test_build_costs_scale_once_in_the_recipe_and_on_the_building(game):
    world = rules.world_game(game, WORLD)
    build = world.recipes["Recipe_ConstructorMk1_C"]
    assert _flows(build.ingredients)["Desc_Cable_C"][0] == 6.0, "8 x 0.75"
    assert world.buildings["Build_ConstructorMk1_C"].build_cost == build.ingredients


def test_energy_scales_every_consumers_draw_and_never_generation(game):
    world = rules.world_game(game, WORLD)
    plate = world.recipes["Recipe_IronPlate_C"]
    assert world.recipe_power_mw(plate) == 3.0, "a Constructor's 4 MW x 0.75"
    assert world.buildings["Build_MinerMk1_C"].power_mw == 3.75, "5 MW x 0.75"
    diamonds = world.recipes["Recipe_Diamond_C"]
    assert (diamonds.power_min_mw, diamonds.power_max_mw) == (187.5, 562.5)
    coal = "Build_GeneratorCoal_C"
    assert world.buildings[coal].power_production_mw == game.buildings[coal].power_production_mw


def test_milestone_costs_stay_vanilla(game):
    """``UFGSchematic::GetCost`` is static: no world reaches it."""
    assert rules.world_game(game, WORLD).schematics is game.schematics


def test_one_table_per_factor_pair_and_never_compounded(game):
    world = rules.world_game(game, WORLD)
    assert rules.world_game(game, WORLD) is world
    assert rules.world_game(world, WORLD) is world
    assert rules.world_game(world, None) is game
    assert rules.vanilla_game(world) is game
    assert rules.factors_of(world) == rules.Factors(energy=0.75, parts=0.75)
    dear = rules.world_game(world, {"game_mode": {**CHEAP, "parts_cost": 2.0}})
    assert dear.recipes["Recipe_IronPlate_C"].ingredients[0].amount == 6.0


def test_the_note_names_each_factor_that_moved(game):
    note = rules.scaling_note(rules.world_game(game, WORLD))
    assert note is not None and "parts x0.75" in note and "power x0.75" in note
    only_power = rules.world_game(game, {"game_mode": {**CHEAP, "parts_cost": 1.0}})
    assert rules.scaling_note(only_power) == (
        "game mode power x0.75: amounts and power are this world's, not vanilla"
    )

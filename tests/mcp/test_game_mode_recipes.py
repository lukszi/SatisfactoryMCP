"""Recipe lookups show a game-mode world's amounts and say so; without a world, vanilla."""

from __future__ import annotations

from typing import Any

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp import app

CHEAP: Any = {
    "node_randomization": "none",
    "node_purity": "no_change",
    "seed": 0,
    "energy_cost": 1.0,
    "parts_cost": 0.75,
    "space_parts_cost": 1.0,
}
NOTE = "game mode parts x0.75"


@pytest.fixture
def cheap(projection, game) -> WorldState:
    return WorldState(projection={**projection, "game_mode": CHEAP}, game=game)


def _no_world(save=None, world=None, as_of=None, **_):
    raise app.Refusal("could not read save: none here")


def _line(out: str, head: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(head))


def test_recipe_detail_shows_the_worlds_ingredients_and_names_the_multiplier(
    cheap, state, use_world
):
    use_world(cheap)
    out = srv.recipe_detail("Iron Plate")
    assert "20 Iron Ingot" in _line(out, "in/min")
    assert "20 Iron Plate" in _line(out, "out/min"), "products are never scaled"
    assert NOTE in out

    use_world(state)
    vanilla = srv.recipe_detail("Iron Plate")
    assert "30 Iron Ingot" in _line(vanilla, "in/min")
    assert "game mode" not in vanilla


def test_without_a_world_a_lookup_stays_vanilla(monkeypatch):
    monkeypatch.setattr(app, "load_world", _no_world)
    out = srv.recipe_detail("Iron Plate")
    assert "30 Iron Ingot" in _line(out, "in/min")
    assert "game mode" not in out


def test_a_build_recipe_shows_its_scaled_parts(cheap, use_world):
    use_world(cheap)
    out = srv.recipe_detail("Recipe_ConstructorMk1_C")
    assert NOTE in out
    # 8 Cable per build x 0.75 = 6, shown per minute of the 1 s placeholder cycle.
    assert "360 Cable" in _line(out, "in/min")


def test_recipe_detail_shows_the_worlds_power(projection, game, use_world):
    mode = {**CHEAP, "energy_cost": 0.75}
    use_world(WorldState(projection={**projection, "game_mode": mode}, game=game))
    out = srv.recipe_detail("Iron Plate")
    assert "power=3MW" in _line(out, "machine="), "a Constructor's 4 MW x 0.75"
    assert "power x0.75" in out
    diamonds = srv.recipe_detail("Recipe_Diamond_C")
    assert "variable power: 187.5-562.5 MW" in diamonds


def test_list_buildings_shows_the_worlds_draw(projection, game, use_world):
    mode = {**CHEAP, "energy_cost": 0.75}
    use_world(WorldState(projection={**projection, "game_mode": mode}, game=game))
    out = srv.list_buildings(building_kind="production")
    row = next(line for line in out.splitlines() if "\tConstructor\t" in line)
    assert "\t3MW\t" in row, "a Constructor's 4 MW x 0.75"
    assert "power x0.75" in out


def test_search_and_alternates_follow_the_world(cheap, use_world):
    use_world(cheap)
    found = srv.search_recipes(consumes="Iron Ingot", limit=50)
    plate = next(line for line in found.splitlines() if line.startswith("Iron Plate"))
    assert "20 Iron Ingot" in plate
    assert NOTE in found
    makers = srv.alternates_for_item("Iron Plate")
    plate = next(line for line in makers.splitlines() if line.startswith("Iron Plate"))
    assert "20 Iron Ingot" in plate
    assert NOTE in makers

"""Which generator type a short circuit builds out with: the biggest the world can run."""

from __future__ import annotations

from satisfactory_mcp.domain.progression.generators import generator_candidates


def _world(game, buildings: list[str], recipes: list[str] = ()):
    from satisfactory_mcp.domain.world.state import WorldState

    descriptors = {game.buildings[b].descriptor for b in buildings}
    grants = [
        rid
        for rid, r in game.recipes.items()
        if r.kind == "building" and any(p.item in descriptors for p in r.products)
    ]
    progression = {"available_recipes": [*grants, *recipes]}
    return WorldState(projection={"progression": progression}, game=game)


def test_biggest_first_and_never_a_burner_or_a_geyser(game):
    rows = generator_candidates(_world(game, []))
    names = [r["name"] for r in rows]
    assert [r["mw"] for r in rows] == sorted((r["mw"] for r in rows), reverse=True)
    assert "Biomass Burner" not in names
    assert "Geothermal Generator" not in names
    assert not any(r["runnable"] for r in rows)
    assert all(r["verdict"].startswith("not unlocked: ") for r in rows)


def test_coal_runs_on_a_raw_fuel_and_a_fuel_plant_needs_its_chain(game):
    """Coal is a resource, so the plant runs once built; Fuel needs refinery recipes."""
    st = _world(game, ["Build_GeneratorCoal_C", "Build_GeneratorFuel_C"])
    rows = {r["name"]: r for r in generator_candidates(st)}
    coal = rows[game.buildings["Build_GeneratorCoal_C"].name]
    fuel = rows[game.buildings["Build_GeneratorFuel_C"].name]
    assert (coal["runnable"], coal["verdict"], coal["fuel"]) == (
        True,
        "unlocked, fuel makeable",
        "Coal",
    )
    assert not fuel["runnable"]
    assert fuel["verdict"] == "unlocked, no fuel makeable"
    assert "Fuel" in fuel["why"], "the planner says which fuel and why"
    first = next(r for r in generator_candidates(st) if r["runnable"])
    assert first["name"] == coal["name"], "the fuel plant is bigger but cannot run"


def test_an_unlocked_fuel_chain_makes_the_bigger_plant_the_answer(game):
    parts = ["Recipe_Plastic_C", "Recipe_Rubber_C", "Recipe_PetroleumCoke_C", "Recipe_LiquidFuel_C"]
    assert "Recipe_LiquidFuel_C" in game.recipes
    machines = ["Build_GeneratorCoal_C", "Build_GeneratorFuel_C", "Build_OilRefinery_C"]
    st = _world(game, machines, [r for r in parts if r in game.recipes])
    first = next(r for r in generator_candidates(st) if r["runnable"])
    assert first["name"] == game.buildings["Build_GeneratorFuel_C"].name

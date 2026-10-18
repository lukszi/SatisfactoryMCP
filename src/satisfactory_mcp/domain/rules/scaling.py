"""The game data as a game mode runs it, built once per pair of factors.

Which values each factor reaches, and the rounding, are in docs/save-projection.md §6.18.
"""

from __future__ import annotations

import math
import threading
from dataclasses import replace

from ...core.gamedata.model import Building, Flow, GameData, Recipe
from .mode import VANILLA, Factors

__all__ = ["factors_of", "scaled_amount", "table_for", "vanilla_of"]

#: Litres per m3: the game counts a fluid amount in whole litres, so it rounds there.
_LITRES = 1000.0


def scaled_amount(amount: float, parts: float) -> float:
    """One part amount under the parts factor: a whole part, never below one."""
    if parts == 1.0 or amount <= 0:
        return amount
    return float(max(1, math.floor(amount * parts + 0.5)))


def _scaled_flow(flow: Flow, fluid: bool, parts: float, duration_s: float) -> Flow:
    unit = _LITRES if fluid else 1.0
    amount = scaled_amount(flow.amount * unit, parts) / unit
    per_min = amount * 60 / duration_s if duration_s else 0.0
    return Flow(item=flow.item, amount=amount, per_min=per_min)


def _scaled_recipe(base: GameData, recipe: Recipe, factors: Factors) -> Recipe:
    def fluid(item: str) -> bool:
        known = base.items.get(item)
        return known is not None and known.is_fluid

    ingredients = tuple(
        _scaled_flow(f, fluid(f.item), factors.parts, recipe.duration_s) for f in recipe.ingredients
    )
    return replace(
        recipe,
        ingredients=ingredients,
        power_min_mw=recipe.power_min_mw * factors.energy,
        power_max_mw=recipe.power_max_mw * factors.energy,
    )


def _scaled_building(
    building: Building, factors: Factors, costs: dict[int, tuple[Flow, ...]]
) -> Building:
    """``costs`` maps each build recipe's ingredient tuple to its scaled one, which is how
    normalize shares a build cost with its recipe."""
    cost = costs.get(id(building.build_cost))
    if cost is None:
        cost = tuple(
            replace(f, amount=scaled_amount(f.amount, factors.parts)) for f in building.build_cost
        )
    return replace(building, power_mw=building.power_mw * factors.energy, build_cost=cost)


def _scaled_table(base: GameData, factors: Factors) -> GameData:
    recipes = {cls: _scaled_recipe(base, r, factors) for cls, r in base.recipes.items()}
    costs = {id(base.recipes[cls].ingredients): r.ingredients for cls, r in recipes.items()}
    buildings = {cls: _scaled_building(b, factors, costs) for cls, b in base.buildings.items()}
    return replace(base, recipes=recipes, buildings=buildings)


#: ``(id(base), factors)`` to the scaled table, and each scaled table's id back to its base
#: and factors. Entries hold their objects, so an id is never reused while it is a key.
_SCALED: dict[tuple[int, Factors], GameData] = {}
_ORIGIN: dict[int, tuple[GameData, Factors]] = {}
_LOCK = threading.Lock()


def vanilla_of(game: GameData) -> GameData:
    """The vanilla table a world table was built from; a vanilla table is its own."""
    return _ORIGIN.get(id(game), (game, VANILLA))[0]


def factors_of(game: GameData) -> Factors:
    """The factors a table was built under; vanilla for the loaded table."""
    return _ORIGIN.get(id(game), (game, VANILLA))[1]


def table_for(game: GameData, factors: Factors) -> GameData:
    """``game``'s vanilla table under ``factors``: one object per pair, never compounded,
    and the vanilla object itself for vanilla factors."""
    base = vanilla_of(game)
    if factors.vanilla:
        return base
    with _LOCK:
        held = _SCALED.get((id(base), factors))
        if held is None:
            held = _SCALED[(id(base), factors)] = _scaled_table(base, factors)
            _ORIGIN[id(held)] = (base, factors)
        return held

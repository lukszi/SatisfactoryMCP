"""A save's 1.2 game mode: the node layout it was created with and its cost multipliers.

What each option changes in the game, and which of it this project models, is in
``docs/save-projection.md`` §6.18. A save without the record reads as the defaults.
"""

from __future__ import annotations

import math
import threading
from dataclasses import replace

from ...core.gamedata.model import Flow, GameData, Recipe
from ...core.saveio.schema import GameMode, Projection

__all__ = [
    "DEFAULT",
    "PURITY_LABELS",
    "RANDOMIZATION_LABELS",
    "energy_cost",
    "game_for",
    "is_default",
    "mode_of",
    "parts_cost",
    "randomized",
    "scaled_game",
    "scaled_part",
    "summary",
]

DEFAULT: GameMode = {
    "node_randomization": "none",
    "node_purity": "no_change",
    "seed": 0,
    "energy_cost": 1.0,
    "parts_cost": 1.0,
    "space_parts_cost": 1.0,
}

#: The New Game screen's own names for the options.
RANDOMIZATION_LABELS = {
    "none": "default",
    "strict": "random",
    "basic_rich": "basic resource rich",
    "advanced_rich": "advanced resource rich",
    "fossil_fuel_rich": "fossil fuel rich",
}
PURITY_LABELS = {
    "no_change": "default",
    "all_pure": "all pure",
    "increase": "mostly pure",
    "all_normal": "average",
    "decrease": "mostly impure",
    "all_impure": "all impure",
    "all_random": "random",
}


def mode_of(projection: Projection | None) -> GameMode:
    """The save's game mode; the defaults for no save or a projection without one."""
    return (projection.get("game_mode") if projection else None) or DEFAULT


def energy_cost(projection: Projection | None) -> float:
    """The factor on every building's power draw; generation is not scaled."""
    return mode_of(projection)["energy_cost"]


def parts_cost(projection: Projection | None) -> float:
    """The factor on every recipe's ingredients, build and production alike."""
    return mode_of(projection)["parts_cost"]


def scaled_part(amount: float, multiplier: float) -> float:
    """One ingredient amount under the parts multiplier: a whole part, never below one."""
    if multiplier == 1.0 or amount <= 0:
        return amount
    return float(max(1, math.floor(amount * multiplier + 0.5)))


#: Litres per m3: the game counts a fluid amount in whole litres, so it rounds there.
_LITRES = 1000.0


def _scaled_flow(flow: Flow, fluid: bool, multiplier: float, duration_s: float) -> Flow:
    unit = _LITRES if fluid else 1.0
    amount = scaled_part(flow.amount * unit, multiplier) / unit
    per_min = amount * 60 / duration_s if duration_s else 0.0
    return Flow(item=flow.item, amount=amount, per_min=per_min)


def _scaled_recipe(game: GameData, recipe: Recipe, multiplier: float) -> Recipe:
    def fluid(item: str) -> bool:
        known = game.items.get(item)
        return known is not None and known.is_fluid

    ingredients = tuple(
        _scaled_flow(f, fluid(f.item), multiplier, recipe.duration_s) for f in recipe.ingredients
    )
    return replace(recipe, ingredients=ingredients)


#: ``(id(base), multiplier)`` to ``(base, scaled)``, and each scaled copy's id back to its
#: base, so one factor yields one object: views are cached on the game data's ``id()``.
_SCALED: dict[tuple[int, float], tuple[GameData, GameData]] = {}
_ORIGIN: dict[int, tuple[GameData, float]] = {}
_LOCK = threading.Lock()


def scaled_game(game: GameData, multiplier: float) -> GameData:
    """``game`` with each production and craft-bench recipe's ingredients under the parts
    multiplier, products untouched. Build recipes stay as they are, because ``cost_of``
    scales building parts. Idempotent; a factor of 1 gives back the vanilla object."""
    with _LOCK:
        base, applied = _ORIGIN.get(id(game), (game, 1.0))
        if applied == multiplier:
            return game
        if multiplier == 1.0:
            return base
        held = _SCALED.get((id(base), multiplier))
        if held is None:
            recipes = {
                cls: r if r.kind == "building" else _scaled_recipe(base, r, multiplier)
                for cls, r in base.recipes.items()
            }
            held = (base, replace(base, recipes=recipes))
            _SCALED[(id(base), multiplier)] = held
            _ORIGIN[id(held[1])] = (base, multiplier)
        return held[1]


def game_for(game: GameData, projection: Projection | None) -> GameData:
    """The recipes as this save's game mode runs them (``scaled_game``)."""
    return scaled_game(game, parts_cost(projection))


def randomized(mode: GameMode) -> bool:
    return mode["node_randomization"] != "none" or mode["node_purity"] != "no_change"


def _factors(mode: GameMode) -> list[tuple[str, float]]:
    """The multipliers that are not 1, with the word each is shown under."""
    named = [
        ("power", mode["energy_cost"]),
        ("parts", mode["parts_cost"]),
        ("space elevator parts", mode["space_parts_cost"]),
    ]
    return [(label, value) for label, value in named if value != 1.0]


def is_default(mode: GameMode) -> bool:
    return not randomized(mode) and not _factors(mode)


def summary(mode: GameMode) -> str | None:
    """One line naming what is not default, ``None`` for a default game."""
    if is_default(mode):
        return None
    parts: list[str] = []
    if randomized(mode):
        nodes = RANDOMIZATION_LABELS.get(mode["node_randomization"], mode["node_randomization"])
        purity = PURITY_LABELS.get(mode["node_purity"], mode["node_purity"])
        parts.append(f"nodes {nodes}, purity {purity}")
    parts += [f"{label} x{value:g}" for label, value in _factors(mode)]
    return "game mode: " + " · ".join(parts)

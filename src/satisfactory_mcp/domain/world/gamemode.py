"""A save's 1.2 game mode: the node layout it was created with and its cost multipliers.

What each option changes in the game, and which of it this project models, is in
``docs/save-projection.md`` §6.18. A save without the record reads as the defaults.
"""

from __future__ import annotations

import math

from ...core.saveio.schema import GameMode, Projection

__all__ = [
    "DEFAULT",
    "PURITY_LABELS",
    "RANDOMIZATION_LABELS",
    "energy_cost",
    "is_default",
    "mode_of",
    "parts_cost",
    "randomized",
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
    """The factor on every build recipe's parts."""
    return mode_of(projection)["parts_cost"]


def scaled_part(amount: float, multiplier: float) -> float:
    """One ingredient amount under the parts multiplier: a whole part, never below one."""
    if multiplier == 1.0 or amount <= 0:
        return amount
    return float(max(1, math.floor(amount * multiplier + 0.5)))


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

"""A save's 1.2 game mode as the domain reads it: the record, its factors and its labels."""

from __future__ import annotations

from typing import NamedTuple

from ...core.saveio.schema import GameMode, Projection

__all__ = [
    "DEFAULT",
    "PURITY_LABELS",
    "RANDOMIZATION_LABELS",
    "Factors",
    "factors",
    "is_default",
    "mode_of",
    "randomized",
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


class Factors(NamedTuple):
    """The two multipliers that change game data: on consumers' draw and on recipe parts."""

    energy: float = 1.0
    parts: float = 1.0

    @property
    def vanilla(self) -> bool:
        return self == VANILLA


VANILLA = Factors()


def mode_of(projection: Projection | None) -> GameMode:
    """The save's game mode; the defaults for no save or a projection without one."""
    return (projection.get("game_mode") if projection else None) or DEFAULT


def factors(projection: Projection | None) -> Factors:
    mode = mode_of(projection)
    return Factors(energy=mode["energy_cost"], parts=mode["parts_cost"])


def randomized(mode: GameMode) -> bool:
    return mode["node_randomization"] != "none" or mode["node_purity"] != "no_change"


def _changed(mode: GameMode) -> list[tuple[str, float]]:
    """The multipliers that are not 1, with the word each is shown under."""
    named = [
        ("power", mode["energy_cost"]),
        ("parts", mode["parts_cost"]),
        ("space elevator parts", mode["space_parts_cost"]),
    ]
    return [(label, value) for label, value in named if value != 1.0]


def is_default(mode: GameMode) -> bool:
    return not randomized(mode) and not _changed(mode)


def summary(mode: GameMode) -> str | None:
    """One line naming what is not default, ``None`` for a default game."""
    if is_default(mode):
        return None
    parts: list[str] = []
    if randomized(mode):
        nodes = RANDOMIZATION_LABELS.get(mode["node_randomization"], mode["node_randomization"])
        purity = PURITY_LABELS.get(mode["node_purity"], mode["node_purity"])
        parts.append(f"nodes {nodes}, purity {purity}")
    parts += [f"{label} x{value:g}" for label, value in _changed(mode)]
    return "game mode: " + " · ".join(parts)

"""A world's rules: its 1.2 game mode, and the game data as that world runs it.

``world_game`` is the one place a world's table is made; ``WorldState.game`` holds it, so
code with a world in hand reads that world's numbers. ``vanilla_game`` is the named way
back for code with no world. The design is in docs/save-projection.md §6.18.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...core.saveio.schema import Projection
from .mode import (
    DEFAULT,
    PURITY_LABELS,
    RANDOMIZATION_LABELS,
    Factors,
    is_default,
    mode_of,
    randomized,
    summary,
)
from .mode import factors as _factors
from .scaling import factors_of, table_for, vanilla_of

__all__ = [
    "DEFAULT",
    "PURITY_LABELS",
    "RANDOMIZATION_LABELS",
    "Factors",
    "factors_of",
    "is_default",
    "mode_of",
    "randomized",
    "scaling_note",
    "summary",
    "vanilla_game",
    "world_game",
]


def world_game(game: GameData, projection: Projection | None) -> GameData:
    """The game data as the save's game mode runs it: recipe ingredients and build costs
    under the parts factor, consumers' draw under the energy factor. Takes a vanilla or
    a world table alike; a vanilla world gets the vanilla object back."""
    return table_for(game, _factors(projection))


def vanilla_game(game: GameData) -> GameData:
    """The unscaled table behind ``game``, for answers that hold no world."""
    return vanilla_of(game)


def scaling_note(game: GameData) -> str | None:
    """The note beside figures read from a scaled table, ``None`` for vanilla."""
    factors = factors_of(game)
    named = [("parts", factors.parts), ("power", factors.energy)]
    changed = [f"{label} x{value:g}" for label, value in named if value != 1.0]
    if not changed:
        return None
    return f"game mode {', '.join(changed)}: amounts and power are this world's, not vanilla"

"""A world's rules: its 1.2 game mode, and the game data as that world runs it.

``world_game`` is the one place a world's table is made, and ``WorldState.game`` holds it, so
code with a world in hand reads that world's numbers. The design is in
docs/save-projection.md §6.18.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...core.saveio.schema import Projection
from .mode import DEFAULT, Factors, mode_of, randomized, summary
from .mode import factors as _factors
from .scaling import factors_of, table_for

__all__ = [
    "DEFAULT",
    "Factors",
    "factors_of",
    "mode_of",
    "randomized",
    "scaling_note",
    "summary",
    "world_game",
]


def world_game(game: GameData, projection: Projection | None) -> GameData:
    """The game data as the save's game mode runs it: recipe ingredients and build costs
    under the parts factor, consumers' draw under the energy factor. Takes a vanilla or
    a world table alike; a vanilla world gets the vanilla object back."""
    return table_for(game, _factors(projection))


def scaling_note(game: GameData) -> str | None:
    """The note beside figures read from a scaled table, ``None`` for vanilla."""
    factors = factors_of(game)
    named = [("parts", factors.parts), ("power", factors.energy)]
    changed = [f"{label} x{value:g}" for label, value in named if value != 1.0]
    if not changed:
        return None
    return f"game mode {', '.join(changed)}: amounts and power are this world's, not vanilla"

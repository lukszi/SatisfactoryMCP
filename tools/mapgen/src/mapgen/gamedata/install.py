"""The installed game opened once: its container, script objects, asset index and class facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satisfactory_mcp.core.gameassets.container import CONTAINER, open_container, paks_dir
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects

__all__ = ["GameReader", "missing_container", "open_game"]


@dataclass(frozen=True)
class GameReader:
    """What every reader of the game's packages takes, opened once per command."""

    store: IoStore
    scripts: ScriptObjects
    index: AssetIndex
    classes: ClassFacts


def missing_container(game: Path) -> str | None:
    """Why ``game`` cannot be read, or ``None`` when its container is there."""
    paks = paks_dir(game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        return f"no {CONTAINER}.utoc under {paks}"
    return None


def open_game(game: Path) -> GameReader:
    """The install's container, opened with its script objects, asset index and class facts."""
    store = open_container(game)
    scripts = ScriptObjects(paks_dir(game), oodle_decompress)
    index = AssetIndex(store)
    return GameReader(store, scripts, index, ClassFacts(store, index))

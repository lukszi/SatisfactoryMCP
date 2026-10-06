"""Importing this package registers every tool: each module's decorators run on import.

The imports look unused and are not -- dropping one would leave the server starting cleanly
without that module's tools, which is why ``__all__`` names them all.
"""

from . import (
    collectibles,
    factories,
    gamedata,
    harddrives,
    inventory,
    planning,
    progression,
    settings,
    spatial,
    world,
)

__all__ = [
    "collectibles",
    "factories",
    "gamedata",
    "harddrives",
    "inventory",
    "planning",
    "progression",
    "settings",
    "spatial",
    "world",
]

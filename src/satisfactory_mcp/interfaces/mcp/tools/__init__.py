"""Importing this package registers every tool, resource and prompt.

The imports look unused and are not: each module's decorators run on import, which
is what attaches it to the shared ``mcp``. Hence the explicit ``__all__`` and the
noqa -- a linter pruning these would silently empty the server.
"""

from . import (
    factories,
    floors,
    gamedata,
    harddrives,
    inventory,
    planning,
    progression,
    prompts,
    resources,
    settings,
    spatial,
    world,
)

__all__ = [
    "factories",
    "floors",
    "gamedata",
    "harddrives",
    "inventory",
    "planning",
    "progression",
    "prompts",
    "resources",
    "settings",
    "spatial",
    "world",
]

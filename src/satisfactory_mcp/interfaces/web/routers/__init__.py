"""One module per concern, and the ORDER they are mounted in.

Each module owns its own ``APIRouter(prefix="/api")`` instance, so the order the surface is
registered in is decided by ``ALL_ROUTERS`` below rather than by import order.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    collectibles,
    crates,
    events,
    factories,
    floors,
    grid,
    health,
    icons,
    inspect,
    nodes,
    placements,
    plans,
    progress,
    power,
    regions,
    routes_layer,
    storage,
    tiles,
    world,
)

__all__ = ["ALL_ROUTERS"]

#: Mounted in this order, and the tuple is APPEND-ONLY: ``/openapi.json`` emits ``paths`` in
#: registration order and the committed ``api-schema.d.ts`` inherits it, so a router that
#: moves within this tuple rewrites the generated file with a diff that means nothing. A new
#: router goes at the end.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    world.router,
    nodes.router,
    inspect.router,
    regions.router,
    tiles.router,
    placements.router,
    routes_layer.router,
    storage.router,
    power.router,
    factories.router,
    floors.router,
    collectibles.router,
    events.router,
    crates.router,
    icons.router,
    plans.router,
    health.router,
    grid.router,
    progress.router,
)

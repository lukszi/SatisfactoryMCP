"""One module per concern, grouped by area, and the ORDER they are mounted in.

Each module owns its own ``APIRouter(prefix="/api")`` instance, so the order the surface is
registered in is decided by ``ALL_ROUTERS`` below rather than by import order.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from .assets import icons, maps, tiles
from .bridge import asks, events, pins, settings
from .codex import gamedata, search
from .dashboard import advice, power_circuits, progress_boosts, progress_unlocks, stock
from .factories import (
    factory_detail,
    factory_graph,
    factory_health,
    factory_labels,
    factory_list,
    trace,
)
from .layers import belts_pipes, crates, floors, placements, power_lines, storage
from .plans import activity, plan_index, plan_site, plan_solve, plan_track, planlog
from .world import (
    collectibles,
    inspect,
    nodes,
    regions,
    world_conduits,
    world_finders,
    worlds,
)

__all__ = ["ALL_ROUTERS"]

#: Mounted in this order, and the tuple is APPEND-ONLY: ``/openapi.json`` emits ``paths`` in
#: registration order and the committed ``api/schema.d.ts`` inherits it, so a router that
#: moves within this tuple rewrites the generated file with a diff that means nothing. A new
#: router goes at the end.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    worlds.router,
    nodes.router,
    inspect.router,
    regions.router,
    tiles.router,
    placements.router,
    belts_pipes.router,
    storage.router,
    power_lines.router,
    factory_list.router,
    floors.router,
    collectibles.router,
    events.router,
    crates.router,
    icons.router,
    plan_index.router,
    factory_health.router,
    power_circuits.router,
    progress_unlocks.router,
    progress_boosts.router,
    factory_labels.router,
    plan_solve.router,
    activity.router,
    planlog.router,
    stock.router,
    gamedata.router,
    search.router,
    trace.router,
    factory_detail.router,
    world_finders.router,
    world_conduits.router,
    pins.router,
    asks.router,
    factory_graph.router,
    plan_track.router,
    plan_site.router,
    settings.router,
    advice.router,
    maps.router,
)

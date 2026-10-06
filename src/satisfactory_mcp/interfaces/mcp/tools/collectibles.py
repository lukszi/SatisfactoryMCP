"""``collected_from_world``: map collectibles placed, taken, left and nearest."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ....domain.collectibles.service import collect_view
from ....presenters.text.collectibles import render_collectibles
from .. import app
from ..params import AsOf, Limit


@app.tool()
def collected_from_world(
    group: Annotated[
        str | None,
        Field(description="one category, e.g. 'power_slug_blue'. Omit to see them all"),
    ] = None,
    show: Annotated[
        str,
        Field(description="census | collected | remaining | nearest"),
    ] = "census",
    mode: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    near: Annotated[
        str | None,
        Field(
            description="origin for show=nearest: 'x,y' in metres, 'me', or a factory name. "
            "Defaults to where the player is standing"
        ),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
    ctx: Context | None = None,
) -> str:
    """Map collectibles: how many exist, how many you took, what is left and what is closest.

    Slugs, somersloops, Mercer spheres and their shrines, mushrooms, drop pods and the loot
    caches around them. Two sources, and neither is asked the other's question:

    * **the map** says what exists and where, read from the installed game's own cooked
      packages, so ``placed`` is exact and every coordinate is exact;
    * **the save** says what is gone. The world is not saved -- a save never mentions a slug
      still lying there -- so its destroyed-actor list *is* the collected list, and it is
      exact too. ``remaining`` is the subtraction of the two.

    Views: ``census`` (default) counts every category; ``collected`` and ``remaining`` list
    individual placements with coordinates; ``nearest`` lists the remaining ones by distance
    from ``near``, defaulting to the player.

    A placement in a cell no save has ever loaded is counted as remaining and reported as
    ``never_streamed``. It is never called present -- the map says where it is and nothing
    on disk says whether it is still there.
    """
    if gone := app.retired(("mode", mode, "show")):
        return gone
    st = app.load_world(save, world, as_of)

    view = collect_view(st, group, show, near)
    if not view.error:
        listed = view.mode if view.mode in ("collected", "nearest") else None
        app.journal_world_find(
            st,
            ctx,
            "collected_from_world",
            "pickups",
            {"view": listed, "group": group, "near": near if listed == "nearest" else None},
            "looked at pickups" + (f" ({group})" if group else ""),
        )
    return render_collectibles(st, view, limit, offset=offset)

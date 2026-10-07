"""``/api/collectibles``: slugs, mercer spheres and the rest, filtered as the tool filters.

Every refusal this endpoint makes is ``collect_view``'s, so that the map and the MCP tool
cannot hold two opinions about one question.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....domain.collectibles import service
from .....domain.collectibles.service import collect_view
from ...serial import (
    CollectibleRow,
    TableAge,
    collectible_json,
    error_response,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- collectibles


Mode = Literal["census", "collected", "remaining", "nearest"]


class CensusRow(TypedDict):
    """One category: the map's count, this save's collections, and what is left.

    ``remaining`` is null where no save records a collection of the class at all;
    ``standing`` and ``never_streamed`` are null when the table's states are another world's.
    """

    category: str
    label: str
    placed: int
    collected: int
    remaining: int | None
    standing: int | None
    never_streamed: int | None
    looted_standing: int
    state_tracked: bool
    pedestal_of: str | None
    spoiler: bool


class CollectiblesResponse(TypedDict):
    """The view ``collect_view`` decided.

    ``mode`` is a closed union because ``collect_view`` refuses anything outside these four
    before a view exists at all, so a fifth mode cannot reach the wire without the domain
    service changing -- and then it should be loud here.

    ``rows`` is a list and never null: the view answers ``None`` for ``mode=census``, which
    counts instead of listing, and the handler sends the empty list. ``counts`` is an open
    map because its keys are observed states and a save whose rows are all in one of them
    sends a one-key object -- a tally, not a schema. ``where`` is ``""`` for every mode that
    measures no distance.
    """

    mode: Mode
    group: str | None
    rows: list[CollectibleRow]
    counts: dict[str, int]
    hidden_pedestals: int
    save_only: bool
    where: str
    census: list[CensusRow]
    found: list[str]
    hidden_spoilers: int
    stale: TableAge | None


@router.get("/collectibles", response_model=CollectiblesResponse)
def collectibles(
    request: Request,
    group: str | None = None,
    mode: str = "remaining",
    near: str | None = None,
    spoilers: Annotated[int | None, Query(ge=0, le=1)] = None,
    save: str | None = None,
    world: str | None = None,
) -> CollectiblesResponse | JSONResponse:
    """Map placements, filtered exactly the way the MCP tool filters them.

    ``collect_view`` owns every refusal -- unknown mode, retired group, and the one that
    matters here: ``mode=remaining`` needs the generated placement table, and without it the
    honest answer is that refusal rather than a shorter list.

    ``census`` rides along in every mode. ``spoilers=0`` drops every category this save has
    never collected one of (pods and loot caches excepted) from the rows, the counts and the
    census, and ``hidden_spoilers`` says how many categories went; absent, every row comes
    with its ``spoiler`` flag.
    """
    st = require_world(request, save, world)

    view = collect_view(st, group, mode, near)
    if view.error:
        return error_response(view.error)

    census = service.census_rows(st) if view.table is not None else []
    found = service.found(st, census)
    observed = st.removed.observed
    hide = spoilers == 0
    kept_census = [c for c in census if not (hide and c["spoiler"])]
    hidden = {c["category"] for c in census} - {c["category"] for c in kept_census}
    rows = [
        collectible_json(r, service.is_spoiler(r["category"], found))
        for r in (view.rows or ())
        if r["category"] not in hidden
    ]
    counts = view.counts
    if hidden:
        counts = service.state_counts(r for r in view.rows or () if r["category"] not in hidden)
    return {
        # ``collect_view`` refused every other word before it built a view.
        "mode": cast(Mode, view.mode),
        "group": view.group,
        "rows": rows,
        "counts": counts,
        "hidden_pedestals": view.hidden,
        "save_only": view.save_only,
        "where": view.where,
        "census": [
            {
                "category": c["category"],
                "label": c["label"],
                "placed": c["placed"],
                "collected": c["collected"],
                "remaining": c["remaining"],
                "standing": c["standing"] if observed else None,
                "never_streamed": c["never_streamed"] if observed else None,
                "looted_standing": c["looted_and_standing"],
                "state_tracked": c["state_tracked"],
                "pedestal_of": c["pedestal_of"],
                "spoiler": c["spoiler"],
            }
            for c in kept_census
        ],
        "found": found,
        "hidden_spoilers": len(hidden),
        "stale": service.table_age(st),
    }

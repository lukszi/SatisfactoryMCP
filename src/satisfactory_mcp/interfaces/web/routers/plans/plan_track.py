"""``/api/plan/track`` and ``/api/plan/feeders``: one plan matched against the save.

Track is one plan version's diff and startup stages against this save, from one solve.
Feeders is the built extractors whose output reaches a running generator.
docs/planner-p4_contract.md §5 is the specification.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .....domain.planning.progress import track
from .....domain.planning.progress.views import FeedersResponse, TrackResponse
from .....domain.world import pin
from ...serial import Biomass, check_plan_key, error_response, plan_log, require_plan, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


@router.get("/plan/track", response_model=TrackResponse)
def plan_track(
    request: Request,
    key: str,
    rev: int | None = None,
    biomass: Biomass = "exclude",
    headroom: Literal["measured", "nameplate"] = "measured",
    save: str | None = None,
    world: str | None = None,
) -> TrackResponse | JSONResponse:
    """One plan version (the head when ``rev`` is omitted) diffed and staged against this save.
    ``headroom`` is the save's figure a plan with no stored headroom is staged against."""
    check_plan_key(key)
    st = require_world(request, save, world)
    state = require_plan(plan_log(st), key, rev)
    try:
        out = track.track_view(st.game, st, state, biomass=biomass == "include", default=headroom)
    except ValueError as exc:
        return error_response(str(exc), 400)
    out["built_at"]["token"] = pin.check(st.header, None)
    return out


@router.get("/plan/feeders", response_model=FeedersResponse)
def plan_feeders(
    request: Request,
    biomass: Biomass = "exclude",
    save: str | None = None,
    world: str | None = None,
) -> FeedersResponse:
    """Built extractors whose output reaches a running generator: what startup waves stand on."""
    st = require_world(request, save, world)
    return track.feeders_view(st.game, st, biomass=biomass == "include")

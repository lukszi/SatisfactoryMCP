"""``/api/plan/site-preview``: what a plan's pad would meet at a candidate spot.

docs/planner-p5_contract.md §4 is the specification. Read only: the drop is an ordinary
``site`` op on ``/api/plans/{key}/ops``.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, Request

from .....domain.planning import siting
from .....domain.planning.siting import preview as site_preview
from .....domain.planning.siting.views import SitePreviewResponse
from .....domain.planning.stored.planlog import PlanState
from .....domain.world import pin
from .....domain.world.state import WorldState
from ... import terrain
from ...serial import Biomass, check_plan_key, plan_log, require_plan, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: (world, plan key, rev, save token, biomass, headroom) -> the session opened for them.
PreviewKey = tuple[str, str, int, str, bool, str]
_PREVIEW_CACHE: OrderedDict[PreviewKey, site_preview.PreviewSession] = OrderedDict()
_LOCK = threading.Lock()
PREVIEW_CACHE_MAX = 8


def _preview_session(
    st: WorldState, state: PlanState, biomass: bool, headroom: str, token: str
) -> site_preview.PreviewSession:
    key: PreviewKey = (st.world_id, state.key, state.rev, token, biomass, headroom)
    with _LOCK:
        hit = _PREVIEW_CACHE.get(key)
        if hit is not None:
            _PREVIEW_CACHE.move_to_end(key)
            return hit
    session = site_preview.open_session(st.game, st, state, biomass=biomass, default=headroom)
    with _LOCK:
        _PREVIEW_CACHE[key] = session
        while len(_PREVIEW_CACHE) > PREVIEW_CACHE_MAX:
            _PREVIEW_CACHE.popitem(last=False)
    return session


@router.get("/plan/site-preview", response_model=SitePreviewResponse)
def plan_site_preview(
    request: Request,
    key: str,
    rev: int | None = None,
    x_m: float | None = None,
    y_m: float | None = None,
    yaw_deg: float | None = None,
    w_m: float | None = None,
    d_m: float | None = None,
    first: bool = False,
    full: bool = False,
    biomass: Biomass = "exclude",
    headroom: Literal["measured", "nameplate"] = "measured",
    save: str | None = None,
    world: str | None = None,
) -> SitePreviewResponse:
    """A plan version's pad at (x, y, yaw, w × d), every part omitted taken from its stored
    site, else from where a first placement starts. ``full`` reads the terrain at 1 m."""
    check_plan_key(key)
    st = require_world(request, save, world)
    state = require_plan(plan_log(st), key, rev)
    token = pin.check(st.header, None)
    session = _preview_session(st, state, biomass == "include", headroom, token)
    base = siting.parse(state) or site_preview.initial_siting(st.game, st, session)
    w = base.width_m if w_m is None else w_m
    d = base.depth_m if d_m is None else d_m
    if not (w > 0 and d > 0):
        w = d = site_preview.FALLBACK_SIDE_M
    sit = siting.Siting(
        x_m=base.x_m if x_m is None else x_m,
        y_m=base.y_m if y_m is None else y_m,
        yaw_deg=(base.yaw_deg if yaw_deg is None else yaw_deg) % 360.0,
        width_m=w,
        depth_m=d,
        source=base.source,
    )
    try:
        field, unread = terrain.field(), False
    except MemoryError:
        field, unread = None, True
    out = site_preview.preview(
        st.game,
        st,
        session,
        sit,
        terrain=field,
        terrain_cap=0 if full else site_preview.DRAG_TEXELS,
        include_static=first,
    )
    if unread and out["in_map"]:
        out["terrain_note"] = site_preview.NOT_READ
    out["token"] = token
    return out

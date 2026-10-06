"""``/api/plan/site-preview``: what a plan's pad would meet at a candidate spot.

docs/planner-p5_contract.md §4 is the specification. Read only: the drop is an ordinary
``site`` op on ``/api/plans/{key}/ops``.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from .....domain.planning import siting
from .....domain.planning.siting import preview as site_preview
from .....domain.world import pin
from ... import terrain
from ...serial import Biomass, check_plan_key, plan_log, require_plan, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")

_PREVIEW_CACHE: OrderedDict[tuple, site_preview.Session] = OrderedDict()
_LOCK = threading.Lock()
PREVIEW_CACHE_MAX = 8


class SitePreviewNode(TypedDict):
    instance: str
    resource: str
    x_m: float
    y_m: float


class SiteTrunk(TypedDict):
    """One trunk: ``run_m`` node to node and ``to_site_m`` the leg to the pad, straight lines;
    ``lift_m`` and ``pumps`` are null without a ground height."""

    name: str
    carrier: str
    members: int
    run_m: float
    to_site_m: float
    lift_m: float | None
    pumps: int | None


class SiteTerrain(TypedDict):
    z_min_m: float | None
    z_median_m: float | None
    z_max_m: float | None
    slope_mean_deg: float | None
    slope_p90_deg: float | None
    roughness_m: float | None
    submerged_pct: float
    nodata_pct: float
    stride: int
    water_m: float | None
    water_below_m: float | None
    cave_pct: float


class SiteCandidate(TypedDict):
    name: str
    kind: str
    machines: int
    bbox_m: list[float] | None


class SiteBuilt(TypedDict):
    """What counts as built for the plan with its pad here: ``mode`` is auto, picked, world or
    none, empty when the plan does not solve."""

    mode: str
    confidence: str
    figure: str
    where: str
    hint: str
    area: str
    built: int | None
    total: int
    current: int
    count: int
    stage_text: str
    candidates: list[SiteCandidate]


class SiteLoss(TypedDict):
    now: int
    here: int
    total: int
    text: str


class SiteValue(TypedDict):
    """A ``site`` op value, ready to push."""

    schema: int
    origin_m: list[float | None]
    yaw_deg: float
    footprint_m: list[float]
    footprint_source: str
    origin_label: str
    when: str


class SiteFit(TypedDict):
    name: str
    machines: int
    value: SiteValue


class SitePreviewResponse(TypedDict):
    """One candidate pad. ``now`` is the stored site's figure; ``nodes`` and ``content_bbox_m``
    come only with ``first=1``. Outside the map square only ``where`` is filled."""

    key: str
    rev: int
    save_id: str
    token: str
    x_m: float
    y_m: float
    yaw_deg: float
    w_m: float
    d_m: float
    source: str
    sited: bool
    in_map: bool
    in_content: bool
    region: str
    z_m: float | None
    z_note: str
    terrain: SiteTerrain | None
    terrain_note: str
    slabs: list[str]
    on_pad: int
    planned: int
    trunks: list[SiteTrunk]
    placeless: list[str]
    built: SiteBuilt
    now: SiteBuilt
    basis: str
    loses: SiteLoss | None
    fits: list[SiteFit]
    overlaps: list[str]
    nodes: list[SitePreviewNode] | None
    content_bbox_m: list[float] | None
    failure: str


def _preview_session(st, state, biomass: bool, headroom: str, token: str) -> site_preview.Session:
    key = (st.world_id, state.key, state.rev, token, biomass, headroom)
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
) -> Any:
    """A plan version's pad at (x, y, yaw, w × d), every part omitted taken from its stored
    site, else from where a first placement starts. ``full`` reads the terrain at 1 m."""
    check_plan_key(key)
    st = require_world(request, save, world)
    state = require_plan(plan_log(st), key, rev)
    token = pin.check(st.header, None)
    session = _preview_session(st, state, biomass == "include", headroom, token)
    base = siting.parse(state) or site_preview.start_siting(st.game, st, session)
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
        first=first,
    )
    if unread and out["in_map"]:
        out["terrain_note"] = site_preview.NOT_READ
    out["token"] = token
    return out

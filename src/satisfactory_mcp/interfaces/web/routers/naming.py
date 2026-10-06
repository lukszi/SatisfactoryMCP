"""``/api/factories/candidates`` and ``/api/labels``: detect, name, rename, amend, forget.

Drawing one factory's graph and machines is ``factory_graph.py``.

Every write goes through ``domain/factories/edits.py``, as the MCP tools do, past the write
guard (``guard.py``), and carries the label-store version it was read at. A name is the rest
of the path (``{name:path}``), so a name holding a ``/`` from before that was refused can
still be renamed or forgotten. A refused write says why in flags, not only in words.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....domain.factories import edits, fed, flowgraph, naming
from ....domain.factories import identity as fidentity
from ....domain.factories.labels import (
    BadName,
    LabelError,
    NameClash,
    StaleStore,
    UnknownLabel,
    stamp,
)
from ....domain.factories.query import build_view
from ....domain.factories.select import SelectorError, select_machines
from ....domain.planning import journal
from ....domain.planning.planlog import Actor
from ....domain.spatial import geo
from ....domain.spatial import regions as spatial_regions
from ....domain.world import pin
from ..serial import (
    Flow,
    MachineSpot,
    busy_response,
    cm_to_m,
    error_response,
    flow_json,
    machine_spots,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class Amount(TypedDict):
    name: str
    count: int


class CandidateRow(TypedDict):
    """``region`` is null in the sea and off the map; ``selector`` is what the MCP tools take.

    ``confident`` is false when the suggestion leads with something that is not a product.
    """

    index: int
    selector: str
    machines: int
    fed: str
    products: list[Flow]
    intermediates: list[Flow]
    sunk: list[Flow]
    unrouted: list[Flow]
    inputs: list[Flow]
    buffers: int
    buildings: list[Amount]
    region: str | None
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    spread_m: float
    score: float
    suggested_name: str
    confident: bool


class Hidden(TypedDict):
    small: int
    not_fed: int


class CandidatesResponse(TypedDict):
    """``token`` goes back as ``as_of`` and ``version`` as ``version`` when naming."""

    token: str
    version: int
    style: str
    min_machines: int
    fed_only: bool
    named: int
    hidden: Hidden
    candidates: list[CandidateRow]


class NamedResponse(TypedDict):
    name: str
    machines: int
    overlaps: list[str]
    version: int
    stored_in: str


class ForgotResponse(TypedDict):
    name: str
    machines: int
    version: int
    stored_in: str


class RenamedResponse(TypedDict):
    """``plans`` followed the new name; ``plans_stuck`` could not and still name ``was``,
    for the reason in ``stuck_reason``. The label itself is renamed either way."""

    name: str
    was: str
    machines: int
    plans: list[str]
    plans_stuck: list[str]
    stuck_reason: str
    version: int
    stored_in: str


class LabelErrorResponse(TypedDict):
    """A label write refused before it reached the store: a bad name (400) or a save, proposal
    or label that does not exist (404)."""

    error: str


class LabelRefusedResponse(TypedDict):
    """A label write that changed nothing. One flag names the cause: ``stale`` (the store
    moved since ``version``), ``name_taken`` (another label holds the name) or ``pin`` (the
    save moved since ``as_of``)."""

    error: str
    stale: bool
    name_taken: bool
    pin: bool


_REFUSALS: dict[int | str, dict[str, Any]] = {
    400: {"model": LabelErrorResponse},
    404: {"model": LabelErrorResponse},
    409: {"model": LabelRefusedResponse},
}


def _flows(fg: flowgraph.FlowGraph, role: str) -> list[dict]:
    return [flow_json(fg, item, rate) for item, rate in fg.listed(role)]


def _cluster(st, machines: list[str], name: str):
    view = build_view(name, machines, st.graph, st.game, st.projection)
    return view, flowgraph.build(st, st.game, view)


def _conflict(exc: Exception, **flag: bool) -> JSONResponse:
    body = {"error": str(exc), "stale": False, "name_taken": False, "pin": False}
    body.update(flag)
    return JSONResponse(body, status_code=409)


def _refused(exc: Exception) -> Any:
    if isinstance(exc, StaleStore):
        return _conflict(exc, stale=True)
    if isinstance(exc, NameClash):
        return _conflict(exc, name_taken=True)
    if isinstance(exc, BadName):
        return error_response(str(exc), 400)
    if isinstance(exc, UnknownLabel):
        return error_response(str(exc), 404)
    if isinstance(exc, LabelError):
        return _conflict(exc)
    return busy_response("factory labels", exc)


@router.get("/factories/candidates", response_model=CandidatesResponse)
def factory_candidates(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    style: str = naming.DEFAULT_STYLE,
    min_machines: int = 1,
    fed_only: bool = False,
) -> Any:
    """Every proposal the player has not named, filtered, each with a suggested name."""
    if style not in naming.STYLES:
        return error_response(f"unknown style “{style}”; known: {', '.join(naming.STYLES)}", 400)
    if min_machines < 1:
        return error_response("min_machines is at least 1", 400)
    st = require_world(request, save, world)
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError:
        rmap = None

    placed = fidentity.positions(st.projection)
    names = naming.proposal_names(st, st.proposals, style, rmap)
    hidden = {"small": 0, "not_fed": 0}
    rows = []
    for index, pr in enumerate(st.proposals):
        if st.labels.covers(pr.machines):
            continue
        if pr.size < min_machines:
            hidden["small"] += 1
            continue
        verdict = fed.feeding(st, st.game, pr.machines)
        if fed_only and verdict == fed.NOT_FED:
            hidden["not_fed"] += 1
            continue
        cand = fidentity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        view, fg = _cluster(st, pr.machines, "proposal")
        extracted = {row[1] for row in view.nodes}
        _item, confident = naming.lead(fg, cand, st.game, extracted)
        region = rmap.label_for(*cand.centroid).name if rmap else None
        box = geo.bbox([placed[m][:2] for m in pr.machines if m in placed])
        rows.append(
            {
                "index": index,
                "selector": f"proposal:{index}",
                "machines": pr.size,
                "fed": verdict,
                "products": _flows(fg, "product"),
                "intermediates": _flows(fg, "intermediate"),
                "sunk": _flows(fg, "sunk"),
                "unrouted": _flows(fg, "unrouted"),
                "inputs": _flows(fg, "input"),
                "buffers": len(fg.buffers),
                "buildings": [
                    {"name": st.game.building_name(k) or k, "count": v}
                    for k, v in cand.buildings.most_common()
                ],
                "region": region,
                "centroid_m": [cm_to_m(cand.centroid[0]), cm_to_m(cand.centroid[1])],
                "bbox_m": None if box is None else [cm_to_m(v) for v in box],
                "spread_m": round(cand.spread_m, 1),
                "score": round(pr.cohesion, 3),
                "suggested_name": names[index],
                "confident": confident,
            }
        )
    return {
        "token": pin.check(st.header, None),
        "version": st.labels.version,
        "style": style,
        "min_machines": min_machines,
        "fed_only": fed_only,
        "named": len(st.labels.labels),
        "hidden": hidden,
        "candidates": rows,
    }


def _session(st) -> str:
    return st.header.get("session_name") or ""


@router.post("/labels", response_model=NamedResponse, responses=_REFUSALS)
def name_candidate(
    request: Request,
    name: str = Body(),
    proposal: int = Body(),
    as_of: str = Body(),
    version: int = Body(),
    notes: str = Body(""),
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Name one proposal as a new factory. 409 for a taken name, a moved save or store."""
    st = require_world(request, save, world)
    try:
        pin.check(st.header, as_of)
    except pin.PinRefused as exc:
        return _conflict(exc, pin=True)
    try:
        picked = select_machines([f"proposal:{proposal}"], st)
    except SelectorError as exc:
        return error_response(str(exc), 404)
    if not picked:
        return error_response("that unnamed cluster matched no machines; detect again", 404)
    cand = fidentity.describe(picked, st.graph, st.game, st.projection, "label")
    overlaps = st.labels.overlaps(picked, name)
    try:
        label, _held, written = edits.name(
            st.world_id,
            _session(st),
            name,
            cand,
            notes=notes,
            when=stamp(st.header),
            create=True,
            expect=version,
        )
    except (StaleStore, LabelError, LockTimeout) as exc:
        return _refused(exc)
    return {
        "name": label.name,
        "machines": len(label.anchors),
        "overlaps": [f"overlaps {k!r} on {n} machine(s)" for k, n in overlaps.items()],
        "version": written,
        "stored_in": str(st.labels.path_for(st.world_id)),
    }


@router.patch("/labels/{name:path}", response_model=RenamedResponse, responses=_REFUSALS)
def rename_label(
    request: Request,
    name: str,
    to: str = Body(),
    version: int = Body(),
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Rename a label by its exact name; its machines stay and plans scoped to it follow."""
    st = require_world(request, save, world)
    page = Actor("page", "", os.getpid())
    try:
        done = edits.rename(
            st.world_id, _session(st), name, to, actor=page, exact=True, expect=version
        )
    except (StaleStore, LabelError, LockTimeout) as exc:
        return _refused(exc)
    journal.append(
        st.world_id,
        "label.rename",
        actor=page,
        args={"was": done.was, "to": done.name},
        text=f"renamed factory “{done.was}” to “{done.name}”",
    )
    return {
        "name": done.name,
        "was": done.was,
        "machines": done.machines,
        "plans": done.plans,
        "plans_stuck": done.stuck,
        "stuck_reason": done.why,
        "version": done.version,
        "stored_in": str(done.path),
    }


@router.delete("/labels/{name:path}", response_model=ForgotResponse, responses=_REFUSALS)
def forget_label(
    request: Request,
    name: str,
    version: int,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Delete a label by its exact name. The machines are untouched."""
    st = require_world(request, save, world)
    try:
        label, written = edits.forget(st.world_id, _session(st), name, exact=True, expect=version)
    except (StaleStore, LabelError, LockTimeout) as exc:
        return _refused(exc)
    return {
        "name": label.name,
        "machines": len(label.anchors),
        "version": written,
        "stored_in": str(st.labels.path_for(st.world_id)),
    }


class AmendedResponse(TypedDict):
    """``added`` and ``dropped`` are what the area changes; ``written`` is false on a dry run
    and when nothing changed. ``version`` is the store's, after any write."""

    name: str
    dry_run: bool
    written: bool
    before: int
    after: int
    added: list[MachineSpot]
    dropped: list[MachineSpot]
    overlaps: list[str]
    token: str
    version: int


AMEND_MODES = ("add", "drop")


class AmendBody(TypedDict):
    """``area`` is a polygon of ``[x_m, y_m]`` corners and ``extra_areas`` any further ones; a
    machine inside any of them counts. ``mode`` is ``add`` or ``drop``."""

    name: str
    area: list[tuple[float, float]]
    extra_areas: NotRequired[list[list[tuple[float, float]]]]
    mode: str
    as_of: str
    version: int
    dry_run: NotRequired[bool]


@router.post("/labels/amend", response_model=AmendedResponse, responses=_REFUSALS)
def amend_label(
    request: Request,
    body: Annotated[AmendBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Add the machines inside ``area`` or ``extra_areas`` (metres, map frame) to a label, or
    drop them from it.

    The same ``plan_amend`` and ``amend`` as ``amend_factory``. A dry run changes nothing.
    """
    name, dry_run = body["name"], body.get("dry_run", False)
    if body["mode"] not in AMEND_MODES:
        return error_response(f"mode is one of {', '.join(AMEND_MODES)}", 400)
    areas = [body["area"], *body.get("extra_areas", [])]
    if any(len(a) < 3 for a in areas):
        return error_response("an area needs at least three corners", 400)
    st = require_world(request, save, world)
    try:
        pin.check(st.header, body["as_of"])
    except pin.PinRefused as exc:
        return _conflict(exc, pin=True)
    label = next((x for x in st.labels.labels if x.name == name), None)
    if label is None:
        return error_response(f"no factory named “{name}” in this world", 404)
    alive = set(st.graph.machines())
    placed = fidentity.positions(st.projection)
    polygons = [[(x * geo.CM_PER_M, y * geo.CM_PER_M) for x, y in a] for a in areas]
    within = sorted(
        m
        for m in alive
        if m in placed and any(geo.inside(placed[m][:2], corners) for corners in polygons)
    )
    wanted, going = (within, set()) if body["mode"] == "add" else ([], set(within))
    try:
        plan = edits.plan_amend(st.labels, label, wanted, going, alive)
    except LabelError as exc:
        return _refused(exc)
    reply = {
        "name": label.name,
        "dry_run": dry_run,
        "written": False,
        "before": len(plan.before),
        "after": len(plan.after),
        "added": machine_spots(st, plan.added),
        "dropped": machine_spots(st, plan.dropped),
        "overlaps": [f"overlaps {k!r} on {n} machine(s)" for k, n in plan.overlaps.items()],
        "token": pin.check(st.header, None),
        "version": st.labels.version,
    }
    if dry_run or not (plan.added or plan.dropped):
        return reply
    cand = fidentity.describe(plan.standing, st.graph, st.game, st.projection, "label")
    try:
        _label, written = edits.amend(
            st.world_id,
            _session(st),
            label.name,
            wanted,
            going,
            cand=cand if plan.standing else None,
            when=stamp(st.header),
            expect=body["version"],
        )
    except (StaleStore, LabelError, LockTimeout) as exc:
        return _refused(exc)
    reply.update(written=True, version=written)
    return reply

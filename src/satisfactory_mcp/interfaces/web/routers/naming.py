"""``/api/factories/candidates``, ``/graph`` and ``/api/labels``: detect, draw, name, rename, forget.

Every write goes through ``domain/factories/edits.py``, as the MCP tools do, past the write
guard (``guard.py``), and carries the label-store version it was read at.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from fastapi import APIRouter, Body, Request

from ....core.filelock import LockTimeout
from ....domain.factories import edits, fed, flowgraph, naming
from ....domain.factories import identity as fidentity
from ....domain.factories.labels import LabelError, StaleStore, stamp
from ....domain.factories.query import build_view
from ....domain.factories.select import SelectorError, select_machines
from ....domain.planning.planlog import Actor
from ....domain.spatial import geo
from ....domain.spatial import regions as spatial_regions
from ....domain.world import pin
from ..serial import _fail, _m, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


class Amount(TypedDict):
    name: str
    count: int


class Flow(TypedDict):
    """Items per minute at nameplate: made, or for an input consumed. ``to`` is where the
    output physically ends up (``storage``, ``export``, ``sink``, ``nowhere``)."""

    name: str
    per_min: float
    to: list[str]


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
    name: str
    was: str
    machines: int
    plans: list[str]
    version: int
    stored_in: str


def _flows(fg: flowgraph.FlowGraph, role: str) -> list[dict]:
    return [
        {"name": item, "per_min": round(rate, 2), "to": sorted(fg.destinations.get(item, ()))}
        for item, rate in fg.listed(role)
    ]


def _cluster(st, machines: list[str], name: str):
    view = build_view(name, machines, st.graph, st.game, st.projection)
    return view, flowgraph.build(st, st.game, view)


def _refused(exc: Exception) -> Any:
    if isinstance(exc, StaleStore | LabelError):
        return _fail(str(exc), 409)
    return _fail(str(exc), 503)


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
        return _fail(f"unknown style {style!r}; known: {', '.join(naming.STYLES)}", 400)
    if min_machines < 1:
        return _fail("min_machines is at least 1", 400)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError:
        rmap = None

    placed = fidentity.positions(st.projection)
    taken = [label.name for label in st.labels.labels]
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
        item, confident = naming.lead(fg, cand, st.game, extracted)
        region = rmap.label_for(*cand.centroid).name if rmap else None
        suggested = naming.suggest(item, region, taken, style)
        taken.append(suggested)
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
                "centroid_m": [_m(cand.centroid[0]), _m(cand.centroid[1])],
                "bbox_m": None if box is None else [_m(v) for v in box],
                "spread_m": round(cand.spread_m, 1),
                "score": round(pr.cohesion, 3),
                "suggested_name": suggested,
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


@router.post("/labels", response_model=NamedResponse)
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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        pin.check(st.header, as_of)
    except pin.PinRefused as exc:
        return _fail(str(exc), 409)
    try:
        picked = select_machines([f"proposal:{proposal}"], st)
    except SelectorError as exc:
        return _fail(str(exc), 404)
    if not picked:
        return _fail(f"proposal:{proposal} matched no machines", 404)
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


@router.patch("/labels/{name}", response_model=RenamedResponse)
def rename_label(
    request: Request,
    name: str,
    to: str = Body(),
    version: int = Body(),
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Rename a label by its exact name; its machines stay and plans scoped to it follow."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        done = edits.rename(
            st.world_id,
            _session(st),
            name,
            to,
            exact=True,
            expect=version,
            actor=Actor("page", "", os.getpid()),
        )
    except (StaleStore, LabelError, LockTimeout) as exc:
        return _refused(exc)
    return {
        "name": done.name,
        "was": done.was,
        "machines": done.machines,
        "plans": done.plans,
        "version": done.version,
        "stored_in": str(done.path),
    }


@router.delete("/labels/{name}", response_model=ForgotResponse)
def forget_label(
    request: Request,
    name: str,
    version: int,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Delete a label by its exact name. The machines are untouched."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
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


class GraphNode(TypedDict):
    """``kind`` is ``group`` (machines on one recipe), ``input``, or a terminal: ``storage``,
    ``export``, ``sink``, ``nowhere``. Counts and ``bbox_m`` are for groups only."""

    id: str
    kind: str
    label: str
    detail: str
    machines: int
    clock: float | None
    makes: list[Flow]
    running: int
    blocked: int
    stopped: int
    bbox_m: tuple[float, float, float, float] | None


class GraphEdge(TypedDict):
    """``per_min`` is null where an output reaches a terminal with no surplus to apportion."""

    source: str
    target: str
    item: str
    per_min: float | None


class FactoryGraphResponse(TypedDict):
    title: str
    token: str
    buffers: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]


TERMINAL_LABELS = {
    "storage": "to storage",
    "export": "leaves the cluster",
    "sink": "AWESOME Sink",
    "nowhere": "goes nowhere",
}


def _bare(key: str, kind: str, label: str, detail: str) -> dict:
    return {
        "id": key,
        "kind": kind,
        "label": label,
        "detail": detail,
        "machines": 0,
        "clock": None,
        "makes": [],
        "running": 0,
        "blocked": 0,
        "stopped": 0,
        "bbox_m": None,
    }


@router.get("/factories/graph", response_model=FactoryGraphResponse)
def factory_graph(
    request: Request,
    factory: str | None = None,
    candidate: str | None = None,
    token: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """The recipe-group production graph of a named factory, or of a detected candidate.

    A candidate is its ``proposal:N`` selector plus the ``token`` it was detected at; a save
    written since then is refused (409), since the index may now name another cluster.
    """
    if bool(factory) == bool(candidate):
        return _fail("pass exactly one of factory= or candidate=", 400)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    if factory:
        label = next((x for x in st.labels.labels if x.name == factory), None)
        if label is None:
            return _fail(f"no factory named {factory!r}", 404)
        alive = set(st.graph.machines())
        machines, title = [m for m in label.anchors if m in alive], label.name
    else:
        if not token:
            return _fail("candidate= needs the token= it was detected at", 400)
        try:
            pin.check(st.header, token)
        except pin.PinRefused as exc:
            return _fail(str(exc), 409)
        try:
            machines = select_machines([candidate], st)
        except SelectorError as exc:
            return _fail(str(exc), 404)
        title = candidate
    _view, fg = _cluster(st, machines, title)
    placed = fidentity.positions(st.projection)
    nodes: list[dict] = []
    for g in fg.groups.values():
        box = geo.bbox([placed[m][:2] for m in g.machines if m in placed])
        nodes.append(
            {
                "id": g.key,
                "kind": "group",
                "label": f"{len(g.machines)}× {g.building}",
                "detail": g.recipe,
                "machines": len(g.machines),
                "clock": round(sum(g.clocks) / len(g.clocks), 3) if g.clocks else None,
                "makes": [
                    {"name": k, "per_min": round(v, 2), "to": sorted(fg.destinations.get(k, ()))}
                    for k, v in sorted(g.makes.items(), key=lambda kv: -kv[1])
                ],
                "running": g.states["running"],
                "blocked": g.states["blocked"],
                "stopped": g.states["stopped"],
                "bbox_m": None if box is None else [_m(v) for v in box],
            }
        )
    used = {e.source for e in fg.edges} | {e.target for e in fg.edges}
    for key in sorted(k for k in used if k.startswith("in:")):
        nodes.append(_bare(key, "input", key[3:], "enters the cluster"))
    for kind, text in TERMINAL_LABELS.items():
        if kind in used:
            nodes.append(_bare(kind, kind, text, f"{fg.terminals.get(kind, 0)} reached"))
    return {
        "title": title,
        "token": pin.check(st.header, None),
        "buffers": len(fg.buffers),
        "nodes": nodes,
        "edges": [
            {"source": e.source, "target": e.target, "item": e.item, "per_min": e.per_min}
            for e in fg.edges
        ],
    }

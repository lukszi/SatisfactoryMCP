"""``/api/factories/graph`` and ``/api/factories/machines``: one factory's machines, drawn.

Both answer for a named factory or for a detected candidate (its ``proposal:N`` selector plus
the ``token`` it was detected at): the graph as recipe groups and terminals, the machines as
spots on the map. Naming, renaming and amending are ``naming.py``.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, NamedTuple, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import flowgraph
from ....domain.factories import identity as fidentity
from ....domain.factories.query import build_view
from ....domain.factories.select import SelectorError, select_machines
from ....domain.spatial import geo
from ....domain.world import pin
from ....domain.world.state import WorldState
from ..serial import (
    Flow,
    MachineSpot,
    RequestRefused,
    cm_to_m,
    flow_json,
    machine_spots,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class GraphNode(TypedDict):
    """``kind`` is ``group`` (machines on one recipe), ``input``, or a terminal: ``storage``,
    ``export``, ``sink``, ``nowhere``. Counts, ``states`` (machines per health state) and ``bbox_m``
    are for groups only."""

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
    states: dict[str, int]
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
    "export": "leaves the {}",
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
        "states": {},
        "bbox_m": None,
    }


class PickedMachines(NamedTuple):
    state: WorldState
    machines: list[str]
    title: str


def _picked_machines(
    request: Request,
    factory: str | None,
    candidate: str | None,
    token: str | None,
    save: str | None,
    world: str | None,
) -> PickedMachines:
    """The standing machines of a named factory, or of a candidate detected at ``token``."""
    if bool(factory) == bool(candidate):
        raise RequestRefused("pass exactly one of factory= or candidate=", 400)
    st = require_world(request, save, world)
    if factory:
        label = next((x for x in st.labels.labels if x.name == factory), None)
        if label is None:
            raise RequestRefused(f"no factory named “{factory}” in this world", 404)
        alive = set(st.graph.machines())
        return PickedMachines(st, [m for m in label.anchors if m in alive], label.name)
    if not token:
        raise RequestRefused("candidate= needs the token= it was detected at", 400)
    try:
        pin.check(st.header, token)
    except pin.PinRefused as exc:
        raise RequestRefused(
            "a newer save was written since this was detected; detect again", 409
        ) from exc
    try:
        return PickedMachines(st, select_machines([candidate], st), candidate)
    except SelectorError as exc:
        raise RequestRefused(str(exc), 404) from exc


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
    st, machines, title = _picked_machines(request, factory, candidate, token, save, world)
    fg = flowgraph.build(st, st.game, build_view(title, machines, st.graph, st.game, st.projection))
    whole = "factory" if factory else "cluster"
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
                    flow_json(fg, k, v) for k, v in sorted(g.makes.items(), key=lambda kv: -kv[1])
                ],
                "running": g.states["running"],
                "blocked": g.states["blocked"],
                "stopped": g.states["stopped"],
                "states": dict(sorted(g.health.items())),
                "bbox_m": None if box is None else [cm_to_m(v) for v in box],
            }
        )
    used = {e.source for e in fg.edges} | {e.target for e in fg.edges}
    for key in sorted(k for k in used if k.startswith("in:")):
        nodes.append(_bare(key, "input", key[3:], f"enters the {whole}"))
    for kind, text in TERMINAL_LABELS.items():
        if kind in used:
            nodes.append(
                _bare(kind, kind, text.format(whole), f"{fg.terminals.get(kind, 0)} reached")
            )
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


class FactoryMachinesResponse(TypedDict):
    title: str
    token: str
    machines: list[MachineSpot]


@router.get("/factories/machines", response_model=FactoryMachinesResponse)
def factory_machines(
    request: Request,
    factory: str | None = None,
    candidate: str | None = None,
    token: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Where each standing machine of a named factory, or of a detected candidate, stands."""
    st, machines, title = _picked_machines(request, factory, candidate, token, save, world)
    return {
        "title": title,
        "token": pin.check(st.header, None),
        "machines": machine_spots(st, machines),
    }

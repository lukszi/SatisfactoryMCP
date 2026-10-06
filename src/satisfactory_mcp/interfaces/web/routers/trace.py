"""``/api/trace``: ``trace_upstream`` as map geometry, with items and rates along the path.

The walk is ``domain.factories.trace``; the seed grammar is ``resolve_seeds``, the same one
the MCP tool takes. Rates are ``flowgraph.build`` over the seeds and everything reached, and
states are ``health.assess``. docs/frontend_vision.md §10. Wire rules: docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import ports
from ....core.saveio import rows as saverows
from ....domain.factories import flowgraph, health
from ....domain.factories import identity as fidentity
from ....domain.factories.query import build_view
from ....domain.factories.select import SelectorError
from ....domain.factories.trace import resolve_seeds, trace
from ....domain.spatial import geo
from ....domain.world import pin
from ..serial import cm_to_m, error_response, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")

PointM = tuple[float, float]


class TraceRate(TypedDict):
    item: str
    per_min: float


class TraceMachine(TypedDict):
    """``kind`` is ``extractor``, ``generator`` or ``production``; ``hops`` is 0 on a seed.
    Rates are nameplate at the machine's clock. ``x_m``/``y_m`` are null for an unplaced
    record."""

    instance: str
    name: str
    kind: str
    seed: bool
    hops: int
    recipe: str | None
    makes: list[TraceRate]
    uses: list[TraceRate]
    state: str
    actionable: bool
    x_m: float | None
    y_m: float | None


class TraceRun(TypedDict):
    """One conduit run the walk crossed; ``ident`` is empty where the run carries no id."""

    ident: str
    medium: Literal["belt", "pipe"]
    pieces: int
    lines_m: list[list[PointM]]


class TraceGroup(TypedDict):
    id: str
    label: str
    detail: str
    machines: int
    running: int
    blocked: int
    stopped: int
    makes: list[TraceRate]


class TraceEdge(TypedDict):
    """Group to group, ``in:<item>`` for supply from outside the traced set, or a terminal
    (``storage``, ``export``, ``sink``, ``nowhere``). ``per_min`` null: nothing to share."""

    source: str
    target: str
    item: str
    per_min: float | None


class TraceResponse(TypedDict):
    """``items`` is what the reached machines make (up) or use (down), seeds excluded."""

    seed: str
    subject: str
    direction: Literal["up", "down"]
    token: str
    visited: int
    deepest: int
    ambiguous: int
    truncated: bool
    seeds: int
    bbox_m: tuple[float, float, float, float] | None
    items: list[TraceRate]
    machines: list[TraceMachine]
    runs: list[TraceRun]
    groups: list[TraceGroup]
    edges: list[TraceEdge]


def _rates(values: dict[str, float]) -> list[dict]:
    return [
        {"item": k, "per_min": round(v, 2)}
        for k, v in sorted(values.items(), key=lambda kv: -kv[1])
    ]


def _kind(game, cls: str) -> str:
    building = game.buildings.get(cls)
    if building is not None and building.is_extractor:
        return "extractor"
    if building is not None and building.is_generator:
        return "generator"
    return "production"


def _runs(st, nodes: set[str]) -> list[dict]:
    actors = (st.projection.get("graph") or {}).get("actors") or []
    run_of = st.physical.run_of
    runs: dict[int, dict] = {}
    segments = [(ports.CONVEYOR, s) for s in saverows.iter_belt_segments(st.projection)]
    segments += [(ports.PIPE, s) for s in saverows.iter_pipe_segments(st.projection)]
    for medium, seg in segments:
        if not 0 <= seg.actor_index < len(actors):
            continue
        actor = actors[seg.actor_index]
        if actor not in nodes or len(seg.points) < 2:
            continue
        link = run_of.get(actor)
        key = id(link) if link is not None else -seg.actor_index - 1
        run = runs.get(key)
        if run is None:
            run = runs[key] = {
                "ident": link.ident if link is not None else "",
                "medium": "pipe" if medium == ports.PIPE else "belt",
                "pieces": link.pieces if link is not None else 1,
                "lines_m": [],
            }
        run["lines_m"].append([[cm_to_m(p[0]), cm_to_m(p[1])] for p in seg.points])
    return list(runs.values())


@router.get("/trace", response_model=TraceResponse)
def trace_path(
    request: Request,
    seed: str,
    direction: str = "up",
    as_of: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What feeds a machine, a building type or a factory (``up``), or what it feeds (``down``)."""
    way = direction.strip().casefold()
    if way not in ("up", "down"):
        return error_response(f"unknown direction “{direction}”: up or down", 400)
    st = require_world(request, save, world)
    try:
        token = pin.check(st.header, as_of)
    except pin.PinRefused:
        return error_response("a newer save was written since this was traced; trace again", 409)
    try:
        seeds, subject = resolve_seeds(st, st.game, seed)
    except SelectorError as exc:
        return error_response(str(exc), 404)
    if not seeds:
        return error_response(f"nothing matches “{seed}”", 404)

    walked = trace(st, st.game, seeds, way)
    reached = {r.instance: r for r in walked.reached}
    members = list(dict.fromkeys([*seeds, *reached]))
    view = build_view(subject, members, st.graph, st.game, st.projection)
    fg = flowgraph.build(st, st.game, view)
    verdicts = {
        m.instance: m
        for m in health.assess(subject, members, st.game, st.projection, st.graph).machines
    }
    placed = fidentity.positions(st.projection)
    seed_set = set(seeds)

    totals: dict[str, float] = {}
    machines = []
    for row in view.machines:
        hit = reached.get(row.instance)
        verdict = verdicts.get(row.instance)
        state = verdict.state if verdict else "unmonitored"
        if hit is not None and row.instance not in seed_set:
            for item, rate in (row.makes if way == "up" else row.uses).items():
                totals[item] = totals.get(item, 0.0) + rate
        at = placed.get(row.instance)
        machines.append(
            {
                "instance": row.instance,
                "name": st.game.building_name(row.building) or row.building,
                "kind": hit.kind if hit else _kind(st.game, row.building),
                "seed": row.instance in seed_set,
                "hops": hit.hops if hit and row.instance not in seed_set else 0,
                "recipe": row.recipe or None,
                "makes": _rates(row.makes),
                "uses": _rates(row.uses),
                "state": state,
                "actionable": state in health.ACTIONABLE,
                "x_m": cm_to_m(at[0]) if at else None,
                "y_m": cm_to_m(at[1]) if at else None,
            }
        )
    machines.sort(key=lambda r: (not r["seed"], r["hops"], r["name"]))
    box = geo.bbox([placed[m][:2] for m in members if m in placed])
    return {
        "seed": seed,
        "subject": subject,
        "direction": way,
        "token": token,
        "visited": walked.visited,
        "deepest": walked.deepest,
        "ambiguous": walked.ambiguous,
        "truncated": walked.truncated,
        "seeds": len(seeds),
        "bbox_m": None if box is None else [cm_to_m(v) for v in box],
        "items": _rates(totals),
        "machines": machines,
        "runs": _runs(st, walked.nodes),
        "groups": [
            {
                "id": g.key,
                "label": f"{len(g.machines)}× {g.building}",
                "detail": g.recipe,
                "machines": len(g.machines),
                "running": g.states["running"],
                "blocked": g.states["blocked"],
                "stopped": g.states["stopped"],
                "makes": _rates(dict(g.makes)),
            }
            for g in fg.groups.values()
        ],
        "edges": [
            {"source": e.source, "target": e.target, "item": e.item, "per_min": e.per_min}
            for e in fg.edges
        ],
    }

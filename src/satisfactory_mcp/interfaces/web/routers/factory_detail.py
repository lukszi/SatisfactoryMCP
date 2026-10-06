"""``/api/factories/aspects`` and ``/api/factories/sites``: the factory detail page's reads.

``aspects`` is ``factory_query`` as rows: one ``build_view`` pass, every aspect at once,
since the view computes them together. ``sites`` is ``factory_sites``, with each site's
share of one factory when ``?factory=`` names one. Wire rules: docs/web-wire.md.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import identity as fidentity
from ....domain.factories.query import build_view
from ....domain.spatial import nodes as nodes_mod
from ..serial import (
    bbox_m,
    cm_to_m,
    error_response,
    instance_leaf,
    point_m,
    require_world,
    standing_anchors,
    xyz_m,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class AspectPower(TypedDict):
    draw_mw: float
    measured_draw_mw: float
    generation_mw: float
    unmonitored: int


class AspectBalance(TypedDict):
    """``measured_net`` is null where every machine touching the item keeps no monitor."""

    item: str
    made: float
    used: float
    net: float
    measured_net: float | None
    verdict: str
    unmonitored_made: float
    unmonitored_used: float


class AspectMachine(TypedDict):
    instance: str
    building: str
    recipe: str | None
    clock: float
    paused: bool
    x_m: float | None
    y_m: float | None
    z_m: float | None


class AspectCount(TypedDict):
    name: str
    count: int


class AspectNode(TypedDict):
    """``left`` is the save's ``mResourcesLeft``, null on an infinite node."""

    node: str
    resource: str
    purity: str
    extractor: str
    clock: float
    left: float | None
    x_m: float | None
    y_m: float | None


class AspectLink(TypedDict):
    """``factory`` is null for machines no label covers."""

    factory: str | None
    machines: int


class AspectIssue(TypedDict):
    """``machine`` is the instance the issue is about, for a ``machine:`` selector; null when
    the issue names none."""

    text: str
    machine: str | None


def _issue(line: str, building_name) -> AspectIssue:
    head, sep, rest = line.partition(": ")
    cls, found, _tail = head.rpartition("_C_")
    if not sep or not found:
        return {"text": line, "machine": None}
    return {"text": f"{building_name(cls + '_C')}: {rest}", "machine": head}


class FactoryAspectsResponse(TypedDict):
    name: str
    size: int
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    spread_m: float
    producers: int
    unmonitored_producers: int
    producing_now: int
    power: AspectPower
    balance: list[AspectBalance]
    machines: list[AspectMachine]
    recipes: list[AspectCount]
    buildings: list[AspectCount]
    nodes: list[AspectNode]
    links: list[AspectLink]
    issues: list[AspectIssue]


def _label_machines(st, factory: str) -> tuple[str, list[str]] | None:
    label = next((x for x in st.labels.labels if x.name == factory), None)
    if label is None:
        return None
    return label.name, standing_anchors(st, label)


def _node_places() -> dict[str, tuple[float, float]]:
    try:
        table = nodes_mod.load_nodes().nodes
    except FileNotFoundError:
        return {}
    return {instance_leaf(n["instance"]): (n["x"], n["y"]) for n in table}


@router.get("/factories/aspects", response_model=FactoryAspectsResponse)
def factory_aspects(
    request: Request,
    factory: str,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What one named factory makes, needs, draws, holds and touches.

    Rates are items/min at the saved clocks, nameplate and measured, never blended.
    """
    st = require_world(request, save, world)
    found = _label_machines(st, factory)
    if found is None:
        return error_response(f"no factory named “{factory}” in this world", 404)
    name, machines = found
    game = st.game
    view = build_view(name, machines, st.graph, game, st.projection, st.labels)
    placed = fidentity.positions(st.projection)
    places = _node_places() if view.nodes else {}

    def building_name(cls: str) -> str:
        return game.building_name(cls) or cls

    return {
        "name": name,
        "size": view.size,
        "centroid_m": point_m(view.centroid),
        "bbox_m": bbox_m(placed, machines),
        "spread_m": round(view.spread_m, 1),
        "producers": view.producers,
        "unmonitored_producers": view.unmonitored_producers,
        "producing_now": view.producing_now,
        "power": {
            "draw_mw": round(view.draw_mw, 2),
            "measured_draw_mw": round(view.measured_draw_mw, 2),
            "generation_mw": round(view.generation_mw, 2),
            "unmonitored": view.unmonitored,
        },
        "balance": [
            {
                **row,
                "made": round(row["made"], 3),
                "used": round(row["used"], 3),
                "net": round(row["net"], 3),
                "measured_net": (
                    None if row["measured_net"] is None else round(row["measured_net"], 3)
                ),
                "unmonitored_made": round(view.flows[row["item"]]["unmonitored_produced"], 3),
                "unmonitored_used": round(view.flows[row["item"]]["unmonitored_consumed"], 3),
            }
            for row in view.balance()
        ],
        "machines": [
            {
                "instance": m.instance,
                "building": building_name(m.building),
                "recipe": m.recipe or None,
                "clock": round(m.clock, 4),
                "paused": m.paused,
                **xyz_m(m.pos if any(m.pos) else None),
            }
            for m in sorted(view.machines, key=lambda x: (building_name(x.building), x.recipe))
        ],
        "recipes": [{"name": k, "count": v} for k, v in view.recipes.most_common()],
        "buildings": [
            {"name": building_name(k), "count": v} for k, v in view.buildings.most_common()
        ],
        "nodes": [
            {
                "node": node,
                "resource": resource,
                "purity": purity,
                "extractor": building_name(cls),
                "clock": round(clock, 4),
                "left": left,
                "x_m": cm_to_m(places[node][0]) if node in places else None,
                "y_m": cm_to_m(places[node][1]) if node in places else None,
            }
            for node, resource, purity, cls, clock, left in view.nodes
        ],
        "links": [
            {"factory": None if k == "(unlabelled)" else k, "machines": v}
            for k, v in view.links.most_common()
        ],
        "issues": [_issue(line, building_name) for line in view.issues],
    }


class SiteRow(TypedDict):
    """``mine`` is how many of the asked factory's machines stand in this site; 0 without
    ``?factory=``."""

    index: int
    direction: str
    grid: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    count: int
    diameter_m: float
    selector: str
    buildings: list[AspectCount]
    mine: int


class SitesResponse(TypedDict):
    total: int
    factory: str | None
    factory_machines: int
    sites: list[SiteRow]


@router.get("/factories/sites", response_model=SitesResponse)
def factory_sites(
    request: Request,
    factory: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Built production buildings clustered into sites, largest first.

    With ``?factory=``, only the sites holding any of that factory's machines, each with
    the count it holds: a factory that is a small part of a big site is a grown-together
    base, and one spread over several sites is not one place.
    """
    st = require_world(request, save, world)
    mine: set[str] = set()
    if factory is not None:
        found = _label_machines(st, factory)
        if found is None:
            return error_response(f"no factory named “{factory}” in this world", 404)
        mine = set(found[1])
    sites = st.sites()
    rows = []
    for index, site in enumerate(sites):
        held = sum(1 for leaf in site["instances"] if leaf in mine)
        if factory is not None and not held:
            continue
        ranked = sorted(site["buildings"].items(), key=lambda kv: -kv[1])
        rows.append(
            {
                "index": index,
                "direction": site["direction"],
                "grid": site["grid"],
                **xyz_m(site["centroid"]),
                "count": site["count"],
                "diameter_m": site["diameter_m"],
                "selector": site["selector"],
                "buildings": [
                    {"name": st.game.building_name(cls) or cls, "count": count}
                    for cls, count in ranked
                ],
                "mine": held,
            }
        )
    return {
        "total": len(sites),
        "factory": factory,
        "factory_machines": len(mine),
        "sites": rows,
    }

"""``/api/world/*``: the World finders -- where I am, nodes, fields, sites, conduits, regions.

Each route calls the domain function its MCP tool calls (``surroundings.player_surroundings``,
``node_search.find_nodes``, ``node_search.rank``, ``conduit_search.search``/``networks``,
``regions.region_rows``), so the page and the chat answer one question one way.

WARNING: the function names are operation_ids -- renaming one churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from fastapi import APIRouter, Query, Request

from ....core.gamedata.search import resolve_item
from ....core.text import ago
from ....domain.collectibles import service as collectibles_service
from ....domain.spatial import geo, ranking, surroundings
from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ....domain.spatial.nodes import search as node_search
from ....domain.spatial.nodes import table as node_table
from ....domain.spatial.places import resolve_place
from ....domain.world import conduit_search
from ....domain.world import conduits as conduits_mod
from ..serial import (
    FoundField,
    Region,
    TableAge,
    _fail,
    _field_json,
    _label_json,
    _m,
    _resource_name,
    _state,
    _xyz,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/world")

PAGE_VIEWS = ("nodes", "fields")


class FoundNode(TypedDict):
    """One node, its status in this save, and the extractor on it.

    ``status`` is ``locked`` for an untapped node no unlocked extractor can work, which is
    also ``spoiler``. ``rate`` is at 100% clock with the best extractor. ``moved`` marks a
    row a later game build moved or renamed (see ``stale``).
    """

    id: str
    name: str
    resource: str
    resource_name: str
    purity: str
    kind: str
    x_m: float
    y_m: float
    z_m: float
    grid: str
    rate: float
    status: Literal["free", "tapped", "locked"]
    occupant: str | None
    occupant_off: bool | None
    region: Region | None
    distance_m: float | None
    moved: bool
    spoiler: bool


class WaterBlock(TypedDict):
    bodies: dict[str, int]
    pumps: int
    per_pump_m3_min: float | None
    sea_level_m: float | None


class ResourceChoice(TypedDict):
    id: str
    name: str
    nodes: int


class NodeChoices(TypedDict):
    resources: list[ResourceChoice]
    purities: list[str]
    kinds: list[str]


class NodeFindResponse(TypedDict):
    """``count``/``total``/``free`` are the tool's header figures over the rows returned."""

    view: Literal["nodes", "fields"]
    description: str
    selectors: list[str]
    where: str
    nodes: list[FoundNode]
    fields: list[FoundField]
    count: int
    total: float
    free: float
    unit: str
    elevation: tuple[float, float] | None
    water: WaterBlock | None
    choices: NodeChoices
    notes: list[str]
    stale: TableAge | None
    save_error: str | None


class RankedSite(TypedDict):
    rank: int
    score: float
    region: str | None
    grid: str
    x_m: float
    y_m: float
    selector: str
    nodes: int
    untapped: float
    spread_m: float
    to_infra_m: float | None
    purity: float
    alt_m: float | None
    rough_m: float | None
    slope_deg: float | None
    wet_pct: float | None


class RankedSitesResponse(TypedDict):
    resource: str
    resource_name: str
    description: str
    sites: list[RankedSite]
    count: int
    weights: dict[str, float]
    notes: list[str]
    stale: TableAge | None


class RunEnd(TypedDict):
    x_m: float
    y_m: float
    z_m: float
    plugs: str | None


class RunRow(TypedDict):
    """One belt chain or pipe piece; ``lines_m`` are the drawn polylines, as trace sends."""

    id: str
    kind: Literal["belt", "lift", "pipe"]
    label: str
    pieces: int
    length_m: float
    a: RunEnd
    b: RunEnd
    z_min_m: float
    z_max_m: float
    directed: bool
    basis: str | None
    carries: str | None
    rate: float | None
    network: int | None
    via: list[str]
    distance_m: float
    lines_m: list[list[tuple[float, float]]]


class NetworkRow(TypedDict):
    network: int | None
    carries: str | None
    pieces: int
    length_m: float
    x_m: float
    y_m: float
    z_min_m: float
    z_max_m: float
    distance_m: float
    touches: list[str]


class ConduitsResponse(TypedDict):
    """``total`` counts every match; ``runs``/``networks`` hold one page of them."""

    view: Literal["runs", "networks"]
    where: str
    where_to: str
    radius_m: float
    to_radius_m: float | None
    runs: list[RunRow]
    networks: list[NetworkRow]
    total: int
    offset: int
    belts: int
    pipes: int
    belt_m: float
    pipe_m: float
    fluids: list[str]
    bridged: list[str]
    notes: list[str]
    age_note: str


class PlayerAt(TypedDict):
    x_m: float
    y_m: float
    z_m: float


class NearestBuilding(TypedDict):
    name: str
    distance_m: float


class HereResponse(TypedDict):
    """``player`` is null for a save with no pawn; the rest of World still answers."""

    age_note: str
    written_ago: str | None
    save_token: str
    player: PlayerAt | None
    region: Region | None
    grid: str | None
    direction: str | None
    radius_m: float
    nodes: list[FoundNode]
    nodes_total: int
    nearest_building: NearestBuilding | None
    pawns: int
    stale: list[TableAge]


class RegionRow(TypedDict):
    name: str
    direction: str
    grid: str
    anchor_m: tuple[float, float]
    area_km2: float
    nodes: int


class RegionTableResponse(TypedDict):
    resource: str | None
    resource_name: str | None
    rows: list[RegionRow]
    accuracy_m: int


def _found_node(r: dict, game, rm, drifted: set[str]) -> FoundNode:
    status = node_search.status_of(r)
    cls = r.get("tapped_by")
    occupant = None
    if cls:
        occupant = game.building_name(cls) or cls
        if r.get("tapped_clock") is not None:
            occupant += f" @{r['tapped_clock']:.0%}"
    leaf = str(r["instance"]).rsplit(".", 1)[-1]
    return {
        "id": r["instance"],
        "name": leaf,
        "resource": r["resource"],
        "resource_name": _resource_name(game, r["resource"]),
        "purity": r["purity"],
        "kind": r["kind"],
        **_xyz((r["x"], r["y"], r["z"])),
        "grid": r["grid"],
        "rate": round(r["rate"], 2),
        "status": status,
        "occupant": occupant,
        "occupant_off": bool(r.get("tapped_paused")) if cls else None,
        "region": _label_json(rm.label_for_node(r)),
        "distance_m": r.get("distance_m"),
        "moved": leaf in drifted,
        "spoiler": status == "locked",
    }


def _choice(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value is None or value.strip().casefold() in allowed:
        return None
    return f"unknown {name} {value!r}. Choose from: {', '.join(allowed)}"


def _resolver(game):
    return lambda query: resolve_item(game, query)


@router.get("/nodes", response_model=NodeFindResponse)
def world_nodes(
    request: Request,
    view: str = "nodes",
    resource: str | None = None,
    purity: str | None = None,
    kind: str | None = None,
    status: str = "all",
    source: Annotated[list[str] | None, Query()] = None,
    near: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Resource nodes as ``search_resource_nodes`` finds them: nodes or fields.

    ``resource`` is a name or class id; ``purity`` and ``kind`` take ``all`` for no filter;
    ``status`` is ``free`` (untapped), ``tapped`` or ``all``. ``source`` repeats and takes
    the tool's selectors. With ``near`` every row carries ``distance_m`` and the page sorts
    by it. Locked nodes stay in, flagged ``spoiler``; the page fades them. A save that will
    not load still answers from the node table, with ``save_error`` set.
    """
    game = request.app.state.game()
    view = view.strip().casefold()
    status = status.strip().casefold()
    refusal = (
        _choice(view, PAGE_VIEWS, "view")
        or _choice(status, node_search.STATUSES, "status")
        or _choice(purity, (*node_table.PURITIES, "all"), "purity")
        or _choice(kind, (*node_table.KINDS, "all"), "kind")
    )
    if refusal:
        return _fail(refusal)
    if resource and resource.strip().casefold() != "all":
        rid = resolve_item(game, resource)
        if rid is None:
            return _fail(f"unknown resource {resource!r}")
        resource = _resource_name(game, rid)
    try:
        spatial_nodes.load_nodes()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    st = None
    save_error = None
    try:
        st = _state(request, save, world)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    found = node_search.find_nodes(
        st,
        game,
        sources=source,
        resource=resource,
        purity=purity,
        kind=kind,
        status=status,
        view=view,
        near=near,
        resolve_resource=_resolver(game),
    )
    if found.error:
        return _fail(found.error)
    if found.unselected:
        return _fail("no selector resolved: " + "; ".join(found.errors))

    rm = spatial_regions.load_regions()
    drifted = found.drifted
    water = found.water
    return {
        "view": view,
        "description": found.description,
        "selectors": found.selectors,
        "where": found.where,
        "nodes": []
        if view == "fields"
        else [_found_node(r, game, rm, drifted) for r in found.rows],
        "fields": [_field_json(f, game) for f in found.fields] if view == "fields" else [],
        "count": len(found.rows),
        "total": round(found.total, 2),
        "free": round(found.free, 2),
        "unit": found.unit,
        "elevation": found.elevation,
        "water": None
        if water is None
        else {k: water[k] for k in ("bodies", "pumps", "per_pump_m3_min", "sea_level_m")},
        "choices": node_search.choices(game),
        "notes": node_search.page_notes(found, st),
        "stale": spatial_nodes.table_age(
            st.header if st else None, None, [r["instance"] for r in found.rows]
        ),
        "save_error": save_error,
    }


@router.get("/sites", response_model=RankedSitesResponse)
def world_sites(
    request: Request,
    resource: str,
    source: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Candidate fields for one resource, best first, as ``rank_build_sites`` ranks them."""
    game = request.app.state.game()
    rid = resolve_item(game, resource)
    if rid is None:
        return _fail(f"unknown resource {resource!r}")
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    ranked = node_search.rank(st, game, rid, source, resolve_resource=_resolver(game))
    if ranked.unselected:
        return _fail("no selector resolved: " + "; ".join(ranked.selection.errors))
    rm = spatial_regions.load_regions()
    sites = []
    for i, sc in enumerate(ranked.scored[:limit], 1):
        v = node_search.site_view(sc, rm)
        sites.append(
            {
                "rank": i,
                "score": v["score"],
                "region": v["region"],
                "grid": v["grid"],
                "x_m": _m(v["x"]),
                "y_m": _m(v["y"]),
                "selector": v["selector"],
                "nodes": v["nodes"],
                "untapped": v["untapped"],
                "spread_m": v["spread_m"],
                "to_infra_m": v["to_infra_m"],
                "purity": v["purity"],
                "alt_m": None if v["alt_m"] is None else round(v["alt_m"], 1),
                "rough_m": v["rough_m"],
                "slope_deg": v["slope_deg"],
                "wet_pct": v["wet_pct"],
            }
        )
    return {
        "resource": rid,
        "resource_name": _resource_name(game, rid),
        "description": ranked.selection.description,
        "sites": sites,
        "count": len(ranked.scored),
        "weights": dict(ranking.WEIGHTS),
        "notes": [*ranked.selection.errors, *ranked.notes],
        "stale": spatial_nodes.table_age(st.header, None, [r["instance"] for r in ranked.rows]),
    }


def _end(e) -> RunEnd:
    return {"x_m": _m(e.x), "y_m": _m(e.y), "z_m": _m(e.z), "plugs": e.plugs}


def _run_row(run, origin, game) -> RunRow:
    return {
        "id": run.ident,
        "kind": run.kind,
        "label": run.label,
        "pieces": run.pieces,
        "length_m": round(run.length_m, 1),
        "a": _end(run.a),
        "b": _end(run.b),
        "z_min_m": round(run.z_min_m, 1),
        "z_max_m": round(run.z_max_m, 1),
        "directed": run.directed,
        "basis": run.basis,
        "carries": (_resource_name(game, run.fluid) if run.fluid else None),
        "rate": None if run.rate is None else round(run.rate, 2),
        "network": run.network,
        "via": list(run.via),
        "distance_m": run.dist_m(*origin),
        "lines_m": [[(_m(p[0]), _m(p[1])) for p in line] for line in run.lines],
    }


@router.get("/conduits", response_model=ConduitsResponse)
def world_conduits(
    request: Request,
    near: str = "me",
    radius_m: Annotated[float, Query(ge=1, le=2000)] = conduits_mod.NEAR_RADIUS_M,
    to: str | None = None,
    to_radius_m: Annotated[float | None, Query(ge=1, le=2000)] = None,
    conduit_kind: str = "all",
    view: str = "runs",
    network: int | None = None,
    run: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Belt and pipe runs near a place (and ``to`` a second one), or every fluid network.

    The runs are ``search_conduits``'s, longest first. ``network`` lists every pipe of one
    fluid network and ``run`` one run by id; both ignore the radii.
    """
    view = view.strip().casefold()
    kind = conduit_kind.strip().casefold()
    refusal = _choice(view, ("runs", "networks"), "view") or _choice(
        kind, (*conduit_search.KINDS, "all"), "conduit_kind"
    )
    if refusal:
        return _fail(refusal)
    if view == "networks" and kind == "belt":
        return _fail("view=networks lists fluid networks; a belt chain belongs to none")
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    game = request.app.state.game()
    base = {
        "view": view,
        "where_to": "",
        "radius_m": radius_m,
        "to_radius_m": None,
        "runs": [],
        "networks": [],
        "offset": offset,
        "belts": 0,
        "pipes": 0,
        "belt_m": 0.0,
        "pipe_m": 0.0,
        "fluids": [],
        "bridged": [],
        "notes": [],
        "age_note": st.age_note,
    }
    if view == "networks":
        try:
            origin, where = resolve_place(st, near)
        except ValueError as exc:
            return _fail(f"! {exc}")
        views = conduit_search.networks(st, origin)
        page = views[offset : offset + limit]
        return {
            **base,
            "where": where,
            "networks": [
                {
                    "network": v.network,
                    "carries": _resource_name(game, v.fluid) if v.fluid else None,
                    "pieces": v.pieces,
                    "length_m": round(v.length_m, 1),
                    "x_m": _m(v.centre[0]),
                    "y_m": _m(v.centre[1]),
                    "z_min_m": round(v.z_min_m, 1),
                    "z_max_m": round(v.z_max_m, 1),
                    "distance_m": v.distance_m,
                    "touches": v.touches,
                }
                for v in page
            ],
            "total": len(views),
            "pipes": sum(v.pieces for v in views),
            "pipe_m": round(sum(v.length_m for v in views), 1),
            "fluids": sorted({_resource_name(game, v.fluid) for v in views if v.fluid}),
        }

    found = conduit_search.search(
        st,
        near,
        radius_m,
        to=to,
        to_radius_m=to_radius_m,
        kind=None if kind == "all" else kind,
        network=network,
        run=run,
    )
    if found.error:
        return _fail(found.error)
    belts, pipes = found.belts, found.pipes
    return {
        **base,
        "where": found.where,
        "where_to": found.where_to,
        "to_radius_m": found.to_radius_m,
        "runs": [_run_row(r, found.origin, game) for r in found.hits[offset : offset + limit]],
        "total": len(found.hits),
        "belts": len(belts),
        "pipes": len(pipes),
        "belt_m": round(sum(r.length_m for r in belts), 1),
        "pipe_m": round(sum(r.length_m for r in pipes), 1),
        "fluids": sorted({_resource_name(game, r.fluid) for r in pipes if r.fluid}),
        "bridged": found.bridged,
    }


@router.get("/here", response_model=HereResponse)
def world_here(
    request: Request,
    radius_m: Annotated[float, Query(ge=1, le=5000)] = 500.0,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Where the player stands and the nodes around them, as ``whereami`` answers it."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    game = request.app.state.game()
    found = surroundings.player_surroundings(st, game, radius_m)
    rows = found.nodes
    instances = [r["instance"] for r in rows]
    drifted = spatial_nodes.drifted(found.skew, instances)
    rm = spatial_regions.load_regions()
    stale = [
        age
        for age in (
            spatial_nodes.table_age(st.header, None, instances),
            collectibles_service.table_age(st),
        )
        if age is not None and (age["behind"] or age["observed_matches"] is False)
    ]
    player = found.player
    building = found.nearest_building
    return {
        "age_note": st.age_note,
        "written_ago": ago(st.header.get("mtime_ns")),
        "save_token": st.token,
        "player": None if player is None else _xyz(player),
        "region": _label_json(found.label) if found.label else None,
        "grid": None if player is None else geo.grid_cell(player[0], player[1]),
        "direction": None if player is None else geo.direction_of(player[0], player[1]),
        "radius_m": radius_m,
        "nodes": [_found_node(r, game, rm, drifted) for r in rows],
        "nodes_total": len(rows),
        "nearest_building": None
        if building is None
        else {"name": building[0], "distance_m": building[1]},
        "pawns": found.pawns,
        "stale": stale,
    }


@router.get("/regions", response_model=RegionTableResponse)
def world_regions(
    request: Request,
    resource: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Named regions with their node counts, as ``list_regions`` lists them."""
    game = request.app.state.game()
    rid = resolve_item(game, resource) if resource else None
    if resource and rid is None:
        return _fail(f"unknown resource {resource!r}")
    table = spatial_nodes.load_nodes()
    rm = spatial_regions.load_regions()
    return {
        "resource": rid,
        "resource_name": _resource_name(game, rid) if rid else None,
        "rows": [
            {
                "name": r["name"],
                "direction": r["direction"],
                "grid": r["grid"],
                "anchor_m": (_m(r["anchor"][0]), _m(r["anchor"][1])),
                "area_km2": r["area_km2"],
                "nodes": r["nodes"],
            }
            for r in spatial_regions.region_rows(table, rid)
        ],
        "accuracy_m": rm.accuracy_m,
    }

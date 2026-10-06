"""``/api/world/*``: the World finders -- where I am, nodes, fields, sites, regions.

Each route calls the domain function its MCP tool calls
(``surroundings.player_surroundings``, ``node_search.find_nodes``,
``node_search.rank_build_sites``, ``regions.region_rows``), so the page and the chat answer one
question one way. Conduits are ``world_conduits.py``. Handler names are operation_ids (wire
rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from typing_extensions import TypedDict

from .....core.gamedata.search import resolve_item
from .....core.text import ago
from .....domain.spatial import geo, ranking, surroundings
from .....domain.spatial import nodes as spatial_nodes
from .....domain.spatial import regions as spatial_regions
from .....domain.spatial.nodes import search as node_search
from .....domain.spatial.nodes import table as node_table
from .....domain.spatial.nodes.views import NodeChoices, SiteRow
from ...serial import (
    FoundField,
    Region,
    TableAge,
    choice_refusal,
    cm_to_m,
    error_response,
    found_field_json,
    node_identity,
    point_m,
    region_json,
    require_world,
    resource_name,
    stale_tables,
    world_state,
    xyz_m,
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


def _found_node(
    node: spatial_nodes.AnnotatedNode, game, region_map, drifted: set[str]
) -> FoundNode:
    status = node_search.status_of(node)
    cls = node.get("tapped_by")
    occupant = None
    if cls:
        occupant = game.building_name(cls) or cls
        if node.get("tapped_clock") is not None:
            occupant += f" @{node['tapped_clock']:.0%}"
    identity = node_identity(node, game)
    return {
        **identity,
        "purity": node["purity"],
        "kind": node["kind"],
        **xyz_m((node["x"], node["y"], node["z"])),
        "grid": node["grid"],
        "rate": round(node["rate"], 2),
        "status": status,
        "occupant": occupant,
        "occupant_off": bool(node.get("tapped_paused")) if cls else None,
        "region": region_json(region_map.label_for_node(node)),
        "distance_m": node.get("distance_m"),
        "moved": identity["name"] in drifted,
        "spoiler": status == "locked",
    }


def _ranked_site_json(rank: int, site: SiteRow) -> RankedSite:
    return {
        "rank": rank,
        "score": site["score"],
        "region": site["region"],
        "grid": site["grid"],
        "x_m": cm_to_m(site["x"]),
        "y_m": cm_to_m(site["y"]),
        "selector": site["selector"],
        "nodes": site["nodes"],
        "untapped": site["untapped"],
        "spread_m": site["spread_m"],
        "to_infra_m": site["to_infra_m"],
        "purity": site["purity"],
        "alt_m": None if site["alt_m"] is None else round(site["alt_m"], 1),
        "rough_m": site["rough_m"],
        "slope_deg": site["slope_deg"],
        "wet_pct": site["wet_pct"],
    }


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
        choice_refusal(view, PAGE_VIEWS, "view")
        or choice_refusal(status, node_search.STATUSES, "status")
        or choice_refusal(purity, (*node_table.PURITIES, "all"), "purity")
        or choice_refusal(kind, (*node_table.KINDS, "all"), "kind")
    )
    if refusal:
        return error_response(refusal)
    if resource and resource.strip().casefold() != "all":
        resource_id = resolve_item(game, resource)
        if resource_id is None:
            return error_response(f"unknown resource {resource!r}")
        resource = resource_name(game, resource_id)
    try:
        spatial_nodes.load_nodes()
    except FileNotFoundError as exc:
        return error_response(str(exc), 404)

    st = None
    save_error = None
    try:
        st = world_state(request, save, world)
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
        return error_response(found.error)
    if found.unselected:
        return error_response("no selector resolved: " + "; ".join(found.errors))

    region_map = spatial_regions.load_regions()
    water = found.water
    fields_view = view == "fields"
    return {
        "view": view,
        "description": found.description,
        "selectors": found.selectors,
        "where": found.where,
        "nodes": (
            []
            if fields_view
            else [
                _found_node(node, game, region_map, found.drifted_leaf_names) for node in found.rows
            ]
        ),
        "fields": [found_field_json(f, game) for f in found.fields] if fields_view else [],
        "count": len(found.rows),
        "total": round(found.total, 2),
        "free": round(found.free, 2),
        "unit": found.unit,
        "elevation": found.elevation,
        "water": None
        if water is None
        else {k: water[k] for k in ("bodies", "pumps", "per_pump_m3_min", "sea_level_m")},
        "choices": node_search.filter_choices(game),
        "notes": node_search.page_notes(found, st),
        "stale": spatial_nodes.table_age(
            st.header if st else None, None, [node["instance"] for node in found.rows]
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
    resource_id = resolve_item(game, resource)
    if resource_id is None:
        return error_response(f"unknown resource {resource!r}")
    st = require_world(request, save, world)
    ranked = node_search.rank_build_sites(
        st, game, resource_id, source, resolve_resource=_resolver(game)
    )
    if ranked.unselected:
        return error_response("no selector resolved: " + "; ".join(ranked.selection.errors))
    region_map = spatial_regions.load_regions()
    sites = [
        _ranked_site_json(rank, node_search.site_row(scored, region_map))
        for rank, scored in enumerate(ranked.scored[:limit], 1)
    ]
    return {
        "resource": resource_id,
        "resource_name": resource_name(game, resource_id),
        "description": ranked.selection.description,
        "sites": sites,
        "count": len(ranked.scored),
        "weights": dict(ranking.WEIGHTS),
        "notes": [*ranked.selection.errors, *ranked.notes],
        "stale": spatial_nodes.table_age(
            st.header, None, [node["instance"] for node in ranked.rows]
        ),
    }


@router.get("/here", response_model=HereResponse)
def world_here(
    request: Request,
    radius_m: Annotated[float, Query(ge=1, le=5000)] = 500.0,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Where the player stands and the nodes around them, as ``whereami`` answers it."""
    st = require_world(request, save, world)
    game = request.app.state.game()
    found = surroundings.player_surroundings(st, game, radius_m)
    rows = found.nodes
    instances = [node["instance"] for node in rows]
    drifted = spatial_nodes.drifted_leaf_names(found.skew, instances)
    region_map = spatial_regions.load_regions()
    stale = stale_tables(st, None, instances)
    player = found.player
    building = found.nearest_building
    return {
        "age_note": st.age_note,
        "written_ago": ago(st.header.get("mtime_ns")),
        "save_token": st.token,
        "player": None if player is None else xyz_m(player),
        "region": region_json(found.label) if found.label else None,
        "grid": None if player is None else geo.grid_cell(player[0], player[1]),
        "direction": None if player is None else geo.direction_of(player[0], player[1]),
        "radius_m": radius_m,
        "nodes": [_found_node(node, game, region_map, drifted) for node in rows],
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
    resource_id = resolve_item(game, resource) if resource else None
    if resource and resource_id is None:
        return error_response(f"unknown resource {resource!r}")
    table = spatial_nodes.load_nodes()
    region_map = spatial_regions.load_regions()
    return {
        "resource": resource_id,
        "resource_name": resource_name(game, resource_id) if resource_id else None,
        "rows": [
            {
                "name": row["name"],
                "direction": row["direction"],
                "grid": row["grid"],
                "anchor_m": point_m(row["anchor"]),
                "area_km2": row["area_km2"],
                "nodes": row["nodes"],
            }
            for row in spatial_regions.region_rows(table, resource_id)
        ],
        "accuracy_m": region_map.accuracy_m,
    }

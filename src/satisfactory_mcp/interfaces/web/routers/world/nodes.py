"""``/api/nodes``: the resource node table, joined to what this save has built on it.

What the join can and cannot say: docs/web-wire.md "Nodes". Handler names are operation_ids.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....domain.spatial import nodes as spatial_nodes
from .....domain.spatial import regions as spatial_regions
from .....domain.spatial.nodes.extraction import Occupant
from ...serial import (
    Region,
    cm_to_m,
    error_response,
    game_data,
    node_identity,
    region_json,
    world_state,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- nodes


class NodeRow(TypedDict):
    """One resource node, joined to whatever this save has built on it.

    The coordinates come from the static table and are never null. ``occupant_cls`` and
    ``occupant_name`` are null where the occupancy join found no extractor, ``region`` for a
    node the raster calls void. ``resource`` is the class id the layer is keyed by and
    ``resource_name`` the word the MCP tools use.

    ``reachable`` is false for a node no unlocked extractor can work (the text surface's
    ``LOCKED``), and null, never true, when the save could not be read. ``spoiler`` is an
    unoccupied node with ``reachable`` false, which the page draws faded.
    """

    id: str
    resource: str
    resource_name: str
    name: str
    kind: str
    purity: str
    x_m: float
    y_m: float
    z_m: float
    occupied: bool
    occupant_cls: str | None
    occupant_name: str | None
    reachable: bool | None
    region: Region | None
    spoiler: bool


class NodesResponse(TypedDict):
    """What ``/api/nodes`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``resource`` echoes the query parameter. ``occupied`` is null rather than 0 whenever
    ``save_error`` is set: "0 of them occupied" would be a claim nobody measured.
    """

    nodes: list[NodeRow]
    resource: str | None
    occupied: int | None
    save_error: str | None


@router.get("/nodes", response_model=NodesResponse)
def nodes(
    request: Request,
    resource: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> NodesResponse | JSONResponse:
    """The resource node table, joined to what this save has built on it; a save that will
    not load still gets the table, with ``save_error`` saying what the join lost."""
    try:
        table = spatial_nodes.load_nodes()
        region_map = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return error_response(str(exc), 404)

    save_error: str | None = None
    taken: dict[str, Occupant] = {}
    unlocked: set[str] | None = None
    try:
        st = world_state(request, save, world)
        taken = spatial_nodes.occupancy_by_node(st.projection)
        unlocked = st.unlocked_building_ids
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    game = game_data(request)
    rows = table.by_resource(resource) if resource else table.nodes
    out: list[NodeRow] = []
    for node in rows:
        held = taken.get(node["instance"])
        occupant = held["extractor"] if held else None
        reachable = None if unlocked is None else spatial_nodes.reachable(node, unlocked)
        out.append(
            {
                **node_identity(node, game),
                "kind": node["kind"],
                "purity": node["purity"],
                "x_m": cm_to_m(node["x"]),
                "y_m": cm_to_m(node["y"]),
                "z_m": cm_to_m(node["z"]),
                "occupied": held is not None,
                "occupant_cls": occupant,
                "occupant_name": game.building_name(occupant),
                # Null, not the domain's "assume yes", when no unlock set was read.
                "reachable": reachable,
                "region": region_json(region_map.label_for_node(node)),
                "spoiler": reachable is False and held is None,
            }
        )
    return {
        "nodes": out,
        "resource": resource,
        "occupied": None if save_error else sum(1 for r in out if r["occupied"]),
        "save_error": save_error,
    }

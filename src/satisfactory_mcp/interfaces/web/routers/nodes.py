"""``/api/nodes``: the resource node table, joined to what this save has built on it.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ..serial import Region, _fail, _label_json, _resource_name, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- nodes


class NodeRow(TypedDict):
    """One resource node, joined to whatever this save has built on it.

    ``x_m``/``y_m``/``z_m`` are not nullable even though ``_xyz`` can answer nulls: that
    helper also serves placements, whose transforms can fail to decode, and a node has no
    transform to fail -- the triple comes from the static table, three floats per node.

    ``occupant_cls`` and ``occupant_name`` are nullable because the occupancy join resolves
    only the extractors whose target is a node key. ``region`` is null for the handful of
    nodes the raster calls void.

    ``resource`` is the class id, which is what the layer keys and the colour table are keyed
    by; ``resource_name`` is the word a reader reads, and is the same word the MCP tools use.

    ``reachable`` is false for a node no unlocked extractor can work -- the state the text
    surface prints as ``LOCKED`` and excludes from free capacity. It is null, never true, when
    the save could not be read: reachability is a fact about what this world has researched,
    and with no world there is nothing to have researched it.

    ``spoiler`` is an unoccupied node with ``reachable`` false, the rows the text surface
    marks ``LOCKED``: the page hides those dots while spoilers are off.
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

    ``resource`` echoes the query parameter and is ``null`` when none was given. ``occupied``
    is ``null`` rather than 0 whenever ``save_error`` is set: "0 of them occupied" would be a
    claim nobody measured, so the two fields are one statement and are typed as one.
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
) -> Any:
    """The resource node table, joined to what this save has built on it.

    The join is partial and says so: ``occupancy`` resolves only the extractors whose target
    is a node key, so ``occupied`` false means "no extractor known here", never "free". The
    popup carries the node id, which doubles as a ``node:`` selector for the MCP tools.

    The region name is joined on this side because the raster is: sending 608 rows and then
    the grid for the page to index into would put the orientation trap (row 0 is the north
    edge) in two places. It is ``null`` for a node the raster calls void.

    **A free node is not always a usable one.** ``reachable`` is the same test the text
    surface marks ``LOCKED`` and leaves out of free capacity -- a resource well satellite
    with no Pressurizer researched is not somewhere a plan can go.

    **A failed save is not a failed answer.** The node table is static and needs no ``.sav``,
    so a world whose save will not load still gets its geography; what it loses is the
    occupancy join and the unlock set, and ``save_error`` says so with ``occupied`` and every
    row's ``reachable`` null beside it.
    """
    try:
        table = spatial_nodes.load_nodes()
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    save_error: str | None = None
    taken: dict = {}
    unlocked: set[str] | None = None
    try:
        st = _state(request, save, world)
        taken = spatial_nodes.occupancy(st.projection)
        unlocked = st.unlocked_building_ids
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    game = request.app.state.game()
    rows = table.by_resource(resource) if resource else table.nodes
    out: list[NodeRow] = []
    for n in rows:
        held = taken.get(n["instance"])
        occupant = held["extractor"] if held else None
        reachable = None if unlocked is None else spatial_nodes.reachable(n, unlocked)
        out.append(
            {
                "id": n["instance"],
                "resource": n["resource"],
                "resource_name": _resource_name(game, n["resource"]),
                "name": str(n["instance"]).rsplit(".", 1)[-1],
                "kind": n["kind"],
                "purity": n["purity"],
                **_xyz((n["x"], n["y"], n["z"])),
                "occupied": held is not None,
                "occupant_cls": occupant,
                "occupant_name": game.building_name(occupant),
                # Not `reachable(n, unlocked)`: the domain reads a null unlock set as "no
                # world to judge against, so assume yes", which is the right default for a
                # capacity sum and the wrong one for a dot somebody plans around.
                "reachable": reachable,
                "region": _label_json(rmap.label_for_node(n)),
                "spoiler": reachable is False and held is None,
            }
        )
    return {
        "nodes": out,
        "resource": resource,
        "occupied": None if save_error else sum(1 for r in out if r["occupied"]),
        "save_error": save_error,
    }

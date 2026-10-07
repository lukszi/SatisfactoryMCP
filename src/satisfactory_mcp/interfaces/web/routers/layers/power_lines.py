"""``/api/power``: every pole and tower, and the span of every wire between them.

The geometry beside the power edges, joined back to them by position; what each field means:
docs/web-wire.md "Power lines". Handler names are operation_ids.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio import rows as saverows
from .....core.saveio.records import instance_leaf
from .....domain.spatial import geo
from .....domain.world.state import WorldState
from ...serial import cm_to_m, require_world, yaw_deg

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: The record lists a wire end can be named from; a pole comes in through its actor index.
NAMED_RECORD_LISTS = ("machines", "extractors", "generators", "attachments", "storage")


class PoleRow(TypedDict):
    """A power pole, wall outlet or tower platform: where it stands and how busy it is.

    ``cls`` and ``name`` are null for a row whose class index is past the legend's end; the
    coordinates never are. ``connections`` is 0, never null, for a pole nothing is wired to.
    """

    cls: str | None
    name: str | None
    x_m: float
    y_m: float
    z_m: float
    yaw: float | None
    connections: int


#: One power wire, as the straight line between the two connectors it is strung between.
#: Functional syntax because ``from`` is a Python keyword.
WireRow = TypedDict(
    "WireRow",
    {
        "a_m": tuple[float, float, float],
        "b_m": tuple[float, float, float],
        "from": str | None,
        "to": str | None,
        "a_pole": int | None,
        "b_pole": int | None,
        "span_m": float,
    },
)


class PowerResponse(TypedDict):
    """The two lists and the three counts.

    ``edge_count`` is how many power edges the projection holds and ``wire_count`` how many
    of those published a span: a non-zero ``edge_count`` with no wires is a save too old to
    carry the geometry, not a world with nothing wired.
    """

    poles: list[PoleRow]
    pole_count: int
    wires: list[WireRow]
    wire_count: int
    edge_count: int


def _power_names(st: WorldState) -> dict[str, str]:
    """Actor identity to display name, for every actor this projection can name.

    Built once per request rather than per wire. An end on an actor no record list carries is
    left out, so its wire says null rather than a guess.
    """
    actors = st.projection.get("graph", {}).get("actors") or []
    names: dict[str, str] = {}
    for key in NAMED_RECORD_LISTS:
        for row in st.projection.get(key) or ():
            if not isinstance(row, dict):
                continue
            name = st.game.building_name(row.get("cls"))
            if name:
                names[instance_leaf(row.get("instance", ""))] = name
    for pole in saverows.iter_power_poles(st.projection):
        if 0 <= pole.actor_index < len(actors):
            name = st.game.building_name(pole.cls)
            if name:
                names[str(actors[pole.actor_index])] = name
    return names


def _degrees(edges: list) -> dict[int, int]:
    """How many power edges touch each actor index, counted once over the edge list."""
    degree: dict[int, int] = {}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            for end in edge[:2]:
                if isinstance(end, int):
                    degree[end] = degree.get(end, 0) + 1
    return degree


def _wire_row(wire: Any, edges: list, actors: list, named: dict, pole_at: dict) -> dict:
    """One wire's span, with both ends named and their poles read off the same edge."""
    edge = edges[wire.index] if wire.index < len(edges) else None
    pair = edge[:2] if isinstance(edge, (list, tuple)) and len(edge) >= 2 else (None, None)
    ends = [
        named.get(str(actors[end])) if isinstance(end, int) and 0 <= end < len(actors) else None
        for end in pair
    ]
    return {
        "a_m": [cm_to_m(v) for v in wire.a],
        "b_m": [cm_to_m(v) for v in wire.b],
        "from": ends[0],
        "to": ends[1],
        "a_pole": pole_at.get(pair[0]) if isinstance(pair[0], int) else None,
        "b_pole": pole_at.get(pair[1]) if isinstance(pair[1], int) else None,
        "span_m": round(geo.distance_3d_m(wire.a, wire.b), 1),
    }


@router.get("/power", response_model=PowerResponse)
def power(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every power pole and tower, and the span of every wire between them, each end
    placed at its connector and named where the save names it."""
    st = require_world(request, save, world)

    projection = st.projection
    actors = projection.get("graph", {}).get("actors") or []
    edges = projection.get("graph", {}).get("power") or []
    named = _power_names(st)
    degree = _degrees(edges)

    # One decode serves both the rows sent and the index the wire join reads, so the two
    # cannot be counted off different rows.
    pole_rows = list(saverows.iter_power_poles(projection))
    poles = [
        {
            "cls": pole.cls,
            "name": st.game.building_name(pole.cls),
            "x_m": cm_to_m(pole.x),
            "y_m": cm_to_m(pole.y),
            "z_m": cm_to_m(pole.z),
            "yaw": yaw_deg(pole.yaw),
            "connections": degree.get(pole.actor_index, 0) if pole.actor_index >= 0 else 0,
        }
        for pole in pole_rows
    ]
    # -1 is "no actor index" and must not become a key: it would join every unindexed pole
    # to whichever one enumerated last.
    pole_at = {pole.actor_index: i for i, pole in enumerate(pole_rows) if pole.actor_index >= 0}
    wires = [
        _wire_row(wire, edges, actors, named, pole_at) for wire in saverows.iter_wires(projection)
    ]
    return {
        "poles": poles,
        "pole_count": len(poles),
        "wires": wires,
        "wire_count": len(wires),
        "edge_count": len(edges),
    }

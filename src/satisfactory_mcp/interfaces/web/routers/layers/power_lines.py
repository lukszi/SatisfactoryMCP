"""``/api/power``: every pole and tower, and the span of every wire between them.

The geometry beside the power edges, joined back to them by position; what each field means:
docs/web-wire.md "Power lines". Handler names are operation_ids.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, cast

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio import rows as saverows
from .....core.saveio.records import instance_leaf
from .....core.saveio.schema import PowerEdge, Projection
from .....domain.spatial import geo
from .....domain.world.state import WorldState
from ...serial import cm_to_m, object_rows, require_world, yaw_deg

__all__ = ["router"]

router = APIRouter(prefix="/api")

NamedList = Literal["machines", "extractors", "generators", "attachments", "storage"]

#: The record lists a wire end can be named from; a pole comes in through its actor index.
NAMED_RECORD_LISTS: tuple[NamedList, ...] = (
    "machines",
    "extractors",
    "generators",
    "attachments",
    "storage",
)


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


def _graph(projection: Projection) -> tuple[list[str], list[PowerEdge]]:
    """The interned actors and the power edges that index them; empty where there is no graph."""
    graph = projection.get("graph")
    if not graph:
        return [], []
    return graph.get("actors") or [], graph.get("power") or []


def _power_names(st: WorldState, actors: list[str]) -> dict[str, str]:
    """Actor identity to display name, for every actor this projection can name.

    Built once per request rather than per wire. An end on an actor no record list carries is
    left out, so its wire says null rather than a guess.
    """
    names: dict[str, str] = {}
    for key in NAMED_RECORD_LISTS:
        for row in object_rows(st.projection.get(key)):
            name = st.game.building_name(row.get("cls"))
            if name:
                names[instance_leaf(row.get("instance", ""))] = name
    for pole in saverows.iter_power_poles(st.projection):
        if 0 <= pole.actor_index < len(actors):
            name = st.game.building_name(pole.cls)
            if name:
                names[str(actors[pole.actor_index])] = name
    return names


def _ends(edge: object) -> tuple[int | None, int | None]:
    """An edge's two actor indices, ``None`` where a torn row gives none."""
    if not isinstance(edge, (list, tuple)):
        return None, None
    values = cast("Sequence[object]", edge)
    if len(values) < 2:
        return None, None
    a, b = values[0], values[1]
    return (a if isinstance(a, int) else None), (b if isinstance(b, int) else None)


def _degrees(edges: list[PowerEdge]) -> dict[int, int]:
    """How many power edges touch each actor index, counted once over the edge list."""
    degree: dict[int, int] = {}
    for edge in edges:
        for end in _ends(edge):
            if end is not None:
                degree[end] = degree.get(end, 0) + 1
    return degree


def _xyz_m(point: list[float]) -> tuple[float, float, float]:
    return cm_to_m(point[0]), cm_to_m(point[1]), cm_to_m(point[2])


def _wire_row(
    wire: saverows.Wire,
    edges: list[PowerEdge],
    actors: list[str],
    named: dict[str, str],
    pole_at: dict[int, int],
) -> WireRow:
    """One wire's span, with both ends named and their poles read off the same edge."""
    pair = _ends(edges[wire.position] if wire.position < len(edges) else None)
    ends = [
        named.get(str(actors[end])) if end is not None and 0 <= end < len(actors) else None
        for end in pair
    ]
    return {
        "a_m": _xyz_m(wire.a),
        "b_m": _xyz_m(wire.b),
        "from": ends[0],
        "to": ends[1],
        "a_pole": pole_at.get(pair[0]) if pair[0] is not None else None,
        "b_pole": pole_at.get(pair[1]) if pair[1] is not None else None,
        "span_m": round(geo.distance_3d_m(wire.a, wire.b), 1),
    }


@router.get("/power", response_model=PowerResponse)
def power(request: Request, save: str | None = None, world: str | None = None) -> PowerResponse:
    """Every power pole and tower, and the span of every wire between them, each end
    placed at its connector and named where the save names it."""
    st = require_world(request, save, world)

    projection = st.projection
    actors, edges = _graph(projection)
    named = _power_names(st, actors)
    degree = _degrees(edges)

    # One decode serves both the rows sent and the index the wire join reads, so the two
    # cannot be counted off different rows.
    pole_rows = list(saverows.iter_power_poles(projection))
    poles: list[PoleRow] = [
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

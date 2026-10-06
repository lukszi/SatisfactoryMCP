"""``/api/power``: every pole and tower, and the span of every wire between them.

The geometry half of a network whose connectivity the projection has carried since schema
11, and the JOIN is positional: ``wires[i]`` is the span of ``graph["power"][i]``, and this
is the one place the two lists are put back together.

A pole is an interned table row, so its ``cls`` is an index into a legend and
``saveio.rows`` answers ``None`` for an index past the end -- and its ``name`` with it,
because ``building_name`` is None in, None out. Its COORDINATES are the other way round:
``iter_power_poles`` drops a row whose class index or position will not read.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import rows as saverows
from ....domain.spatial import geo
from ....domain.world.state import WorldState
from ..serial import cm_to_m, instance_leaf, require_world, yaw_deg

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- power


class PoleRow(TypedDict):
    """A power pole, wall outlet or tower platform: where it stands and how busy it is.

    ``cls`` and ``name`` are nullable on the interned-table terms above; the coordinates are
    not, because the iterator drops a row that has none.

    ``connections`` is never null: a pole nothing is wired to reports 0, which is a
    measurement rather than a missing value -- it is in the geometry table and in no edge.
    """

    cls: str | None
    name: str | None
    x_m: float
    y_m: float
    z_m: float
    yaw: float | None
    connections: int


#: One power wire, as the straight line between the two connectors it is strung between.
#:
#: FUNCTIONAL SYNTAX because ``from`` is a Python keyword and so cannot be spelled in a
#: class body at all. The key is the wire's own and is what the page already reads.
#:
#: ``a_m`` and ``b_m`` are TUPLES, the only way a schema says "exactly three": typegen turns
#: them into a ``[number, number, number]`` the page indexes without a length guard.
#: ``iter_wires`` drops a row that is not six readable numbers, so each end is three floats
#: or the wire is not here.
#:
#: ``from`` and ``to`` are null where the projection carries no record naming that actor --
#: a hypertube entrance, a drop pod, the AWESOME Sink -- because a name guessed for those
#: would arrive looking like a reading. ``a_pole`` and ``b_pole`` are the index into this
#: response's own ``poles`` list of the pole that end terminates at, and null for an end
#: that lands on anything else; an INDEX because a ``PoleRow`` carries no instance id.
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

    ``edge_count`` is the one number here that is not the length of a list beside it: it is
    how many power EDGES the projection holds, and ``wire_count`` how many of those published
    a span. A save too old to carry the geometry answers a non-zero ``edge_count`` with a
    ``wire_count`` of 0, which is what tells "nothing to draw" from "nothing here".
    """

    poles: list[PoleRow]
    pole_count: int
    wires: list[WireRow]
    wire_count: int
    edge_count: int


def _power_names(st: WorldState) -> dict[str, str]:
    """Actor short name -> display name, for every actor this projection can name.

    ``graph["actors"]`` carries an IDENTITY per actor -- ``Build_SmelterMk1_C_2147380350``,
    the class with a serial glued on -- and this joins it to the class on the record lists,
    so a wire can say what is at each end without printing an engine id at a reader.

    Built once per request rather than per wire: a per-endpoint scan of six lists would be
    thousands of list walks for one payload.

    A pole gets in through its ``actor_index`` column. Everything else comes off the five
    record lists, which are not all of the world, so an end that lands on an actor no list
    carries comes out ``null`` rather than guessed at.
    """
    actors = st.projection.get("graph", {}).get("actors") or []
    out: dict[str, str] = {}
    for key in ("machines", "extractors", "generators", "attachments", "storage"):
        for row in st.projection.get(key) or ():
            if not isinstance(row, dict):
                continue
            name = st.game.building_name(row.get("cls"))
            if name:
                out[instance_leaf(row.get("instance", ""))] = name
    for pole in saverows.iter_power_poles(st.projection):
        if 0 <= pole.actor_index < len(actors):
            name = st.game.building_name(pole.cls)
            if name:
                out[str(actors[pole.actor_index])] = name
    return out


@router.get("/power", response_model=PowerResponse)
def power(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every power pole and tower, and the span of every wire between them.

    The geometry beside ``graph["power"]``, which says who is joined to whom and nothing
    about where. The two are joined by position -- ``wires[i]`` is the span of
    ``graph["power"][i]`` -- and this is the one place they are put back together.

    **A wire's ends are CONNECTOR positions, not building origins.** A connector sits at a
    fixed offset on its owner -- 7 m above a Mk1 pole, 2.1 m forward and 4.7 m to one side of
    a constructor's centre -- so origin-to-origin would draw every wire through the middle of
    the machine it feeds, and a client that files a wire on a storey by endpoint height puts
    it a storey high wherever the storeys are shorter than that offset. ``a_pole``/``b_pole``
    are there for exactly that: the pole a wire actually serves, at the height it stands.

    **``span_m`` is the CHORD.** A wire hangs as a catenary and this is the straight line
    between its ends, which is shorter -- and the save carries no sag either, since
    ``mCachedLength`` is the same chord. Three-dimensional, because a tower span climbs 24 m
    and that is real cable.

    ``from`` and ``to`` are in the EDGE's order, which the projection measured: the save's
    own endpoint order agrees with it only about half the time.
    """
    st = require_world(request, save, world)

    projection = st.projection
    actors = projection.get("graph", {}).get("actors") or []
    edges = projection.get("graph", {}).get("power") or []
    named = _power_names(st)

    # Counted once over the edge list: ``power["poles"]`` is geometry and holds no
    # connectivity, and counting per pole would be one scan of the edge list per pole.
    degree: dict[int, int] = {}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            for end in edge[:2]:
                if isinstance(end, int):
                    degree[end] = degree.get(end, 0) + 1

    # One decode of the pole table for both jobs below -- the rows the payload sends and the
    # actor-index lookup the wire join reads. Two passes would be two chances for the emitted
    # list and the joined indices to be counted off different rows.
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
    # -1 is "this pole has no actor index" and must not become a key: it would join every
    # unindexed pole to whichever one enumerated last.
    pole_at = {pole.actor_index: i for i, pole in enumerate(pole_rows) if pole.actor_index >= 0}

    wires = []
    for wire in saverows.iter_wires(projection):
        edge = edges[wire.index] if wire.index < len(edges) else None
        pair = edge[:2] if isinstance(edge, (list, tuple)) and len(edge) >= 2 else (None, None)
        ends = [
            named.get(str(actors[end])) if isinstance(end, int) and 0 <= end < len(actors) else None
            for end in pair
        ]
        a = [cm_to_m(v) for v in wire.a]
        b = [cm_to_m(v) for v in wire.b]
        wires.append(
            {
                "a_m": a,
                "b_m": b,
                "from": ends[0],
                "to": ends[1],
                # Read off the same edge as ``from``/``to``, so the four fields cannot
                # disagree about which actor an end belongs to.
                "a_pole": pole_at.get(pair[0]) if isinstance(pair[0], int) else None,
                "b_pole": pole_at.get(pair[1]) if isinstance(pair[1], int) else None,
                # Three-dimensional: a wire's climb is real cable, and ``distance_m`` would
                # drop the 24 m of it on a tower span.
                "span_m": round(geo.distance_3d_m(wire.a, wire.b), 1),
            }
        )

    return {
        "poles": poles,
        "pole_count": len(poles),
        "wires": wires,
        "wire_count": len(wires),
        "edge_count": len(edges),
    }

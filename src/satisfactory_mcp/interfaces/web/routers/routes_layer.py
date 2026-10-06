"""``/api/belts`` and ``/api/pipes``: the two routed networks, as built.

One file for two endpoints, because ``_curve_m`` is one translation that both call and
nothing else does: a bend read one way here and another way there is the failure mode a
shared helper exists to make impossible. Named ``routes_layer`` rather than ``routes``,
which under a package of FastAPI routers would read as the framework's own endpoint table.

A belt piece and a pipe piece are INTERNED table rows -- their class is an index into a
legend and ``saveio.rows`` answers ``None`` for an index past the end -- so ``BeltRow.cls``
and ``PipeRow.cls`` are nullable, and so are the ``name``s ``building_name`` renders from
them. An ATTACHMENT is an ordinary actor record, so ``AttachmentRow.cls`` is a string.

WARNING: the function names are the operation_ids -- renaming one churns the committed
schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import rows as saverows
from ....domain.world.state import WorldState
from ..serial import cm_to_m, error_response, world_state, xyz_m, yaw_deg

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------ the shared route geometry


#: A point on a route, ``[x, y, z]`` in game metres.
#:
#: A tuple rather than ``list[float]``, the only way a schema can say "exactly three":
#: typegen turns it into a ``[number, number, number]`` the page indexes without a length
#: guard. Not nullable in any position -- ``rows._points`` drops a point whose x, y or z
#: will not read as a number.
Point3M = tuple[float, float, float]

#: The tangents that bend ONE span of a route, ``[leave, arrive]`` in game metres.
#:
#: ``leave`` is the tangent leaving the point behind the span and ``arrive`` the one arriving
#: at the point ahead of it -- the pair a cubic Hermite between those two points takes. They
#: are displacements in the same space as the points, so whatever transform a client applies
#: to a point applies to these unchanged, including the y-flip that draws the map.
SpanCurveM = tuple[Point3M, Point3M]

#: A route's curve, one entry per span, in step with ``points_m``. **Nullable at two levels
#: and they mean different things.** ``null`` in a SLOT: that span is straight and is drawn
#: as the line it already was. ``null`` for the WHOLE field: the route has no bend anywhere
#: in it, or the projection predates the column -- both of which mean the same thing to a
#: client, which is why ``_curve_m`` spells them the same way.
RouteCurveM = list[SpanCurveM | None] | None


# ---------------------------------------------------------------------- belts


#: The docs dump's own native class for a conveyor LIFT, and how a lift is told apart from a
#: belt here -- not the ``Lift`` in ``Build_ConveyorLiftMk2_C``, because a substring match on
#: an engine id is not a classification and this distinction decides how the map draws a
#: piece.
LIFT_NATIVE = "FGBuildableConveyorLift"


class BeltClass(TypedDict):
    """What one belt class is. Spread into every ``BeltRow``; see the note there."""

    cls: str | None
    name: str | None
    lift: bool | None
    items_per_min: float | None


class BeltRow(TypedDict):
    """One conveyor piece, as the polyline it was actually built along.

    ``BeltClass``'s four fields are restated here rather than inherited, because inheritance
    would put them at the front and the handler spreads them into the MIDDLE.

    ``cls`` and ``name`` are nullable on the interned-table terms the module docstring gives.
    ``lift`` is nullable and the third answer is not a false one: a class the dump has no
    entry for gets ``null``, because "not a lift" would be a guess and the map draws a lift
    and a belt as different things. ``items_per_min`` is ``null`` where the dump is silent.
    """

    chain: int
    cls: str | None
    name: str | None
    lift: bool | None
    items_per_min: float | None
    points_m: list[Point3M]
    curve_m: RouteCurveM


class AttachmentRow(TypedDict):
    """A splitter or a merger: a piece of the belt network, drawn by the belt layer.

    ``cls`` and ``name`` are NOT nullable, unlike the belt row above: an attachment is an
    actor record. The coordinates ARE, because an actor whose transform did not decode has
    no ``pos``, and ``yaw`` is null where the projection predates schema 12.
    ``w_m``/``l_m`` are the dump's own soft clearance box, 4 x 4 m on all four of these
    classes and null for a class the dump has no entry for.
    """

    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw: float | None
    w_m: float | None
    l_m: float | None


class BeltsResponse(TypedDict):
    """What ``/api/belts`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    belts: list[BeltRow]
    count: int
    chains: int
    attachments: list[AttachmentRow]
    attachment_count: int


def _belt_class(st: WorldState, cls: str | None) -> BeltClass:
    """What one belt class is, resolved once per class rather than once per piece."""
    building = st.game.buildings.get(cls) if cls else None
    return {
        "cls": cls,
        "name": st.game.building_name(cls),
        "lift": None if building is None else building.native == LIFT_NATIVE,
        # The tier's own throughput, 60 to 780, instead of a "Mk3" the page would have to
        # parse back out of a display name.
        "items_per_min": (building.items_per_min or None) if building else None,
    }


def _curve_m(spans: Any, points: list) -> RouteCurveM:
    """A route's spline tangents as metres, or ``None`` where the route is straight.

    The projection's own column translated into this module's units and no further: a tangent
    is a displacement in the same space as a point, so the same divide-by-100 is the whole
    conversion. See ``RouteCurveM`` for what the two levels of ``None`` mean.

    Read guarded, entry by entry, on the same terms as the points beside it: a span that will
    not decode becomes a straight one and costs a curve rather than the route.
    """
    if not isinstance(spans, (list, tuple)) or len(spans) != len(points) - 1:
        return None
    out: list = []
    for entry in spans:
        if not isinstance(entry, (list, tuple)) or len(entry) != 6:
            out.append(None)  # 0, the projection's flat-span marker, lands here too
            continue
        try:
            vals = [cm_to_m(float(c)) for c in entry]
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append([vals[:3], vals[3:]])
    return out if any(out) else None


def _attachment_row(st: WorldState, row: dict) -> AttachmentRow:
    """One splitter or merger: where it stands, which way it faces, and what it is.

    Shorter than ``_record_row`` on purpose: a splitter has no recipe, no clock and nothing
    to pause, so the machine row's shape would be six null columns saying that six times.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "name": st.game.building_name(cls),
        **xyz_m(row.get("pos")),
        "yaw": yaw_deg(row.get("yaw")),
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
    }


@router.get("/belts", response_model=BeltsResponse)
def belts(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every conveyor belt and lift, as the polyline it was actually built along.

    The pieces arrive interned the way the structures next door are, in world centimetres;
    the legend is resolved here so the page does not have to carry it, and the row is decoded
    by ``core.saveio.rows`` so a malformed segment costs that segment rather than the network.

    **Points are in travel order, input to output.** The save stores them output-first and
    the projection reverses them, so a client can draw direction along a run without knowing
    that. ``chain`` is the belt chain a piece belongs to, so "the whole run" is a group-by
    rather than a geometry problem.

    **``curve_m`` is what makes a curved belt curved.** ``points_m`` are the spline's control
    points and were never the whole spline -- the chain trailer stores two tangents beside
    each one -- so a bend drawn from the points alone is the chords between its corners, out
    by up to 16.4 m of arc on a single piece.

    **A lift is a belt whose top-down polyline is a single point.** Every lift on the
    reference save has exactly zero horizontal extent, so a map that draws them as lines
    draws nothing at all where they are and the client owes them a glyph instead.

    **``attachments`` rides along rather than travelling with the machines**, because a
    splitter runs no recipe, draws no power and is meaningless without the runs either side
    of it. That is also what keeps it from being drawn twice: it is in no other payload, so a
    map with the machines layer on and the belts layer off shows no splitters at all.

    Sent one row per piece, ungrouped, the same posture ``/api/structures`` takes: the
    per-piece class is what a popup reads.
    """
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)

    resolved: dict[int, BeltClass] = {}
    rows = []
    for seg in saverows.iter_belt_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _belt_class(st, seg.cls)
        points = [[cm_to_m(x), cm_to_m(y), cm_to_m(z)] for x, y, z in seg.points]
        rows.append(
            {
                "chain": seg.chain,
                **resolved[seg.class_index],
                "points_m": points,
                "curve_m": _curve_m(seg.spans, points),
            }
        )
    attachments = [
        _attachment_row(st, row)
        for row in st.projection.get("attachments") or ()
        if isinstance(row, dict)
    ]
    return {
        "belts": rows,
        "count": len(rows),
        "chains": len({r["chain"] for r in rows}),
        "attachments": attachments,
        "attachment_count": len(attachments),
    }


# ---------------------------------------------------------------------- pipes


#: Which way the fluid goes, where the network settles it.
PipeDirection = Literal["forward", "reverse", "unknown"]

#: What the direction was INFERRED FROM: the four values ``domain/world/flow.py`` defines,
#: and there is no fifth. Never null -- the missing-flow case defaults to the same
#: ``unresolved`` the resolver itself sends when it declines.
#:
#: A closed union rather than ``str``, because the page maps it with a record keyed by
#: exactly these four: a fifth basis should be a compile error there and a loud failure
#: here, rather than an arrow that says nothing.
PipeFlowBasis = Literal["machine port", "pump", "propagated", "unresolved"]


class PipeClass(TypedDict):
    """What one pipe class is. Spread into every ``PipeRow``; see the note there."""

    cls: str | None
    name: str | None
    flow_m3_min: float | None


class PipeRow(TypedDict):
    """One fluid pipe, as the polyline it was built along, and what it carries.

    The class fields sit in the MIDDLE, after the network join and before the geometry,
    because that is where ``**resolved[seg.class_index]`` lands in the handler.
    ``cls``/``name`` are nullable on the interned-table terms the module docstring gives, and
    ``flow_m3_min`` is null where the dump is silent.

    ``row`` is this pipe's position in the RAW segments table -- the join ``/api/floors``
    keys a pipe run by, sent rather than counted so that a torn row leaves a gap here instead
    of silently renumbering everything after it.

    ``network`` is the game's own ``FGPipeNetwork`` id forwarded whole, not the index into
    this payload's own list, and is null for a pipe no network claims.
    """

    row: int
    direction: PipeDirection
    basis: PipeFlowBasis
    network: int | None
    fluid: str | None
    fluid_name: str | None
    cls: str | None
    name: str | None
    flow_m3_min: float | None
    points_m: list[Point3M]
    curve_m: RouteCurveM


class PipesResponse(TypedDict):
    """What ``/api/pipes`` sends on a 200. An error is a 4xx with ``{"error": ...}``."""

    pipes: list[PipeRow]
    count: int
    networks: int
    directed: int


def _pipe_class(st: WorldState, cls: str | None) -> PipeClass:
    """What one pipe class is, resolved once per class rather than once per piece."""
    building = st.game.buildings.get(cls) if cls else None
    return {
        "cls": cls,
        "name": st.game.building_name(cls),
        # The dump's own throughput for the tier -- 300 on Mk1, 600 on Mk2 -- rather than a
        # "Mk2" the page would have to parse back out of a display name.
        "flow_m3_min": (building.flow_m3_min or None) if building else None,
    }


@router.get("/pipes", response_model=PipesResponse)
def pipes(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every fluid pipe, as the polyline it was actually built along, and what it carries.

    The belts' other half, and the same shape one layer down.

    **Each pipe says which fluid it carries**, which is the thing a belt cannot say: the game
    keeps an ``FGPipeNetwork`` per connected plumbing system with the fluid on it and its
    members listed, so ``fluid`` is the world's own answer rather than an inference from what
    the pipe is plugged into.

    **``direction`` is INFERRED, and ``basis`` says from what.** Nothing on a pipe records
    which way the fluid goes, and the points are in the order the file stores them. But the
    plumbing AROUND it records a great deal: the save serialises every fluid coupling and
    names a machine's port ``PipeInputFactory`` or ``PipeOutputFactory``.
    ``domain/world/flow.py`` reads that graph and declines wherever more than one answer is
    consistent. So ``direction`` is ``forward`` along ``points_m``, ``reverse`` against it,
    or ``unknown``, and ``basis`` is one of:

    * ``machine port`` -- this very pipe ends at a port the save TYPES. Barely an inference.
    * ``pump`` -- a pump or valve at one end, one-way by construction.
    * ``propagated`` -- only the shape of the wider network settles it.
    * ``unresolved`` -- and then ``direction`` is ``unknown``. A pipe in a loop, or a trunk
      with producers and consumers on both sides, genuinely has no fixed direction.

    A client may draw an arrow on the first three and must not on the fourth.

    **``curve_m`` rides here too, on exactly the belts' terms.** Pipes are straight runs and
    elbows, and the six points of an elbow are its corners rather than its curve: an elbow
    drawn from the points alone is the polygon cutting the corner it was built to round.

    Not in here: pumps, junctions, valves and fluid buffers. They carry no spline at all,
    only a header position, so they are a different row shape -- the same question the belts
    key leaves open about splitters and mergers.
    """
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)

    networks = list((st.projection.get("pipes") or {}).get("networks") or ())
    # Positional against ``segments``, so a projection too old to carry the join reads as one
    # long row of "unknown" rather than as an error. ``seg.index`` is the row's position in
    # the raw table rather than a count of what decoded, which is what keeps the two lists
    # lined up when a row is torn.
    flows = st.pipe_flow
    resolved: dict[int, PipeClass] = {}
    rows = []
    for seg in saverows.iter_pipe_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _pipe_class(st, seg.cls)
        points = [[cm_to_m(x), cm_to_m(y), cm_to_m(z)] for x, y, z in seg.points]
        net = seg.network_index
        entry = networks[net] if 0 <= net < len(networks) else {}
        fluid = entry.get("fluid") if isinstance(entry, dict) else None
        flow = flows[seg.index] if 0 <= seg.index < len(flows) else {}
        rows.append(
            {
                "row": seg.index,
                "direction": flow.get("direction", "unknown"),
                "basis": flow.get("basis", "unresolved"),
                "network": entry.get("id") if isinstance(entry, dict) else None,
                "fluid": fluid,
                # Resolved against the dump, so a popup never has to show a reader a
                # ``Desc_…_C``.
                "fluid_name": st.game.item_name(fluid) if fluid else None,
                **resolved[seg.class_index],
                "points_m": points,
                "curve_m": _curve_m(seg.spans, points),
            }
        )
    return {
        "pipes": rows,
        "count": len(rows),
        "networks": len({r["network"] for r in rows if r["network"] is not None}),
        "directed": sum(1 for r in rows if r["direction"] != "unknown"),
    }

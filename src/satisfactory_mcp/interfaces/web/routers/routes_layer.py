"""``/api/belts`` and ``/api/pipes``: the two routed networks, as built.

One module for both because ``_curve_m`` is the one translation of a bend they share. What
each field means: docs/web-wire.md "Belts and pipes". Handler names are operation_ids.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import rows as saverows
from ....domain.world.state import WorldState
from ..serial import cm_to_m, placement_fields, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------ the shared route geometry


#: ``[x, y, z]`` in metres; a tuple so typegen emits exactly three numbers.
Point3M = tuple[float, float, float]

#: The ``[leave, arrive]`` tangents that bend one span, in metres.
SpanCurveM = tuple[Point3M, Point3M]

#: One entry per span; a null slot is a straight span, a null field a straight route.
RouteCurveM = list[SpanCurveM | None] | None


# ---------------------------------------------------------------------- belts


#: How a lift is told from a belt: the dump's native class, never a substring of the class id.
LIFT_NATIVE = "FGBuildableConveyorLift"


class BeltClass(TypedDict):
    """What one belt class is. Spread into every ``BeltRow``."""

    cls: str | None
    name: str | None
    lift: bool | None
    items_per_min: float | None


class BeltRow(TypedDict):
    """One conveyor piece, as the polyline it was actually built along.

    ``cls`` and ``name`` are null for a piece whose class index is past the legend's end.
    ``lift`` is null, never false, for a class the dump has no entry for, and
    ``items_per_min`` is null where the dump is silent.
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

    ``cls`` and ``name`` are not nullable: an attachment is an actor record. The coordinates
    are null where the transform did not decode, ``yaw`` where the projection predates schema
    12, and ``w_m``/``l_m`` for a class the dump has no entry for.
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
        "items_per_min": (building.items_per_min or None) if building else None,
    }


def _points_m(points: Any) -> list[list[float | None]]:
    return [[cm_to_m(x), cm_to_m(y), cm_to_m(z)] for x, y, z in points]


def _curve_m(spans: Any, points: list) -> RouteCurveM:
    """A route's spline tangents in metres, or ``None`` where the route is straight.

    A tangent is a displacement in the same space as a point, so dividing by 100 is the whole
    conversion. A span that will not decode becomes straight rather than costing the route.
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


@router.get("/belts", response_model=BeltsResponse)
def belts(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every conveyor belt and lift, as the polyline it was actually built along, in travel
    order, with the splitters and mergers on them."""
    st = require_world(request, save, world)

    resolved: dict[int, BeltClass] = {}
    rows = []
    for seg in saverows.iter_belt_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _belt_class(st, seg.cls)
        points = _points_m(seg.points)
        rows.append(
            {
                "chain": seg.chain,
                **resolved[seg.class_index],
                "points_m": points,
                "curve_m": _curve_m(seg.spans, points),
            }
        )
    attachments = [
        placement_fields(st.game, row)
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

#: What ``direction`` was inferred from: the four values ``domain/world/flow.py`` defines.
#: Closed, so a fifth fails here and in the page's record keyed by them, never silently.
PipeFlowBasis = Literal["machine port", "pump", "propagated", "unresolved"]


class PipeClass(TypedDict):
    """What one pipe class is. Spread into every ``PipeRow``."""

    cls: str | None
    name: str | None
    flow_m3_min: float | None


class PipeRow(TypedDict):
    """One fluid pipe, as the polyline it was built along, and what it carries.

    ``cls``/``name`` are null past the legend's end and ``flow_m3_min`` where the dump is
    silent. ``row`` is the pipe's position in the raw segments table, the key ``/api/floors``
    uses. ``network`` is the game's own ``FGPipeNetwork`` id, null for an unclaimed pipe.
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
        "flow_m3_min": (building.flow_m3_min or None) if building else None,
    }


def _pipe_row(st: WorldState, seg: Any, networks: list, flows: list, pipe_class: dict) -> dict:
    """One pipe: the network that claims it, its inferred direction, its class and shape.

    ``seg.index`` is the raw row position, so the positional joins to ``networks`` and
    ``flows`` stay lined up when a row is torn, and a projection too old for them reads as
    ``unknown`` rather than as an error.
    """
    entry = networks[seg.network_index] if 0 <= seg.network_index < len(networks) else {}
    fluid = entry.get("fluid") if isinstance(entry, dict) else None
    flow = flows[seg.index] if 0 <= seg.index < len(flows) else {}
    points = _points_m(seg.points)
    return {
        "row": seg.index,
        "direction": flow.get("direction", "unknown"),
        "basis": flow.get("basis", "unresolved"),
        "network": entry.get("id") if isinstance(entry, dict) else None,
        "fluid": fluid,
        "fluid_name": st.game.item_name(fluid) if fluid else None,
        **pipe_class,
        "points_m": points,
        "curve_m": _curve_m(seg.spans, points),
    }


@router.get("/pipes", response_model=PipesResponse)
def pipes(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every fluid pipe, as the polyline it was actually built along, the fluid it carries
    and which way it flows where the plumbing around it settles that."""
    st = require_world(request, save, world)

    networks = list((st.projection.get("pipes") or {}).get("networks") or ())
    flows = st.pipe_flow
    resolved: dict[int, PipeClass] = {}
    rows = []
    for seg in saverows.iter_pipe_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _pipe_class(st, seg.cls)
        rows.append(_pipe_row(st, seg, networks, flows, resolved[seg.class_index]))
    return {
        "pipes": rows,
        "count": len(rows),
        "networks": len({r["network"] for r in rows if r["network"] is not None}),
        "directed": sum(1 for r in rows if r["direction"] != "unknown"),
    }

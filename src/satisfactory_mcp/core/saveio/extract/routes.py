"""Belt and pipe routes as polylines with optional curve columns.

Row layouts, the output-first chain order, the translated-not-rotated frame and the tangent
bound are in ``docs/save-projection.md`` §6.16.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import TypeAlias

from ..schema import BeltsBlock, PipeNetwork, PipesBlock, Point, RouteRow, SplineSpan
from .census import CHAIN_NOTES_SHOWN, Drops
from .interning import Interner
from .parser import PARSE_ERROR, ParsedObject, SaveValue
from .readers import as_sequence, ref_path, struct_fields, to_float

__all__ = [
    "TANGENT_EPS_CM",
    "ChainActor",
    "HeldNetwork",
    "PipeActor",
    "belts",
    "bulge",
    "conveyor_class",
    "pipes",
    "spans",
]

#: ``(actorPosition, chainActor)`` per conveyor chain.
ChainActor: TypeAlias = tuple[Sequence[float] | None, ParsedObject]
#: ``(class, instanceName, actorPosition, mSplineData)`` per pipe.
PipeActor: TypeAlias = tuple[str, str, Sequence[float] | None, SaveValue]
#: ``(mPipeNetworkID, fluidClass, [memberPath, ...])`` per pipe network.
HeldNetwork: TypeAlias = tuple[SaveValue, str | None, list[str | None]]
#: A spline point as read: ``(location, arriveTangent, leaveTangent)``.
_RawPoint: TypeAlias = tuple[SaveValue, SaveValue, SaveValue]

#: How far a span may leave its chord, in centimetres, before its tangents are carried: the
#: control points' own whole-centimetre resolution (§6.16).
TANGENT_EPS_CM = 1.0

#: The peak magnitude of both cubic Hermite tangent basis functions on ``[0, 1]``.
_HERMITE_PEAK = 4.0 / 27.0


def _length(x: float, y: float, z: float) -> float:
    """Length of a 3-vector. Three scalars, so a 2-tuple cannot pass for a spline tangent:
    ``test_geo_centroid`` reserves the stdlib distance helper package-wide."""
    return math.sqrt(x * x + y * y + z * z)


def bulge(
    p0: Sequence[float], m0: Sequence[float], p1: Sequence[float], m1: Sequence[float]
) -> float:
    """An UPPER BOUND, in centimetres, on how far a cubic Hermite span leaves its own chord.

    It may only overstate: overstating costs bytes, understating flattens a real bend. The
    derivation and its check against a 512-point tessellation are in §6.16.
    """
    chord_x, chord_y, chord_z = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    chord_sq = chord_x * chord_x + chord_y * chord_y + chord_z * chord_z
    if chord_sq <= 0:
        # Coincident control points (where a lift meets its belt): all of both tangents is
        # sideways.
        return _HERMITE_PEAK * (_length(*m0) + _length(*m1))
    leave_along = (m0[0] * chord_x + m0[1] * chord_y + m0[2] * chord_z) / chord_sq
    arrive_along = (m1[0] * chord_x + m1[1] * chord_y + m1[2] * chord_z) / chord_sq
    across = _length(
        m0[0] - leave_along * chord_x, m0[1] - leave_along * chord_y, m0[2] - leave_along * chord_z
    ) + _length(
        m1[0] - arrive_along * chord_x,
        m1[1] - arrive_along * chord_y,
        m1[2] - arrive_along * chord_z,
    )

    # The along-chord position u(t) = a t^3 + b t^2 + c t, solved exactly for its extremes.
    a, b, c = (
        leave_along + arrive_along - 2.0,
        3.0 - 2.0 * leave_along - arrive_along,
        leave_along,
    )
    low, high = 0.0, 1.0
    if a:
        disc = 4.0 * b * b - 12.0 * a * c
        roots = (
            ((-2.0 * b + math.sqrt(disc)) / (6.0 * a), (-2.0 * b - math.sqrt(disc)) / (6.0 * a))
            if disc >= 0.0
            else ()
        )
    else:
        roots = (-c / (2.0 * b),) if b else ()
    for t in roots:
        if 0.0 < t < 1.0:
            u = ((a * t + b) * t + c) * t
            low, high = min(low, u), max(high, u)
    beyond = (max(0.0, -low) + max(0.0, high - 1.0)) * _length(chord_x, chord_y, chord_z)
    return _HERMITE_PEAK * across + beyond


def spans(
    points: list[Point], tangents: list[tuple[list[int], list[int]]]
) -> list[list[SplineSpan]]:
    """The curve column for one route: ``[[...]]`` to splat onto the row, ``[]`` if straight.

    One entry per span, ``0`` where the span is flat by `bulge`; a route with no bend gets no
    column at all, so its row stays byte-identical to schema 14's.
    """
    columns: list[SplineSpan] = []
    curved = False
    for index in range(len(points) - 1):
        leave, arrive = tangents[index][1], tangents[index + 1][0]
        if bulge(points[index], leave, points[index + 1], arrive) < TANGENT_EPS_CM:
            columns.append(0)
        else:
            columns.append(leave + arrive)
            curved = True
    return [columns] if curved else []


def _origin(origin: Sequence[float] | None) -> tuple[float, float, float] | None:
    """An actor position as three floats, or None where it will not read."""
    try:
        x, y, z = (to_float(v) for v in as_sequence(origin))
    except (TypeError, ValueError):
        return None
    return x, y, z


def _translated_route(
    raw_points: Iterable[SaveValue],
    origin_xyz: tuple[float, float, float],
    read_point: Callable[[SaveValue], _RawPoint],
    drops: Drops,
    point_drop_reason: str,
) -> tuple[list[Point], list[tuple[list[int], list[int]]]]:
    """``(points, tangents)`` in world centimetres; an unreadable point costs only itself.

    ``read_point`` returns a raw ``(location, arrive, leave)``. All three are read and rounded
    before either list grows, so a fault cannot leave the two lists one apart. Only the points
    are translated: tangents are displacements.
    """
    origin_x, origin_y, origin_z = origin_xyz
    points: list[list[int]] = []
    tangents: list[tuple[list[int], list[int]]] = []
    for raw in raw_points:
        try:
            raw_at, arrive, leave = read_point(raw)
            location = as_sequence(raw_at)
            # Rounded, not truncated like `structures`: that field's truncation is banked.
            at = [
                round(_coordinate(location[0]) + origin_x),
                round(_coordinate(location[1]) + origin_y),
                round(_coordinate(location[2]) + origin_z),
            ]
            pair = (
                [round(_coordinate(v)) for v in as_sequence(arrive)],
                [round(_coordinate(v)) for v in as_sequence(leave)],
            )
        except (TypeError, ValueError, IndexError):
            drops[point_drop_reason] += 1
            continue
        points.append(at)
        tangents.append(pair)
    return points, tangents


def _coordinate(value: SaveValue) -> float:
    """A spline coordinate as the number it is, or the ``TypeError`` arithmetic raises."""
    if isinstance(value, (int, float)):
        return value
    raise TypeError(f"a spline coordinate is a {type(value).__name__}, not a number")


def _belt_point(raw: SaveValue) -> _RawPoint:
    point = as_sequence(raw)
    return point[0], point[1], point[2]


def _pipe_point(raw: SaveValue) -> _RawPoint:
    fields = struct_fields(raw)
    return (
        fields.get("Location"),
        fields.get("ArriveTangent") or (0, 0, 0),
        fields.get("LeaveTangent") or (0, 0, 0),
    )


def _report_unreadable_chain(exc: BaseException, notes_so_far: int) -> int:
    """Name the first few undecodable chains on stderr; the TYPE says whether the format moved."""
    if notes_so_far < CHAIN_NOTES_SHOWN:
        print(f"pioneersav: conveyor chain skipped: {exc}", file=sys.stderr)
    elif notes_so_far == CHAIN_NOTES_SHOWN:
        print(
            "pioneersav: further unreadable conveyor chains not listed; "
            "the projection's warnings carry the total",
            file=sys.stderr,
        )
    else:
        return notes_so_far
    return notes_so_far + 1


def belts(chains: list[ChainActor], actors: Interner, drops: Drops) -> BeltsBlock:
    """Every conveyor's route, as ``belts`` rows (§6.16).

    ``chains`` is ``[(actorPosition, chainActor), ...]``; ``actors`` is the frozen graph table.
    The geometry is in the chain actor's trailing bytes, decoded lazily here.
    """
    classes = Interner()
    segments: list[RouteRow] = []
    chain_index = 0
    chain_notes = 0

    for origin, obj in chains:
        try:
            info = obj.actorSpecificInfo
        except PARSE_ERROR as exc:
            drops["conveyor chain(s) dropped: the trailing bytes would not decode"] += 1
            chain_notes = _report_unreadable_chain(exc, chain_notes)
            continue
        if not (isinstance(info, list) and len(info) >= 3 and isinstance(info[2], list)):
            drops["conveyor chain(s) dropped: no segment list in actorSpecificInfo"] += 1
            continue
        origin_xyz = _origin(origin)
        if origin_xyz is None:
            drops["conveyor chain(s) dropped: the actor position would not read"] += 1
            continue

        rows: list[RouteRow] = []
        # Stored output-first: reversed, the rows come out in travel order.
        for segment in reversed(info[2]):
            if not (isinstance(segment, list) and len(segment) >= 3):
                drops["belt segment(s) dropped: no [?, class, points] to read"] += 1
                continue
            instance = ref_path(segment[1]) or ""
            cls = conveyor_class(instance)
            if not cls:
                drops["belt segment(s) dropped: the class is not a known conveyor"] += 1
                continue
            points, tangents = _translated_route(
                segment[2] if isinstance(segment[2], list) else (),
                origin_xyz,
                _belt_point,
                drops,
                "belt point(s) dropped: location or tangent would not read",
            )
            if len(points) < 2:
                drops["belt segment(s) dropped: fewer than 2 readable points"] += 1
                continue
            rows.append(
                [
                    chain_index,
                    classes.intern(cls),
                    points,
                    actors.index_of(instance.rsplit(".", 1)[-1]),
                    *spans(points, tangents),
                ]
            )
        if rows:
            segments.extend(rows)
            chain_index += 1

    return {"classes": classes.names(), "segments": segments}


def conveyor_class(path: str) -> str:
    """``...PersistentLevel.Build_ConveyorBeltMk3_C_1264`` -> ``Build_ConveyorBeltMk3_C``.

    A chain names its belts by instance; stripping the index was checked against the real
    ``typePath`` of every segment in the save folder.
    """
    leaf = path.rsplit(".", 1)[-1]
    parts = leaf.split("_")
    if parts and parts[-1].isdigit():
        parts.pop()
    return "_".join(parts) if len(parts) > 1 else ""


def pipes(
    pipe_actors: list[PipeActor], networks: list[HeldNetwork], actors: Interner, drops: Drops
) -> PipesBlock:
    """Every fluid pipe's route as ``pipes`` rows, and the fluid each one carries (§6.16).

    ``pipe_actors`` is ``[(class, instanceName, actorPosition, mSplineData), ...]`` and
    ``networks`` ``[(mPipeNetworkID, fluidClass, [memberPath, ...]), ...]``. Flow direction is
    not on a pipe; ``domain/world/flow.py`` infers it.
    """
    network_of: dict[str, int] = {}
    network_rows: list[PipeNetwork] = []
    for network_id, fluid, members in networks:
        row_index = len(network_rows)
        network_rows.append(
            {"id": network_id if isinstance(network_id, int) else None, "fluid": fluid}
        )
        for member in members:
            if member:
                network_of[member] = row_index

    classes = Interner()
    segments: list[RouteRow] = []

    for cls, instance, origin, spline in pipe_actors:
        if not isinstance(spline, list):
            drops["pipe(s) dropped: mSplineData is not a list of points"] += 1
            continue
        origin_xyz = _origin(origin)
        if origin_xyz is None:
            drops["pipe(s) dropped: the actor position would not read"] += 1
            continue
        points, tangents = _translated_route(
            spline,
            origin_xyz,
            _pipe_point,
            drops,
            "pipe point(s) dropped: Location or tangent would not read",
        )
        if len(points) < 2:
            drops["pipe(s) dropped: fewer than 2 readable spline points"] += 1
            continue
        segments.append(
            [
                network_of.get(str(instance), -1),
                classes.intern(cls),
                points,
                actors.index_of(str(instance).rsplit(".", 1)[-1]),
                *spans(points, tangents),
            ]
        )

    return {"classes": classes.names(), "networks": network_rows, "segments": segments}

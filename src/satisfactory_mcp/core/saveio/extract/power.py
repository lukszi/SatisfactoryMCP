"""The power network's geometry: the poles, and the drawn span of every wire.

``power["wires"][i]`` is the span of ``graph["power"][i]``, always, because one pass writes
both; see ``docs/save-projection.md`` §6.12.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, TypeAlias

from ..schema import PoleRow, PoleTable, Position, PowerBlock, PowerEdge, WireRow
from .census import Drops
from .interning import Interner
from .readers import to_float

if TYPE_CHECKING:
    from .parser import SaveValue

__all__ = ["PoleActor", "WireSpan", "power_network", "wire_span"]

#: ``(class, instanceName, pos, yaw)`` per pole.
PoleActor: TypeAlias = tuple[str, str, Position | None, float | None]
#: A wire's two drawn ends, ``[x, y, z]`` each in whole world centimetres.
WireSpan: TypeAlias = tuple[list[int], list[int]]


def wire_span(raw: SaveValue) -> WireSpan | None:
    """The two ends of one power wire, in WORLD centimetres, or None if it has no pair.

    The first ``Locations`` pair of ``mWireInstances``, which is one strand of a two-strand
    tower line (§6.12). Walked, because the decoded property nests values beside their types.
    """
    found: list[list[int]] = []

    def walk(node: SaveValue) -> None:
        if len(found) >= 2 or not isinstance(node, (list, tuple)):
            return
        if len(node) == 2 and node[0] == "Locations":
            point = node[1]
            if isinstance(point, (list, tuple)) and len(point) == 3:
                try:
                    found.append(
                        [
                            round(to_float(point[0])),
                            round(to_float(point[1])),
                            round(to_float(point[2])),
                        ]
                    )
                except (TypeError, ValueError):
                    pass
                return
        for child in node:
            walk(child)

    walk(raw)
    return (found[0], found[1]) if len(found) == 2 else None


def power_network(
    wire_ends: dict[str, list[tuple[str, str]]],
    wire_geometry: dict[str, WireSpan],
    pole_actors: list[PoleActor],
    actor_positions: dict[str, tuple[float, ...]],
    actors: Interner,
    drops: Drops,
) -> tuple[list[PowerEdge], PowerBlock]:
    """``(graph["power"], power)``: the edge list and the geometry that is its twin.

    The wire pass is the last to mint actor indices, so ``actors`` is frozen after it and the
    pole table only reads it.
    """
    edges, wires = _wire_edges(wire_ends, wire_geometry, actor_positions, actors)
    actors.freeze()
    return edges, {"poles": _pole_table(pole_actors, actors, drops), "wires": wires}


def _wire_edges(
    wire_ends: dict[str, list[tuple[str, str]]],
    wire_geometry: dict[str, WireSpan],
    actor_positions: dict[str, tuple[float, ...]],
    actors: Interner,
) -> tuple[list[PowerEdge], list[WireRow | None]]:
    """One edge and one span per wire with exactly two connections, in one pass."""
    edges: list[PowerEdge] = []
    wires: list[WireRow | None] = []
    for path, ends in wire_ends.items():
        # A half-built or orphaned line is dropped rather than guessed at.
        if len(ends) != 2:
            continue
        a_owner, b_owner = ends[0][0], ends[1][0]
        edges.append([actors.intern(a_owner), actors.intern(b_owner)])
        span = wire_geometry.get(str(path).rsplit(".", 1)[-1])
        wires.append(_wire_row(span, a_owner, b_owner, actor_positions))
    return edges, wires


def _pole_table(pole_actors: list[PoleActor], actors: Interner, drops: Drops) -> PoleTable:
    """``{classes, instances}`` rows ``[classIndex, x, y, z, yaw, actorIndex]`` (§6.16)."""
    classes = Interner()
    instances: list[PoleRow] = []
    for cls, instance, at, yaw in pole_actors:
        if at is None:
            drops["pole(s) dropped: the actor position would not read"] += 1
            continue
        instances.append(
            [
                classes.intern(cls),
                round(at[0]),
                round(at[1]),
                round(at[2]),
                yaw,
                actors.index_of(str(instance).rsplit(".", 1)[-1]),
            ]
        )
    return {"classes": classes.names(), "instances": instances}


def _wire_row(
    span: WireSpan | None,
    a_owner: str,
    b_owner: str,
    actor_positions: dict[str, tuple[float, ...]],
) -> WireRow | None:
    """One wire's six numbers, each end assigned to the nearer of the edge's two actors.

    The save's own end order agrees with the edge's only half the time (§6.12). Plan distance,
    because the candidates differ by metres vertically and hundreds horizontally.
    """
    if span is None:
        return None
    a, b = span
    at_a, at_b = actor_positions.get(a_owner), actor_positions.get(b_owner)
    if at_a is not None and at_b is not None:
        straight = _plan_gap(a, at_a) + _plan_gap(b, at_b)
        crossed = _plan_gap(b, at_a) + _plan_gap(a, at_b)
        if crossed < straight:
            a, b = b, a
    return [*a, *b]


def _plan_gap(point: Sequence[float], at: Sequence[float]) -> float:
    """Horizontal distance between a wire endpoint and an actor's origin, centimetres."""
    return math.hypot(point[0] - at[0], point[1] - at[1])

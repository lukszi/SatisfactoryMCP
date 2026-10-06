"""The power network's geometry: the poles, and the drawn span of every wire.

``power["wires"][i]`` is the span of ``graph["power"][i]``, always, because one pass writes
both; see ``docs/save-projection.md`` §6.12.
"""

from __future__ import annotations

import math

from .census import Drops
from .interning import Interner

__all__ = ["power_network", "wire_span"]


def wire_span(raw) -> tuple[list, list] | None:
    """The two ends of one power wire, in WORLD centimetres, or None if it has no pair.

    The first ``Locations`` pair of ``mWireInstances``, which is one strand of a two-strand
    tower line (§6.12). Walked, because the decoded property nests values beside their types.
    """
    found: list[list] = []

    def walk(node) -> None:
        if len(found) >= 2 or not isinstance(node, (list, tuple)):
            return
        if len(node) == 2 and node[0] == "Locations":
            point = node[1]
            if isinstance(point, (list, tuple)) and len(point) == 3:
                try:
                    found.append(
                        [round(float(point[0])), round(float(point[1])), round(float(point[2]))]
                    )
                except (TypeError, ValueError):
                    pass
                return
        for child in node:
            walk(child)

    walk(raw)
    return (found[0], found[1]) if len(found) == 2 else None


def power_network(
    wire_ends: dict,
    wire_geometry: dict,
    pole_actors: list,
    actor_positions: dict,
    actors: Interner,
    drops: Drops,
) -> tuple[list, dict]:
    """``(graph["power"], power)``: the edge list and the geometry that is its twin.

    The wire pass is the last to mint actor indices, so ``actors`` is frozen after it and the
    pole table only reads it.
    """
    edges, wires = _wire_edges(wire_ends, wire_geometry, actor_positions, actors)
    actors.freeze()
    return edges, {"poles": _pole_table(pole_actors, actors, drops), "wires": wires}


def _wire_edges(
    wire_ends: dict, wire_geometry: dict, actor_positions: dict, actors: Interner
) -> tuple[list[list[int]], list[list | None]]:
    """One edge and one span per wire with exactly two connections, in one pass."""
    edges: list[list[int]] = []
    wires: list[list | None] = []
    for path, ends in wire_ends.items():
        # A half-built or orphaned line is dropped rather than guessed at.
        if len(ends) != 2:
            continue
        a_owner, b_owner = ends[0][0], ends[1][0]
        edges.append([actors.intern(a_owner), actors.intern(b_owner)])
        span = wire_geometry.get(str(path).rsplit(".", 1)[-1])
        wires.append(_wire_row(span, a_owner, b_owner, actor_positions))
    return edges, wires


def _pole_table(pole_actors: list, actors: Interner, drops: Drops) -> dict:
    """``{classes, instances}`` rows ``[classIndex, x, y, z, yaw, actorIndex]`` (§6.16)."""
    classes = Interner()
    instances: list[list] = []
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


def _wire_row(span, a_owner: str, b_owner: str, actor_positions: dict) -> list | None:
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


def _plan_gap(point, at) -> float:
    """Horizontal distance between a wire endpoint and an actor's origin, centimetres."""
    return math.hypot(point[0] - at[0], point[1] - at[1])

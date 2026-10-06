"""What a connector role name says about the edge it sits on.

``graph["material"]`` holds conveyors, pipes and hypertubes (which carry a PLAYER) in one
flat list that only the role names tell apart -- 126 of the reference world's 11,664 edges
are hypertubes -- so every consumer of that layer classifies here.
"""

from __future__ import annotations

__all__ = [
    "CONVEYOR",
    "HYPERTUBE",
    "PIPE",
    "edge_medium",
    "is_hypertube_edge",
    "medium",
    "port_direction",
]

#: What one edge carries; an unknown role (``None``) is a gap in this table, not a hypertube.
CONVEYOR = "conveyor"
PIPE = "pipe"
HYPERTUBE = "hypertube"

#: ``PipeHyper*``, and matched by name rather than by a ``Hyper`` substring so that a role
#: added later is unknown rather than silently classified.
_HYPERTUBE_ROLES = frozenset(
    {
        "PipeHyperConnection0",
        "PipeHyperConnection1",
        "PipeHyperStartConnection",
    }
)

#: A belt's own two ends are ``ConveyorAny0``/``1``; a machine, splitter or merger types its
#: port ``Input``/``Output`` with the port's index.
_CONVEYOR_ROLES = frozenset(
    {"ConveyorAny0", "ConveyorAny1"}
    | {f"Input{i}" for i in range(8)}
    | {f"Output{i}" for i in range(8)}
)

#: Fluid plumbing. ``Connection0``-``3`` are a junction's, pump's or valve's ports and carry
#: no ``Pipe`` in their names, which is why this is a list and not a substring test.
_PIPE_ROLES = frozenset(
    {
        "PipelineConnection0",
        "PipelineConnection1",
        "FGPipeConnectionFactory",
        "PipeInputFactory",
        "PipeOutputFactory",
        "ConnectionAny0",
        "ConnectionAny1",
        "Connection0",
        "Connection1",
        "Connection2",
        "Connection3",
    }
)


def medium(role: str) -> str | None:
    """What a single connector carries, or ``None`` where the name does not say."""
    if role in _HYPERTUBE_ROLES:
        return HYPERTUBE
    if role in _PIPE_ROLES:
        return PIPE
    if role in _CONVEYOR_ROLES:
        return CONVEYOR
    return None


def edge_medium(role_a: str, role_b: str) -> str | None:
    """What an edge carries, from its two ends; ``None`` when neither says, or when they
    disagree, which means this vocabulary misread the save and picking one would hide it."""
    a, b = medium(role_a), medium(role_b)
    if a is None:
        return b
    if b is None or a == b:
        return a
    return None


def port_direction(role: str) -> str | None:
    """``"out"`` or ``"in"`` where the connector names its direction, else ``None``."""
    lowered = role.lower()
    if "output" in lowered:
        return "out"
    if "input" in lowered:
        return "in"
    return None


def is_hypertube_edge(role_a: str, role_b: str) -> bool:
    """Whether this edge moves a player instead of material.

    Positive identification only, so a role this table has never seen keeps its edge.
    """
    return role_a in _HYPERTUBE_ROLES or role_b in _HYPERTUBE_ROLES

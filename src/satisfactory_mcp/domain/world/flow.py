"""Which way the fluid goes, inferred from the plumbing rather than read off a pipe.

The save stores no flow direction, but it does store the couplings and it types the ports:
``PipeInputFactory`` is a consumer, ``PipeOutputFactory`` a producer, ``ConnectionAny0``/``1``
an explicit "either way", and ``PipelineConnection0``/``1`` are a segment's first and last
spline point, which is what ``forward`` and ``reverse`` are measured against. Two models then
orient pipes -- the cut, where an edge whose removal splits the network carries everything
from the side holding a producer and no consumer to a side holding a consumer, and the one-way
device, a pump or valve running ``Connection0`` to ``Connection1`` -- and conservation
propagates both to a fixpoint. Both decline wherever more than one ordering is consistent: an
edge inside a cycle splits nothing, and a trunk with producers and consumers on both sides has
no fixed direction without the RATES. On the reference save 365 of 503 pipes resolve.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import NamedTuple, TypeAlias

from typing_extensions import TypedDict

from ...core.saveio import rows as saverows
from ...core.saveio.schema import Projection
from .fluid_couplings import (
    FluidNode,
    coupling_actor_class,
    fluid_couplings,
    producer_consumer_classes,
)

__all__ = ["FORWARD", "REVERSE", "UNKNOWN", "PipeFlow", "pipe_flow"]

#: Along the segment's own point order, against it, and "we will not say".
FORWARD = "forward"
REVERSE = "reverse"
UNKNOWN = "unknown"

#: A junction and a buffer are ONE volume of fluid: what arrives at any port can leave by any
#: other, so their ports collapse into a single node. The T and the cross are both here and
#: both have to be: a junction left out is a CUT in the network, not a missing node.
_JUNCTIONS = (
    "Build_PipelineJunction_Cross_C",
    "Build_PipelineJunction_T_C",
)
#: The bodies that also HOLD fluid, which is what the conservation guard needs and a junction
#: is not: a tank can accept flow that nothing beyond it consumes, so it can end a route.
_STORES = (
    "Build_IndustrialTank_C",
    "Build_PipeStorageTank_C",
)
_BODIES = _JUNCTIONS + _STORES

#: One-way by construction, from ``Connection0`` (the inlet) to ``Connection1``.
_ONE_WAY = (
    "Build_PipelinePump_C",
    "Build_PipelinePumpMk2_C",
    "Build_Valve_C",
)

#: Basis labels, most local evidence first.
BASIS_PORT = "machine port"
BASIS_DEVICE = "pump"
BASIS_NETWORK = "propagated"
BASIS_NONE = "unresolved"


class PipeFlow(TypedDict):
    """One pipe's direction against its stored points, and the evidence it rests on."""

    direction: str
    basis: str


#: A pipe's two ends, ``None`` where the save couples nothing there.
PipeEnds: TypeAlias = tuple[FluidNode | None, FluidNode | None]
#: A one-way device as ``(inlet, outlet)``.
Device: TypeAlias = tuple[FluidNode, FluidNode]
#: ``("pipe", row)`` or ``("device", index)``: which edge joins two nodes.
EdgeTag: TypeAlias = tuple[str, int]
Adjacency: TypeAlias = dict[FluidNode, list[tuple[FluidNode, EdgeTag]]]
#: A port that faces one way: its node, ``source`` or ``sink``, and its actor index.
Terminal: TypeAlias = tuple[FluidNode, str, int]
#: Typed ports per node; a node absent is a node with none.
PortCount: TypeAlias = defaultdict[FluidNode, int]
#: An edge at a node: ``pipe`` or ``device``, its index, and whether the node is its first end.
Incidence: TypeAlias = tuple[str, int, bool]


class _PipeGraph(NamedTuple):
    """The plumbing as nodes fluid can stand at, and what joins them.

    ``stores`` come out separately because a tank can supply and accept, yet it PRODUCES and
    CONSUMES nothing and so can never orient a pipe.
    """

    pipes: list[PipeEnds]
    devices: list[Device]
    terminals: list[Terminal]
    adjacency: Adjacency
    stores: set[FluidNode]


def _port_kind(name: str, cls: str, producers: set[str], consumers: set[str]) -> str:
    """``source``, ``sink`` or ``any``: the port's own typing first, then the building's."""
    if name.startswith("PipeInputFactory"):
        return "sink"
    if name.startswith("PipeOutputFactory"):
        return "source"
    if name.startswith("ConnectionAny"):
        return "any"
    if cls in producers:
        return "source"
    if cls in consumers:
        return "sink"
    return "any"


def _build(projection: Projection) -> _PipeGraph:
    """The plumbing as pipe edges, one-way edges, typed terminals and their adjacency."""
    joins, ports_of, actors, roles = fluid_couplings(
        projection, lambda actor: coupling_actor_class(actor) in _BODIES
    )
    role_name = dict(enumerate(roles))
    role_ix = {name: i for i, name in enumerate(roles)}
    segments = list(saverows.iter_pipe_segments(projection))
    pipe_actors = {seg.actor_index for seg in segments if seg.actor_index >= 0}
    producers, consumers = producer_consumer_classes(projection)

    devices: list[Device] = []
    terminals: list[Terminal] = []
    stores: set[FluidNode] = set()
    for actor, ports in ports_of.items():
        cls = coupling_actor_class(actors[actor]) if 0 <= actor < len(actors) else ""
        if actor in pipe_actors:
            continue  # emitted below, in the segments' own order
        if cls in _BODIES:
            if cls in _STORES:
                stores.add(joins.find((actor, min(ports))))
            continue
        if cls in _ONE_WAY:
            c0, c1 = role_ix.get("Connection0"), role_ix.get("Connection1")
            if c0 in ports and c1 in ports:
                devices.append((joins.find((actor, c0)), joins.find((actor, c1))))
            continue
        for role in ports:
            kind = _port_kind(role_name.get(role, ""), cls, producers, consumers)
            if kind != "any":
                terminals.append((joins.find((actor, role)), kind, actor))

    # One entry per ROW of the table, not per row that decoded: ``/api/pipes`` joins to this
    # list by a segment's position, so a torn row owes it a slot that says "no idea".
    c0, c1 = role_ix.get("PipelineConnection0"), role_ix.get("PipelineConnection1")
    pipes: list[PipeEnds] = [(None, None)] * saverows.pipe_segment_count(projection)
    for seg in segments:
        actor = seg.actor_index
        ports = ports_of.get(actor, ()) if actor >= 0 else ()
        pipes[seg.position] = (
            joins.find((actor, c0)) if c0 in ports else None,
            joins.find((actor, c1)) if c1 in ports else None,
        )

    adjacency: Adjacency = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        adjacency[n0].append((n1, ("pipe", i)))
        adjacency[n1].append((n0, ("pipe", i)))
    for j, (n0, n1) in enumerate(devices):
        adjacency[n0].append((n1, ("device", j)))
        adjacency[n1].append((n0, ("device", j)))
    return _PipeGraph(pipes, devices, terminals, adjacency, stores)


def _reach(adjacency: Adjacency, start: FluidNode, without: EdgeTag) -> set[FluidNode]:
    """Everything fluid could get to from ``start`` without using edge ``without``."""
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for peer, tag in adjacency.get(node, ()):
            if tag == without or peer in seen:
                continue
            seen.add(peer)
            stack.append(peer)
    return seen


def _settle_by_cuts(
    pipes: list[PipeEnds], adjacency: Adjacency, source: PortCount, sink: PortCount
) -> list[int]:
    """Orient every pipe whose removal leaves producers alone on one side, consumers beyond."""
    total_source, total_sink = sum(source.values()), sum(sink.values())
    settled = [0] * len(pipes)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None or n0 == n1:
            continue
        near = _reach(adjacency, n0, ("pipe", i))
        if n1 in near:
            continue  # a cycle: both orderings are consistent, so neither is claimed
        near_source = sum(source[n] for n in near)
        near_sink = sum(sink[n] for n in near)
        if near_source and not near_sink and total_sink - near_sink:
            settled[i] = 1
        elif near_sink and not near_source and total_source - near_source:
            settled[i] = -1
    return settled


def _forced_edge(rows: list[Incidence], settled: list[int]) -> tuple[int, bool, int] | None:
    """The one unsettled pipe at a bare node, if what arrives there forces its direction."""
    arriving = leaving = 0
    open_edge: tuple[int, bool] | None = None
    for kind, index, at_first in rows:
        if kind == "device":
            # The device draws fluid out of its inlet node and into its outlet node.
            leaving += 1 if at_first else 0
            arriving += 0 if at_first else 1
        elif settled[index] == 0:
            if open_edge is not None:
                return None
            open_edge = (index, at_first)
        elif (settled[index] == 1) == at_first:
            leaving += 1
        else:
            arriving += 1
    if open_edge is None:
        return None
    index, at_first = open_edge
    if arriving and not leaving:
        return index, at_first, 1 if at_first else -1
    if leaving and not arriving:
        return index, at_first, -1 if at_first else 1
    return None


def _has_receiver(
    adjacency: Adjacency,
    far: FluidNode,
    index: int,
    wanted: PortCount,
    wanted_ports: set[FluidNode],
    stores: AbstractSet[FluidNode],
) -> bool:
    """Whether the side a pipe would send fluid to can take it: a port, a pump end or a tank.

    Without this, flow is invented into a bare stub of pipe that ends in nothing.
    """
    beyond = _reach(adjacency, far, ("pipe", index))
    return any(wanted[n] for n in beyond) or bool(beyond & wanted_ports) or bool(beyond & stores)


def _settle_by_conservation(
    settled: list[int],
    pipes: list[PipeEnds],
    devices: Sequence[Device],
    adjacency: Adjacency,
    stores: AbstractSet[FluidNode],
    source: PortCount,
    sink: PortCount,
) -> None:
    """At a node that is nothing but plumbing what arrives has to leave; run to a fixpoint."""
    incident: dict[FluidNode, list[Incidence]] = defaultdict(list)
    for i, (n0, n1) in enumerate(pipes):
        if n0 is None or n1 is None:
            continue
        incident[n0].append(("pipe", i, True))
        incident[n1].append(("pipe", i, False))
    for j, (n0, n1) in enumerate(devices):
        incident[n0].append(("device", j, True))
        incident[n1].append(("device", j, False))
    inlets = {n0 for n0, _n1 in devices}
    outlets = {n1 for _n0, n1 in devices}

    changed = True
    while changed:
        changed = False
        for node, rows in incident.items():
            if source[node] or sink[node]:
                continue  # it has a port of its own, so nothing here is forced
            forced = _forced_edge(rows, settled)
            if forced is None:
                continue
            index, at_first, direction = forced
            n0, n1 = pipes[index]
            far = n1 if at_first else n0
            outbound = (direction == 1) == at_first
            wanted, wanted_ports = (sink, inlets) if outbound else (source, outlets)
            if not _has_receiver(adjacency, far, index, wanted, wanted_ports, stores):
                continue
            settled[index] = direction
            changed = True


def _solve(
    pipes: list[PipeEnds],
    devices: list[Device],
    terminals: list[Terminal],
    adjacency: Adjacency,
    stores: AbstractSet[FluidNode] = frozenset(),
    *,
    cuts: bool = True,
    one_way: bool = True,
) -> list[int]:
    """Direction per pipe as +1 (points[0] to points[-1]), -1 (the reverse) or 0."""
    source: PortCount = defaultdict(int)
    sink: PortCount = defaultdict(int)
    for node, kind, _actor in terminals:
        (source if kind == "source" else sink)[node] += 1
    settled = _settle_by_cuts(pipes, adjacency, source, sink) if cuts else [0] * len(pipes)
    _settle_by_conservation(
        settled, pipes, devices if one_way else (), adjacency, stores, source, sink
    )
    return settled


def pipe_flow(projection: Projection) -> list[PipeFlow]:
    """One ``{"direction", "basis"}`` per pipe segment, in the segments' own order.

    ``direction`` is ``forward`` along the segment's stored points, ``reverse`` against them,
    or ``unknown``. ``basis`` names the evidence, from the most local outwards: a typed
    ``machine port`` at one end of this very pipe, a one-way ``pump`` at one end of it, or
    ``propagated`` when only the shape of the wider network settles it.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    settled = _solve(pipes, devices, terminals, adjacency, stores)

    port_nodes = {node for node, _kind, _actor in terminals}
    device_nodes = {node for edge in devices for node in edge}
    out: list[PipeFlow] = []
    for i, (n0, n1) in enumerate(pipes):
        if not settled[i]:
            out.append({"direction": UNKNOWN, "basis": BASIS_NONE})
            continue
        if n0 in port_nodes or n1 in port_nodes:
            basis = BASIS_PORT
        elif n0 in device_nodes or n1 in device_nodes:
            basis = BASIS_DEVICE
        else:
            basis = BASIS_NETWORK
        out.append({"direction": FORWARD if settled[i] == 1 else REVERSE, "basis": basis})
    return out

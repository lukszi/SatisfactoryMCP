"""Build a FactoryGraph from a save projection."""

from __future__ import annotations

from ...core.saveio import ports
from ...core.saveio.records import actor_class, iter_machine_records
from ...core.saveio.schema import Projection
from .model import Edge, FactoryGraph, kind_of

__all__ = ["build_graph"]


def build_graph(projection: Projection) -> FactoryGraph:
    payload = projection.get("graph") or {}
    actors: list[str] = payload.get("actors", [])
    roles: list[str] = payload.get("roles", [])

    cls = {a: actor_class(a) for a in actors}
    # The interned actor list is derived from EDGES, so a machine wired to nothing would be
    # absent from the graph and could never be reported; seed every machine record (§6.1).
    for _group, leaf, _record in iter_machine_records(projection):
        cls.setdefault(leaf, actor_class(leaf))
    graph = FactoryGraph(cls=cls)

    def role(index: int) -> str:
        return roles[index] if 0 <= index < len(roles) else ""

    for row in payload.get("material", ()):
        if len(row) < 2:
            continue
        a, b = actors[row[0]], actors[row[1]]
        ra = role(row[2]) if len(row) > 2 else ""
        rb = role(row[3]) if len(row) > 3 else ""
        edge = Edge(a=a, b=b, role_a=ra, role_b=rb)
        # A hypertube moves the PLAYER, so it is not material flow and must never merge two
        # factories that share nothing but a commute.
        if ports.is_hypertube_edge(ra, rb):
            graph.hyper.append(edge)
        # A transport station's connection is a factory BOUNDARY, not internal flow,
        # so it goes on its own layer rather than silently merging two factories.
        elif kind_of(cls.get(a, "")) == "transport" or kind_of(cls.get(b, "")) == "transport":
            graph.transport.append(edge)
        else:
            graph.material.append(edge)

    for row in payload.get("power", ()):
        if len(row) < 2:
            continue
        graph.power.append(Edge(a=actors[row[0]], b=actors[row[1]]))

    return graph

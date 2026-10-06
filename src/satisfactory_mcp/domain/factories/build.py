"""Build a FactoryGraph from a save projection."""

from __future__ import annotations

from ...core.saveio import ports
from ...core.saveio.records import actor_class
from .model import Edge, FactoryGraph, kind_of

__all__ = ["build_graph"]


def build_graph(projection: dict) -> FactoryGraph:
    payload = projection.get("graph") or {}
    actors: list[str] = payload.get("actors", [])
    roles: list[str] = payload.get("roles", [])

    cls = {a: actor_class(a) for a in actors}
    # The interned actor list is derived from EDGES, so a machine wired to nothing --
    # 6 of 570 on the reference save, mostly half-built assemblers -- would be absent
    # from the graph entirely and so could never be reported as unlabelled. An
    # isolated machine is exactly the thing a coverage report exists to surface.
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            name = record["instance"].rsplit(".", 1)[-1]
            cls.setdefault(name, actor_class(name))
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

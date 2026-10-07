"""The fluid couplings a save states, joined into the nodes fluid can stand at.

Both fluid models start here: ``flow`` orients pipes and ``headlift`` lifts supply over them.
A node is a coupling between two connectors, or a junction or tank body whose ports collapse
into one volume of fluid.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import NamedTuple, TypeAlias

from ...core.saveio.ports import PIPE, medium
from ...core.saveio.schema import Projection
from ...core.unionfind import UnionFind

__all__ = [
    "FluidCouplings",
    "FluidNode",
    "coupling_actor_class",
    "fluid_couplings",
    "producer_consumer_classes",
]

#: A place fluid can stand: the ``joins`` root of an ``(actor index, role index)`` port.
FluidNode: TypeAlias = tuple[int, int]


class FluidCouplings(NamedTuple):
    """``joins`` over ``(actor index, role index)``, and every fluid port each actor uses."""

    joins: UnionFind[FluidNode]
    ports_of: dict[int, set[int]]
    actors: list[str]
    roles: list[str]


def coupling_actor_class(actor: str) -> str:
    """``Build_OilRefinery_C_2147245036`` -> ``Build_OilRefinery_C``."""
    head, sep, _tail = actor.rpartition("_C_")
    return head + "_C" if sep else actor


def fluid_couplings(projection: Projection, is_body: Callable[[str], bool]) -> FluidCouplings:
    """Every fluid coupling joined, with the ports of each actor ``is_body`` names merged."""
    graph = projection.get("graph") or {}
    actors = list(graph.get("actors") or ())
    roles = list(graph.get("roles") or ())
    fluid_roles = {i for i, name in enumerate(roles) if medium(name) == PIPE}
    joins = UnionFind[FluidNode]()
    ports_of: dict[int, set[int]] = defaultdict(set)
    for edge in graph.get("material") or ():
        if not isinstance(edge, (list, tuple)) or len(edge) < 4:
            continue
        a, b, role_a, role_b = edge[0], edge[1], edge[2], edge[3]
        if role_a not in fluid_roles or role_b not in fluid_roles:
            continue
        joins.union((a, role_a), (b, role_b))
        ports_of[a].add(role_a)
        ports_of[b].add(role_b)
    for actor, ports in ports_of.items():
        if 0 <= actor < len(actors) and is_body(actors[actor]):
            first = min(ports)
            for role in ports:
                joins.union((actor, first), (actor, role))
    return FluidCouplings(joins, ports_of, actors, roles)


def producer_consumer_classes(projection: Projection) -> tuple[set[str], set[str]]:
    """Extractor and generator classes: what only ever produces, what only ever consumes.

    They settle a building whose one port carries the generic ``FGPipeConnectionFactory``.
    """
    producers = {r.get("cls") for r in projection.get("extractors") or () if isinstance(r, dict)}
    consumers = {r.get("cls") for r in projection.get("generators") or () if isinstance(r, dict)}
    return producers, consumers

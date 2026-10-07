"""What actually feeds what, contracted out of the save's own connection records.

A conduit run here is every belt or pipe piece the save joins end to end between two
things that are not conduit: the unit a player calls "that belt". The join is by ACTOR
IDENTITY -- ``graph["material"]`` names both ends of every coupling -- so a link is
something the save states rather than something geometry guesses, and a belt passing over
a machine cannot become a belt feeding it. Direction comes from the connector role at a
machine end, then from the machine's own nature, and is declined where neither says.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TypeAlias, cast

from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio import rows as saverows
from ...core.saveio.records import actor_class
from ...core.saveio.schema import Projection
from ...core.unionfind import UnionFind

#: Per run root, the actor indices of the nodes at its ends and the port roles they meet it by.
Boundary: TypeAlias = dict[int, dict[int, list[str]]]

__all__ = [
    "BASIS_UNKNOWN",
    "BY_NATURE",
    "BY_ROLE",
    "Link",
    "PhysicalGraph",
    "build_physical_graph",
    "side_by_nature",
]

#: How a link's direction was settled, most local evidence first. ``BY_ROLE`` is a port that
#: names itself an input or an output; ``BY_NATURE`` is a run between two devices whose ports
#: do not, resolved because an extractor only ever produces and a generator only ever
#: consumes; ``BASIS_UNKNOWN`` is a run between two pipe fittings, which genuinely has no
#: direction without the rates.
BY_ROLE = "role"
BY_NATURE = "nature"
BASIS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Link:
    """One conduit run, contracted to the two things it joins.

    ``source``/``target`` are actor short names, in flow order only when ``basis`` is not
    ``BASIS_UNKNOWN``. Either is ``None`` for a run whose other end reaches nothing -- a torn
    line, a build in progress. ``pieces`` is how many conduit actors the run contracted.
    """

    source: str | None
    target: str | None
    medium: str  # ports.CONVEYOR | ports.PIPE
    basis: str
    pieces: int
    #: The port role at each end, as the save spells it. ``""`` at an end that is nothing.
    source_role: str = ""
    target_role: str = ""
    #: A ``chain:<n>`` or ``pipe:<row>`` naming a piece this very run contracted, which
    #: ``search_conduits`` prints and ``resolve_place`` takes; save-projection.md §6.15.
    #: Empty only where no piece of the run is in ``graph["actors"]`` at all.
    ident: str = ""

    def other(self, actor: str) -> str | None:
        """The far end from ``actor``. The only safe way to walk a ``BASIS_UNKNOWN`` link.

        An undirected link is indexed from both of its ends, so ``feeds(x)`` can hand back
        one whose ``source`` IS ``x`` -- reading ``source`` there walks in a circle.
        """
        return self.target if actor == self.source else self.source


@dataclass
class PhysicalGraph:
    """Every link, indexed both ways, plus what could not be joined.

    ``dangling`` holds the links with one end on nothing. ``undirected`` counts the links
    whose direction was declined: they appear in both indexes, because a run that might feed
    a machine has to be visible from it.
    """

    links: list[Link] = field(default_factory=list[Link])
    inbound: dict[str, list[Link]] = field(default_factory=lambda: defaultdict(list))
    outbound: dict[str, list[Link]] = field(default_factory=lambda: defaultdict(list))
    dangling: list[Link] = field(default_factory=list[Link])
    undirected: int = 0
    #: Runs joined to no node at all: conduit floating in the world, both ends open.
    orphan_runs: int = 0
    #: Every conduit actor, to the link its run contracted to. What turns a walk over the
    #: raw graph back into the runs it crossed. An orphan run's pieces are absent.
    run_of: dict[str, Link] = field(default_factory=dict[str, Link])

    def feeds(self, actor: str) -> list[Link]:
        """Every link that delivers to ``actor``, undirected runs included."""
        return list(self.inbound.get(actor, ()))

    def drains(self, actor: str) -> list[Link]:
        return list(self.outbound.get(actor, ()))


def side_by_nature(cls: str, game: GameData) -> str | None:
    """Which way material can move at a device whose ports do not say."""
    building = game.buildings.get(cls)
    if building is None:
        return None
    if building.is_extractor:
        return "out"
    if building.is_generator:
        return "in"
    return None


def _named_side(roles: list[str]) -> str | None:
    """The first direction any of a node's port roles names on this run."""
    return next((ports.port_direction(r) for r in roles if ports.port_direction(r)), None)


def _run_root(joins: UnionFind, piece: int) -> int:
    """The root piece of ``piece``'s run: ``joins`` holds actor indices only."""
    return cast(int, joins.find(piece))


def _contract_conduits(
    projection: Projection, actors: list[str], roles: list[str]
) -> tuple[dict[int, list[int]], Boundary]:
    """Conduit pieces joined into runs: ``(pieces by run root, nodes and roles at each run)``.

    A conduit is a piece with geometry in one of the polyline tables and no behaviour of its
    own; everything else the graph names is a node, including the fittings.
    """

    def role_at(index: object) -> str:
        return roles[index] if isinstance(index, int) and 0 <= index < len(roles) else ""

    conduit_classes = set((projection.get("belts") or {}).get("classes") or ()) | set(
        (projection.get("pipes") or {}).get("classes") or ()
    )
    is_conduit = [actor_class(a) in conduit_classes for a in actors]

    joins = UnionFind()
    edges: list[tuple[int, int, str, str]] = []
    for edge in (projection.get("graph") or {}).get("material") or ():
        if not isinstance(edge, (list, tuple)) or len(edge) < 4:
            continue
        a, b = edge[0], edge[1]
        if not (isinstance(a, int) and isinstance(b, int)):
            continue
        if not (0 <= a < len(actors) and 0 <= b < len(actors)):
            continue
        role_a, role_b = role_at(edge[2]), role_at(edge[3])
        if ports.is_hypertube_edge(role_a, role_b):
            continue
        edges.append((a, b, role_a, role_b))
        if is_conduit[a] and is_conduit[b]:
            joins.union(a, b)

    runs: dict[int, list[int]] = defaultdict(list)
    for i, conduit in enumerate(is_conduit):
        if conduit:
            runs[_run_root(joins, i)].append(i)
    boundary: Boundary = defaultdict(lambda: defaultdict(list))
    for a, b, role_a, role_b in edges:
        if is_conduit[a] and not is_conduit[b]:
            boundary[_run_root(joins, a)][b].append(role_b)
        elif is_conduit[b] and not is_conduit[a]:
            boundary[_run_root(joins, b)][a].append(role_a)
    return runs, boundary


def _run_numbers(projection: Projection) -> dict[int, int]:
    """Every conduit piece's actor index to its run number: a pipe's row, a belt's chain.

    Keyed by the actor index both tables carry, the save's own identity for the piece rather
    than a nearest match; save-projection.md §6.15.
    """
    numbers: dict[int, int] = {
        seg.actor_index: seg.index
        for seg in saverows.iter_pipe_segments(projection)
        if seg.actor_index >= 0
    }
    numbers.update(
        (seg.actor_index, seg.chain)
        for seg in saverows.iter_belt_segments(projection)
        if seg.actor_index >= 0
    )
    return numbers


def _run_ident(pieces: list[int], numbers: dict[int, int], medium: str) -> str:
    """``chain:<n>`` or ``pipe:<row>`` by the LOWEST number on the run, which does not move
    when a piece is added at the far end."""
    found = [numbers[i] for i in pieces if i in numbers]
    prefix = "pipe" if medium == ports.PIPE else "chain"
    return f"{prefix}:{min(found)}" if found else ""


def _dangling_link(
    attached: dict[int, list[str]],
    actors: list[str],
    game: GameData,
    medium: str,
    pieces: int,
    ident: str,
) -> Link:
    """A run with one known end: its SOURCE where the run leaves it, its TARGET where the run
    arrives, so "leaves and reaches nothing" and "arrives from nothing" stay apart."""
    ((node, node_roles),) = attached.items()
    role_side = _named_side(node_roles)
    side = role_side or side_by_nature(actor_class(actors[node]), game)
    arriving = side == "in"
    return Link(
        source=None if arriving else actors[node],
        target=actors[node] if arriving else None,
        medium=medium,
        basis=BY_ROLE if role_side else (BY_NATURE if side else BASIS_UNKNOWN),
        pieces=pieces,
        source_role="" if arriving else node_roles[0],
        target_role=node_roles[0] if arriving else "",
        ident=ident,
    )


def _joined_link(
    attached: dict[int, list[str]],
    actors: list[str],
    game: GameData,
    medium: str,
    pieces: int,
    ident: str,
) -> Link:
    """A run between two nodes, oriented by their port roles, else by their natures."""
    (node_a, roles_a), (node_b, roles_b) = attached.items()
    side_a, side_b = _named_side(roles_a), _named_side(roles_b)
    basis = BY_ROLE
    if side_a is None and side_b is None:
        side_a = side_by_nature(actor_class(actors[node_a]), game)
        side_b = side_by_nature(actor_class(actors[node_b]), game)
        basis = BY_NATURE if (side_a or side_b) else BASIS_UNKNOWN
    if side_a == "out" or side_b == "in":
        source, target, source_roles, target_roles = node_a, node_b, roles_a, roles_b
    elif side_a == "in" or side_b == "out":
        source, target, source_roles, target_roles = node_b, node_a, roles_b, roles_a
    else:
        source, target, source_roles, target_roles = node_a, node_b, roles_a, roles_b
        basis = BASIS_UNKNOWN
    return Link(
        source=actors[source],
        target=actors[target],
        medium=medium,
        basis=basis,
        pieces=pieces,
        source_role=source_roles[0],
        target_role=target_roles[0],
        ident=ident,
    )


def _index_link(graph: PhysicalGraph, link: Link) -> None:
    graph.links.append(link)
    if link.source is None or link.target is None:
        graph.dangling.append(link)
    if link.source is not None:
        graph.outbound[link.source].append(link)
    if link.target is not None:
        graph.inbound[link.target].append(link)
    if link.basis == BASIS_UNKNOWN and link.source is not None and link.target is not None:
        graph.undirected += 1
        # No direction means either end may be the feeder, so the link answers from both.
        # Reporting one arbitrary orientation is the confident wrong edge.
        graph.outbound[link.target].append(link)
        graph.inbound[link.source].append(link)


def build_physical_graph(projection: Projection, game: GameData) -> PhysicalGraph:
    """Contract every belt and pipe run in a projection into node-to-node links.

    Splitters, mergers, junctions, pumps and valves stay as NODES rather than being walked
    through: a splitter dividing three ways is the answer to half the starvation questions
    on a real save, and contracting it away would hide the division.
    """
    graph = projection.get("graph") or {}
    actors: list[str] = list(graph.get("actors") or ())
    roles: list[str] = list(graph.get("roles") or ())
    out = PhysicalGraph()
    if not actors:
        return out
    runs, boundary = _contract_conduits(projection, actors, roles)
    numbers = _run_numbers(projection)
    pipe_classes = set((projection.get("pipes") or {}).get("classes") or ())
    for root, pieces in runs.items():
        medium = ports.PIPE if actor_class(actors[root]) in pipe_classes else ports.CONVEYOR
        ident = _run_ident(pieces, numbers, medium)
        attached = boundary.get(root) or {}
        # Three or more nodes on one run would be a conduit piece with three ports, which
        # the game has none of; taking the first two would hide the malformed record.
        if not attached or len(attached) > 2:
            out.orphan_runs += 1
            continue
        make = _dangling_link if len(attached) == 1 else _joined_link
        link = make(attached, actors, game, medium, len(pieces), ident)
        _index_link(out, link)
        for i in pieces:
            out.run_of[actors[i]] = link
    return out

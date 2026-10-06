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

from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio import rows as saverows

__all__ = ["Link", "PhysicalGraph", "build_physical_graph"]

#: How a link's direction was settled, most local evidence first. ``ROLE`` is a port that
#: names itself an input or an output; ``NATURE`` is a run between two devices whose ports
#: do not, resolved because an extractor only ever produces and a generator only ever
#: consumes; ``UNKNOWN`` is a run between two pipe fittings, which genuinely has no
#: direction without the rates.
BY_ROLE = "role"
BY_NATURE = "nature"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Link:
    """One conduit run, contracted to the two things it joins.

    ``source``/``target`` are actor short names, in flow order only when ``basis`` is not
    ``UNKNOWN``. Either is ``None`` for a run whose other end reaches nothing -- a torn
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
    #: A ``chain:<n>`` or ``pipe:<row>`` on the run, which ``search_conduits`` prints and
    #: ``resolve_place`` takes. Both join by ACTOR INDEX, so the id names a piece this very
    #: run contracted rather than the nearest one -- §6.15, ``docs/save-projection.md``.
    #: Empty only where no piece of the run is in ``graph["actors"]`` at all.
    ident: str = ""

    def other(self, actor: str) -> str | None:
        """The far end from ``actor``. The only safe way to walk an ``UNKNOWN`` link.

        An undirected link is indexed from both of its ends, so ``feeds(x)`` can hand back
        one whose ``source`` IS ``x`` -- reading ``source`` there walks in a circle.
        """
        return self.target if actor == self.source else self.source


@dataclass
class PhysicalGraph:
    """Every link, indexed both ways, plus what could not be joined.

    ``dangling`` holds the links whose ``target`` is ``None``. ``undirected`` counts the
    links whose direction was declined: they appear in both indexes, because a run that
    might feed a machine has to be visible from it.
    """

    links: list[Link] = field(default_factory=list)
    inbound: dict[str, list[Link]] = field(default_factory=lambda: defaultdict(list))
    outbound: dict[str, list[Link]] = field(default_factory=lambda: defaultdict(list))
    dangling: list[Link] = field(default_factory=list)
    undirected: int = 0
    #: Runs joined to no node at all: conduit floating in the world, both ends open.
    orphan_runs: int = 0
    #: Every conduit actor, to the link its run contracted to. What turns a walk over the
    #: raw graph back into the runs it crossed. An orphan run's pieces are absent.
    run_of: dict[str, Link] = field(default_factory=dict)

    def feeds(self, actor: str) -> list[Link]:
        """Every link that delivers to ``actor``, undirected runs included."""
        return list(self.inbound.get(actor, ()))

    def drains(self, actor: str) -> list[Link]:
        return list(self.outbound.get(actor, ()))


def _orient(role: str) -> str | None:
    lowered = role.lower()
    if "output" in lowered:
        return "out"
    if "input" in lowered:
        return "in"
    return None


def _nature(cls: str, game: GameData) -> str | None:
    """Which way material can move at a device whose ports do not say."""
    building = game.buildings.get(cls)
    if building is None:
        return None
    if building.is_extractor:
        return "out"
    if building.is_generator:
        return "in"
    return None


def _class_of(actor: str) -> str:
    head, _, tail = actor.rpartition("_")
    return head if tail.isdigit() else actor


class _Union:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def build_physical_graph(projection: dict, game: GameData) -> PhysicalGraph:
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

    def role_at(index: object) -> str:
        return roles[index] if isinstance(index, int) and 0 <= index < len(roles) else ""

    # A conduit is a piece with geometry in one of the polyline tables and no behaviour of
    # its own; everything else the graph names is a node, including the fittings.
    conduit_classes = set((projection.get("belts") or {}).get("classes") or ()) | set(
        (projection.get("pipes") or {}).get("classes") or ()
    )
    is_conduit = [_class_of(a) in conduit_classes for a in actors]

    joins = _Union(len(actors))
    edges: list[tuple[int, int, str, str]] = []
    for edge in graph.get("material") or ():
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

    boundary: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    members: dict[int, list[int]] = defaultdict(list)
    for i, conduit in enumerate(is_conduit):
        if conduit:
            members[joins.find(i)].append(i)
    for a, b, role_a, role_b in edges:
        if is_conduit[a] and not is_conduit[b]:
            boundary[joins.find(a)][b].append(role_b)
        elif is_conduit[b] and not is_conduit[a]:
            boundary[joins.find(b)][a].append(role_a)

    pipe_classes = set((projection.get("pipes") or {}).get("classes") or ())
    # Every conduit piece to the number its run gets named by -- a pipe's own row, a belt's
    # CHAIN -- keyed by the actor index both tables carry, which is the save's own identity
    # for the piece rather than a nearest match. §6.15, docs/save-projection.md.
    numbered: dict[int, int] = {
        seg.actor_index: seg.index
        for seg in saverows.iter_pipe_segments(projection)
        if seg.actor_index >= 0
    }
    numbered.update(
        (seg.actor_index, seg.chain)
        for seg in saverows.iter_belt_segments(projection)
        if seg.actor_index >= 0
    )

    def register(link: Link, on: list[int]) -> None:
        for i in on:
            out.run_of[actors[i]] = link

    for root, on in members.items():
        pieces = len(on)
        medium = ports.PIPE if _class_of(actors[root]) in pipe_classes else ports.CONVEYOR
        # The LOWEST number on the run: any piece of it lands the reader on the run, and the
        # lowest is the one that does not move when a piece is added at the far end.
        found = [numbered[i] for i in on if i in numbered]
        prefix = "pipe" if medium == ports.PIPE else "chain"
        ident = f"{prefix}:{min(found)}" if found else ""
        attached = boundary.get(root) or {}
        if not attached:
            out.orphan_runs += 1
            continue
        if len(attached) == 1:
            ((node, node_roles),) = attached.items()
            side = next((_orient(r) for r in node_roles if _orient(r)), None) or _nature(
                _class_of(actors[node]), game
            )
            # The one known end is the SOURCE where the run leaves it and the TARGET where
            # it arrives, so "a belt leaves this machine and reaches nothing" and "a belt
            # arrives from nothing" stay distinguishable.
            arriving = side == "in"
            link = Link(
                source=None if arriving else actors[node],
                target=actors[node] if arriving else None,
                medium=medium,
                basis=BY_ROLE if side else UNKNOWN,
                pieces=pieces,
                source_role="" if arriving else node_roles[0],
                target_role=node_roles[0] if arriving else "",
                ident=ident,
            )
            out.links.append(link)
            out.dangling.append(link)
            (out.inbound if arriving else out.outbound)[actors[node]].append(link)
            register(link, on)
            continue
        # A run with three or more nodes on it would mean a conduit piece with three ports,
        # which the game has none of; taking the first two would hide the malformed record.
        if len(attached) > 2:
            out.orphan_runs += 1
            continue
        (na, roles_a), (nb, roles_b) = attached.items()
        side_a = next((_orient(r) for r in roles_a if _orient(r)), None)
        side_b = next((_orient(r) for r in roles_b if _orient(r)), None)
        basis = BY_ROLE
        if side_a is None and side_b is None:
            side_a, side_b = (
                _nature(_class_of(actors[na]), game),
                _nature(_class_of(actors[nb]), game),
            )
            basis = BY_NATURE if (side_a or side_b) else UNKNOWN
        if side_a == "out" or side_b == "in":
            src, dst, src_role, dst_role = na, nb, roles_a[0], roles_b[0]
        elif side_a == "in" or side_b == "out":
            src, dst, src_role, dst_role = nb, na, roles_b[0], roles_a[0]
        else:
            src, dst, src_role, dst_role = na, nb, roles_a[0], roles_b[0]
            basis = UNKNOWN
        link = Link(
            source=actors[src],
            target=actors[dst],
            medium=medium,
            basis=basis,
            pieces=pieces,
            source_role=src_role,
            target_role=dst_role,
            ident=ident,
        )
        out.links.append(link)
        out.outbound[link.source].append(link)
        out.inbound[actors[dst]].append(link)
        register(link, on)
        if basis == UNKNOWN:
            out.undirected += 1
            # No direction means either end may be the feeder, so the link answers from
            # both. Reporting one arbitrary orientation is the confident wrong edge.
            out.outbound[actors[dst]].append(link)
            out.inbound[link.source].append(link)
    return out

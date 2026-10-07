"""Walking the material graph in the direction the stuff actually moves: what feeds THIS
machine, which on a save mid-cutover decides what is safe to repipe.

Direction is read off the connector role at each end, then off the machine's own nature
where the role is the bare ``FGPipeConnectionFactory``. The walk passes THROUGH logistics and
reports only machines, keeping the conduit runs it crossed. docs/planning.md §8.5n has the
measurements.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio.records import instance_leaf
from ...core.saveio.schema import BuildableRecord, ExtractorRecord
from ..world.logistics import Link, side_by_nature
from .select import SelectorError, resolve_factory

if TYPE_CHECKING:
    from ..world.state import WorldState

__all__ = [
    "Feeds",
    "Reached",
    "Trace",
    "feeder_records",
    "live_feeders",
    "material_feeds",
    "resolve_seeds",
    "trace",
]

#: Hard stop on the walk, far above the reference save's deepest chain of 72 hops, so a
#: malformed graph cannot spin.
MAX_HOPS = 500

#: ``(upstream, downstream, ambiguous)``: ``upstream[x]`` is everything that feeds ``x``.
Feeds = tuple[dict[str, set[str]], dict[str, set[str]], int]


@dataclass
class Reached:
    instance: str
    cls: str
    name: str
    kind: str  # extractor | generator | production
    hops: int


@dataclass
class Trace:
    direction: str
    seeds: list[str] = field(default_factory=list[str])
    reached: list[Reached] = field(default_factory=list[Reached])
    #: Every node visited including belts and pipes, which the report leaves out.
    visited: int = 0
    deepest: int = 0
    #: Edges whose direction could not be established even from the machine's own role.
    #: Traversed BOTH ways, which can only over-report -- never miss a real feeder.
    ambiguous: int = 0
    truncated: bool = False
    #: The conduit runs the walk crossed, contracted out of the belt and pipe nodes it
    #: passed through. Empty when no physical graph was supplied.
    crossed: list[Link] = field(default_factory=list[Link])
    #: Every node visited, logistics included, seeds included.
    nodes: set[str] = field(default_factory=set[str])

    def by_class(self) -> dict[str, list[Reached]]:
        out: dict[str, list[Reached]] = {}
        for row in self.reached:
            out.setdefault(row.name, []).append(row)
        return out


def _kind(game: GameData, cls: str) -> str | None:
    b = game.buildings.get(cls)
    if b is None:
        return None
    if b.is_extractor:
        return "extractor"
    if b.is_generator:
        return "generator"
    if b.is_manufacturer:
        return "production"
    return None


def _cls(records: Mapping[str, BuildableRecord], leaf: str) -> str:
    record = records.get(leaf)
    return record.get("cls", "") if record is not None else ""


def _side(role: str, cls: str, game: GameData) -> str | None:
    """Which way material moves at a connector: its role first, then the machine's nature,
    which settles every bare ``FGPipeConnectionFactory`` on an extractor or a generator."""
    return ports.port_direction(role) or side_by_nature(cls, game)


def material_feeds(state: WorldState, game: GameData) -> Feeds:
    """Directed feeds-into maps, plus how many edges stayed ambiguous. Built afresh on every
    call; ``WorldState.feeds`` keeps the answer."""
    graph = state.projection.get("graph") or {}
    roles, actors = graph.get("roles") or [], graph.get("actors") or []
    records = state.records_by_leaf

    up: dict[str, set[str]] = {}
    down: dict[str, set[str]] = {}
    ambiguous = 0
    for edge in graph.get("material") or ():
        role_a, role_b = roles[edge[2]], roles[edge[3]]
        if ports.is_hypertube_edge(role_a, role_b):
            continue
        a, b = actors[edge[0]], actors[edge[1]]
        side_a = _side(role_a, _cls(records, a), game)
        side_b = _side(role_b, _cls(records, b), game)
        if side_a == "out" or side_b == "in":
            pairs = [(a, b)]
        elif side_a == "in" or side_b == "out":
            pairs = [(b, a)]
        else:
            # Belt-to-belt and pipe-to-pipe segments, which have no direction of their
            # own. Walked BOTH ways: over-reporting a feeder is recoverable, missing one
            # is what costs 5 GW.
            ambiguous += 1
            pairs = [(a, b), (b, a)]
        for source, target in pairs:
            down.setdefault(source, set()).add(target)
            up.setdefault(target, set()).add(source)
    return up, down, ambiguous


def resolve_seeds(state: WorldState, game: GameData, seed: str) -> tuple[list[str], str]:
    """A seed as machine leaves plus a subject line: an instance (bare or ``machine:``), a
    building, else a factory.

    ``label:<name>`` names only a factory, so a label that shares a building's name still
    traces the label.

    Raises ``SelectorError`` when the text is none of the three.
    """
    records = state.records_by_leaf
    what = seed.strip()
    if what.casefold().startswith("label:"):
        wanted = what[len("label:") :].strip()
        if state.labels.find(wanted) is None:
            raise SelectorError(f"no label named {wanted!r}")
        name, machines = resolve_factory(state, wanted)
        seeds = [str(m) for m in machines]
        return seeds, f"factory {name!r} ({len(seeds)} machines)"
    if what.casefold().startswith("machine:") and "," not in what:
        what = instance_leaf(what[len("machine:") :].strip())
        if what not in records:
            raise SelectorError(f"no machine called {what!r} in this save")
    if what in records:
        return [what], f"{records[what].get('cls', '?')} {what}"
    by_class = [
        inst
        for inst, rec in records.items()
        if rec.get("cls") == what
        or (
            rec.get("cls") in game.buildings
            and game.buildings[rec["cls"]].name.casefold() == what.casefold()
        )
    ]
    if by_class:
        return by_class, f"{len(by_class)}x {what}"
    name, machines = resolve_factory(state, what)
    seeds = [str(m) for m in machines]
    return seeds, f"factory {name!r} ({len(seeds)} machines)"


def trace(state: WorldState, game: GameData, seeds: list[str], direction: str = "up") -> Trace:
    """Every machine up- or downstream of ``seeds``, logistics walked through."""
    up, down, ambiguous = state.feeds(game)
    adjacency = up if direction == "up" else down
    out = Trace(direction=direction, seeds=list(seeds), ambiguous=ambiguous)

    records = state.records_by_leaf
    start = [s for s in seeds if s in records or s in adjacency]
    seen: dict[str, int] = {s: 0 for s in start}
    queue: deque[str] = deque(start)
    while queue:
        node = queue.popleft()
        if seen[node] >= MAX_HOPS:
            out.truncated = True
            continue
        for nxt in adjacency.get(node, ()):
            if nxt in seen:
                continue
            seen[nxt] = seen[node] + 1
            queue.append(nxt)

    out.visited = len(seen)
    out.nodes = set(seen)
    out.deepest = max(seen.values(), default=0)
    # The same nodes, contracted rather than re-walked: the traversal above is untouched and
    # this only keeps what it already crossed. Deduplicated by identity, because one run is
    # dozens of nodes.
    run_of = state.physical.run_of
    kept: dict[int, Link] = {}
    for node in seen:
        link = run_of.get(node)
        if link is not None:
            kept.setdefault(id(link), link)
    out.crossed = list(kept.values())
    for node, hops in seen.items():
        if node in seeds:
            continue
        cls = _cls(records, node)
        kind = _kind(game, cls)
        if kind is None:
            continue  # logistics: traversed, not reported
        out.reached.append(
            Reached(
                instance=node,
                cls=cls,
                name=game.buildings[cls].name if cls in game.buildings else cls,
                kind=kind,
                hops=hops,
            )
        )
    out.reached.sort(key=lambda r: (r.hops, r.name))
    return out


def power_at_risk(state: WorldState, game: GameData, machines: list[str]) -> tuple[float, int, int]:
    """MW of generation that would stop if ``machines`` stopped feeding it.

    Returns ``(mw, generators, running)``. Only generators PROVEN to be running are
    charged: a machine that produced inside the last complete 300 s window certainly had
    power and fuel, and one that did not may be idle for a dozen reasons. Counting the
    idle ones would inflate the risk of touching a line that is already dead.
    """
    downstream = trace(state, game, machines, direction="down")
    mw = 0.0
    total = running = 0
    by_instance = state.records_by_leaf
    for row in downstream.reached:
        if row.kind != "generator":
            continue
        total += 1
        record = by_instance.get(row.instance) or {}
        uptime = record.get("uptime") or {}
        if (uptime.get("produce_s") or 0.0) <= 0:
            continue
        running += 1
        building = game.buildings.get(row.cls)
        if building:
            mw += building.power_production_mw * (record.get("clock") or 1.0)
    return mw, total, running


def live_feeders(g: GameData, st: WorldState, floor_mw: float = 1.0) -> list[tuple[str, float]]:
    """Built extractors whose output currently reaches a running generator.

    Which of the machines already on the ground are load-bearing right now, which a startup
    order cannot answer on its own. The distribution is lopsided in practice -- one of
    sixteen Oil Extractors carrying all the running fuel generation -- so "repipe the
    extractors" is usually many safe moves and one that browns out the base.
    """
    return [
        (f"{name} {instance_leaf(record['instance'])[-10:]}", mw)
        for record, name, mw in feeder_records(g, st, floor_mw)
    ]


def feeder_records(
    g: GameData, st: WorldState, floor_mw: float = 1.0
) -> list[tuple[ExtractorRecord, str, float]]:
    """``live_feeders`` as (extractor record, building name, MW), largest first."""
    out: list[tuple[ExtractorRecord, str, float]] = []
    for record in st.projection.get("extractors", ()):
        instance = instance_leaf(record["instance"])
        mw, _, running = power_at_risk(st, g, [instance])
        if running and mw >= floor_mw:
            building = g.buildings.get(record.get("cls", ""))
            out.append((record, building.name if building else str(record.get("cls")), mw))
    out.sort(key=lambda row: -row[2])
    return out

"""Walking the material graph in the direction the stuff actually moves.

`factory_query` answers "what does this factory touch" between labelled sets. The question
underneath it is narrower and was unanswerable: *what feeds THIS machine* -- which, on a
live save mid-cutover, decides what you are allowed to repipe. Thirteen Oil Extractors sit
on the Spire nodes and twenty Fuel Generators are burning; repiping the wrong extractor
first drops several GW. The answer turns out to be **one** of the thirteen.

Direction is read, not inferred
-------------------------------
Every material edge already carries the connector role at each end -- `Output1`,
`Input0`, `PipeInputFactory` -- and the role is what orients it. Measured on the reference
save, of 2,300 connectors landing on a production machine:

* 2,002 are `Input`/`Output` (belts) and 126 are `PipeInputFactory`/`PipeOutputFactory`.
  **92.5% state their direction outright.**
* 172 are the bare `FGPipeConnectionFactory`, which does not -- and every single one of
  them is on an extractor or a generator. An extractor only ever produces and a generator
  only ever consumes, so its own role settles the edge. The resolution is exact, not a
  guess.

A trap worth recording: asking whether BOTH ends name a direction says 0% of 11,664 edges
are orientable, which is true and useless. The far end is nearly always a belt, and a belt
genuinely has no direction as an object -- only the machine end does.

Logistics is traversed, then named
----------------------------------
A trace from the generators touches 331 nodes at depth 72, almost all of it conveyor and
pipe segments. A path through that is unreadable, so the walk passes THROUGH logistics and
reports only machines in its table, which is the same thing `graph.query` does to find a
factory's boundary. What it also keeps is which RUNS those nodes belonged to
(`..world.logistics`), so the route can be named without the table growing 300 rows.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ...core.saveio import ports

__all__ = ["Reached", "Trace", "orient", "resolve_seeds", "trace"]

#: Hard stop on the walk. The reference save's deepest chain is 72 hops of mostly belt,
#: so this is far above anything real -- it exists so a malformed graph cannot spin.
MAX_HOPS = 500


def orient(role: str) -> str | None:
    """Which way material moves at this connector, or None when the name does not say."""
    lowered = role.lower()
    if "output" in lowered:
        return "out"
    if "input" in lowered:
        return "in"
    return None


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
    seeds: list[str] = field(default_factory=list)
    reached: list[Reached] = field(default_factory=list)
    #: Every node visited including belts and pipes, which the report leaves out.
    visited: int = 0
    deepest: int = 0
    #: Edges whose direction could not be established even from the machine's own role.
    #: Traversed BOTH ways, which can only over-report -- never miss a real feeder.
    ambiguous: int = 0
    truncated: bool = False
    #: The conduit runs the walk crossed, contracted out of the belt and pipe nodes it
    #: passed through. Empty when no physical graph was supplied.
    crossed: list = field(default_factory=list)
    #: Every node visited, logistics included, seeds included.
    nodes: set[str] = field(default_factory=set)

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


def _adjacency(state, game: GameData) -> tuple[dict[str, set[str]], dict[str, set[str]], int]:
    """Directed feeds-into maps, plus how many edges stayed ambiguous.

    Returns ``(upstream, downstream, ambiguous)`` where ``upstream[x]`` is everything that
    feeds ``x``.
    """
    graph = state.projection.get("graph") or {}
    roles, actors = graph.get("roles") or [], graph.get("actors") or []
    cls_of = {r["instance"].rsplit(".", 1)[-1]: r.get("cls", "") for r in state._all_records()}

    up: dict[str, set[str]] = {}
    down: dict[str, set[str]] = {}
    ambiguous = 0
    for edge in graph.get("material") or ():
        role_a, role_b = roles[edge[2]], roles[edge[3]]
        if ports.is_hypertube_edge(role_a, role_b):
            continue
        a, b = actors[edge[0]], actors[edge[1]]
        side_a, side_b = orient(role_a), orient(role_b)
        # The connector name first; then the machine's own nature, which settles every
        # bare FGPipeConnectionFactory on this save because they all sit on an extractor
        # (only produces) or a generator (only consumes).
        if side_a is None:
            kind = _kind(game, cls_of.get(a, ""))
            side_a = "out" if kind == "extractor" else "in" if kind == "generator" else None
        if side_b is None:
            kind = _kind(game, cls_of.get(b, ""))
            side_b = "out" if kind == "extractor" else "in" if kind == "generator" else None

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


def resolve_seeds(state, game: GameData, seed: str) -> tuple[list[str], str]:
    """A seed as machine leaves plus a subject line: an instance, a building, else a factory.

    ``label:<name>`` names only a factory, so a label that shares a building's name still
    traces the label.

    Raises ``SelectorError`` when the text is none of the three.
    """
    from .resolve import resolve_factory
    from .select import SelectorError

    records = {r["instance"].rsplit(".", 1)[-1]: r for r in state._all_records()}
    what = seed.strip()
    if what.casefold().startswith("label:"):
        wanted = what[len("label:") :].strip()
        if state.labels.find(wanted) is None:
            raise SelectorError(f"no label named {wanted!r}")
        name, machines = resolve_factory(state, wanted)
        seeds = [str(m) for m in machines]
        return seeds, f"factory {name!r} ({len(seeds)} machines)"
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


def trace(state, game: GameData, seeds: list[str], direction: str = "up") -> Trace:
    """Every machine up- or downstream of ``seeds``, logistics walked through."""
    up, down, ambiguous = _adjacency(state, game)
    adjacency = up if direction == "up" else down
    out = Trace(direction=direction, seeds=list(seeds), ambiguous=ambiguous)

    cls_of = {r["instance"].rsplit(".", 1)[-1]: r.get("cls", "") for r in state._all_records()}
    start = [s for s in seeds if s in cls_of or s in adjacency]
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
    run_of = getattr(getattr(state, "physical", None), "run_of", None) or {}
    kept: dict[int, object] = {}
    for node in seen:
        link = run_of.get(node)
        if link is not None:
            kept.setdefault(id(link), link)
    out.crossed = list(kept.values())
    for node, hops in seen.items():
        if node in seeds:
            continue
        cls = cls_of.get(node, "")
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


def power_at_risk(state, game: GameData, machines: list[str]) -> tuple[float, int, int]:
    """MW of generation that would stop if ``machines`` stopped feeding it.

    Returns ``(mw, generators, running)``. Only generators PROVEN to be running are
    charged: a machine that produced inside the last complete 300 s window certainly had
    power and fuel, and one that did not may be idle for a dozen reasons. Counting the
    idle ones would inflate the risk of touching a line that is already dead.
    """
    downstream = trace(state, game, machines, direction="down")
    mw = 0.0
    total = running = 0
    by_instance = {r["instance"].rsplit(".", 1)[-1]: r for r in state._all_records()}
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

"""A machine set as recipe groups joined by items, with where each output physically goes.

``output_destinations`` walks one output downstream over the contracted belt and pipe runs;
``build`` groups machines by recipe, apportions nameplate rates onto the edges and classes
every item as product, sunk, intermediate, unrouted or input. docs/frontend_vision.md §9.4
has the rules.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio.records import actor_class
from ..world.logistics import PhysicalGraph
from .health import ACTIONABLE, assess
from .query import FactoryView

__all__ = [
    "BUFFER",
    "EXPORT",
    "INSIDE",
    "NOWHERE",
    "SINK",
    "STORAGE",
    "FlowGraph",
    "build",
    "node_kind",
    "output_destinations",
]

INSIDE, STORAGE, BUFFER, EXPORT, SINK, NOWHERE = (
    "inside",
    "storage",
    "buffer",
    "export",
    "sink",
    "nowhere",
)

TERMINALS = (STORAGE, EXPORT, SINK, NOWHERE)

#: Class fragments of the nodes a run passes through rather than ends at.
FITTINGS = ("Splitter", "Merger", "Junction", "Pump", "Valve", "Attachment")

#: Walk cap in link hops; a real belt web is a few dozen.
MAX_HOPS = 400


def node_kind(actor: str) -> str:
    """``sink``, ``storage`` or ``other`` for a node a run ends at, by its class name."""
    cls = actor_class(actor)
    if "ResourceSink" in cls:
        return SINK
    if "Storage" in cls or "IndustrialTank" in cls:
        return STORAGE
    return "other"


def output_destinations(
    physical: PhysicalGraph,
    start: str,
    medium: str,
    inside: set[str],
    is_machine: Callable[[str], bool],
) -> set[tuple[str, str | None]]:
    """Where ``start``'s ``medium`` outputs can end up: ``(kind, actor)`` pairs.

    Fittings are walked through. A box or tank with a way out is a ``BUFFER`` and is walked
    through too; one with none is ``STORAGE``. A machine is ``INSIDE`` or ``EXPORT``; any
    other node that is not a fitting is ``EXPORT``; a run to nothing is ``NOWHERE``.
    """
    found: set[tuple[str, str | None]] = set()
    seen = {start}
    stack = [(start, 0)]
    while stack:
        node, hops = stack.pop()
        if hops > MAX_HOPS:
            continue
        for link in physical.drains(node):
            if link.medium != medium:
                continue
            nxt = link.target if link.source == node else link.other(node)
            if nxt is None:
                found.add((NOWHERE, None))
                continue
            if nxt in seen:
                continue
            seen.add(nxt)
            if is_machine(nxt):
                found.add((INSIDE if nxt in inside else EXPORT, nxt))
                continue
            kind = node_kind(nxt)
            if kind == SINK:
                found.add((SINK, nxt))
                continue
            onward = [x for x in physical.drains(nxt) if x.medium == medium]
            if kind == STORAGE:
                found.add((BUFFER if onward else STORAGE, nxt))
            elif not onward:
                found.add((NOWHERE if any(f in nxt for f in FITTINGS) else EXPORT, nxt))
            if onward:
                stack.append((nxt, hops + 1))
    return found


@dataclass
class Group:
    key: str
    building: str
    recipe: str
    machines: list[str] = field(default_factory=list)
    clocks: list[float] = field(default_factory=list)
    makes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    uses: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    states: Counter = field(default_factory=Counter)
    health: Counter = field(default_factory=Counter)


@dataclass(frozen=True)
class FlowEdge:
    source: str
    target: str
    item: str
    per_min: float | None


@dataclass
class FlowGraph:
    groups: dict[str, Group] = field(default_factory=dict)
    edges: list[FlowEdge] = field(default_factory=list)
    #: item -> product | sunk | intermediate | unrouted | input
    roles: dict[str, str] = field(default_factory=dict)
    #: item -> the terminal kinds any of its output reaches
    destinations: dict[str, set[str]] = field(default_factory=dict)
    produced: dict[str, float] = field(default_factory=dict)
    consumed: dict[str, float] = field(default_factory=dict)
    #: Boxes and tanks the walk passed through because something drains them.
    buffers: set[str] = field(default_factory=set)
    #: terminal kind -> how many distinct actors of that kind were reached
    terminals: Counter = field(default_factory=Counter)

    def listed(self, role: str) -> list[tuple[str, float]]:
        side = self.consumed if role == "input" else self.produced
        rows = [(item, side.get(item, 0.0)) for item, r in self.roles.items() if r == role]
        return sorted(rows, key=lambda row: (-row[1], row[0]))


def _medium(game: GameData, item: str) -> str:
    found = next((x for x in game.items.values() if x.name == item), None)
    return ports.PIPE if found is not None and found.is_fluid else ports.CONVEYOR


def _group_machines(out: FlowGraph, state, game: GameData, view: FactoryView) -> dict[str, str]:
    """Fill ``out.groups`` by building and recipe; returns each machine's group key."""
    inside = sorted(row.instance for row in view.machines)
    report = assess(view.name, inside, game, state.projection, state.graph)
    state_of = {m.instance: m.state for m in report.machines}
    group_of: dict[str, str] = {}
    for row in view.machines:
        label = row.recipe or ", ".join(sorted(row.makes)) or ", ".join(sorted(row.uses))
        key = f"{row.building}|{label}"
        group = out.groups.get(key)
        if group is None:
            group = out.groups[key] = Group(
                key, game.building_name(row.building) or row.building, label
            )
        group.machines.append(row.instance)
        group.clocks.append(row.clock)
        for item, rate in row.makes.items():
            group.makes[item] += rate
        for item, rate in row.uses.items():
            group.uses[item] += rate
        machine_state = state_of.get(row.instance, "unmonitored")
        if machine_state == "blocked":
            group.states["blocked"] += 1
        else:
            group.states["stopped" if machine_state in ACTIONABLE else "running"] += 1
        group.health[machine_state] += 1
        group_of[row.instance] = key
    for group in out.groups.values():
        for item, rate in group.makes.items():
            out.produced[item] = out.produced.get(item, 0.0) + rate
        for item, rate in group.uses.items():
            out.consumed[item] = out.consumed.get(item, 0.0) + rate
    return group_of


def _walk_outputs(out: FlowGraph, state, game: GameData, view: FactoryView, group_of: dict):
    """Where every group's outputs go: ``(reach, kinds, used_inside)``.

    ``reach`` maps (group, item) to the groups inside that use it, ``kinds`` to the terminal
    kinds it reaches outside; boxes walked through land in ``out.buffers``.
    """
    inside = {row.instance for row in view.machines}
    physical = state.physical
    reach: dict[tuple[str, str], set[str]] = defaultdict(set)
    kinds: dict[tuple[str, str], set[str]] = defaultdict(set)
    medium_cache: dict[str, str] = {}
    terminal_actors: dict[str, set[str]] = defaultdict(set)
    used_inside: set[str] = set()
    for row in view.machines:
        for item in row.makes:
            medium = medium_cache.setdefault(item, _medium(game, item))
            pair = (group_of[row.instance], item)
            destinations = output_destinations(
                physical, row.instance, medium, inside, state.graph.is_machine
            )
            for kind, actor in destinations:
                if kind == INSIDE:
                    used_inside.add(item)
                    target = group_of.get(actor or "")
                    if target and item in out.groups[target].uses:
                        reach[pair].add(target)
                elif kind == BUFFER:
                    out.buffers.add(actor or "")
                else:
                    kinds[pair].add(kind)
                    terminal_actors[kind].add(actor or "")
    out.terminals = Counter({k: len(v) for k, v in terminal_actors.items()})
    return reach, kinds, used_inside


def _apportion(out: FlowGraph, reach: dict, kinds: dict) -> None:
    """Share each group's demand over the groups that reach it, then its surplus over the
    terminal kinds it reaches; an unmet demand comes ``in:`` from outside."""
    allotted: dict[tuple[str, str], float] = defaultdict(float)
    for target, group in out.groups.items():
        for item, demand in group.uses.items():
            suppliers = [
                producer
                for producer in out.groups
                if target in reach.get((producer, item), ()) and producer != target
            ]
            made = sum(out.groups[producer].makes[item] for producer in suppliers)
            for producer in suppliers:
                share = demand * out.groups[producer].makes[item] / made if made else 0.0
                allotted[(producer, item)] += share
                out.edges.append(FlowEdge(producer, target, item, round(share, 2)))
            if made < demand - 1e-6:
                out.edges.append(FlowEdge(f"in:{item}", target, item, round(demand - made, 2)))

    for (producer, item), reached in kinds.items():
        surplus = out.groups[producer].makes[item] - allotted[(producer, item)]
        each = round(surplus / len(reached), 2) if surplus > 1e-6 else None
        for kind in sorted(reached):
            out.edges.append(FlowEdge(producer, kind, item, each))


def _item_roles(out: FlowGraph, kinds: dict, used_inside: set[str]) -> None:
    for item in set(out.produced) | set(out.consumed):
        where = set().union(*(kinds.get((producer, item), set()) for producer in out.groups))
        out.destinations[item] = where
        if item not in out.produced:
            out.roles[item] = "input"
        elif STORAGE in where or EXPORT in where:
            out.roles[item] = "product"
        elif SINK in where:
            out.roles[item] = "sunk"
        elif item in used_inside:
            out.roles[item] = "intermediate"
        else:
            out.roles[item] = "unrouted"


def build(state, game: GameData, view: FactoryView) -> FlowGraph:
    """The recipe-group graph of ``view``'s machines, with item roles and apportioned rates."""
    out = FlowGraph()
    group_of = _group_machines(out, state, game, view)
    reach, kinds, used_inside = _walk_outputs(out, state, game, view, group_of)
    _apportion(out, reach, kinds)
    _item_roles(out, kinds, used_inside)
    return out

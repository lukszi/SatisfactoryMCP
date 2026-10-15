"""A built factory's flow graph in the plan graph's wire shape, so one page drawing serves both.

``flowgraph.build`` gives recipe groups, apportioned nameplate flows and the terminal kinds
each output physically reaches; this module gives them class-id node ids, ranks, item groups,
power and the measured productivity. docs/frontend_vision.md §9.8 has the shape and which
figures are measured and which computed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import TypeAlias

from ...core.gamedata.model import GameData
from ..planning.readout.item_groups import item_group
from ..planning.readout.views import PlanGraphBuilding
from .flowgraph import EXPORT, NOWHERE, SINK, STORAGE, FlowGraph, Group
from .query import FactoryView, MachineRow
from .views import BuiltGraph, BuiltGraphEdge, BuiltGraphNode

__all__ = ["Box", "built_graph"]

#: Machine instances to their bounding box on the map, or None when none is placed.
Box: TypeAlias = Callable[[list[str]], "tuple[float, float, float, float] | None"]

_EPS = 1e-6

#: Terminal kind -> (node kind, id prefix, note under the rate).
_ENDS: dict[str, tuple[str, str, str]] = {
    EXPORT: ("export", "ex", "leaves the {}"),
    STORAGE: ("export", "box", "to storage"),
    SINK: ("sink", "sink", "sunk"),
    NOWHERE: ("leftover", "left", "backs up"),
}
_LEFT = "left"
#: Edges carry rates rounded to 0.01/min; less than a few of those left over is rounding.
_ROUNDING = 0.05


class _Names:
    """Item names back to class ids, and their units."""

    def __init__(self, game: GameData) -> None:
        self.game = game
        self.ids = {item.name: cls for cls, item in game.items.items()}

    def id(self, name: str) -> str:
        return self.ids.get(name, name)

    def unit(self, name: str) -> str:
        found = self.game.items.get(self.id(name))
        return "m3" if found is not None and found.is_fluid else "items"


def _utilisation(work: float) -> float:
    part = work - math.floor(work)
    return 1.0 if part < _EPS or part > 1 - _EPS else round(part, 4)


def _process_id(row: MachineRow, names: _Names) -> str:
    """``p:<recipe>``, ``p:x:<extractor>:<resource>`` or ``p:g:<generator>:<fuel>``, as a plan."""
    if row.recipe_id:
        return "p:" + row.recipe_id
    if row.makes:
        return f"p:x:{row.building}:{names.id(min(row.makes))}"
    if row.uses:
        return f"p:g:{row.building}:{names.id(min(row.uses))}"
    return f"p:{row.building}:{row.recipe or 'idle'}"


def _process_label(
    group: Group, first: MachineRow, game: GameData, names: _Names
) -> tuple[str, str | None]:
    """The node's label and the class id of the item it is for."""
    recipe = game.recipes.get(first.recipe_id)
    if recipe is not None:
        return recipe.name, recipe.main_product
    if first.makes:
        made = min(first.makes)
        return f"{group.building} on {made}", names.id(made)
    if first.uses:
        return f"{group.building} on {min(first.uses)}", None
    return f"{group.building} · {group.recipe or 'no recipe'}", None


def _process_node(
    group: Group, rows: list[MachineRow], game: GameData, names: _Names, node_id: str
) -> BuiltGraphNode:
    first = rows[0]
    label, item_id = _process_label(group, first, game, names)
    recipe = game.recipes.get(first.recipe_id)
    building = game.buildings.get(first.building)
    running = [row for row in rows if not row.paused]
    work = sum(row.clock for row in running)
    shares = [row.share for row in running if row.share is not None]
    built = len(rows)
    return {
        "id": node_id,
        "kind": "process",
        "label": label,
        "detail": group.recipe,
        "rank": 1,
        "item_id": item_id,
        "item_name": game.item_name(item_id) if item_id else None,
        "recipe_id": first.recipe_id or None,
        "recipe_name": recipe.name if recipe is not None else None,
        "building": group.building,
        "building_icon": building.descriptor if building is not None else None,
        "built": built,
        "machines": round(work, 4),
        "clock": round(100 * sum(row.clock for row in rows) / built, 4) if built else None,
        "mw": round(sum(row.mw for row in rows), 2),
        "utilisation": _utilisation(work),
        "per_min": None,
        "unit": "items",
        "note": None,
        "productivity": round(sum(shares) / len(shares), 4) if shares else None,
        "monitored": len(shares),
        "states": dict(sorted(group.health.items())),
        "bbox_m": None,
    }


def _item_node(
    kind: str, node_id: str, name: str, names: _Names, rate: float, note: str
) -> BuiltGraphNode:
    item_id = names.id(name)
    return {
        "id": node_id,
        "kind": kind,
        "label": name,
        "detail": note,
        "rank": 0,
        "item_id": item_id,
        "item_name": name,
        "recipe_id": None,
        "recipe_name": None,
        "building": None,
        "building_icon": None,
        "built": 0,
        "machines": 0.0,
        "clock": None,
        "mw": 0.0,
        "utilisation": 1.0,
        "per_min": round(rate, 4),
        "unit": names.unit(name),
        "note": note,
        "productivity": None,
        "monitored": 0,
        "states": {},
        "bbox_m": None,
    }


def _edge(source: str, target: str, name: str, names: _Names, rate: float) -> BuiltGraphEdge:
    item_id = names.id(name)
    return {
        "id": f"{source}>{target}:{item_id}",
        "source": source,
        "target": target,
        "item_id": item_id,
        "item_name": name,
        "item_group": item_group(item_id),
        "per_min": round(rate, 4),
        "unit": names.unit(name),
        "text": None if rate > _EPS else f"{name} overflow",
    }


def _ranks(processes: Iterable[str], links: list[tuple[str, str]]) -> dict[str, int]:
    """Longest path from the inputs over the condensation: a cycle shares one column."""
    nodes = list(processes)
    succ: dict[str, list[str]] = defaultdict(list)
    for a, b in links:
        succ[a].append(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = [0, 0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        for w in succ[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w not in comp:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            while True:
                w = stack.pop()
                comp[w] = counter[1]
                if w == v:
                    break
            counter[1] += 1

    for v in nodes:
        if v not in index:
            visit(v)
    rank = {c: 1 for c in comp.values()}
    # Tarjan numbers components in reverse topological order: walk them downstream first.
    for c in sorted(rank, reverse=True):
        for v in (n for n in nodes if comp[n] == c):
            for w in succ[v]:
                if comp[w] != c:
                    rank[comp[w]] = max(rank[comp[w]], rank[c] + 1)
    return {v: rank[comp[v]] for v in nodes}


def _buildings(nodes: list[BuiltGraphNode]) -> list[PlanGraphBuilding]:
    totals: dict[str, PlanGraphBuilding] = {}
    for node in nodes:
        if node["kind"] != "process" or not node["building"]:
            continue
        name = node["building"]
        got = totals.setdefault(name, {"building": name, "count": 0, "groups": 0, "mw": 0.0})
        got["count"] += node["built"]
        got["groups"] += 1
        got["mw"] = round(got["mw"] + node["mw"], 2)
    return sorted(totals.values(), key=lambda b: (-abs(b["mw"]), b["building"]))


class _Builder:
    def __init__(self, flow: FlowGraph, view: FactoryView, game: GameData, whole: str) -> None:
        self.flow, self.game, self.whole = flow, game, whole
        self.names = _Names(game)
        self.rows = {row.instance: row for row in view.machines}
        self.ids: dict[str, str] = {}
        self.nodes: dict[str, BuiltGraphNode] = {}
        self.edges: dict[str, BuiltGraphEdge] = {}

    def processes(self) -> None:
        for key, group in sorted(self.flow.groups.items()):
            rows = [self.rows[m] for m in group.machines if m in self.rows]
            if not rows:
                continue
            node_id = _process_id(rows[0], self.names)
            if node_id in self.nodes:
                node_id += ":" + rows[0].building
            self.ids[key] = node_id
            self.nodes[node_id] = _process_node(group, rows, self.game, self.names, node_id)

    def end(self, kind: str, prefix: str, name: str, note: str) -> str:
        node_id = f"{prefix}:{self.names.id(name)}"
        if node_id not in self.nodes:
            self.nodes[node_id] = _item_node(kind, node_id, name, self.names, 0.0, note)
        return node_id

    def link(self, source: str, target: str, name: str, rate: float) -> None:
        edge = _edge(source, target, name, self.names, rate)
        got = self.edges.get(edge["id"])
        if got is None:
            self.edges[edge["id"]] = edge
        else:
            got["per_min"] = round(got["per_min"] + rate, 4)
            got["text"] = None if got["per_min"] > _EPS else got["text"]
        for end in (source, target):
            node = self.nodes[end]
            if node["kind"] != "process":
                node["per_min"] = round((node["per_min"] or 0.0) + rate, 4)

    def flows(self) -> None:
        sent: dict[tuple[str, str], float] = defaultdict(float)
        for e in self.flow.edges:
            rate = e.per_min or 0.0
            if e.source.startswith("in:"):
                source = self.end("input", "in", e.item, f"enters the {self.whole}")
            else:
                source = self.ids[e.source]
                sent[(e.source, e.item)] += rate
            if e.target in _ENDS:
                kind, prefix, note = _ENDS[e.target]
                target = self.end(kind, prefix, e.item, note.format(self.whole))
            else:
                target = self.ids[e.target]
            self.link(source, target, e.item, rate)
        self.untaken(sent)

    def untaken(self, sent: dict[tuple[str, str], float]) -> None:
        """Output that no edge carries, e.g. from an unconnected port, goes to left over."""
        for key, group in sorted(self.flow.groups.items()):
            if key not in self.ids:
                continue
            for name, made in sorted(group.makes.items()):
                rest = made - sent[(key, name)]
                if rest > _ROUNDING and not self.reaches_end(key, name):
                    target = self.end("leftover", _LEFT, name, "backs up")
                    self.link(self.ids[key], target, name, rest)

    def reaches_end(self, key: str, name: str) -> bool:
        return any(
            e.source == key and e.item == name and e.target in _ENDS for e in self.flow.edges
        )

    def ranked(self) -> None:
        processes = [n for n, node in self.nodes.items() if node["kind"] == "process"]
        links = [
            (e["source"], e["target"])
            for e in self.edges.values()
            if self.nodes[e["source"]]["kind"] == "process"
            and self.nodes[e["target"]]["kind"] == "process"
        ]
        ranks = _ranks(processes, links)
        top = max(ranks.values(), default=0) + 1
        for node_id, node in self.nodes.items():
            if node["kind"] == "process":
                node["rank"] = ranks[node_id]
            elif node["kind"] != "input":
                node["rank"] = top


def built_graph(
    flow: FlowGraph, view: FactoryView, game: GameData, whole: str, box: Box | None = None
) -> BuiltGraph:
    """``flow`` over ``view``'s machines as nodes and edges; ``whole`` is ``factory`` or
    ``cluster``, the word an input "enters"; ``box`` gives a process its machines' bbox."""
    builder = _Builder(flow, view, game, whole)
    builder.processes()
    for key, node_id in builder.ids.items():
        builder.nodes[node_id]["bbox_m"] = box(flow.groups[key].machines) if box else None
    builder.flows()
    builder.ranked()
    nodes = sorted(builder.nodes.values(), key=lambda n: (n["rank"], n["id"]))
    running = [node["mw"] for node in nodes]
    return {
        "nodes": nodes,
        "edges": sorted(builder.edges.values(), key=lambda e: e["id"]),
        "buildings": _buildings(nodes),
        "mw_draw": round(-sum(mw for mw in running if mw < 0), 2),
        "mw_generated": round(sum(mw for mw in running if mw > 0), 2),
    }

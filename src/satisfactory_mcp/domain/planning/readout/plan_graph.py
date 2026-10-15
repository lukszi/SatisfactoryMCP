"""The production graph a solve reads out as: one node per process group, flows between them.

docs/planner_vision.md §4.2 is the shape and the id rules; docs/planning.md §8.5o is how one
item's balance is split into flows and what counts as left over.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ..solver.model import MW, NEGLIGIBLE_IPM, ProcessRow, Scenario, SinkRow, Solution
from .flows import decompose
from .item_groups import item_group
from .views import PlanGraph, PlanGraphBuilding, PlanGraphEdge, PlanGraphNode, SolveRow

__all__ = ["POWER_ID", "SINK_BUILDING", "main_id", "production_graph"]

#: The item id power goes by in node and edge ids.
POWER_ID = "power"
SINK_BUILDING = "AWESOME Sink"
SINK_BUILD_ID = "Build_ResourceSink_C"

_EPS = 1e-6
#: An item balance off by less than this is rounding, not something left over.
_TOL = NEGLIGIBLE_IPM

#: Order among the nodes that end an item's line, after the processes that use it.
_SINK_ORDER = {"export": 0, "leftover": 1, "sink": 2}


def main_id(g: GameData, proc: ProcessRow) -> str | None:
    """The item a row is for: its recipe's main product, else its largest output."""
    recipe = g.recipes.get(proc["recipe"] or "")
    if recipe is not None and recipe.main_product is not None:
        return recipe.main_product
    made = [(rate, item) for item, rate in proc["rates"].items() if rate > _EPS and item != MW]
    return max(made)[1] if made else None


def _group_key(proc: ProcessRow) -> str:
    """What makes rows one node: the recipe, an extractor's building and resource, or the pid."""
    if proc["kind"] == "recipe" and proc["recipe"]:
        return proc["recipe"]
    if proc["kind"] == "extractor":
        made = sorted(i for i, r in proc["rates"].items() if r > _EPS and i != MW)
        return f"x:{proc['building_id'] or proc['building']}:{made[0] if made else ''}"
    return proc["pid"]


@dataclass
class _Group:
    id: str
    rows: list[tuple[SolveRow, ProcessRow]] = field(
        default_factory=list[tuple[SolveRow, ProcessRow]]
    )
    net: dict[str, float] = field(default_factory=dict[str, float])

    @property
    def rank(self) -> int:
        return max(row["depth"] for row, _ in self.rows) + 1

    @property
    def built(self) -> int:
        return sum(row["machines"] for row, _ in self.rows)

    @property
    def units(self) -> float:
        return sum(row["machines"] * row["clock"] for row, _ in self.rows)

    @property
    def mw(self) -> float:
        return sum(row["mw"] for row, _ in self.rows)


def _groups(paired: list[tuple[SolveRow, ProcessRow]]) -> list[_Group]:
    by_id: dict[str, _Group] = {}
    for row, proc in paired:
        group = by_id.setdefault("p:" + _group_key(proc), _Group("p:" + _group_key(proc)))
        group.rows.append((row, proc))
        for item, rate in proc["rates"].items():
            if item != MW:
                group.net[item] = group.net.get(item, 0.0) + rate
    return list(by_id.values())


def _item_name(g: GameData, item: str) -> str:
    return "power" if item == MW else g.item_name(item)


def _unit(g: GameData, item: str) -> str:
    if item == MW:
        return "MW"
    found = g.items.get(item)
    return "m3" if found is not None and found.is_fluid else "items"


def _num(value: float, dp: int = 1) -> str:
    scale = 10**dp
    text = f"{math.floor(value * scale + 0.5) / scale:,.{dp}f}"
    if dp:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _mw_text(value: float, signed: bool = False) -> str:
    whole = -math.floor(-value + 0.5) if value < 0 else math.floor(value + 0.5)
    return ("+" if signed and whole > 0 else "") + f"{whole:,}" + " MW"


def _utilisation(units: float) -> float:
    part = units - math.floor(units)
    return 1.0 if part < _EPS or part > 1 - _EPS else round(part, 4)


def _building_icon(g: GameData, building_id: str | None) -> str | None:
    """The descriptor class a building's picture is filed under, as ``/api/icons/`` serves it."""
    building = g.buildings.get(building_id or "")
    return building.descriptor if building is not None else None


def _process_node(g: GameData, group: _Group) -> PlanGraphNode:
    first, proc = group.rows[0]
    recipe = g.recipes.get(proc["recipe"] or "")
    item = main_id(g, proc)
    if recipe is not None:
        label = recipe.name
    elif proc["kind"] == "extractor" and item is not None:
        label = f"{first['building']} on {g.item_name(item)}"
    else:
        label = proc["label"]
    built, units, mw = group.built, group.units, group.mw
    clock = 100 * units / built if built else None
    power = f" · {_mw_text(mw, signed=True)}" if mw else ""
    detail = f"{first['building']} ×{built:,}{power}"
    return {
        "id": group.id,
        "kind": "process",
        "label": label,
        "detail": detail + (f" · {_num(clock, 1)}%" if clock is not None else ""),
        "rank": group.rank,
        "row": first["id"],
        "rows": [row["id"] for row, _ in group.rows],
        "item": item,
        "item_id": item,
        "item_name": g.item_name(item) if item is not None else None,
        "recipe_id": proc["recipe"],
        "recipe_name": recipe.name if recipe is not None else None,
        "building": first["building"] or None,
        "building_icon": _building_icon(g, proc["building_id"]),
        "built": built,
        "machines": round(units, 4),
        "clock": round(clock, 4) if clock is not None else None,
        "mw": round(mw, 2),
        "utilisation": _utilisation(units),
        "per_min": None,
        "unit": "items",
    }


def _item_node(
    g: GameData, kind: str, item: str, per_min: float, rank: int, detail: str
) -> PlanGraphNode:
    power = item == MW
    key = POWER_ID if power else item
    prefix = {"input": "in", "export": "ex", "leftover": "left", "sink": "sink"}[kind]
    return {
        "id": f"{prefix}:{key}",
        "kind": kind,
        "label": _item_name(g, item),
        "detail": detail,
        "rank": rank,
        "row": None,
        "rows": [],
        "item": None if power else item,
        "item_id": key,
        "item_name": _item_name(g, item),
        "recipe_id": None,
        "recipe_name": None,
        "building": None,
        "building_icon": None,
        "built": 0,
        "machines": 0.0,
        "clock": None,
        "mw": None,
        "utilisation": 1.0,
        "per_min": round(per_min, 4),
        "unit": _unit(g, item),
    }


def _amount(g: GameData, item: str, rate: float) -> str:
    if item == MW:
        return _mw_text(rate)
    return _num(rate, 1) + (" m³/min" if _unit(g, item) == "m3" else "/min")


@dataclass
class _Ends:
    """Where each item leaves the plan: export, left over, sunk; and what comes from outside."""

    exported: dict[str, float] = field(default_factory=dict[str, float])
    leftover: dict[str, float] = field(default_factory=dict[str, float])
    sunk: dict[str, float] = field(default_factory=dict[str, float])
    drawn: dict[str, float] = field(default_factory=dict[str, float])


def _ends(groups: list[_Group], sol: Solution, sc: Scenario) -> _Ends:
    """Each item's export, the excess over an asked rate and the unbalanced rest as left
    over, its sunk rate, and what is drawn from outside (§8.5o)."""
    out = _Ends(sunk={row["item"]: row["rate"] for row in sol.sinks if row["rate"] > _EPS})
    made: dict[str, float] = {}
    for group in groups:
        for item, rate in group.net.items():
            made[item] = made.get(item, 0.0) + rate
    items = sorted(set(made) | set(sol.exports) | set(sol.raw_used) | set(out.sunk))
    maximised = sc.target_item if sc.objective == "max_item" else None
    for item in items:
        exported = sol.exports.get(item, 0.0)
        asked = sc.export_minimums.get(item) or 0.0
        if item != MW and item != maximised and asked > _EPS and exported > asked + _TOL:
            out.leftover[item] = exported - asked
            exported = asked
        if exported > _EPS:
            out.exported[item] = exported
        if item == MW:
            continue
        drawn = sol.raw_used.get(item, 0.0)
        rest = made.get(item, 0.0) + drawn - sol.exports.get(item, 0.0) - out.sunk.get(item, 0.0)
        if rest > _TOL:
            out.leftover[item] = out.leftover.get(item, 0.0) + rest
        elif rest < -_TOL:
            drawn -= rest
        if drawn > _EPS:
            out.drawn[item] = drawn
    return out


def _edge(g: GameData, source: str, target: str, item: str, rate: float) -> PlanGraphEdge:
    key = POWER_ID if item == MW else item
    return {
        "id": f"{source}>{target}:{key}",
        "source": source,
        "target": target,
        "item": "MW" if item == MW else g.item_name(item),
        "item_id": key,
        "item_name": _item_name(g, item),
        "item_group": item_group(item),
        "per_min": round(rate, 4),
        "unit": _unit(g, item),
        "text": _mw_text(rate) if item == MW else None,
    }


def _ordered(nodes: Iterable[tuple[int, str, float]]) -> list[tuple[str, float]]:
    return [(node, amount) for _, node, amount in sorted(nodes)]


def _item_edges(g: GameData, groups: list[_Group], ends: _Ends, top: int) -> list[PlanGraphEdge]:
    """Every item's balance split into flows, upstream suppliers to upstream users first."""
    items = sorted({i for grp in groups for i in grp.net} | set(ends.exported) | set(ends.drawn))
    edges: list[PlanGraphEdge] = []
    for item in items:
        if item == MW:
            continue
        supply = [(0, "in:" + item, ends.drawn[item])] if item in ends.drawn else []
        supply += [
            (grp.rank, grp.id, grp.net[item]) for grp in groups if grp.net.get(item, 0) > _EPS
        ]
        demand = [
            (grp.rank, grp.id, -grp.net[item]) for grp in groups if grp.net.get(item, 0) < -_EPS
        ]
        tail = [
            ("export", "ex:" + item, ends.exported.get(item, 0.0)),
            ("leftover", "left:" + item, ends.leftover.get(item, 0.0)),
            ("sink", "sink:" + item, ends.sunk.get(item, 0.0)),
        ]
        demand += [(top + _SINK_ORDER[kind], node, amount) for kind, node, amount in tail]
        for source, target, rate in decompose(_ordered(supply), _ordered(demand)):
            edges.append(_edge(g, source, target, item, rate))
    return edges


def _power_edges(g: GameData, groups: list[_Group], ends: _Ends) -> list[PlanGraphEdge]:
    """Exported MW from the generators, in node order; their other output runs the plant."""
    exported = ends.exported.get(MW, 0.0)
    if exported <= _EPS:
        return []
    supply = _ordered((grp.rank, grp.id, grp.mw) for grp in groups if grp.mw > _EPS)
    flows = decompose(supply, [("ex:" + POWER_ID, exported)])
    return [_edge(g, source, target, MW, rate) for source, target, rate in flows]


def _sink_node(g: GameData, row: SinkRow, rank: int) -> PlanGraphNode:
    item, rate, sinks = row["item"], row["rate"], row["sinks"]
    many = "s" if sinks != 1 else ""
    node = _item_node(
        g,
        "sink",
        item,
        rate,
        rank,
        f"sunk {_amount(g, item, rate)} · {sinks} {SINK_BUILDING}{many}",
    )
    node.update(
        building=SINK_BUILDING,
        building_icon=_building_icon(g, SINK_BUILD_ID),
        built=sinks,
        machines=float(sinks),
        mw=-round(row["mw"], 2),
    )
    return node


def _buildings(groups: list[_Group], sol: Solution) -> list[PlanGraphBuilding]:
    """Machines and MW per building type, the largest power figure first."""
    totals: dict[str, PlanGraphBuilding] = {}
    for group in groups:
        name = group.rows[0][0]["building"] or "?"
        got = totals.setdefault(name, {"building": name, "count": 0, "groups": 0, "mw": 0.0})
        got["count"] += group.built
        got["groups"] += 1
        got["mw"] += group.mw
    if sol.sinks:
        totals[SINK_BUILDING] = {
            "building": SINK_BUILDING,
            "count": sum(row["sinks"] for row in sol.sinks),
            "groups": len(sol.sinks),
            "mw": -sum(row["mw"] for row in sol.sinks),
        }
    for got in totals.values():
        got["mw"] = round(got["mw"], 2)
    return sorted(totals.values(), key=lambda b: (-abs(b["mw"]), b["building"]))


def production_graph(
    g: GameData,
    paired: list[tuple[SolveRow, ProcessRow]],
    sol: Solution,
    sc: Scenario,
    positions: Mapping[str, list[float]] | None = None,
) -> PlanGraph:
    """The graph over rows that carry their ``id`` and ``depth``, with ``positions`` kept for
    the nodes it has. Nodes: inputs, processes, exports, leftovers, sinks."""
    groups = _groups(paired)
    ends = _ends(groups, sol, sc)
    top = max((grp.rank for grp in groups), default=0) + 1
    nodes = [
        _item_node(g, "input", item, rate, 0, "from outside the plan")
        for item, rate in ends.drawn.items()
    ]
    nodes += [_process_node(g, group) for group in groups]
    nodes += [
        _item_node(g, "export", item, rate, top, "exported " + _amount(g, item, rate))
        for item, rate in ends.exported.items()
    ]
    nodes += [
        _item_node(g, "leftover", item, rate, top, "left over " + _amount(g, item, rate))
        for item, rate in ends.leftover.items()
    ]
    nodes += [_sink_node(g, row, top) for row in sol.sinks if row["rate"] > _EPS]
    edges = _item_edges(g, groups, ends, top) + _power_edges(g, groups, ends)
    ids = {node["id"] for node in nodes}
    kept = {k: list(v) for k, v in (positions or {}).items() if k in ids}
    return {
        "nodes": nodes,
        "edges": edges,
        "positions": kept,
        "buildings": _buildings(groups, sol),
    }

"""Answer questions about a named factory, or about any machine set.

One entry point rather than eight tools: every question resolves a machine set, then reads
one aspect off the ``FactoryView`` built here. Every rate is carried twice, nameplate and
measured, and never blended; ``balance`` (production minus consumption per item) is the view
worth most. docs/save-projection.md §6.2c has the rules and the measurements behind them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import NamedTuple, TypeAlias

from ...core.gamedata.model import GameData
from ...core.saveio.records import instance_leaf
from ...core.saveio.schema import BuildableRecord, Projection
from ..power.report import measured_share
from ..spatial import geo
from ..spatial import nodes as nodes_mod
from .labels import LabelStore
from .model import FactoryGraph
from .views import BalanceRow

#: Item name -> ``produced``/``consumed`` rates, each also ``measured_`` and ``unmonitored_``.
Flows: TypeAlias = dict[str, dict[str, float]]
#: ``(instance leaf, record)`` for each member of one projection group.
Members: TypeAlias = list[tuple[str, BuildableRecord]]

__all__ = ["ASPECTS", "WATER_VOLUME_PURITY", "FactoryView", "NodeRow", "build_view"]

#: What can be asked for; explicit so an unknown aspect is an error with a list.
ASPECTS = (
    "summary",
    "machines",
    "recipes",
    "buildings",
    "balance",
    "inputs",
    "outputs",
    "internal",
    "power",
    "nodes",
    "links",
    "issues",
)

#: A Water Extractor stands on a water volume, which has no purity; "?" read as broken.
WATER_VOLUME_PURITY = "n/a (water volume)"


@dataclass
class MachineRow:
    instance: str
    building: str
    recipe: str
    clock: float
    paused: bool
    #: ``(x, y, z)`` in centimetres, as the record carries it.
    pos: tuple[float, ...]
    #: Nameplate items/min this machine makes and uses, by item name; empty when paused.
    makes: dict[str, float] = field(default_factory=dict[str, float])
    uses: dict[str, float] = field(default_factory=dict[str, float])


class NodeRow(NamedTuple):
    """One extractor's node; resource and purity come from the node table, never the save."""

    node: str
    resource: str
    purity: str
    extractor_cls: str
    clock: float
    resources_left: float | None


@dataclass
class FactoryView:
    """Everything derivable from one machine set, computed once."""

    name: str
    machines: list[MachineRow] = field(default_factory=list[MachineRow])
    recipes: Counter[str] = field(default_factory=Counter[str])
    buildings: Counter[str] = field(default_factory=Counter[str])
    #: item -> ``produced``/``consumed``, each bare (nameplate), ``measured_`` and
    #: ``unmonitored_``; measured + unmonitored <= nameplate.
    flows: Flows = field(default_factory=dict[str, dict[str, float]])
    #: Machines that contributed flow, and how many of those keep no monitor.
    producers: int = 0
    unmonitored_producers: int = 0
    #: Machines mid-production when the save was written: the snapshot beside the window.
    producing_now: int = 0
    draw_mw: float = 0.0
    #: Draw weighted by each machine's own productivity window; unmonitored ones in full.
    measured_draw_mw: float = 0.0
    unmonitored: int = 0
    generation_mw: float = 0.0
    nodes: list[NodeRow] = field(default_factory=list[NodeRow])
    #: Factory or label name -> how many of ITS machines this set reaches; asymmetric.
    links: Counter[str] = field(default_factory=Counter[str])
    issues: list[str] = field(default_factory=list[str])
    centroid: tuple[float, float] = (0.0, 0.0)
    spread_m: float = 0.0

    @property
    def size(self) -> int:
        return len(self.machines)

    def net(self, item: str) -> float:
        flow = self.flows.get(item, {})
        return flow.get("produced", 0.0) - flow.get("consumed", 0.0)

    def measured_net(self, item: str) -> float:
        flow = self.flows.get(item, {})
        return flow.get("measured_produced", 0.0) - flow.get("measured_consumed", 0.0)

    def measurable(self, item: str, side: str) -> bool:
        """Whether anything readable made (``produced``) or used (``consumed``) this item.

        False means the measured figure is UNKNOWN, not zero: a 0.00 there would report a
        factory the save cannot see as one that has stopped.
        """
        flow = self.flows.get(item, {})
        return flow.get(side, 0.0) - flow.get(f"unmonitored_{side}", 0.0) > 1e-9

    def outputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items with a surplus: they leave, or they back up."""
        nets = [(item, self.net(item)) for item in self.flows]
        return sorted([(k, v) for k, v in nets if v > tol], key=lambda kv: -kv[1])

    def inputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items in deficit: they must be fed in from outside."""
        deficits = [(item, -self.net(item)) for item in self.flows]
        return sorted([(k, v) for k, v in deficits if v > tol], key=lambda kv: -kv[1])

    def balance(self, tol: float = 1e-6) -> list[BalanceRow]:
        """Every item, largest net first: made, used, both nets and the verdict.

        ``measured_net`` is None where nothing readable touches the item: unknown, not zero.
        """
        rows: list[BalanceRow] = []
        for item in sorted(self.flows, key=lambda k: -abs(self.net(k))):
            flow = self.flows[item]
            net = self.net(item)
            readable = self.measurable(item, "produced") or self.measurable(item, "consumed")
            rows.append(
                {
                    "item": item,
                    "made": flow["produced"],
                    "used": flow["consumed"],
                    "net": net,
                    "measured_net": self.measured_net(item) if readable else None,
                    "verdict": (
                        "surplus" if net > tol else "needs feeding" if net < -tol else "internal"
                    ),
                }
            )
        return rows

    def internal(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Made and consumed within the set -- the mark of a self-contained line."""
        out: list[tuple[str, float]] = []
        for item, flow in self.flows.items():
            if abs(self.net(item)) <= tol and flow.get("produced", 0.0) > tol:
                out.append((item, flow["produced"]))
        return sorted(out, key=lambda kv: -kv[1])


def _empty_flow() -> dict[str, float]:
    return {
        "produced": 0.0,
        "consumed": 0.0,
        "measured_produced": 0.0,
        "measured_consumed": 0.0,
        "unmonitored_produced": 0.0,
        "unmonitored_consumed": 0.0,
    }


def _charge_power(view: FactoryView, rated: float, record: BuildableRecord) -> None:
    """One machine's draw, on both the nameplate and the measured side."""
    view.draw_mw += rated
    share = measured_share(record)
    if share is None:
        view.unmonitored += 1
    view.measured_draw_mw += rated if share is None else rated * share


def _add_flow(flows: Flows, item: str, side: str, rate: float, share: float | None) -> None:
    """One machine's contribution to one item. Unlike ``_charge_power``, an unmonitored
    machine is not counted in full: on this side that would invent throughput."""
    flow = flows[item]
    flow[side] += rate
    if share is None:
        flow[f"unmonitored_{side}"] += rate
    else:
        flow[f"measured_{side}"] += rate * share


def _count_producer(view: FactoryView, record: BuildableRecord, share: float | None) -> None:
    """Count one machine that contributes flow, so the measured column can be read."""
    view.producers += 1
    if share is None:
        view.unmonitored_producers += 1
    if (record.get("uptime") or {}).get("producing"):
        view.producing_now += 1


def _machine_row(record: BuildableRecord, leaf: str, recipe_name: str = "") -> MachineRow:
    return MachineRow(
        instance=leaf,
        building=record.get("cls", "?"),
        recipe=recipe_name,
        clock=float(record.get("clock") or 1.0),
        paused=bool(record.get("paused")),
        pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
    )


def _members(projection: Projection, group: str, wanted: set[str]) -> Members:
    """``(leaf, record)`` for every record of ``group`` in the machine set."""
    leaves = ((instance_leaf(r.get("instance")), r) for r in projection.get(group, ()))
    return [(leaf, record) for leaf, record in leaves if leaf in wanted]


def _place(
    view: FactoryView,
    points: list[tuple[float, float]],
    record: BuildableRecord,
    leaf: str,
    recipe_name: str = "",
) -> MachineRow:
    """List one member, count its building and remember where it stands."""
    row = _machine_row(record, leaf, recipe_name)
    view.machines.append(row)
    view.buildings[row.building] += 1
    if pos := record.get("pos"):
        points.append((pos[0], pos[1]))
    return row


def _add_manufacturers(
    view: FactoryView,
    flows: Flows,
    points: list[tuple[float, float]],
    members: Members,
    game: GameData,
) -> None:
    for leaf, record in members:
        recipe_id = record.get("recipe")
        recipe = game.recipes.get(recipe_id or "")
        row = _place(view, points, record, leaf, recipe.name if recipe else "")
        if recipe is None:
            if recipe_id is None:
                view.issues.append(f"{leaf}: no recipe set, produces nothing")
            continue
        view.recipes[recipe.name] += 1
        if row.paused:
            view.issues.append(f"{leaf}: paused ({recipe.name})")
            continue
        share = measured_share(record)
        _count_producer(view, record, share)
        for product in recipe.products:
            rate = product.per_min * row.clock
            _add_flow(flows, game.item_name(product.item), "produced", rate, share)
            row.makes[game.item_name(product.item)] = rate
        for ingredient in recipe.ingredients:
            rate = ingredient.per_min * row.clock
            _add_flow(flows, game.item_name(ingredient.item), "consumed", rate, share)
            row.uses[game.item_name(ingredient.item)] = rate
        if game.buildings.get(record.get("cls", "")) is None:
            # Silently contributing 0 MW would understate the whole factory's draw.
            view.issues.append(f"{leaf}: unknown building {record.get('cls')!r}, power not counted")
        else:
            _charge_power(view, game.recipe_power_mw(recipe, row.clock), record)


def _node_resource(
    record: BuildableRecord, node_table: Mapping[str, Mapping[str, object]]
) -> tuple[str, str]:
    """``(resource id, purity)`` of an extractor's node, ``""`` for what the table lacks."""
    node: str | None = record.get("node")
    meta = node_table.get(node or "") or node_table.get(instance_leaf(node) if node else "") or {}
    resource, purity = str(meta.get("resource") or ""), str(meta.get("purity") or "")
    if not resource and "WaterPump" in record.get("cls", ""):
        return "Desc_Water_C", WATER_VOLUME_PURITY
    return resource, purity


def _add_extractors(
    view: FactoryView,
    flows: Flows,
    points: list[tuple[float, float]],
    members: Members,
    game: GameData,
    projection: Projection,
) -> None:
    node_state = projection.get("node_state", {})
    try:
        node_table = nodes_mod.load_nodes().by_instance()
    except Exception:
        node_table = {}
    for leaf, record in members:
        row = _place(view, points, record, leaf)
        node = record.get("node")
        resource, purity = _node_resource(record, node_table)
        view.nodes.append(
            NodeRow(
                node=instance_leaf(node) if node else "(unresolved)",
                resource=game.item_name(resource) if resource else "?",
                purity=purity or "?",
                extractor_cls=record.get("cls", "?"),
                clock=row.clock,
                resources_left=(node_state.get(node or "") or {}).get("resources_left"),
            )
        )
        if row.paused:
            view.issues.append(f"{leaf}: paused extractor")
            continue
        building = game.buildings.get(record.get("cls", ""))
        if building is None:
            continue
        _charge_power(view, building.power_at(row.clock), record)
        share = measured_share(record)
        if resource and purity:
            _count_producer(view, record, share)
            grade = "normal" if purity == WATER_VOLUME_PURITY else purity
            rate = building.extract_rate(grade, row.clock)
            _add_flow(flows, game.item_name(resource), "produced", rate, share)
            row.makes[game.item_name(resource)] = rate
        elif not node:
            view.issues.append(f"{leaf}: extractor bound to no node, output unknown")
        else:
            view.issues.append(
                f"{leaf}: node {instance_leaf(node)} not in the node table, output unknown"
            )


def _add_generators(
    view: FactoryView,
    flows: Flows,
    points: list[tuple[float, float]],
    members: Members,
    game: GameData,
) -> None:
    for leaf, record in members:
        row = _place(view, points, record, leaf)
        if row.paused:
            view.issues.append(f"{leaf}: paused generator")
            continue
        building = game.buildings.get(record.get("cls", ""))
        if building is None:
            continue
        view.generation_mw += building.power_production_mw * row.clock
        fuel = game.items.get(record.get("fuel") or "")
        if fuel is not None:
            share = measured_share(record)
            _count_producer(view, record, share)
            rate = building.fuel_rate_per_min(fuel) * row.clock
            _add_flow(flows, fuel.name, "consumed", rate, share)
            row.uses[fuel.name] = rate


def _boundary_links(
    graph: FactoryGraph, wanted: set[str], labels: LabelStore | None
) -> Counter[str]:
    """Machines outside the set reachable from it, counted by the label they carry.

    The walk passes THROUGH belts, pipes and containers and stops at the first machine,
    because a material edge never runs machine to machine directly.
    """
    links: Counter[str] = Counter()
    adjacency = graph.adjacency("material")
    seen: set[str] = set(wanted)
    frontier = [machine for machine in wanted if machine in adjacency]
    while frontier:
        next_frontier: list[str] = []
        for node in frontier:
            for edge in adjacency.get(node, ()):
                other = edge.other(node)
                if other in seen:
                    continue
                seen.add(other)
                if graph.is_machine(other):
                    label = labels.label_for(other) if labels else None
                    links[label.name if label else "(unlabelled)"] += 1
                else:
                    next_frontier.append(other)
        frontier = next_frontier
    return links


def build_view(
    name: str,
    machines: list[str],
    graph: FactoryGraph,
    game: GameData,
    projection: Projection,
    labels: LabelStore | None = None,
) -> FactoryView:
    """Compute every aspect of one machine set in a single pass over the projection."""
    wanted = set(machines)
    view = FactoryView(name=name)
    flows: Flows = defaultdict(_empty_flow)
    points: list[tuple[float, float]] = []
    _add_manufacturers(view, flows, points, _members(projection, "machines", wanted), game)
    extractors = _members(projection, "extractors", wanted)
    _add_extractors(view, flows, points, extractors, game, projection)
    _add_generators(view, flows, points, _members(projection, "generators", wanted), game)
    view.flows = dict(flows)
    middle = geo.centroid(points)
    if middle is not None:
        view.centroid = middle
        view.spread_m = geo.diameter_m(points)
    view.links = _boundary_links(graph, wanted, labels)
    return view

"""Answer questions about a named factory, or about any machine set.

One entry point rather than eight tools, because every question shares the same two
steps: resolve a set of machines, then read something off it. What differs is only the
projection taken.

Every rate is carried twice, nameplate at the saved clock and measured over the productivity
window that closed when the save was written, and the two are never blended: a Foundry on
Solid Steel Ingot at 150% is nameplate 1.5x its recipe rate whether or not it has ever had
iron, and the pair is what says which. The safe direction is **opposite on the two sides** --
an unreadable machine charged in full makes a power figure conservative and an output figure
optimistic -- so a machine with no monitor contributes nothing to the measured flows and its
nameplate rate is parked in ``unmonitored_*``, leaving measured production a floor by
construction.

The one derived view worth more than the rest is ``balance``: per-item production minus
consumption across the set. Its sign is the interesting part --

* **positive** -- surplus, so it leaves the factory or backs up
* **negative** -- has to be fed in from outside
* **zero** -- made and consumed internally, which is what a self-contained line looks like

That single table answers "what does the steel factory need and what does it give me",
which no per-machine listing does.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..power.report import measured_share
from ..spatial import geo
from ..spatial import nodes as nodes_mod
from .model import FactoryGraph

__all__ = ["ASPECTS", "FactoryView", "build_view"]

#: What can be asked for. Kept explicit so an unknown aspect is an error with a list
#: rather than a silently empty answer.
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


@dataclass
class MachineRow:
    instance: str
    building: str
    recipe: str
    clock: float
    paused: bool
    pos: tuple[float, float, float]
    #: Nameplate items/min this machine makes and uses, by item name; empty when paused.
    makes: dict[str, float] = field(default_factory=dict)
    uses: dict[str, float] = field(default_factory=dict)


@dataclass
class FactoryView:
    """Everything derivable from one machine set, computed once."""

    name: str
    machines: list[MachineRow] = field(default_factory=list)
    recipes: Counter = field(default_factory=Counter)
    buildings: Counter = field(default_factory=Counter)
    #: item -> six items/min figures, ``produced`` and ``consumed`` each in three flavours:
    #: bare (nameplate), ``measured_`` (monitored machines, weighted by their own window)
    #: and ``unmonitored_`` (nameplate rate of machines carrying no monitor at all). The
    #: three sum: measured + unmonitored <= nameplate, with equality when nothing is idle.
    flows: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Machines that contributed any flow, and how many of those keep no productivity
    #: monitor. The second is what makes the measured column readable: a factory where it
    #: equals the first has no measured production, which is not the same as none.
    producers: int = 0
    unmonitored_producers: int = 0
    #: Machines mid-production at the instant the save was written. The window figure is an
    #: average over five minutes and this is the snapshot; on a factory that has just
    #: stopped they disagree, and the disagreement is the finding.
    producing_now: int = 0
    draw_mw: float = 0.0
    #: Draw weighted per machine by its own 300 s productivity monitor -- the only measured
    #: number here. A machine carrying no monitor is charged in FULL, so an unreadable one
    #: can only make this figure conservative; ``unmonitored`` says how many those are.
    measured_draw_mw: float = 0.0
    unmonitored: int = 0
    generation_mw: float = 0.0
    #: (node instance, resource, purity, extractor class, clock, resources_left)
    nodes: list[tuple] = field(default_factory=list)
    #: other factory/label name -> how many of ITS machines this set can reach without
    #: passing through a third machine. Asymmetric on purpose: from a 15-machine copper
    #: setup you reach 16 tor-factory machines on the shared belt web, but walking back
    #: from the tor factory the first copper machine blocks the rest.
    links: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)
    centroid: tuple[float, float] = (0.0, 0.0)
    spread_m: float = 0.0

    @property
    def size(self) -> int:
        return len(self.machines)

    def net(self, item: str) -> float:
        f = self.flows.get(item, {})
        return f.get("produced", 0.0) - f.get("consumed", 0.0)

    def measured_net(self, item: str) -> float:
        f = self.flows.get(item, {})
        return f.get("measured_produced", 0.0) - f.get("measured_consumed", 0.0)

    def measurable(self, item: str, side: str) -> bool:
        """Whether anything readable made (``produced``) or used (``consumed``) this item.

        False means the measured figure for it is UNKNOWN and not zero -- every machine
        touching it that way keeps no monitor. Printing a 0.00 there would report a factory
        the save cannot see as a factory that has stopped.
        """
        f = self.flows.get(item, {})
        return f.get(side, 0.0) - f.get(f"unmonitored_{side}", 0.0) > 1e-9

    def outputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items with a surplus: they leave, or they back up."""
        out = [(k, self.net(k)) for k in self.flows]
        return sorted([(k, v) for k, v in out if v > tol], key=lambda kv: -kv[1])

    def inputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items in deficit: they must be fed in from outside."""
        out = [(k, -self.net(k)) for k in self.flows]
        return sorted([(k, v) for k, v in out if v > tol], key=lambda kv: -kv[1])

    def internal(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Made and consumed within the set -- the mark of a self-contained line."""
        out = []
        for k, f in self.flows.items():
            if abs(self.net(k)) <= tol and f.get("produced", 0.0) > tol:
                out.append((k, f["produced"]))
        return sorted(out, key=lambda kv: -kv[1])


def _short(instance: str) -> str:
    return instance.rsplit(".", 1)[-1]


def build_view(
    name: str,
    machines: list[str],
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    labels=None,
) -> FactoryView:
    """Compute every aspect of one machine set in a single pass over the projection."""
    wanted = set(machines)
    view = FactoryView(name=name)
    flows: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "produced": 0.0,
            "consumed": 0.0,
            "measured_produced": 0.0,
            "measured_consumed": 0.0,
            "unmonitored_produced": 0.0,
            "unmonitored_consumed": 0.0,
        }
    )

    # resources_left is all the SAVE knows about a node. Resource and purity come from
    # the node table, which is why an unresolved extractor reports "?" rather than
    # silently defaulting to normal purity and inflating its extraction rate.
    node_state = projection.get("node_state", {})
    try:
        node_table = nodes_mod.load_nodes().by_instance()
    except Exception:
        node_table = {}

    points: list[tuple[float, float]] = []

    def charge(rated: float, record: dict) -> None:
        """One machine's draw, on both the nameplate and the measured side."""
        view.draw_mw += rated
        share = measured_share(record)
        if share is None:
            view.unmonitored += 1
        view.measured_draw_mw += rated if share is None else rated * share

    def flow(item: str, side: str, rate: float, share: float | None) -> None:
        """One machine's contribution to one item, on both sides.

        An unmonitored machine is NOT charged in full here, which is the reverse of what
        ``charge`` above does with the same record: on this side charging it in full would
        invent throughput. Its rate goes to the unmonitored column so it can be seen.
        """
        f = flows[item]
        f[side] += rate
        if share is None:
            f[f"unmonitored_{side}"] += rate
        else:
            f[f"measured_{side}"] += rate * share

    def census(record: dict, share: float | None) -> None:
        """Count one machine that contributes flow, so the measured column can be read."""
        view.producers += 1
        if share is None:
            view.unmonitored_producers += 1
        if (record.get("uptime") or {}).get("producing"):
            view.producing_now += 1

    for record in projection.get("machines", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        paused = bool(record.get("paused"))
        rid = record.get("recipe")
        recipe = game.recipes.get(rid or "")
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe=recipe.name if recipe else "",
                clock=clock,
                paused=paused,
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        view.buildings[record.get("cls", "?")] += 1
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        if recipe is None:
            if rid is None:
                view.issues.append(f"{short}: no recipe set, produces nothing")
            continue
        view.recipes[recipe.name] += 1
        if paused:
            view.issues.append(f"{short}: paused ({recipe.name})")
            continue
        share = measured_share(record)
        census(record, share)
        row = view.machines[-1]
        for f in recipe.products:
            flow(game.item_name(f.item), "produced", f.per_min * clock, share)
            row.makes[game.item_name(f.item)] = f.per_min * clock
        for f in recipe.ingredients:
            flow(game.item_name(f.item), "consumed", f.per_min * clock, share)
            row.uses[game.item_name(f.item)] = f.per_min * clock
        if game.buildings.get(record.get("cls", "")) is None:
            # Silently contributing 0 MW would understate the whole factory's draw.
            view.issues.append(
                f"{short}: unknown building {record.get('cls')!r}, power not counted"
            )
        else:
            charge(game.recipe_power_mw(recipe, clock), record)

    for record in projection.get("extractors", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        paused = bool(record.get("paused"))
        view.buildings[record.get("cls", "?")] += 1
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe="",
                clock=clock,
                paused=paused,
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        building = game.buildings.get(record.get("cls", ""))
        node = record.get("node")
        state = node_state.get(node) or {}
        meta = node_table.get(node) or node_table.get(_short(node) if node else "") or {}
        purity = meta.get("purity") or ""
        resource = meta.get("resource") or ""
        # A Water Extractor sits on an FGWaterVolume, which is not a node and has no
        # purity. Reporting "?" for both made a working pump look broken; the building
        # class already says what it draws, so say that and name the reason instead.
        if not resource and "WaterPump" in record.get("cls", ""):
            resource, purity = "Desc_Water_C", "n/a (water volume)"
        view.nodes.append(
            (
                _short(node) if node else "(unresolved)",
                game.item_name(resource) if resource else "?",
                purity or "?",
                record.get("cls", "?"),
                clock,
                state.get("resources_left"),
            )
        )
        if paused:
            view.issues.append(f"{short}: paused extractor")
            continue
        if building is not None:
            charge(building.power_at(clock), record)
            share = measured_share(record)
            if resource and purity:
                census(record, share)
                # Water volumes have no purity multiplier: extraction is the flat rate.
                grade = "normal" if purity == "n/a (water volume)" else purity
                rate = building.extract_rate(grade, clock)
                flow(game.item_name(resource), "produced", rate, share)
                view.machines[-1].makes[game.item_name(resource)] = rate
            elif not node:
                view.issues.append(f"{short}: extractor bound to no node, output unknown")
            else:
                view.issues.append(
                    f"{short}: node {_short(node)} not in the node table, output unknown"
                )

    for record in projection.get("generators", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        view.buildings[record.get("cls", "?")] += 1
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe="",
                clock=clock,
                paused=bool(record.get("paused")),
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        if record.get("paused"):
            view.issues.append(f"{short}: paused generator")
            continue
        building = game.buildings.get(record.get("cls", ""))
        if building is None:
            continue
        view.generation_mw += building.power_production_mw * clock
        fuel = game.items.get(record.get("fuel") or "")
        if fuel is not None:
            share = measured_share(record)
            census(record, share)
            flow(fuel.name, "consumed", building.fuel_rate_per_min(fuel) * clock, share)
            view.machines[-1].uses[fuel.name] = building.fuel_rate_per_min(fuel) * clock

    view.flows = dict(flows)

    middle = geo.centroid(points)
    if middle is not None:
        view.centroid = middle
        view.spread_m = geo.diameter_m(points)

    # The factory's boundary. A material edge runs machine -> belt -> ... -> machine, so
    # walking only direct edges finds nothing; the walk has to pass THROUGH logistics and
    # stop at the first machine outside the set. Naming what sits there is what turns an
    # instance id into "feeds the steel factory".
    adjacency = graph.adjacency("material")
    seen: set[str] = set(wanted)
    frontier = [m for m in wanted if m in adjacency]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for edge in adjacency.get(node, ()):
                other = edge.other(node)
                if other in seen:
                    continue
                seen.add(other)
                if graph.is_machine(other):
                    label = labels.label_for(other) if labels else None
                    view.links[label.name if label else "(unlabelled)"] += 1
                else:
                    nxt.append(other)  # keep going through belts, pipes and containers
        frontier = nxt

    return view

"""Which nodes feed which trunk line.

A plan counts extractor lines; this says which nodes share each one. Nodes join a pipe along a
nearest-neighbour chain from the far end inward, cut where the next would overflow, and runs
and pump counts are straight-line lower bounds since there is no terrain (docs/planning.md §8.5c).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ...spatial import geo
from ..solver.model import Scenario

__all__ = ["Trunk", "TrunkPlan", "plan_trunks"]


@dataclass
class TrunkMember:
    instance: str
    x: float
    y: float
    z: float
    purity: str
    rate: float

    @property
    def short(self) -> str:
        """The node id with its boilerplate prefix off and nothing else, so it can be
        pasted back into search_resource_nodes."""
        name = self.instance
        for prefix in ("BP_ResourceNode", "BP_FrackingCore", "BP_FrackingSatellite"):
            if name.startswith(prefix):
                return prefix[3:6].lower() + name[len(prefix) :]
        return name


@dataclass
class Trunk:
    """One pipe or belt run, and the nodes tapped along it."""

    item: str
    name: str
    carrier: str
    capacity: float
    members: list[TrunkMember] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return sum(m.rate for m in self.members)

    @property
    def used(self) -> float:
        """Fraction of one line's capacity. Never above 1.0 by construction."""
        return self.rate / self.capacity if self.capacity else 0.0

    @property
    def run_m(self) -> float:
        """Straight-line length of the chain, node to node, in metres: a LOWER BOUND."""
        return sum(
            geo.distance_3d_m((a.x, a.y, a.z), (b.x, b.y, b.z))
            for a, b in zip(self.members, self.members[1:], strict=False)
        )

    def to_site_m(self, dest_cm: tuple[float, float]) -> float:
        """Straight line from the chain's last node to the site, metres: a lower bound."""
        if not self.members:
            return 0.0
        last = self.members[-1]
        return geo.distance_m((last.x, last.y), dest_cm)

    def lift_to_site_m(self, dest_z_m: float | None) -> float | None:
        """The last node's height over the site's ground, metres; positive flows downhill."""
        if not self.members or dest_z_m is None:
            return None
        return round(self.members[-1].z / 100.0 - dest_z_m, 1)

    @property
    def head_m(self) -> float:
        """Elevation span across the trunk's nodes, in metres."""
        zs = [m.z / 100.0 for m in self.members]
        return (max(zs) - min(zs)) if zs else 0.0

    def pumps(self, head_lift_m: float) -> int:
        """Pipeline pumps lifting this trunk's climb at one pump's ``mDesignPressure``; zero
        when the run falls. A lower bound: friction is not modelled (docs/planning.md §8.5c)."""
        if head_lift_m <= 0 or self.lift_m >= 0:
            return 0
        return math.ceil(-self.lift_m / head_lift_m - 1e-9)

    @property
    def lift_m(self) -> float:
        """Climb from the chain's last node to its first, in metres.

        Signed, unlike ``head_m``: the chain runs inward, so positive flows DOWNHILL to the
        plant and needs no pumping.
        """
        if not self.members:
            return 0.0
        return (self.members[0].z - self.members[-1].z) / 100.0


@dataclass
class TrunkPlan:
    trunks: list[Trunk] = field(default_factory=list)
    #: Extractors on no node -- Water Extractors -- named rather than silently dropped.
    placeless: list[tuple[str, float, int]] = field(default_factory=list)
    #: (x, y) the trunks converge on, and where it came from.
    destination: tuple[float, float] | None = None
    destination_label: str = ""
    notes: list[str] = field(default_factory=list)


def _chain(members: list[TrunkMember], start: TrunkMember) -> list[TrunkMember]:
    """Nearest-neighbour order from ``start``, greedy on purpose (docs/planning.md §8.5c)."""
    remaining = [m for m in members if m is not start]
    out = [start]
    while remaining:
        last = out[-1]
        nxt = min(remaining, key=lambda m: geo.distance_m((last.x, last.y), (m.x, m.y)))
        remaining.remove(nxt)
        out.append(nxt)
    return out


def _split(chain: list[TrunkMember], capacity: float) -> list[list[TrunkMember]]:
    """Cut the chain wherever the next node would overflow the line; a single node above
    capacity gets a run of its own rather than a silent split (docs/planning.md §8.5c)."""
    runs: list[list[TrunkMember]] = []
    current: list[TrunkMember] = []
    load = 0.0
    for member in chain:
        if current and load + member.rate > capacity + 1e-9:
            runs.append(current)
            current, load = [], 0.0
        current.append(member)
        load += member.rate
    if current:
        runs.append(current)
    return runs


def _take_cost(node_row: dict, building_id: str, centre: tuple[float, float]) -> tuple[int, float]:
    """What taking a node costs: already ours, then untapped, then held by another
    extractor; ties go to the node nearest the pool's centre (docs/planning.md §8.5d)."""
    if not node_row["tapped"]:
        rank = 1
    elif node_row.get("tapped_by") == building_id:
        rank = 0
    else:
        rank = 2
    return rank, geo.distance_m((node_row["x"], node_row["y"]), centre)


def _choose_nodes(proc: dict, pool: list[dict], notes: list[str]) -> list[dict]:
    """The nodes of ``pool`` one extractor row taps, cheapest to take first.

    The solve only says how many of a purity; which ones is chosen here, and a note says
    when the choice displaces another extractor or runs short.
    """
    wanted = int(proc["machines"])
    building_id = proc["building_id"]
    centre = geo.centroid([(r["x"], r["y"]) for r in pool])
    chosen = sorted(pool, key=lambda r: _take_cost(r, building_id, centre))[:wanted]
    displaced = [r for r in chosen if r["tapped"] and r.get("tapped_by") != building_id]
    if displaced:
        notes.append(
            f"{proc['label']}: {len(displaced)} of the chosen node(s) are held by a "
            "different extractor and must be cleared first -- no free or "
            "already-correct node was left"
        )
    if len(chosen) < wanted:
        notes.append(
            f"{proc['label']}: plan wants {wanted} but only {len(chosen)} node(s) are "
            "in scope -- the trunk bill covers what exists"
        )
    return chosen


def _trunks_for(
    game: GameData,
    scenario: Scenario,
    item: str,
    members: list[TrunkMember],
    destination: tuple[float, float] | None,
) -> list[Trunk]:
    """One item's nodes chained from the far end inward and cut into capacity-bound runs."""
    known = game.items.get(item)
    fluid = bool(known and known.is_fluid)
    capacity = scenario.pipe_m3min if fluid else scenario.belt_ipm
    # Without a named destination the plant stands in the middle of its field.
    target = destination or geo.centroid([(m.x, m.y) for m in members])
    # Start at the far end so the chain runs the way the fluid moves and lift_m is measured.
    start = max(members, key=lambda m: geo.distance_m((m.x, m.y), target))
    return [
        Trunk(
            item=item,
            name=game.item_name(item),
            carrier="pipe" if fluid else "belt",
            capacity=capacity,
            members=run,
        )
        for run in _split(_chain(members, start), capacity)
    ]


def plan_trunks(
    prepared,
    game: GameData,
    destination: tuple[float, float] | None = None,
    destination_label: str = "",
) -> TrunkPlan:
    """Assign the plan's extracted nodes to capacity-bounded trunk lines."""
    out = TrunkPlan(destination=destination, destination_label=destination_label)
    if prepared.solution is None or prepared.request is None:
        return out
    rows = [r for r in prepared.request.node_rows if r.get("kind") == "node"]

    taken: dict[str, list[TrunkMember]] = {}
    for proc in prepared.solution.processes:
        if proc["kind"] != "extractor":
            continue
        item = next((i for i, rate in proc["rates"].items() if rate > 0), None)
        if item is None:
            continue
        pool = [r for r in rows if r["resource"] == item and r["purity"] == proc["purity"]]
        if not pool:
            # Water: no node, no purity, no geometry anywhere this project can read.
            out.placeless.append((game.item_name(item), proc["rates"][item], int(proc["machines"])))
            continue
        chosen = _choose_nodes(proc, pool, out.notes)
        each = proc["rates"][item] / max(len(chosen), 1)
        for r in chosen:
            taken.setdefault(item, []).append(
                TrunkMember(
                    instance=instance_leaf(r["instance"]),
                    x=r["x"],
                    y=r["y"],
                    z=r.get("z", 0.0),
                    purity=r["purity"],
                    rate=each,
                )
            )

    for item, members in sorted(taken.items(), key=lambda kv: -sum(m.rate for m in kv[1])):
        out.trunks.extend(_trunks_for(game, prepared.request.scenario, item, members, destination))
    return out

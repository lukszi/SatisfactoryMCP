"""Which nodes feed which trunk line.

A solved plan says "13 Oil Extractors at 250% on Crude Oil, 3,450 m3/min". A player
standing on the Spire Coast needs the next sentence: a Mk2 pipe carries 600 m3/min, so
that is **six trunks**, and *these* nodes go on each one.

Nothing else in this project answers that. `logistics` counts lines
(``ceil(rate / capacity)``) which is the right total and says nothing about which nodes
share one, and the layout schematic starts at the factory edge with the crude already
arrived. The gap between them is the walk a player actually has to plan.

Two things this deliberately does not do
----------------------------------------
**No routing.** There is no terrain here, so a path is invented the moment it is drawn.
What comes out is a grouping plus straight-line distances, which is a lower bound on pipe
and is labelled as one.

**No routing, but pump counts are now real.** For a long time this said head-per-pump was
a game constant with no source, and refused to give a number. It is ``mDesignPressure`` in
Docs.json -- 20 m on a Mk1 pump, 50 m on a Mk2 -- and was there the whole time under a name
nobody grepped for. Counts are still a LOWER bound, because pipe friction and the head a
full pipe holds on its own are not modelled, and they quote the best pump the player has
actually unlocked rather than the best that exists.

Why a chain and not a cluster
-----------------------------
A trunk is a *line*, not a blob: pipes are laid end to end and each node joins the one
running past it. So nodes are ordered along a nearest-neighbour chain from the node
furthest from the destination inward, and the chain is cut whenever the next node would
overflow the pipe. Capacitated k-means would give tighter blobs and a worse answer -- two
nodes 40 m apart but on opposite sides of the run are not on the same pipe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ...spatial import geo

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
        """The node id with its boilerplate prefix off, and nothing else removed.

        Truncating from the right instead turned BP_ResourceNode621 into "ode621" and
        BP_FrackingCore985647 into "985647" -- unrecognisable, and no longer something
        a player can paste back into search_resource_nodes.
        """
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
        """Straight-line length of the chain, node to node, in metres.

        A LOWER BOUND on pipe: there is no terrain here, so any real route is longer.
        """
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
        """Pipeline pumps needed to lift this trunk's climb, at a given pump's head.

        Zero when the run falls: fluid flows downhill unaided, which is the whole reason
        `lift_m` is signed.

        This project refused to answer this for a long time, on the grounds that
        head-per-pump was a game rule with no data behind it. It is `mDesignPressure` in
        Docs.json -- 20 m on a Mk1 pump, 50 m on a Mk2 -- and was there all along under a
        name nobody grepped for. The refusal was the right instinct applied to a wrong
        fact, and the honest fix is to give the number, not to keep hedging.

        Still a LOWER bound, for a reason that has not gone away: pumps also have to
        overcome pipe friction and the head a full pipe holds on its own, neither of which
        is modelled here. It answers "at least this many", which is what sizing a build
        needs.
        """
        if head_lift_m <= 0 or self.lift_m >= 0:
            return 0
        return math.ceil(-self.lift_m / head_lift_m - 1e-9)

    @property
    def lift_m(self) -> float:
        """Climb from the chain's last node to its first, in metres.

        Signed and directional, unlike ``head_m``. The chain runs from the far end
        inward, so a positive number means the trunk flows DOWNHILL to the plant and
        needs no pumping; a negative one is the climb that does.
        """
        if not self.members:
            return 0.0
        return (self.members[0].z - self.members[-1].z) / 100.0


@dataclass
class TrunkPlan:
    trunks: list[Trunk] = field(default_factory=list)
    #: Extractors the plan uses that sit on no node -- Water Extractors. Reported by
    #: name rather than dropped, because "why is my 9,200 m3/min of water missing"
    #: is otherwise a silent hole in the answer.
    placeless: list[tuple[str, float, int]] = field(default_factory=list)
    #: (x, y) the trunks converge on, and where it came from.
    destination: tuple[float, float] | None = None
    destination_label: str = ""
    notes: list[str] = field(default_factory=list)


def _chain(members: list[TrunkMember], start: TrunkMember) -> list[TrunkMember]:
    """Nearest-neighbour order from ``start``. Greedy on purpose.

    An optimal path here is a travelling-salesman problem, and the difference between
    greedy and optimal is dwarfed by the terrain this model cannot see -- a cliff in the
    way costs more than a suboptimal join order ever does.
    """
    remaining = [m for m in members if m is not start]
    out = [start]
    while remaining:
        last = out[-1]
        nxt = min(remaining, key=lambda m: geo.distance_m((last.x, last.y), (m.x, m.y)))
        remaining.remove(nxt)
        out.append(nxt)
    return out


def _split(chain: list[TrunkMember], capacity: float) -> list[list[TrunkMember]]:
    """Cut the chain wherever the next node would overflow the line.

    A single node above capacity gets a run of its own rather than being dropped or
    silently splitting: a pure Crude Oil node at 250% makes exactly 600 m3/min, and one
    over that is a real situation the player has to solve with a second pipe off the same
    extractor -- which is their problem to see, not ours to hide.
    """
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
    sc = prepared.request.scenario
    rows = [r for r in prepared.request.node_rows if r.get("kind") == "node"]

    # Nodes the plan actually taps, by (resource, purity). The solve reports extractors
    # aggregated -- "7 Oil Extractors on impure Crude Oil" -- so which SEVEN of the
    # impure nodes is a choice this makes, not one the LP made.
    taken: dict[str, list[TrunkMember]] = {}
    for proc in prepared.solution.processes:
        if proc["kind"] != "extractor":
            continue
        item = next((i for i, rate in proc["rates"].items() if rate > 0), None)
        if item is None:
            continue
        wanted = int(proc["machines"])
        pool = [r for r in rows if r["resource"] == item and r["purity"] == proc["purity"]]
        if not pool:
            # Water: no node, no purity, no geometry anywhere this project can read.
            out.placeless.append((game.item_name(item), proc["rates"][item], wanted))
            continue
        # Least work first, then tightest cluster. Extra nodes of a purity exist
        # precisely when the plan does not need all of them, so the choice is free and
        # worth making well.
        #
        # The ranking is by what the node COSTS to take, which is not the same as
        # "prefer untapped". A node already carrying the extractor this plan wants is
        # the cheapest of all -- nothing to build and nothing to remove, and
        # diff_vs_save will match it as standing. Untapped is next: build one. A node
        # held by the WRONG extractor is last, because taking it means demolishing
        # something that is currently running.
        #
        # On the reference save every Spire Coast crude node is tapped, all of them by
        # the Oil Pump this plan wants, so a plain free-first rule would have ranked all
        # thirteen equal-worst and picked on geometry alone.
        cx, cy = geo.centroid([(r["x"], r["y"]) for r in pool])

        def _cost(r: dict, want=proc["building_id"], cx=cx, cy=cy) -> tuple[int, float]:
            if not r["tapped"]:
                rank = 1
            elif r.get("tapped_by") == want:
                rank = 0
            else:
                rank = 2
            return rank, geo.distance_m((r["x"], r["y"]), (cx, cy))

        pool.sort(key=_cost)
        chosen = pool[:wanted]
        displaced = [r for r in chosen if r["tapped"] and r.get("tapped_by") != proc["building_id"]]
        if displaced:
            out.notes.append(
                f"{proc['label']}: {len(displaced)} of the chosen node(s) are held by a "
                "different extractor and must be cleared first -- no free or "
                "already-correct node was left"
            )
        if len(chosen) < wanted:
            out.notes.append(
                f"{proc['label']}: plan wants {wanted} but only {len(chosen)} node(s) are "
                "in scope -- the trunk bill covers what exists"
            )
        each = proc["rates"][item] / max(len(chosen), 1)
        for r in chosen:
            taken.setdefault(item, []).append(
                TrunkMember(
                    instance=r["instance"].rsplit(".", 1)[-1],
                    x=r["x"],
                    y=r["y"],
                    z=r.get("z", 0.0),
                    purity=r["purity"],
                    rate=each,
                )
            )

    for item, members in sorted(taken.items(), key=lambda kv: -sum(m.rate for m in kv[1])):
        it = game.items.get(item)
        fluid = bool(it and it.is_fluid)
        capacity = sc.pipe_m3min if fluid else sc.belt_ipm
        # Absent a named destination, the node set's own centroid: the plant goes in the
        # middle of its field unless the player says otherwise.
        target = destination or geo.centroid([(m.x, m.y) for m in members])
        # Start at the far end so the chain runs INWARD, which is the direction the
        # fluid moves and the direction lift_m is measured in.
        start = max(members, key=lambda m: geo.distance_m((m.x, m.y), target))
        for run in _split(_chain(members, start), capacity):
            out.trunks.append(
                Trunk(
                    item=item,
                    name=game.item_name(item),
                    carrier="pipe" if fluid else "belt",
                    capacity=capacity,
                    members=run,
                )
            )
    return out

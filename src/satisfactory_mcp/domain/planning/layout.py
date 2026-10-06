"""Turn a solved plan into a buildable schematic: blocks, buses and floors.

This is a SCHEMATIC, not a blueprint. It answers "what modules do I build, what feeds
what, how much space, and what goes on which floor". It deliberately does not produce
world coordinates: there is no terrain heightmap in any data available here, so belt
pathfinding and foundation alignment would be invention rather than derivation.

Three ideas do the work:

**Blocks come from throughput, not taste.** 46 Refineries consuming 1380 m3/min of
crude cannot sit on one manifold when a Mk2 pipe carries 600 -- that is 3 lines, so it
is 3 blocks of ~16. Line count *is* block count, which makes the split derived rather
than arbitrary.

**Connections are buses, not pairings.** The LP gives net balances, not who feeds whom.
Recovering specific producer-consumer pairs is a min-cost flow problem with no unique
answer absent geometry, so each item gets one bus that producers feed and consumers
draw from. That is also what a manifold physically is.

**Floors come from chain depth.** Stage = longest path through the item graph, so
extractors land on the bottom floor and generators on top, with a logistics deck
between each pair. Depth is computed on the graph's CONDENSATION, because the recipe
graph genuinely contains cycles -- Recycled Plastic and Recycled Rubber consume each
other's output. Collapsing each strongly connected component makes the graph acyclic
and puts cycle members on one floor, which is also right physically: they have to be
built together.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ...core.gamedata.footprint import FOUNDATION_M, Packed
from ...core.gamedata.model import GameData
from .solver.carrier import carrier_for
from .solver.model import MW, Solution

__all__ = [
    "LOGISTICS_FLOOR_M",
    "Block",
    "Bus",
    "Floor",
    "Layout",
    "build_layout",
    "chain_depth",
    "fluid_head",
]

#: Height reserved for a logistics deck: belts, pipes and a walkway between them.
LOGISTICS_FLOOR_M = 4.0

#: Vertical headroom above the tallest machine on a production floor.
FLOOR_HEADROOM_M = 1.0

#: Foundations are 1, 2 or 4 m thick; floor heights round up to this.
FLOOR_STEP_M = 2.0

#: A manifold longer than this stops being sensible to build or feed evenly.
MAX_MACHINES_PER_BLOCK = 24


@dataclass
class Block:
    """One buildable module: a row of identical machines on one manifold."""

    key: str
    label: str
    building_id: str
    building: str
    recipe: str | None
    machines: int
    clock: float
    part: int  # 1-based index within a split group
    parts: int  # how many blocks the process was split into
    inputs: dict[str, float] = field(default_factory=dict)
    outputs: dict[str, float] = field(default_factory=dict)
    stage: int = 0
    #: Per-MACHINE dimensions, which is what the build table prints as "each(m)".
    width_m: float = 0.0
    depth_m: float = 0.0
    height_m: float = 0.0
    #: The whole block laid out on foundations. The single source of "how much floor do
    #: N of these need" -- see Footprint.pack. None only when the building has no
    #: clearance data at all, which `build_layout` reports rather than treating as free.
    packed: Packed | None = None

    @property
    def foundations(self) -> int:
        return self.packed.foundations if self.packed else 0

    @property
    def block_width_m(self) -> float:
        return self.packed.width_m if self.packed else 0.0

    @property
    def block_depth_m(self) -> float:
        return self.packed.depth_m if self.packed else 0.0

    @property
    def name(self) -> str:
        return f"{self.label} ({self.part}/{self.parts})" if self.parts > 1 else self.label


@dataclass
class Bus:
    """All movement of one item, pooled. Producers feed it, consumers draw from it."""

    item: str
    name: str
    rate: float
    carrier: str  # belt | pipe
    unit: str
    lines: int
    producers: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)
    from_stage: int = 0
    to_stage: int = 0
    external: bool = False  # enters or leaves the site


@dataclass
class Floor:
    index: int
    kind: str  # production | logistics
    stage: int | None
    height_m: float
    blocks: list[Block] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    #: Which declared site this floor belongs to. Empty outside a site partition; set by
    #: ``layout_service`` when floors are stacked per site, so a reader can tell three
    #: separate buildings from one tower.
    site: str = ""

    @property
    def foundations(self) -> int:
        return sum(b.foundations for b in self.blocks)

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)


@dataclass
class Layout:
    blocks: list[Block]
    buses: list[Bus]
    floors: list[Floor]
    warnings: list[str] = field(default_factory=list)

    @property
    def foundations(self) -> int:
        """Peak footprint: floors stack, so the site is sized by its largest floor."""
        return max((f.foundations for f in self.floors), default=0)

    @property
    def total_foundations(self) -> int:
        return sum(f.foundations for f in self.floors)

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)

    @property
    def height_m(self) -> float:
        return sum(f.height_m for f in self.floors)

    def site_side_m(self) -> float:
        """Side of a square site that fits the largest floor."""
        return math.ceil(math.sqrt(max(self.foundations, 1))) * FOUNDATION_M


def _split_process(game: GameData, proc: dict, belt_ipm: float, pipe_m3min: float) -> int:
    """How many parallel manifolds this process needs.

    The binding item wins: if crude needs 3 pipes and the output needs 1 belt, the
    block is still 3 blocks, because one manifold cannot be fed by three pipes.
    """
    needed = 1
    for item, rate in proc.get("rates", {}).items():
        if item == MW:
            continue
        line = carrier_for(game, item, belt_ipm, pipe_m3min)
        needed = max(needed, line.lines_for(abs(rate)))
    # A manifold also stops being practical past a certain length.
    needed = max(needed, math.ceil(proc["machines"] / MAX_MACHINES_PER_BLOCK))
    return max(1, min(needed, proc["machines"]))


def _blocks_from(game: GameData, sol: Solution, belt_ipm: float, pipe_m3min: float) -> list[Block]:
    blocks: list[Block] = []
    for proc in sol.processes:
        parts = _split_process(game, proc, belt_ipm, pipe_m3min)
        machines = proc["machines"]
        building = game.buildings.get(proc["building_id"] or "")
        fp = building.footprint if building else None

        # Spread machines as evenly as possible; remainder goes to the first blocks.
        base, extra = divmod(machines, parts)
        for part in range(parts):
            n = base + (1 if part < extra else 0)
            if n <= 0:
                continue
            share = (n / machines) if machines else 0.0
            inputs: dict[str, float] = {}
            outputs: dict[str, float] = {}
            # Straight from the solve: these already include clock and boost, and
            # they cover extractors and generators, which have no recipe at all.
            for item, rate in proc.get("rates", {}).items():
                if item == MW:
                    continue
                (outputs if rate > 0 else inputs)[item] = abs(rate) * share
            blocks.append(
                Block(
                    key=f"{proc['pid']}#{part + 1}",
                    label=proc["label"],
                    building_id=proc["building_id"] or "",
                    building=proc["building"],
                    recipe=proc.get("recipe"),
                    machines=n,
                    clock=proc["clock"],
                    part=part + 1,
                    parts=parts,
                    inputs=inputs,
                    outputs=outputs,
                    width_m=fp.width_m if fp else 0.0,
                    depth_m=fp.depth_m if fp else 0.0,
                    height_m=fp.height_m if fp else 0.0,
                    # ONE sizing primitive, shared with everything else that asks how
                    # much floor N machines need. This used to be `fp.foundations * n`,
                    # which the footprint's own docstring warns is an upper bound: it
                    # ignores shared edges, so two 20 m machines side by side were
                    # charged 6 tiles where they span 40 m and need 5. Across a plan that
                    # was about a third too much concrete, and the water-siting note had
                    # independently grown its own copy of the same wrong arithmetic.
                    packed=fp.pack(n) if fp else None,
                )
            )
    return blocks


def _strongly_connected(n: int, edges: dict[int, set[int]]) -> list[list[int]]:
    """Tarjan's SCC, iterative so a deep chain cannot blow the recursion limit."""
    index = [None] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0

    for root in range(n):
        if index[root] is not None:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True

        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if index[nxt] is None:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if on_stack[nxt]:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
    return result


def chain_depth(nodes: Sequence[tuple[Iterable[str], Iterable[str]]]) -> list[int]:
    """Chain depth per node, computed on the condensation of the item graph.

    Each node is ``(inputs, outputs)`` as item ids; the result is its longest-path
    depth, so pure consumers of raw material sit at 0 and terminal consumers sit
    highest. Shared by the layout (floors) and the diff (build stages), because
    "what has to exist before this can run" is one question, not two.

    A plain longest-path walk is not available: the recipe graph genuinely contains
    cycles, because Recycled Plastic and Recycled Rubber each consume the other's
    output. Naive relaxation does not settle on a cycle either -- it lifts every
    member by one stage per pass until the iteration cap, so depth ends up reporting
    how long the loop ran rather than how deep the chain is.

    Collapsing each strongly connected component to a single node fixes both: the
    condensation is acyclic by construction, and every member of a cycle shares a
    depth, which is also right physically since they must be built together.
    """
    producers: dict[str, list[int]] = {}
    for i, (_ins, outs) in enumerate(nodes):
        for item in outs:
            producers.setdefault(item, []).append(i)

    edges: dict[int, set[int]] = {}
    for i, (ins, _outs) in enumerate(nodes):
        for item in ins:
            for src in producers.get(item, ()):
                if src != i:
                    edges.setdefault(src, set()).add(i)

    components = _strongly_connected(len(nodes), edges)
    component_of = {}
    for cid, members in enumerate(components):
        for member in members:
            component_of[member] = cid

    # Longest path over the condensation, which is a DAG.
    condensed: dict[int, set[int]] = {}
    for src, dsts in edges.items():
        for dst in dsts:
            a, b_ = component_of[src], component_of[dst]
            if a != b_:
                condensed.setdefault(a, set()).add(b_)

    depth = [0] * len(components)
    for _ in range(len(components)):
        changed = False
        for a, dsts in condensed.items():
            for b_ in dsts:
                if depth[b_] < depth[a] + 1:
                    depth[b_] = depth[a] + 1
                    changed = True
        if not changed:
            break

    return [depth[component_of[i]] for i in range(len(nodes))]


def _assign_stages(blocks: list[Block]) -> None:
    for stage, b in zip(chain_depth([(b.inputs, b.outputs) for b in blocks]), blocks):
        b.stage = stage


def _buses(
    game: GameData,
    blocks: list[Block],
    sol: Solution,
    belt_ipm: float,
    pipe_m3min: float,
) -> list[Bus]:
    items: set[str] = set()
    for b in blocks:
        items |= set(b.inputs) | set(b.outputs)

    buses: list[Bus] = []
    for item in sorted(items):
        if item == MW:
            continue
        produced = sum(b.outputs.get(item, 0.0) for b in blocks)
        consumed = sum(b.inputs.get(item, 0.0) for b in blocks)
        rate = max(produced, consumed)
        if rate <= 1e-6:
            continue
        line = carrier_for(game, item, belt_ipm, pipe_m3min)
        src = [b for b in blocks if b.outputs.get(item, 0.0) > 1e-6]
        dst = [b for b in blocks if b.inputs.get(item, 0.0) > 1e-6]
        it = game.items.get(item)
        buses.append(
            Bus(
                item=item,
                name=it.name if it else item,
                rate=rate,
                carrier=line.kind,
                unit=line.unit,
                lines=line.lines_for(rate),
                producers=[b.key for b in src],
                consumers=[b.key for b in dst],
                from_stage=min((b.stage for b in src), default=0),
                # With no consumer on site the item leaves at the level it is made,
                # so the destination is its own stage -- not 0, which would render
                # as flowing backwards down the stack.
                to_stage=max(
                    (b.stage for b in dst),
                    default=min((b.stage for b in src), default=0),
                ),
                # Leaves or enters the site: exported, sunk, or drawn from raw supply.
                external=item in sol.exports or item in sol.sunk or not src or not dst,
            )
        )
    buses.sort(key=lambda b: -b.rate)
    return buses


def fluid_head(layout: Layout, pump_head_m: float = 0.0) -> list[dict]:
    """Which fluids the floor assignment makes climb, and by how many storeys.

    Floors follow CHAIN DEPTH, which is a correctness property -- a consumer sits above
    its producer, so the schematic reads in build order. It is not a physics property.
    Fluids do not care about chain depth: a pipe running downhill is free while one
    running uphill needs head, and water in particular can only be drawn at sea level, so
    it always starts at the bottom whatever the chain says.

    Chain-depth ordering therefore tends to make everything climb. On a measured oil plan
    it put extractors at F0, refineries F2, blenders F4, generators F6 -- water up two
    storeys, crude, heavy oil residue and fuel up one each. Reordering by hand so the
    water extractors sit at sea level under the blenders, generators one above and
    refineries on top leaves only water and fuel climbing one storey each, and lets
    residue and crude fall for free.

    Reporting this was once the whole answer, on the grounds that the right stack depends
    on terrain and on how much the player will pump. `order_floors_by="head"` now searches
    the orders too -- naming a cost and then defaulting to the arrangement that pays it
    was the gap: on the measured oil plan chain order lifts 66 pipe-storeys where 52 is
    available, and water climbs four floors when two will do.

    ``pumps`` is per riser and real: metres come from the actual floors crossed, and head
    per pump from ``mDesignPressure``. It is a LOWER bound for the same reason the trunk
    figure is -- pipe friction and the head a full pipe holds are not modelled.
    """
    floor_of: dict[int, int] = {}
    site_of: dict[int, str] = {}
    for floor in layout.floors:
        if floor.stage is not None:
            floor_of[floor.stage] = floor.index
            site_of[floor.stage] = floor.site
    height_of = {floor.index: floor.height_m for floor in layout.floors}

    out: list[dict] = []
    for bus in layout.buses:
        if bus.carrier != "pipe" or bus.external:
            continue
        start, end = floor_of.get(bus.from_stage), floor_of.get(bus.to_stage)
        if start is None or end is None or start == end:
            continue
        # Every floor strictly between the two, so the climb is the real stack height
        # crossed rather than storeys times an assumed storey.
        lo, hi = sorted((start, end))
        metres = sum(h for i, h in height_of.items() if lo <= i < hi)
        per_line = math.ceil(metres / pump_head_m - 1e-9) if pump_head_m > 0 and end > start else 0
        out.append(
            {
                "item": bus.name,
                "rate": bus.rate,
                "unit": bus.unit,
                "floors": end - start,
                "lines": bus.lines,
                "metres": metres,
                "pumps_per_line": per_line,
                "pumps": per_line * bus.lines,
                "direction": "climbs" if end > start else "falls",
                # Under a site partition every bus is within ONE site (a cross-site flow
                # is external to both), so the riser can be named to its building.
                "site": site_of.get(bus.from_stage, ""),
            }
        )
    out.sort(key=lambda d: (-d["floors"], -d["rate"]))
    return out


def _decks_for(blocks: list[Block], cap: int) -> list[list[Block]]:
    """Split one chain stage across as many decks as a foundation cap allows.

    The uncapped layout answers "how big a site does this need" by giving each stage a
    deck of whatever size it wants -- 504x504 m on a measured oil plan. The question a
    player with a finished platform actually has is the reverse: *I have 30x30
    foundations, how many decks?* Same computation, run backwards.

    Blocks keep their order, so a deck still reads in build order, and a block larger
    than the cap gets a deck to itself rather than being silently dropped -- the caller
    is told instead.
    """
    if cap <= 0:
        return [blocks]
    decks: list[list[Block]] = []
    current: list[Block] = []
    used = 0
    for block in blocks:
        need = block.foundations
        if current and used + need > cap:
            decks.append(current)
            current, used = [], 0
        current.append(block)
        used += need
    if current:
        decks.append(current)
    return decks


#: Above this many production stages, the exact head ordering is not searched. 8! is
#: 40,320 permutations and instant; 12! is half a billion and is not. Measured plans run
#: to four or five stages, so the cap has never bitten -- it exists so that a pathological
#: plan degrades to the chain order with a note rather than hanging.
MAX_ORDERED_STAGES = 8


def _lift_cost(order: list[int], blocks: list[Block], buses: list[Bus]) -> float:
    """Pipe-storeys climbed under a given bottom-to-top stage order.

    Weighted by LINE COUNT rather than by raw rate, because the thing being paid for is
    pumps and a pump serves one pipe: 10,300 m3/min of water is 18 pipes, and lifting it
    one storey costs eighteen risers to pump, not "10,300 units of badness". Rate and
    lines are near-proportional, so this rarely changes the winner -- it changes what the
    number MEANS, and the number is quoted.

    Only the upward leg counts. A pipe running downhill is free, which is the whole reason
    reordering helps, and only pipes count at all: a belt does not care which way it runs.
    """
    at = {stage: position for position, stage in enumerate(order)}
    cost = 0.0
    for bus in buses:
        if bus.carrier != "pipe" or bus.external:
            continue
        start, end = at.get(bus.from_stage), at.get(bus.to_stage)
        if start is None or end is None:
            continue
        cost += bus.lines * max(0, end - start)
    return cost


def _water_stages(blocks: list[Block]) -> set[int]:
    """Stages holding a Water Extractor.

    Pinned to the bottom whatever the search prefers: water is the one fluid that cannot
    be drawn anywhere but sea level, so a stack that lifts water to reach it is not a
    build. Everything else is free to move.
    """
    return {b.stage for b in blocks if b.building_id == "Build_WaterPump_C"}


def order_stages_by_head(blocks: list[Block], buses: list[Bus]) -> tuple[list[int], list[str]]:
    """Bottom-to-top stage order that minimises fluid lift.

    Chain depth is a CORRECTNESS property -- a consumer above its producer reads in build
    order -- and it is not a physics property. Fluids do not care about chain depth: a pipe
    running downhill is free and one running uphill needs pumps. `fluid_head` has always
    said so and then ordered by chain depth anyway, which on the measured oil plan lifted
    water two storeys when one was available.

    Floors may be reordered freely because a pipe or belt runs in either direction. The
    only fixed point is water at the bottom.
    """
    from itertools import permutations

    stages = sorted({b.stage for b in blocks})
    notes: list[str] = []
    if len(stages) > MAX_ORDERED_STAGES:
        notes.append(
            f"{len(stages)} stages is past the {MAX_ORDERED_STAGES}-stage search limit, so "
            "floors keep chain order; the head figures below are still measured"
        )
        return stages, notes

    pinned = _water_stages(blocks)
    best, best_cost = stages, _lift_cost(stages, blocks, buses)
    for candidate in permutations(stages):
        order = list(candidate)
        # Water first, or not at all. Sorted so a tie is deterministic rather than
        # whichever permutation the iterator happened to reach first.
        if pinned and set(order[: len(pinned)]) != pinned:
            continue
        cost = _lift_cost(order, blocks, buses)
        if cost < best_cost - 1e-9:
            best, best_cost = order, cost
    if best != stages:
        notes.append(
            "floors are ordered to MINIMISE FLUID LIFT, not by chain depth, so a block may "
            "sit below something it feeds -- pipes run both ways and only the upward leg "
            "costs pumps"
        )
    if pinned:
        notes.append(
            "Water Extractors are pinned to the bottom deck: water is the one fluid that "
            "cannot be drawn anywhere but sea level"
        )
    return best, notes


def _pump_total(floors: list[Floor], buses: list[Bus], pump_head_m: float = 50.0) -> int:
    """Pumps a floor arrangement needs, for comparing two candidate stacks.

    The default head is the Mk2 pump, which is what the search assumes when the caller has
    not said. Which tier is actually available changes the count but almost never the
    ranking, since it scales every riser together.
    """
    stub = Layout(blocks=[], buses=buses, floors=floors)
    return sum(row["pumps"] for row in fluid_head(stub, pump_head_m))


def _floors(
    blocks: list[Block],
    buses: list[Bus],
    max_floor_foundations: int = 0,
    stage_order: list[int] | None = None,
) -> list[Floor]:
    stages = list(stage_order) if stage_order else sorted({b.stage for b in blocks})
    at = {stage: position for position, stage in enumerate(stages)}
    floors: list[Floor] = []
    index = 0
    for position, stage in enumerate(stages):
        on_stage = [b for b in blocks if b.stage == stage]
        for deck in _decks_for(on_stage, max_floor_foundations):
            index = _emit_deck(floors, index, stage, deck)
        if position < len(stages) - 1:
            # By POSITION in the stack, not by stage number. The two are the same under
            # chain order and diverge the moment floors are reordered for head -- a bus
            # between stages 1 and 3 crosses this deck only if the deck sits between
            # where those stages actually ended up.
            crossing = [
                bus
                for bus in buses
                if (
                    bus.from_stage in at
                    and bus.to_stage in at
                    and min(at[bus.from_stage], at[bus.to_stage])
                    <= position
                    < max(at[bus.from_stage], at[bus.to_stage])
                )
                or (bus.external and bus.from_stage == stage)
            ]
            floors.append(
                Floor(
                    index=index,
                    kind="logistics",
                    stage=None,
                    height_m=LOGISTICS_FLOOR_M,
                    buses=crossing,
                )
            )
            index += 1
    return floors


def _emit_deck(floors: list[Floor], index: int, stage: int, on_stage: list[Block]) -> int:
    """Append one production deck, sized by its tallest machine. Returns the next index."""
    tallest = max((b.height_m for b in on_stage), default=0.0)
    height = math.ceil((tallest + FLOOR_HEADROOM_M) / FLOOR_STEP_M) * FLOOR_STEP_M
    floors.append(
        Floor(index=index, kind="production", stage=stage, height_m=height, blocks=on_stage)
    )
    return index + 1


def build_layout(
    game: GameData,
    sol: Solution,
    belt_ipm: float = 780.0,
    pipe_m3min: float = 600.0,
    max_floor_foundations: int = 0,
    order_floors_by: str = "chain",
) -> Layout:
    """Decompose a solved plan into blocks, buses and floors.

    ``order_floors_by`` is "chain" (depth order, so the schematic reads in build order) or
    "head" (minimise fluid lift). See `order_stages_by_head`.
    """
    blocks = _blocks_from(game, sol, belt_ipm, pipe_m3min)
    _assign_stages(blocks)
    buses = _buses(game, blocks, sol, belt_ipm, pipe_m3min)

    warnings: list[str] = []
    order = None
    if (order_floors_by or "chain").strip().casefold() == "head":
        order, head_notes = order_stages_by_head(blocks, buses)
        warnings.extend(head_notes)
        # The search minimises PIPE-STOREYS, which is a proxy: the real cost is pumps, and
        # pumps round up per line, so a 21% better proxy was worth only 4% of pumps on the
        # measured plan. A proxy that can be wrong in the small can be wrong in the large,
        # so both candidate stacks are built and counted, and the loser is discarded. Two
        # floor builds, against 40,320 if the search itself counted pumps.
        chain_floors = _floors(blocks, buses, max_floor_foundations, None)
        head_floors = _floors(blocks, buses, max_floor_foundations, order)
        best_head = min(
            (chain_floors, None), (head_floors, order), key=lambda pair: _pump_total(pair[0], buses)
        )
        if best_head[1] is None:
            warnings.append(
                "chain order needs no more pumps than the head-ordered stack here, so the "
                "floors are left in build order -- reordering has to earn it"
            )
        order = best_head[1]
    floors = _floors(blocks, buses, max_floor_foundations, order)

    missing = sorted({b.building for b in blocks if b.foundations == 0})
    if missing:
        warnings.append("no clearance data, excluded from the space budget: " + ", ".join(missing))
    split = [b for b in blocks if b.parts > 1]
    if split:
        worst = max(split, key=lambda b: b.parts)
        warnings.append(
            f"{len({b.label for b in split})} process(es) split across parallel "
            f"manifolds by throughput, up to {worst.parts}x ({worst.label})"
        )
    return Layout(blocks=blocks, buses=buses, floors=floors, warnings=warnings)

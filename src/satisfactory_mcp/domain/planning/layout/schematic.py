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

from ....core.gamedata.model import GameData
from ..solver.carrier import carrier_for
from ..solver.graph import chain_depth
from ..solver.model import MW, Solution
from .head import _pump_total, order_stages_by_head
from .model import (
    FLOOR_HEADROOM_M,
    FLOOR_STEP_M,
    LOGISTICS_FLOOR_M,
    MAX_MACHINES_PER_BLOCK,
    Block,
    Bus,
    Floor,
    Layout,
)

__all__ = ["build_layout"]


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

"""Turn a solved plan into a buildable schematic: blocks, buses and floors.

A schematic, never a blueprint: there is no terrain here, so no coordinates. Blocks come from
line counts, connections are one bus per item, and floors follow chain depth on the item
graph's condensation, or minimise fluid lift on request (docs/planning.md §8.5, §8.5m).
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
                    # The one sizing primitive, never n x foundations (docs/planning.md §8.5g).
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
    """Split one chain stage across as many decks as a foundation cap allows, in block
    order; a block larger than the cap gets a deck of its own (docs/planning.md §8.5b)."""
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
            # By POSITION in the stack, not stage number: they differ once floors are
            # reordered for head.
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
    if (order_floors_by or "chain").strip().casefold() == "head":
        order, head_notes = order_stages_by_head(blocks, buses)
        warnings.extend(head_notes)
        # The search minimises a proxy, so both stacks are built and their real pump
        # counts decide (docs/planning.md §8.5m).
        chain_floors = _floors(blocks, buses, max_floor_foundations, None)
        head_floors = _floors(blocks, buses, max_floor_foundations, order)
        floors, order = min(
            (chain_floors, None), (head_floors, order), key=lambda pair: _pump_total(pair[0], buses)
        )
        if order is None:
            warnings.append(
                "chain order needs no more pumps than the head-ordered stack here, so the "
                "floors are left in build order -- reordering has to earn it"
            )
    else:
        floors = _floors(blocks, buses, max_floor_foundations, None)

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

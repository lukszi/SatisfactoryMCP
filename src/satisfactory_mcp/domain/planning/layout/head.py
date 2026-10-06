"""Fluid head: which pipes a floor order makes climb, and the order that lifts least."""

from __future__ import annotations

import math

from .model import Block, Bus, Floor, Layout

__all__ = ["fluid_head", "order_stages_by_head"]


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

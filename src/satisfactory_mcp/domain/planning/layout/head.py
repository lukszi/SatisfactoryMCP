"""Fluid head: which pipes a floor order makes climb, and the order that lifts least.

Chain depth is a correctness property, not a physics one: only the upward leg of a pipe
costs pumps, and water is drawn at sea level only (docs/planning.md §8.5a, §8.5m).
"""

from __future__ import annotations

import math
from itertools import permutations

from typing_extensions import TypedDict

from ....core.gamedata.constants import WATER_PUMP
from .model import Block, Bus, Floor, Layout

__all__ = ["HeadRow", "fluid_head", "order_stages_by_head"]


class HeadRow(TypedDict):
    """One pipe bus that changes floor: ``direction`` is ``climbs`` or ``falls``."""

    item: str
    rate: float
    unit: str
    floors: int
    lines: int
    metres: float
    pumps_per_line: int
    pumps: int
    direction: str
    site: str


def fluid_head(layout: Layout, pump_head_m: float = 0.0) -> list[HeadRow]:
    """Each internal pipe bus that changes floor: storeys, metres crossed and the pumps a
    climb needs at ``pump_head_m`` per pump -- a lower bound, since pipe friction and the
    head a full pipe holds are not modelled (docs/planning.md §8.5m)."""
    floor_of: dict[int, int] = {}
    site_of: dict[int, str] = {}
    for floor in layout.floors:
        if floor.stage is not None:
            floor_of[floor.stage] = floor.index
            site_of[floor.stage] = floor.site
    height_of = {floor.index: floor.height_m for floor in layout.floors}

    out: list[HeadRow] = []
    for bus in layout.buses:
        if bus.carrier != "pipe" or bus.external:
            continue
        start, end = floor_of.get(bus.from_stage), floor_of.get(bus.to_stage)
        if start is None or end is None or start == end:
            continue
        # The real stack height crossed, never storeys times an assumed storey.
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
                # A cross-site flow is external to both, so a riser has one site.
                "site": site_of.get(bus.from_stage, ""),
            }
        )
    out.sort(key=lambda d: (-d["floors"], -d["rate"]))
    return out


#: Above this many production stages the exact head order is not searched: 8! is 40,320
#: permutations, 12! half a billion (docs/planning.md §8.5m).
MAX_ORDERED_STAGES = 8


def _lift_cost(order: list[int], blocks: list[Block], buses: list[Bus]) -> float:
    """Pipe-storeys climbed under a bottom-to-top stage order, weighted by line count since
    a pump serves one pipe; downhill legs and belts are free (docs/planning.md §8.5m)."""
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
    """Stages holding a Water Extractor, pinned to the bottom: water is drawn at sea level."""
    return {b.stage for b in blocks if b.building_id == WATER_PUMP}


def order_stages_by_head(blocks: list[Block], buses: list[Bus]) -> tuple[list[int], list[str]]:
    """Bottom-to-top stage order that minimises fluid lift, with water's stages pinned at the
    bottom; every other floor may move, since a pipe runs either way (docs/planning.md §8.5m)."""
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
        # Water first, or not at all; strict improvement keeps a tie deterministic.
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


def pump_total(floors: list[Floor], buses: list[Bus], pump_head_m: float = 50.0) -> int:
    """Pumps a floor arrangement needs, for comparing two candidate stacks; at the Mk2's
    head, since the tier scales every riser together and so rarely changes the ranking."""
    stub = Layout(blocks=[], buses=buses, floors=floors)
    return sum(row["pumps"] for row in fluid_head(stub, pump_head_m))

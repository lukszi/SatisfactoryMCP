"""What a plan costs to BUILD, as opposed to what it costs to run.

The whole plan and its deck, every item whether held or not, attributed to the buildings
that want it -- not ``diff``'s shortfall list. It stops at build-gun components and never
costs belts or pipes, which have no route here (docs/planning.md §8.5f).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from typing_extensions import TypedDict

from ....core.gamedata.model import GameData

__all__ = [
    "BuildingCost",
    "MachineCount",
    "MaterialLine",
    "MaterialsBill",
    "build_materials",
    "cost_of",
]

#: The 8 m x 8 m foundation the layout counts in. `build_layout` reports whole tiles of
#: this size, so this is the class its foundation totals are priced at.
FOUNDATION_ID = "Build_Foundation_8x1_01_C"


class MachineCount(TypedDict):
    """What the bill reads from a solution row: whole machines of one building class."""

    building_id: str | None
    machines: int


@dataclass
class BuildingCost:
    building_id: str
    name: str
    count: int
    #: item id -> total for ``count`` of them.
    parts: dict[str, float] = field(default_factory=dict[str, float])
    #: True when the dump carries no build recipe for this class, so the cost is
    #: genuinely unknown rather than zero.
    unpriced: bool = False

    @property
    def items(self) -> int:
        return int(sum(self.parts.values()))


@dataclass
class MaterialLine:
    item: str
    name: str
    needed: float
    held: float = 0.0
    #: Building names that want this part, largest contribution first.
    wanted_by: list[str] = field(default_factory=list[str])

    @property
    def short(self) -> float:
        return max(0.0, self.needed - self.held)

    @property
    def covered(self) -> bool:
        return self.held >= self.needed


@dataclass
class MaterialsBill:
    lines: list[MaterialLine] = field(default_factory=list[MaterialLine])
    buildings: list[BuildingCost] = field(default_factory=list[BuildingCost])
    machines: int = 0
    foundations: int = 0
    #: Building classes needed whose build cost is not in the dump. Named rather than
    #: dropped: a bill silently missing a building reads as complete.
    unpriced: list[str] = field(default_factory=list[str])
    notes: list[str] = field(default_factory=list[str])

    @property
    def affordable(self) -> bool:
        return bool(self.lines) and all(line.covered for line in self.lines)

    @property
    def shortfall(self) -> list[MaterialLine]:
        return sorted([x for x in self.lines if not x.covered], key=lambda x: -x.short)


def cost_of(game: GameData, building_id: str, count: int) -> BuildingCost:
    """What ``count`` of one building class costs to place."""
    building = game.buildings.get(building_id)
    out = BuildingCost(
        building_id=building_id,
        name=building.name if building else building_id,
        count=count,
    )
    if building is None or not building.build_cost:
        out.unpriced = True
        return out
    for flow in building.build_cost:
        out.parts[flow.item] = out.parts.get(flow.item, 0.0) + flow.amount * count
    return out


def build_materials(
    game: GameData,
    processes: Sequence[MachineCount],
    stock: dict[str, float] | None = None,
    foundations: int = 0,
) -> MaterialsBill:
    """Total what a plan costs to construct, machines and deck together.

    ``processes`` are solution rows as ``plan_factory`` prints them, so what gets charged
    is WHOLE machines -- the thing you actually place -- and never the LP's fractional
    machine-equivalents.
    """
    out = MaterialsBill(foundations=max(0, int(foundations)))
    wanted: dict[str, int] = {}
    for p in processes:
        bid = p.get("building_id")
        if not bid:
            continue
        n = int(p["machines"])
        wanted[bid] = wanted.get(bid, 0) + n
        out.machines += n
    if out.foundations:
        wanted[FOUNDATION_ID] = wanted.get(FOUNDATION_ID, 0) + out.foundations

    totals: dict[str, float] = {}
    contributors: dict[str, dict[str, float]] = {}
    for bid, count in sorted(wanted.items(), key=lambda kv: -kv[1]):
        entry = cost_of(game, bid, count)
        out.buildings.append(entry)
        if entry.unpriced:
            out.unpriced.append(entry.name)
            continue
        for item, amount in entry.parts.items():
            totals[item] = totals.get(item, 0.0) + amount
            per_item = contributors.setdefault(item, {})
            per_item[entry.name] = per_item.get(entry.name, 0.0) + amount

    held = stock or {}
    for item, amount in sorted(totals.items(), key=lambda kv: -kv[1]):
        out.lines.append(
            MaterialLine(
                item=item,
                name=game.item_name(item),
                needed=amount,
                held=float(held.get(item, 0.0)),
                wanted_by=[
                    name
                    for name, _ in sorted(contributors[item].items(), key=lambda kv: -kv[1])[:3]
                ],
            )
        )

    if out.unpriced:
        out.notes.append(
            "no build recipe in the dump for "
            + ", ".join(sorted(set(out.unpriced))[:4])
            + " -- that cost is unknown rather than zero, so this bill is a LOWER bound"
        )
    return out

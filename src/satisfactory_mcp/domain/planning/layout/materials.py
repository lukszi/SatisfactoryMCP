"""What a plan costs to BUILD, as opposed to what it costs to run.

The startup re-frame (§8.5d, ``docs/planning.md``) split the two cleanly: power is what a
plant costs to run and is the only thing constraining the order you switch it on in, while
materials are what it costs to construct and are independent of order. So "can I afford
this yet?" became a question worth answering on its own.

Every number is data. A building's cost is ``Building.build_cost`` -- the ingredients of
the ``kind == "building"`` recipe that constructs it -- so a Fuel-Powered Generator is
15 Motor + 15 Encased Industrial Beam + 30 Copper Sheet + 50 Rubber + 50 Quickwire because
Docs.json says so, and a game update moves it without touching this file.

Why this is not ``diff._cost``
------------------------------
``diff_vs_save`` already charges materials, and this does NOT replace it. They answer
different questions and the difference is the whole point:

* ``_cost`` charges the **delta** -- ``row.build``, what is left to place -- filtered to
  items you are **short of**, ranked by how hard the shortfall is to fix. It is a shopping
  list for the next session.
* This charges the **whole plan**, every item whether or not you hold it, attributed to the
  buildings that want it, plus the **deck**. It is the price tag.

The second is what you need before starting and the first is what you need once you have.
Neither is derivable from the other: the delta cannot tell you what the plant costs, and
the total cannot tell you what to go and make next.

**Foundations are the number nobody had.** They are not machines, so no build table counts
them, and at 5 Concrete each a measured 6,472-foundation deck is 32,360 Concrete -- larger
than most of the machine bill and previously invisible.

Direct components, not flattened to ore
---------------------------------------
The bill stops at what the build gun consumes, which is what a player actually needs on
hand. Flattening further is ``bom``'s job and a genuinely different computation: Recycled
Plastic and Recycled Rubber form a real 2-cycle, so a tree walk has no correct depth limit
and only the LP expands it honestly. Doing it badly here would produce a confident ore
number that quietly stopped early. The two compose instead -- this says "9,970 Motors",
``bom`` says what a Motor costs.

Belts and pipes are deliberately not costed. Their cost is per metre and there is no route:
the same missing terrain that stops ``plan_layout`` drawing coordinates and stops ``trunks``
claiming more than a straight-line lower bound. A belt bill from a guessed length would be
the largest invented number in this project. Line counts are reported instead, because
those are measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....core.gamedata.model import GameData

__all__ = ["BuildingCost", "MaterialLine", "MaterialsBill", "build_materials"]

#: The 8 m x 8 m foundation the layout counts in. `build_layout` reports whole tiles of
#: this size, so this is the class its foundation totals are priced at.
FOUNDATION_ID = "Build_Foundation_8x1_01_C"


@dataclass
class BuildingCost:
    building_id: str
    name: str
    count: int
    #: item id -> total for ``count`` of them.
    parts: dict[str, float] = field(default_factory=dict)
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
    wanted_by: list[str] = field(default_factory=list)

    @property
    def short(self) -> float:
        return max(0.0, self.needed - self.held)

    @property
    def covered(self) -> bool:
        return self.held >= self.needed


@dataclass
class MaterialsBill:
    lines: list[MaterialLine] = field(default_factory=list)
    buildings: list[BuildingCost] = field(default_factory=list)
    machines: int = 0
    foundations: int = 0
    #: Building classes needed whose build cost is not in the dump. Named rather than
    #: dropped: a bill silently missing a building reads as complete.
    unpriced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

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
    processes: list[dict],
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

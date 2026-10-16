"""What the target Space Elevator phase still bills, and what makes each part today."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData, Recipe, Schematic
from ...factories.query import build_view
from ..phases import phase_number

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = ["BillPart", "Makers", "default_recipe", "target_bill", "unlock_of"]

#: The systems a player buys an unlock in, by schematic type; others are not offered.
SYSTEM_OF_TYPE = {
    "EST_Milestone": "milestone",
    "EST_Tutorial": "milestone",
    "EST_MAM": "MAM",
    "EST_Alternate": "hard drive",
    "EST_HardDrive": "hard drive",
    "EST_ResourceSink": "shop",
}

_TOL = 1e-6


@dataclass(frozen=True)
class BillPart:
    item: str
    owed: float
    held: float

    @property
    def short(self) -> float:
        return max(0.0, self.owed - self.held)


def target_bill(st: WorldState) -> tuple[int | None, list[BillPart]]:
    """The target phase's number and its parts still owed, largest bill first."""
    req = st.phase_requirements()
    target = req["target_phase"]
    stock = st.stock()
    for row in req["phases"]:
        if row["phase"] and row["phase"] == target:
            parts = [
                BillPart(item=item, owed=float(owed), held=float(stock.get(item, 0.0)))
                for item, owed in sorted(row["outstanding"].items(), key=lambda kv: -kv[1])
            ]
            return phase_number(target), parts
    return phase_number(target), []


def default_recipe(game: GameData, item: str, have: set[str]) -> tuple[Recipe | None, bool]:
    """The recipe an item's line is planned with, and whether the save has a way to make it:
    the default recipe, else the first maker; unlocked when any maker is."""
    makers = sorted(game.producers_of(item, "part"), key=lambda r: (r.is_alternate, r.name))
    makers = [r for r in makers if r.main_product == item] or makers
    if not makers:
        return None, False
    return makers[0], any(r.cls in have for r in makers)


def unlock_of(game: GameData, recipe: Recipe) -> Schematic | None:
    """The milestone or MAM node that grants ``recipe``, else any schematic that does."""
    found = [game.schematics[s] for s in recipe.unlocked_by if s in game.schematics]
    for wanted in ("milestone", "MAM"):
        for schematic in found:
            if SYSTEM_OF_TYPE.get(schematic.type) == wanted:
                return schematic
    return found[0] if found else None


@dataclass
class Makers:
    """Nameplate surplus per minute of each item, per named factory and across the world."""

    by_factory: dict[str, list[tuple[str, float]]]
    world: dict[str, float]

    @classmethod
    def of(cls, st: WorldState) -> Makers:
        game = st.game
        by_name = {item.name: c for c, item in game.items.items()}
        everything = build_view("world", list(st.graph.machines()), st.graph, game, st.projection)
        world = {
            by_name[name]: round(everything.net(name), 3)
            for name in everything.flows
            if name in by_name and everything.net(name) > _TOL
        }
        alive = set(st.graph.machines())
        by_factory: dict[str, list[tuple[str, float]]] = {}
        for label in st.labels.labels:
            standing = [m for m in label.anchors if m in alive]
            view = build_view(label.name, standing, st.graph, game, st.projection)
            for name in view.flows:
                rate = view.net(name)
                if name in by_name and rate > _TOL:
                    by_factory.setdefault(by_name[name], []).append((label.name, round(rate, 3)))
        for rows in by_factory.values():
            rows.sort(key=lambda r: -r[1])
        return cls(by_factory=by_factory, world=world)

    def rate(self, item: str) -> float:
        return self.world.get(item, 0.0)

    def factories(self, item: str) -> list[tuple[str, float]]:
        return self.by_factory.get(item, [])

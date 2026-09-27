"""One ladder of purchasable schematics, priced against spendable stock.

The HUB milestones and the MAM tree are the same walk -- status, bill, shortfall,
prerequisites -- over two ``EST_*`` types, so both tools read this rather than each growing
its own vocabulary for the same four facts. What it cannot answer is whether a TIER is open
for purchase: no ``EST_Milestone`` in Docs.json carries a dependency of any kind, and the
Space Elevator gate that opens tiers rides on assets the dump does not ship (§6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData, Schematic

if TYPE_CHECKING:
    from ..world.inventory import Inventory
    from .unlocks import UnlockSet

__all__ = ["Missing", "Rung", "SchematicLadder"]


@dataclass(frozen=True)
class Missing:
    """One line of a bill the player cannot cover, with both sides of the comparison."""

    item: str
    need: float
    have: float

    @property
    def short_by(self) -> float:
        return self.need - self.have


@dataclass(frozen=True)
class Rung:
    """One schematic, and where the player stands in front of it."""

    schematic: Schematic
    done: bool
    missing: tuple[Missing, ...]
    blocked_by: tuple[str, ...]

    @property
    def status(self) -> str:
        """Prerequisites outrank the bill: a node whose dependencies are unmet is BLOCKED
        however much stock is on hand, because buying the parts is not the next move."""
        if self.done:
            return "DONE"
        if self.blocked_by:
            return "BLOCKED"
        return "short" if self.missing else "READY"


@dataclass
class SchematicLadder:
    """Schematics of one type, each priced against what the player can actually spend."""

    game: GameData
    unlocks: UnlockSet
    inventory: Inventory

    def rungs(self, type: str) -> list[Rung]:
        """Every schematic of ``type`` still on offer, in name order -- tier ordering is
        the caller's. Retired nodes are left out, so no list or count can offer one.

        MAM nodes sit at tier 0 and 3 with no progression meaning, so sorting by tier here
        would reorder the MAM view for nothing.
        """
        done = self.unlocks.purchased_schematic_ids
        stock = self.inventory.stock()
        out = []
        for s in sorted(self.game.schematics.values(), key=lambda s: s.name.casefold()):
            if s.type != type or s.is_retired:
                continue
            missing = tuple(
                Missing(item=f.item, need=f.amount, have=stock.get(f.item, 0.0))
                for f in s.cost
                if stock.get(f.item, 0.0) < f.amount
            )
            blocked = tuple(
                self.game.schematics[d].name
                for d in s.dependencies
                if d in self.game.schematics and d not in done
            )
            out.append(Rung(schematic=s, done=s.cls in done, missing=missing, blocked_by=blocked))
        return out

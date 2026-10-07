"""Hard drives: the choices waiting in the MAM, and the drives still in the bag."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from ...core.gamedata.model import GameData, Recipe
from ...core.saveio.schema import Projection

if TYPE_CHECKING:
    from ..world.inventory import Inventory
    from .unlocks import UnlockSet

__all__ = ["HardDriveDesk", "HardDriveOffer", "HardDriveOption"]


class HardDriveOption(TypedDict):
    """One reward a drive offers; an unknown schematic is its own name, granting nothing."""

    schematic: str
    name: str
    recipes: list[Recipe]
    slots: int


@dataclass
class HardDriveOffer:
    hard_drive_id: int | None
    rerolls_left: int
    options: list[HardDriveOption]


@dataclass
class HardDriveDesk:
    """Pending hard-drive choices, each option resolved to the recipes it would grant."""

    projection: Projection
    game: GameData
    unlocks: UnlockSet

    @property
    def last_used_hard_drive_id(self) -> int | None:
        """The id of the drive analysed most recently in the MAM.

        mLastUsedHardDriveID, the counter the game numbers each analysed drive with. The
        drive it names can still be pending: on the reference save it is 36, and drive 36
        is the newest row among the unclaimed offers. It says nothing about which choice
        was settled.
        """
        raw = self.projection.get("research", {}).get("last_used_hard_drive_id")
        return raw if isinstance(raw, int) else None

    @cached_property
    def hard_drive_offers(self) -> list[HardDriveOffer]:
        """The player's live pending choices, straight from the save."""
        out: list[HardDriveOffer] = []
        for entry in self.projection.get("research", {}).get("unclaimed_hard_drives", ()):
            options: list[HardDriveOption] = []
            for sid in entry.get("options", ()):
                s = self.game.schematics.get(sid)
                if s is None:
                    options.append({"schematic": sid, "name": sid, "recipes": [], "slots": 0})
                    continue
                options.append(
                    {
                        "schematic": sid,
                        "name": s.name,
                        "recipes": self.unlocks.schematic_recipes(s),
                        "slots": s.grants_inventory_slots,
                    }
                )
            executed = entry.get("rerolls_executed") or 0
            out.append(
                HardDriveOffer(
                    hard_drive_id=entry.get("hard_drive_id"),
                    rerolls_left=max(0, 1 - executed),
                    options=options,
                )
            )
        out.sort(key=lambda o: (o.hard_drive_id is None, o.hard_drive_id))
        return out

    def spare_hard_drives(self, inventory: Inventory) -> int:
        """Unanalysed drives on hand."""
        return int(inventory.stock().get("Desc_HardDrive_C", 0))

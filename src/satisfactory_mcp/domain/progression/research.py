"""MAM-gated capabilities: whether one is researched, and what still blocks it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from ..world.inventory import Inventory
    from .ladder import Rung
    from .unlocks import UnlockSet

__all__ = ["ResearchGates"]


@dataclass
class ResearchGates:
    """The unlock flags a save carries, and the schematics behind the ones it lacks."""

    projection: dict
    game: GameData
    unlocks: UnlockSet
    inventory: Inventory

    #: Capability -> the unlock flag that records it, where one exists. Present only
    #: once true: UE omits a SaveGame property at its default, so absent means false.
    CAPABILITY_FLAGS: ClassVar[dict[str, str]] = {
        "production_boost": "mIsBuildingProductionBoostUnlocked",
    }

    #: MAM tree -> the class-id prefixes its nodes carry. The trees themselves are not in
    #: Docs.json (the BPD_ResearchTree_* assets do not ship) and the save names only which
    #: trees are open, so membership is read off the schematic's class id. Nine trees pair
    #: 1:1 with a prefix of their own name; [UNVERIFIED] the four alien-organism prefixes
    #: are grouped by elimination -- they are the MAM nodes left once the other nine trees
    #: have theirs, and no other tree remains for them.
    #: BPD_ResearchTree_HardDrive_C is deliberately absent: its nodes are EST_Alternate
    #: schematics won from drives, not EST_MAM rows, and no MAM view lists them.
    TREE_PREFIXES: ClassVar[dict[str, tuple[str, ...]]] = {
        "BPD_ResearchTree_AlienOrganisms_C": (
            "Research_ACarapace_",
            "Research_AO_",
            "Research_AOrganisms_",
            "Research_AOrgans_",
        ),
        "BPD_ResearchTree_AlienTech_C": ("Research_Alien_",),
        "BPD_ResearchTree_Caterium_C": ("Research_Caterium_",),
        "BPD_ResearchTree_Mycelia_C": ("Research_Mycelia_",),
        "BPD_ResearchTree_Nutrients_C": ("Research_Nutrients_",),
        "BPD_ResearchTree_PowerSlugs_C": ("Research_PowerSlugs_",),
        "BPD_ResearchTree_Quartz_C": ("Research_Quartz_",),
        "BPD_ResearchTree_Sulfur_C": ("Research_Sulfur_",),
        "BPD_ResearchTree_XMas_C": ("Research_XMas_",),
    }

    @property
    def _unlock_flags(self) -> dict:
        return self.projection.get("unlock_flags", {}) or {}

    @property
    def _research(self) -> dict:
        return self.projection.get("research", {}) or {}

    @property
    def knows_trees(self) -> bool:
        """Whether the projection carries the unlocked-tree list at all.

        False on a projection written before the key was extracted, where an empty list
        and a world with no tree open look identical -- and nothing may be called locked
        on that evidence.
        """
        return "unlocked_trees" in self._research

    @property
    def unlocked_trees(self) -> set[str]:
        """The MAM trees the player has opened. Empty is a real answer when
        ``knows_trees``."""
        return set(self._research.get("unlocked_trees") or ())

    @property
    def ongoing(self) -> dict[str, float]:
        """Schematic -> seconds of research left on it, as of the moment of the save.

        A node in here has been paid for and is running; it is neither outstanding work
        nor finished. The clock is stored, not a timestamp, so it does not tick down
        while the game is closed.
        """
        out: dict[str, float] = {}
        for row in self._research.get("ongoing") or ():
            cls = row.get("schematic")
            left = row.get("seconds_left")
            if cls:
                out[cls] = float(left) if isinstance(left, (int, float)) else 0.0
        return out

    def tree_of(self, schematic_id: str) -> str | None:
        """The MAM tree a node lives in, or ``None`` for a class no prefix claims."""
        for tree, prefixes in self.TREE_PREFIXES.items():
            if any(schematic_id.startswith(p) for p in prefixes):
                return tree
        return None

    def tree_locked(self, schematic_id: str) -> bool:
        """Whether this node sits in a tree the player has not opened yet.

        False whenever the answer is not known -- an old projection, or a class this
        register does not place -- since an unplaceable node reported as locked is a
        worse answer than one reported as available.
        """
        tree = self.tree_of(schematic_id)
        return bool(tree) and self.knows_trees and tree not in self.unlocked_trees

    def status(self, rung: Rung) -> str:
        """A MAM node's status: DONE, RUNNING, TREE SHUT, or the ladder's own."""
        if rung.done:
            return "DONE"
        if rung.schematic.cls in self.ongoing:
            return "RUNNING"
        if self.tree_locked(rung.schematic.cls):
            return "TREE SHUT"
        return rung.status

    def has_capability(self, name: str) -> bool:
        """Whether a MAM-gated capability is researched.

        The unlock flag wins when the projection carries one, because it is what the game
        itself checks. The purchased-schematic set is the fallback, and it is not merely
        belt-and-braces: the flag is absent from a save taken before the research AND from
        any projection written before schema 10 extracted it, and those two look identical
        from here. Falling back keeps an older projection answering correctly instead of
        reporting every capability locked.
        """
        from ...core.gamedata.constants import CAPABILITY_SCHEMATICS

        flag = self.CAPABILITY_FLAGS.get(name)
        if flag and flag in self._unlock_flags:
            return bool(self._unlock_flags[flag])
        gate = CAPABILITY_SCHEMATICS.get(name)
        return bool(gate) and gate in self.unlocks.purchased_schematic_ids

    def research_gate(self, name: str) -> dict | None:
        """The schematic that unlocks ``name``, its cost, and what the player holds.

        ``None`` when the capability is already researched, so a caller can treat a
        truthy result as "here is what is still in the way".
        """
        from ...core.gamedata.constants import CAPABILITY_SCHEMATICS

        gate = CAPABILITY_SCHEMATICS.get(name)
        schematic = self.game.schematics.get(gate or "")
        if schematic is None or self.has_capability(name):
            return None
        stock = self.inventory.stock()
        rows = [
            {
                "item": f.item,
                "name": self.game.item_name(f.item),
                "need": f.amount,
                "have": stock.get(f.item, 0.0),
            }
            for f in schematic.cost
        ]
        return {
            "capability": name,
            "schematic": gate,
            "schematic_name": schematic.name,
            "kind": schematic.type,
            "cost": rows,
            "short": [r for r in rows if r["have"] < r["need"]],
            "affordable": all(r["have"] >= r["need"] for r in rows),
            #: Prerequisite schematics not yet purchased. Empty on an unblocked node.
            "blocked_by": [
                self.game.schematics[d].name
                for d in schematic.dependencies
                if d in self.game.schematics and d not in self.unlocks.purchased_schematic_ids
            ],
        }

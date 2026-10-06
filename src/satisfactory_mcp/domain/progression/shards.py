"""Power Shards and Somersloops: what is held, what is slotted, what is free."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.constants import POTENTIAL_SHARD_SLOTS, shards_for_clock
from ...core.gamedata.model import GameData
from ...core.saveio.records import instance_leaf
from ..world.inventory import SPENDABLE

if TYPE_CHECKING:
    from ..world.inventory import Inventory

__all__ = ["OverclockBudget"]

#: What each spendable bucket is called where a budget says where things are.
_PLACE_OF_BUCKET = {"player": "carried", "storage": "storage", "depot": "depot"}


@dataclass
class OverclockBudget:
    """The two spendable pools that change a machine's rate.

    Takes the build records rather than reaching for them: both budgets read
    ``InventoryPotential`` off every machine, extractor and generator alike, and
    which records those are is the census's business.
    """

    projection: dict
    game: GameData
    inventory: Inventory
    records: list[dict] = field(default_factory=list)

    #: The Somersloop and Mercer Sphere item classes; ids, since a localised dump renames.
    SOMERSLOOP_ITEM: ClassVar[str] = "Desc_WAT1_C"
    MERCER_ITEM: ClassVar[str] = "Desc_WAT2_C"

    def _slug_rows(self, stock: dict[str, float]) -> tuple[list[dict], float]:
        """Uncrafted slugs held, richest first, and the shards they would craft into."""
        slugs = []
        craftable = 0.0
        for item, yield_each in sorted(self.game.slug_yields().items(), key=lambda kv: -kv[1]):
            held = stock.get(item, 0.0)
            if not held:
                continue
            slugs.append(
                {
                    "item": item,
                    "name": self.game.item_name(item),
                    "held": held,
                    "each": yield_each,
                    "shards": held * yield_each,
                }
            )
            craftable += held * yield_each
        return slugs, craftable

    def _shard_holders(self, shard_items: dict, per_shard: float) -> tuple[list[dict], int]:
        """Every building with a shard slotted or a clock that needs one, and the slotted sum."""
        committed = 0
        holders: list[dict] = []
        for record in self.records:
            slotted = sum(
                n
                for item, n in (record.get("potential_slots") or {}).items()
                if item in shard_items
            )
            clock = float(record.get("clock") or 1.0)
            needed = shards_for_clock(clock, per_shard)
            if not slotted and not needed:
                continue
            committed += slotted
            holders.append(
                {
                    "instance": instance_leaf(record["instance"]),
                    "cls": record.get("cls", "?"),
                    "clock": clock,
                    "slotted": slotted,
                    "needed": needed,
                    # A slot filled but not being used by the current clock.
                    "idle": max(0, slotted - needed),
                }
            )
        holders.sort(key=lambda h: (-h["slotted"], h["cls"]))
        return holders, committed

    def shard_budget(self) -> dict:
        """Power Shards held, committed and free.

        Committed shards are READ off each buildable's ``InventoryPotential`` and never
        derived from its clock, which a shard caps rather than sets (save-projection.md §6.5).
        ``free`` is what ``stock`` spends, so slotted shards are never counted twice.
        """
        shard_items = self.game.clock_shards()
        per_shard = max(shard_items.values()) if shard_items else 0.0
        stock = self.inventory.stock()
        free = sum(stock.get(item, 0.0) for item in shard_items)

        # Where they physically are, since ``free`` pools the spendable buckets and so
        # cannot answer "is that in the Depot?".
        wanted = set(shard_items) | set(self.game.slug_yields())
        by_place: dict[str, dict[str, float]] = {}
        for bucket in SPENDABLE:
            source = self.inventory.bucket(bucket)
            held = {self.game.item_name(k): v for k, v in source.items() if k in wanted and v}
            if held:
                by_place[_PLACE_OF_BUCKET[bucket]] = held

        # Uncrafted slugs are latent shards, counted apart: on the reference save the Depot
        # alone holds slugs worth 404 shards against 22 already crafted.
        slugs, craftable = self._slug_rows(stock)
        holders, committed = self._shard_holders(shard_items, per_shard)
        return {
            "shard_items": shard_items,
            "slugs": slugs,
            "by_place": by_place,
            # Potential, never availability: crafting is a manual step.
            "craftable": craftable,
            "potential": free + craftable,
            "free": free,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "slots_per_building": POTENTIAL_SHARD_SLOTS,
            # False on a projection too old to carry InventoryPotential: unknown, not zero.
            "measured": any("potential_slots" in r for r in self.records),
        }

    def sloop_budget(self) -> dict:
        """Somersloops on hand and in machines.

        Free ones are what ``stock`` spends; committed ones are read from the same
        ``InventoryPotential`` slots shards use, never inverted from the production boost.
        Mercer Spheres share the WAT prefix and are counted apart, never added in (§6.5).
        """
        stock = self.inventory.stock()
        free = float(stock.get(self.SOMERSLOOP_ITEM, 0.0))
        by_place: dict[str, float] = {}
        for bucket in SPENDABLE:
            held = float(self.inventory.bucket(bucket).get(self.SOMERSLOOP_ITEM, 0.0))
            if held:
                by_place[_PLACE_OF_BUCKET[bucket]] = held

        committed = 0.0
        holders: list[dict] = []
        for record in self.records:
            slots = record.get("potential_slots") or {}
            slotted = float(slots.get(self.SOMERSLOOP_ITEM, 0.0))
            if not slotted:
                continue
            committed += slotted
            building = self.game.buildings.get(record.get("cls", ""))
            holders.append(
                {
                    "instance": instance_leaf(record["instance"]),
                    "cls": record.get("cls", "?"),
                    "name": building.name if building else record.get("cls", "?"),
                    "sloops": slotted,
                    # What the plan model says those slots are worth, against the save's boost.
                    "boost": building.boost_for(int(slotted)) if building else None,
                    "boost_in_save": record.get("production_boost"),
                }
            )
        holders.sort(key=lambda h: (-h["sloops"], h["cls"]))
        return {
            "item": self.SOMERSLOOP_ITEM,
            "free": free,
            "by_place": by_place,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "mercer_spheres": float(stock.get(self.MERCER_ITEM, 0.0)),
            # As the shard budget's ``measured``: false means unknown, not zero.
            "committed_measured": any("potential_slots" in r for r in self.records),
        }

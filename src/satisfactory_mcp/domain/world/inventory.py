"""What the world holds, split into what can be spent and what merely exists.

Two granularities over the same stacks: ``stock``/``breakdown`` sum the world into per-item
totals, ``holdings`` keeps one row per place. Fluids are m3 everywhere here; the sidecar
reports litres."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from math import ceil

from ...core.gamedata.constants import STACK_SIZE
from ...core.gamedata.model import GameData
from ...core.saveio.records import instance_leaf
from ...core.saveio.schema import CrateRecord, InventoriesBlock, Projection, StorageRecord

__all__ = ["BUCKETS", "CRATE_KIND_TEXT", "SPENDABLE", "Holding", "Inventory"]

#: What each ``crate_kind`` means, in the words a reader wants rather than the enum's.
#: ``none`` is the game's own ``CT_None``: ``mCrateType`` arrived in build 433351, so a crate
#: made before that carries no type and never will. Here rather than in the web router
#: because the map popup and the ``crates`` tool both gloss the same three words.
CRATE_KIND_TEXT = {
    "death": "dropped where a pioneer died",
    "dismantle": "overflow from dismantling with a full inventory",
    "none": "kind not recorded -- this crate predates the game's death/dismantle distinction",
}

#: The piles ``breakdown`` reports. ``depot`` is the uploaded Dimensional Depot pool, which
#: is a top-level projection key rather than one of ``sources``.
BUCKETS = ("player", "storage", "depot", "machine", "crate")

#: Which of them ``stock`` adds up. The other two are stated in `Inventory.stock`.
SPENDABLE = ("player", "storage", "depot")


@dataclass(frozen=True)
class Holding:
    """One place that holds things: a container, a fluid buffer, or a crate on the ground.

    ``items`` is biggest first. ``fill`` is the fraction of the place that is used and is
    ``None`` where it cannot be measured rather than 0 -- a class the dump carries no
    capacity for, a container the projection wrote no slot count for, or an item whose
    stack size the dump does not give.
    """

    source: str
    kind: str
    cls: str
    instance: str
    pos: tuple[float, float, float] | None
    items: tuple[tuple[str, float], ...]
    total: float
    slots: int | None = None
    slots_used: int | None = None
    fill: float | None = None
    capacity_m3: float | None = None
    #: What a fluid buffer holds, per the ``FGPipeNetwork`` that claims it; a buffer no
    #: network claims names nothing, which is why this is nullable on a ``fluid`` row.
    fluid: str | None = None
    #: ``death``, ``dismantle`` or ``none`` on a crate row, and ``None`` on a container.
    crate_kind: str | None = None

    def amount_of(self, item: str) -> float:
        return next((n for i, n in self.items if i == item), 0.0)


@dataclass
class Inventory:
    """Item stacks, joined to the item dump so fluids can be scaled to m3."""

    projection: Projection
    game: GameData

    @cached_property
    def sources(self) -> InventoriesBlock:
        """The raw per-place stacks the sidecar wrote: player, storage, machine -- and,
        from schema 19, crate: the death and dismantle crates lying on the ground, which
        counted into ``machine`` until then. A pre-19 projection simply has no ``crate``
        key, and every ``.get`` below reads that as an empty bucket."""
        return self.projection.get("inventories", {}) or {}

    def bucket(self, name: str) -> dict[str, float]:
        """One of ``BUCKETS`` as raw stacks; ``depot`` is a top-level projection key."""
        source: dict[str, float] | None = (
            self.projection.get("depot") if name == "depot" else self.sources.get(name)
        )
        return source or {}

    def stock(self) -> dict[str, float]:
        """What the player can actually spend: the ``SPENDABLE`` buckets, fluids in m3.

        Machine buffers are excluded (every stack in the world gives Water 5,556,375 L,
        so anything would look affordable), and so are crates on the ground, which delete
        themselves when emptied; save-projection.md §6.14 has the decision.
        """
        out: dict[str, float] = {}
        for name in SPENDABLE:
            for item, amount in self.bucket(name).items():
                out[item] = out.get(item, 0.0) + amount
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        for item, m3 in self.fluid_buffers().items():
            out[item] = out.get(item, 0.0) + m3
        return out

    def fluid_buffers(self) -> dict[str, float]:
        """m3 standing in fluid buffers, per fluid. Part of the ``storage`` pile: the sidecar
        keeps a buffer's contents on its storage row, not in ``inventories``, so without this
        the piles would not sum to ``holdings``."""
        out: dict[str, float] = {}
        for row in self.projection.get("storage") or ():
            if (fluid := row.get("fluid")) and (stored := row.get("stored_m3")):
                out[fluid] = out.get(fluid, 0.0) + float(stored)
        return out

    def machine_buffers(self) -> dict[str, float]:
        """Material sitting in machine inputs, outputs and pipes. Not spendable.

        No longer includes the crates on the ground: schema 19 gave those their own
        bucket, so this is at last only what its name says. On a pre-19 projection the
        crates are still in here, indistinguishably, which is the shape that projection
        actually wrote.
        """
        out = dict(self.sources.get("machine", {}))
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        return out

    def breakdown(self) -> dict[str, dict[str, float]]:
        """Per item, how much is in each pile, keyed by ``BUCKETS`` plus ``spendable``.

        The numbers behind the affordability check: ``spendable`` is exactly what `stock`
        returns, and the other piles are reported beside it rather than dropped, because
        "short 40 Circuit Board" and "40 Circuit Board sitting in machine inputs" are
        different situations with different fixes.
        """
        out: dict[str, dict[str, float]] = {}
        for name in BUCKETS:
            for item, amount in self.bucket(name).items():
                row = out.setdefault(item, dict.fromkeys((*BUCKETS, "spendable"), 0.0))
                scaled = self._m3(item, float(amount))
                row[name] += scaled
                if name in SPENDABLE:
                    row["spendable"] += scaled
        for item, m3 in self.fluid_buffers().items():
            row = out.setdefault(item, dict.fromkeys((*BUCKETS, "spendable"), 0.0))
            row["storage"] += m3
            row["spendable"] += m3
        return out

    def holdings(self, item: str | None = None) -> list[Holding]:
        """Every container, fluid buffer and crate as its own row, fullest first.

        The per-PLACE view of the same stacks `breakdown` sums: which box, where it stands,
        and how full it is. ``item`` keeps only the places holding that item, a fluid buffer
        answering to the fluid its plumbing claims.
        """
        rows = [self._holding(row, "storage") for row in self.projection.get("storage") or ()] + [
            self._holding(row, "crate") for row in self.projection.get("crates") or ()
        ]
        if item is not None:
            rows = [h for h in rows if h.amount_of(item)]
        rows.sort(key=lambda h: -(h.amount_of(item) if item is not None else h.total))
        return rows

    def _m3(self, item: str, amount: float) -> float:
        it = self.game.items.get(item)
        return amount / 1000.0 if it is not None and it.is_fluid else amount

    def _holding(self, row: StorageRecord | CrateRecord, source: str) -> Holding:
        """One projection row as a `Holding`, with whatever fullness can be measured."""
        cls = str(row.get("cls") or "")
        pos = row.get("pos")
        instance = instance_leaf(row.get("instance", ""))
        where = (float(pos[0]), float(pos[1]), float(pos[2])) if pos and len(pos) >= 3 else None
        slots: int | None = row.get("slots")
        building = self.game.buildings.get(cls)
        if "stored_m3" in row:
            # Already m3 in the projection, unlike every other stack here, and a bare float
            # is not a reading until it is put against what the class holds.
            stored = float(row.get("stored_m3") or 0.0)
            capacity = getattr(building, "storage_capacity_m3", 0.0) if building else 0.0
            fluid = row.get("fluid")
            return Holding(
                source=source,
                kind="fluid",
                cls=cls,
                instance=instance,
                pos=where,
                items=((fluid, stored),) if fluid else (),
                total=stored,
                slots=slots,
                capacity_m3=capacity or None,
                fill=stored / capacity if capacity else None,
                fluid=fluid,
            )

        items = tuple((str(e[0]), float(e[1])) for e in row.get("items") or () if len(e) >= 2)
        items = tuple(sorted(items, key=lambda e: -e[1]))
        used = self._slots_used(items)
        return Holding(
            source=source,
            kind="solid",
            cls=cls,
            instance=instance,
            pos=where,
            items=items,
            total=sum(n for _, n in items),
            slots=slots,
            slots_used=used,
            fill=used / slots if used is not None and slots else None,
            crate_kind=str(row.get("kind") or "none") if source == "crate" else None,
        )

    def _slots_used(self, items: tuple[tuple[str, float], ...]) -> int | None:
        """How many slots those stacks occupy, or ``None`` if any item's stack size is
        unknown -- a part-counted box would read as a nearly empty one."""
        used = 0
        for item, count in items:
            it = self.game.items.get(item)
            size = STACK_SIZE.get(it.stack_size) if it is not None else None
            if not size:
                return None
            used += ceil(count / size)
        return used

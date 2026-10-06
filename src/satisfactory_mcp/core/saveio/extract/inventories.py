"""Inventory stacks: the world totals by owner, and the containers and crates that hold them."""

from __future__ import annotations

from .readers import owner_class, ref_class, struct_fields
from .registers import (
    CRATE_CLASSES,
    CRATE_KINDS,
    FLUID_BUFFER_CLASSES,
    STORAGE_CLASSES,
    STORAGE_OWNER_HINTS,
)

__all__ = [
    "accumulate_inventory",
    "crate_kind",
    "crates",
    "inventory_bucket",
    "inventory_totals",
    "storage",
]


def inventory_bucket(instance: str) -> str:
    """Which pile a stack belongs to -- player, storage, crate or machine -- from the
    component's ``...Build_X_C_123.Role`` instanceName.

    Storage is membership of ``STORAGE_CLASSES`` and a crate of ``CRATE_CLASSES``, not a word
    in the owner's name: the Personal Storage Box carries no "Storage" a name test would
    catch. Summing everything would count pipe contents in litres as spendable stock.
    """
    owner = instance.rsplit(".", 2)[-2] if instance.count(".") >= 2 else instance
    role = instance.rsplit(".", 1)[-1]
    if "PlayerState" in owner or owner.startswith(("Char_", "BP_Player")):
        return "player"
    if role == "StorageInventory" and (
        owner_class(owner) in STORAGE_CLASSES or any(tag in owner for tag in STORAGE_OWNER_HINTS)
    ):
        return "storage"
    if role.lower() == "inventory" and owner_class(owner) in CRATE_CLASSES:
        return "crate"
    return "machine"


def accumulate_inventory(raw, totals: dict) -> None:
    """Add mInventoryStacks into ``totals`` as {itemClass: count}.

    The ``Item`` member is a bare ``[assetPath, int]`` pair, not a keyed struct.
    """
    for stack in raw if isinstance(raw, list) else []:
        fields = struct_fields(stack)
        item_raw = fields.get("Item")
        if isinstance(item_raw, list) and item_raw:
            item = ref_class(item_raw[0])
        else:
            item = ref_class(struct_fields(item_raw).get("ItemClass"))
        amount = fields.get("NumItems", 0)
        if item and isinstance(amount, (int, float)) and amount:
            totals[item] = totals.get(item, 0) + amount


def inventory_totals(stacks) -> dict:
    """mInventoryStacks -> a fresh {itemClass: count}."""
    totals: dict = {}
    accumulate_inventory(stacks, totals)
    return totals


def _items_biggest_first(totals: dict) -> list[list]:
    """``[[item, count], ...]``, ties by class, so a popup's top few are the useful few."""
    return [
        [item, amount] for item, amount in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def storage(storage_actors: list, storage_inventories: dict, networks: list) -> list:
    """Every container and fluid buffer, where it stands, and what is inside it (§6.16).

    ``storage_actors`` is ``[(class, instanceName, pos, yaw, mFluidBox), ...]``,
    ``storage_inventories`` ``{owner: (totals, slotCount)}`` for every ``StorageInventory``,
    and ``networks`` the pipe networks, which name a buffer's fluid.
    """
    fluid_of: dict[str, str | None] = {}
    for _network_id, fluid, members in networks:
        for member in members:
            if member:
                fluid_of[str(member)] = fluid

    rows: list[dict] = []
    for cls, instance, pos, yaw, fluid_box in sorted(
        storage_actors, key=lambda actor: (actor[0], str(actor[1]))
    ):
        row: dict = {"cls": cls, "instance": instance, "pos": pos, "yaw": yaw}
        if cls in FLUID_BUFFER_CLASSES:
            row["fluid"] = fluid_of.get(str(instance))
            # Cubic metres, not the litres ``inventories`` reports for a fluid stack.
            try:
                row["stored_m3"] = round(float(fluid_box), 2)
            except (TypeError, ValueError):
                row["stored_m3"] = None
        else:
            totals, slots = storage_inventories.get(str(instance), ({}, 0))
            row["items"] = _items_biggest_first(totals)
            row["slots"] = slots
        rows.append(row)
    return rows


def crate_kind(raw) -> str:
    """``mCrateType`` as one of ``CRATE_KINDS``' words, defaulting to ``none``.

    The parser hands the enum back as ``[enumName, "EFGCrateType::CT_DeathCrate"]``; any
    other shape or an unknown value is the enum's own "this crate does not say".
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return CRATE_KINDS["CT_None"]
    return CRATE_KINDS.get(str(raw[1]).rsplit("::", 1)[-1], CRATE_KINDS["CT_None"])


def crates(crate_actors: list, inventory_components: dict) -> list:
    """Every crate on the ground, its kind, place and contents, sorted by kind then instance.

    ``inventory_components`` holds every component named ``Inventory`` in either case; only
    crate owners are looked up, so pawns and drop pods go unclaimed. See §6.13.
    """
    rows: list[dict] = []
    for cls, instance, pos, yaw, raw_type in crate_actors:
        totals, slots = inventory_components.get(str(instance), ({}, 0))
        rows.append(
            {
                "cls": cls,
                "instance": instance,
                "pos": pos,
                "yaw": yaw,
                "kind": crate_kind(raw_type),
                "items": _items_biggest_first(totals),
                "slots": slots,
            }
        )
    rows.sort(key=lambda row: (row["kind"], str(row["instance"])))
    return rows

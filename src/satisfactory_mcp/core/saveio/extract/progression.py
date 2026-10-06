"""The singleton managers' struct arrays: depot, phase costs, hard drives, research."""

from __future__ import annotations

from .readers import ref_class, struct_fields

__all__ = ["cost_amounts", "hard_drives", "ongoing", "phase_costs", "stored_items"]


def stored_items(raw) -> dict:
    """FGCentralStorageSubsystem.mStoredItems -> {itemClass: amount} (the Dimensional Depot)."""
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass")) or ref_class(fields.get("Item"))
        amount = fields.get("Amount", fields.get("NumItems", 0))
        if item is None and isinstance(entry, list) and len(entry) >= 2:
            item = ref_class(entry[0])
            if isinstance(entry[1], (int, float)):
                amount = entry[1]
        if item and isinstance(amount, (int, float)) and amount:
            out[item] = out.get(item, 0) + amount
    return out


def phase_costs(raw) -> dict:
    """mGamePhaseCosts: remaining delivery amounts per phase. DEPRECATED AND FROZEN.

    Emitted only so the server can show it beside the live record and say so; see
    ``docs/save-projection.md`` §6.4. Only 4 phases are stored, so later phases never appear.
    """
    out: dict = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        raw_phase = fields.get("gamePhase")
        # ByteProperty arrives as [enumTypeName, valueName]; take the value.
        if isinstance(raw_phase, list) and raw_phase:
            phase = str(raw_phase[-1])
        else:
            phase = ref_class(raw_phase) or str(raw_phase)
        out[phase] = cost_amounts(fields.get("cost"))
    return out


def cost_amounts(raw) -> dict:
    """An ``FItemAmount`` array -> {itemClass: amount}."""
    out: dict[str, float] = {}
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        item = ref_class(fields.get("ItemClass"))
        if item:
            out[item] = fields.get("Amount", 0)
    return out


def hard_drives(raw) -> list:
    """mUnclaimedHardDriveData -> the player's live 2-way choices.

    ``FHardDriveData`` per FGResearchManager.h. Rerolls left is ``1 - rerolls_executed``
    (``mNumRerollsPerHardDrive = 1``, a config default a packaged ini could override).
    """
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        rewards = [ref_class(reward) for reward in fields.get("PendingRewards") or []]
        out.append(
            {
                "hard_drive_id": fields.get("HardDriveID"),
                "options": [reward for reward in rewards if reward],
                "rerolls_executed": fields.get("PendingRewardsRerollsExecuted", 0),
            }
        )
    return out


def ongoing(raw) -> list:
    """mSavedOngoingResearch. The float is seconds REMAINING, not a timestamp."""
    out: list = []
    for entry in raw if isinstance(raw, list) else []:
        fields = struct_fields(entry)
        inner = struct_fields(fields.get("ResearchData")) if "ResearchData" in fields else fields
        out.append(
            {
                "schematic": ref_class(inner.get("Schematic")),
                "seconds_left": fields.get("ResearchCompleteTimestamp"),
                "fields_seen": sorted(fields),
            }
        )
    return out

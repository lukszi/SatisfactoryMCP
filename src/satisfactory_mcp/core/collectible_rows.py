"""One row of ``data/world_collectibles.json`` and the objects nested in it.

The generator (``tools/collectibles``) writes it and ``domain.collectibles`` reads it, so the
shape is declared once, here, where both may import it.
"""

from __future__ import annotations

from typing import Literal, NotRequired

from typing_extensions import TypedDict

__all__ = ["HazardContext", "MapPlacement", "PickupContents", "RowState", "UnlockCost"]

#: What the newest save says about a row; docs/world-collectibles.md defines each.
RowState = Literal["collected", "present", "unknown"]


class PickupContents(TypedDict):
    """A loot cache's or mushroom's ``mPickupItems``."""

    item: str | None
    item_path: str | None
    count: int | None


class UnlockCost(TypedDict):
    """A drop pod's ``mUnlockCost``; ``cost_type`` is null where the pod holds the default."""

    cost_type: str | None
    item: NotRequired[str | None]
    amount: NotRequired[int | None]
    power_mw: NotRequired[float]


class HazardContext(TypedDict, total=False):
    """A row's ``hazard`` object: geometry to other map actors, each key only when it applies."""

    hostiles_nearby: dict[str, int]
    spawns_here: list[str]
    nearest_hostile_cm: float
    nearest_gas_cm: float
    nearest_gas_class: str
    inside_spore_flower_damage_sphere: bool
    nearest_uranium_cm: float
    nearest_nuclear_hog_spawner_cm: float


#: A placement the map itself makes. The functional form, because "class" is a keyword.
MapPlacement = TypedDict(
    "MapPlacement",
    {
        "instance": str,
        "cell": str,
        "category": str,
        "class": str,
        "x": float,
        "y": float,
        "z": float,
        "state": RowState,
        "attached_to": NotRequired[str],
        "looted": NotRequired[bool | None],
        "contents": NotRequired[PickupContents],
        "unlock_cost": NotRequired[UnlockCost],
        "hazard": NotRequired[HazardContext],
    },
)

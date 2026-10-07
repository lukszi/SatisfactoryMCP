"""Two plumbing faults the save states outright, neither of which walks a pipe.

A fluid buffer's own head lift is the height of the fluid standing in it, so one holding
less than `BUFFER_BALANCE_HEAD_M` of fluid cannot push out as fast as it takes in. A
pipeline pump no wire reaches still passes fluid while lifting nothing, so a line that
climbs past it stops climbing and no machine on it looks broken. See `docs/plumbing.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.gamedata.constants import BUFFER_BALANCE_HEAD_M
from ...core.gamedata.model import GameData
from ...core.saveio.records import instance_leaf
from ...core.saveio.schema import Position, Projection

if TYPE_CHECKING:
    from ..factories.model import FactoryGraph

__all__ = [
    "PUMP_CLASSES",
    "ThrottledBuffer",
    "balance_level_m3",
    "throttled_buffers",
    "unwired_pumps",
]

#: The classes that lift. A Valve is the same native class and is deliberately not one:
#: its ``mDesignPressure`` is 0, so leaving one unpowered costs no head lift.
PUMP_CLASSES = ("Build_PipelinePump_C", "Build_PipelinePumpMk2_C")


@dataclass(frozen=True)
class ThrottledBuffer:
    """One fluid buffer standing below the level it needs to output at its intake rate."""

    instance: str
    cls: str
    fluid: str | None
    stored_m3: float
    balance_m3: float
    pos: Position | None = None

    @property
    def share(self) -> float:
        return self.stored_m3 / self.balance_m3 if self.balance_m3 else 0.0


def balance_level_m3(game: GameData, cls: str) -> float | None:
    """Cubic metres this buffer class must hold to output as fast as it takes in.

    ``None`` for anything game data does not describe as a reservoir. The fill height is the
    clearance box's own height, which is 8 m on the Fluid Buffer and 12 m on the Industrial
    one -- the two figures the manual quotes for their head lift when full.
    """
    building = game.buildings.get(cls)
    if building is None or not building.storage_capacity_m3 or building.footprint is None:
        return None
    height = building.footprint.height_m
    if height <= 0:
        return None
    return building.storage_capacity_m3 * BUFFER_BALANCE_HEAD_M / height


def throttled_buffers(projection: Projection, game: GameData) -> list[ThrottledBuffer]:
    """Every fluid buffer in the world below its balance level, emptiest first.

    A level of zero counts: an empty buffer delivers nothing, and it is the same fault at
    its limit. ``stored_m3`` is the key that says a storage row is a fluid buffer at all.
    """
    out: list[ThrottledBuffer] = []
    for row in projection.get("storage", ()):
        level = row.get("stored_m3")
        if level is None:
            continue
        cls = row.get("cls", "")
        need = balance_level_m3(game, cls)
        if need is None or level >= need:
            continue
        out.append(
            ThrottledBuffer(
                instance=instance_leaf(row.get("instance", "")),
                cls=cls,
                fluid=row.get("fluid"),
                stored_m3=float(level),
                balance_m3=need,
                pos=row.get("pos"),
            )
        )
    return sorted(out, key=lambda b: b.share)


def unwired_pumps(projection: Projection, graph: FactoryGraph) -> tuple[list[str], int]:
    """Pipeline pumps no wire reaches, and how many pumps this could not see.

    The second number is the guard on the first: the graph's actor list is cut from EDGES,
    so a pump coupled to nothing at all is absent from it and would otherwise be counted as
    wired. ``building_counts`` is the world's own census and settles the difference.
    """
    counts = projection.get("building_counts") or {}
    standing = sum(int(counts.get(cls, 0)) for cls in PUMP_CLASSES)
    seen = [name for name, cls in graph.cls.items() if cls in PUMP_CLASSES]
    unwired = sorted(name for name in seen if not graph.neighbours(name, "power"))
    return unwired, max(0, standing - len(seen))

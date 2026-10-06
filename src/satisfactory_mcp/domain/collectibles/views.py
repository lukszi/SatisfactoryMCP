"""The shapes this package builds: a map placement, what this save says of it, the census.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict

from ...core.jsontypes import JsonObject

__all__ = [
    "CensusRow",
    "CollectedSummary",
    "LabelledCensusRow",
    "MapPlacement",
    "NamedActor",
    "Placement",
]

#: One row of ``data/world_collectibles.json``: a placement the map itself makes.
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
        "state": str,
        "looted": NotRequired[bool],
        "unlock_cost": NotRequired[JsonObject],
        "contents": NotRequired[JsonObject],
        "hazard": NotRequired[JsonObject],
        "attached_to": NotRequired[str],
    },
)


class Placement(TypedDict):
    """A map placement and what THIS save says about it; ``pos`` in centimetres."""

    category: str
    cls: str
    name: str
    cell: str
    pos: tuple[float, float, float]
    collected: bool
    observed: str | None
    looted: bool | None
    contents: JsonObject | None
    unlock_cost: JsonObject | None
    hazard: JsonObject
    distance_m: NotRequired[float]


class CensusRow(TypedDict):
    """One category: placed by the map, collected by this save, and how much is observed."""

    category: str
    cls: str
    placed: int
    remaining: int | None
    collected: int
    looted_and_standing: int
    standing: int
    never_streamed: int
    gone_in_a_later_save: int
    unstated: int
    state_tracked: bool
    pedestal_of: str | None
    note: str


class LabelledCensusRow(CensusRow):
    label: str
    spoiler: bool


class NamedActor(TypedDict):
    """A destroyed actor the save-only census can only name."""

    name: str
    cell: str
    pos: None


class CollectedSummary(TypedDict):
    """What this save records as collected. ``source`` is ``map`` or ``save-only``; the map
    keys are absent from a save-only one, and ``group``/``actors`` answer a group asked for."""

    total: int
    cells: int
    source: str
    groups: NotRequired[dict[str, int]]
    resolved: NotRequired[int]
    unresolved: NotRequired[int]
    unresolved_stems: NotRequired[dict[str, int]]
    census: NotRequired[list[CensusRow]]
    other: NotRequired[dict[str, int]]
    error: NotRequired[str]
    group: NotRequired[str]
    actors: NotRequired[list[Placement] | list[NamedActor]]

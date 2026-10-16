"""The wire shapes of the Progress roadmap: the gate, the next steps and the alternates."""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "AlternateRow",
    "AlternatesView",
    "ChainFigures",
    "GateInput",
    "GateMaker",
    "GatePart",
    "GateView",
    "NextStep",
    "ShortItem",
]


class GateMaker(TypedDict):
    """A named factory with a nameplate surplus of the item, per minute at saved clocks."""

    factory: str
    per_min: float


class GateInput(TypedDict):
    """One input of a part's recipe: per part made, needed for what is short, held."""

    item: str
    name: str
    per_unit: float
    needed: float
    held: float
    made_per_min: float
    makers: list[GateMaker]


class GatePart(TypedDict):
    """One part the target phase still bills.

    ``made_per_min`` is the world's nameplate surplus (named factories or not);
    ``need_per_min`` what delivers ``short`` in the horizon; ``machines`` and ``mw`` size the
    whole chain at that rate (null when nothing is short or no plan makes it). ``unlock`` is
    the milestone or MAM node granting a locked recipe.
    """

    item: str
    name: str
    owed: float
    held: float
    short: float
    recipe: str | None
    machine: str | None
    recipe_unlocked: bool
    unlock: str | None
    unlock_system: str | None
    made_per_min: float
    makers: list[GateMaker]
    need_per_min: float
    machines: int | None
    mw: float | None
    inputs: list[GateInput]


class ShortItem(TypedDict):
    """An item an unlock's bill is short of, and by how much."""

    item: str
    name: str
    short: float


class NextStep(TypedDict):
    """One ranked step. ``kind``: make (an item no machine makes, for unlocks), line (a
    part with an unlocked recipe nothing makes), grow (a part made slower than the horizon
    needs), unlock (a milestone or MAM node a part's recipe waits on). ``after`` holds the
    1-based numbers of listed steps this one waits on; ``parts`` the bill parts it opens."""

    kind: str
    item: str | None
    name: str
    amount: float
    per_min: float
    made_per_min: float
    system: str | None
    status: str | None
    for_unlocks: list[str]
    parts: list[str]
    after: list[int]
    inputs_held: bool | None
    short: list[ShortItem]


class GateView(TypedDict):
    """``phase`` is the target phase number, null on a save with none; ``solves`` the
    planner solves this answer took."""

    phase: int | None
    horizon_h: float
    parts: list[GatePart]
    steps: list[NextStep]
    solves: int


class ChainFigures(TypedDict):
    machines: int
    mw: float
    raw: float


class AlternateRow(TypedDict):
    """An alternate for an item the bill's chains make, and what having it changes in the
    whole bill at the horizon's rates: whole machines, nameplate MW, raw per minute (water
    left out). ``source`` is unlocked, drive (offered by ``drive`` beside ``other``) or pool
    (on no drive on hand yet)."""

    recipe: str
    name: str
    item: str
    item_name: str
    machine: str | None
    source: str
    drive: int | None
    other: str | None
    machines: int
    mw: float
    raw: float
    parts: list[str]


class AlternatesView(TypedDict):
    """``base`` is the bill's chains with the save's recipes and every default one."""

    phase: int | None
    horizon_h: float
    base: ChainFigures | None
    alternates: list[AlternateRow]
    solves: int

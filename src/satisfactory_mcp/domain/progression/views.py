"""The shapes this package builds: the progression summary, phases, gates and both budgets.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "GateCost",
    "PhaseRequirements",
    "PhaseRow",
    "ProgressionSummary",
    "ResearchGate",
    "ShardBudget",
    "ShardHolder",
    "SloopBudget",
    "SloopHolding",
    "SlugRow",
]


class ProgressionSummary(TypedDict):
    """``WorldState.progression()`` verbatim, on the same terms as ``PowerReport``.

    ``game_phase`` and ``target_phase`` are ``null`` on the pre-1.0 saves that carry no
    phase at all; ``highest_complete_tier`` is ``null`` when not one tier is finished, which
    is different from tier 0 and there is no tier 0.

    ``milestones_by_tier`` is keyed by the tier NUMBER, which JSON spells as a string, and is
    left an open map: the tiers are the game's, and a game update adds one.
    """

    game_phase: str | None
    target_phase: str | None
    phase_costs_remaining: dict[str, dict[str, int]]
    milestones_by_tier: dict[int, str]
    highest_complete_tier: int | None
    purchased_schematics: int
    available_recipes: int


class SlugRow(TypedDict):
    item: str
    name: str
    held: float
    each: float
    shards: float


class PhaseRow(TypedDict):
    """One stored phase cost: the frozen bill, what is still owed, and how far to trust it.

    ``stale`` is ``usable``, ``derived``, ``complete``, ``stale`` or ``unmapped``
    (save-projection.md §6.4); ``phase`` is ``None`` for an ``egp`` with no known phase.
    """

    egp: str
    phase: str | None
    outstanding: dict[str, float]
    snapshot: dict[str, float]
    paid_applied: dict[str, float]
    complete: list[str]
    stale: str


class PhaseRequirements(TypedDict):
    current_phase: str
    target_phase: str
    paid_off_target: dict[str, float]
    phases: list[PhaseRow]


class GateCost(TypedDict):
    item: str
    name: str
    need: float
    have: float


class ResearchGate(TypedDict):
    """The schematic in front of a capability, its bill against stock, and what blocks it."""

    capability: str
    schematic: str
    schematic_name: str
    kind: str
    cost: list[GateCost]
    short: list[GateCost]
    affordable: bool
    blocked_by: list[str]


class ShardHolder(TypedDict):
    """A building with a shard slotted or a clock that needs one; ``idle`` slots go unused."""

    instance: str
    cls: str
    clock: float
    slotted: float
    needed: int
    idle: float


class ShardBudget(TypedDict):
    """Power Shards held, committed and free; ``measured`` false means committed is unknown."""

    shard_items: dict[str, float]
    slugs: list[SlugRow]
    by_place: dict[str, dict[str, float]]
    craftable: float
    potential: float
    free: float
    committed: float
    owned: float
    holders: list[ShardHolder]
    slots_per_building: int
    measured: bool


class SloopHolding(TypedDict):
    """A building with Somersloops slotted: the plan model's ``boost`` beside the save's."""

    instance: str
    cls: str
    name: str
    sloops: float
    boost: float | None
    boost_in_save: float | None


class SloopBudget(TypedDict):
    item: str
    free: float
    by_place: dict[str, float]
    committed: float
    owned: float
    holders: list[SloopHolding]
    mercer_spheres: float
    committed_measured: bool

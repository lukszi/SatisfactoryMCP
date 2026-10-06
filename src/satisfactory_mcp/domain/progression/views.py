"""The wire shapes this package builds: the progression summary and the slugs held.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["ProgressionSummary", "SlugRow"]


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

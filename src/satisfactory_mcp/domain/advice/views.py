"""The shapes this package builds: the hidden-advisories file and the options of one pass.

Why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["AdviceDoc", "AdviceOptions", "HiddenEntry"]


class HiddenEntry(TypedDict):
    """One dismissed or snoozed advisory (docs/advisors_contract.md §4). ``ids`` is null past
    the id cap; ``until_play_s`` and ``hours`` only for a snooze."""

    state: str
    ids: list[str] | None
    weight: float
    severity: str
    until_play_s: float | None
    hours: float | None
    by: dict[str, object]
    at: float
    rev: int


class AdviceDoc(TypedDict):
    schema: int
    version: int
    hidden: dict[str, HiddenEntry]


class AdviceOptions(TypedDict):
    """The shared settings one advisor pass reads."""

    biomass: bool
    headroom: str
    box_fed: bool

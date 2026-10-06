"""The wire shapes this package builds: machine states counted, and the floor tallies.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["FloorCounts", "StateCount"]


class StateCount(TypedDict):
    state: str
    count: int


class FloorCounts(TypedDict):
    """The shape of the answer before the rows. Nested; see ``FloorReport.counts``."""

    platforms: int
    bands: int
    runs: int
    violations: int
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``: open maps, so the domain's
    #: two vocabularies are not restated here.
    placements: dict[str, int]
    membership: dict[str, int]

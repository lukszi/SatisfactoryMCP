"""The wire shapes the solver builds: the overclock pick per row, and the grid's sources.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["OverclockOption", "OverclockRow", "PowerSource", "RowName"]


class OverclockOption(TypedDict):
    """A row's two builds: ``machines`` with the last at ``last_clock`` for ``shards``, or
    ``spread_machines`` at ``spread_clock``. ``pinned`` is the row's own choice ("last",
    "spread") or null to follow the plan; ``applied`` says the overclocked build is the one
    listed. ``without``: no shards were left; ``unused``: it costs more than spreading."""

    pinned: str | None
    applied: bool
    machines: int
    last_clock: float
    shards: int
    extra_mw: float
    spread_machines: int
    spread_clock: float
    without: bool
    unused: bool


class OverclockRow(TypedDict):
    label: str
    building: str
    machines: int
    instead: int
    last_clock: float
    shards: int
    extra_mw: float
    pinned: str | None
    applied: bool


class RowName(TypedDict):
    label: str
    building: str


class PowerSource(TypedDict):
    """One source of the grid mix: running MW and its price in points per MWh."""

    source: str
    mw: float
    price: float

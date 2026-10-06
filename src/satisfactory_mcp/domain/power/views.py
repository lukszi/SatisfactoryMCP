"""The wire shapes this package builds: one generator class, counted and summed.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["GeneratorTotal"]


class GeneratorTotal(TypedDict):
    """One generator class, counted and summed. A value of ``PowerSummary.by_generator``."""

    name: str
    count: int
    mw: float

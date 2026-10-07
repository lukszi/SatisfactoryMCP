"""The wire shapes of one item's alternates: every recipe, and what requiring it changes.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from ..stored.views import PlanOpBody, ResultDelta

__all__ = ["PlanAlternatesResponse", "SwapOption"]


class SwapOption(TypedDict):
    """One recipe for the item. ``status`` is in use, required, banned, available or locked;
    ``delta`` is null when ``solved`` is false (locked, or banned by a pattern)."""

    recipe_id: str
    name: str
    alternate: bool
    machine: str | None
    unlocked: bool | None
    spoiler: bool
    granted_by: list[str]
    status: str
    in_use: bool
    required: bool
    banned: bool
    banned_by: str | None
    solved: bool
    delta: ResultDelta | None
    require_ops: list[PlanOpBody]
    ban_ops: list[PlanOpBody]
    free_ops: list[PlanOpBody]


class PlanAlternatesResponse(TypedDict):
    """Every recipe for one item with what requiring it changes in the plan at ``rev``."""

    key: str
    rev: int
    item: str
    name: str
    head_feasible: bool
    head_machines: int
    head_mw_draw: float | None
    head_mw_net: float | None
    options: list[SwapOption]
    hidden: int
    text: str

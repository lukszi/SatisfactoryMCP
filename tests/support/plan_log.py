"""The actors, recipe and op values the plan log tests write with."""

from __future__ import annotations

from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)
BOLTED = "Recipe_Alternate_BoltedFrame_C"


def site_value(x: float) -> dict:
    """A valid ``site`` op value at ``x`` metres east."""
    return {"origin_m": [float(x), 0.0, None], "footprint_m": [40.0, 40.0]}


def head_bytes(plans: PlanLog, key: str) -> bytes:
    """The raw op log of plan ``key``, to show a refused write left it untouched."""
    return (plans.root / key / "ops.jsonl").read_bytes()

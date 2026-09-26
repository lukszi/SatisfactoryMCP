"""``/api/progress/milestones``: the ``milestones`` tool's ladder, every HUB milestone as a row.

The same ``SchematicLadder`` the MCP tool walks, priced against the same spendable stock, so
READY means the bill is covered and nothing about whether the tier is open. The dashboard it
feeds: docs/frontend_vision.md §8. Wire rules: docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.progression.ladder import SchematicLadder
from ..serial import _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


class ItemAmount(TypedDict):
    item: str
    name: str
    amount: float


class MilestoneRow(TypedDict):
    """``status`` is ``Rung.status``: DONE, BLOCKED, short or READY."""

    cls: str
    tier: int
    name: str
    status: str
    cost: list[ItemAmount]
    short: list[ItemAmount]
    unlocks: int
    blocked_by: list[str]


class TierRow(TypedDict):
    tier: int
    done: int
    total: int


class MilestonesResponse(TypedDict):
    """``highest_complete_tier`` is null when no tier is finished; there is no tier 0."""

    game_phase: str | None
    highest_complete_tier: int | None
    tiers: list[TierRow]
    milestones: list[MilestoneRow]


@router.get("/progress/milestones", response_model=MilestonesResponse)
def progress_milestones(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every HUB milestone with its bill, what stock is short of it, and what it unlocks."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    g = st.game
    ladder = SchematicLadder(game=g, unlocks=st.unlocks, inventory=st.inventory)
    rungs = sorted(ladder.rungs("EST_Milestone"), key=lambda r: (r.schematic.tier, r.schematic.name))

    tiers: dict[int, list[int]] = {}
    rows = []
    for rung in rungs:
        s = rung.schematic
        tally = tiers.setdefault(s.tier, [0, 0])
        tally[0] += rung.done
        tally[1] += 1
        rows.append(
            {
                "cls": s.cls,
                "tier": s.tier,
                "name": s.name,
                "status": rung.status,
                "cost": [
                    {"item": f.item, "name": g.item_name(f.item), "amount": f.amount}
                    for f in s.cost
                ],
                "short": [
                    {"item": m.item, "name": g.item_name(m.item), "amount": round(m.short_by, 1)}
                    for m in rung.missing
                ],
                "unlocks": len(st.unlocks.schematic_recipes(s)),
                "blocked_by": list(rung.blocked_by),
            }
        )

    prog = st.progression()
    return {
        "game_phase": prog["game_phase"],
        "highest_complete_tier": prog["highest_complete_tier"],
        "tiers": [{"tier": t, "done": d, "total": n} for t, (d, n) in sorted(tiers.items())],
        "milestones": rows,
    }

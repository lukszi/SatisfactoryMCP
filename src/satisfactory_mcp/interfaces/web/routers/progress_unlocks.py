"""``/api/progress/{milestones,mam,phase,harddrives}``: what the save has unlocked and what is next.

``milestones`` and ``mam`` walk the same ``SchematicLadder`` the MCP tools walk, priced against
the same spendable stock, so READY means the bill is covered and nothing about whether the tier
is open. ``phase`` and ``harddrives`` read the ``WorldState`` records ``phase_requirements`` and
``list_pending_hard_drive_choices`` read. The dashboard: docs/frontend_vision.md §8; spoilers:
§12.3 there. Handler names are operation_ids (wire rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata.constants import CAPABILITY_SCHEMATICS
from ....domain.progression.ladder import SchematicLadder
from ....domain.progression.phases import opened_tier, opening_phase, phase_number
from ..serial import ItemAmount, item_amounts, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


class MilestoneRow(TypedDict):
    """``status`` is ``Rung.status``: DONE, BLOCKED, short or READY.

    ``opens_at`` is the Space Elevator phase that opens a tier the save has not reached yet.
    """

    cls: str
    tier: int
    name: str
    status: str
    cost: list[ItemAmount]
    short: list[ItemAmount]
    unlocks: int
    blocked_by: list[str]
    opens_at: int | None
    spoiler: bool


class TierRow(TypedDict):
    tier: int
    done: int
    total: int
    spoiler: bool


class MilestonesResponse(TypedDict):
    """``highest_complete_tier`` is null when no tier is finished; there is no tier 0."""

    game_phase: str | None
    highest_complete_tier: int | None
    tiers: list[TierRow]
    milestones: list[MilestoneRow]


def _highest_reached_tier(rows: list[dict[str, Any]]) -> int:
    """The highest tier with a finished milestone, else the lowest tier there is."""
    done = [r["tier"] for r in rows if r["status"] == "DONE"]
    if done:
        return max(done)
    return min((r["tier"] for r in rows), default=0)


@router.get("/progress/milestones", response_model=MilestonesResponse)
def progress_milestones(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """Every HUB milestone with its bill, what stock is short of it, and what it unlocks.

    A tier above both the highest one with a finished milestone and the highest one the
    delivered Space Elevator phases open is a spoiler.
    """
    st = require_world(request, save, world)

    game = st.game
    ladder = SchematicLadder(game=game, unlocks=st.unlocks, inventory=st.inventory)
    rungs = sorted(
        ladder.rungs("EST_Milestone"), key=lambda r: (r.schematic.tier, r.schematic.name)
    )

    tiers: dict[int, list[int]] = {}
    rows = []
    for rung in rungs:
        schematic = rung.schematic
        tally = tiers.setdefault(schematic.tier, [0, 0])
        tally[0] += rung.done
        tally[1] += 1
        rows.append(
            {
                "cls": schematic.cls,
                "tier": schematic.tier,
                "name": schematic.name,
                "status": rung.status,
                "cost": item_amounts(game, ((f.item, f.amount) for f in schematic.cost)),
                "short": item_amounts(game, ((m.item, round(m.short_by, 1)) for m in rung.missing)),
                "unlocks": len(st.unlocks.schematic_recipes(schematic)),
                "blocked_by": list(rung.blocked_by),
            }
        )

    progression = st.progression()
    opened = opened_tier(progression["game_phase"])
    reached = _highest_reached_tier(rows)
    top = reached if opened is None else max(reached, opened)
    for row in rows:
        shut = opened is not None and row["tier"] > opened and row["status"] != "DONE"
        row["opens_at"] = opening_phase(row["tier"]) if shut else None
        row["spoiler"] = row["tier"] > top
    tier_rows = [
        {"tier": tier, "done": done, "total": total, "spoiler": tier > top}
        for tier, (done, total) in sorted(tiers.items())
    ]
    if spoilers is False:
        rows = [r for r in rows if not r["spoiler"]]
        tier_rows = [t for t in tier_rows if not t["spoiler"]]
    return {
        "game_phase": progression["game_phase"],
        "highest_complete_tier": progression["highest_complete_tier"],
        "tiers": tier_rows,
        "milestones": rows,
    }


class MamRow(TypedDict):
    """``status`` is DONE, RUNNING, TREE SHUT, BLOCKED, short or READY, as ``mam_research``."""

    cls: str
    name: str
    tree: str | None
    status: str
    running_s: float | None
    capability: str | None
    cost: list[ItemAmount]
    short: list[ItemAmount]
    unlocks: int
    blocked_by: list[str]
    spoiler: bool


class CapabilityRow(TypedDict):
    """``tree_shut`` is true while the research sits in a MAM tree not opened yet."""

    capability: str
    researched: bool
    schematic_name: str | None
    tree_shut: bool
    spoiler: bool


class MamResponse(TypedDict):
    """``knows_trees`` is false on a projection too old to list the opened trees."""

    knows_trees: bool
    capabilities: list[CapabilityRow]
    research: list[MamRow]


def _tree_name(tree: str | None) -> str | None:
    if not tree:
        return None
    core = tree.removeprefix("BPD_ResearchTree_").removesuffix("_C")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", core)


@router.get("/progress/mam", response_model=MamResponse)
def progress_mam(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """Every MAM node with the ``mam_research`` status, bill and shortfall.

    A node or capability in a tree not opened yet is a spoiler.
    """
    st = require_world(request, save, world)

    research = st.research
    gates = {v: k for k, v in CAPABILITY_SCHEMATICS.items()}
    ladder = SchematicLadder(game=st.game, unlocks=st.unlocks, inventory=st.inventory)
    rows = []
    for rung in ladder.rungs("EST_MAM"):
        schematic = rung.schematic
        running = research.ongoing.get(schematic.cls)
        status = research.status(rung)
        rows.append(
            {
                "cls": schematic.cls,
                "name": schematic.name,
                "tree": _tree_name(research.tree_of(schematic.cls)),
                "status": status,
                "running_s": None if running is None else float(running),
                "capability": gates.get(schematic.cls),
                "cost": item_amounts(st.game, ((f.item, f.amount) for f in schematic.cost)),
                "short": item_amounts(
                    st.game, ((m.item, round(m.short_by, 1)) for m in rung.missing)
                ),
                "unlocks": len(st.unlocks.schematic_recipes(schematic)),
                "blocked_by": list(rung.blocked_by),
                "spoiler": status == "TREE SHUT",
            }
        )

    capabilities = []
    for name, cls in CAPABILITY_SCHEMATICS.items():
        schematic = st.game.schematics.get(cls)
        shut = research.tree_locked(cls)
        capabilities.append(
            {
                "capability": name,
                "researched": st.has_capability(name),
                "schematic_name": schematic.name if schematic else None,
                "tree_shut": shut,
                "spoiler": shut,
            }
        )
    if spoilers is False:
        rows = [r for r in rows if not r["spoiler"]]
        capabilities = [c for c in capabilities if not c["spoiler"]]
    return {"knows_trees": research.knows_trees, "capabilities": capabilities, "research": rows}


class HaveRow(TypedDict):
    item: str
    name: str
    amount: float
    have: float
    short: float


class PhaseRow(TypedDict):
    """``trust`` is the domain's ``stale`` flag: usable, derived, complete, stale or unmapped."""

    phase: str | None
    legacy_key: str
    trust: str
    outstanding: list[HaveRow]
    complete: list[str]
    spoiler: bool


class PhaseResponse(TypedDict):
    """``deliverable`` is null when no row belongs to the target phase."""

    current_phase: str | None
    target_phase: str | None
    delivered: list[ItemAmount]
    deliverable: bool | None
    phases: list[PhaseRow]


@router.get("/progress/phase", response_model=PhaseResponse)
def progress_phase(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """The Space Elevator record ``phase_requirements`` reads, joined to spendable stock.

    A phase numbered past the target phase is a spoiler, and every phase is one on a save
    with no target phase.
    """
    st = require_world(request, save, world)

    req = st.phase_requirements()
    stock = st.stock()
    target = phase_number(req["target_phase"])
    phases = []
    deliverable = None
    for row in req["phases"]:
        items = []
        for item, need in sorted(row["outstanding"].items(), key=lambda kv: -kv[1]):
            have = float(stock.get(item, 0.0))
            items.append(
                {
                    "item": item,
                    "name": st.game.item_name(item),
                    "amount": float(need),
                    "have": have,
                    "short": round(max(0.0, float(need) - have), 1),
                }
            )
        if row["phase"] and row["phase"] == req["target_phase"]:
            deliverable = all(i["short"] <= 0 for i in items)
        phases.append(
            {
                "phase": row["phase"],
                "legacy_key": row["egp"],
                "trust": row["stale"],
                "outstanding": items,
                "complete": [st.game.item_name(i) for i in row["complete"]],
                "spoiler": target is None or (phase_number(row["phase"]) or 0) > target,
            }
        )
    if spoilers is False:
        phases = [p for p in phases if not p["spoiler"]]
    return {
        "current_phase": req["current_phase"] or None,
        "target_phase": req["target_phase"] or None,
        "delivered": item_amounts(st.game, sorted(req["paid_off_target"].items())),
        "deliverable": deliverable,
        "phases": phases,
    }


class GrantedRecipe(TypedDict):
    cls: str
    name: str
    machine: str | None
    products: list[ItemAmount]


class DriveOption(TypedDict):
    """``slots`` is the inventory slots an option grants instead of recipes; 0 for most."""

    schematic: str
    name: str
    slots: int
    recipes: list[GrantedRecipe]


class DriveRow(TypedDict):
    hard_drive_id: int | None
    rerolls_left: int
    options: list[DriveOption]


class HardDrivesResponse(TypedDict):
    """``spare`` is unanalysed drives on hand; ``last_used`` the drive analysed most recently."""

    spare: int
    last_used: int | None
    drives: list[DriveRow]


@router.get("/progress/harddrives", response_model=HardDrivesResponse)
def progress_harddrives(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """``list_pending_hard_drive_choices``: each unclaimed drive's two options and rerolls."""
    st = require_world(request, save, world)

    game = st.game
    drives = []
    for offer in st.hard_drive_offers:
        options = []
        for opt in offer.options:
            recipes = []
            for recipe in opt["recipes"]:
                machine = game.machine(recipe)
                recipes.append(
                    {
                        "cls": recipe.cls,
                        "name": recipe.name,
                        "machine": machine.name if machine else None,
                        "products": item_amounts(
                            game, ((f.item, round(f.per_min, 2)) for f in recipe.products)
                        ),
                    }
                )
            options.append(
                {
                    "schematic": opt["schematic"],
                    "name": opt["name"],
                    "slots": int(opt["slots"] or 0),
                    "recipes": recipes,
                }
            )
        drives.append(
            {
                "hard_drive_id": offer.hard_drive_id,
                "rerolls_left": int(offer.rerolls_left),
                "options": options,
            }
        )
    return {
        "spare": int(st.spare_hard_drives()),
        "last_used": st.harddrive_desk.last_used_hard_drive_id,
        "drives": drives,
    }

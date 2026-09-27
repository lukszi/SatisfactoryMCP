"""``/api/progress/*``: the progression tools' facts, one route per tool.

``milestones`` and ``mam`` walk the same ``SchematicLadder`` the MCP tools walk, priced against
the same spendable stock, so READY means the bill is covered and nothing about whether the
tier is open. ``phase``, ``shards``, ``sloops`` and ``harddrives`` read the same ``WorldState``
records as ``phase_requirements``, ``power_shards``, ``somersloops`` and
``list_pending_hard_drive_choices``. The dashboard they feed: docs/frontend_vision.md §8 and
"Phase 4: Progress". Wire rules: docs/web-wire.md. ``spoiler`` on a row and ``?spoilers=``:
docs/frontend_vision.md §12.3.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata.constants import CAPABILITY_SCHEMATICS, max_clock
from ....domain.progression.ladder import SchematicLadder
from ....domain.progression.phases import opened_tier, opening_phase, phase_number
from ....domain.world.state import WorldState
from ..serial import _fail, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


class ItemAmount(TypedDict):
    item: str
    name: str
    amount: float


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


def _reach(rows: list[dict[str, Any]]) -> int:
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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    g = st.game
    ladder = SchematicLadder(game=g, unlocks=st.unlocks, inventory=st.inventory)
    rungs = sorted(
        ladder.rungs("EST_Milestone"), key=lambda r: (r.schematic.tier, r.schematic.name)
    )

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
    opened = opened_tier(prog["game_phase"])
    top = _reach(rows) if opened is None else max(_reach(rows), opened)
    for row in rows:
        shut = opened is not None and row["tier"] > opened and row["status"] != "DONE"
        row["opens_at"] = opening_phase(row["tier"]) if shut else None
        row["spoiler"] = row["tier"] > top
    tier_rows = [
        {"tier": t, "done": d, "total": n, "spoiler": t > top}
        for t, (d, n) in sorted(tiers.items())
    ]
    if spoilers is False:
        rows = [r for r in rows if not r["spoiler"]]
        tier_rows = [t for t in tier_rows if not t["spoiler"]]
    return {
        "game_phase": prog["game_phase"],
        "highest_complete_tier": prog["highest_complete_tier"],
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


def _amounts(st: WorldState, pairs: Any) -> list[ItemAmount]:
    return [{"item": i, "name": st.game.item_name(i), "amount": float(a)} for i, a in pairs]


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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    research = st.research
    gates = {v: k for k, v in CAPABILITY_SCHEMATICS.items()}
    ladder = SchematicLadder(game=st.game, unlocks=st.unlocks, inventory=st.inventory)
    rows = []
    for rung in ladder.rungs("EST_MAM"):
        s = rung.schematic
        running = research.ongoing.get(s.cls)
        status = research.status(rung)
        rows.append(
            {
                "cls": s.cls,
                "name": s.name,
                "tree": _tree_name(research.tree_of(s.cls)),
                "status": status,
                "running_s": None if running is None else float(running),
                "capability": gates.get(s.cls),
                "cost": _amounts(st, ((f.item, f.amount) for f in s.cost)),
                "short": _amounts(st, ((m.item, round(m.short_by, 1)) for m in rung.missing)),
                "unlocks": len(st.unlocks.schematic_recipes(s)),
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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

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
        "delivered": _amounts(st, sorted(req["paid_off_target"].items())),
        "deliverable": deliverable,
        "phases": phases,
    }


class NamedAmount(TypedDict):
    name: str
    amount: float


class PlaceRow(TypedDict):
    """``place`` is carried, storage or depot."""

    place: str
    items: list[NamedAmount]


class SlugRow(TypedDict):
    item: str
    name: str
    held: float
    each: float
    shards: float


class ShardHolder(TypedDict):
    instance: str
    name: str | None
    clock: float
    slotted: int
    needed: int
    idle: int
    x_m: float | None
    y_m: float | None


class ShardsResponse(TypedDict):
    """``measured`` false means ``committed`` is unknown rather than zero."""

    measured: bool
    free: float
    craftable: float
    potential: float
    committed: int
    owned: float
    per_shard: float
    max_clock: float
    slots_per_building: int
    idle: int
    slugs: list[SlugRow]
    by_place: list[PlaceRow]
    holders: list[ShardHolder]


def _positions(st: WorldState) -> dict[str, dict[str, float | None]]:
    out = {}
    for record in st.overclock.records:
        xyz = _xyz(record.get("pos"))
        out[str(record.get("instance", "")).rsplit(".", 1)[-1]] = {
            "x_m": xyz["x_m"],
            "y_m": xyz["y_m"],
        }
    return out


def _nowhere() -> dict[str, float | None]:
    return {"x_m": None, "y_m": None}


@router.get("/progress/shards", response_model=ShardsResponse)
def progress_shards(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """The ``power_shards`` budget: free, craftable from slugs, committed, and who holds them."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    budget = st.shard_budget()
    per_shard = max(budget["shard_items"].values()) if budget["shard_items"] else 0.0
    at = _positions(st)
    return {
        "measured": budget["measured"],
        "free": float(budget["free"]),
        "craftable": float(budget["craftable"]),
        "potential": float(budget["potential"]),
        "committed": int(budget["committed"]),
        "owned": float(budget["owned"]),
        "per_shard": float(per_shard),
        "max_clock": float(max_clock(per_shard)),
        "slots_per_building": int(budget["slots_per_building"]),
        "idle": sum(int(h["idle"]) for h in budget["holders"]),
        "slugs": [
            {
                "item": s["item"],
                "name": s["name"],
                "held": float(s["held"]),
                "each": float(s["each"]),
                "shards": float(s["shards"]),
            }
            for s in budget["slugs"]
        ],
        "by_place": [
            {
                "place": place,
                "items": [{"name": k, "amount": float(v)} for k, v in sorted(held.items())],
            }
            for place, held in budget["by_place"].items()
        ],
        "holders": [
            {
                "instance": h["instance"],
                "name": st.game.building_name(h["cls"]),
                "clock": float(h["clock"]),
                "slotted": int(h["slotted"]),
                "needed": int(h["needed"]),
                "idle": int(h["idle"]),
                **at.get(h["instance"], _nowhere()),
            }
            for h in budget["holders"]
        ],
    }


class SloopHolder(TypedDict):
    """``boost`` is the plan model's multiplier, ``boost_in_save`` the save's own."""

    instance: str
    name: str
    sloops: float
    boost: float | None
    boost_in_save: float | None
    x_m: float | None
    y_m: float | None


class SloopsResponse(TypedDict):
    """``amplifier_researched`` false means no sloop can go into a machine yet.

    ``amplifier_tree_shut`` is true while that research sits in a MAM tree not opened yet, and
    ``amplifier_spoiler`` while it is both unresearched and in that shut tree.
    """

    measured: bool
    free: float
    committed: float
    owned: float
    mercer_spheres: float
    by_place: list[NamedAmount]
    amplifier_researched: bool
    amplifier_research: str | None
    amplifier_tree_shut: bool
    amplifier_spoiler: bool
    amplifier_cost: list[ItemAmount]
    holders: list[SloopHolder]


@router.get("/progress/sloops", response_model=SloopsResponse)
def progress_sloops(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    spoilers: bool | None = None,
) -> Any:
    """The ``somersloops`` budget: free, slotted and owned, and which machines hold them.

    With ``spoilers=0`` a spoiler amplifier research loses its name and bill.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    budget = st.sloop_budget()
    gate = st.research_gate("production_boost")
    shut = st.research.tree_locked(CAPABILITY_SCHEMATICS["production_boost"])
    spoiler = gate is not None and shut
    hide = spoiler and spoilers is False
    at = _positions(st)
    return {
        "measured": budget["committed_measured"],
        "free": float(budget["free"]),
        "committed": float(budget["committed"]),
        "owned": float(budget["owned"]),
        "mercer_spheres": float(budget["mercer_spheres"]),
        "by_place": [{"name": k, "amount": float(v)} for k, v in budget["by_place"].items()],
        "amplifier_researched": gate is None,
        "amplifier_research": gate["schematic_name"] if gate and not hide else None,
        "amplifier_tree_shut": shut,
        "amplifier_spoiler": spoiler,
        "amplifier_cost": _amounts(st, ((r["item"], r["need"]) for r in gate["cost"]))
        if gate and not hide
        else [],
        "holders": [
            {
                "instance": h["instance"],
                "name": h["name"],
                "sloops": float(h["sloops"]),
                "boost": None if h["boost"] is None else float(h["boost"]),
                "boost_in_save": None if h["boost_in_save"] is None else float(h["boost_in_save"]),
                **at.get(h["instance"], _nowhere()),
            }
            for h in budget["holders"]
        ],
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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    g = st.game
    drives = []
    for offer in st.hard_drive_offers:
        options = []
        for opt in offer.options:
            recipes = []
            for r in opt["recipes"]:
                machine = g.machine(r)
                recipes.append(
                    {
                        "cls": r.cls,
                        "name": r.name,
                        "machine": machine.name if machine else None,
                        "products": _amounts(
                            st, ((f.item, round(f.per_min, 2)) for f in r.products)
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

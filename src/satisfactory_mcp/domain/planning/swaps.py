"""What requiring, banning or freeing each recipe for one item would do to a stored plan.

docs/planner-p3_contract.md §5.3 is the specification. The web drawer and ``alternates_for_item``
both call ``swap_deltas``; the deltas are facts and the options are never ordered by them.
"""

from __future__ import annotations

from ...core.gamedata import search
from ...core.gamedata.model import GameData, Recipe
from ...core.gamedata.unlocks import granted_by
from ..world.state import WorldState
from . import manage, summary
from .planlog import PlanArgs, PlanState
from .scenario import match_recipes

__all__ = ["STATUS_ORDER", "makers", "swap_deltas"]

STATUS_ORDER = ("in use", "required", "available", "banned", "locked")


def makers(g: GameData, item: str) -> list[Recipe]:
    """Recipes whose first product is ``item``; every maker when none has it first."""
    every = search.makers_of(g, item)
    first = [r for r in every if r.products and r.products[0].item == item]
    return first or every


def _literal(g: GameData, member: str, recipe: Recipe) -> bool:
    return member == recipe.cls or member.strip().casefold() == recipe.name.casefold()


def _banned_by(g: GameData, banned: list[str], recipe: Recipe) -> tuple[str | None, bool]:
    for member in banned:
        if _literal(g, member, recipe):
            return member, False
        if recipe.cls in match_recipes(g, member, [recipe.cls]):
            return member, True
    return None, False


def _first(g: GameData, rid: str) -> str | None:
    recipe = g.recipes.get(rid)
    return recipe.products[0].item if recipe is not None and recipe.products else None


def _op(kind: str, field: str, member: str) -> dict:
    return {"op": kind, "field": field, "member": member}


def _ops(g: GameData, args: PlanArgs, recipe: Recipe, item: str) -> tuple[list, list, list]:
    required = [m for m in args.required if _literal(g, m, recipe)]
    literal_bans = [m for m in args.banned if _literal(g, m, recipe)]
    others = [m for m in args.required if m not in required and _first(g, m) == item]
    require = [_op("add", "required", recipe.cls)]
    require += [_op("remove", "required", m) for m in others]
    require += [_op("remove", "banned", m) for m in literal_bans]
    ban = [_op("add", "banned", recipe.cls)] + [_op("remove", "required", m) for m in required]
    free = [_op("remove", "required", m) for m in required]
    free += [_op("remove", "banned", m) for m in literal_bans]
    return require, ban, free


def _applied(args: PlanArgs, ops: list[dict]) -> dict:
    raw = args.to_dict()
    for op in ops:
        members = list(raw[op["field"]])
        if op["op"] == "add":
            if op["member"] not in members:
                members.append(op["member"])
        else:
            members = [m for m in members if m != op["member"]]
        raw[op["field"]] = members
    return PlanArgs.from_dict(raw).kwargs()


def _status(unlocked: bool, required: bool, in_use: bool, banned: bool) -> str:
    if not unlocked:
        return "locked"
    if required:
        return "required"
    if in_use:
        return "in use"
    if banned:
        return "banned"
    return "available"


def _counts(options: list[dict], hidden: int) -> str:
    tally: dict[str, int] = {}
    for option in options:
        tally[option["status"]] = tally.get(option["status"], 0) + 1
    parts = [f"{tally[s]} {s}" for s in STATUS_ORDER if s != "available" and tally.get(s)]
    if hidden:
        parts.append(f"{hidden} locked hidden")
    return f" ({', '.join(parts)})" if parts else ""


def swap_deltas(
    g: GameData, st: WorldState, state: PlanState, item_id: str, spoilers: bool = True
) -> dict:
    """Every recipe making ``item_id`` with what requiring it would change in ``state``."""
    head = summary.solve_summary(g, st, state.kwargs())
    in_use = {r.get("recipe_id") for r in head.get("rows") or ()}
    have = st.available_recipe_ids
    args = state.args
    options, hidden = [], 0
    for recipe in makers(g, item_id):
        unlocked = recipe.cls in have
        if not unlocked and not spoilers:
            hidden += 1
            continue
        required = any(_literal(g, m, recipe) for m in args.required)
        banned_by, by_pattern = _banned_by(g, list(args.banned), recipe)
        require, ban, free = _ops(g, args, recipe, item_id)
        solved = unlocked and not by_pattern
        delta = None
        if solved:
            delta = manage.result_delta(head, summary.solve_summary(g, st, _applied(args, require)))
        building = g.machine(recipe)
        options.append(
            {
                "recipe_id": recipe.cls,
                "name": recipe.name,
                "alternate": recipe.is_alternate,
                "machine": building.name if building else None,
                "unlocked": unlocked,
                "spoiler": not unlocked,
                "granted_by": granted_by(g, recipe),
                "status": _status(unlocked, required, recipe.cls in in_use, banned_by is not None),
                "in_use": recipe.cls in in_use,
                "required": required,
                "banned": banned_by is not None,
                "banned_by": banned_by,
                "solved": solved,
                "delta": delta,
                "require_ops": require,
                "ban_ops": [] if by_pattern else ban,
                "free_ops": [] if by_pattern else free,
            }
        )
    rank = {s: i for i, s in enumerate(STATUS_ORDER)}
    options.sort(key=lambda o: (rank[o["status"]], o["alternate"], o["name"].casefold()))
    name = g.item_name(item_id)
    feasible = bool(head.get("feasible"))
    return {
        "key": state.key,
        "rev": state.rev,
        "item": item_id,
        "name": name,
        "head_feasible": feasible,
        "head_machines": int(head.get("machines") or 0),
        "head_mw_draw": head.get("mw_draw"),
        "head_mw_net": head.get("mw_net"),
        "options": options,
        "hidden": hidden,
        "text": f"recipes for {name} in “{state.name}” v{state.rev}: "
        f"{len(options)}{_counts(options, hidden)}",
    }

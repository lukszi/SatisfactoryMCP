"""What requiring, banning or freeing each recipe for one item would do to a stored plan.

docs/planner-p3_contract.md §5.3 is the specification. The web drawer and ``alternates_for_item``
both call ``swap_deltas``; the deltas are facts and the options are never ordered by them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ....core.gamedata import search
from ....core.gamedata.model import GameData, Recipe
from ....core.gamedata.search import match_recipes
from ....core.gamedata.unlocks import granted_by
from ....core.jsontypes import JsonObject
from ...world.state import WorldState
from ..readout import summary
from ..stored import manage
from ..stored.plan_args import PlanArgs
from ..stored.planlog import PlanState
from ..stored.views import PlanOpBody
from .views import PlanAlternatesResponse, SwapOption

__all__ = ["STATUS_ORDER", "primary_makers", "replaced_required", "swap_deltas"]

STATUS_ORDER = ("in use", "required", "available", "banned", "locked")


def primary_makers(g: GameData, item: str) -> list[Recipe]:
    """Recipes whose main product is ``item``; every maker when none has it first."""
    every = search.makers_of(g, item)
    first = [r for r in every if r.main_product == item]
    return first or every


def _names_recipe(member: str, recipe: Recipe) -> bool:
    """Whether a required or banned entry names ``recipe`` itself, by id or exact name."""
    return member == recipe.cls or member.strip().casefold() == recipe.name.casefold()


def _banned_by(
    g: GameData, banned: list[str], recipe: Recipe, pool: list[str]
) -> tuple[str | None, bool]:
    """The ban entry that removes ``recipe``, and whether it is a pattern rather than its name."""
    scope = pool if recipe.cls in pool else [*pool, recipe.cls]
    literal = None
    for member in banned:
        if recipe.cls not in match_recipes(g, member, scope):
            continue
        if not _names_recipe(member, recipe):
            return member, True
        literal = literal or member
    return literal, False


def _main_product(g: GameData, rid: str) -> str | None:
    recipe = g.recipes.get(rid)
    return recipe.main_product if recipe is not None else None


def _op(kind: str, field: str, member: str) -> PlanOpBody:
    return {"op": kind, "field": field, "member": member}


def _swap_ops(
    g: GameData, args: PlanArgs, recipe: Recipe, item: str
) -> tuple[list[PlanOpBody], list[PlanOpBody], list[PlanOpBody]]:
    """The require, ban and free ops for ``recipe`` against the plan's arguments."""
    required = [m for m in args.required if _names_recipe(m, recipe)]
    literal_bans = [m for m in args.banned if _names_recipe(m, recipe)]
    others = [m for m in args.required if m not in required and _main_product(g, m) == item]
    require = [_op("add", "required", recipe.cls)]
    require += [_op("remove", "required", m) for m in others]
    require += [_op("remove", "banned", m) for m in literal_bans]
    ban = [_op("add", "banned", recipe.cls)] + [_op("remove", "required", m) for m in required]
    free = [_op("remove", "required", m) for m in required]
    free += [_op("remove", "banned", m) for m in literal_bans]
    return require, ban, free


def replaced_required(
    g: GameData, head: PlanState, item: str, ops: Sequence[Mapping[str, object]]
) -> list[JsonObject]:
    """The ``remove required`` ops that make a require of ``item`` in ``ops`` replace every
    other required recipe for it in ``head`` (contract C3), whatever the page last saw."""
    added = {
        op.get("member")
        for op in ops
        if op.get("op") == "add"
        and op.get("field") == "required"
        and _main_product(g, str(op.get("member"))) == item
    }
    if not added:
        return []
    return [
        {"op": "remove", "field": "required", "member": m}
        for m in head.args.required
        if m not in added and _main_product(g, m) == item
    ]


def _applied(args: PlanArgs, ops: list[PlanOpBody]) -> dict:
    """``args`` with each set op applied, as solve arguments."""
    raw = args.to_dict()
    for op in ops:
        field, member = op.get("field", ""), op.get("member")
        members = list(raw[field])
        if op.get("op") == "add":
            if member not in members:
                members.append(member)
        else:
            members = [m for m in members if m != member]
        raw[field] = members
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


def _counts(options: list[SwapOption], hidden: int) -> str:
    tally: dict[str, int] = {}
    for option in options:
        tally[option["status"]] = tally.get(option["status"], 0) + 1
    parts = [f"{tally[s]} {s}" for s in STATUS_ORDER if s != "available" and tally.get(s)]
    if hidden:
        parts.append(f"{hidden} locked hidden")
    return f" ({', '.join(parts)})" if parts else ""


def swap_deltas(
    g: GameData, st: WorldState, stored: PlanState, item_id: str, spoilers: bool = True
) -> PlanAlternatesResponse:
    """Every recipe making ``item_id`` with what requiring it would change in ``stored``."""
    head = summary.solve_summary(g, st, stored.kwargs())
    in_use = {r["recipe_id"] for r in head["rows"]}
    have = st.available_recipe_ids
    args = stored.args
    pool = [r.cls for r in st.unlocked_recipes("part")]
    options: list[SwapOption] = []
    hidden = 0
    for recipe in primary_makers(g, item_id):
        unlocked = recipe.cls in have
        if not unlocked and not spoilers:
            hidden += 1
            continue
        required = any(_names_recipe(m, recipe) for m in args.required)
        banned_by, by_pattern = _banned_by(g, list(args.banned), recipe, pool)
        require, ban, free = _swap_ops(g, args, recipe, item_id)
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
        "key": stored.key,
        "rev": stored.rev,
        "item": item_id,
        "name": name,
        "head_feasible": feasible,
        "head_machines": int(head.get("machines") or 0),
        "head_mw_draw": head.get("mw_draw"),
        "head_mw_net": head.get("mw_net"),
        "options": options,
        "hidden": hidden,
        "text": f"recipes for {name} in “{stored.name}” v{stored.rev}: "
        f"{len(options)}{_counts(options, hidden)}",
    }

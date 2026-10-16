"""Which alternates would cut most from the chains the target phase still needs.

For each alternate whose item the bill's chains make, two solves of the whole bill at the
horizon's rates: the pool with the alternate required for its item, and the pool without it.
The difference is what having it is worth. docs/progress_roadmap.md, "Alternates".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData, Recipe
from .bill import target_bill
from .chains import ChainCost, Chains
from .gate import need_rates
from .views import AlternateRow, AlternatesView, ChainFigures

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = ["alternates_view"]

_PREFIX = "Alternate: "


def _figures(cost: ChainCost) -> ChainFigures:
    return {"machines": cost.machines, "mw": cost.mw, "raw": cost.raw}


def _drives(st: WorldState) -> dict[str, tuple[int | None, str | None]]:
    """Recipe id -> the drive on hand offering it and the drive's other option."""
    out: dict[str, tuple[int | None, str | None]] = {}
    for offer in st.hard_drive_offers:
        for opt in offer.options:
            others = [o["name"].removeprefix(_PREFIX) for o in offer.options if o is not opt]
            for recipe in opt["recipes"]:
                out.setdefault(recipe.cls, (offer.hard_drive_id, others[0] if others else None))
    return out


def _candidates(game: GameData, makes: frozenset[str]) -> list[Recipe]:
    return sorted(
        (
            r
            for r in game.recipes.values()
            if r.is_alternate and r.kind == "part" and not r.is_event and r.main_product in makes
        ),
        key=lambda r: r.name,
    )


def _rivals(game: GameData, recipe: Recipe) -> set[str]:
    item = recipe.main_product
    return {r.cls for r in game.producers_of(item or "", "part") if r.main_product == item}


def alternates_view(st: WorldState, horizon_h: float) -> AlternatesView:
    """Every alternate that cuts machines, power or raw from the bill's chains."""
    game = st.game
    phase, parts = target_bill(st)
    rates = need_rates(parts, horizon_h)
    empty: AlternatesView = {
        "phase": phase,
        "horizon_h": horizon_h,
        "base": None,
        "alternates": [],
        "solves": 0,
    }
    if not rates:
        return empty
    chains = Chains.for_world(st, next(iter(rates)))
    base = chains.cost(rates)
    if base is None:
        return {**empty, "solves": chains.solves}
    per_part = {item: chains.cost({item: rate}) for item, rate in rates.items()}
    pool = chains.recipes
    have = st.available_recipe_ids
    drives = _drives(st)
    rows: list[AlternateRow] = []
    for recipe in _candidates(game, base.makes):
        rivals = _rivals(game, recipe)
        with_it = chains.cost(rates, [r for r in pool if r not in rivals] + [recipe.cls])
        without = (
            chains.cost(rates, [r for r in pool if r != recipe.cls]) if recipe.cls in pool else base
        )
        if with_it is None or without is None:
            continue
        delta = (
            with_it.machines - without.machines,
            with_it.mw - without.mw,
            with_it.raw - without.raw,
        )
        if min(delta) >= 0:
            continue
        drive, other = drives.get(recipe.cls, (None, None))
        source = "unlocked" if recipe.cls in have else "drive" if recipe.cls in drives else "pool"
        building = game.buildings.get(recipe.machine) if recipe.machine else None
        item = recipe.main_product or ""
        rows.append(
            {
                "recipe": recipe.cls,
                "name": recipe.name.removeprefix(_PREFIX),
                "item": item,
                "item_name": game.item_name(item),
                "machine": building.name if building else None,
                "source": source,
                "drive": drive,
                "other": other,
                "machines": delta[0],
                "mw": round(delta[1], 1),
                "raw": round(delta[2], 1),
                "parts": [game.item_name(p) for p, c in per_part.items() if c and item in c.makes],
            }
        )
    rows.sort(key=lambda r: (r["machines"], r["mw"], r["raw"], r["name"]))
    return {**empty, "base": _figures(base), "alternates": rows, "solves": chains.solves}

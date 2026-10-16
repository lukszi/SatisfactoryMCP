"""The gate: each part the target phase bills, who makes it, and what its chain takes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bill import SYSTEM_OF_TYPE, BillPart, Makers, default_recipe, target_bill, unlock_of
from .chains import Chains
from .steps import next_steps
from .views import GateInput, GateMaker, GatePart, GateView

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = ["gate_view", "need_rates"]


def need_rates(parts: list[BillPart], horizon_h: float) -> dict[str, float]:
    """Per minute of each part to deliver what is short within ``horizon_h`` hours."""
    return {p.item: p.short / (horizon_h * 60) for p in parts if p.short > 0}


def _makers(makers: Makers, item: str) -> list[GateMaker]:
    return [{"factory": f, "per_min": r} for f, r in makers.factories(item)]


def _inputs(
    st: WorldState, makers: Makers, part: BillPart, stock: dict[str, float]
) -> list[GateInput]:
    recipe, _ = default_recipe(st.game, part.item, st.available_recipe_ids)
    if recipe is None:
        return []
    made = sum(f.amount for f in recipe.products if f.item == part.item) or 1.0
    rows: list[GateInput] = []
    for flow in recipe.ingredients:
        per_unit = flow.amount / made
        rows.append(
            {
                "item": flow.item,
                "name": st.game.item_name(flow.item),
                "per_unit": round(per_unit, 4),
                "needed": round(per_unit * part.short, 1),
                "held": float(stock.get(flow.item, 0.0)),
                "made_per_min": makers.rate(flow.item),
                "makers": _makers(makers, flow.item),
            }
        )
    return rows


def gate_view(st: WorldState, horizon_h: float) -> GateView:
    """Every part the target phase still bills, sized for delivery within ``horizon_h``,
    and the ranked steps toward it."""
    game = st.game
    phase, parts = target_bill(st)
    makers = Makers.of(st)
    stock = st.stock()
    have = st.available_recipe_ids
    rates = need_rates(parts, horizon_h)
    chains = Chains.for_world(st, next(iter(rates))) if rates else None
    rows: list[GatePart] = []
    for part in parts:
        recipe, unlocked = default_recipe(game, part.item, have)
        source = unlock_of(game, recipe) if recipe is not None and not unlocked else None
        machine = game.buildings.get(recipe.machine) if recipe and recipe.machine else None
        need = rates.get(part.item, 0.0)
        cost = chains.cost({part.item: need}) if chains is not None and need else None
        rows.append(
            {
                "item": part.item,
                "name": game.item_name(part.item),
                "owed": part.owed,
                "held": part.held,
                "short": part.short,
                "recipe": recipe.name if recipe else None,
                "machine": machine.name if machine else None,
                "recipe_unlocked": unlocked,
                "unlock": source.name if source else None,
                "unlock_system": SYSTEM_OF_TYPE.get(source.type) if source else None,
                "made_per_min": makers.rate(part.item),
                "makers": _makers(makers, part.item),
                "need_per_min": round(need, 4),
                "machines": cost.machines if cost else None,
                "mw": cost.mw if cost else None,
                "inputs": _inputs(st, makers, part, stock),
            }
        )
    return {
        "phase": phase,
        "horizon_h": horizon_h,
        "parts": rows,
        "steps": next_steps(st, parts, makers, rates),
        "solves": chains.solves if chains else 0,
    }

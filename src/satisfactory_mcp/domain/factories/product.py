"""A factory's main product: the item its icon shows. Rule: docs/frontend_kit.md, "Factories"."""

from __future__ import annotations

from ...core.gamedata.model import GameData
from .query import FactoryView

__all__ = ["main_product"]

_TOL = 1e-6


def _classes_by_name(game: GameData) -> dict[str, str]:
    return {item.name: cls for cls, item in game.items.items()}


def main_product(view: FactoryView, game: GameData) -> str | None:
    """The item class of the most processed surplus a recipe makes (highest sink value,
    then the larger surplus); an extraction-only set shows its main resource. None when
    two candidates tie or nothing qualifies."""
    by_name = _classes_by_name(game)
    made = {item for row in view.machines if row.recipe_id for item in row.makes}
    ranked = sorted(
        (
            (game.items[by_name[name]].sink_points, round(view.net(name), 3), name)
            for name in made
            if name in by_name and view.net(name) > _TOL
        ),
        reverse=True,
    )
    if ranked:
        if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
            return None
        return by_name[ranked[0][2]]
    extracted: dict[str, float] = {}
    for row in view.machines:
        if not row.recipe_id:
            for name, rate in row.makes.items():
                extracted[name] = extracted.get(name, 0.0) + rate
    top = sorted(extracted.items(), key=lambda kv: kv[1], reverse=True)[:2]
    if not top or top[0][0] not in by_name or (len(top) > 1 and top[0][1] == top[1][1]):
        return None
    return by_name[top[0][0]]

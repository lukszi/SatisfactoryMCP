"""The chains behind the Space Elevator bill, sized by the planner's least-raw solve.

The recipe pool is what the save can build plus every default recipe, so a part whose
recipe is still locked has a chain too. docs/progress_roadmap.md, "Chains".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from ....core.gamedata.constants import WATER
from ...planning.analysis.bom import live_processes
from ...planning.solver.model import Scenario
from ...planning.solver.optimize import solve
from ...planning.solver.scenario import chain_scenario

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = ["ChainCost", "Chains"]


@dataclass(frozen=True)
class ChainCost:
    """One solve: whole machines, nameplate MW drawn, raw resources per minute (water left
    out) and the items its recipes make."""

    machines: int
    mw: float
    raw: float
    makes: frozenset[str] = field(default_factory=frozenset[str])


@dataclass
class Chains:
    """Solves of the bill's chains over one recipe pool; ``solves`` counts them."""

    base: Scenario
    solves: int = 0

    @classmethod
    def for_world(cls, st: WorldState, first_part: str) -> Chains:
        game = st.game
        chain = chain_scenario(game, st, first_part)
        defaults = [
            rid
            for rid, recipe in game.recipes.items()
            if recipe.kind == "part" and not recipe.is_alternate and not recipe.is_event
        ]
        recipes = list(dict.fromkeys([*chain.scenario.recipes, *defaults]))
        # Every building: a locked part's chain and a pool alternate need theirs.
        return cls(base=replace(chain.scenario, recipes=recipes, buildings_available=None))

    @property
    def recipes(self) -> list[str]:
        return list(self.base.recipes)

    def cost(
        self, rates: Mapping[str, float], recipes: Iterable[str] | None = None
    ) -> ChainCost | None:
        """The chain making ``rates`` per minute from ``recipes`` (the pool when None);
        None when no plan makes them."""
        wanted = {item: rate for item, rate in rates.items() if rate > 0}
        if not wanted:
            return None
        sc = replace(
            self.base,
            recipes=list(self.base.recipes if recipes is None else recipes),
            target_item=next(iter(wanted)),
            exports=tuple(wanted),
            export_minimums=wanted,
        )
        self.solves += 1
        sol = solve(sc)
        if not sol.ok:
            return None
        rows = live_processes(sol)
        game = self.base.game
        makes = {
            recipe.main_product
            for row in rows
            if row["recipe"] and (recipe := game.recipes.get(row["recipe"])) and recipe.main_product
        }
        return ChainCost(
            machines=sum(int(row["machines"]) for row in rows),
            mw=round(sum(-row["mw"] for row in rows if row["mw"] < 0), 1),
            raw=round(sum(v for k, v in sol.raw_used.items() if k != WATER and v > 1e-3), 1),
            makes=frozenset(makes),
        )

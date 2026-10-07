"""What would unlocking a recipe be worth to THIS plan?

One counterfactual per locked alternate, swept without pre-filtering, so a recipe opening a
chain the plan cannot reach yet still counts. Every delta is an UPPER bound: the machine a
candidate needs is assumed buildable and named. docs/planning.md §8.5j.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....core.gamedata.model import Recipe
from ...world.state import WorldState
from ..solver.model import Solution
from ..solver.optimize import solve
from ..solver.scenario import PlanRequest, with_recipes

__all__ = ["UnlockDelta", "UnlockSweep", "sweep_unlocks"]


@dataclass
class UnlockDelta:
    """One locked recipe, and what adding it does to a plan."""

    recipe: str
    name: str
    #: The plan's own objective before and after, oriented so larger is better.
    before: float
    after: float
    machines_before: float
    machines_after: float
    #: Buildings the recipe needs that this world has not built; the delta assumes them.
    needs: list[str] = field(default_factory=list[str])
    #: Schematics that grant it -- how the player would actually get it.
    unlocked_by: list[str] = field(default_factory=list[str])
    #: Processes the counterfactual switches ON that the baseline did not use: what the
    #: gain depends on (docs/planning.md §8.5j).
    activates: list[str] = field(default_factory=list[str])
    ok: bool = True

    @property
    def gain(self) -> float:
        """Improvement in the plan's objective. Positive is always better."""
        return self.after - self.before

    @property
    def machines_delta(self) -> float:
        return self.machines_after - self.machines_before


@dataclass
class UnlockSweep:
    objective: str
    baseline: float
    rows: list[UnlockDelta] = field(default_factory=list[UnlockDelta])
    tried: int = 0
    notes: list[str] = field(default_factory=list[str])

    @property
    def movers(self) -> list[UnlockDelta]:
        return [r for r in self.rows if r.ok and abs(r.gain) > self.tolerance]

    @property
    def unsolved(self) -> list[UnlockDelta]:
        """Candidates whose counterfactual did not solve: gain UNKNOWN, stored as zero, so
        they are kept out of ``movers`` and reported as themselves."""
        return [r for r in self.rows if not r.ok]

    @property
    def tolerance(self) -> float:
        """Below this a delta is solver noise; relative, since a large plan's last digits
        are not repeatable."""
        return max(1e-6, abs(self.baseline) * 1e-6)


def _as_gain(objective: str, value: float) -> float:
    """Objective value oriented so that larger is always an improvement."""
    return -value if objective.startswith("min") else value


def sweep_unlocks(
    request: PlanRequest,
    state: WorldState,
    candidates: list[Recipe] | None = None,
) -> UnlockSweep:
    """Re-solve ``request`` once per locked recipe and report what each is worth."""
    sc = request.scenario
    objective = sc.objective
    base: Solution = solve(sc)
    running = {p["label"] for p in base.processes}
    out = UnlockSweep(objective=objective, baseline=_as_gain(objective, base.objective_value))
    if not base.ok:
        out.notes.append("the plan itself is infeasible, so there is nothing to compare against")
        return out

    game = state.game
    pool = candidates if candidates is not None else state.locked_alternates
    for recipe in pool:
        out.tried += 1
        after = solve(with_recipes(sc, [recipe.cls]))
        needed = [recipe.machine] if recipe.machine else []
        row = UnlockDelta(
            recipe=recipe.cls,
            name=recipe.name,
            before=out.baseline,
            after=_as_gain(objective, after.objective_value) if after.ok else out.baseline,
            machines_before=base.machines_total,
            machines_after=after.machines_total if after.ok else base.machines_total,
            needs=sorted(
                game.buildings[b].name if b in game.buildings else b
                for b in needed
                if state.built(b) == 0
            ),
            unlocked_by=sorted(
                game.schematics[s].name for s in (recipe.unlocked_by or ()) if s in game.schematics
            ),
            activates=sorted({p["label"] for p in after.processes} - running - {recipe.name})
            if after.ok
            else [],
            ok=after.ok,
        )
        out.rows.append(row)

    # A tie on gain goes to fewer machines: equal power is not equally good to build.
    out.rows.sort(key=lambda r: (-r.gain, r.machines_delta))
    return out

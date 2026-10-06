"""What would unlocking a recipe be worth to THIS plan?

One counterfactual per candidate -- solve, solve again with the recipe bolted on, report
the difference -- swept over every locked alternate without pre-filtering, because a recipe
that opens a chain the plan cannot currently reach touches none of its items and is exactly
the interesting case. Every delta is an UPPER bound: ``_needed_buildings`` adds a machine
the player has never built to the buildable set so the candidate is not judged infeasible
for that reason, and names it. A zero is an answer, so ``tried`` is reported alongside the
winners rather than only the winners.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....core.gamedata.model import Recipe
from ...world.state import WorldState
from ..solver.optimize import Solution
from .advisor import _needed_buildings, _solve_with

__all__ = ["UnlockDelta", "sweep_unlocks"]


@dataclass
class UnlockDelta:
    """One locked recipe, and what adding it does to a plan."""

    recipe: str
    name: str
    #: The plan's own objective, before and after. Already sign-normalised by `solve`,
    #: so higher is better for max_* and lower is better for min_*.
    before: float
    after: float
    machines_before: float
    machines_after: float
    #: Buildings the recipe needs that the plan's world has not built. The delta assumes
    #: they exist, so this is what the number is conditional on.
    needs: list[str] = field(default_factory=list)
    #: Schematics that grant it -- how the player would actually get it.
    unlocked_by: list[str] = field(default_factory=list)
    #: Processes the counterfactual switches ON that the baseline did not use -- what the
    #: gain actually depends on, which is the difference between "+13.6%" and "+13.6% if
    #: you reintroduce the Turbofuel chain you deleted on purpose".
    activates: list[str] = field(default_factory=list)
    ok: bool = True

    @property
    def gain(self) -> float:
        """Improvement in the plan's objective. Positive is always better."""
        return self.after - self.before

    @property
    def machines(self) -> float:
        return self.machines_after - self.machines_before


@dataclass
class UnlockSweep:
    objective: str
    baseline: float
    rows: list[UnlockDelta] = field(default_factory=list)
    tried: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def movers(self) -> list[UnlockDelta]:
        return [r for r in self.rows if r.ok and abs(r.gain) > self.tolerance]

    @property
    def unsolved(self) -> list[UnlockDelta]:
        """Candidates whose counterfactual did not solve.

        Their gain is UNKNOWN. It is stored as zero because there is no other number to
        store, and that files them silently with the ones measured worthless -- so they
        come out of ``movers`` and are reported as themselves.
        """
        return [r for r in self.rows if not r.ok]

    @property
    def tolerance(self) -> float:
        """Below this a delta is solver noise rather than a finding.

        Relative to the baseline, because an LP on a 107,000 MW plan does not return the
        same digits twice at the bottom end.
        """
        return max(1e-6, abs(self.baseline) * 1e-6)


def _better(objective: str, value: float) -> float:
    """Objective value oriented so that larger is always an improvement.

    Without the flip a min_* recipe that halves raw usage reports a large NEGATIVE gain
    and sorts last.
    """
    return -value if objective.startswith("min") else value


def sweep_unlocks(
    request,
    state: WorldState,
    candidates: list[Recipe] | None = None,
) -> UnlockSweep:
    """Re-solve ``request`` once per locked recipe and report what each is worth."""
    sc = request.scenario
    objective = sc.objective
    base: Solution = _solve_with(sc, state, [])
    running = {p["label"] for p in base.processes}
    out = UnlockSweep(objective=objective, baseline=_better(objective, base.objective_value))
    if not base.ok:
        out.notes.append("the plan itself is infeasible, so there is nothing to compare against")
        return out

    pool = candidates if candidates is not None else state.locked_alternates
    for recipe in pool:
        out.tried += 1
        needed = _needed_buildings(state, [recipe.cls])
        after = _solve_with(sc, state, [recipe.cls])
        row = UnlockDelta(
            recipe=recipe.cls,
            name=recipe.name,
            before=out.baseline,
            after=_better(objective, after.objective_value) if after.ok else out.baseline,
            machines_before=base.machines_total,
            machines_after=after.machines_total if after.ok else base.machines_total,
            needs=sorted(
                state.game.buildings[b].name if b in state.game.buildings else b
                for b in needed
                if state.built(b) == 0
            ),
            unlocked_by=sorted(
                state.game.schematics[s].name
                for s in (recipe.unlocked_by or ())
                if s in state.game.schematics
            ),
            activates=sorted({p["label"] for p in after.processes} - running - {recipe.name})
            if after.ok
            else [],
            ok=after.ok,
        )
        out.rows.append(row)

    # A tie on gain is broken by fewer machines: two routes worth the same power are not
    # equally good to build.
    out.rows.sort(key=lambda r: (-r.gain, r.machines))
    return out

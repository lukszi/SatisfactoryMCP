"""Everything ``diff_vs_save`` has to DECIDE before a delta can be written down.

``build_diff`` answers "what is missing" against a scope and a solution somebody else had
to choose: solve the plan, work out which machines count as already built (a named factory,
or the one a stored plan was saved for), read the grid, and -- only when a stage question
was asked -- partition the plan into startup stages and match them against the save.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..factories.resolve import resolve_factory
from ..factories.select import SelectorError
from ..world.state import WorldState
from . import siting as siting_mod
from .commission import Tracking, commission, track
from .diff import DiffReport, build_diff
from .prepare import PreparedPlan, prepare

__all__ = ["DiffVsSaveReport", "build_diff_report"]


@dataclass
class DiffVsSaveReport:
    """A solved plan, the save it was matched against, and the stages if asked."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed or came back empty -- there is nothing to diff.
    rep: DiffReport | None = None
    power: dict = field(default_factory=dict)
    #: The startup partition matched against the save, only when a stage was asked for.
    tracking: Tracking | None = None
    #: What the scope costs the reader, when a factory narrowed what counts as built.
    scope_note: str = ""
    #: Said when a stored plan re-solves to a different plan_id than it was saved with.
    drift_note: str = ""
    #: Feasible, but the solve chose to build nothing. Distinct from a failure.
    empty: bool = False
    #: The recalled plan's recorded site and the approximate what-stands-here census over
    #: it, both only when the plan carries a siting. The survey counts by class rather than
    #: matching identity; ``planning.siting`` says why.
    site: siting_mod.Siting | None = None
    site_survey: siting_mod.SiteSurvey | None = None


def build_diff_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    *,
    objective: str = "",
    plan: str | None = None,
    plan_name: str = "",
    stage: int | None = None,
    factory: str | None = None,
    biomass: bool = False,
) -> DiffVsSaveReport:
    """Solve ``plan_kwargs`` and match it against the save under an optional scope.

    A ``SelectorError`` from a named factory propagates, including the case where the
    selector resolves but every machine it named has since been dismantled: an empty
    scope is the caller's mistake, not a diff saying the plan is unbuilt.
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = DiffVsSaveReport(prepared=prepared)
    if prepared.failure:
        return report
    req, sol = prepared.request, prepared.solution

    if not sol.processes:
        report.empty = True
        return report

    # A plan saved with for_factory carries its own scope, so `diff_vs_save(plan=...)`
    # already answers "how far along is THAT factory" without naming it again.
    scope_name = factory
    if scope_name is None and plan:
        stored = st.plans.find(plan)
        scope_name = (stored.factory or None) if stored else None

    scope = None
    if scope_name:
        resolved_name, machines = resolve_factory(st, scope_name)
        if not machines:
            raise SelectorError(
                f"{scope_name!r} resolved to no machines that still exist in this save"
            )
        scope = set(machines)
        report.scope_note = (
            f"scoped to {resolved_name!r} ({len(scope)} machines): everything outside it "
            "counts as not built, and nodes tapped by other factories are unavailable"
        )

    report.rep = rep = build_diff(g, st, sol, req, scope=scope, biomass=biomass)
    report.power = pw = st.power_report(biomass=biomass)

    # A sited plan gets the census over its own pad. Beside the identity-matched diff,
    # not instead of it: the diff says whether the machines exist, the survey says
    # whether they stand where the plan was sited.
    if plan and (stored := st.plans.find(plan)) is not None:
        sit = siting_mod.parse(stored)
        if sit is not None:
            report.site = sit
            report.site_survey = siting_mod.survey(g, st, sit, sol.processes)

    # Off unless asked for: the stage numbering is only stable for a STORED plan.
    if plan or stage is not None:
        report.tracking = track(
            prepared,
            commission(prepared, g, pw["headroom_mw"], "power_report, nameplate"),
            rep,
            g,
            st,
            plan_name=plan_name,
        )
        if plan_name and (stored := st.plans.find(plan_name)) and stored.plan_id != req.plan_id:
            # A stage number is a milestone the player remembers, and a re-solve against a
            # moved world can renumber the whole partition under them.
            report.drift_note = (
                f"plan {plan_name!r} was saved against plan_id {stored.plan_id} and "
                f"re-solves to {req.plan_id} -- the WORLD moved, so these stage numbers "
                "may not be the ones you were given before"
            )
    return report

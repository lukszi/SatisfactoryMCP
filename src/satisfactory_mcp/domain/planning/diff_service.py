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
from .commission import Commissioning, Tracking, commission, machine_states, track
from .diff import DiffReport, build_diff
from .planlog import PlanState
from .prepare import PreparedPlan, prepare

__all__ = [
    "DEFAULT_HEADROOM",
    "HEADROOM_DEFAULTS",
    "MEASURED_SOURCE",
    "NAMEPLATE_SOURCE",
    "STORED_SOURCE",
    "DiffVsSaveReport",
    "build_diff_report",
    "default_headroom",
    "match_scope",
]

NAMEPLATE_SOURCE = "nameplate from the save"
MEASURED_SOURCE = "measured from the save"
STORED_SOURCE = "stored on the plan"

#: What a plan with no stored headroom is partitioned against (docs/planner_p4.md, B3).
HEADROOM_DEFAULTS = ("measured", "nameplate")
DEFAULT_HEADROOM = "measured"


def default_headroom(power: dict, which: str = DEFAULT_HEADROOM) -> tuple[float, str]:
    """The save's headroom a plan without a stored one uses, and its source words."""
    if which == "nameplate":
        return float(power.get("headroom_mw", 0.0)), NAMEPLATE_SOURCE
    return float(power.get("measured_headroom_mw", 0.0)), MEASURED_SOURCE


@dataclass
class DiffVsSaveReport:
    """A solved plan, the save it was matched against, and the stages if asked."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed or came back empty -- there is nothing to diff.
    rep: DiffReport | None = None
    power: dict = field(default_factory=dict)
    #: The startup partition matched against the save, only when a stage was asked for.
    tracking: Tracking | None = None
    #: The startup order the partition came from, beside ``tracking``.
    run: Commissioning | None = None
    #: graph.health state per matched machine, from the one pass ``tracking`` also read.
    health: dict[str, str] = field(default_factory=dict)
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


def match_scope(
    g: GameData, st: WorldState, prepared: PreparedPlan, scope_name: str | None, biomass: bool
) -> tuple[DiffReport, str]:
    """The diff of a solved plan under an optional factory scope, and the scope's note.

    A ``SelectorError`` propagates when the named factory has no machines left: an empty
    scope is the caller's mistake, not a diff saying the plan is unbuilt.
    """
    scope = None
    note = ""
    if scope_name:
        resolved_name, machines = resolve_factory(st, scope_name)
        if not machines:
            raise SelectorError(
                f"{scope_name!r} resolved to no machines that still exist in this save"
            )
        scope = set(machines)
        note = (
            f"scoped to {resolved_name!r} ({len(scope)} machines): everything outside it "
            "counts as not built, and nodes tapped by other factories are unavailable"
        )
    rep = build_diff(g, st, prepared.solution, prepared.request, scope=scope, biomass=biomass)
    return rep, note


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
    headroom_mw: float | None = None,
    stored: PlanState | None = None,
    default: str = DEFAULT_HEADROOM,
) -> DiffVsSaveReport:
    """Solve ``plan_kwargs`` and match it against the save under an optional scope.

    ``stored`` is the recalled plan version: scope, siting and plan_id come from it rather
    than from ``st.plans``. ``headroom_mw`` replaces the save's headroom (``default``:
    measured or nameplate) for the startup partition.
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = DiffVsSaveReport(prepared=prepared)
    if prepared.failure:
        return report
    req, sol = prepared.request, prepared.solution

    if not sol.processes:
        report.empty = True
        return report

    recalled = stored if stored is not None else (st.plans.find(plan) if plan else None)
    scope_name = factory
    if scope_name is None and recalled is not None:
        scope_name = recalled.factory or None

    rep, report.scope_note = match_scope(g, st, prepared, scope_name, biomass)
    report.rep = rep
    report.power = pw = st.power_report(biomass=biomass)

    # A sited plan gets the census over its own pad. Beside the identity-matched diff,
    # not instead of it: the diff says whether the machines exist, the survey says
    # whether they stand where the plan was sited.
    if recalled is not None:
        sit = siting_mod.parse(recalled)
        if sit is not None:
            report.site = sit
            report.site_survey = siting_mod.survey(g, st, sit, sol.processes)

    # Off unless asked for: the stage numbering is only stable for a STORED plan.
    if plan or stored is not None or stage is not None:
        if headroom_mw is None:
            head, source = default_headroom(pw, default)
        else:
            head, source = float(headroom_mw), STORED_SOURCE
        report.run = run = commission(prepared, g, head, source)
        report.health = machine_states(rep, g, st)
        report.tracking = track(
            prepared, run, rep, g, st, plan_name=plan_name, health=report.health
        )
        if stored is not None:
            then = stored
        else:
            then = st.plans.find(plan_name) if plan_name else None
        if plan_name and then is not None and then.plan_id and then.plan_id != req.plan_id:
            # A stage number is a milestone the player remembers, and a re-solve against a
            # moved world can renumber the whole partition under them.
            report.drift_note = (
                f"plan {plan_name!r} was saved against plan_id {then.plan_id} and "
                f"re-solves to {req.plan_id} -- the WORLD moved, so these stage numbers "
                "may not be the ones you were given before"
            )
    return report

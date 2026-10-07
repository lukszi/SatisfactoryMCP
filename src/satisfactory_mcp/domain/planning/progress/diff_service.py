"""Everything ``diff_vs_save`` has to DECIDE before a delta can be written down.

``build_diff`` answers "what is missing" against a scope and a solution somebody else had
to choose: solve the plan, work out which machines count as already built (a named factory,
or the one a stored plan was saved for), read the grid, and -- only when a stage question
was asked -- partition the plan into startup stages and match them against the save.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ...factories.select import SelectorError, resolve_factory
from ...power.views import PowerReport
from ...world.state import WorldState
from .. import siting as siting_mod
from ..solver.prepare import PreparedPlan, prepare
from ..stored.planlog import PlanState
from ..stored.store import Plan
from . import built
from .diff import DiffReport, build_diff, request_of, solution_of
from .stages import Tracking, machine_states, track
from .startup import Commissioning, commission

__all__ = [
    "DEFAULT_HEADROOM",
    "GIVEN_SOURCE",
    "HEADROOM_DEFAULTS",
    "MEASURED_SOURCE",
    "NAMEPLATE_SOURCE",
    "STORED_SOURCE",
    "DiffVsSaveReport",
    "build_diff_report",
    "default_headroom",
    "diff_in_scope",
    "plan_progress",
    "resolve_headroom",
]

NAMEPLATE_SOURCE = "nameplate from the save"
MEASURED_SOURCE = "measured from the save"
STORED_SOURCE = "stored on the plan"
GIVEN_SOURCE = "given by caller"

#: What a plan with no stored headroom is partitioned against (docs/planner_p4.md, B3).
HEADROOM_DEFAULTS = ("measured", "nameplate")
DEFAULT_HEADROOM = "measured"


def default_headroom(power: PowerReport, which: str = DEFAULT_HEADROOM) -> tuple[float, str]:
    """The save's headroom a plan without a stored one uses, and its source words."""
    if which == "nameplate":
        return float(power.get("headroom_mw", 0.0)), NAMEPLATE_SOURCE
    return float(power.get("measured_headroom_mw", 0.0)), MEASURED_SOURCE


def resolve_headroom(
    power: PowerReport,
    *,
    given: float | None = None,
    stored: PlanState | None = None,
    default: str = DEFAULT_HEADROOM,
) -> tuple[float, str]:
    """The headroom a startup order is built against, and its source words: the caller's,
    else the plan's stored one, else the save's ``default`` reading."""
    if given is not None:
        return float(given), GIVEN_SOURCE
    if stored is not None and stored.headroom_mw is not None:
        return float(stored.headroom_mw), STORED_SOURCE
    return default_headroom(power, default)


@dataclass
class DiffVsSaveReport:
    """A solved plan, the save it was matched against, and the stages if asked."""

    prepared: PreparedPlan
    #: The grid the plan is matched against, read whether or not the plan solves.
    power: PowerReport
    #: ``None`` when the plan failed or came back empty -- there is nothing to diff.
    diff: DiffReport | None = None
    #: The startup partition matched against the save, only when a stage was asked for.
    tracking: Tracking | None = None
    #: The startup order the partition came from, beside ``tracking``.
    startup: Commissioning | None = None
    #: graph.health state per matched machine, from the one pass ``tracking`` also read.
    health: dict[str, str] = field(default_factory=dict[str, str])
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


def _scope_note(name: str, count: int) -> str:
    return (
        f"scoped to {name!r} ({count} machines): everything outside it counts as not built, "
        "and nodes tapped by other factories are unavailable"
    )


def diff_in_scope(
    g: GameData,
    st: WorldState,
    prepared: PreparedPlan,
    scope_name: str | None,
    biomass: bool,
    stored: PlanState | Plan | None = None,
) -> tuple[DiffReport, str]:
    """The diff of a solved plan under an optional factory scope, and the scope's note.

    With ``stored``, the plan's built machines are found at its site (``built.detect``)
    unless ``scope_name`` or the plan picks a factory, the whole world or nothing; the
    result rides on ``DiffReport.built_at``. Without it, a ``SelectorError`` propagates
    when the named factory has no machines left: an empty scope is the caller's mistake,
    not a diff saying the plan is unbuilt.
    """
    solution, request = solution_of(prepared), request_of(prepared)
    if stored is not None:
        found = built.detect(g, st, stored, prepared, scope_name)
        diff = build_diff(g, st, solution, request, scope=found.scope, biomass=biomass)
        low = None
        if found.scope_low is not None:
            low = build_diff(g, st, solution, request, scope=found.scope_low, biomass=biomass)
        built.fill_progress(found, diff, low)
        diff.built_at = found
        note = ""
        if found.mode == "picked":
            note = _scope_note(found.picked, len(found.scope or ()))
        return diff, note
    scope: set[str] | None = None
    note = ""
    if scope_name and built.mode_of(scope_name) == "world":
        scope_name = None
    if scope_name and built.mode_of(scope_name) == "none":
        scope, scope_name = set(), None
    if scope_name:
        resolved_name, machines = resolve_factory(st, scope_name)
        if not machines:
            raise SelectorError(
                f"{scope_name!r} resolved to no machines that still exist in this save"
            )
        scope = set(machines)
        note = _scope_note(resolved_name, len(scope))
    diff = build_diff(g, st, solution, request, scope=scope, biomass=biomass)
    return diff, note


def plan_progress(g: GameData, st: WorldState, stored: PlanState | Plan) -> built.BuiltAt | None:
    """A stored plan's ``built_at`` alone, for a list of plans: one solve and one match, no
    startup order. None when the plan does not solve or builds nothing."""
    prepared = prepare(g, st, stored.kwargs(), diagnose=False)
    if prepared.failure or prepared.solution is None or not prepared.solution.processes:
        return None
    diff, _ = diff_in_scope(g, st, prepared, None, False, stored=stored)
    return diff.built_at


def _track_stages(
    g: GameData,
    st: WorldState,
    report: DiffVsSaveReport,
    diff: DiffReport,
    *,
    plan_name: str,
    stored: PlanState | None,
    default: str,
) -> None:
    """Partition the plan into startup stages and match them against the save, in place."""
    head, source = resolve_headroom(report.power, stored=stored, default=default)
    report.startup = commission(report.prepared, g, head, source)
    report.health = machine_states(diff, g, st)
    report.tracking = track(
        report.prepared,
        report.startup,
        diff,
        g,
        st,
        plan_name=plan_name,
        health=report.health,
    )
    saved_plan: PlanState | Plan | None
    if stored is not None:
        saved_plan = stored
    else:
        saved_plan = st.plans.find(plan_name) if plan_name else None
    plan_id = request_of(report.prepared).plan_id
    saved_id = saved_plan.plan_id if saved_plan is not None else ""
    if plan_name and saved_id and saved_id != plan_id:
        # A stage number is a milestone the player remembers, and a re-solve against a
        # moved world can renumber the whole partition under them.
        report.drift_note = (
            f"plan {plan_name!r} was saved against plan_id {saved_id} and "
            f"re-solves to {plan_id} -- the WORLD moved, so these stage numbers "
            "may not be the ones you were given before"
        )


def build_diff_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: Mapping[str, object],
    *,
    objective: str = "",
    plan: str | None = None,
    plan_name: str = "",
    stage: int | None = None,
    factory: str | None = None,
    biomass: bool = False,
    stored: PlanState | None = None,
    default: str = DEFAULT_HEADROOM,
) -> DiffVsSaveReport:
    """Solve ``plan_kwargs`` and match it against the save under an optional scope.

    ``stored`` is the recalled plan version: scope, siting, plan_id and the startup
    headroom come from it rather than from ``st.plans``; a plan with no stored headroom is
    partitioned against the save's ``default`` reading (measured or nameplate).
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = DiffVsSaveReport(prepared=prepared, power=st.power_report(biomass=biomass))
    if prepared.failure:
        return report

    solution = solution_of(prepared)
    if not solution.processes:
        report.empty = True
        return report

    recalled = stored if stored is not None else (st.plans.find(plan) if plan else None)
    diff, report.scope_note = diff_in_scope(g, st, prepared, factory, biomass, stored=recalled)
    report.diff = diff

    # A sited plan gets the census over its own pad. Beside the identity-matched diff,
    # not instead of it: the diff says whether the machines exist, the survey says
    # whether they stand where the plan was sited.
    if recalled is not None:
        sit = siting_mod.parse(recalled)
        if sit is not None:
            report.site = sit
            report.site_survey = siting_mod.survey(g, st, sit, solution.processes)

    # Off unless asked for: the stage numbering is only stable for a STORED plan.
    if plan or stored is not None or stage is not None:
        _track_stages(g, st, report, diff, plan_name=plan_name, stored=stored, default=default)
    return report

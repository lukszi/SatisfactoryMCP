"""Everything ``commission_plan`` has to LOOK UP before a startup order can be printed.

``commission`` orders the waves. Around it sat two world questions: how much power the
grid actually has free -- which is a choice between two defensible numbers, not a
reading -- and which extractors already on the ground are load-bearing, because a wave
that repipes one of those takes running generation down mid-startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData
from ...factories.select import SelectorError
from ...factories.trace import live_feeders
from ...world.state import WorldState
from ..solver.prepare import PreparedPlan, prepare
from ..stored.planlog import PlanState
from .diff_service import DEFAULT_HEADROOM, diff_in_scope, resolve_headroom
from .stages import Tracking, track
from .startup import Commissioning, commission

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from .built import BuiltAt

__all__ = ["CommissionReport", "build_commission_report"]


@dataclass
class CommissionReport:
    """A startup sequence, the headroom it was computed against, and the cutover risk."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed -- there is nothing to switch on.
    startup: Commissioning | None = None
    #: The headroom the sequence was actually built against, and where it came from.
    headroom_mw: float = 0.0
    headroom_source: str = ""
    power: dict = field(default_factory=dict)
    #: Built extractors already feeding running generators. Only computed for a
    #: sequence that exists, since it is advice about following one.
    live_feeders: list[tuple[str, float]] = field(default_factory=list)
    #: The same waves matched against the save, only for a stored plan.
    tracking: Tracking | None = None
    #: Where the stored plan's built machines were found.
    built_at: BuiltAt | None = None


def build_commission_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    headroom_mw: float | None,
    *,
    objective: str = "",
    biomass: bool = False,
    stored: PlanState | None = None,
    default: str = DEFAULT_HEADROOM,
) -> CommissionReport:
    """Solve ``plan_kwargs``, order it into waves, and read what the waves stand on.

    ``stored`` is the recalled plan version: its ``headroom_mw`` is used when the caller
    gives none, and the waves are matched against the save under its scope.
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = CommissionReport(prepared=prepared)
    if prepared.failure:
        return report

    # Headroom is an INPUT and is printed as one. A sequence computed against a save
    # that has since moved is then visibly stale rather than quietly wrong -- the same
    # reason phase_requirements labels its rows instead of filtering them. Measured by
    # default: on the reference save nameplate leaves 116 MW free against a 392 MW minimum
    # slice, so no plan gets stages (docs/planner_p4.md, B3).
    report.power = power = st.power_report(biomass=biomass)
    report.headroom_mw, report.headroom_source = resolve_headroom(
        power, given=headroom_mw, stored=stored, default=default
    )

    report.startup = startup = commission(prepared, g, report.headroom_mw, report.headroom_source)
    if startup.ok:
        # What the sequence is standing on. A wave that repipes an extractor already
        # feeding live generators takes that power down mid-startup, which is exactly the
        # moment the plan has least headroom to spare. Read from the save's own
        # connections rather than assumed, and only PROVEN-running generators are charged.
        report.live_feeders = live_feeders(g, st)
    if stored is not None and prepared.solution.processes:
        try:
            diff, _ = diff_in_scope(g, st, prepared, None, biomass, stored=stored)
        except SelectorError:
            return report
        report.built_at = diff.built_at
        report.tracking = track(prepared, startup, diff, g, st, plan_name=stored.name)
    return report

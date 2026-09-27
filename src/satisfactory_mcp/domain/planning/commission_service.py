"""Everything ``commission_plan`` has to LOOK UP before a startup order can be printed.

``commission`` orders the waves. Around it sat two world questions: how much power the
grid actually has free -- which is a choice between two defensible numbers, not a
reading -- and which extractors already on the ground are load-bearing, because a wave
that repipes one of those takes running generation down mid-startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..factories.select import SelectorError
from ..world.state import WorldState
from .commission import Commissioning, Tracking, commission, live_feeders, track
from .diff_service import STORED_SOURCE, match_scope
from .planlog import PlanState
from .prepare import PreparedPlan, prepare

__all__ = ["CommissionReport", "build_commission_report"]


@dataclass
class CommissionReport:
    """A startup sequence, the headroom it was computed against, and the cutover risk."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed -- there is nothing to switch on.
    plan_run: Commissioning | None = None
    #: The headroom the sequence was actually built against, and where it came from.
    head_mw: float = 0.0
    head_source: str = ""
    power: dict = field(default_factory=dict)
    #: Built extractors already feeding running generators. Only computed for a
    #: sequence that exists, since it is advice about following one.
    live: list[tuple[str, float]] = field(default_factory=list)
    #: The same waves matched against the save, only for a stored plan.
    tracking: Tracking | None = None


def build_commission_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    headroom_mw: float | None,
    *,
    objective: str = "",
    biomass: bool = False,
    stored: PlanState | None = None,
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
    # reason phase_requirements labels its rows instead of filtering them.
    report.power = power = st.power_report(biomass=biomass)
    if headroom_mw is not None:
        head, source = float(headroom_mw), "given by caller"
    elif stored is not None and stored.headroom_mw is not None:
        head, source = float(stored.headroom_mw), STORED_SOURCE
    else:
        # Nameplate on purpose. Measured headroom is usually much larger -- 6,034 MW
        # against 711 on the reference save, because most of that factory is idle -- but
        # energising a block can un-starve the very machines that are idle, and the fuse
        # blows on demand, not on averages. The safe bound is the default; the measured
        # one is reported so a player who knows their base is quiet can pass it in.
        head, source = power["headroom_mw"], "power_report, nameplate"
    report.head_mw, report.head_source = head, source

    report.plan_run = plan_run = commission(prepared, g, head, source)
    if plan_run.ok:
        # What the sequence is standing on. A wave that repipes an extractor already
        # feeding live generators takes that power down mid-startup, which is exactly the
        # moment the plan has least headroom to spare. Read from the save's own
        # connections rather than assumed, and only PROVEN-running generators are charged.
        report.live = live_feeders(g, st)
    if stored is not None and prepared.solution.processes:
        try:
            rep, _ = match_scope(g, st, prepared, stored.factory or None, biomass)
        except SelectorError:
            return report
        report.tracking = track(prepared, plan_run, rep, g, st, plan_name=stored.name)
    return report

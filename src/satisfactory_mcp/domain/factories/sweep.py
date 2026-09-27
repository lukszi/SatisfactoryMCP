"""The health of every named factory at once, worst first: one order for every surface."""

from __future__ import annotations

from dataclasses import dataclass

from .health import ACTIONABLE, STATES, HealthReport, MachineHealth, assess
from .query import FactoryView, build_view

__all__ = ["FactorySweep", "sweep"]


@dataclass
class FactorySweep:
    """One named factory's ``assess`` and ``build_view``, over its anchors still standing."""

    label: object
    standing: list[str]
    report: HealthReport
    view: FactoryView

    @property
    def actionable(self) -> int:
        return sum(self.report.by_state[s] for s in ACTIONABLE)

    def worst_actionable(self, limit: int) -> list[MachineHealth]:
        """The machines needing action, worst state first; never a paused or fine one."""
        order = {s: i for i, s in enumerate(STATES)}
        return sorted(
            (m for m in self.report.machines if m.state in ACTIONABLE),
            key=lambda m: (order[m.state], m.uptime if m.uptime is not None else 0.0),
        )[:limit]


def sweep(st) -> list[FactorySweep]:
    """Every label of ``st``, most machines needing action first, then lowest mean uptime."""
    alive = set(st.graph.machines())
    rows = []
    for label in st.labels.labels:
        standing = [m for m in label.anchors if m in alive]
        report = assess(label.name, standing, st.game, st.projection, st.graph)
        view = build_view(label.name, standing, st.graph, st.game, st.projection, st.labels)
        rows.append(FactorySweep(label=label, standing=standing, report=report, view=view))
    rows.sort(
        key=lambda r: (
            -r.actionable,
            r.report.mean_uptime if r.report.mean_uptime is not None else 2.0,
        )
    )
    return rows

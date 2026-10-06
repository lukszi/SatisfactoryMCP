"""The matrix of one solve: its columns, rows, bounds and objectives, and the two-phase MILP.

docs/planning.md §8.1 is the formulation; every item balance is an equality (§8.2).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import LinearConstraint, milp

from ....core import solverlane
from ....core.gamedata.constants import AWESOME_SINK_MW
from .model import MW, Process, Scenario, Solution
from .overclock import machine_price_mw

__all__ = [
    "Columns",
    "balance_rows",
    "bounds_and_group_caps",
    "goal_vector",
    "integrality_vector",
    "machine_price_vector",
    "max_machines_row",
    "power_row",
    "price_machines_for_power",
    "recycle_once_rows",
    "sloop_budget_row",
    "solve_two_phase",
]


@dataclass(frozen=True)
class Columns:
    """Where each variable sits: processes, then raw, export and sink flows, then grid import."""

    n_processes: int
    raw_items: tuple[str, ...]
    export_items: tuple[str, ...]
    sink_items: tuple[str, ...]

    @classmethod
    def for_scenario(cls, scenario: Scenario, processes: list[Process]) -> Columns:
        items = sorted({item for process in processes for item in process.rates})
        return cls(
            n_processes=len(processes),
            raw_items=tuple(sorted(scenario.raw_caps)),
            export_items=tuple(sorted(set(scenario.exports) | set(scenario.export_minimums))),
            sink_items=tuple(item for item in items if _sinkable(scenario, item)),
        )

    @property
    def size(self) -> int:
        return (
            self.n_processes
            + len(self.raw_items)
            + len(self.export_items)
            + len(self.sink_items)
            + 1
        )

    @property
    def grid(self) -> int:
        return self.size - 1

    def process(self, i: int) -> int:
        return i

    def raw(self, j: int) -> int:
        return self.n_processes + j

    def export(self, j: int) -> int:
        return self.n_processes + len(self.raw_items) + j

    def sink(self, j: int) -> int:
        return self.n_processes + len(self.raw_items) + len(self.export_items) + j

    @property
    def exports_power(self) -> bool:
        return MW in self.export_items


def _sinkable(scenario: Scenario, item_id: str) -> bool:
    if not scenario.allow_sinks:
        return False
    item = scenario.game.items.get(item_id)
    return bool(item and item.sinkable)


def balance_rows(processes: list[Process], columns: Columns) -> list[np.ndarray]:
    """One equality row per item: process nets, plus raw in and export and sink out."""
    items = sorted({item for process in processes for item in process.rates})
    balanced = items + [i for i in columns.raw_items if i not in items]
    # An export no process touches still needs its row, or its column is unbounded (§8.2b).
    balanced += [
        i for i in columns.export_items if i != MW and i not in items and i not in columns.raw_items
    ]
    rows: list[np.ndarray] = []
    for item in balanced:
        row = np.zeros(columns.size)
        for i, process in enumerate(processes):
            row[columns.process(i)] = process.net(item)
        if item in columns.raw_items:
            row[columns.raw(columns.raw_items.index(item))] = 1.0
        if item in columns.export_items:
            row[columns.export(columns.export_items.index(item))] = -1.0
        if item in columns.sink_items:
            row[columns.sink(columns.sink_items.index(item))] = -1.0
        rows.append(row)
    return rows


def power_row(scenario: Scenario, processes: list[Process], columns: Columns) -> np.ndarray:
    """The ``MW`` balance: machines and sinks draw, generators and the grid supply."""
    row = np.zeros(columns.size)
    for i, process in enumerate(processes):
        row[columns.process(i)] = process.mw
    # One AWESOME Sink per belt line of sunk material.
    for j, _item in enumerate(columns.sink_items):
        row[columns.sink(j)] = -AWESOME_SINK_MW / max(scenario.belt_ipm, 1.0)
    row[columns.grid] = 1.0
    if columns.exports_power:
        row[columns.export(columns.export_items.index(MW))] = -1.0
    return row


def bounds_and_group_caps(
    scenario: Scenario, processes: list[Process], columns: Columns
) -> tuple[np.ndarray, np.ndarray, list[LinearConstraint]]:
    """Lower and upper bounds per column, and one shared cap per group of clock modes."""
    lower = np.zeros(columns.size)
    upper = np.full(columns.size, np.inf)
    grouped: dict[str, list[int]] = {}
    for i, process in enumerate(processes):
        if process.max_count is None:
            continue
        if process.group is None:
            upper[columns.process(i)] = process.max_count
        else:
            grouped.setdefault(process.group, []).append(i)
    # The modes of a group are the same machines, so they share one cap (§8.9).
    group_caps: list[LinearConstraint] = []
    for members in grouped.values():
        cap = min(processes[i].max_count for i in members)
        if len(members) == 1:
            upper[columns.process(members[0])] = cap
            continue
        row = np.zeros(columns.size)
        for i in members:
            row[columns.process(i)] = 1.0
            upper[columns.process(i)] = cap
        group_caps.append(LinearConstraint(row, -np.inf, cap))
    for j, item in enumerate(columns.raw_items):
        upper[columns.raw(j)] = scenario.raw_caps[item]
    for j, item in enumerate(columns.export_items):
        if item in scenario.export_minimums:
            lower[columns.export(j)] = scenario.export_minimums[item]
    # A power plant must be self-contained; anything else may draw from the grid.
    if columns.exports_power:
        upper[columns.grid] = 0.0
    else:
        upper[columns.grid] = np.inf if scenario.grid_import_mw is None else scenario.grid_import_mw
    return lower, upper, group_caps


def recycle_once_rows(
    scenario: Scenario, processes: list[Process], columns: Columns
) -> list[LinearConstraint]:
    """Per item the named set makes and eats: consumed inside <= produced outside (§8.2h)."""
    if not scenario.recycle_once:
        return []
    named = {i for i, process in enumerate(processes) if process.pid in scenario.recycle_once}
    made = {
        item for i in named for item, rate in processes[i].rates.items() if rate > 0 and item != MW
    }
    eaten = {
        item for i in named for item, rate in processes[i].rates.items() if rate < 0 and item != MW
    }
    rows: list[LinearConstraint] = []
    for item in sorted(made & eaten):
        row = np.zeros(columns.size)
        for i, process in enumerate(processes):
            rate = process.rates.get(item, 0.0)
            if i in named and rate < 0:
                row[columns.process(i)] += -rate
            elif i not in named and rate > 0:
                row[columns.process(i)] -= rate
        rows.append(LinearConstraint(row, -np.inf, 0.0))
    return rows


def sloop_budget_row(
    scenario: Scenario, processes: list[Process], columns: Columns
) -> LinearConstraint | None:
    """Somersloops spent across every column, at most the budget; None when nothing spends."""
    if not scenario.sloop_budget:
        return None
    row = np.zeros(columns.size)
    used = False
    for i, process in enumerate(processes):
        if process.sloops:
            row[columns.process(i)] = process.sloops
            used = True
    return LinearConstraint(row, -np.inf, scenario.sloop_budget) if used else None


def max_machines_row(scenario: Scenario, columns: Columns) -> LinearConstraint | None:
    if scenario.max_machines is None:
        return None
    row = np.zeros(columns.size)
    for i in range(columns.n_processes):
        row[columns.process(i)] = 1.0
    return LinearConstraint(row, -np.inf, scenario.max_machines)


def integrality_vector(scenario: Scenario, columns: Columns) -> np.ndarray:
    integrality = np.zeros(columns.size)
    if scenario.integral:
        for i in range(columns.n_processes):
            integrality[columns.process(i)] = 1
    return integrality


def goal_vector(
    scenario: Scenario, processes: list[Process], columns: Columns
) -> np.ndarray | Solution:
    """Phase 1's objective, minimised, or an infeasible Solution naming what is missing."""
    goal = np.zeros(columns.size)
    if scenario.objective == "max_mw":
        if MW not in columns.export_items:
            return Solution.infeasible("objective max_mw requires __MW__ in exports")
        goal[columns.export(columns.export_items.index(MW))] = -1.0
    elif scenario.objective == "max_item":
        if not scenario.target_item or scenario.target_item not in columns.export_items:
            return Solution.infeasible(
                f"objective max_item requires {scenario.target_item!r} in exports"
            )
        goal[columns.export(columns.export_items.index(scenario.target_item))] = -1.0
    elif scenario.objective == "min_raw":
        # Raw arrives through raw columns and extractor processes, and both are priced.
        for j in range(len(columns.raw_items)):
            goal[columns.raw(j)] = scenario.raw_weights.get(columns.raw_items[j], 1.0)
        for i, process in enumerate(processes):
            if process.kind == "extractor":
                goal[columns.process(i)] = sum(
                    rate * scenario.raw_weights.get(item, 1.0)
                    for item, rate in process.rates.items()
                    if rate > 0
                )
    elif scenario.objective == "min_machines":
        for i in range(columns.n_processes):
            goal[columns.process(i)] = 1.0
    elif scenario.objective == "min_power":
        for i, process in enumerate(processes):
            if process.mw < 0:
                goal[columns.process(i)] = -process.mw
    else:
        return Solution.infeasible(f"unknown objective {scenario.objective!r}")
    return goal


def price_machines_for_power(
    goal: np.ndarray, scenario: Scenario, processes: list[Process], columns: Columns
) -> np.ndarray:
    """``goal`` with each machine priced in MW when the objective is power (§8.4)."""
    priced = goal.copy()
    if scenario.objective in ("max_mw", "min_power"):
        for i, process in enumerate(processes):
            priced[columns.process(i)] += machine_price_mw(scenario, process.building)
    return priced


def machine_price_vector(
    scenario: Scenario, processes: list[Process], columns: Columns
) -> np.ndarray:
    """Phase 2's cost per machine: 1 each, or build points plus running power at a horizon."""
    machine_cost = np.zeros(columns.size)
    per_mw = scenario.payback_hours * scenario.power_price
    for i, process in enumerate(processes):
        machine_cost[columns.process(i)] = (
            max(scenario.build_points.get(process.building or "", 0.0), 1.0)
            + per_mw * max(0.0, -process.mw)
            if per_mw > 0
            else 1.0
        )
    return machine_cost


def solve_two_phase(
    objective: np.ndarray,
    machine_cost: np.ndarray,
    constraints: list[LinearConstraint],
    bounds: tuple[np.ndarray, np.ndarray],
    integrality: np.ndarray,
    scenario: Scenario,
) -> tuple[np.ndarray, list[str]] | Solution:
    """Optimise ``objective``, then pin it and minimise ``machine_cost`` (§8.1).

    Returns the column values and any warning, or an infeasible Solution when phase 1 fails.
    """
    phase1 = solverlane.run(
        lambda: milp(c=objective, constraints=constraints, integrality=integrality, bounds=bounds)
    )
    if not phase1.success or phase1.x is None:
        return Solution.infeasible(f"phase 1 infeasible: {phase1.message}")
    if scenario.objective == "min_machines":
        return phase1.x, []
    optimum = float(objective @ phase1.x)
    # A MILP optimum is not exact to 1e-6; too tight a pin makes phase 2 infeasible.
    tolerance = (
        max(1e-6, abs(optimum) * 1e-7) if not scenario.integral else max(1e-4, abs(optimum) * 1e-6)
    )
    pin = LinearConstraint(objective.reshape(1, -1), optimum - tolerance, optimum + tolerance)
    phase2 = solverlane.run(
        lambda: milp(
            c=machine_cost,
            constraints=[*constraints, pin],
            integrality=integrality,
            bounds=bounds,
        )
    )
    if phase2.success and phase2.x is not None:
        return phase2.x, []
    return phase1.x, ["phase 2 (minimise machines) failed; counts are not minimal"]

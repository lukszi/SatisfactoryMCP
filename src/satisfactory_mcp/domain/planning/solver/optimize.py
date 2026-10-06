"""LP/MILP factory optimizer: build the matrix, solve it in two phases, read out a build table.

One variable per process (a recipe, extractor or generator at a fixed mode) plus raw, export,
sink and grid flows; power is the pseudo-item ``__MW__``. Every item balance is an equality,
and clocks are discrete modes. docs/planning.md §8.1-§8.4 has the formulation and its reasons.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import LinearConstraint

from .build_table import binding_constraints, build_rows, logistics, solution_warnings
from .lp import (
    _Columns,
    balance_rows,
    bounds_and_group_caps,
    goal_vector,
    integrality_vector,
    machine_price_vector,
    max_machines_row,
    power_row,
    price_machines_for_power,
    recycle_once_rows,
    sloop_budget_row,
    solve_two_phase,
)
from .model import MW, Scenario, Solution
from .processes import build_processes

__all__ = ["free_lunch_audit", "solve"]

_EPS = 1e-7


def solve(sc: Scenario) -> Solution:
    """Solve ``sc``: optimise its goal, then minimise machines at that goal (§8.1)."""
    processes = build_processes(sc)
    if not processes:
        return Solution.infeasible("no processes available")
    columns = _Columns.for_scenario(sc, processes)

    equalities = np.vstack([*balance_rows(processes, columns), power_row(sc, processes, columns)])
    zeros = np.zeros(len(equalities))
    lower, upper, group_caps = bounds_and_group_caps(sc, processes, columns)
    optional = [sloop_budget_row(sc, processes, columns), max_machines_row(sc, columns)]
    constraints = [
        LinearConstraint(equalities, zeros, zeros),
        *group_caps,
        *recycle_once_rows(sc, processes, columns),
        *(row for row in optional if row is not None),
    ]

    goal = goal_vector(sc, processes, columns)
    if isinstance(goal, Solution):
        return goal
    goal_with_machine_price = price_machines_for_power(goal, sc, processes, columns)
    solved = solve_two_phase(
        goal_with_machine_price,
        machine_price_vector(sc, processes, columns),
        constraints,
        (lower, upper),
        integrality_vector(sc, columns),
        sc,
    )
    if isinstance(solved, Solution):
        return solved
    x, warnings = solved

    table = build_rows(sc, processes, x, columns)
    raw_used = {
        item: round(float(x[columns.raw(j)]), 4)
        for j, item in enumerate(columns.raw_items)
        if x[columns.raw(j)] > _EPS
    }
    exports = {
        item: round(float(x[columns.export(j)]), 4)
        for j, item in enumerate(columns.export_items)
        if x[columns.export(j)] > _EPS
    }
    sunk = {
        item: round(float(x[columns.sink(j)]), 4)
        for j, item in enumerate(columns.sink_items)
        if x[columns.sink(j)] > _EPS
    }
    # The goal without the machine price, so objective_value is never understated (§8.4).
    pure_goal = float(goal @ x)
    grid_draw = round(float(x[columns.grid]), 2)
    flows = logistics(sc, processes, x, columns, raw_used)
    warnings += solution_warnings(sc, processes, x, columns, table.dropped, flows, sunk, grid_draw)
    return Solution(
        status="optimal",
        objective_value=round(-pure_goal if sc.objective.startswith("max") else pure_goal, 4),
        net_mw=exports.get(MW, 0.0) - grid_draw,
        processes=table.rows,
        raw_used=raw_used,
        exports=exports,
        sunk=sunk,
        machines_total=round(table.machines_total, 3),
        grid_import_mw=grid_draw,
        machine_penalty_mw=round(float((goal_with_machine_price - goal) @ x), 4),
        logistics=flows,
        warnings=warnings,
        binding=binding_constraints(sc, processes, x, columns),
        payback_curve=table.payback_curve,
        overclock=table.overclock,
    )


def free_lunch_audit(sc: Scenario) -> tuple[bool, float]:
    """Strip every matter source and maximise MW. Must return exactly 0.

    A non-zero result means some recipe cycle creates matter from nothing (docs/planning.md
    §8.3). Run on every solve.
    """
    probe = Scenario(
        game=sc.game,
        recipes=sc.recipes,
        objective="max_mw",
        exports=(MW,),
        raw_caps={},
        extractor_nodes={},
        allow_sinks=False,
        clocks=sc.clocks,
        buildings_available=sc.buildings_available,
    )
    sol = solve(probe)
    value = sol.net_mw if sol.ok else 0.0
    return (abs(value) < 1e-6), value

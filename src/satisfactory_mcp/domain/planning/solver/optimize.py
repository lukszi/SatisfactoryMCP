"""LP/MILP factory optimizer: build the matrix, solve it in two phases, read out a build table.

One variable per process (a recipe, extractor or generator at a fixed mode) plus raw, export,
sink and grid flows; power is the pseudo-item ``__MW__``. Every item balance is an equality,
and clocks are discrete modes. docs/planning.md §8.1-§8.4 has the formulation and its reasons.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint

from ....core.arrays import F64Grid
from ....core.gamedata.constants import AWESOME_SINK_MW
from .build_table import binding_constraints, build_rows, logistics, solution_warnings
from .lp import (
    Columns,
    balance_rows,
    bounds_and_group_caps,
    goal_vector,
    integrality_vector,
    machine_price_vector,
    max_machines_row,
    power_row,
    power_vector,
    price_machines_for_power,
    raw_tie_row,
    recycle_once_rows,
    sloop_budget_row,
    solve_two_phase,
)
from .model import MW, Scenario, SinkRow, Solution
from .processes import build_processes

__all__ = ["free_lunch_audit", "solve"]

_EPS = 1e-7


def _flow_values(
    x: F64Grid, items: tuple[str, ...], column: Callable[[int], int]
) -> dict[str, float]:
    """``{item: rate}`` for each of ``items`` whose column is above zero, to 4 dp."""
    return {
        item: round(float(x[column(j)]), 4) for j, item in enumerate(items) if x[column(j)] > _EPS
    }


def _sink_rows(sc: Scenario, sunk: dict[str, float]) -> list[SinkRow]:
    """Whole AWESOME Sinks for each sunk item, one belt line each, largest first."""
    rows: list[SinkRow] = []
    for item, rate in sorted(sunk.items(), key=lambda kv: -kv[1]):
        sinks = max(1, math.ceil(rate / max(sc.belt_ipm, 1.0) - 1e-9))
        rows.append(
            {
                "item": item,
                "name": sc.game.item_name(item),
                "rate": rate,
                "sinks": sinks,
                "mw": sinks * AWESOME_SINK_MW,
            }
        )
    return rows


def solve(sc: Scenario) -> Solution:
    """Solve ``sc``: optimise its goal, then minimise machines at that goal (§8.1).

    ``min_raw_power`` runs one stage more: least raw first, then least power within
    ``RAW_TIE_SLACK`` of it, then least machines.
    """
    processes = build_processes(sc)
    if not processes:
        return Solution.infeasible("no processes available")
    columns = Columns.for_scenario(sc, processes)

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
    reported = goal
    bounds, integrality = Bounds(lower, upper), integrality_vector(sc, columns)
    if sc.objective == "min_raw_power":
        tie = raw_tie_row(goal, constraints, bounds, integrality)
        if isinstance(tie, Solution):
            return tie
        constraints.append(tie)
        goal = power_vector(sc, processes, columns)
    goal_with_machine_price = price_machines_for_power(goal, sc, processes, columns)
    solved = solve_two_phase(
        goal_with_machine_price,
        machine_price_vector(sc, processes, columns),
        constraints,
        bounds,
        integrality,
        sc,
    )
    if isinstance(solved, Solution):
        return solved
    x, warnings = solved

    table = build_rows(sc, processes, x, columns)
    raw_used = _flow_values(x, columns.raw_items, columns.raw)
    exports = _flow_values(x, columns.export_items, columns.export)
    sunk = _flow_values(x, columns.sink_items, columns.sink)
    # The goal without the machine price, so objective_value is never understated (§8.4).
    pure_goal = float(reported @ x)
    grid_draw = round(float(x[columns.grid]), 2)
    flows = logistics(sc, processes, x, columns, raw_used)
    sinks = _sink_rows(sc, sunk)
    warnings += solution_warnings(sc, processes, x, columns, table.dropped, flows, sinks, grid_draw)
    return Solution(
        status="optimal",
        objective_value=round(-pure_goal if sc.objective.startswith("max") else pure_goal, 4),
        net_mw=exports.get(MW, 0.0) - grid_draw,
        processes=table.rows,
        raw_used=raw_used,
        exports=exports,
        sunk=sunk,
        machines_total=round(table.machines_total + sum(r["sinks"] for r in sinks), 3),
        grid_import_mw=grid_draw,
        machine_penalty_mw=round(float((goal_with_machine_price - goal) @ x), 4),
        logistics=flows,
        warnings=warnings,
        binding=binding_constraints(sc, processes, x, columns),
        payback_curve=table.payback_curve,
        overclock=table.overclock,
        sinks=sinks,
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

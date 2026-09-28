"""HiGHS solves run on long-lived lanes, never on the thread that asked."""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import LinearConstraint, milp

from satisfactory_mcp.core import solverlane

SRC = Path(__file__).resolve().parent.parent / "src"
SOLVERS = {"milp", "linprog"}


def test_run_answers_on_a_lane_thread():
    assert solverlane.run(lambda: threading.current_thread().name).startswith("solver-lane-")


def test_run_raises_the_jobs_error_in_the_caller():
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        solverlane.run(boom)


def test_run_inside_a_lane_does_not_wait_on_itself():
    assert solverlane.run(lambda: solverlane.run(lambda: 7)) == 7


def test_short_lived_threads_that_solve_all_finish():
    def solve(seed: int) -> None:
        r = np.random.default_rng(seed)
        a = r.uniform(0, 5, (20, 30))
        integ = np.zeros(30)
        integ[:10] = 1
        solverlane.run(
            lambda: milp(
                c=-r.uniform(1, 3, 30),
                constraints=LinearConstraint(a, -np.inf, 100),
                integrality=integ,
                bounds=(0, 50),
            )
        )

    for seed in range(80):
        worker = threading.Thread(target=solve, args=(seed,))
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), f"solve {seed} never finished"


def _inside_run(tree: ast.AST) -> set[int]:
    covered: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "solverlane"
        ):
            covered.update(id(inner) for inner in ast.walk(node))
    return covered


def test_every_solver_call_in_the_package_goes_through_a_lane():
    stray = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        covered = _inside_run(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in SOLVERS
                and id(node) not in covered
            ):
                stray.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert stray == []

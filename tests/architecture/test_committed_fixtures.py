"""Every file under tests/fixtures is tracked, so a missing one fails the test that needs it.

A skip would turn a broken checkout into a green run with fewer tests in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.paths import committed_fixture

TESTS = Path(__file__).resolve().parents[1]


def _is_pytest_skip(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skip"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
    )


def _texts(call: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(call)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_a_missing_fixture_fails_rather_than_skips():
    with pytest.raises(pytest.fail.Exception, match="no_such.bin is tracked and missing"):
        committed_fixture("no_such.bin")


def test_no_test_skips_because_a_fixture_is_not_committed():
    offenders = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_pytest_skip(node) and any("not committed" in t for t in _texts(node)):
                offenders.append(f"  {path.relative_to(TESTS)}:{node.lineno}")
    assert not offenders, (
        "read the fixture through tests.support.paths.committed_fixture, which fails:\n"
        + "\n".join(offenders)
    )

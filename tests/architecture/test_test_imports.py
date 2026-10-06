"""Test modules share code through ``tests.support`` and never import one another.

Importing a test module re-runs its collection-time code and couples two files nobody reads
together; ``conftest`` is pytest's to load, not a module to import names from.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def _is_test_module(dotted: str) -> bool:
    parts = dotted.split(".")
    if parts[0] == "tests":
        return len(parts) > 1 and parts[1] != "support"
    return parts[0] == "conftest" or parts[0].startswith("test_")


def _imported(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def test_no_test_module_imports_another_or_conftest():
    offenders = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _imported(tree):
            if _is_test_module(module):
                offenders.append(f"  {path.relative_to(TESTS)}:{line} imports {module}")
    assert not offenders, (
        "move the shared name into tests/support and import it from there:\n" + "\n".join(offenders)
    )

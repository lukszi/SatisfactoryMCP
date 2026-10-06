"""Where the repository and the committed test fixtures are, independent of the caller's folder."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def committed_fixture(name: str) -> Path:
    """``tests/fixtures/<name>``, failing the test when it is absent: every fixture is tracked,
    so a missing one is a broken checkout and never a reason to skip."""
    path = FIXTURES / name
    if not path.is_file():
        pytest.fail(f"tests/fixtures/{name} is tracked and missing -- broken checkout?")
    return path

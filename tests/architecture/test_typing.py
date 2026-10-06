"""Types: pyright's errors per package held to a ratchet, and the ``typing.Any`` exemptions.

The reasons, and how a package graduates to strict, are in docs/DEVELOPING.md, "Types".
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tomllib
from collections import Counter
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from tests.support.import_graph import REPO

PYPROJECT = REPO / "pyproject.toml"

#: pyright's own worker threads: 8 measured 7 s against 20 s for one, and 12 or 16 no faster.
PYRIGHT_THREADS = 8

#: Errors per package under ``[tool.pyright]``. A file counts toward the longest key that holds
#: it. A number only goes down: lower it when the count falls, and fix new errors instead of
#: raising it.
BUDGETS: dict[str, int] = {
    "src/pioneersav": 0,
    "src/satisfactory_mcp": 0,
    "src/satisfactory_mcp/core": 0,
    "src/satisfactory_mcp/core/gameassets": 8,
    "src/satisfactory_mcp/core/gamedata": 0,
    "src/satisfactory_mcp/core/saveio": 3,
    "src/satisfactory_mcp/domain": 0,
    "src/satisfactory_mcp/domain/advice": 0,
    "src/satisfactory_mcp/domain/collectibles": 0,
    "src/satisfactory_mcp/domain/factories": 4,
    "src/satisfactory_mcp/domain/maps": 0,
    "src/satisfactory_mcp/domain/planning/analysis": 0,
    "src/satisfactory_mcp/domain/planning/layout": 1,
    "src/satisfactory_mcp/domain/planning/progress": 0,
    "src/satisfactory_mcp/domain/planning/readout": 0,
    "src/satisfactory_mcp/domain/planning/siting": 0,
    "src/satisfactory_mcp/domain/planning/solver": 2,
    "src/satisfactory_mcp/domain/planning/stored": 0,
    "src/satisfactory_mcp/domain/power": 0,
    "src/satisfactory_mcp/domain/progression": 0,
    "src/satisfactory_mcp/domain/session": 0,
    "src/satisfactory_mcp/domain/spatial": 48,
    "src/satisfactory_mcp/domain/world": 8,
    "src/satisfactory_mcp/interfaces/mcp": 35,
    "src/satisfactory_mcp/interfaces/web": 46,
    "src/satisfactory_mcp/presenters/text": 171,
    "tools": 4,
    "tools/collectibles": 4,
    "tools/mapgen/src/mapgen": 17,
    "tools/mapgen/src/mapgen/enhance": 1,
    "tools/mapgen/src/mapgen/gamedata": 17,
    "tools/mapgen/src/mapgen/lighting": 26,
    "tools/mapgen/src/mapgen/palette": 21,
    "tools/mapgen/src/mapgen/terrain": 15,
    "tools/mapgen/src/mapgen/tiles": 5,
}

#: The ruff rule that bans ``typing.Any``, and the per-file-ignore that exempts the tests.
ANY_BAN = "TID251"
TESTS_PATTERN = "tests/**/*.py"
TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _unmet(extras: dict[str, list[str]]) -> list[str]:
    """The extras' requirements this environment does not meet: the counts are theirs."""
    unmet = []
    for spec in [*extras["dev"], *extras["web"], *extras["gen"]]:
        requirement = Requirement(spec)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            have = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            unmet.append(f"{requirement.name} missing")
            continue
        if not requirement.specifier.contains(have, prereleases=True):
            unmet.append(f"{requirement.name} {have} against {requirement.specifier}")
    return unmet


def _pyright_errors() -> list[str]:
    """The repository-relative file of every error pyright reports under the committed config."""
    unmet = _unmet(_pyproject()["project"]["optional-dependencies"])
    if unmet:
        pytest.fail(f"the typing gate needs every extra as pinned (uv sync --all-extras): {unmet}")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYRIGHT_")}
    command = [sys.executable, "-m", "pyright", "--outputjson", "--pythonpath", sys.executable]
    command += ["--threads", str(PYRIGHT_THREADS), "--project", str(PYPROJECT)]
    done = subprocess.run(
        command, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode not in (0, 1):
        pytest.fail(f"pyright exited {done.returncode}:\n{done.stderr or done.stdout}")
    report = json.loads(done.stdout)
    return [
        Path(os.path.relpath(entry["file"], REPO)).as_posix()
        for entry in report["generalDiagnostics"]
        if entry["severity"] == "error"
    ]


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _package(path: str) -> str | None:
    holders = [key for key in BUDGETS if _within(path, key)]
    return max(holders, key=len) if holders else None


def test_pyright_errors_stay_inside_their_package_budgets() -> None:
    errors = _pyright_errors()
    strict = _pyproject()["tool"]["pyright"]["strict"]
    in_strict = sorted({path for path in errors if any(_within(path, s) for s in strict)})
    assert not in_strict, f"pyright errors in [tool.pyright] strict paths: {in_strict}"

    unowned = sorted({path for path in errors if _package(path) is None})
    assert not unowned, f"errors in files no BUDGETS key holds; add the package: {unowned}"
    missing = sorted(key for key in BUDGETS if not (REPO / key).is_dir())
    assert not missing, f"BUDGETS keys with no such directory; delete them: {missing}"

    counts = Counter(_package(path) for path in errors)
    moved = {key: counts[key] for key, budget in BUDGETS.items() if counts[key] != budget}
    over = [f"{key}: {n} > {BUDGETS[key]}" for key, n in moved.items() if n > BUDGETS[key]]
    assert not over, "new pyright errors; fix them rather than raise a budget: " + "; ".join(over)
    assert not moved, "fewer pyright errors; lower these BUDGETS:\n" + "\n".join(
        f'    "{key}": {n},' for key, n in moved.items()
    )


def _imports_any(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module in TYPING_MODULES:
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names = {node.attr} if node.value.id in TYPING_MODULES else set()
        else:
            continue
        if "Any" in names:
            return True
    return False


def _typing_typeddicts(path: Path) -> list[int]:
    """Lines that take ``TypedDict`` from ``typing`` rather than ``typing_extensions``."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names = {node.attr} if node.value.id == "typing" else set()
        else:
            continue
        if "TypedDict" in names:
            found.append(node.lineno)
    return found


def test_every_typed_dict_is_one_pydantic_accepts_on_python_3_11() -> None:
    """pydantic refuses ``typing.TypedDict`` in a model below 3.12 (docs/web-wire.md, rule 2)."""
    wrong = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in sorted((REPO / "src").rglob("*.py"))
        if "node_modules" not in path.parts
        for line in _typing_typeddicts(path)
    ]
    assert not wrong, f"take TypedDict from typing_extensions: {wrong}"


def test_the_any_exemptions_only_shrink() -> None:
    ignores = _pyproject()["tool"]["ruff"]["lint"]["per-file-ignores"]
    exempt = [pattern for pattern, rules in ignores.items() if ANY_BAN in rules]
    listed = [pattern for pattern in exempt if pattern != TESTS_PATTERN]
    globs = [pattern for pattern in listed if any(char in pattern for char in "*?[{")]
    assert not globs, f"name each module that imports typing.Any, never a glob: {globs}"
    stale = [p for p in listed if not (REPO / p).is_file() or not _imports_any(REPO / p)]
    assert not stale, f"no longer import typing.Any; delete their {ANY_BAN} lines: {stale}"

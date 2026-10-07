"""Types: pyright in strict mode finds nothing, on the venv's Python and on the oldest one, and
the boundaries strict mode cannot see hold.

The reasons are in docs/DEVELOPING.md, "Types".
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from tests.support.import_graph import REPO

PYPROJECT = REPO / "pyproject.toml"

#: pyright's own worker threads: 8 measured 7 s against 20 s for one, and 12 or 16 no faster.
PYRIGHT_THREADS = 8

#: Rules that report the environment rather than the code: a package whose ``py.typed`` is
#: missing, or an import that does not resolve, hides every error behind it.
ENVIRONMENT_RULES = frozenset(
    {"reportMissingImports", "reportMissingModuleSource", "reportMissingTypeStubs"}
)

#: What numpy's and scipy-stubs' stubs, written for the venv's Python, turn into when read as
#: an older one: the run on the venv's own Python counts these.
NEWER_STUB_RULES = frozenset(
    {
        "reportUnknownArgumentType",
        "reportUnknownLambdaType",
        "reportUnknownMemberType",
        "reportUnknownParameterType",
        "reportUnknownVariableType",
    }
)

#: A comment that would take a line or a file out of the count.
SILENCER = re.compile(r"#\s*(type:\s*ignore|pyright:)")

#: Calls whose result is ``Any``, and the annotations that bind one at the boundary: a check
#: narrows it from there.
UNTYPED_LOADS = frozenset(
    {("json", "load"), ("json", "loads"), ("pickle", "load"), ("pickle", "loads")}
    | {("tomllib", "load"), ("tomllib", "loads")}
)
BOUNDARY_TYPES = frozenset({"JsonValue", "object", "dict[str, object]"})

#: ``cast`` calls per area: each is a claim the checker takes on trust. A number only goes
#: down; an area is the longest key that holds the file.
CAST_BUDGETS: dict[str, int] = {
    "src/pioneersav": 0,
    "src/satisfactory_mcp": 0,
    "src/satisfactory_mcp/core": 8,
    "src/satisfactory_mcp/domain": 32,
    "src/satisfactory_mcp/interfaces": 16,
    "src/satisfactory_mcp/presenters": 0,
    "tools": 2,
    "tools/mapgen/src/mapgen": 28,
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


def _relative(file: str) -> str:
    return Path(os.path.relpath(file, REPO)).as_posix()


def _pyright_errors(*options: str) -> list[dict]:
    """Every error pyright reports under the committed config, with ``options`` added."""
    unmet = _unmet(_pyproject()["project"]["optional-dependencies"])
    if unmet:
        pytest.fail(f"the typing gate needs every extra as pinned (uv sync --all-extras): {unmet}")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYRIGHT_")}
    command = [sys.executable, "-m", "pyright", "--outputjson", "--pythonpath", sys.executable]
    command += ["--threads", str(PYRIGHT_THREADS), "--project", str(PYPROJECT), *options]
    done = subprocess.run(
        command, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode not in (0, 1):
        pytest.fail(f"pyright exited {done.returncode}:\n{done.stderr or done.stdout}")
    diagnostics = json.loads(done.stdout)["generalDiagnostics"]
    unresolved = sorted(
        f"{_relative(entry['file'])}: {entry['message']}"
        for entry in diagnostics
        if entry.get("rule") in ENVIRONMENT_RULES
    )
    if unresolved:
        pytest.fail(
            "the environment, not the code: rebuild it (uv sync --all-extras --locked); "
            "these imports do not resolve or ship no types:\n" + "\n".join(unresolved[:20])
        )
    return [entry for entry in diagnostics if entry["severity"] == "error"]


def _shown(errors: list[dict]) -> str:
    return "\n".join(
        f"  {_relative(e['file'])}:{e['range']['start']['line'] + 1}: [{e.get('rule')}] "
        + e["message"].splitlines()[0]
        for e in errors[:40]
    )


def _oldest_python() -> str:
    """The lowest Python ``requires-python`` admits, as ``3.11``."""
    floors = [
        spec.version
        for spec in SpecifierSet(_pyproject()["project"]["requires-python"])
        if spec.operator == ">="
    ]
    assert len(floors) == 1, "requires-python names one lower bound"
    return ".".join(floors[0].split(".")[:2])


@pytest.mark.long
def test_pyright_finds_nothing_in_strict_mode() -> None:
    config = _pyproject()["tool"]["pyright"]
    assert config["typeCheckingMode"] == "strict" and "strict" not in config
    errors = _pyright_errors()
    assert not errors, f"{len(errors)} pyright errors; fix them:\n{_shown(errors)}"


@pytest.mark.long
def test_the_code_type_checks_on_the_oldest_python() -> None:
    """The same run as the oldest supported Python reads it: a standard-library name that
    arrived later fails here. The rules a newer stub trips over are the main run's."""
    errors = [
        e
        for e in _pyright_errors("--pythonversion", _oldest_python())
        if e.get("rule") not in NEWER_STUB_RULES
    ]
    assert not errors, f"pyright errors on Python {_oldest_python()}:\n{_shown(errors)}"


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


def _sources() -> list[Path]:
    """Every module the type rules cover: ``src`` and ``tools``, less the page and mapgen's tests."""
    skip = ("src/satisfactory_mcp/interfaces/web/frontend/", "tools/mapgen/tests/")
    return [
        path
        for root in ("src", "tools")
        for path in sorted((REPO / root).rglob("*.py"))
        if "node_modules" not in path.parts
        and not path.relative_to(REPO).as_posix().startswith(skip)
    ]


def _silencers(path: Path) -> list[int]:
    source = path.read_text(encoding="utf-8")
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and SILENCER.search(token.string)
    ]


def test_no_comment_silences_the_checker() -> None:
    """``# type: ignore`` and ``# pyright:`` would hide an error from the count; fix it instead."""
    found = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in _sources()
        for line in _silencers(path)
    ]
    assert not found, f"comments that silence pyright: {found}"


def _unbound_loads(path: Path) -> list[int]:
    """Lines where a ``json``, ``pickle`` or ``tomllib`` load binds to no boundary type."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in UNTYPED_LOADS
        ):
            continue
        holder = parents.get(node)
        while isinstance(holder, ast.IfExp):
            holder = parents.get(holder)
        if isinstance(holder, ast.AnnAssign) and ast.unparse(holder.annotation) in BOUNDARY_TYPES:
            continue
        found.append(node.lineno)
    return found


def test_a_loaded_document_binds_to_a_boundary_type() -> None:
    """What ``json.load`` and its kin return is ``Any``, which strict mode does not see: bound
    to a TypedDict it is an unchecked cast. It binds to ``JsonValue`` (or ``object``), and a
    check or one named ``cast`` narrows it from there."""
    found = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in _sources()
        for line in _unbound_loads(path)
    ]
    assert not found, f"bind these to {sorted(BOUNDARY_TYPES)}: {found}"


def _casts(path: Path) -> int:
    """How many ``cast`` / ``typing.cast`` calls ``path`` makes."""
    count = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        func = node.func if isinstance(node, ast.Call) else None
        if (
            isinstance(func, ast.Name)
            and func.id == "cast"
            or (
                isinstance(func, ast.Attribute)
                and func.attr == "cast"
                and isinstance(func.value, ast.Name)
                and func.value.id in TYPING_MODULES
            )
        ):
            count += 1
    return count


def _area(path: str) -> str:
    holders = [key for key in CAST_BUDGETS if path == key or path.startswith(key + "/")]
    assert holders, f"{path} is in no CAST_BUDGETS area"
    return max(holders, key=len)


def test_casts_only_go_down() -> None:
    """A ``cast`` is a claim the checker takes on trust, so a new one has to replace an old one."""
    counts: Counter[str] = Counter()
    for path in _sources():
        counts[_area(path.relative_to(REPO).as_posix())] += _casts(path)
    moved = {key: counts[key] for key, budget in CAST_BUDGETS.items() if counts[key] != budget}
    over = [
        f"{key}: {n} > {CAST_BUDGETS[key]}" for key, n in moved.items() if n > CAST_BUDGETS[key]
    ]
    assert not over, "new casts; type the producer instead: " + "; ".join(over)
    assert not moved, "fewer casts; lower these CAST_BUDGETS:\n" + "\n".join(
        f'    "{key}": {n},' for key, n in moved.items()
    )


def _annotations(tree: ast.Module) -> list[ast.expr]:
    """Every annotation in ``tree``, and the value of every ``X: TypeAlias = ...``."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            found.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns:
            found.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
            if node.value is not None and ast.unparse(node.annotation) == "TypeAlias":
                found.append(node.value)
    return found


def _bare_arrays(path: Path) -> list[int]:
    """Lines whose annotations name ``ndarray`` with no shape and dtype."""
    lines = []
    for annotation in _annotations(ast.parse(path.read_text(encoding="utf-8"))):
        nodes = list(ast.walk(annotation))
        typed = {id(node.value) for node in nodes if isinstance(node, ast.Subscript)}
        lines += [
            node.lineno
            for node in nodes
            if isinstance(node, ast.Attribute) and node.attr == "ndarray" and id(node) not in typed
        ]
    return lines


def test_an_array_names_its_dtype() -> None:
    """A bare ``np.ndarray`` holds anything; ``core/arrays.py`` names the dtype."""
    found = [
        f"{path.relative_to(REPO).as_posix()}:{line}"
        for path in _sources()
        for line in _bare_arrays(path)
    ]
    assert not found, f"name the dtype (core/arrays.py): {found}"

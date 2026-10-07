"""The import graph the architecture tests read: every import of every module, by AST.

Lazy imports inside function bodies are edges too, which is why the graph is read off the
source rather than off ``sys.modules``. Standard library only, so the rules run on a machine
with no game, no Node and no web extra.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
PKG = SRC / "satisfactory_mcp"
TOOLS = REPO / "tools"
PARSER = "pioneersav"
PARSER_PKG = SRC / PARSER
WEB = PKG / "interfaces" / "web"
WEB_ROUTERS = WEB / "routers"

#: The ``gen`` extra: ``ooz`` (from pyooz), ``texture2ddecoder`` and Pillow, which the
#: generators need to read the installed game's container, ``zstandard``, the render
#: caches' codec (``mapgen.bandstore``), and ``numba``, the render's kernels (``mapgen.jit``).
GEN_EXTRA_ROOTS = frozenset({"ooz", "pyooz", "texture2ddecoder", "PIL", "zstandard", "numba"})

#: The ``gpu`` extra: CuPy, which compiles and runs the render's CUDA kernels (``mapgen.jit``).
GPU_EXTRA_ROOTS = frozenset({"cupy"})


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def sources(root: Path) -> list[Path]:
    """Every Python file under ``root`` that is this project's own (never ``node_modules``)."""
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and "node_modules" not in path.parts
    )


def module_name(path: Path) -> str:
    """The dotted name a file under ``src/`` is imported as."""
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(path: Path) -> str:
    """The package an import inside ``path`` is relative to."""
    name = module_name(path)
    return name if path.name == "__init__.py" else name.rsplit(".", 1)[0]


def is_module(dotted: str) -> bool:
    """True when the dotted name is a file on disk rather than a symbol inside one."""
    base = SRC.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def root_of(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def covers(target: str, prefixes: tuple[str, ...]) -> bool:
    return any(target == prefix or target.startswith(prefix + ".") for prefix in prefixes)


def targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Every module this import node reaches, absolute and dotted.

    The module imported from is an edge, and so is each imported name that is a submodule
    rather than a symbol -- only the filesystem can tell ``from .. import config`` from
    ``from .. import clamp``.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        parts = package.split(".")
        parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        base = ".".join(parts)
        resolved = f"{base}.{node.module}" if node.module else base
    else:
        resolved = node.module or ""
    if not resolved:
        return []
    found = [resolved]
    found.extend(
        f"{resolved}.{alias.name}"
        for alias in node.names
        if alias.name != "*" and is_module(f"{resolved}.{alias.name}")
    )
    return found


@functools.cache
def edges(root: Path = PKG) -> frozenset[tuple[str, str]]:
    """Every (importer, target) module pair under ``root``, lazy imports included."""
    found: set[tuple[str, str]] = set()
    for path in sources(root):
        importer = module_name(path)
        package = package_of(path)
        for node in ast.walk(parse(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in targets(node, package):
                    found.add((importer, target))
    return frozenset(found)


@functools.cache
def tools_edges() -> frozenset[tuple[str, str]]:
    """Every (importer, target) pair in ``tools/``, named as the ``tools`` package imports it."""
    found: set[tuple[str, str]] = set()
    for path in sources(TOOLS):
        parts = path.relative_to(TOOLS).with_suffix("").parts
        importer = ".".join(("tools", *(parts[:-1] if parts[-1] == "__init__" else parts)))
        for node in ast.walk(parse(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in targets(node, "tools"):
                    found.add((importer, target))
    return frozenset(found)


def import_nodes(
    node: ast.AST, in_function: bool = False
) -> Iterator[tuple[ast.Import | ast.ImportFrom, bool]]:
    """Every import in the tree, paired with whether a function body encloses it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            yield child, in_function
        deeper = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        yield from import_nodes(child, deeper)


def docstring_ids(tree: ast.AST) -> set[int]:
    """The identity of every docstring node, so prose can be told from a string value."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(id(value))
    return found

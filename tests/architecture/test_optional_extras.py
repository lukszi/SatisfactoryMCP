"""The ``gen`` extra is optional at import time: only ``core.gameassets`` names it, lazily.

The reasons are in docs/DEVELOPING.md, "Architecture rules".
"""

from __future__ import annotations

import ast
import sys

from tests.support.import_graph import (
    GEN_EXTRA_ROOTS,
    GPU_EXTRA_ROOTS,
    PARSER_PKG,
    PKG,
    edges,
    import_nodes,
    module_name,
    package_of,
    parse,
    root_of,
    sources,
    targets,
)

#: The one package allowed to name them, and only inside a function body.
GAMEASSETS = "satisfactory_mcp.core.gameassets"

#: What ``core.gameassets`` may import at module scope besides the stdlib and ``core``:
#: numpy, a hard dependency of the project rather than an extra.
GAMEASSETS_HARD_ROOTS = frozenset({"numpy"})


def _gameassets_sources():
    return sources(PKG / "core" / "gameassets")


def _describe(pairs: set[tuple[str, str]]) -> str:
    return "\n".join(f"  {importer} -> {target}" for importer, target in sorted(pairs))


def test_the_gen_extra_is_optional_at_import_time():
    outside = {
        (importer, target)
        for importer, target in edges() | edges(PARSER_PKG)
        if root_of(target) in GEN_EXTRA_ROOTS
        and importer != GAMEASSETS
        and not importer.startswith(GAMEASSETS + ".")
    }
    assert not outside, (
        "the `gen` extra is generation-time only -- these modules would stop importing on a "
        f"machine that has not installed it, and only {GAMEASSETS} may name it:\n"
        + _describe(outside)
    )

    eager = []
    for path in _gameassets_sources():
        for node, in_function in import_nodes(parse(path)):
            if in_function:
                continue
            for target in targets(node, package_of(path)):
                if root_of(target) in GEN_EXTRA_ROOTS:
                    eager.append(f"  {module_name(path)}:{node.lineno} imports {target}")
    assert not eager, (
        "these imports of the `gen` extra run at import time -- move them inside the "
        "function that needs them, the way `iostore.oodle_decompress` does:\n"
        + "\n".join(sorted(eager))
    )


def test_the_gpu_extra_is_the_generators_alone():
    """CuPy runs the render's CUDA kernels in ``tools/mapgen``; the package never names it."""
    found = {
        (importer, target)
        for importer, target in edges() | edges(PARSER_PKG)
        if root_of(target) in GPU_EXTRA_ROOTS
    }
    assert not found, "the `gpu` extra is the generators' alone:\n" + _describe(found)


def test_gameassets_never_imports_dynamically():
    """No ``importlib``, ``__import__`` or ``sys.path``: each would hide an import from the AST."""
    found = []
    for path in _gameassets_sources():
        name = module_name(path)
        tree = parse(path)
        for node, _in_function in import_nodes(tree):
            for target in targets(node, package_of(path)):
                if root_of(target) == "importlib":
                    found.append(f"  {name}:{node.lineno} imports {target}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__import__":
                found.append(f"  {name}:{node.lineno} calls __import__")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                found.append(f"  {name}:{node.lineno} touches sys.path")
    assert not found, (
        "this package reaches its decoders through a parameter and its own imports, and "
        "nothing else -- a dynamic import here makes the layering unreadable rather than "
        "merely unusual:\n" + "\n".join(sorted(found))
    )


def test_gameassets_imports_nothing_but_the_stdlib_and_core():
    stray = []
    for path in _gameassets_sources():
        name = module_name(path)
        for node, _in_function in import_nodes(parse(path)):
            for target in targets(node, package_of(path)):
                root = root_of(target)
                allowed = (
                    root in sys.stdlib_module_names
                    or root in GEN_EXTRA_ROOTS
                    or root in GAMEASSETS_HARD_ROOTS
                    or target == "satisfactory_mcp.config"
                    or target == "satisfactory_mcp.core"
                    or target.startswith("satisfactory_mcp.core.")
                )
                if not allowed:
                    stray.append(f"  {name}:{node.lineno} imports {target}")
    assert not stray, (
        "core/gameassets may import the standard library, satisfactory_mcp.core (and "
        "config), numpy, and the `gen` extra from inside a function -- nothing else, or "
        "reading the game's assets stops being something the server can be built without:\n"
        + "\n".join(sorted(stray))
    )

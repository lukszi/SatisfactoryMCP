"""Imports run one way: core, domain, presenters, interfaces; the parser and tools/ beside them.

The reasons for each rule are in docs/DEVELOPING.md, "Architecture rules".
"""

from __future__ import annotations

import sys

from tests.support.import_graph import (
    GEN_EXTRA_ROOTS,
    PARSER,
    PARSER_PKG,
    PKG,
    covers,
    edges,
    is_module,
    root_of,
    tools_edges,
)

#: Longest-prefix-first layer map, applied to importer and target alike. ``tools`` is the
#: generators, a layer above everything that no layer may import.
LAYERS: tuple[tuple[str, str], ...] = (
    ("satisfactory_mcp.core", "core"),
    ("satisfactory_mcp.domain", "domain"),
    ("satisfactory_mcp.presenters", "presenters"),
    ("satisfactory_mcp.interfaces", "interfaces"),
    ("satisfactory_mcp.config", "core"),
    ("satisfactory_mcp.server", "interfaces"),
    ("tools", "tools"),
)

#: Third-party packages of the outside world, checked as one pseudo-layer.
SDK_ROOTS = frozenset({"mcp", "pydantic", "fastapi", "uvicorn", "starlette"})

#: Who may import whom. A layer always may import itself; no row lists ``tools``.
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "domain": frozenset({"domain", "core"}),
    "presenters": frozenset({"presenters", "domain", "core"}),
    "interfaces": frozenset({"interfaces", "presenters", "domain", "core", "sdk"}),
    "tools": frozenset({"tools", "core", "domain"}),
}

#: Known violations. Empty, and a new entry has to be argued for in the diff that adds it.
WHITELIST: frozenset[tuple[str, str]] = frozenset()

#: The pre-refactor import paths, deleted, with where each one went.
FORBIDDEN_PATHS: dict[str, str] = {
    "docs": "core.gamedata",
    "save": "core.saveio + domain.world.state",
    "graph": "domain.factories",
    "spatial": "domain.spatial",
    "planning": "domain.planning",
    "tools": "interfaces.mcp.tools",
    "app": "interfaces.mcp.app",
    "render": "presenters.text.primitives",
}

#: The homes the refactor moved things to, asserted to exist by literal name.
LAYERED_HOMES: tuple[str, ...] = (
    "satisfactory_mcp.core.gamedata",
    "satisfactory_mcp.core.gameassets",
    "satisfactory_mcp.core.saveio",
    "satisfactory_mcp.core.text",
    "satisfactory_mcp.domain.world",
    "satisfactory_mcp.domain.progression",
    "satisfactory_mcp.domain.power",
    "satisfactory_mcp.domain.factories",
    "satisfactory_mcp.domain.spatial",
    "satisfactory_mcp.domain.collectibles",
    "satisfactory_mcp.domain.planning",
    "satisfactory_mcp.domain.session",
    "satisfactory_mcp.domain.advice",
    "satisfactory_mcp.presenters.text",
    "satisfactory_mcp.interfaces.mcp",
    "satisfactory_mcp.interfaces.web",
)

#: Everything the package root may contain: the four layers, two modules, the marker.
ROOT_ENTRIES: frozenset[str] = frozenset(
    {"__init__.py", "config.py", "server.py", "core", "domain", "presenters", "interfaces"}
)

PRESENTER_ROOTS = ("satisfactory_mcp.presenters",)

#: The one module in the application allowed to import the parser: it runs in the child.
PARSER_IMPORTER = "satisfactory_mcp.core.saveio.extract.parser"

#: What ``tools/`` may import beyond the standard library and the ``gen`` extra.
TOOLS_ALLOWED_PREFIXES: tuple[str, ...] = ("tools", "mapgen", "satisfactory_mcp.core")

#: Measured exceptions: three hard dependencies, and the parser read by a one-shot CLI.
TOOLS_EXTRA_ROOTS = frozenset({"numpy", "scipy", "platformdirs", "pioneersav"})

#: The generators that write the terrain field read the package that reads it.
TOOLS_EXTRA_PREFIXES: tuple[str, ...] = ("satisfactory_mcp.domain.spatial",)


def layer(module: str) -> str | None:
    """The layer a dotted module belongs to, or None (bare ``satisfactory_mcp`` included)."""
    if module.split(".", 1)[0] in SDK_ROOTS:
        return "sdk"
    best: tuple[int, str] | None = None
    for prefix, name in LAYERS:
        if (module == prefix or module.startswith(prefix + ".")) and (
            best is None or len(prefix) > best[0]
        ):
            best = (len(prefix), name)
    return None if best is None else best[1]


def violations() -> set[tuple[str, str]]:
    """Edges that cross a layer boundary the wrong way."""
    bad: set[tuple[str, str]] = set()
    for importer, target in edges():
        source_layer, target_layer = layer(importer), layer(target)
        if source_layer is None or target_layer is None:
            continue
        if target_layer not in ALLOWED[source_layer]:
            bad.add((importer, target))
    return bad


def describe(pairs: set[tuple[str, str]]) -> str:
    return "\n".join(
        f"  {importer} ({layer(importer)}) -> {target} ({layer(target)})"
        for importer, target in sorted(pairs)
    )


def test_no_new_violations():
    new = violations() - WHITELIST
    assert not new, (
        "import layering violated -- domain and core must not know presenters, "
        f"interfaces or the SDK:\n{describe(new)}"
    )


def test_whitelist_is_not_stale():
    gone = WHITELIST - violations()
    assert not gone, (
        f"stale whitelist entry -- the violation is gone, delete the entry:\n{describe(gone)}"
    )


def test_whitelist_is_empty():
    assert WHITELIST == frozenset(), (
        "the whitelist is meant to stay empty -- a new exemption needs a reason "
        f"written next to it:\n{describe(set(WHITELIST))}"
    )


def test_domain_and_core_never_import_a_presenter():
    """Spelled literally, so editing ``LAYERS`` cannot dissolve the rule."""
    leaks = {
        (importer, target)
        for importer, target in edges()
        if layer(importer) in {"core", "domain"} and covers(target, PRESENTER_ROOTS)
    }
    assert not leaks, (
        "domain and core must return data, never formatted text -- move the "
        f"formatting into presenters/text instead:\n{describe(leaks)}"
    )


def test_the_layered_homes_exist():
    missing = [name for name in LAYERED_HOMES if not is_module(name)]
    assert not missing, (
        "the layered tree is incomplete -- these are the agreed homes and they "
        "have to exist:\n" + "\n".join(f"  {name}" for name in missing)
    )


def test_the_package_root_holds_only_the_layers():
    found = {p.name for p in PKG.iterdir() if p.name != "__pycache__"}
    assert found == ROOT_ENTRIES, (
        "the package root drifted -- everything belongs in core, domain, "
        "presenters or interfaces:\n"
        f"  unexpected: {sorted(found - ROOT_ENTRIES)}\n"
        f"  missing:    {sorted(ROOT_ENTRIES - found)}"
    )


def test_the_old_paths_stay_deleted():
    for name, moved_to in sorted(FORBIDDEN_PATHS.items()):
        for candidate in (PKG / name, PKG / f"{name}.py"):
            assert not candidate.exists(), (
                f"satisfactory_mcp.{name} is back -- it moved to {moved_to} and the old "
                "path is not a place code may live again; fix the caller's import instead"
            )


def test_the_parser_knows_nothing_about_the_application():
    leaks = {
        (importer, target)
        for importer, target in edges(PARSER_PKG)
        if covers(target, ("satisfactory_mcp",))
    }
    assert not leaks, (
        f"{PARSER} is a standalone library and must not import this application -- move "
        "whatever it needs into the caller:\n" + describe(leaks)
    )


def test_only_the_extractor_imports_the_parser():
    wrong = {
        (importer, target)
        for importer, target in edges()
        if covers(target, (PARSER,)) and importer != PARSER_IMPORTER
    }
    assert not wrong, (
        f"the parser lives behind a subprocess -- only {PARSER_IMPORTER} may import "
        f"{PARSER}, everything else goes through core.saveio.projection:\n" + describe(wrong)
    )


def test_the_generators_reach_down_and_nothing_reaches_up_to_them():
    stray = []
    for importer, target in sorted(tools_edges()):
        root = root_of(target)
        allowed = (
            root in sys.stdlib_module_names
            or root in GEN_EXTRA_ROOTS
            or root in TOOLS_EXTRA_ROOTS
            or covers(target, TOOLS_ALLOWED_PREFIXES)
            or covers(target, TOOLS_EXTRA_PREFIXES)
        )
        if not allowed:
            stray.append(f"  {importer} -> {target}")
    assert not stray, (
        "a generator may read the standard library, the `gen` extra, satisfactory_mcp.core "
        "and itself -- anything else either makes the server depend on a generation-time "
        "package or points a generator at a layer above core. Fix the import, or add it to "
        "TOOLS_EXTRA_ROOTS / TOOLS_EXTRA_PREFIXES with the reason:\n" + "\n".join(stray)
    )


def test_nothing_in_the_package_imports_a_generator():
    """The generators are not in the wheel, so such an import fails on every install."""
    reaching = {
        (importer, target)
        for importer, target in edges() | edges(PARSER_PKG)
        if covers(target, ("tools",))
    }
    assert not reaching, (
        "the generators are not shipped -- nothing in the package or the parser may import "
        "them, and whatever is wanted belongs in core:\n" + describe(reaching)
    )

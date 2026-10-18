"""Game data reaches world code through the world's rules only (save-projection.md §6.18).

A world's table is ``WorldState.game``, which ``domain.rules.world_game`` makes. The table as
shipped is reached by two named accessors, and only where no world is in scope or only names
are read; a new consumer that skips the world's rules fails here instead of reading vanilla.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from tests.support.import_graph import PKG, covers, edges, module_name, parse, sources

APP = "satisfactory_mcp"
RULES = f"{APP}.domain.rules"

#: The accessors that hand out the table as shipped.
VANILLA_ACCESSORS = frozenset({"vanilla_game", "vanilla_game_data"})

#: Who may reach the shipped table, and why each needs no world's rules.
VANILLA_READERS: dict[str, str] = {
    f"{APP}.interfaces.mcp.app": "loads the table WorldState scales; name lookups",
    f"{APP}.interfaces.mcp.resources": "the docs census counts classes",
    f"{APP}.interfaces.mcp.tools.gamedata": "search_items shows names, forms, MJ and sink points",
    f"{APP}.interfaces.mcp.tools.planning._requests": "resolves recipe names",
    f"{APP}.interfaces.web.serial.responses": "defines the accessor and the world fallback",
    f"{APP}.interfaces.web.routers.codex.gamedata": "the codex with no readable save; item rows",
    f"{APP}.interfaces.web.routers.codex.search": "the header's jump search shows names",
    f"{APP}.interfaces.web.routers.world.world_finders": "resolves a resource name",
}

#: The modules that build the shipped table, and who may import them.
LOADERS = (f"{APP}.core.gamedata.loader", f"{APP}.core.gamedata.normalize")
LOADER_IMPORTERS = (
    f"{APP}.core.gamedata",
    f"{APP}.interfaces.mcp.app",
    f"{APP}.interfaces.web.app",
)

#: Where the web app keeps its shipped-table loader on ``app.state``.
STATE_GAME_READERS = frozenset(
    {f"{APP}.interfaces.web.app", f"{APP}.interfaces.web.serial.responses"}
)

#: Who may read the projection's game-mode record: the saveio that writes it, the rules
#: that read it, and the worlds route that sends it as its own wire field.
GAME_MODE_READERS = (f"{APP}.core.saveio", RULES, f"{APP}.interfaces.web.routers.world.worlds")


def _trees() -> Iterator[tuple[str, ast.Module]]:
    for path in sources(PKG):
        yield module_name(path), parse(path)


def _uses(tree: ast.AST, names: frozenset[str]) -> list[int]:
    """Lines that read one of ``names`` as a value: a call or a reference, never an import."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in names
            and isinstance(node.ctx, ast.Load)
            or isinstance(node, ast.Attribute)
            and node.attr in names
        ):
            lines.append(node.lineno)
    return lines


def _report(found: list[str]) -> str:
    return "\n".join(f"  {line}" for line in sorted(found))


def test_the_shipped_table_is_read_only_where_no_world_applies():
    found = [
        f"{module}:{line}"
        for module, tree in _trees()
        if module not in VANILLA_READERS
        for line in _uses(tree, VANILLA_ACCESSORS)
    ]
    assert not found, (
        "code with a world in hand reads st.game, which carries the world's game mode; "
        "app.game_of(st) / serial.game_of(request, st) fall back to vanilla without one:\n"
        + _report(found)
    )


def test_the_vanilla_readers_list_is_not_stale():
    readers = {module for module, tree in _trees() if _uses(tree, VANILLA_ACCESSORS)}
    gone = set(VANILLA_READERS) - readers
    assert not gone, f"no longer reads the shipped table, drop it from the list: {sorted(gone)}"


def test_only_the_apps_build_the_shipped_table():
    found = [
        f"{importer} -> {target}"
        for importer, target in edges()
        if covers(target, LOADERS) and not covers(importer, LOADER_IMPORTERS)
    ]
    assert not found, "game data is loaded once per app and reached through it:\n" + _report(found)


def test_the_web_apps_loader_stays_behind_its_accessor():
    found = [
        f"{module}:{node.lineno}"
        for module, tree in _trees()
        if module not in STATE_GAME_READERS
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "game"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "state"
    ]
    assert not found, "read the game through serial.game_of or vanilla_game_data:\n" + _report(
        found
    )


def test_only_the_rules_package_reaches_its_internals():
    found = [
        f"{importer} -> {target}"
        for importer, target in edges()
        if target.startswith(RULES + ".") and not covers(importer, (RULES,))
    ]
    assert not found, (
        "import the seam, satisfactory_mcp.domain.rules; how a factor scales a value is its "
        "own business:\n" + _report(found)
    )


def test_only_the_rules_read_the_game_mode_record():
    found = [
        f"{module}:{node.lineno}"
        for module, tree in _trees()
        if not covers(module, GAME_MODE_READERS)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "game_mode"
    ]
    assert not found, (
        "a world's multipliers are domain.rules' to apply; read rules.mode_of or the "
        "world's table instead:\n" + _report(found)
    )


def test_the_rules_package_exists():
    assert (PKG / Path(*RULES.split(".")[1:]) / "__init__.py").is_file()

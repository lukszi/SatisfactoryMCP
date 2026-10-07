"""Line caps: a module past its cap holds a second concern and wants a module of its own.

The reasons and the measurements behind each number are in docs/DEVELOPING.md,
"Architecture rules".
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from tests.support.import_graph import (
    PARSER_PKG,
    PKG,
    REPO,
    TOOLS,
    WEB_ROUTERS,
    line_count,
    parse,
    sources,
)

#: Any module of the application or the parser.
MODULE_MAX_LINES = 850
#: A router module under ``interfaces/web/routers``.
ROUTER_MAX_LINES = 650
#: A module of the MCP adapter under ``interfaces/mcp``.
TOOL_MODULE_MAX_LINES = 650
#: A generator under ``tools/`` outside ``tools/mapgen`` (which holds its own caps in its own
#: suite), and one function in it.
GENERATOR_MAX_LINES = 800
GENERATOR_FUNCTION_MAX_LINES = 150
#: A hand-written TypeScript module of the map page, and one top-level function in it.
FRONTEND_MAX_LINES = 600
FRONTEND_FUNCTION_MAX_LINES = 150

FRONTEND_SRC = PKG / "interfaces" / "web" / "frontend" / "src"
#: Written by openapi-typescript from the server's schema, not by hand.
FRONTEND_GENERATED = {"schema.d.ts"}

#: The first line of a top-level function: a declaration, or a function or arrow bound to a
#: name. Formatted code closes it with a ``}`` at the margin.
_TS_FUNCTION = re.compile(
    r"(export )?(default )?(async )?function\b"
    r"|(export )?(const|let) \w+(: [^=]+)? = (async )?(function\b|\(|\w+ =>)"
)


def _frontend_sources() -> list[Path]:
    return sorted(p for p in FRONTEND_SRC.rglob("*.ts") if p.name not in FRONTEND_GENERATED)


def _ts_functions(lines: list[str]) -> Iterator[tuple[int, int]]:
    """``(first, last)`` line index of each top-level function with a body.

    The body ends at the first ``}`` on the margin; a margin line that is neither that nor a
    signature's closing ``)`` means the start was a one-line binding.
    """
    for first, line in enumerate(lines):
        if not _TS_FUNCTION.match(line) or line.rstrip().endswith(";"):
            continue
        for last in range(first + 1, len(lines)):
            text = lines[last]
            if text.startswith("}"):
                yield first, last
                break
            if text and not text[0].isspace() and not text.startswith(")"):
                break


def _over(paths, cap: int) -> list[str]:
    return [
        f"  {path.relative_to(REPO).as_posix()}: {lines} lines"
        for path in paths
        if (lines := line_count(path)) > cap
    ]


def _generator_sources():
    return [p for p in sources(TOOLS) if "mapgen" not in p.relative_to(TOOLS).parts]


def test_no_module_grows_into_a_god_module():
    over = _over([*sources(PKG), *sources(PARSER_PKG)], MODULE_MAX_LINES)
    assert not over, (
        f"over the {MODULE_MAX_LINES}-line module cap -- split it by concern into a package "
        "rather than raise the cap:\n" + "\n".join(over)
    )


def test_no_router_grows_back_into_a_one_file_api():
    over = _over(sources(WEB_ROUTERS), ROUTER_MAX_LINES)
    assert not over, (
        f"a router module is over {ROUTER_MAX_LINES} lines -- that is a second concern, and "
        "it wants its own file and its own entry at the END of ALL_ROUTERS (never in the "
        "middle: the tuple's order is the committed schema's path order):\n" + "\n".join(over)
    )


def test_no_tool_module_grows_back_into_a_god_module():
    over = _over(sources(PKG / "interfaces" / "mcp"), TOOL_MODULE_MAX_LINES)
    assert not over, (
        f"an MCP module is over {TOOL_MODULE_MAX_LINES} lines -- that is a second concern; "
        "give it its own module in the tool's package:\n" + "\n".join(over)
    )


def test_generator_modules_stay_under_the_line_cap():
    over = _over(_generator_sources(), GENERATOR_MAX_LINES)
    assert not over, f"over the {GENERATOR_MAX_LINES}-line cap:\n" + "\n".join(over)


def test_no_generator_function_grows_past_its_cap():
    over = []
    for path in _generator_sources():
        for node in ast.walk(parse(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = node.end_lineno - node.lineno + 1
                if lines > GENERATOR_FUNCTION_MAX_LINES:
                    over.append(f"  {path.relative_to(REPO).as_posix()}::{node.name}: {lines}")
    assert not over, (
        f"over the {GENERATOR_FUNCTION_MAX_LINES}-line function cap -- split it rather than "
        "raise the cap:\n" + "\n".join(over)
    )


def test_no_page_module_grows_past_its_cap():
    sources = _frontend_sources()
    assert sources, f"no TypeScript under {FRONTEND_SRC}"
    over = _over(sources, FRONTEND_MAX_LINES)
    assert not over, (
        f"a page module is over {FRONTEND_MAX_LINES} lines -- that is a second concern; give "
        "it a module of its own beside this one:\n" + "\n".join(over)
    )


def test_no_page_function_grows_past_its_cap():
    over = []
    measured = 0
    for path in _frontend_sources():
        lines = path.read_text(encoding="utf-8").splitlines()
        for first, last in _ts_functions(lines):
            measured += 1
            if last - first + 1 > FRONTEND_FUNCTION_MAX_LINES:
                where = f"{path.relative_to(REPO).as_posix()}:{first + 1}"
                over.append(f"  {where}: {last - first + 1}")
    assert measured, "no top-level function found -- the pattern no longer reads the page"
    assert not over, (
        f"over the {FRONTEND_FUNCTION_MAX_LINES}-line function cap -- split it rather than "
        "raise the cap:\n" + "\n".join(over)
    )

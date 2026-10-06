"""The comment budget of docs/comments.md, measured per file: prose lines over code lines.

A RATCHET, not an aspiration. Each cap is the highest ratio measured in its tree plus a small
working margin, so the suite fails the moment a file grows a new essay. They are not the
numbers docs/comments.md argues for, and lowering a cap is a deliberate second pass over the
files it would fail, never a constant edited on its own.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGETS = [
    (ROOT / "src" / "satisfactory_mcp" / "interfaces", 0.80),
    (ROOT / "src" / "satisfactory_mcp" / "presenters", 0.40),
    (ROOT / "tools", 0.65),
    (ROOT / "src" / "satisfactory_mcp" / "domain", 0.70),
    (ROOT / "src" / "satisfactory_mcp" / "core", 0.95),
    (ROOT / "src" / "pioneersav", 0.95),
    (ROOT / "tests", 1.00),
]
FRONTEND = ROOT / "src" / "satisfactory_mcp" / "interfaces" / "web" / "frontend"
TS_BUDGET = 1.30
#: Written by openapi-typescript from the server's schema, not by hand.
TS_GENERATED = {"schema.d.ts"}
MIN_CODE_LINES = 40  # tiny files are all header; the budget is about essays, not stubs


def prose_and_code(path: Path) -> tuple[int, int] | None:
    """Python: ``#`` comment lines and bare string statements (docstrings), against the rest.

    A string counts as prose only when it is a statement of its own, so ``__all__`` entries
    and the lines of a multi-line message stay code.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        tree = ast.parse(source)
    except (tokenize.TokenError, SyntaxError):
        return None
    comment_lines: set[int] = set()
    for token in tokens:
        if token.type == tokenize.COMMENT:
            comment_lines.update(range(token.start[0], token.end[0] + 1))
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            doc_lines.update(range(node.lineno, node.end_lineno + 1))
    prose = code = 0
    for line_number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if (line_number in comment_lines and stripped.startswith("#")) or line_number in doc_lines:
            prose += 1
        else:
            code += 1
    return prose, code


def ts_prose_and_code(path: Path) -> tuple[int, int]:
    """TypeScript: lines starting ``//`` and every line of a ``/* */`` or ``/** */`` block."""
    prose = code = 0
    in_block = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if in_block:
            prose += 1
            in_block = "*/" not in stripped
        elif stripped.startswith("//"):
            prose += 1
        elif stripped.startswith("/*"):
            prose += 1
            in_block = "*/" not in stripped[2:]
        else:
            code += 1
    return prose, code


def _typescript_sources() -> list[Path]:
    sources = [
        *(FRONTEND / "src").rglob("*.ts"),
        *FRONTEND.glob("*.d.ts"),
        FRONTEND / "vite.config.ts",
    ]
    return [path for path in sources if path.name not in TS_GENERATED and path.is_file()]


def _over_budget(
    counted: Iterable[tuple[Path, tuple[int, int] | None]], budget: float
) -> Iterator[str]:
    """``ratio>cap path`` for every counted file of at least MIN_CODE_LINES over ``budget``."""
    for path, counts in counted:
        if counts is None:
            continue
        prose, code = counts
        if code >= MIN_CODE_LINES and prose / code > budget:
            yield f"{prose / code:.2f}>{budget} {path.relative_to(ROOT)}"


def test_the_prose_stays_inside_its_budget() -> None:
    over = [
        entry
        for root, budget in BUDGETS
        for entry in _over_budget(((p, prose_and_code(p)) for p in root.rglob("*.py")), budget)
    ]
    over += _over_budget(((p, ts_prose_and_code(p)) for p in _typescript_sources()), TS_BUDGET)
    over.sort(reverse=True)
    assert not over, (
        f"comment budget: {len(over)} file(s) over (docs/comments.md). "
        "The remedy is to split the file so each explanation sits beside the code it guards, "
        "or to move a fact to its one home and delete the copy (rule 1) -- not to delete the "
        "explanation. The caps are per directory, so raising one file's cap is not available; "
        "raising a whole layer's cap needs its own argument. Over: "
        + "; ".join(over[:10])
        + ("" if len(over) <= 10 else f"; +{len(over) - 10} more")
    )

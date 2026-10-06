"""The comment budget of docs/comments.md, measured per file: prose lines over code lines.

A RATCHET, not an aspiration. The caps below are the sweep's measured result plus a small
working margin, so the suite fails the moment a file grows a new essay -- which is the
property that matters. They are not the numbers docs/comments.md argues for; the sweep
converged on the density of the reviewed example (routers/crates.py, 0.92) rather than on
0.25, and lowering a cap is a deliberate second pass over the files it would fail, never a
constant edited on its own.
"""

from __future__ import annotations

import io
import tokenize
import warnings
from pathlib import Path

ENFORCE = True

ROOT = Path(__file__).resolve().parent.parent
BUDGETS = [
    (ROOT / "src" / "satisfactory_mcp" / "interfaces", 1.45),
    (ROOT / "src" / "satisfactory_mcp" / "presenters", 0.60),
    (ROOT / "tools", 1.00),
    (ROOT / "src" / "satisfactory_mcp" / "domain", 1.35),
    (ROOT / "src" / "satisfactory_mcp" / "core", 1.00),
    (ROOT / "src" / "pioneersav", 1.40),
]
MIN_CODE_LINES = 40  # tiny files are all header; the budget is about essays, not stubs


def prose_and_code(path: Path) -> tuple[int, int] | None:
    src = path.read_text(encoding="utf-8")
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    comment_lines: set[int] = set()
    doc_lines: set[int] = set()
    prev = None
    for t in toks:
        if t.type == tokenize.COMMENT:
            comment_lines.update(range(t.start[0], t.end[0] + 1))
        elif t.type == tokenize.STRING and prev in (
            None,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            doc_lines.update(range(t.start[0], t.end[0] + 1))
        if t.type not in (tokenize.COMMENT,):
            prev = t.type
    prose = code = 0
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if (i in comment_lines and s.startswith("#")) or i in doc_lines:
            prose += 1
        else:
            code += 1
    return prose, code


def test_the_prose_stays_inside_its_budget() -> None:
    over: list[str] = []
    for root, budget in BUDGETS:
        for path in root.rglob("*.py"):
            counted = prose_and_code(path)
            if counted is None:
                continue
            prose, code = counted
            if code < MIN_CODE_LINES:
                continue
            ratio = prose / code
            if ratio > budget:
                over.append(f"{ratio:.2f}>{budget} {path.relative_to(ROOT)}")
    if not over:
        return
    over.sort(reverse=True)
    message = (
        f"comment budget: {len(over)} file(s) over (docs/comments.md). "
        "The remedy is to split the file so each explanation sits beside the code it guards, "
        "or to move a fact to its one home and delete the copy (rule 1) -- not to delete the "
        "explanation. The caps are per directory, so raising one file's cap is not available; "
        "raising a whole layer's cap needs its own argument. Over: "
        + "; ".join(over[:10])
        + ("" if len(over) <= 10 else f"; +{len(over) - 10} more")
    )
    if ENFORCE:
        raise AssertionError(message)
    warnings.warn(message, stacklevel=1)

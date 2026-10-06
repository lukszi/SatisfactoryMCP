"""The formatting PRIMITIVES: tables, envelopes, footers, numbers and rates.

This file owns HOW a table looks and never WHICH table a tool returns. Context budget is the
binding constraint: every response is read by a model, and all 291 automatable recipes in the
tightest possible TSV is already ~25k characters. The layout rules live here and are not
restated per formatter:

  * compact TSV, not JSON -- the win is dropping repeated keys and braces
  * names in rows, IDs once in a footer
  * scoped aggregates BEFORE rows (an unscoped total is often actively misleading)
  * truncation envelope counts DATA ROWS ONLY (miscounting misleads the model)
  * coordinates in metres, not raw centimetres
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from ...core.text import num, plural

__all__ = [
    "NARROW_HINT",
    "Page",
    "bullets",
    "capped",
    "clamp",
    "cut",
    "envelope",
    "flows",
    "ids_footer",
    "kv",
    "num",
    "page",
    "paged_table",
    "plural",
    "rate",
    "table",
    "where_bands",
]

MAX_ROWS = 25


def rate(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    return f"{num(value)}{unit}"


#: What a truncated table says when it cannot page. Deliberately not a parameter name: the
#: envelope naming one the tool does not accept is the defect this replaces.
NARROW_HINT = "narrow the query, or raise limit"


def table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    total: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
    hint: str = "",
) -> str:
    """Tab-separated table with a truncation envelope. ``total`` is the number of MATCHES,
    not the number of lines emitted; the header and any footer are never counted.

    Passing ``offset`` is the DECLARATION THAT THE CALLER PAGES, and the only thing that
    lets the envelope say "call again with offset=N". A tool without an ``offset``
    parameter must not pass one: it would be telling the reader to send an argument the
    schema rejects. Such a table gets ``hint`` instead, or ``NARROW_HINT``.
    """
    body = [list(r) for r in rows]
    if limit is not None:
        body = body[: max(0, limit)]
    shown = len(body)
    lines = ["\t".join(headers)]
    lines += ["\t".join("" if c is None else str(c) for c in r) for r in body]
    out = "\n".join(lines)

    if total is None or total <= shown:
        return out

    start = offset or 0
    remaining = total - start - shown
    parts = [f"# {total} match(es), showing {shown} from offset {start}."]
    if remaining > 0:
        if offset is None:
            parts.append(f"{remaining} more: {hint or NARROW_HINT}")
        else:
            parts.append(f"{remaining} more: call again with offset={start + shown}")
            if hint:
                parts.append(hint)
    return out + "\n" + " ".join(parts)


def envelope(summary: str, body: str = "", notes: Iterable[str] = ()) -> str:
    """Summary first, then notes, then rows: warnings lead because that is where the
    actionable insight is, not in the twentieth data row."""
    chunks = [summary.rstrip()]
    note_list = [n for n in notes if n]
    if note_list:
        chunks.append("\n".join(f"! {n}" for n in note_list))
    if body.strip():
        chunks.append(body.rstrip())
    return "\n".join(chunks)


def ids_footer(pairs: Iterable[tuple[str, str]], label: str = "ids") -> str:
    """One line mapping display names to class IDs, kept out of the rows."""
    items = [f"{name}={cid}" for name, cid in pairs]
    if not items:
        return ""
    return f"# {label}: " + " ".join(items)


def cut(text: str, width: int) -> str:
    """``text`` cut to ``width`` with a visible ``~``: a silently clipped list reads as whole."""
    return text if len(text) <= width else text[: width - 1] + "~"


def kv(pairs: Iterable[tuple[str, object]], sep: str = "  ") -> str:
    return sep.join(f"{k}={v}" for k, v in pairs if v not in (None, "", []))


def bullets(lines: Iterable[str], marker: str = "-") -> str:
    return "\n".join(f"{marker} {line}" for line in lines if line)


def flows(items: Iterable[tuple[str, float]]) -> str:
    """Render ``(name, amount)`` pairs as ``30 Crude Oil + 20 Water``. Fluids arrive
    pre-divided by 1000, so this never shows raw litres."""
    return " + ".join(f"{num(amount)} {name}" for name, amount in items) or "-"


def where_bands(distances_m: Iterable[float], gap_m: float = 200.0, max_bands: int = 3) -> str:
    """Where a set of machines actually is, as ``4@0 13@1 6@2.5`` kilometres.

    A count hides what matters and a mean hides it just as well: 23 Water Extractors reads as
    one fleet until you see 4 at the plant, 13 at the main base and 6 two and a half
    kilometres away. Clustering is single-linkage at the same 200 m the node and site
    clustering uses.
    """
    ordered = sorted(distances_m)
    if not ordered:
        return ""
    groups: list[list[float]] = [[ordered[0]]]
    for d in ordered[1:]:
        if d - groups[-1][-1] > gap_m:
            groups.append([])
        groups[-1].append(d)
    if len(groups) > max_bands:
        # Near groups stay separate, since that is where the reusable machines are; the tail
        # collapses, since it only ever means "and some far away".
        head, tail = groups[: max_bands - 1], groups[max_bands - 1 :]
        groups = [*head, [d for g in tail for d in g]]
    parts = [f"{len(g)}@{sum(g) / len(g) / 1000:.1f}".rstrip("0").rstrip(".") for g in groups]
    return " ".join(f"{p}0" if p.endswith("@") else p for p in parts)


def clamp(value: int | None, default: int = 10, lo: int = 1, hi: int = MAX_ROWS) -> int:
    """Clamp a caller-supplied limit. Tool schemas also bound this, belt and braces."""
    if value is None:
        return default
    return max(lo, min(hi, int(value)))


class Page(NamedTuple):
    """The rows a ``limit``/``offset`` pair asks for: ``rows[start:end]``, ``size`` at most."""

    start: int
    end: int
    size: int

    def of(self, rows: Sequence) -> Sequence:
        return rows[self.start : self.end]


def page(limit: int | None, offset: int, default: int = 10) -> Page:
    """The page ``limit`` rows from ``offset`` names, the limit clamped like every table's."""
    size = clamp(limit, default=default)
    start = max(0, offset)
    return Page(start, start + size, size)


def paged_table(
    headers: Sequence[str], rows: Sequence, window: Page, total: int | None = None
) -> str:
    """``table`` over the rows ``window`` selects, counting ``total`` (default: all rows)."""
    return table(
        headers,
        window.of(rows),
        total=len(rows) if total is None else total,
        offset=window.start,
        limit=window.size,
    )


def capped(items: Sequence[str], shown: int, *, sep: str = ", ", more: str = " (+{n} more)") -> str:
    """The first ``shown`` items joined by ``sep``, and the rest counted in ``more``'s words."""
    rest = len(items) - shown
    return sep.join(items[:shown]) + (more.format(n=rest) if rest > 0 else "")

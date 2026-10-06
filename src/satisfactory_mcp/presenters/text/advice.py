"""Advisories as chat reads them: the ``ui_context`` line and the ``world_summary`` block.

docs/advisors_contract.md §6 has the shapes and the budgets.
"""

from __future__ import annotations

import re

from ...domain.advice import WORDS, Advisory, Current
from ...domain.advice.advisory import capped

__all__ = ["CONTEXT_ROWS", "SUMMARY_ROWS", "context_lines", "parse_hide", "summary_block"]

CONTEXT_ROWS = 3
SUMMARY_ROWS = 12
ROW_WIDTH = 160
SNOOZE_HOURS = 1.0
_HIDE = re.compile(
    r"\s*(adv:[0-9a-f]{4,6})(?:\s+(snooze)(?:\s+(\d+(?:\.\d+)?)\s*(h|m|min)?)?)?\s*",
    re.IGNORECASE,
)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_hide(raw) -> tuple[str, str, float | None] | None:
    """``"adv:3f9a"`` -> dismiss; ``"adv:3f9a snooze"`` -> 1 h of play; ``"… snooze 4h"``
    or ``"… snooze 30m"`` -> that long. None when it is not one."""
    if not isinstance(raw, str):
        return None
    hit = _HIDE.fullmatch(raw)
    if hit is None:
        return None
    adv_id = hit.group(1).lower()
    if not hit.group(2):
        return adv_id, "dismiss", None
    hours = float(hit.group(3)) if hit.group(3) else SNOOZE_HOURS
    if (hit.group(4) or "h").lower() in ("m", "min"):
        hours /= 60.0
    return adv_id, "snooze", hours


def _short(adv: Advisory) -> str:
    return _cut(f"{adv.id} {WORDS[adv.kind]}: {adv.tool_text}", ROW_WIDTH)


def context_lines(cur: Current) -> list[str]:
    """The ``advice`` line: the first three rows the card shows, and the hint to hide one."""
    total, hidden = len(cur.active), len(cur.hidden)
    head = f"advice ({total}, {hidden} hidden)"
    if not total:
        return [f"{head}: nothing worth a look"]
    first, _rest = capped([a for a, _back, _rev in cur.active])
    shown = [_short(a) for a in first[:CONTEXT_ROWS]]
    line = f"{head}: " + " · ".join(shown)
    if total > CONTEXT_ROWS:
        line += f" (+{total - CONTEXT_ROWS} more: world_summary)"
    lead = first[0].id
    hint = (
        f'to hide one on the page when the user asks: ui_context(dismissed=["{lead}"]), '
        f'or "{lead} snooze" for 1 h of play'
    )
    return [line, hint]


def summary_block(cur: Current) -> str:
    """``world_summary``'s ``worth a look`` block: every active row, at most twelve."""
    if not cur.active:
        return "## worth a look\nnothing" + (f" ({len(cur.hidden)} hidden)" if cur.hidden else "")
    rows = []
    for adv, back, _rev in cur.active[:SUMMARY_ROWS]:
        subject = f"“{adv.subject}”" if adv.subject_kind in ("factory", "plan") else adv.subject
        again = " (back: worse than when hidden)" if back else ""
        rows.append(
            f"{adv.id} · {WORDS[adv.kind]} · {subject} · {adv.tool_text}{again} · "
            f"next: {adv.next_call}"
        )
    if len(cur.active) > SUMMARY_ROWS:
        rows.append(f"(+{len(cur.active) - SUMMARY_ROWS} more)")
    head = "## worth a look" + (f" ({len(cur.hidden)} hidden)" if cur.hidden else "")
    return head + "\n" + "\n".join(rows)

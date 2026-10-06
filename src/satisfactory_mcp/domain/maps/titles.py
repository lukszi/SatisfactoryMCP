"""The one name a map type is shown by, on the page and in chat: "Painted · 6 Oct ★".

Composed here and nowhere else; docs/maps_contract.md §3.5 is the specification.
"""

from __future__ import annotations

import time

from ...core.gameassets.versions import STYLES
from . import axes as ax

__all__ = ["DEFAULT_MARK", "date_word", "style_name", "titles"]

DEFAULT_MARK = "★"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SHOWN = frozenset({"ready", "missing"})


def style_name(axes: dict) -> str:
    """The style table's ``name``, the artwork's for any artwork, else the label capitalised."""
    style = axes.get("style") if isinstance(axes.get("style"), dict) else {}
    renderer = axes.get("renderer") if isinstance(axes.get("renderer"), dict) else {}
    known = STYLES.get(str(style.get("id")))
    if known is None and renderer.get("family") == "artwork":
        known = STYLES["artwork"]
    if known is not None:
        return str(known["name"])
    label = ax.style_label(axes)
    return label[:1].upper() + label[1:]


def date_word(created: float | None, with_time: bool = False) -> str:
    """``6 Oct`` or ``6 Oct 14:05`` in local time; "" when the time is unknown."""
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        return ""
    t = time.localtime(created)
    word = f"{t.tm_mday} {MONTHS[t.tm_mon - 1]}"
    return word + (f" {t.tm_hour:02d}:{t.tm_min:02d}" if with_time else "")


def _in_switcher(row: dict, default: str | None) -> bool:
    entry = row["entry"]
    return row["status"] in SHOWN and (bool(entry.get("in_switcher", True)) or row["id"] == default)


def titles(rows: list[dict], default: str | None) -> dict[str, str]:
    """Each view row's title by id: its label, else its style's name, dated beside a twin.

    A row is dated when the switcher shows another unlabelled row of the same name; two of
    them built on one day add the time. The default gets ``★``.
    """
    names = {row["id"]: style_name(row["axes"]) for row in rows}
    unlabelled = [row for row in rows if not row["entry"].get("label")]
    out: dict[str, str] = {}
    for row in rows:
        title = row["entry"].get("label") or names[row["id"]]
        if not row["entry"].get("label"):
            twins = [
                other
                for other in unlabelled
                if other is not row
                and names[other["id"]] == names[row["id"]]
                and _in_switcher(other, default)
            ]
            day = date_word(row["entry"].get("created"))
            if twins and day:
                same_day = any(date_word(t["entry"].get("created")) == day for t in twins)
                title += " · " + date_word(row["entry"].get("created"), with_time=same_day)
        if row["id"] == default:
            title += " " + DEFAULT_MARK
        out[row["id"]] = title
    return out

"""The advisory row and its contract: kinds, severities, caps, keys and ids.

docs/advisors_contract.md is the specification; ``rules`` fills these rows, ``store`` hides
them, and the page and chat read them through ``capped``.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

__all__ = [
    "KINDS",
    "PER_KIND",
    "SEVERITIES",
    "SEVERITY",
    "SPOTS_SENT",
    "TEXT_MAX",
    "TONE",
    "TOOL_MAX",
    "VISIBLE",
    "WORDS",
    "Advisory",
    "Spot",
    "capped",
    "ids_for",
    "key_for",
]

SEVERITIES = ("act", "consider", "note")
TONE = {"act": "blocked", "consider": "mid", "note": "muted"}
#: Rank order, which is also the order of the kinds in docs/advisors_contract.md §2.
KINDS = (
    "unconnected",
    "dead_node",
    "starved",
    "power",
    "underclock",
    "headroom",
    "no_recipe",
    "plan",
    "pickups",
    "box_empty",
)
SEVERITY = {
    "unconnected": "act",
    "dead_node": "act",
    "starved": "act",
    "power": "act",
    "underclock": "consider",
    "headroom": "consider",
    "no_recipe": "consider",
    "plan": "consider",
    "pickups": "note",
    "box_empty": "note",
}
#: The chip word of each kind, on the page and in chat.
WORDS = {
    "unconnected": "unconnected",
    "dead_node": "no node",
    "starved": "starved",
    "power": "power",
    "underclock": "underclock",
    "headroom": "headroom",
    "no_recipe": "no recipe",
    "plan": "plan",
    "pickups": "pickups",
    "box_empty": "box empty",
}
VISIBLE = 5
PER_KIND = 3
SPOTS_SENT = 50
TEXT_MAX = 90
TOOL_MAX = 160


@dataclass(frozen=True)
class Spot:
    """One place a row names: a machine (``instance`` its leaf) or a pickup (``instance`` "")."""

    instance: str
    name: str
    x_m: float | None
    y_m: float | None


@dataclass(frozen=True)
class Advisory:
    key: str
    id: str
    kind: str
    severity: str
    subject_kind: str
    subject: str
    text: str
    tool_text: str
    weight: float
    members: tuple[str, ...]
    spots: tuple[Spot, ...]
    bbox_m: tuple[float, float, float, float] | None
    lines: tuple[str, ...]
    next_call: str
    seed: str | None
    reveal: tuple[str, ...]
    plan: str | None
    source: str

    @property
    def tone(self) -> str:
        return TONE[self.severity]

    def rank(self) -> tuple:
        return (
            SEVERITIES.index(self.severity),
            KINDS.index(self.kind),
            -self.weight,
            self.subject,
        )


def key_for(kind: str, subject_kind: str, subject: str) -> str:
    """``<kind>|<subject kind>:<subject>``; a ``|`` inside the subject is written ``%7C``."""
    if subject_kind == "world":
        return f"{kind}|world"
    return f"{kind}|{subject_kind}:{subject.replace('|', '%7C')}"


def ids_for(keys) -> dict[str, str]:
    """``adv:`` plus four hex of sha1(key); keys sharing a prefix get six hex each."""
    keys = sorted(set(keys))
    digests = {key: hashlib.sha1(key.encode("utf-8")).hexdigest() for key in keys}
    prefixes = Counter(digest[:4] for digest in digests.values())
    return {
        key: "adv:" + (digest[:4] if prefixes[digest[:4]] == 1 else digest[:6])
        for key, digest in digests.items()
    }


def capped(rows: list, visible: int = VISIBLE, per_kind: int = PER_KIND) -> tuple[list, list]:
    """``(shown, rest)``: the first rows a card shows, at most ``per_kind`` of one kind and
    ``visible`` in all, keeping rank order. advice.ts applies the same rule."""
    seen_of_kind: Counter = Counter()
    shown, rest = [], []
    for row in rows:
        seen_of_kind[row.kind] += 1
        fits = seen_of_kind[row.kind] <= per_kind and len(shown) < visible
        (shown if fits else rest).append(row)
    return shown, rest

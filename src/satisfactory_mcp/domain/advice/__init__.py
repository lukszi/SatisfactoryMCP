"""Advisors: the cheap list of things worth a look, and what the player hid of it.

``rules`` computes the rows, ``store`` keeps dismiss and snooze, and ``current`` joins the
two the way the page and chat both read them. docs/advisors_contract.md is the specification.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.schema import NewerSchema
from ...core.singleflight import Singleflight
from .. import settings
from ..planning.planlog import PlanLog, PlanLogError
from . import rules, store
from .rules import KINDS, PER_KIND, SEVERITIES, TONE, VISIBLE, WORDS, Advisory, Spot

__all__ = [
    "KINDS",
    "PER_KIND",
    "SEVERITIES",
    "TONE",
    "VISIBLE",
    "WORDS",
    "Advisory",
    "Current",
    "Spot",
    "current",
    "options",
    "rules",
    "store",
]

#: (projection, game, labels version, plan heads, options) -> ranked rows. The projection and
#: game are held by the entry: the key is their ``id()``.
_ROWS = Singleflight(maxsize=8)


@dataclass
class Current:
    """One read: every firing row, which are active, which are hidden, and the play clock."""

    items: list[Advisory]
    active: list[tuple[Advisory, bool, int]]
    hidden: list[tuple[Advisory, dict]]
    version: int
    play_s: float
    notes: list[str]

    def find(self, adv_id: str) -> Advisory | None:
        return next((a for a in self.items if a.id == adv_id), None)


def options(biomass: bool | None = None, headroom: str | None = None) -> tuple[dict, list[str]]:
    """The shared settings the pass reads, and a note per one that could not be read."""
    out, notes = {}, []
    try:
        values = settings.read()["values"]
    except (NewerSchema, OSError) as exc:
        values = {k: s.default for k, s in settings.SPECS.items()}
        notes.append(f"shared settings unreadable, defaults used: {exc}")
    out["biomass"] = bool(values["biomass"]) if biomass is None else biomass
    out["headroom"] = str(values["stage_headroom"]) if headroom is None else headroom
    out["box_fed"] = bool(values["advice_box_fed"])
    return out, notes


def _heads(st) -> tuple:
    try:
        heads = PlanLog(st.world_id, st.header.get("session_name") or "").heads()
    except (PlanLogError, OSError):
        return ()
    return tuple(sorted((s.key, s.rev) for s in heads))


def _rows(st, opts: dict, spoilers: bool) -> list[Advisory]:
    key = (
        id(st.projection),
        id(st.game),
        st.labels.version,
        _heads(st),
        tuple(sorted(opts.items())),
        spoilers,
    )
    held = _ROWS.get(
        key, lambda: (st.projection, st.game, rules.compute(st, spoilers=spoilers, **opts))
    )
    return held[2]


def current(
    st, *, biomass: bool | None = None, headroom: str | None = None, spoilers: bool = False
) -> Current:
    """What fires on ``st`` with this world's hidden entries applied. ``NewerSchema`` when the
    store is from a newer version."""
    opts, notes = options(biomass, headroom)
    data = store.read(st.world_id)
    rows = _rows(st, opts, spoilers)
    items = rules.with_ids(rows, data["hidden"].keys())
    play_s = float(st.header.get("play_duration_s") or 0.0)
    active, hidden = store.split(items, data, play_s)
    return Current(items, active, hidden, data["version"], play_s, notes)

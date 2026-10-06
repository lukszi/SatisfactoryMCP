"""Dismissed and snoozed advisories: one file per world, written by the page and by chat.

Every write holds the file lock and bumps ``version``; one entry's ``rev`` counts writes to
it, and a write naming another ``rev`` is refused. docs/advisors_contract.md §4 is the
specification; the file is kept like the asks and pins files (``session.versioned``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar, cast

from ... import config
from ...core import schema
from ...core.jsontypes import JsonValue
from ..session import versioned
from .advisory import SEVERITIES, Advisory
from .views import AdviceDoc, HiddenEntry

__all__ = [
    "HOURS",
    "IDS_CAP",
    "MODES",
    "SCHEMA",
    "AdviceError",
    "AdviceMissing",
    "AdviceStale",
    "got_worse",
    "hide",
    "path_for",
    "read",
    "restore",
    "split",
]

SCHEMA = 1
IDS_CAP = 200
MODES = ("dismiss", "snooze")
HOURS = (0.5, 24.0)
GROWTH = 1.5
PRUNE_S = 30 * 86400.0

T = TypeVar("T")

#: An advisory's place in the hidden list: ``(adv, back, rev)`` active, ``(adv, entry)`` hidden.
Active = tuple[Advisory, bool, int]
Hidden = tuple[Advisory, HiddenEntry]


class AdviceError(ValueError):
    """A hide or restore that cannot be honoured, worded for the player."""


class AdviceMissing(AdviceError, KeyError):
    def __str__(self) -> str:
        return self.args[0]


class AdviceStale(AdviceError):
    def __init__(self, key: str, entry: HiddenEntry | None) -> None:
        super().__init__("that advisory changed since you read it")
        self.key = key
        self.entry = entry


def path_for(world_id: str) -> Path:
    return config.advice_dir() / f"{config.world_file_stem(world_id)}.json"


def _empty() -> AdviceDoc:
    return {"schema": SCHEMA, "version": 0, "hidden": {}}


def read(world_id: str) -> AdviceDoc:
    path = path_for(world_id)
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    schema.check(raw, SCHEMA, path)
    hidden = raw.get("hidden") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(hidden, dict):
        return _empty()
    out = _empty()
    out["version"] = versioned.count(raw.get("version"), 0)
    out["hidden"] = {k: cast(HiddenEntry, v) for k, v in hidden.items() if isinstance(v, dict)}
    return out


def _locked_update(world_id: str, change: Callable[[AdviceDoc], tuple[T, bool]]) -> T:
    """Run ``change(data) -> (result, dirty)`` under the file lock; write when dirty."""
    return versioned.locked_update(path_for(world_id), lambda: read(world_id), change)


def _check_rev(key: str, entry: HiddenEntry | None, rev: int | None) -> None:
    if rev is None:
        return
    have = int(entry.get("rev") or 0) if entry else 0
    if isinstance(rev, bool) or rev != have:
        raise AdviceStale(key, entry.copy() if entry else None)


def _prune(data: AdviceDoc, firing: set[str], now: float) -> None:
    for key in [k for k in data["hidden"] if k not in firing]:
        if now - float(data["hidden"][key].get("at") or 0.0) > PRUNE_S:
            del data["hidden"][key]


def hide(
    world_id: str,
    adv: Advisory,
    mode: str,
    *,
    play_s: float,
    by: dict[str, object],
    hours: float | None = None,
    rev: int | None = None,
    firing: Iterable[str] = (),
) -> HiddenEntry:
    """Dismiss or snooze ``adv``; returns the entry written. ``rev`` None is last writer wins."""
    if mode not in MODES:
        raise AdviceError(f"mode is one of {', '.join(MODES)}, not {mode!r}")
    until = snoozed_hours = None
    if mode == "snooze":
        if isinstance(hours, bool) or not isinstance(hours, int | float):
            raise AdviceError("a snooze needs hours of play")
        if not HOURS[0] <= hours <= HOURS[1]:
            raise AdviceError(f"a snooze is {HOURS[0]:g} to {HOURS[1]:g} hours of play")
        snoozed_hours = float(hours)
        until = float(play_s) + snoozed_hours * 3600.0
    members = list(adv.members)
    capped = len(members) > IDS_CAP

    def change(data: AdviceDoc) -> tuple[HiddenEntry, bool]:
        entry = data["hidden"].get(adv.key)
        _check_rev(adv.key, entry, rev)
        now = time.time()
        fresh: HiddenEntry = {
            "state": "snoozed" if mode == "snooze" else "dismissed",
            "ids": None if capped else members,
            "weight": adv.weight,
            "severity": adv.severity,
            "until_play_s": until,
            "hours": snoozed_hours,
            "by": by,
            "at": now,
            "rev": (int(entry.get("rev") or 0) if entry else 0) + 1,
        }
        data["hidden"][adv.key] = fresh
        _prune(data, set(firing) | {adv.key}, now)
        return fresh.copy(), True

    return _locked_update(world_id, change)


def restore(world_id: str, key: str, rev: int | None = None) -> HiddenEntry:
    """Show ``key`` again; returns the entry removed. ``AdviceMissing`` when it is not hidden."""

    def change(data: AdviceDoc) -> tuple[HiddenEntry, bool]:
        entry = data["hidden"].get(key)
        if entry is None:
            if rev:
                raise AdviceStale(key, None)
            raise AdviceMissing("that advisory is not hidden")
        _check_rev(key, entry, rev)
        del data["hidden"][key]
        return entry.copy(), True

    return _locked_update(world_id, change)


def got_worse(adv: Advisory, entry: HiddenEntry) -> bool:
    """A machine not in the hidden set joined, the severity rose, or (past the id cap, or
    with no members at all) the weight grew by half."""
    stored = entry.get("severity")
    if stored in SEVERITIES and SEVERITIES.index(adv.severity) < SEVERITIES.index(stored):
        return True
    ids = entry.get("ids")
    if isinstance(ids, list) and (ids or adv.members):
        return bool(set(adv.members) - set(ids))
    return adv.weight > 0 and adv.weight >= GROWTH * float(entry.get("weight") or 0.0)


def split(
    items: list[Advisory], data: AdviceDoc, play_s: float
) -> tuple[list[Active], list[Hidden]]:
    """``(active, hidden)``: active is ``[(adv, back, rev)]``, hidden ``[(adv, entry)]``,
    both in rank order. A snooze past its play-time mark, or anything that got worse,
    is active again."""
    active: list[Active] = []
    hidden: list[Hidden] = []
    for adv in items:
        entry = data["hidden"].get(adv.key)
        if entry is None:
            active.append((adv, False, 0))
            continue
        rev = int(entry.get("rev") or 0)
        if got_worse(adv, entry):
            active.append((adv, True, rev))
            continue
        until = entry.get("until_play_s")
        if entry.get("state") == "snoozed" and until is not None and play_s >= float(until):
            active.append((adv, False, rev))
            continue
        hidden.append((adv, entry))
    return active, hidden

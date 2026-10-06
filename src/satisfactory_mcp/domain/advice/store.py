"""Dismissed and snoozed advisories: one file per world, written by the page and by chat.

Every write holds the file lock and bumps ``version``; one entry's ``rev`` counts writes to
it, and a write naming another ``rev`` is refused. docs/advisors_contract.md §4 is the
specification.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ... import config
from ...core import atomic, filelock, schema
from .advisory import SEVERITIES, Advisory

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


class AdviceError(ValueError):
    """A hide or restore that cannot be honoured, worded for the player."""


class AdviceMissing(AdviceError, KeyError):
    def __str__(self) -> str:
        return self.args[0]


class AdviceStale(AdviceError):
    def __init__(self, key: str, entry: dict | None) -> None:
        super().__init__("that advisory changed since you read it")
        self.key = key
        self.entry = entry


def path_for(world_id: str) -> Path:
    return config.advice_dir() / f"{config.world_file_stem(world_id)}.json"


def _empty() -> dict:
    return {"schema": SCHEMA, "version": 0, "hidden": {}}


def read(world_id: str) -> dict:
    path = path_for(world_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    schema.check(raw, SCHEMA, path)
    if not isinstance(raw, dict) or not isinstance(raw.get("hidden"), dict):
        return _empty()
    out = _empty()
    out["version"] = int(raw.get("version") or 0)
    out["hidden"] = {
        k: v for k, v in raw["hidden"].items() if isinstance(k, str) and isinstance(v, dict)
    }
    return out


def _write(world_id: str, change):
    path = path_for(world_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.held(path):
        data = read(world_id)
        result, dirty = change(data)
        if dirty:
            data["version"] += 1
            atomic.write_text(path, json.dumps(data, ensure_ascii=False))
    return result


def _check_rev(key: str, entry: dict | None, rev: int | None) -> None:
    if rev is None:
        return
    have = int(entry.get("rev") or 0) if entry else 0
    if isinstance(rev, bool) or rev != have:
        raise AdviceStale(key, dict(entry) if entry else None)


def _prune(data: dict, firing: set[str], now: float) -> None:
    for key in [k for k, e in data["hidden"].items() if k not in firing]:
        if now - float(data["hidden"][key].get("at") or 0.0) > PRUNE_S:
            del data["hidden"][key]


def hide(
    world_id: str,
    adv: Advisory,
    mode: str,
    *,
    play_s: float,
    by: dict,
    hours: float | None = None,
    rev: int | None = None,
    firing=(),
) -> dict:
    """Dismiss or snooze ``adv``; returns the entry written. ``rev`` None is last writer wins."""
    if mode not in MODES:
        raise AdviceError(f"mode is one of {', '.join(MODES)}, not {mode!r}")
    until = None
    if mode == "snooze":
        if isinstance(hours, bool) or not isinstance(hours, int | float):
            raise AdviceError("a snooze needs hours of play")
        if not HOURS[0] <= hours <= HOURS[1]:
            raise AdviceError(f"a snooze is {HOURS[0]:g} to {HOURS[1]:g} hours of play")
        until = float(play_s) + float(hours) * 3600.0
    members = list(adv.members)
    capped = len(members) > IDS_CAP

    def change(data: dict):
        entry = data["hidden"].get(adv.key)
        _check_rev(adv.key, entry, rev)
        now = time.time()
        fresh = {
            "state": "snoozed" if mode == "snooze" else "dismissed",
            "ids": None if capped else members,
            "weight": adv.weight,
            "severity": adv.severity,
            "until_play_s": until,
            "hours": float(hours) if mode == "snooze" else None,
            "by": by,
            "at": now,
            "rev": (int(entry.get("rev") or 0) if entry else 0) + 1,
        }
        data["hidden"][adv.key] = fresh
        _prune(data, set(firing) | {adv.key}, now)
        return dict(fresh), True

    return _write(world_id, change)


def restore(world_id: str, key: str, rev: int | None = None) -> dict:
    """Show ``key`` again; returns the entry removed. ``AdviceMissing`` when it is not hidden."""

    def change(data: dict):
        entry = data["hidden"].get(key)
        if entry is None:
            if rev:
                raise AdviceStale(key, None)
            raise AdviceMissing("that advisory is not hidden")
        _check_rev(key, entry, rev)
        del data["hidden"][key]
        return dict(entry), True

    return _write(world_id, change)


def got_worse(adv: Advisory, entry: dict) -> bool:
    """A machine not in the hidden set joined, the severity rose, or (past the id cap, or
    with no members at all) the weight grew by half."""
    stored = entry.get("severity")
    if stored in SEVERITIES and SEVERITIES.index(adv.severity) < SEVERITIES.index(stored):
        return True
    ids = entry.get("ids")
    if isinstance(ids, list) and (ids or adv.members):
        return bool(set(adv.members) - set(ids))
    return adv.weight > 0 and adv.weight >= GROWTH * float(entry.get("weight") or 0.0)


def split(items: list[Advisory], data: dict, play_s: float):
    """``(active, hidden)``: active is ``[(adv, back, rev)]``, hidden ``[(adv, entry)]``,
    both in rank order. A snooze past its play-time mark, or anything that got worse,
    is active again."""
    active, hidden = [], []
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

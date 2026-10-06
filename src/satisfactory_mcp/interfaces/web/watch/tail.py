"""Tail the plan logs, the activity journal and the settings file into events with data.

Tailed rather than stat-compared: every new commit or entry becomes an event carrying it
(docs/planner_slice_contract.md §11.3). The first scan only records where each file ends, so
a server start announces nothing old; docs/web-wire.md has the rules.
"""

from __future__ import annotations

from pathlib import Path

from .... import config
from ....domain import settings
from ....domain.planning.stored.plan_args import PlanLogError
from ....domain.planning.stored.planlog import Commit, PlanLog
from ....domain.session import journal
from ..serial import actor_json, settings_json
from .events import KIND_ACTIVITY, KIND_PLANS, KIND_SETTINGS, WatchEvent

__all__ = ["LogTail"]


def _end_of_last_line(path: Path) -> int:
    """The offset just past the file's last newline: a torn last line is read next time."""
    try:
        return path.read_bytes().rfind(b"\n") + 1
    except OSError:
        return 0


def _plan_event(world: str, key: str, rows: list[dict]) -> WatchEvent | None:
    """One plan's new commits as a single event, or ``None`` when none would parse."""
    try:
        commits = [Commit.from_dict(r) for r in rows]
    except (ValueError, KeyError, TypeError):
        return None
    if not commits:
        return None
    newest = commits[-1]
    try:
        state = PlanLog(world).state(key, newest.rev)
        name, forgotten = state.name, state.forgotten
    except (PlanLogError, OSError, ValueError):
        name, forgotten = "", False
    actors: list[dict] = []
    for commit in commits:
        body = actor_json(commit.actor)
        if body not in actors:
            actors.append(body)
    data = {
        "world": world,
        "key": key,
        "name": name,
        "rev": newest.rev,
        "from_rev": commits[0].rev - 1,
        "actors": actors,
        "text": newest.text(),
        "ts": newest.ts,
        "forgotten": forgotten,
    }
    return WatchEvent(KIND_PLANS, f"{key}/ops.jsonl", newest.ts, data)


def _activity_event(world: str, row: dict) -> WatchEvent:
    ts = float(row.get("ts") or 0.0)
    data = {
        "world": world,
        "id": str(row.get("id") or ""),
        "ts": ts,
        "actor": actor_json(row.get("actor")),
        "kind": str(row.get("kind") or ""),
        "plan": row.get("plan"),
        "rev": row.get("rev"),
        "text": str(row.get("text") or ""),
        "args": row.get("args"),
    }
    return WatchEvent(KIND_ACTIVITY, data["id"], ts, data)


class LogTail:
    """Where each tailed file was read up to, and what the settings file looked like."""

    def __init__(self) -> None:
        self._offsets: dict[Path, int] | None = None
        self._settings_seen = False
        self._settings_stamp: tuple[int, int] | None = None

    def _tailed(self) -> list[tuple[str, str, Path]]:
        """``(kind, world, path)`` for every plan log and journal file on disk now."""
        found = []
        for kind, root, pattern in (
            (KIND_PLANS, config.plans_dir(), "*/ops.jsonl"),
            (KIND_ACTIVITY, config.activity_dir(), "*.jsonl"),
        ):
            try:
                worlds = sorted(d for d in root.iterdir() if d.is_dir())
            except OSError:
                continue
            for world in worlds:
                found += [(kind, world.name, path) for path in sorted(world.glob(pattern))]
        return found

    def tail_scan(self) -> list[WatchEvent]:
        """New commits and entries since the last call; the first call only takes offsets.

        A file first seen after that baseline is read from its start, which is how a new
        plan's ``create`` is announced.
        """
        files = self._tailed()
        if self._offsets is None:
            self._offsets = {path: _end_of_last_line(path) for _kind, _world, path in files}
            return []
        events: list[WatchEvent] = []
        for kind, world, path in files:
            offset = self._offsets.get(path, 0)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < offset:
                self._offsets[path] = _end_of_last_line(path)
                continue
            if size == offset:
                continue
            rows, self._offsets[path] = journal.tail(path, offset)
            if not rows:
                continue
            if kind == KIND_PLANS:
                event = _plan_event(world, path.parent.name, rows)
                events += [] if event is None else [event]
            else:
                events += [_activity_event(world, row) for row in rows]
        return events

    def settings_scan(self) -> list[WatchEvent]:
        """The settings file as one event when its stamp moved; the first call only records it."""
        try:
            stat = config.settings_path().stat()
            stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        first = not self._settings_seen
        if not first and stamp == self._settings_stamp:
            return []
        self._settings_seen = True
        self._settings_stamp = stamp
        if first or stamp is None:
            return []
        try:
            view = settings.read()
        except Exception:
            return []
        data = settings_json(view)
        return [WatchEvent(KIND_SETTINGS, "settings.json", float(view["updated"] or 0.0), data)]

    def scan(self) -> list[WatchEvent]:
        """Everything tailed since the last scan. Blocking: run it through ``asyncio.to_thread``."""
        return self.tail_scan() + self.settings_scan()

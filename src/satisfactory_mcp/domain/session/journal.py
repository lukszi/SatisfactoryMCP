"""The activity journal: one append-only file per writing process (contract §8).

Holds what is not a plan edit, such as a chat solve that saved nothing. docs/mcp-surface.md
§10.1k says who writes what, and why nothing is written until a process calls ``set_writer``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ... import config
from ...core.jsontypes import JsonObject, JsonValue
from ..planning.stored.planlog import Actor
from .views import JournalEntry

__all__ = ["KINDS", "append", "files", "read", "set_writer", "tail", "writer_name"]

KINDS = (
    "plan.solve",
    "plan.view",
    "plan.rejected",
    "ask.add",
    "ask.drop",
    "ask.seen",
    "ask.answered",
    "world.find",
    "label.rename",
    "advice.hide",
    "advice.restore",
    "pin.add",
    "pin.edit",
    "pin.drop",
)
MAX_LINE = 1000
MAX_TEXT = 200

_lock = threading.Lock()
_writer = ""
_seq: dict[Path, int] = {}


def set_writer(kind: str) -> None:
    global _writer
    if kind not in ("chat", "web"):
        raise ValueError(f"a journal writer is 'chat' or 'web', not {kind!r}")
    _writer = f"{kind}-{os.getpid()}"


def writer_name() -> str:
    return _writer


def _dir(world_id: str) -> Path:
    return config.activity_dir() / config.world_file_stem(world_id)


def files(world_id: str) -> list[Path]:
    folder = _dir(world_id)
    return sorted(folder.glob("*.jsonl")) if folder.is_dir() else []


def _entries(data: bytes) -> list[JsonObject]:
    out: list[JsonObject] = []
    for line in data.split(b"\n")[:-1]:
        try:
            entry: JsonValue = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _number(value: JsonValue) -> float:
    """A stamp or a sequence number as a line wrote it; 0 for none."""
    return float(value) if isinstance(value, int | float | str) and value else 0.0


def _last_seq(path: Path) -> int:
    try:
        entries = _entries(path.read_bytes())
    except FileNotFoundError:
        return 0
    return max((int(_number(e.get("seq"))) for e in entries), default=0)


def _drop_torn_tail(path: Path) -> None:
    """Cut a last line with no newline: a crashed writer can leave half a line."""
    if path.is_file() and path.stat().st_size:
        with open(path, "r+b") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0)
                data = handle.read()
                handle.seek(data.rfind(b"\n") + 1)
                handle.truncate()


def _append_line(path: Path, line: bytes) -> None:
    _drop_torn_tail(path)
    with open(path, "ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _line(entry: JournalEntry) -> bytes:
    return (json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def append(
    world_id: str,
    kind: str,
    *,
    actor: Actor,
    sav: str = "",
    tool: str = "",
    plan: str | None = None,
    rev: int | None = None,
    args: Mapping[str, object] | None = None,
    text: str = "",
) -> JournalEntry | None:
    writer = _writer
    if not writer:
        return None
    try:
        with _lock:
            path = _dir(world_id) / f"{writer}.jsonl"
            if path not in _seq:
                _seq[path] = _last_seq(path)
            seq = _seq[path] + 1
            entry: JournalEntry = {
                "id": f"{writer}:{seq}",
                "seq": seq,
                "ts": time.time(),
                "actor": actor.to_dict(),
                "sav": sav,
                "kind": kind,
                "tool": tool,
                "plan": plan,
                "rev": rev,
                "args": None if args is None else dict(args),
                "text": text[:MAX_TEXT],
            }
            line = _line(entry)
            if len(line) > MAX_LINE:
                entry["args"] = None
                line = _line(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            _append_line(path, line)
            _seq[path] = seq
            return entry
    except OSError:
        return None


def read(world_id: str, since_ts: float = 0.0, limit: int = 200) -> list[JournalEntry]:
    """The newest ``limit`` entries after ``since_ts`` from every writer, oldest first.

    The entries are ``append``'s lines; an older writer's may lack a field."""
    out: list[JsonObject] = []
    for path in files(world_id):
        try:
            out += [e for e in _entries(path.read_bytes()) if _number(e.get("ts")) > since_ts]
        except OSError:
            continue
    out.sort(key=lambda e: (_number(e.get("ts")), str(e.get("id") or "")))
    return cast(list[JournalEntry], out[-limit:] if limit > 0 else [])


def tail(path: Path, offset: int) -> tuple[list[JsonObject], int]:
    try:
        size = path.stat().st_size
        if size < offset:
            offset = 0
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    return _entries(data[:end]), offset + end

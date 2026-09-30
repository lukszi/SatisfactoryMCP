"""The activity journal: one append-only file per writing process (contract §8).

Holds what is not a plan edit, such as a chat solve that saved nothing. docs/mcp-surface.md
§10.1k says who writes what, and why nothing is written until a process calls ``set_writer``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ... import config
from .planlog import Actor

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
)
MAX_LINE = 1000
MAX_TEXT = 200

_lock = threading.Lock()
_writer = ""
_seq: dict[str, int] = {}


def set_writer(kind: str) -> None:
    global _writer
    if kind not in ("chat", "web"):
        raise ValueError(f"a journal writer is 'chat' or 'web', not {kind!r}")
    _writer = f"{kind}-{os.getpid()}"


def writer_name() -> str:
    return _writer


def _safe(world_id: str) -> str:
    return "".join(c for c in world_id if c.isalnum() or c in "-_") or "world"


def _dir(world_id: str) -> Path:
    return config.activity_dir() / _safe(world_id)


def files(world_id: str) -> list[Path]:
    folder = _dir(world_id)
    return sorted(folder.glob("*.jsonl")) if folder.is_dir() else []


def _entries(data: bytes) -> list[dict]:
    out = []
    for line in data.split(b"\n")[:-1]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _last_seq(path: Path) -> int:
    try:
        entries = _entries(path.read_bytes())
    except FileNotFoundError:
        return 0
    return max((int(e.get("seq") or 0) for e in entries), default=0)


def _write(path: Path, line: bytes) -> None:
    if path.is_file() and path.stat().st_size:
        with open(path, "r+b") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0)
                data = handle.read()
                handle.seek(data.rfind(b"\n") + 1)
                handle.truncate()
    with open(path, "ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _line(entry: dict) -> bytes:
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
    args: dict | None = None,
    text: str = "",
) -> dict | None:
    writer = _writer
    if not writer:
        return None
    try:
        with _lock:
            path = _dir(world_id) / f"{writer}.jsonl"
            if path not in _seq:
                _seq[path] = _last_seq(path)
            seq = _seq[path] + 1
            entry = {
                "id": f"{writer}:{seq}",
                "seq": seq,
                "ts": time.time(),
                "actor": actor.to_dict(),
                "sav": sav,
                "kind": kind,
                "tool": tool,
                "plan": plan,
                "rev": rev,
                "args": args,
                "text": text[:MAX_TEXT],
            }
            line = _line(entry)
            if len(line) > MAX_LINE:
                entry["args"] = None
                line = _line(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write(path, line)
            _seq[path] = seq
            return entry
    except OSError:
        return None


def read(world_id: str, since_ts: float = 0.0, limit: int = 200) -> list[dict]:
    """The newest ``limit`` entries after ``since_ts`` from every writer, oldest first."""
    out = []
    for path in files(world_id):
        try:
            out += [e for e in _entries(path.read_bytes()) if float(e.get("ts") or 0) > since_ts]
        except OSError:
            continue
    out.sort(key=lambda e: (float(e.get("ts") or 0), str(e.get("id") or "")))
    return out[-limit:] if limit > 0 else []


def tail(path: Path, offset: int) -> tuple[list[dict], int]:
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

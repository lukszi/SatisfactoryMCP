"""What the page has open: one small file per world, written by the web process alone.

docs/planner_slice_contract.md §9 is the specification. The web server is the only writer,
so there is no lock; ``atomic.write_text`` keeps a reader from ever seeing half a file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ... import config
from ...core import atomic

__all__ = [
    "FOLLOW",
    "OPEN_WITHIN_S",
    "SCHEMA",
    "VIEWS",
    "InvalidFocus",
    "is_open",
    "path_for",
    "read",
    "write",
]

SCHEMA = 1
OPEN_WITHIN_S = 45.0
VIEWS = ("map", "dashboard", "planner")
FOLLOW = ("follow", "toasts", "off")


class InvalidFocus(ValueError):
    pass


def path_for(world_id: str) -> Path:
    return config.ui_dir() / f"{config.world_file_stem(world_id)}.json"


def _text(focus: dict, name: str, default: str = "") -> str:
    value = focus.get(name, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise InvalidFocus(f"{name} must be text, not {value!r}")
    return value


def _choice(focus: dict, name: str, allowed: tuple[str, ...], default: str) -> str:
    value = _text(focus, name, default) or default
    if value not in allowed:
        raise InvalidFocus(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _rev(focus: dict) -> int | None:
    value = focus.get("rev")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidFocus(f"rev must be a version number, not {value!r}")
    return value


def _selection(focus: dict) -> dict | None:
    value = focus.get("selection")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidFocus(f"selection must be an object or null, not {value!r}")
    return {name: _text(value, name) for name in ("kind", "label", "ref")}


def _clean(focus: dict) -> dict:
    """The stored shape of ``focus``, every field present; raises ``InvalidFocus``."""
    if not isinstance(focus, dict):
        raise InvalidFocus(f"focus must be an object, not {focus!r}")
    return {
        "schema": SCHEMA,
        "heartbeat": 0.0,
        "view": _choice(focus, "view", VIEWS, "map"),
        "dash": _text(focus, "dash"),
        "plan": _text(focus, "plan") or None,
        "rev": _rev(focus),
        "tab": _text(focus, "tab"),
        "selection": _selection(focus),
        "follow": _choice(focus, "follow", FOLLOW, "follow"),
        "sav": _text(focus, "sav"),
    }


def write(world_id: str, focus: dict) -> dict:
    out = _clean(focus)
    out["heartbeat"] = time.time()
    path = path_for(world_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(out, ensure_ascii=False))
    return out


def read(world_id: str) -> dict | None:
    try:
        raw = json.loads(path_for(world_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def is_open(focus: dict | None, now: float | None = None) -> bool:
    if not focus:
        return False
    try:
        beat = float(focus.get("heartbeat") or 0.0)
    except (TypeError, ValueError):
        return False
    return (time.time() if now is None else now) - beat <= OPEN_WITHIN_S

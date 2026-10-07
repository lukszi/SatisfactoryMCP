"""What the page has open: one small file per world, written by the web process alone.

docs/planner_slice_contract.md §9 is the specification. The web server is the only writer,
so there is no lock; ``atomic.write_text`` keeps a reader from ever seeing half a file.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from ... import config
from ...core import atomic
from ...core.jsontypes import JsonObject, JsonValue, is_object_dict
from .views import FocusDoc, FocusSelection

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


def _text(focus: Mapping[str, object], name: str, default: str = "") -> str:
    value = focus.get(name, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise InvalidFocus(f"{name} must be text, not {value!r}")
    return value


def _choice(focus: Mapping[str, object], name: str, allowed: tuple[str, ...], default: str) -> str:
    value = _text(focus, name, default) or default
    if value not in allowed:
        raise InvalidFocus(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def _rev(focus: Mapping[str, object]) -> int | None:
    value = focus.get("rev")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidFocus(f"rev must be a version number, not {value!r}")
    return value


def _selection(focus: Mapping[str, object]) -> FocusSelection | None:
    value = focus.get("selection")
    if value is None:
        return None
    if not is_object_dict(value):
        raise InvalidFocus(f"selection must be an object or null, not {value!r}")
    picked = value
    return {
        "kind": _text(picked, "kind"),
        "label": _text(picked, "label"),
        "ref": _text(picked, "ref"),
    }


def _clean(focus: object) -> FocusDoc:
    """The stored shape of ``focus``, every field present; raises ``InvalidFocus``."""
    if not is_object_dict(focus):
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


def write(world_id: str, focus: Mapping[str, object]) -> FocusDoc:
    out = _clean(focus)
    out["heartbeat"] = time.time()
    path = path_for(world_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(out, ensure_ascii=False))
    return out


def read(world_id: str) -> JsonObject | None:
    try:
        raw: JsonValue = json.loads(path_for(world_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def is_open(focus: Mapping[str, object] | None, now: float | None = None) -> bool:
    if not focus:
        return False
    stamp = focus.get("heartbeat") or 0.0
    if not isinstance(stamp, int | float | str):
        return False
    try:
        beat = float(stamp)
    except ValueError:
        return False
    return (time.time() if now is None else now) - beat <= OPEN_WITHIN_S

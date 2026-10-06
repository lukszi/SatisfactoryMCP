"""Asks: numbered questions the page queues for chat, marked seen and answered by chat.

docs/planner-p4_contract.md §4 is the specification; docs/planner_p4.md says what was built.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ... import config
from ...core import filelock, schema
from ..planning.stored.planlog import PlanLog, PlanLogError

__all__ = [
    "ABOUT_KINDS",
    "ANSWER_MAX",
    "LABEL_MAX",
    "MAX_LIVE",
    "REF_MAX",
    "SCHEMA",
    "TEXT_MAX",
    "AboutMissing",
    "AskError",
    "AskMissing",
    "AskStale",
    "create",
    "drop",
    "live",
    "mark_answered",
    "mark_seen",
    "parse",
    "parse_answer",
    "path_for",
    "read",
    "row",
    "state_of",
]

SCHEMA = 1
MAX_LIVE = 200
TEXT_MAX = 200
LABEL_MAX = 120
REF_MAX = 200
ANSWER_MAX = 200
ABOUT_KINDS = ("plan", "process", "stage", "item", "pin", "advice")
_ASK = re.compile(r"\s*ask:(\d+)\s*", re.IGNORECASE)
_ANSWERED = re.compile(r"\s*ask:(\d+)(?:\s*[:=\-–—]\s*|\s+|$)(.*)", re.IGNORECASE | re.DOTALL)
_KEY = re.compile(r"[0-9a-f]{8}")


class AskError(ValueError):
    """An ask request that cannot be honoured, worded for the player."""


class AboutMissing(AskError):
    """The plan an ask would be about is not a live plan in this world."""


class AskMissing(AskError, KeyError):
    def __init__(self, n: int, deleted: bool = False, top: int = 0) -> None:
        if deleted:
            text = f"ask:{n} was deleted"
        elif top:
            text = f"ask:{n} does not exist (asks run to ask:{top})"
        else:
            text = f"ask:{n} does not exist (no asks yet)"
        super().__init__(text)
        self.n = n
        self.deleted = deleted

    def __str__(self) -> str:
        return self.args[0]


class AskStale(AskError):
    def __init__(self, ask: dict) -> None:
        super().__init__(f"ask:{ask['n']} changed since you read it")
        self.ask = ask


def path_for(world_id: str) -> Path:
    return config.asks_dir() / f"{config.world_file_stem(world_id)}.json"


def _empty() -> dict:
    return {"schema": SCHEMA, "version": 0, "next": 1, "asks": []}


def read(world_id: str) -> dict:
    path = path_for(world_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    schema.check(raw, SCHEMA, path)
    if not isinstance(raw, dict) or not isinstance(raw.get("asks"), list):
        return _empty()
    out = _empty()
    out["version"] = int(raw.get("version") or 0)
    out["asks"] = [a for a in raw["asks"] if isinstance(a, dict) and isinstance(a.get("n"), int)]
    top = max((a["n"] for a in out["asks"]), default=0)
    out["next"] = max(int(raw.get("next") or 1), top + 1)
    return out


def _locked_update(world_id: str, change):
    """Run ``change(data) -> (result, dirty)`` under the file lock; write when dirty."""
    return filelock.update_versioned_json(path_for(world_id), lambda: read(world_id), change)


def parse(text) -> int | None:
    if not isinstance(text, str):
        return None
    hit = _ASK.fullmatch(text)
    return int(hit.group(1)) if hit else None


def parse_answer(text) -> tuple[int, str] | None:
    """``"ask:7 the Blender makes the fuel"`` -> ``(7, "the Blender makes the fuel")``; the
    answer is one line of at most ``ANSWER_MAX`` characters, ``""`` when none is given."""
    if not isinstance(text, str):
        return None
    hit = _ANSWERED.fullmatch(text)
    if hit is None:
        return None
    line = " ".join(hit.group(2).split())
    if len(line) > ANSWER_MAX:
        line = line[: ANSWER_MAX - 1].rstrip() + "…"
    return int(hit.group(1)), line


def _bump_rev(ask: dict) -> None:
    ask["rev"] = int(ask.get("rev") or 1) + 1


def state_of(ask: dict) -> str:
    if ask.get("answered"):
        return "answered"
    return "seen" if ask.get("seen") else "open"


def _question_text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AskError("an ask needs a question")
    text = value.strip()
    if len(text) > TEXT_MAX:
        raise AskError(f"an ask is at most {TEXT_MAX} characters, not {len(text)}")
    return text


def _about_field(name: str, value, limit: int, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise AskError(f"about.{name} is text, not {value!r}")
    text = value.strip()
    if required and not text:
        raise AskError(f"about.{name} cannot be blank")
    if len(text) > limit:
        raise AskError(f"about.{name} is at most {limit} characters, not {len(text)}")
    return text


def _validated_about(world_id: str, about) -> dict:
    if not isinstance(about, dict):
        raise AskError(f"about must be an object, not {about!r}")
    kind = about.get("kind")
    if kind not in ABOUT_KINDS:
        raise AskError(f"about.kind is one of {', '.join(ABOUT_KINDS)}, not {kind!r}")
    out = {
        "kind": kind,
        "label": _about_field("label", about.get("label"), LABEL_MAX, True),
        "ref": _about_field("ref", about.get("ref"), REF_MAX, False),
        "plan": None,
        "rev": None,
    }
    plan = about.get("plan")
    if plan is not None:
        if not isinstance(plan, str) or not _KEY.fullmatch(plan):
            raise AboutMissing(f"no plan “{plan}” in this world")
        try:
            state = PlanLog(world_id).state(plan)
        except (PlanLogError, OSError):
            state = None
        if state is None or state.forgotten:
            raise AboutMissing(f"no plan “{plan}” in this world")
        out["plan"] = plan
    rev = about.get("rev")
    if rev is not None:
        if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
            raise AskError(f"about.rev is a version number, not {rev!r}")
        out["rev"] = rev
    return out


def _plan_names(world_id: str) -> dict[str, str]:
    try:
        return {s.key: s.name for s in PlanLog(world_id).heads(include_forgotten=True)}
    except (PlanLogError, OSError):
        return {}


def row(ask: dict, names: dict[str, str]) -> dict:
    about = dict(ask.get("about") or {})
    plan = about.get("plan")
    return {
        "n": ask["n"],
        "id": f"ask:{ask['n']}",
        "text": str(ask.get("text") or ""),
        "about": {
            "kind": str(about.get("kind") or ""),
            "label": str(about.get("label") or ""),
            "ref": str(about.get("ref") or ""),
            "plan": plan,
            "rev": about.get("rev"),
        },
        "state": state_of(ask),
        "rev": int(ask.get("rev") or 1),
        "created": float(ask.get("created") or 0.0),
        "seen": ask.get("seen"),
        "seen_by": str(ask.get("seen_by") or ""),
        "answered": ask.get("answered"),
        "answered_by": str(ask.get("answered_by") or ""),
        "answer": str(ask.get("answer") or ""),
        "plan_name": names.get(plan) if plan else None,
        "copy": f"ask:{ask['n']} {ask.get('text') or ''}",
    }


def live(world_id: str) -> list[dict]:
    data = read(world_id)
    names = _plan_names(world_id)
    return [row(a, names) for a in data["asks"] if not a.get("deleted")]


def create(world_id: str, text: str, about: dict) -> dict:
    question = _question_text(text)
    subject = _validated_about(world_id, about)

    def change(data: dict):
        if sum(1 for a in data["asks"] if not a.get("deleted")) >= MAX_LIVE:
            raise AskError(f"this world already has {MAX_LIVE} asks; delete one first")
        ask = {
            "n": data["next"],
            "text": question,
            "about": subject,
            "rev": 1,
            "created": time.time(),
            "seen": None,
            "seen_by": "",
            "answered": None,
            "answered_by": "",
            "deleted": False,
        }
        data["next"] += 1
        data["asks"].append(ask)
        return ask, True

    return row(_locked_update(world_id, change), _plan_names(world_id))


def _find(data: dict, n: int) -> dict:
    ask = next((a for a in data["asks"] if a["n"] == n), None)
    if ask is None:
        raise AskMissing(n, top=data["next"] - 1)
    if ask.get("deleted"):
        raise AskMissing(n, deleted=True)
    return ask


def drop(world_id: str, n: int, rev: int) -> dict:
    def change(data: dict):
        ask = _find(data, n)
        if isinstance(rev, bool) or rev != ask.get("rev"):
            raise AskStale(dict(ask))
        ask["deleted"] = True
        _bump_rev(ask)
        return dict(ask), True

    return row(_locked_update(world_id, change), _plan_names(world_id))


def mark_seen(world_id: str, ns: list[int], who: str) -> list[int]:
    """Open asks among ``ns`` become seen; returns the ones that were newly seen."""
    wanted = set(ns)
    if not wanted:
        return []

    def change(data: dict):
        now, fresh = time.time(), []
        for ask in data["asks"]:
            if ask["n"] in wanted and not ask.get("deleted") and state_of(ask) == "open":
                ask["seen"], ask["seen_by"] = now, who
                _bump_rev(ask)
                fresh.append(ask["n"])
        return fresh, bool(fresh)

    return _locked_update(world_id, change)


def mark_answered(
    world_id: str, ns: list[int], who: str, answers: dict[int, str] | None = None
) -> list[int]:
    """Asks among ``ns`` become answered, keeping the answer line given for one; returns the
    asks that changed. ``AskMissing`` when one is unknown or deleted."""
    if not ns:
        return []
    answers = answers or {}

    def change(data: dict):
        found = [_find(data, n) for n in dict.fromkeys(ns)]
        now, fresh = time.time(), []
        for ask in found:
            line = answers.get(ask["n"], "")
            if ask.get("answered") and (not line or line == ask.get("answer")):
                continue
            if not ask.get("answered"):
                ask["answered"], ask["answered_by"] = now, who
            if line:
                ask["answer"] = line
            _bump_rev(ask)
            fresh.append(ask["n"])
        return fresh, bool(fresh)

    return _locked_update(world_id, change)

"""The one wire format a map generator reports progress in: ``::plan`` and ``::stage`` lines.

A generator prints these beside its human log lines; ``domain.maps.jobs`` decodes them and
falls back to its regexes for a line that is not one. Stage ids are ``<step>`` or
``<step>:<layer>``. Standard library only: the generators import this too.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["PlanEvent", "StageEvent", "decode", "encode_plan", "encode_stage"]

PLAN_PREFIX = "::plan "
STAGE_PREFIX = "::stage "


@dataclass(frozen=True)
class StageEvent:
    id: str
    done: float


@dataclass(frozen=True)
class PlanEvent:
    steps: tuple[tuple[str, float], ...]


def _dumps(body: dict) -> str:
    return json.dumps(body, separators=(",", ":"))


def encode_stage(id: str, done: float) -> str:
    """``::stage {"id":"draw:painted","done":0.5}``; ``done`` is clamped to 0..1."""
    return STAGE_PREFIX + _dumps({"id": id, "done": round(min(max(done, 0.0), 1.0), 4)})


def encode_plan(steps: Sequence[tuple[str, float]]) -> str:
    """``::plan {"steps":[["sweep",36.0],...]}``: the steps a run expects, in seconds."""
    return PLAN_PREFIX + _dumps({"steps": [[step, float(s)] for step, s in steps]})


def decode(line: str) -> PlanEvent | StageEvent | None:
    """The event a line carries, or ``None`` for a human line or a malformed one."""
    text = line.strip()
    try:
        if text.startswith(STAGE_PREFIX):
            body = json.loads(text[len(STAGE_PREFIX) :])
            return StageEvent(str(body["id"]), float(body["done"]))
        if text.startswith(PLAN_PREFIX):
            body = json.loads(text[len(PLAN_PREFIX) :])
            return PlanEvent(tuple((str(step), float(s)) for step, s in body["steps"]))
    except (ValueError, KeyError, TypeError):
        return None
    return None

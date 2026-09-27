"""``/api/plan/solve``, ``/api/ui/focus`` and ``/api/activity``: the workbench's other half.

Solve re-solves a request or a stored version and stores nothing. Focus records what the
page has open, for ``ui_context``. Activity is the plan logs and the journal merged by time.
docs/planner_slice_contract.md §9 and §11 are the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import re
import time
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request

from ....domain.planning import focus, journal, summary
from ....domain.planning.planlog import InvalidOp, PlanArgs, PlanLog, UnknownPlan
from ..serial import ActorBody, _actor_json, _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")

_KEY = re.compile(r"[0-9a-f]{8}")


class SolveRate(TypedDict):
    item: str
    per_min: float


class SolveRow(TypedDict):
    """One build row. ``clock`` is a fraction (1.0 = 100%) and ``mw`` is signed: negative draws."""

    building: str
    recipe: str
    recipe_id: str | None
    item: str | None
    machines: int
    clock: float
    mw: float
    inputs: list[SolveRate]
    outputs: list[SolveRate]
    required: bool


class SolveResponse(TypedDict):
    """A solve's facts. Infeasible is a 200 with ``feasible: false``; unknown MW are null."""

    feasible: bool
    headline: str
    plan_id: str
    notes: list[str]
    warnings: list[str]
    machines: int
    processes: int
    mw_draw: float | None
    mw_generated: float | None
    mw_net: float | None
    grid_import: bool
    exports: list[SolveRate]
    rows: list[SolveRow]
    shards: int | None
    sloops_used: int
    blockers: list[str]
    token: str


class SolveBody(TypedDict):
    """Exactly one of ``args`` (a request) and ``key`` (a stored plan, at ``rev`` or its head)."""

    args: NotRequired[dict | None]
    key: NotRequired[str | None]
    rev: NotRequired[int | None]


class Selection(TypedDict):
    kind: str
    label: str
    ref: str


class FocusBody(TypedDict):
    view: str
    dash: NotRequired[str]
    plan: NotRequired[str | None]
    rev: NotRequired[int | None]
    tab: NotRequired[str]
    selection: NotRequired[Selection | None]
    follow: NotRequired[str]
    sav: NotRequired[str]


class FocusResponse(TypedDict):
    ok: bool
    heartbeat: float


class ActivityRow(TypedDict):
    """A plan commit (``source`` "plan", ``kind`` "commit") or a journal entry."""

    id: str
    ts: float
    source: str
    actor: ActorBody
    kind: str
    plan: str | None
    name: str | None
    rev: int | None
    text: str
    args: dict | None


class ActivityResponse(TypedDict):
    now: float
    entries: list[ActivityRow]


@router.post("/plan/solve", response_model=SolveResponse)
def solve_plan(
    request: Request,
    body: Annotated[SolveBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Solve a request or a stored version against this save; nothing is written."""
    args, key = body.get("args"), body.get("key")
    if (args is None) == (key is None):
        return _fail("send exactly one of args and key", 400)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    if key is not None:
        if not _KEY.fullmatch(key):
            return _fail(f"no saved plan with key {key!r}", 404)
        try:
            kwargs = PlanLog(st.world_id).state(key, body.get("rev")).kwargs()
        except (UnknownPlan, InvalidOp) as exc:
            return _fail(str(exc), 404)
    else:
        try:
            kwargs = PlanArgs.from_dict(args).kwargs()
        except InvalidOp as exc:
            return _fail(str(exc), 400)
    try:
        return summary.solve_summary(st.game, st, kwargs)
    except ValueError as exc:
        return _fail(str(exc), 400)


@router.put("/ui/focus", response_model=FocusResponse)
def put_focus(
    request: Request,
    body: Annotated[FocusBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Record what the page has open, stamped with a heartbeat. The page's only focus write."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        written = focus.write(st.world_id, dict(body))
    except focus.InvalidFocus as exc:
        return _fail(str(exc), 400)
    return {"ok": True, "heartbeat": written["heartbeat"]}


def _commit_rows(log: PlanLog, since: float) -> list[ActivityRow]:
    rows: list[ActivityRow] = []
    for state in log.heads(include_forgotten=True):
        for commit in log.commits(state.key):
            if commit.ts <= since:
                continue
            rows.append(
                {
                    "id": f"{state.key}:v{commit.rev}",
                    "ts": commit.ts,
                    "source": "plan",
                    "actor": _actor_json(commit.actor),
                    "kind": "commit",
                    "plan": state.key,
                    "name": state.name,
                    "rev": commit.rev,
                    "text": commit.text(),
                    "args": None,
                }
            )
    return rows


def _entry_row(entry: dict, names: dict[str, str]) -> ActivityRow:
    plan = entry.get("plan")
    return {
        "id": str(entry.get("id") or ""),
        "ts": float(entry.get("ts") or 0.0),
        "source": "journal",
        "actor": _actor_json(entry.get("actor")),
        "kind": str(entry.get("kind") or ""),
        "plan": plan,
        "name": names.get(plan) if plan else None,
        "rev": entry.get("rev"),
        "text": str(entry.get("text") or ""),
        "args": entry.get("args"),
    }


@router.get("/activity", response_model=ActivityResponse)
def activity(
    request: Request,
    since: float = 0.0,
    limit: int = 50,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Plan commits and journal entries after ``since``, oldest first, the newest ``limit``."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    now = time.time()
    limit = max(0, min(limit, 500))
    log = PlanLog(st.world_id, st.header.get("session_name") or "")
    names = {s.key: s.name for s in log.heads(include_forgotten=True)}
    rows = _commit_rows(log, since)
    rows += [_entry_row(e, names) for e in journal.read(st.world_id, since_ts=since, limit=limit)]
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    return {"now": now, "entries": rows[-limit:] if limit else []}

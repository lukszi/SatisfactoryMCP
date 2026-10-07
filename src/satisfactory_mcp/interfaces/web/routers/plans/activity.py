"""``/api/ui/focus`` and ``/api/activity``: what the page has open, and what happened since.

Focus records the open view for chat's ``ui_context``; activity is the plan logs and the
journal merged by time. docs/planner_slice_contract.md §11 specifies both. Handler names are
operation_ids (wire rule 1 of docs/web-wire.md).
"""

from __future__ import annotations

import time
from typing import Annotated, Any, NotRequired

from fastapi import APIRouter, Body, Request
from typing_extensions import TypedDict

from .....domain.planning.stored.planlog import PlanLog
from .....domain.session import focus, journal
from .....domain.session.views import JournalEntry
from ...serial import ActorBody, actor_json, error_response, plan_log, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


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
    count: int  # entries a collapsed run stands for; 1 otherwise


class ActivityResponse(TypedDict):
    now: float
    entries: list[ActivityRow]


@router.put("/ui/focus", response_model=FocusResponse)
def put_focus(
    request: Request,
    body: Annotated[FocusBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Record what the page has open, stamped with a heartbeat. The page's only focus write."""
    st = require_world(request, save, world)
    try:
        written = focus.write(st.world_id, dict(body))
    except focus.InvalidFocus as exc:
        return error_response(str(exc), 400)
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
                    "actor": actor_json(commit.actor),
                    "kind": "commit",
                    "plan": state.key,
                    "name": state.name,
                    "rev": commit.rev,
                    "text": commit.text(),
                    "args": None,
                    "count": 1,
                }
            )
    return rows


def _entry_row(entry: JournalEntry, names: dict[str, str]) -> ActivityRow:
    plan = entry.get("plan")
    return {
        "id": str(entry.get("id") or ""),
        "ts": float(entry.get("ts") or 0.0),
        "source": "journal",
        "actor": actor_json(entry.get("actor")),
        "kind": str(entry.get("kind") or ""),
        "plan": plan,
        "name": names.get(plan) if plan else None,
        "rev": entry.get("rev"),
        "text": str(entry.get("text") or ""),
        "args": entry.get("args"),
        "count": 1,
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
    st = require_world(request, save, world)
    now = time.time()
    limit = max(0, min(limit, 500))
    log = plan_log(st)
    names = {s.key: s.name for s in log.heads(include_forgotten=True)}
    rows = _commit_rows(log, since)
    rows += [_entry_row(e, names) for e in journal.read(st.world_id, since_ts=since, limit=500)]
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    rows = _collapse_views(rows)
    return {"now": now, "entries": rows[-limit:] if limit else []}


def _view_key(row: ActivityRow) -> tuple | None:
    if row["kind"] == "world.find":
        return ("world.find", row["actor"].get("kind"), row["actor"].get("pid"))
    if row["kind"] != "plan.view":
        return None
    args = row["args"] if isinstance(row["args"], dict) else {}
    look = tuple(str(args.get(k)) for k in ("view", "item", "stage", "section"))
    return (row["plan"], row["actor"].get("kind"), row["actor"].get("pid"), *look)


def _collapse_views(rows: list[ActivityRow]) -> list[ActivityRow]:
    """A run of the same ``plan.view``, or of ``world.find`` by one actor, keeps its newest
    entry and counts the run, so repeat looks cannot crowd plan commits out of the cap."""
    out: list[ActivityRow] = []
    for row in rows:
        key = _view_key(row)
        if key is not None and out and _view_key(out[-1]) == key:
            out[-1] = {**row, "count": out[-1]["count"] + row["count"]}
        else:
            out.append(row)
    return out

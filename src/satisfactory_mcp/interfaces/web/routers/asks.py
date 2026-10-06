"""``/api/asks``: questions the page queues for chat, which chat marks seen and answered.

The store is ``domain/planning/asks.py``; every write passes the guard and appends one journal
entry, and a delete carries the ``rev`` it read. docs/planner-p4_contract.md §4, §5 and §7 are
the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....domain.planning import asks as ask_store
from ....domain.planning import journal
from ....domain.planning.planlog import Actor
from ..serial import busy_response, error_response, newer_schema_response, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


class AskAbout(TypedDict):
    """What an ask is about: ``kind`` is plan, process, stage, item or pin; ``plan`` a plan key."""

    kind: str
    label: str
    ref: str
    plan: NotRequired[str | None]
    rev: NotRequired[int | None]


class AskRow(TypedDict):
    """One ask. ``state`` is open, seen or answered; ``answer`` is the one line chat left
    with it ("" for none); ``copy`` is what the page puts on the clipboard; ``plan_name`` is
    ``about.plan`` resolved when read."""

    n: int
    id: str
    text: str
    about: AskAbout
    state: str
    rev: int
    created: float
    seen: float | None
    seen_by: str
    answered: float | None
    answered_by: str
    answer: str
    plan_name: str | None
    copy: str


class AsksResponse(TypedDict):
    version: int
    asks: list[AskRow]


class AskCreateBody(TypedDict):
    text: str
    about: AskAbout


class AskDropBody(TypedDict):
    rev: int


class AskDropped(TypedDict):
    ok: bool
    n: int


class AskStaleResponse(TypedDict):
    """The 409 of an ask delete: nothing was written; ``ask`` is the row as it stands."""

    error: str
    stale: bool
    ask: AskRow


def _page() -> Actor:
    return Actor("page", "", os.getpid())


STORE_NAME = "the asks"


def _refused(world_id: str, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return newer_schema_response(exc, STORE_NAME)
    if isinstance(exc, LockTimeout):
        return busy_response("asks", exc)
    if isinstance(exc, ask_store.AskStale):
        names = {r["about"]["plan"]: r["plan_name"] for r in ask_store.live(world_id)}
        body = {"error": str(exc), "stale": True, "ask": ask_store.row(exc.ask, names)}
        return JSONResponse(body, status_code=409)
    if isinstance(exc, ask_store.AskMissing | ask_store.AboutMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (ask_store.AskError, LockTimeout, NewerSchema)


def _note(world_id: str, kind: str, ask: dict, text: str) -> None:
    about = ask["about"]
    journal.append(
        world_id,
        kind,
        actor=_page(),
        plan=about.get("plan"),
        rev=about.get("rev"),
        args={"n": ask["n"]},
        text=text,
    )


@router.get("/asks", response_model=AsksResponse)
def asks(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every live ask of this world, ascending by number, answered ones included."""
    st = require_world(request, save, world)
    try:
        version = ask_store.read(st.world_id)["version"]
        rows = ask_store.live(st.world_id)
    except NewerSchema as exc:
        return newer_schema_response(exc, STORE_NAME)
    return {"version": version, "asks": rows}


@router.post("/asks", status_code=201, response_model=AskRow)
def create_ask(
    request: Request,
    body: Annotated[AskCreateBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Queue one question for chat; the player pastes its ``copy`` into chat."""
    st = require_world(request, save, world)
    try:
        ask = ask_store.create(st.world_id, body["text"], dict(body["about"]))
    except _ERRORS as exc:
        return _refused(st.world_id, exc)
    _note(st.world_id, "ask.add", ask, f"queued {ask['id']} “{ask['text']}”")
    return ask


@router.delete(
    "/asks/{n}",
    response_model=AskDropped,
    responses={409: {"model": AskStaleResponse}},
)
def drop_ask(
    request: Request,
    n: int,
    body: Annotated[AskDropBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Delete one ask, refused with a 409 when ``rev`` is not its current one."""
    st = require_world(request, save, world)
    try:
        ask = ask_store.drop(st.world_id, n, body["rev"])
    except _ERRORS as exc:
        return _refused(st.world_id, exc)
    _note(st.world_id, "ask.drop", ask, f"deleted ask:{n}")
    return {"ok": True, "n": n}

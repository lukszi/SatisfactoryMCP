"""``/api/asks``: questions the page queues for chat, which chat marks seen and answered.

The store is ``domain/session/asks.py``; every write passes the guard and appends one journal
entry, and a delete carries the ``rev`` it read. docs/planner-p4_contract.md §4, §5 and §7 are
the specification.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.filelock import LockTimeout
from .....core.schema import NewerSchema
from .....domain.session import asks as ask_store
from .....domain.session import journal
from .....domain.session.views import AskAbout, AskRow
from ...serial import (
    Dropped,
    RevBody,
    busy_response,
    error_response,
    newer_schema_response,
    page_actor,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class AsksResponse(TypedDict):
    version: int
    asks: list[AskRow]


class AskCreateBody(TypedDict):
    text: str
    about: AskAbout


class AskStaleResponse(TypedDict):
    """The 409 of an ask delete: nothing was written; ``ask`` is the row as it stands."""

    error: str
    stale: bool
    ask: AskRow


STORE_NAME = "the asks"


def _refused(world_id: str, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return newer_schema_response(exc, STORE_NAME)
    if isinstance(exc, LockTimeout):
        return busy_response("asks", exc)
    if isinstance(exc, ask_store.AskStale):
        names = {
            plan: name
            for r in ask_store.live(world_id)
            if (plan := r["about"].get("plan")) and (name := r["plan_name"]) is not None
        }
        body: AskStaleResponse = {
            "error": str(exc),
            "stale": True,
            "ask": ask_store.row(exc.ask, names),
        }
        return JSONResponse(body, status_code=409)
    if isinstance(exc, ask_store.AskMissing | ask_store.AboutMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (ask_store.AskError, LockTimeout, NewerSchema)


def _journal(world_id: str, kind: str, ask: AskRow, text: str) -> None:
    about = ask["about"]
    journal.append(
        world_id,
        kind,
        actor=page_actor(),
        plan=about.get("plan"),
        rev=about.get("rev"),
        args={"n": ask["n"]},
        text=text,
    )


@router.get("/asks", response_model=AsksResponse)
def asks(
    request: Request, save: str | None = None, world: str | None = None
) -> AsksResponse | JSONResponse:
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
) -> AskRow | JSONResponse:
    """Queue one question for chat; the player pastes its ``copy`` into chat."""
    st = require_world(request, save, world)
    try:
        ask = ask_store.create(st.world_id, body["text"], dict(body["about"]))
    except _ERRORS as exc:
        return _refused(st.world_id, exc)
    _journal(st.world_id, "ask.add", ask, f"queued {ask['id']} “{ask['text']}”")
    return ask


@router.delete(
    "/asks/{n}",
    response_model=Dropped,
    responses={409: {"model": AskStaleResponse}},
)
def drop_ask(
    request: Request,
    n: int,
    body: Annotated[RevBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Dropped | JSONResponse:
    """Delete one ask, refused with a 409 when ``rev`` is not its current one."""
    st = require_world(request, save, world)
    try:
        ask = ask_store.drop(st.world_id, n, body["rev"])
    except _ERRORS as exc:
        return _refused(st.world_id, exc)
    _journal(st.world_id, "ask.drop", ask, f"deleted ask:{n}")
    return {"ok": True, "n": n}

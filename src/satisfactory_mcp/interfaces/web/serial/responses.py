"""How a handler reads the world it was asked about, and how it says no."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from ....core.schema import NewerSchema
from ....domain.planning.planlog import InvalidOp, PlanLog, PlanState, UnknownPlan
from ....domain.world.state import WorldState

__all__ = [
    "PLAN_KEY",
    "RequestRefused",
    "busy_response",
    "check_plan_key",
    "error_response",
    "newer_schema_response",
    "plan_log",
    "plan_not_found",
    "require_plan",
    "require_world",
    "session_name",
    "world_state",
]


class RequestRefused(Exception):
    """A refusal raised from inside a handler; ``app.py`` answers it as ``{"error"}``."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def error_response(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def world_state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)


def require_world(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about, or the 404 that names why it would not load."""
    try:
        return world_state(request, save, world)
    except Exception as exc:
        raise RequestRefused(f"could not read save: {exc}", 404) from exc


#: Every plan key is eight hex digits; anything else is refused before a log is opened.
PLAN_KEY = re.compile(r"[0-9a-f]{8}")


def plan_not_found(key: str) -> RequestRefused:
    return RequestRefused(f"no plan “{key}” in this world", 404)


def check_plan_key(key: str) -> None:
    if not PLAN_KEY.fullmatch(key):
        raise plan_not_found(key)


def session_name(st: WorldState) -> str:
    return st.header.get("session_name") or ""


def plan_log(st: WorldState) -> PlanLog:
    return PlanLog(st.world_id, session_name(st))


def require_plan(log: PlanLog, key: str, rev: int | None = None) -> PlanState:
    """One plan at ``rev`` (its head when ``None``), or the 404 for a plan or rev not there."""
    try:
        return log.state(key, rev)
    except UnknownPlan as exc:
        raise plan_not_found(key) from exc
    except InvalidOp as exc:
        raise RequestRefused(str(exc), 404) from exc


def newer_schema_response(
    exc: NewerSchema,
    what: str,
    *,
    verb: str = "were",
    ending: str = "Upgrade to read them; nothing was changed",
) -> JSONResponse:
    """The 503 for a store a newer version wrote: names the store, never its path."""
    text = (
        f"{what} {verb} saved by a newer version of satisfactory-mcp (schema {exc.found}; this "
        f"one reads up to {exc.known}). {ending}"
    )
    return JSONResponse({"error": text, "newer_schema": True}, status_code=503)


def busy_response(what: str, exc: Exception, *, verb: str = "are") -> JSONResponse:
    """The 503 for a store whose file lock timed out; nothing was written."""
    return error_response(f"{what} {verb} busy, nothing written: {exc}", 503)

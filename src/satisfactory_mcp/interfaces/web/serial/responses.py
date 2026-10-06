"""How a handler reads the world it was asked about, and how it says no."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ....core.schema import NewerSchema
from ....domain.planning.stored.planlog import Actor, InvalidOp, PlanLog, PlanState, UnknownPlan
from ....domain.world.state import WorldState

__all__ = [
    "IMMUTABLE",
    "PLAN_KEY",
    "RequestRefused",
    "busy_response",
    "cached_file",
    "check_plan_key",
    "choice_refusal",
    "error_response",
    "newer_schema_response",
    "page_actor",
    "plan_log",
    "plan_not_found",
    "require_plan",
    "require_world",
    "session_name",
    "sidecar_meta_block",
    "world_state",
]

#: The ``Cache-Control`` of a file asked for with its build tag: it can never change.
IMMUTABLE = "public, max-age=31536000, immutable"


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


def page_actor() -> Actor:
    """Who a write from the page is stamped as: the page, through this server's process."""
    return Actor("page", "", os.getpid())


def choice_refusal(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    """Why ``value`` is not one of ``allowed``, or ``None`` when it is or was not given."""
    if value is None or value.strip().casefold() in allowed:
        return None
    return f"unknown {name} {value!r}. Choose from: {', '.join(allowed)}"


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


def cached_file(
    request: Request,
    path: Path,
    build: str,
    media_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """A generated file tagged with its build: cached for good behind ``?v=``, else revalidated.

    ``immutable`` is earned by the ``?v=`` tag alone, which changes whenever the file is
    regenerated; an untagged fetch revalidates, and the ETag makes that a 304, not the bytes.
    """
    etag = f'"{build}"'
    tagged = {
        **(headers or {}),
        "Cache-Control": IMMUTABLE if "v" in request.query_params else "no-cache",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=tagged)
    return FileResponse(path, media_type=media_type, headers=tagged)


def sidecar_meta_block(path: Path | None) -> dict[str, Any]:
    """The ``_meta`` block of a generator's JSON sidecar; ``{}`` when absent or unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    except (OSError, ValueError):
        return {}
    block = raw.get("_meta") if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else {}

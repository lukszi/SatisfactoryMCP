"""How a handler reads the world it was asked about, and how it says no."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ....core.schema import NewerSchema
from ....domain.world.state import WorldState

__all__ = [
    "RequestRefused",
    "busy_response",
    "error_response",
    "newer_schema_response",
    "require_world",
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

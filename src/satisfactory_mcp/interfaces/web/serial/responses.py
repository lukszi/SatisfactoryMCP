"""How a handler reads the world it was asked about, and how it says no."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ....domain.world.state import WorldState

__all__ = ["RequestRefused", "error_response", "require_world", "world_state"]


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

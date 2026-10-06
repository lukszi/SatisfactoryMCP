"""How a handler reads the world it was asked about, and how it says no."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ....domain.world.state import WorldState

__all__ = ["error_response", "world_state"]


def error_response(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def world_state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)

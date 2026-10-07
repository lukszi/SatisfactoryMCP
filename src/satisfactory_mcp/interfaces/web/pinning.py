"""``?as_of=`` on every read: a page holding a save token gets a refusal, never a blend.

docs/frontend_vision.md §16 has the rule; ``domain/world/pin.py`` has the check.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from ...domain.world import pin
from .guard import READS

__all__ = ["STALE", "pinning"]

STALE = "the save changed since this page read it; refresh to read the new one"


async def pinning(request: Request, call_next: RequestResponseEndpoint) -> Response:
    params = request.query_params
    as_of = params.get("as_of")
    if not as_of or request.method not in READS or not request.url.path.startswith("/api/"):
        return await call_next(request)
    try:
        st = await run_in_threadpool(
            request.app.state.load_state, params.get("save"), params.get("world")
        )
    except Exception:
        return await call_next(request)
    try:
        pin.check(st.header, as_of)
    except pin.PinRefused as exc:
        return JSONResponse({"error": STALE, "stale": True, "pin": str(exc)}, status_code=409)
    return await call_next(request)

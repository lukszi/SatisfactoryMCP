"""``/api/events``: the server-sent event stream, and the only route that is not JSON.

The one endpoint that holds a connection open, and the one with no ``?save=``/``?world=``:
it never reads a world, only that world's header. It subscribes to the ``SaveWatcher`` the
app's lifespan starts, through ``request.app.state`` rather than through ``Depends`` -- a
dependency would put a parameter into ``/openapi.json`` for a stream that has no schema.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ....core.saveio import projection as proj
from ....domain.world import pin
from ..watch import KIND_SAVE, KINDS, WatchEvent

__all__ = ["PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment line. Proxies and browsers
#: both drop a connection that has said nothing for a while.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")

#: The last ``save`` event's token, memoised on the event that produced it. The stream
#: replays the newest event of each kind to every new subscriber, so without this each
#: reconnecting browser costs a fresh header scan for a write they all share.
_MEMO: tuple[tuple[str, float] | None, str | None] = (None, None)


# --------------------------------------------------------------------- events


def _sse(event: str | None, data: str) -> bytes:
    if event is None:
        return f": {data}\n\n".encode()
    return f"event: {event}\ndata: {data}\n\n".encode()


async def _payload(event: WatchEvent) -> str:
    """One event as its data line: what moved, and -- for a save -- which world state it is.

    The token is identity rather than payload, so ``events``'s rule below still holds: the
    event says only that a write happened, and the page still decides what to refetch. What
    it buys is that the page and an assistant holding a ``sav:`` pin can name the same world
    state, which a rotating filename cannot do.

    Best-effort and off the event loop, because naming the state costs a header read in a
    child process (~90 ms): a stream that cannot name it sends ``null`` and goes on saying
    that a write happened, which is the half a browser acts on.
    """
    global _MEMO
    body = event.as_dict()
    if event.kind != KIND_SAVE:
        return json.dumps(body)
    key = (event.filename, event.mtime)
    if _MEMO[0] != key:
        try:
            _MEMO = (key, pin.remember(await asyncio.to_thread(proj.resolve_save, event.filename)))
        except Exception:
            _MEMO = (key, None)
    return json.dumps({**body, "save_token": _MEMO[1]})


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events: one event per observed write, plus keepalives.

    ``save`` is the game writing a ``.sav``; ``notes`` is a factory label (or the legacy
    plan file); ``plans`` is new commits in one plan's log; ``activity`` is one journal
    entry. A browser listens for the ones it can act on.

    ``save`` and ``notes`` carry the trigger, never the payload: the page decides what to
    refetch. ``plans`` and ``activity`` carry the commit summary or the entry itself, as
    docs/web-wire.md lists.
    """
    watcher = request.app.state.watcher
    queue = watcher.subscribe()

    async def stream():
        try:
            # The replay, in a fixed order rather than in whatever order the trees were
            # last scanned: a browser reading two events at once should read them the same
            # way every time it connects.
            for kind in KINDS:
                held = watcher.latest.get(kind)
                if held is not None:
                    yield _sse(kind, await _payload(held))
            while True:
                if watcher.cut(queue) and queue.empty():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield _sse(None, "ping")
                    continue
                yield _sse(event.kind, await _payload(event))
        finally:
            watcher.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

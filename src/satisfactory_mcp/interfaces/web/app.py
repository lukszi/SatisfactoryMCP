"""The FastAPI application, and the two loaders every endpoint reads the world through.

``create_app`` takes them as arguments so the test suite can run the whole HTTP surface
against the committed fixture projection with no game install and no ``.sav`` on disk. The
default game loader is a copy of ``interfaces.mcp.app``'s rather than an import of it: the
two surfaces are siblings over one domain, and importing would make the stdio server a
dependency of the HTTP server.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ... import config
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...core.schema import NewerSchema
from ...domain.planning import journal
from ...domain.world.state import WorldState, load_state
from .guard import guard
from .routers import ALL_ROUTERS
from .watch import SaveWatcher

__all__ = ["STATIC_DIR", "app", "create_app"]

STATIC_DIR = Path(__file__).parent / "static"

#: What ``/`` says when the frontend has not been built, which is the state of a fresh
#: clone: ``static/`` is generated output and is not committed. This string is the only
#: mention of ``frontend/`` Python is allowed -- see
#: ``test_the_frontend_sources_are_not_reachable_from_python``.
_NOT_BUILT = (
    "frontend not built: run `npm ci && npm run build` in "
    "src/satisfactory_mcp/interfaces/web/frontend/ and reload.\n"
    "\n"
    "The JSON API is up regardless -- see /docs.\n"
)


def _newer(request: Request, exc: NewerSchema) -> JSONResponse:
    """A 503 naming what cannot be read, never the file's path: nothing is written."""
    what = "the factory names" if exc.path.parent == config.labels_dir() else "the plans"
    text = (
        f"{what} were saved by a newer version of satisfactory-mcp (schema {exc.found}; this "
        f"one reads up to {exc.known}). Upgrade to read them; nothing was changed"
    )
    return JSONResponse({"error": text, "newer_schema": True}, status_code=503)


@lru_cache(maxsize=1)
def _game() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def create_app(
    state_loader: Callable[..., WorldState] | None = None,
    game_loader: Callable[[], GameData] | None = None,
    prewarm: bool = False,
    tail: bool = False,
) -> FastAPI:
    """Build the ASGI app.

    ``state_loader(save, world)`` returns the world a request asked for; ``game_loader()``
    returns the normalized docs. Both default to the real thing and are replaced wholesale
    in tests, never half-injected. ``prewarm`` and ``tail`` are off by default because only
    the served instance below is pointed at a real save and plan directory -- see
    ``SaveWatcher``. ``tail`` also names this process the ``web`` journal writer, which is
    process-wide and so must not leak out of a test into the tool tests that follow it.
    """
    load_game = game_loader or _game
    load = state_loader or (lambda save=None, world=None: load_state(load_game(), save, world))

    @asynccontextmanager
    async def lifespan(instance: FastAPI):
        if tail:
            journal.set_writer("web")
        await instance.state.watcher.start()
        try:
            yield
        finally:
            await instance.state.watcher.stop()

    instance = FastAPI(
        title="Satisfactory MCP web",
        summary="A JSON and map view of the same world the MCP tools plan against.",
        lifespan=lifespan,
    )
    instance.state.load_state = load
    instance.state.game = load_game
    instance.state.watcher = SaveWatcher(prewarm=prewarm, tail=tail)
    instance.middleware("http")(guard)
    instance.add_exception_handler(NewerSchema, _newer)
    # The whole JSON surface, in one loop over one tuple: there is no second include, so
    # ``ALL_ROUTERS`` alone decides registration order. See its declaration.
    for extracted in ALL_ROUTERS:
        instance.include_router(extracted)

    # Mounted at the root and therefore LAST: a mount at "/" swallows every path that did
    # not already match, so the API routers have to be registered above it. The gate is on
    # index.html rather than on the directory, because a directory left half-written by an
    # interrupted build is the same situation as no directory at all; either way ``/``
    # answers 503 -- the page is a capability this process has not got, not a missing path.
    if (STATIC_DIR / "index.html").is_file():
        instance.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    else:

        @instance.get("/", include_in_schema=False)
        def frontend_not_built() -> PlainTextResponse:
            return PlainTextResponse(_NOT_BUILT, status_code=503)

    return instance


#: The instance ``uvicorn`` is pointed at. Built on import; nothing here reads a save until
#: a request arrives or the watcher sees the game write one.
app = create_app(prewarm=True, tail=True)

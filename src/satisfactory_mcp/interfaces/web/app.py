"""The FastAPI application, and the two loaders every endpoint reads the world through.

``create_app`` takes them as arguments so the test suite can run the whole HTTP surface
against the committed fixture projection with no game install and no ``.sav`` on disk. The
default game loader is a copy of ``interfaces.mcp.app``'s rather than an import of it: the
two surfaces are siblings over one domain, and importing would make the stdio server a
dependency of the HTTP server.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from ... import config
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...core.gpu import vulkan_available
from ...core.schema import NewerSchema
from ...domain.maps import registry
from ...domain.planning import siting
from ...domain.planning.stored import planlog
from ...domain.session import journal
from ...domain.world.state import WorldState, load_state
from . import terrain
from .guard import guard
from .mapjobs import MapJobRunner
from .pinning import pinning
from .routers import ALL_ROUTERS
from .routers.assets.maps import newer_map_list
from .serial import RequestRefused, newer_schema_response
from .watch.watcher import SaveWatcher

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


def _newer_schema(request: Request, exc: NewerSchema) -> JSONResponse:
    """A 503 naming what cannot be read, never the file's path: nothing is written."""
    if exc.path == registry.manifest_path():
        return newer_map_list(exc)
    if exc.path.parent == config.labels_dir():
        return newer_schema_response(exc, "the factory names")
    if exc.path.is_relative_to(config.plans_dir()):
        return newer_schema_response(exc, "the plans")
    return newer_schema_response(
        exc, "a data file", verb="was", ending="Upgrade to read it; nothing changed"
    )


async def _request_refused(request: Request, exc: RequestRefused) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


async def _api_http_error(request: Request, exc: HTTPException) -> Response:
    """The API's ``{"error"}`` body for a path or method no route serves; the page keeps its own."""
    if not request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    text = "nothing here" if exc.status_code == 404 else str(exc.detail).lower()
    return JSONResponse({"error": text}, status_code=exc.status_code, headers=exc.headers)


@lru_cache(maxsize=1)
def _load_game_data() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def _recipe_names(load_game: Callable[[], GameData]) -> Callable[[], dict[str, str]]:
    @lru_cache(maxsize=1)
    def names() -> dict[str, str]:
        return {cls: recipe.name for cls, recipe in load_game().recipes.items()}

    return names


def create_app(
    state_loader: Callable[..., WorldState] | None = None,
    game_loader: Callable[[], GameData] | None = None,
    prewarm: bool = False,
    served: bool = False,
) -> FastAPI:
    """Build the ASGI app.

    ``state_loader(save, world)`` returns the world a request asked for; ``game_loader()``
    returns the normalized docs. Both default to the real thing and are replaced wholesale
    in tests, never half-injected. ``prewarm`` and ``served`` are off by default because only
    the served instance below is pointed at a real save and plan directory -- see
    ``SaveWatcher``. ``served`` also names this process the ``web`` journal writer, which is
    process-wide and so must not leak out of a test into the tool tests that follow it.
    """
    load_game = game_loader or _load_game_data

    def load_saved_state(save: str | None = None, world: str | None = None) -> WorldState:
        return load_state(load_game(), save, world)

    load_world_state = state_loader or load_saved_state

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncGenerator[None, None]:
        if served:
            journal.set_writer("web")
            planlog.use_recipe_names(_recipe_names(load_game))
            siting.set_ground_z(siting.terrain_provider(terrain.field))
            # Once per process, off the event loop: the Maps tab offers the upscaler by it.
            asyncio.get_running_loop().run_in_executor(None, vulkan_available)
        await instance.state.watcher.start()
        await instance.state.mapjobs.start()
        try:
            yield
        finally:
            await instance.state.mapjobs.stop()
            await instance.state.watcher.stop()
            if served:
                planlog.use_recipe_names(None)
                siting.set_ground_z(None)

    instance = FastAPI(
        title="Satisfactory MCP web",
        summary="A JSON and map view of the same world the MCP tools plan against.",
        lifespan=lifespan,
    )
    instance.state.load_state = load_world_state
    instance.state.game = load_game
    instance.state.watcher = SaveWatcher(prewarm=prewarm, tail=served)
    # Only the served instance reads and re-adopts the jobs on disk; test apps start empty.
    instance.state.mapjobs = MapJobRunner(instance.state.watcher, recover=served)
    instance.middleware("http")(pinning)
    instance.middleware("http")(guard)
    instance.exception_handler(NewerSchema)(_newer_schema)
    instance.exception_handler(RequestRefused)(_request_refused)
    instance.exception_handler(HTTPException)(_api_http_error)
    # The whole JSON surface, in one loop over one tuple: there is no second include, so
    # ``ALL_ROUTERS`` alone decides registration order. See its declaration.
    for router in ALL_ROUTERS:
        instance.include_router(router)

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
app = create_app(prewarm=True, served=True)

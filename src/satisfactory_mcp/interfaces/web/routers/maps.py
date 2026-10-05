"""``/api/maps``: the base-map types, the shared default, and the generation jobs that make them.

The registry is ``domain/maps/registry.py`` and the runner is ``app.state.mapjobs``, reached
through the request as ``events.py`` reaches the watcher. Every write passes the guard, and
every change reaches every page as the ``maps`` event. docs/maps_contract.md is the
specification.

WARNING: the function names are the operation_ids -- renaming one churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....domain.maps import axes as ax
from ....domain.maps import jobs as job_store
from ....domain.maps import presets, registry
from ..serial import _fail

__all__ = ["router"]

router = APIRouter(prefix="/api")

Status = Literal["building", "ready", "failed", "missing"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled", "interrupted"]
Preset = Literal["render", "artwork", "heightmap", "caves", "rocks", "paint"]
Layer = Literal["terrain", "satellite", "painted"]


class MapStaleAxis(TypedDict):
    axis: str
    text: str


class MapRerender(TypedDict):
    """A newer renderer this map could be drawn with; ``needs`` are inputs to rebuild first."""

    recipe: int
    label: str
    needs: list[str]
    text: str


class MapFreshness(TypedDict):
    """``stale`` is outdated DATA (amber); ``rerender`` and ``restyle`` are offers (neutral)."""

    stale: list[MapStaleAxis]
    rerender: MapRerender | None
    restyle: bool
    incomplete: bool


class MapTypeBody(TypedDict):
    """One map type. ``name`` is derived from its axes; ``label`` is the player's, or null."""

    id: str
    label: str | None
    name: str
    style: str
    renderer: str
    data: str
    kind: str
    layer: str
    size_px: int | None
    dir: str
    bytes: int
    max_z: int | None
    created: float | None
    status: Status
    origin: str
    in_switcher: bool
    default: bool
    replaces: str | None
    job: str | None
    freshness: MapFreshness
    axes: dict[str, Any]


class MapJobBody(TypedDict):
    """One generation job. ``pct`` is 0..1, null when the generator's lines say nothing of it."""

    id: str
    preset: str
    options: dict[str, Any]
    label: str | None
    produces: list[str]
    replaces: str | None
    status: JobStatus
    created: float
    started: float | None
    ended: float | None
    stage: str
    stage_words: str
    pct: float | None
    eta_s: int | None
    elapsed_s: float | None
    estimate_s: int | None
    exit_code: int | None
    peak_rss: int | None
    error_line: str | None
    last_line: str | None


class MapInputBody(TypedDict):
    """An input directory maps are drawn from; ``present`` false when it was never built."""

    name: str
    present: bool
    version: int | None
    cl: int | None
    transcribed: str | None


class MapCanGenerate(TypedDict):
    gen: bool
    tools: bool
    game: bool
    heightfield: bool
    ok: bool
    reason: str | None


class MapDisk(TypedDict):
    free_bytes: int
    maps_bytes: int
    cache_bytes: int


class MapsResponse(TypedDict):
    """Every type in display order, the jobs (running, queued, last ten), and what can run."""

    version: int
    default: str | None
    types: list[MapTypeBody]
    jobs: list[MapJobBody]
    can_generate: MapCanGenerate
    inputs: list[MapInputBody]
    disk: MapDisk
    game_cl: int | None
    unregistered: list[str]
    queue_max: int
    sizes: list[int]


class MapPatchBody(TypedDict):
    """``label`` "" clears it; ``version`` is the list version the page holds."""

    label: NotRequired[str | None]
    in_switcher: NotRequired[bool | None]
    version: NotRequired[int | None]


class MapDefaultBody(TypedDict):
    """A type id, or ``plain`` for no imagery."""

    id: str
    version: NotRequired[int | None]


class MapJobOptions(TypedDict):
    layers: NotRequired[list[Layer]]
    size: NotRequired[int]
    recipe: NotRequired[Literal["current", "kernel-only"]]
    top: NotRequired[bool]
    keep_cache: NotRequired[bool]
    enhance: NotRequired[bool]
    tiles_2x: NotRequired[bool]
    titan_trees: NotRequired[bool]


class MapJobRequest(TypedDict):
    preset: Preset
    options: NotRequired[MapJobOptions]
    label: NotRequired[str | None]
    replaces: NotRequired[str | None]


class MapJobResponse(TypedDict):
    job: MapJobBody


class MapJobDetailResponse(TypedDict):
    """The job and the last 200 lines of its log."""

    job: MapJobBody
    log_tail: list[str]


class MapEstimateResponse(TypedDict):
    """What a job would cost: wall time, disk kept, disk needed while it runs."""

    seconds: int
    keep_bytes: int
    transient_bytes: int
    free_bytes: int
    needs_bytes: int
    ok: bool
    reason: str | None
    measured: bool


class MapsStaleResponse(TypedDict):
    """The 409 of a write whose ``version`` is not the current one: nothing was written."""

    error: str
    stale: bool
    version: int


class MapCacheResponse(TypedDict):
    freed_bytes: int


def _type_json(row: dict, default: str | None) -> dict:
    entry, axes = row["entry"], row["axes"]
    return {
        "id": row["id"],
        "label": entry.get("label"),
        "name": row["name"],
        "style": ax.style_label(axes),
        "renderer": ax.renderer_label(axes),
        "data": ax.data_label(axes),
        "kind": entry.get("kind") or "render",
        "layer": entry.get("layer") or "",
        "size_px": (axes.get("renderer") or {}).get("size_px") or entry.get("size_px"),
        "dir": entry["dir"],
        "bytes": int(entry.get("bytes") or 0),
        "max_z": entry.get("max_z"),
        "created": entry.get("created"),
        "status": row["status"],
        "origin": entry.get("origin") or "adopted",
        "in_switcher": bool(entry.get("in_switcher", True)),
        "default": row["id"] == default,
        "replaces": entry.get("replaces"),
        "job": entry.get("job"),
        "freshness": row["freshness"],
        "axes": axes,
    }


def _maps_json(request: Request) -> dict:
    view = registry.view()
    runner = request.app.state.mapjobs
    local = registry.local_dir()
    try:
        free = shutil.disk_usage(local if local.exists() else local.parent).free
    except OSError:
        free = 0
    types = [_type_json(row, view["default"]) for row in view["types"]]
    inputs = [
        {
            "name": name,
            "present": now is not None,
            "version": (now or {}).get("version"),
            "cl": (now or {}).get("cl"),
            "transcribed": (now or {}).get("transcribed"),
        }
        for name, now in view["current"]["inputs"].items()
    ]
    return {
        "version": view["version"],
        "default": view["default"],
        "types": types,
        "jobs": runner.snapshot(),
        "can_generate": presets.can_generate(),
        "inputs": inputs,
        "disk": {
            "free_bytes": free,
            "maps_bytes": sum(t["bytes"] for t in types),
            "cache_bytes": registry.cache_bytes(),
        },
        "game_cl": view["current"]["game_cl"],
        "unregistered": registry.unregistered(),
        "queue_max": 4,
        "sizes": list(presets.RENDER_SIZES),
    }


def _refused(exc: Exception) -> JSONResponse:
    if isinstance(exc, registry.MapsStale):
        body = {"error": str(exc), "stale": True, "version": exc.current}
        return JSONResponse(body, status_code=409)
    if isinstance(exc, registry.MapsUnknown):
        return _fail(str(exc), 404)
    if isinstance(exc, registry.MapsRefused):
        return _fail(str(exc), 409)
    if isinstance(exc, NewerSchema):
        text = (
            f"the map list was saved by a newer version of satisfactory-mcp (schema "
            f"{exc.found}; this one reads up to {exc.known}). Upgrade to read it; nothing changed"
        )
        return JSONResponse({"error": text, "newer_schema": True}, status_code=503)
    if isinstance(exc, LockTimeout):
        return _fail(f"the map list is busy, nothing written: {exc}", 503)
    return _fail(str(exc), 400)


WRITE_REFUSALS = (registry.MapsError, NewerSchema, LockTimeout)


async def _after_write(request: Request) -> dict:
    request.app.state.mapjobs.announce()
    return await asyncio.to_thread(_maps_json, request)


@router.get("/maps", response_model=MapsResponse)
async def maps_index(request: Request) -> Any:
    """Every base-map type with its computed freshness, the jobs, and whether generation can run."""
    try:
        return await asyncio.to_thread(_maps_json, request)
    except NewerSchema as exc:
        return _refused(exc)


@router.get("/maps/estimate", response_model=MapEstimateResponse)
def map_estimate(
    preset: Preset,
    layers: str = "terrain,satellite",
    size: int = presets.FULL_PX,
    recipe: Literal["current", "kernel-only"] = "current",
    top: bool = True,
    keep_cache: bool = False,
    enhance: bool = False,
    tiles_2x: bool = True,
) -> Any:
    """What a job with these options would cost, and whether the disk has room for it now."""
    options = {
        "layers": [layer for layer in layers.split(",") if layer],
        "size": size,
        "recipe": recipe,
        "top": top,
        "keep_cache": keep_cache,
        "enhance": enhance,
        "tiles_2x": tiles_2x,
    }
    if preset != "render":
        options = {k: options[k] for k in ("enhance", "tiles_2x")} if preset == "artwork" else {}
    try:
        return presets.estimate(preset, options)
    except presets.PresetError as exc:
        return _fail(str(exc), 400)


@router.put(
    "/maps/default",
    response_model=MapsResponse,
    responses={409: {"model": MapsStaleResponse}},
)
async def default_map(request: Request, body: Annotated[MapDefaultBody, Body()]) -> Any:
    """Set the type every fresh page opens on, for every browser on this machine."""
    try:
        await asyncio.to_thread(registry.set_default, body["id"], body.get("version"))
    except WRITE_REFUSALS as exc:
        return _refused(exc)
    return await _after_write(request)


@router.post("/maps/adopt", response_model=MapsResponse)
async def adopt_maps(request: Request) -> Any:
    """Register pyramids lying under ``data/local`` that the list does not know, where they lie."""
    try:
        await asyncio.to_thread(registry.adopt_existing)
    except WRITE_REFUSALS as exc:
        return _refused(exc)
    return await _after_write(request)


@router.delete("/maps/cache", response_model=MapCacheResponse)
async def clear_map_cache(request: Request) -> Any:
    """Delete the rasters kept for fast re-renders; refused while a job is running."""
    if request.app.state.mapjobs.run is not None:
        return _fail("a job is running and may be reading the cache; try once it ends", 409)
    freed = await asyncio.to_thread(registry.clear_cache)
    request.app.state.mapjobs.announce()
    return {"freed_bytes": freed}


@router.post(
    "/maps/jobs",
    status_code=202,
    response_model=MapJobResponse,
    responses={409: {"model": MapsStaleResponse}},
)
async def start_map_job(request: Request, body: Annotated[MapJobRequest, Body()]) -> Any:
    """Queue a generation job. 409 when four are queued already, 507 when the disk is short."""
    runner = request.app.state.mapjobs
    try:
        job = runner.submit(
            body["preset"], dict(body.get("options") or {}), body.get("label"), body.get("replaces")
        )
    except presets.QueueFull as exc:
        return _fail(str(exc), 409)
    except presets.DiskShort as exc:
        return _fail(str(exc), 507)
    except presets.PresetError as exc:
        return _fail(str(exc), 400)
    except WRITE_REFUSALS as exc:
        return _refused(exc)
    runner.announce(job)
    return JSONResponse({"job": runner.view(job)}, status_code=202)


@router.get("/maps/jobs/{job}", response_model=MapJobDetailResponse)
async def map_job(request: Request, job: str) -> Any:
    """One job and the end of its log."""
    found = request.app.state.mapjobs.jobs.get(job)
    if found is None:
        return _fail(f"no map job “{job}”", 404)
    tail = await asyncio.to_thread(job_store.log_tail, job)
    return {"job": request.app.state.mapjobs.view(found), "log_tail": tail}


@router.delete("/maps/jobs/{job}", response_model=MapJobResponse)
async def cancel_map_job(request: Request, job: str) -> Any:
    """Cancel a running job, its partial output going to the trash, or take a queued one off."""
    runner = request.app.state.mapjobs
    try:
        found = await runner.cancel(job)
    except KeyError:
        return _fail(f"no map job “{job}”", 404)
    return {"job": runner.view(found)}


@router.patch(
    "/maps/{ident}",
    response_model=MapsResponse,
    responses={409: {"model": MapsStaleResponse}},
)
async def change_map(request: Request, ident: str, body: Annotated[MapPatchBody, Body()]) -> Any:
    """Rename a type or show or hide it in the map's switcher."""
    try:
        await asyncio.to_thread(
            registry.update, ident, body.get("label"), body.get("in_switcher"), body.get("version")
        )
    except WRITE_REFUSALS as exc:
        return _refused(exc)
    return await _after_write(request)


@router.delete(
    "/maps/{ident}",
    response_model=MapsResponse,
    responses={409: {"model": MapsStaleResponse}},
)
async def delete_map(request: Request, ident: str, version: int | None = None) -> Any:
    """Move a type's files to the trash and forget it. Refuses the default (409)."""
    busy = request.app.state.mapjobs.busy_ids()
    try:
        await asyncio.to_thread(registry.delete, ident, version, busy)
    except WRITE_REFUSALS as exc:
        return _refused(exc)
    asyncio.get_running_loop().run_in_executor(None, registry.purge_trash)
    return await _after_write(request)

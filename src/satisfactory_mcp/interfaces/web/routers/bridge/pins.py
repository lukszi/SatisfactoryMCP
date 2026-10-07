"""``/api/pins``: numbered handles on plans, processes, machines, factories, fields, nodes, points.

The store is ``domain/session/pins.py``; every write passes the guard, carries the ``rev`` it
read, and appends one journal entry. docs/planner-p3_contract.md §4, §5 and §8 are the
specification.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, NotRequired

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.filelock import LockTimeout
from .....core.schema import NewerSchema
from .....domain.session import journal
from .....domain.session import pins as pin_store
from .....domain.session.views import PinRecord, PinRef, PinRow
from .....domain.world.state import WorldState
from ...serial import (
    Dropped,
    RevBody,
    busy_response,
    error_response,
    newer_schema_response,
    page_actor,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class PinsResponse(TypedDict):
    version: int
    pins: list[PinRow]


class PinCreated(PinRow):
    existing: bool


class PinCreateBody(TypedDict):
    kind: str
    ref: PinRef
    label: NotRequired[str]


class PinRenameBody(TypedDict):
    rev: int
    label: str


class PinStaleResponse(TypedDict):
    """The 409 of a pin write: nothing was written; ``pin`` is the row as it stands."""

    error: str
    stale: bool
    pin: PinRow


STORE_NAME = "the pins"


def _refused(st: WorldState, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return newer_schema_response(exc, STORE_NAME)
    if isinstance(exc, LockTimeout):
        return busy_response("pins", exc)
    if isinstance(exc, pin_store.PinStale):
        body: PinStaleResponse = {
            "error": str(exc),
            "stale": True,
            "pin": pin_store.row(st, exc.pin),
        }
        return JSONResponse(body, status_code=409)
    if isinstance(exc, pin_store.PinMissing | pin_store.ObjectMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (pin_store.PinError, LockTimeout, NewerSchema)


def _plan_of(pin: PinRow | PinRecord) -> str | None:
    return (pin.get("ref") or {}).get("plan") if pin["kind"] in ("plan", "process") else None


def _journal(
    st: WorldState, kind: str, pin: PinRow | PinRecord, args: Mapping[str, object], text: str
) -> None:
    journal.append(st.world_id, kind, actor=page_actor(), plan=_plan_of(pin), args=args, text=text)


@router.get("/pins", response_model=PinsResponse)
def pins(
    request: Request, save: str | None = None, world: str | None = None
) -> PinsResponse | JSONResponse:
    """Every live pin of this world, ascending by number, gone ones included and marked."""
    st = require_world(request, save, world)
    try:
        version = pin_store.read(st.world_id)["version"]
        rows = pin_store.live(st)
    except NewerSchema as exc:
        return newer_schema_response(exc, STORE_NAME)
    return {"version": version, "pins": rows}


@router.post(
    "/pins",
    status_code=201,
    response_model=PinCreated,
    responses={200: {"model": PinCreated}},
)
def create_pin(
    request: Request,
    body: Annotated[PinCreateBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> PinCreated | JSONResponse:
    """Pin an object; pinning one that already has a live pin returns that pin with a 200."""
    st = require_world(request, save, world)
    try:
        pin, existing = pin_store.create(
            st, body["kind"], dict(body["ref"]), body.get("label") or ""
        )
    except _ERRORS as exc:
        return _refused(st, exc)
    if existing:
        found: PinCreated = {**pin, "existing": True}
        return JSONResponse(found, status_code=200)
    args = {"n": pin["n"], "kind": pin["kind"]}
    _journal(st, "pin.add", pin, args, f"pinned {pin['id']} {pin['text']}")
    return {**pin, "existing": False}


@router.patch(
    "/pins/{n}",
    response_model=PinRow,
    responses={409: {"model": PinStaleResponse}},
)
def rename_pin(
    request: Request,
    n: int,
    body: Annotated[PinRenameBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> PinRow | JSONResponse:
    """A new label for one pin, refused with a 409 when ``rev`` is not the pin's current one."""
    st = require_world(request, save, world)
    try:
        stored = pin_store.rename(st.world_id, n, body["rev"], body["label"])
    except _ERRORS as exc:
        return _refused(st, exc)
    shown = pin_store.row(st, stored)
    args = {"n": n, "label": shown["label"]}
    _journal(st, "pin.edit", stored, args, f"renamed pin:{n} “{shown['label']}”")
    return shown


@router.delete(
    "/pins/{n}",
    response_model=Dropped,
    responses={409: {"model": PinStaleResponse}},
)
def drop_pin(
    request: Request,
    n: int,
    body: Annotated[RevBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Dropped | JSONResponse:
    """Delete one pin. Not undoable, and its number is never given out again."""
    st = require_world(request, save, world)
    try:
        stored = pin_store.drop(st.world_id, n, body["rev"])
    except _ERRORS as exc:
        return _refused(st, exc)
    _journal(st, "pin.drop", stored, {"n": n}, f"deleted pin:{n}")
    return {"ok": True, "n": n}

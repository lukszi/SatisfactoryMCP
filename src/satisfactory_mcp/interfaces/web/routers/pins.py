"""``/api/pins``: numbered handles on plans, processes, machines, factories, fields, nodes, points.

The store is ``domain/planning/pins.py``; every write passes the guard, carries the ``rev`` it
read, and appends one journal entry. docs/planner-p3_contract.md §4, §5 and §8 are the
specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....domain.planning import journal
from ....domain.planning import pins as pin_store
from ....domain.planning.planlog import Actor
from ..serial import error_response, world_state

__all__ = ["router"]

router = APIRouter(prefix="/api")


class PinRef(TypedDict, total=False):
    """What a pin points at. ``resource`` and ``nodes`` are filled by the server for a field."""

    plan: str
    recipe: str
    factory: str
    machine: str
    node: str
    x_m: float
    y_m: float
    resource: str
    nodes: list[str]


class PinRow(TypedDict):
    """One pin as the page shows it. ``selector`` is the canonical text it stands for; a gone
    pin says why in ``gone_why``; ``x_m``/``y_m`` are null for a pin with no place."""

    n: int
    id: str
    kind: str
    ref: PinRef
    label: str
    text: str
    selector: str
    x_m: float | None
    y_m: float | None
    rev: int
    created: float
    gone: bool
    gone_why: str


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


class PinDropBody(TypedDict):
    rev: int


class PinDropped(TypedDict):
    ok: bool
    n: int


class PinStaleResponse(TypedDict):
    """The 409 of a pin write: nothing was written; ``pin`` is the row as it stands."""

    error: str
    stale: bool
    pin: PinRow


def _page() -> Actor:
    return Actor("page", "", os.getpid())


def _newer(exc: NewerSchema) -> JSONResponse:
    text = (
        f"the pins were saved by a newer version of satisfactory-mcp (schema {exc.found}; this "
        f"one reads up to {exc.known}). Upgrade to read them; nothing was changed"
    )
    return JSONResponse({"error": text, "newer_schema": True}, status_code=503)


def _refused(st, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return _newer(exc)
    if isinstance(exc, LockTimeout):
        return error_response(f"pins are busy, nothing written: {exc}", 503)
    if isinstance(exc, pin_store.PinStale):
        body = {"error": str(exc), "stale": True, "pin": pin_store.row(st, exc.pin)}
        return JSONResponse(body, status_code=409)
    if isinstance(exc, pin_store.PinMissing | pin_store.ObjectMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (pin_store.PinError, LockTimeout, NewerSchema)


def _plan_of(pin: dict) -> str | None:
    return (pin.get("ref") or {}).get("plan") if pin["kind"] in ("plan", "process") else None


def _note(st, kind: str, pin: dict, args: dict, text: str) -> None:
    journal.append(st.world_id, kind, actor=_page(), plan=_plan_of(pin), args=args, text=text)


@router.get("/pins", response_model=PinsResponse)
def pins(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every live pin of this world, ascending by number, gone ones included and marked."""
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)
    try:
        version = pin_store.read(st.world_id)["version"]
        rows = pin_store.live(st)
    except NewerSchema as exc:
        return _newer(exc)
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
) -> Any:
    """Pin an object; pinning one that already has a live pin returns that pin with a 200."""
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)
    try:
        pin, existing = pin_store.create(
            st, body["kind"], dict(body["ref"]), body.get("label") or ""
        )
    except _ERRORS as exc:
        return _refused(st, exc)
    if existing:
        return JSONResponse({**pin, "existing": True}, status_code=200)
    args = {"n": pin["n"], "kind": pin["kind"]}
    _note(st, "pin.add", pin, args, f"pinned {pin['id']} {pin['text']}")
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
) -> Any:
    """A new label for one pin, refused with a 409 when ``rev`` is not the pin's current one."""
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)
    try:
        stored = pin_store.rename(st.world_id, n, body["rev"], body["label"])
    except _ERRORS as exc:
        return _refused(st, exc)
    shown = pin_store.row(st, stored)
    args = {"n": n, "label": shown["label"]}
    _note(st, "pin.edit", stored, args, f"renamed pin:{n} “{shown['label']}”")
    return shown


@router.delete(
    "/pins/{n}",
    response_model=PinDropped,
    responses={409: {"model": PinStaleResponse}},
)
def drop_pin(
    request: Request,
    n: int,
    body: Annotated[PinDropBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Delete one pin. Not undoable, and its number is never given out again."""
    try:
        st = world_state(request, save, world)
    except Exception as exc:
        return error_response(f"could not read save: {exc}", 404)
    try:
        stored = pin_store.drop(st.world_id, n, body["rev"])
    except _ERRORS as exc:
        return _refused(st, exc)
    _note(st, "pin.drop", stored, {"n": n}, f"deleted pin:{n}")
    return {"ok": True, "n": n}

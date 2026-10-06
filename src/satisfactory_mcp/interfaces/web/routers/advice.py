"""``/api/advice``: what is worth a look in this save, and what the player hid of it.

The rows are ``domain/advice``; every write passes the guard, carries the ``rev`` it read and
appends one journal entry. docs/advisors_contract.md §5 is the specification.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....domain import advice
from ....domain.advice import store as hidden_store
from ....domain.planning import journal
from ....domain.planning.planlog import Actor
from ....domain.world import pin
from ..serial import error_response, require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")


class AdviceMachine(TypedDict):
    """A place the row names: a machine leaf, or "" for a pickup; metres on save axes."""

    instance: str
    name: str
    x_m: float | None
    y_m: float | None


class AdviceRow(TypedDict):
    """One advisory. ``tone`` is blocked, mid or muted; ``state`` is active, dismissed or
    snoozed; ``back`` is true when it resurfaced because it got worse; ``rev`` is its hidden
    entry's, 0 when never hidden. ``reveal`` names the map layers its map action shows."""

    id: str
    key: str
    kind: str
    severity: str
    tone: str
    subject_kind: str
    subject: str
    text: str
    lines: list[str]
    weight: float
    machines: list[AdviceMachine]
    bbox_m: list[float] | None
    seed: str | None
    plan: str | None
    reveal: list[str]
    next_call: str
    state: str
    back: bool
    rev: int
    until_play_s: float | None
    by: str


class AdviceResponse(TypedDict):
    """``active`` is every row not hidden, ranked; the page applies the visible caps."""

    save_token: str
    play_s: float
    version: int
    active: list[AdviceRow]
    hidden: list[AdviceRow]


class AdviceHideBody(TypedDict):
    """``hours`` is play time, 0.5 to 24, for a snooze; ``rev`` 0 means "not hidden yet"."""

    key: str
    mode: Literal["dismiss", "snooze"]
    hours: NotRequired[float | None]
    rev: NotRequired[int | None]


class AdviceRestoreBody(TypedDict):
    rev: int


class AdviceRestored(TypedDict):
    ok: bool
    id: str


class AdviceStaleResponse(TypedDict):
    """The 409 of a hide or restore: nothing was written; ``row`` is the row as it stands,
    null when it no longer fires."""

    error: str
    stale: bool
    row: AdviceRow | None


def _row(adv, state: str, back: bool, rev: int, entry: dict | None) -> AdviceRow:
    by = (entry or {}).get("by") or {}
    return {
        "id": adv.id,
        "key": adv.key,
        "kind": adv.kind,
        "severity": adv.severity,
        "tone": adv.tone,
        "subject_kind": adv.subject_kind,
        "subject": adv.subject,
        "text": adv.text,
        "lines": list(adv.lines),
        "weight": adv.weight,
        "machines": [
            {"instance": s.instance, "name": s.name, "x_m": s.x_m, "y_m": s.y_m} for s in adv.spots
        ],
        "bbox_m": list(adv.bbox_m) if adv.bbox_m else None,
        "seed": adv.seed,
        "plan": adv.plan,
        "reveal": list(adv.reveal),
        "next_call": adv.next_call,
        "state": state,
        "back": back,
        "rev": rev,
        "until_play_s": (entry or {}).get("until_play_s") if state == "snoozed" else None,
        "by": str(by.get("kind") or "") if isinstance(by, dict) else "",
    }


def _rows(cur: advice.Current) -> tuple[list[AdviceRow], list[AdviceRow]]:
    active = [_row(a, "active", back, rev, None) for a, back, rev in cur.active]
    hidden = [
        _row(a, str(e.get("state") or "dismissed"), False, int(e.get("rev") or 0), e)
        for a, e in cur.hidden
    ]
    return active, hidden


def _one(cur: advice.Current, key: str) -> AdviceRow | None:
    active, hidden = _rows(cur)
    return next((r for r in active + hidden if r["key"] == key), None)


def _newer(exc: NewerSchema) -> JSONResponse:
    text = (
        f"the hidden advisories were saved by a newer version of satisfactory-mcp (schema "
        f"{exc.found}; this one reads up to {exc.known}). Upgrade to read them; nothing was changed"
    )
    return JSONResponse({"error": text, "newer_schema": True}, status_code=503)


def _refused(st, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return _newer(exc)
    if isinstance(exc, LockTimeout):
        return error_response(f"advisories are busy, nothing written: {exc}", 503)
    if isinstance(exc, hidden_store.AdviceStale):
        row = _one(advice.current(st), exc.key)
        return JSONResponse({"error": str(exc), "stale": True, "row": row}, status_code=409)
    if isinstance(exc, hidden_store.AdviceMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (hidden_store.AdviceError, LockTimeout, NewerSchema)


def _page() -> Actor:
    return Actor("page", "", os.getpid())


def _subject(adv) -> str:
    return f"“{adv.subject}”" if adv.subject_kind in ("factory", "plan") else adv.subject


def _hours(hours: float) -> str:
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:g} h"


@router.get("/advice", response_model=AdviceResponse)
def advice_list(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    biomass: Literal["include", "exclude"] | None = None,
    spoilers: int = 0,
) -> Any:
    """Every advisory firing on this save, active and hidden. ``biomass`` overrides the
    shared setting for this read; ``spoilers=1`` counts pickups not found yet."""
    st = require_world(request, save, world)
    wants = None if biomass is None else biomass == "include"
    try:
        cur = advice.current(st, biomass=wants, spoilers=bool(spoilers))
    except NewerSchema as exc:
        return _newer(exc)
    active, hidden = _rows(cur)
    return {
        "save_token": pin.remember(st.header),
        "play_s": cur.play_s,
        "version": cur.version,
        "active": active,
        "hidden": hidden,
    }


@router.post(
    "/advice/hidden",
    response_model=AdviceRow,
    responses={409: {"model": AdviceStaleResponse}},
)
def hide_advice(
    request: Request,
    body: Annotated[AdviceHideBody, Body()],
    save: str | None = None,
    world: str | None = None,
    spoilers: int = 0,
) -> Any:
    """Dismiss an advisory until it gets worse, or snooze it for hours of play time."""
    st = require_world(request, save, world)
    try:
        cur = advice.current(st, spoilers=bool(spoilers))
        adv = next((a for a in cur.items if a.key == body["key"]), None)
        if adv is None:
            return error_response("that advisory no longer fires on this save", 404)
        mode, hours = body["mode"], body.get("hours")
        hidden_store.hide(
            st.world_id,
            adv,
            mode,
            play_s=cur.play_s,
            by=_page().to_dict(),
            hours=hours,
            rev=body.get("rev"),
            firing=[a.key for a in cur.items],
        )
    except _ERRORS as exc:
        return _refused(st, exc)
    what = "dismissed" if mode == "dismiss" else "snoozed"
    text = f"{what} {adv.id} {adv.kind.replace('_', ' ')} {_subject(adv)}"
    if mode == "snooze":
        text += f" for {_hours(float(hours))} of play"
    args = {"id": adv.id, "key": adv.key, "mode": mode}
    journal.append(st.world_id, "advice.hide", actor=_page(), args=args, text=text)
    return _one(advice.current(st, spoilers=bool(spoilers)), adv.key)


@router.delete(
    "/advice/hidden/{adv_id}",
    response_model=AdviceRestored,
    responses={409: {"model": AdviceStaleResponse}},
)
def restore_advice(
    request: Request,
    adv_id: str,
    body: Annotated[AdviceRestoreBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Show a hidden advisory again; a ``rev`` that is not its current one is a 409."""
    st = require_world(request, save, world)
    try:
        cur = advice.current(st)
        adv = cur.find(adv_id)
        if adv is None:
            return error_response(f"{adv_id} is not an advisory on this save", 404)
        hidden_store.restore(st.world_id, adv.key, body["rev"])
    except _ERRORS as exc:
        return _refused(st, exc)
    args = {"id": adv.id, "key": adv.key}
    journal.append(
        st.world_id, "advice.restore", actor=_page(), args=args, text=f"restored {adv.id}"
    )
    return {"ok": True, "id": adv.id}

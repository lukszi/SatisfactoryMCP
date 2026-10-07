"""``/api/advice``: what is worth a look in this save, and what the player hid of it.

The rows are ``domain/advice``; every write passes the guard, carries the ``rev`` it read and
appends one journal entry. docs/advisors_contract.md §5 is the specification.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Annotated, Literal, NotRequired, cast

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from typing_extensions import TypedDict

from .....core.filelock import LockTimeout
from .....core.schema import NewerSchema
from .....domain import advice
from .....domain.advice import store as hidden_store
from .....domain.advice.advisory import Advisory
from .....domain.advice.views import HiddenEntry
from .....domain.session import journal
from .....domain.world import pin
from .....domain.world.state import WorldState
from ...serial import (
    RevBody,
    busy_response,
    error_response,
    newer_schema_response,
    page_actor,
    require_world,
)

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


class AdviceRestored(TypedDict):
    ok: bool
    id: str


class AdviceStaleResponse(TypedDict):
    """The 409 of a hide or restore: nothing was written; ``row`` is the row as it stands,
    null when it no longer fires."""

    error: str
    stale: bool
    row: AdviceRow | None


def _actor_kind(by: object) -> str:
    """The ``kind`` of the actor a hidden entry names, "" where the file says none."""
    if not isinstance(by, dict):
        return ""
    return str(cast("dict[str, object]", by).get("kind") or "")


def _advice_row(
    adv: Advisory, state: str, back: bool, rev: int, entry: HiddenEntry | None
) -> AdviceRow:
    by: object = (entry.get("by") if entry is not None else None) or {}
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
        "until_play_s": (
            entry.get("until_play_s") if entry is not None and state == "snoozed" else None
        ),
        "by": _actor_kind(by),
    }


def _advice_rows(cur: advice.Current) -> tuple[list[AdviceRow], list[AdviceRow]]:
    active = [_advice_row(a, "active", back, rev, None) for a, back, rev in cur.active]
    hidden = [
        _advice_row(a, str(e.get("state") or "dismissed"), False, int(e.get("rev") or 0), e)
        for a, e in cur.hidden
    ]
    return active, hidden


def _row_for_key(cur: advice.Current, key: str) -> AdviceRow | None:
    active, hidden = _advice_rows(cur)
    return next((r for r in active + hidden if r["key"] == key), None)


STORE_NAME = "the hidden advisories"


def _refused(st: WorldState, exc: Exception) -> JSONResponse:
    if isinstance(exc, NewerSchema):
        return newer_schema_response(exc, STORE_NAME)
    if isinstance(exc, LockTimeout):
        return busy_response("advisories", exc)
    if isinstance(exc, hidden_store.AdviceStale):
        row = _row_for_key(advice.current(st), exc.key)
        body: AdviceStaleResponse = {"error": str(exc), "stale": True, "row": row}
        return JSONResponse(body, status_code=409)
    if isinstance(exc, hidden_store.AdviceMissing):
        return error_response(str(exc), 404)
    return error_response(str(exc), 400)


_ERRORS = (hidden_store.AdviceError, LockTimeout, NewerSchema)


def _quoted_subject(adv: Advisory) -> str:
    return f"“{adv.subject}”" if adv.subject_kind in ("factory", "plan") else adv.subject


def _play_time_text(hours: float) -> str:
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:g} h"


@router.get("/advice", response_model=AdviceResponse)
def advice_list(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    biomass: Literal["include", "exclude"] | None = None,
    spoilers: int = 0,
) -> AdviceResponse | JSONResponse:
    """Every advisory firing on this save, active and hidden. ``biomass`` overrides the
    shared setting for this read; ``spoilers=1`` counts pickups not found yet."""
    st = require_world(request, save, world)
    wants = None if biomass is None else biomass == "include"
    try:
        cur = advice.current(st, biomass=wants, spoilers=bool(spoilers))
    except NewerSchema as exc:
        return newer_schema_response(exc, STORE_NAME)
    active, hidden = _advice_rows(cur)
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
) -> AdviceRow | JSONResponse | None:
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
            by=page_actor().to_dict(),
            hours=hours,
            rev=body.get("rev"),
            firing=[a.key for a in cur.items],
        )
    except _ERRORS as exc:
        return _refused(st, exc)
    what = "dismissed" if mode == "dismiss" else "snoozed"
    text = f"{what} {adv.id} {adv.kind.replace('_', ' ')} {_quoted_subject(adv)}"
    if mode == "snooze" and hours is not None:
        text += f" for {_play_time_text(float(hours))} of play"
    args = {"id": adv.id, "key": adv.key, "mode": mode}
    journal.append(st.world_id, "advice.hide", actor=page_actor(), args=args, text=text)
    return _row_for_key(advice.current(st, spoilers=bool(spoilers)), adv.key)


@router.delete(
    "/advice/hidden/{adv_id}",
    response_model=AdviceRestored,
    responses={409: {"model": AdviceStaleResponse}},
)
def restore_advice(
    request: Request,
    adv_id: str,
    body: Annotated[RevBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> AdviceRestored | JSONResponse:
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
        st.world_id, "advice.restore", actor=page_actor(), args=args, text=f"restored {adv.id}"
    )
    return {"ok": True, "id": adv.id}

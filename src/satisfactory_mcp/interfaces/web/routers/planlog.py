"""``/api/plans`` writes and ``/api/plans/{key}``: read a plan at a version, push edits to it.

Every write goes through ``domain/planning/planlog.py``, as the MCP tools do, past the write
guard (``guard.py``), and carries the ``base_rev`` it was read at. A conflict is a 409 whose
body says what changed since, so the page can put a chip on the control that collided.
docs/planner_slice_contract.md §11 is the specification; ``pin:`` members are rewritten to
what they stand for before the store (docs/planner-p3_contract.md §7.2).

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Annotated, Any, NotRequired, TypedDict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ....core.filelock import LockTimeout
from ....core.gamedata.model import GameData
from ....domain.planning import journal, manage, pins, summary, swaps
from ....domain.planning.planlog import (
    Actor,
    AlreadyUndone,
    Commit,
    Forgotten,
    InvalidOp,
    NameTaken,
    Outdated,
    PlanArgs,
    PlanLog,
    PlanLogError,
    PlanState,
    Pushed,
    UnknownPlan,
)
from ....domain.world import pin
from ..serial import ActorBody, PlanOpBody, _actor_json, _fail, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")

_logger = logging.getLogger(__name__)

_KEY = re.compile(r"[0-9a-f]{8}")


class CommitBody(TypedDict):
    """One version. ``text`` is ``describe_commit``: the page never words an op itself."""

    rev: int
    base_rev: int
    ts: float
    actor: ActorBody
    sav: str
    ops: list[PlanOpBody]
    merged_over: list[int]
    undoes: int | None
    note: str
    text: str


class PlanArgsBody(TypedDict):
    """The whole solve request, every field present at its default when unset (contract §2)."""

    objective: str
    target_item: str | None
    sources: list[str]
    exports: list[str]
    export_minimums: dict[str, float]
    only_free_nodes: bool
    allow_sinks: bool
    clocks: list[float]
    extractor_clocks: list[float]
    machine_cost_mw: float
    banned: list[str]
    required: list[str]
    only_recipes: list[str]
    water_extractors: int | None
    sloops: int
    belt_ipm: float | None
    pipe_m3min: float | None
    recycle_once: list[str]
    supplied: dict[str, float]
    logistics_items: list[str]
    power_priority: int


class PlanStateBody(TypedDict):
    """A plan at ``rev``. ``head`` is the newest rev and ``text`` the head commit's words."""

    key: str
    rev: int
    name: str
    forgotten: bool
    notes: str
    factory: str
    created: str
    plan_id: str
    siting: dict | None
    args: PlanArgsBody
    headroom_mw: float | None
    names: dict[str, str]
    head: int
    text: str


class PushedResponse(TypedDict):
    """A write that landed, or ``noop`` when every op was already true at the head."""

    key: str
    rev: int
    base_rev: int
    noop: bool
    merged_over: list[int]
    applied: list[PlanOpBody]
    dropped: list[PlanOpBody]
    others: list[CommitBody]
    text: str
    state: PlanStateBody


class ConflictBody(TypedDict):
    key: str
    mine: PlanOpBody
    theirs: PlanOpBody
    theirs_rev: int
    theirs_actor: ActorBody
    text: str


class OutdatedResponse(TypedDict):
    """The 409 of a push: nothing was applied; ``state`` is the head to re-read from."""

    error: str
    outdated: bool
    head: int
    base_rev: int
    since: list[CommitBody]
    conflicts: list[ConflictBody]
    state: PlanStateBody


class AlreadyUndoneResponse(TypedDict):
    error: str
    already_undone: bool
    by: int


class NameTakenResponse(TypedDict):
    error: str
    name_taken: bool


class CreatePlanBody(TypedDict):
    name: str
    args: dict
    from_entry: NotRequired[str]


class PlanOpsResponse(TypedDict):
    key: str
    head: int
    commits: list[CommitBody]


class PushBody(TypedDict):
    """``require_item``: the item class id a drawer require is for; the server then also
    removes every other required recipe for it that the head holds (contract C3)."""

    base_rev: int
    ops: list[dict]
    sav: NotRequired[str]
    require_item: NotRequired[str | None]


class PushArgsBody(TypedDict):
    base_rev: int
    args: dict
    sav: NotRequired[str]
    from_entry: NotRequired[str]


class UndoBody(TypedDict):
    base_rev: int
    rev: int
    sav: NotRequired[str]


class RestoreBody(TypedDict):
    base_rev: int
    rev: int
    sav: NotRequired[str]


class DuplicateBody(TypedDict, total=False):
    rev: int | None
    name: str | None


class VersionRow(TypedDict):
    """One commit in the Versions list. ``restores`` is the rev a restore brought back."""

    rev: int
    ts: float
    actor: ActorBody
    text: str
    undoes: int | None
    undone_by: int | None
    restores: int | None
    merged_over: list[int]
    note: str


class VersionsResponse(TypedDict):
    key: str
    name: str
    head: int
    forgotten: bool
    versions: list[VersionRow]


def _page() -> Actor:
    return Actor("page", "", os.getpid())


def _log(st) -> PlanLog:
    return PlanLog(st.world_id, st.header.get("session_name") or "")


def _token(st, given: str | None) -> str:
    if given:
        return given
    try:
        return pin.check(st.header, None)
    except Exception:
        return ""


def _note(from_entry: str | None) -> str:
    return f"applied chat solve {from_entry}" if from_entry else ""


def _commit(commit: Commit) -> CommitBody:
    return {**commit.to_dict(), "actor": _actor_json(commit.actor), "text": commit.text()}


def _state_body(log: PlanLog, state: PlanState, game: GameData) -> PlanStateBody:
    head = log.commits(state.key)[-1]
    return {
        **state.to_dict(),
        "siting": state.siting or None,
        "names": summary.names_for(game, state.args),
        "head": head.rev,
        "text": head.text(),
    }


def _pushed(log: PlanLog, pushed: Pushed, game: GameData) -> PushedResponse:
    return {
        "key": pushed.key,
        "rev": pushed.rev,
        "base_rev": pushed.base_rev,
        "noop": pushed.noop,
        "merged_over": list(pushed.merged_over),
        "applied": pushed.applied,
        "dropped": pushed.dropped,
        "others": [_commit(c) for c in pushed.others],
        "text": pushed.text(pushed.state.name),
        "state": _state_body(log, pushed.state, game),
    }


def _outdated(log: PlanLog, exc: Outdated, game: GameData) -> JSONResponse:
    body: OutdatedResponse = {
        "error": exc.text(exc.state.name),
        "outdated": True,
        "head": exc.head,
        "base_rev": exc.base_rev,
        "since": [_commit(c) for c in exc.since],
        "conflicts": [
            {**c.to_dict(), "theirs_actor": _actor_json(c.theirs_actor)} for c in exc.conflicts
        ],
        "state": _state_body(log, exc.state, game),
    }
    return JSONResponse(body, status_code=409)


def _refused(log: PlanLog, exc: Exception, game: GameData) -> JSONResponse:
    if isinstance(exc, Outdated):
        return _outdated(log, exc, game)
    if isinstance(exc, AlreadyUndone):
        body = {"error": str(exc), "already_undone": True, "by": exc.by}
        return JSONResponse(body, status_code=409)
    if isinstance(exc, Forgotten):
        return _fail(str(exc), 410)
    if isinstance(exc, UnknownPlan):
        return _no_plan(exc.what)
    if isinstance(exc, LockTimeout):
        return _fail(f"plans are busy, nothing written: {exc}", 503)
    return _fail(str(exc), 400)


_ERRORS = (PlanLogError, LockTimeout)


def _reject(st, key: str, sav: str, exc: Exception) -> None:
    if isinstance(exc, Outdated):
        journal.append(
            st.world_id,
            "plan.rejected",
            actor=_page(),
            sav=sav,
            plan=key,
            rev=exc.head,
            text="; ".join(c.text() for c in exc.conflicts),
        )


def _no_plan(key: str) -> JSONResponse:
    return _fail(f"no plan “{key}” in this world", 404)


def _opened(request: Request, key: str, save: str | None, world: str | None):
    """``(state, log)`` for a plan route, or the 404 that stops it."""
    if not _KEY.fullmatch(key):
        return None, _no_plan(key)
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return None, _fail(f"could not read save: {exc}", 404)
    return st, _log(st)


@router.post(
    "/plans",
    status_code=201,
    response_model=PushedResponse,
    responses={409: {"model": NameTakenResponse}},
)
def create_plan(
    request: Request,
    body: Annotated[CreatePlanBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """A new plan at v1, stamped against the save this request read."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    log = _log(st)
    try:
        canon, _said = pins.canonical_args(st, body["args"])
    except pins.PinError as exc:
        return _fail(str(exc), 400)
    try:
        args = PlanArgs.from_dict(canon)
        draft = PlanState(key="", rev=1, name=body["name"], args=args)
        try:
            stamped = summary.stamp_for(st.game, st)(draft)
        except Exception:
            _logger.warning(
                "could not stamp new plan %r; saved unstamped", body["name"], exc_info=True
            )
            stamped = {"plan_id": "", "provenance": {}}
        pushed = log.create(
            body["name"],
            args,
            actor=_page(),
            sav=_token(st, None),
            plan_id=stamped["plan_id"],
            provenance=stamped["provenance"],
            note=_note(body.get("from_entry")),
        )
    except NameTaken as exc:
        return JSONResponse({"error": str(exc), "name_taken": True}, status_code=409)
    except _ERRORS as exc:
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.get("/plans/{key}", response_model=PlanStateBody)
def plan_state(
    request: Request,
    key: str,
    rev: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """A plan at ``rev`` (the head when omitted), forgotten plans included."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    try:
        return _state_body(log, log.state(key, rev), st.game)
    except UnknownPlan:
        return _no_plan(key)
    except InvalidOp as exc:
        return _fail(str(exc), 404)


@router.get("/plans/{key}/ops", response_model=PlanOpsResponse)
def plan_ops(
    request: Request,
    key: str,
    since: int = 0,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Every commit after ``since``, oldest first."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    try:
        commits = log.commits(key, since=since)
        head = log.head_rev(key)
    except UnknownPlan:
        return _no_plan(key)
    return {"key": key, "head": head, "commits": [_commit(c) for c in commits]}


@router.post(
    "/plans/{key}/ops",
    response_model=PushedResponse,
    responses={409: {"model": OutdatedResponse}},
)
def push_ops(
    request: Request,
    key: str,
    body: Annotated[PushBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """One gesture as one commit, merged onto the head by rule M1 or refused whole."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    sav = _token(st, body.get("sav"))
    try:
        ops, _said = pins.canonical_ops(st, body["ops"])
    except pins.PinError as exc:
        return _fail(str(exc), 400)
    item = body.get("require_item")
    extend = None
    if item:
        game = st.game

        def extend(head):
            return swaps.replaced_required(game, head, item, ops)

    try:
        pushed = log.push(
            key,
            body["base_rev"],
            ops,
            actor=_page(),
            sav=sav,
            stamp=summary.stamp_for(st.game, st),
            extend=extend,
        )
    except _ERRORS as exc:
        _reject(st, key, sav, exc)
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.post(
    "/plans/{key}/args",
    response_model=PushedResponse,
    responses={409: {"model": OutdatedResponse}},
)
def push_args(
    request: Request,
    key: str,
    body: Annotated[PushArgsBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """A whole request, diffed against ``base_rev`` and merged: how a chat solve is applied."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    sav = _token(st, body.get("sav"))
    try:
        args, _said = pins.canonical_args(st, body["args"])
    except pins.PinError as exc:
        return _fail(str(exc), 400)
    try:
        pushed = log.push_args(
            key,
            body["base_rev"],
            args,
            actor=_page(),
            sav=sav,
            stamp=summary.stamp_for(st.game, st),
            note=_note(body.get("from_entry")),
        )
    except _ERRORS as exc:
        _reject(st, key, sav, exc)
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.post(
    "/plans/{key}/undo",
    response_model=PushedResponse,
    responses={409: {"model": OutdatedResponse | AlreadyUndoneResponse}},
)
def undo_rev(
    request: Request,
    key: str,
    body: Annotated[UndoBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """The inverse of commit ``rev`` as a new commit; redo is the undo of that undo."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    sav = _token(st, body.get("sav"))
    try:
        pushed = log.undo(
            key,
            body["base_rev"],
            body["rev"],
            actor=_page(),
            sav=sav,
            stamp=summary.stamp_for(st.game, st),
        )
    except _ERRORS as exc:
        _reject(st, key, sav, exc)
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.post(
    "/plans/{key}/restore",
    response_model=PushedResponse,
    responses={409: {"model": OutdatedResponse}},
)
def restore_rev(
    request: Request,
    key: str,
    body: Annotated[RestoreBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """A new commit that makes the head equal ``rev`` again: never a rewind."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    sav = _token(st, body.get("sav"))
    try:
        pushed = log.restore_to(
            key,
            body["base_rev"],
            body["rev"],
            actor=_page(),
            sav=sav,
            stamp=summary.stamp_for(st.game, st),
        )
    except NameTaken as exc:
        return JSONResponse({"error": str(exc), "name_taken": True}, status_code=409)
    except _ERRORS as exc:
        _reject(st, key, sav, exc)
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.post(
    "/plans/{key}/duplicate",
    status_code=201,
    response_model=PushedResponse,
    responses={409: {"model": NameTakenResponse}},
)
def duplicate_plan(
    request: Request,
    key: str,
    body: Annotated[DuplicateBody, Body()],
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """A new plan at v1 equal to this one at ``rev`` (the head when omitted)."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    rev = body.get("rev")
    try:
        head = log.head_rev(key)
    except UnknownPlan:
        return _no_plan(key)
    if rev is not None and not 1 <= rev <= head:
        return _fail(f"plan {key} has no v{rev}; it is at v{head}", 404)
    try:
        pushed = manage.duplicate(
            log,
            key,
            actor=_page(),
            rev=rev,
            name=body.get("name"),
            sav=_token(st, None),
            stamp=summary.stamp_for(st.game, st),
        )
    except NameTaken as exc:
        return JSONResponse({"error": str(exc), "name_taken": True}, status_code=409)
    except _ERRORS as exc:
        return _refused(log, exc, st.game)
    return _pushed(log, pushed, st.game)


@router.get("/plans/{key}/versions", response_model=VersionsResponse)
def plan_versions(
    request: Request,
    key: str,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Every version of one plan, newest first, with what undid or restored what."""
    st, log = _opened(request, key, save, world)
    if st is None:
        return log
    try:
        head = log.state(key)
        rows = manage.versions(log, key)
    except UnknownPlan:
        return _no_plan(key)
    return {
        "key": key,
        "name": head.name,
        "head": head.rev,
        "forgotten": head.forgotten,
        "versions": [
            {
                "rev": r["commit"].rev,
                "ts": r["commit"].ts,
                "actor": _actor_json(r["commit"].actor),
                "text": r["commit"].text(),
                "undoes": r["commit"].undoes,
                "undone_by": r["undone_by"],
                "restores": r["restores"],
                "merged_over": list(r["commit"].merged_over),
                "note": r["commit"].note,
            }
            for r in rows
        ],
    }

"""Stored plans: listing them, their versions, renaming and forgetting (§10.1k)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Annotated, cast

from pydantic import Field

from .....domain.planning import siting as siting_mod
from .....domain.planning.progress.diff_service import plan_progress
from .....domain.planning.solver.scenario import PlanKwargs, build_scenario
from .....domain.planning.stored import manage
from .....domain.planning.stored import provenance as prov
from .....domain.planning.stored.plan_args import PlanLogError
from .....domain.planning.stored.planlog import PlanLog, PlanState, factory_words
from .....domain.planning.stored.store import PLAN_ARGS, Plan
from .....domain.planning.stored.views import SelectorRecord
from .....domain.world.state import WorldState
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, BaseRev, Limit
from ._plan_log import (
    age,
    commit_text,
    head_stamper,
    needs_base,
    ops_text,
    world_plan_log,
    write,
)
from ._requests import find_stored_plan, pinned_plan_name, unknown_plan

LAST_CHANGE_WIDTH = 36


def _arg_text(value: object) -> str:
    """One stored argument as the reader typed it, never as the solver resolved it."""
    if isinstance(value, dict):
        pairs = cast("dict[str, object]", value).items()
        return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in pairs)
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in cast("Sequence[object]", value))
    return str(value)


def _last_change(log: PlanLog, key: str, names: dict[str, str], now: float) -> str:
    """Who last changed the plan ``key``, how long ago, and what, cut to the column."""
    for commit in reversed(log.commits(key)):
        text = ops_text(commit.ops, names)
        if text:
            return render.cut(
                f"{commit.actor.display()} {age(now - commit.ts)}: {text}", LAST_CHANGE_WIDTH
            )
    return "-"


def _plan_detail(st: WorldState, stored: Plan) -> str:
    """One stored plan in full, without solving it.

    ``plan_factory plan=<name>`` answers a different question at LP cost: it prints what
    the request RESOLVES to today. This prints the request.
    """
    sit = siting_mod.parse(stored)
    head = [
        f'# plan "{stored.name}" v{stored.rev} (key {stored.key})',
        f"# {st.age_note}",
        render.kv(
            [
                ("plan_id", stored.plan_id or "(none)"),
                ("saved_against", stored.created),
                ("for_factory", factory_words(stored.factory)),
            ]
        ),
    ]
    if stored.notes:
        head.append(f"notes: {stored.notes}")
    if sit is not None:
        head.append(f"sited: {sit.describe()}")

    stored_args = [(k, stored.args[k]) for k in PLAN_ARGS if k in stored.args]
    parts = [
        "# the stored REQUEST -- every argument not listed is at its default\n"
        + render.table(("argument", "value"), [(k, _arg_text(v)) for k, v in stored_args])
    ]
    field = cast("list[SelectorRecord]", (stored.provenance or {}).get("selectors") or [])
    if field:
        parts.append(
            "# what each source selector resolved to WHEN SAVED\n"
            + render.table(
                ("selector", "nodes", "box(m)"),
                [
                    (
                        e.get("selector", ""),
                        e.get("count", 0),
                        ",".join(f"{v:g}" for v in bbox) if (bbox := e.get("bbox")) else "-",
                    )
                    for e in field
                ],
            )
        )

    notes = [
        "nothing was solved here: pass plan=<name> to plan_factory for today's answer",
        f"change it with base_rev={stored.rev}; plan_log name={stored.name!r} lists its versions",
    ]
    try:
        if (
            build_scenario(st.game, st, **cast(PlanKwargs, stored.kwargs())).plan_id
            != stored.plan_id
        ):
            notes.append(
                "the WORLD has moved since this was saved (an unlock, a freed node, a new "
                "building), so re-solving it will not reproduce the plan_id above"
            )
        notes.extend(prov.notes(st.game, st, stored))
    except Exception as exc:  # a stored plan can outlive the thing it referenced
        notes.append(f"the staleness checks could not run: {type(exc).__name__}: {exc}")
    return render.envelope("\n".join(head), "\n".join(parts), notes)


def _sited_cell(stored: Plan) -> str:
    """``x,y y<yaw> WxD`` in metres for the plans table, or ``-`` when it is not sited."""
    sit = siting_mod.parse(stored)
    if sit is None:
        return "-"
    sited = f"{sit.x_m:.0f},{sit.y_m:.0f}"
    sited += f" y{sit.yaw_deg:g}" if sit.yaw_deg else ""
    sited += f" {sit.width_m:g}x{sit.depth_m:g}" if sit.has_footprint else ""
    return sited


def _built_cell(st: WorldState, stored: Plan) -> str:
    """``12/16 @oil setup``, ``?`` or ``not placed`` for the plans table."""
    try:
        found = plan_progress(app.game(), st, stored)
    except Exception:  # a stored plan can outlive the thing it referenced
        return "?"
    if found is None:
        return "?"
    if found.confidence == "unsure":
        return "? which factory"
    if found.built is None:
        return "not placed"
    where = found.picked or (found.top.name if found.top is not None else "")
    if found.mode == "world":
        where = "whole world"
    return f"{found.figure().replace(' ', '')}" + (f" @{where}" if where else "")


def _plans_table_notes(
    rows: list[tuple[object, ...]], drifted: bool, unrecorded: list[str]
) -> list[str]:
    notes = [
        (
            "'world moved' means the plan is unchanged but the solve inputs are not "
            "-- an unlock, a freed node or a new building. Re-run it to see how"
        ),
        (
            "pass name=<plan> for one plan's stored arguments, its recorded field and its "
            "full siting, without solving anything; plan_log name=<plan> for its versions"
        ),
    ]
    if any("~" in str(cell) for row in rows for cell in row):
        notes.append("a '~' ends a cell this table had to cut; name=<plan> prints it whole")
    if drifted:
        notes.append(
            "'field N->M' means the plan's own selectors no longer resolve to the nodes it "
            "was saved against -- the map layer under a name was re-cut, so the plan now "
            "plans over a different part of the world. Recall it for which nodes moved"
        )
    if unrecorded:
        notes.append(
            f"no recorded field, so the check above cannot run: {', '.join(unrecorded)} "
            "-- these predate it. A blank status is 'not checked', not 'unchanged'; "
            "re-run one with save_as=<its own name> to record what its selectors mean"
        )
    return notes


@app.tool()
def list_plans(
    name: Annotated[
        str | None,
        Field(description="one plan's full stored request, siting and field, unsolved"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Plans saved for this world, their versions, and whether the world has moved under them.

    ``name`` prints one plan in full instead: the arguments as they were stored, what its
    source selectors resolved to when saved, and its whole siting. Nothing is solved, so
    this answers "what did I ask for" -- ``plan_factory plan=<name>`` answers the other
    question, what those arguments resolve to today, and pays an LP solve for it.

    Every plan carries a version (``v14``). Pass it as ``base_rev`` to any tool that
    changes the plan; ``plan_log`` lists the versions and undoes them.
    """
    st = app.load_world(save, world, as_of)
    if name:
        return _plan_detail(st, find_stored_plan(st, name))
    if not st.plans.plans:
        return render.envelope(
            f"# no plans saved for world {st.plans.world_id!r}",
            "Pass save_as=<name> to plan_factory to store one.",
        )
    log = world_plan_log(st)
    names = app.recipe_names()
    now = time.time()
    rows: list[tuple[object, ...]] = []
    unrecorded: list[str] = []
    drifted = False
    for stored in st.plans.plans:
        checked = manage.plan_status(st, stored)
        drifted = drifted or checked.drift
        if not checked.broken and not checked.recorded:
            unrecorded.append(stored.name)
        args = stored.args
        try:
            last = _last_change(log, stored.key, names, now)
        except PlanLogError:
            last = "-"
        rows.append(
            (
                stored.name,
                f"v{stored.rev}",
                last,
                args.get("objective", "max_mw"),
                args.get("target_item") or "-",
                render.cut(",".join(cast("list[str]", args.get("sources") or [])), 36)
                or "whole map",
                _built_cell(st, stored),
                _sited_cell(stored),
                "; ".join(checked.flags),
                render.cut(stored.notes, 36),
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(rows)} saved plan(s)",
        render.table(
            (
                "name",
                "ver",
                "last change",
                "objective",
                "target",
                "sources",
                "built",
                "sited(m)",
                "status",
                "notes",
            ),
            rows,
        ),
        _plans_table_notes(rows, drifted, unrecorded),
    )


@app.tool()
def forget_plan(
    name: str,
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Delete a saved plan. Nothing in the world is touched, and plan_log can undo it."""
    st = app.load_world(save, world, as_of)
    stored = find_stored_plan(st, name)
    if base_rev is None:
        return needs_base(stored.name, stored.rev, "nothing forgotten")
    pushed, text = write(
        stored.name,
        "nothing forgotten",
        lambda: world_plan_log(st).push(
            stored.key,
            base_rev,
            [{"op": "forget"}],
            actor=app.actor(ctx),
            sav=app.save_token(st),
        ),
    )
    if pushed is None:
        return text
    return (
        f"forgot plan {stored.name!r} in v{pushed.rev}; plan_log name={stored.name!r} "
        f"undo={pushed.rev} base_rev={pushed.rev} brings it back\n{text}"
    )


@app.tool()
def rename_plan(
    name: str,
    to: Annotated[str, Field(description="the new name")],
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Rename a saved plan. Nothing is re-solved and nothing else about it changes.

    The plan keeps its key, its recorded field, its siting and its notes.
    """
    st = app.load_world(save, world, as_of)
    stored = find_stored_plan(st, name)
    try:
        wanted = world_plan_log(st).free_name(to, stored.key)
    except PlanLogError as exc:
        return f"! {exc}"
    was = stored.name
    if was == wanted:
        return f"plan {was!r} already has that name"
    if base_rev is None:
        return needs_base(was, stored.rev, "nothing renamed")
    pushed, text = write(
        was,
        "nothing renamed",
        lambda: world_plan_log(st).push(
            stored.key,
            base_rev,
            [{"op": "rename", "name": wanted}],
            actor=app.actor(ctx),
            sav=app.save_token(st),
        ),
    )
    if pushed is None:
        return text
    path = PlanLog.dir_for(st.world_id)
    return render.envelope(
        f"# renamed plan {was!r} to {wanted!r}\nstored in {path}\n{text}",
        "",
        [
            (
                f"recall it as plan={wanted!r}; the plan id, field record, siting and "
                "notes are untouched"
            )
        ],
    )


def _history(log: PlanLog, found: PlanState, since: int | None, limit: int) -> str:
    """One plan's versions after ``since``, newest first, with what undid or merged each."""
    names = app.recipe_names()
    now = time.time()
    commits = log.commits(found.key)
    undone = manage.undone_by(commits)
    shown = [c for c in reversed(commits) if c.rev > (since or 0)]
    lines: list[str] = []
    for commit in shown[:limit]:
        flags = [f"{age(now - commit.ts)} ago"]
        if commit.merged_over:
            flags.append("merged over " + ",".join(f"v{r}" for r in commit.merged_over))
        if commit.rev in undone:
            flags.append(f"undone in v{undone[commit.rev]}")
        lines.append(f"{commit_text(commit, names)}  ({'; '.join(flags)})")
    if len(shown) > limit:
        lines.append(f"(+{len(shown) - limit} more: raise limit, or pass since=)")
    head = f'# plan "{found.name}" v{found.rev} (key {found.key})'
    if found.forgotten:
        head += " -- FORGOTTEN; undo its forget to bring it back"
    notes = [
        (
            f'undo one version: plan_log name="{found.name}" undo=<v> base_rev={found.rev}; '
            "restore=<v> makes the head equal that version again. Both are new versions"
        )
    ]
    return render.envelope(head, "\n".join(lines) or "(no versions after that one)", notes)


@app.tool()
def plan_log(
    name: str,
    since: Annotated[int | None, Field(description="list versions after this one")] = None,
    undo: Annotated[int | None, Field(description="undo this version (needs base_rev)")] = None,
    restore: Annotated[
        int | None,
        Field(description="make the head equal this version (needs base_rev)"),
    ] = None,
    base_rev: BaseRev = None,
    limit: Limit = 15,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """One plan's versions, newest first: who changed what, from chat or from the page.

    ``undo=<v>`` writes a new version that reverses that one; ``restore=<v>`` writes a new
    version equal to that one. Both take ``base_rev`` and merge like any other edit. A
    forgotten plan is found too, so its forget can be undone.
    """
    st = app.load_world(save, world, as_of)
    name = pinned_plan_name(st, name)
    log = world_plan_log(st)
    found = log.find(name, include_forgotten=True)
    if found is None:
        raise unknown_plan(st, name)
    if undo is None and restore is None:
        try:
            return _history(log, found, since, render.clamp(limit, default=15))
        except PlanLogError as exc:
            return f"! {exc}"
    if undo is not None and restore is not None:
        return "! pass undo= or restore=, not both; nothing changed"
    verb = "undone" if undo is not None else "restored"
    if base_rev is None:
        return needs_base(found.name, found.rev, f"nothing {verb}")
    who, sav, stamp = app.actor(ctx), app.save_token(st), head_stamper(st)
    if undo is not None:
        pushed, text = write(
            found.name,
            "nothing undone",
            lambda: log.undo(found.key, base_rev, undo, actor=who, sav=sav, stamp=stamp),
        )
        done = f"undid v{undo}"
    else:
        assert restore is not None, "undo or restore, checked above"
        pushed, text = write(
            found.name,
            "nothing restored",
            lambda: log.restore_to(found.key, base_rev, restore, actor=who, sav=sav, stamp=stamp),
        )
        done = f"restored v{restore}"
    if pushed is None:
        return text
    changes = ops_text(pushed.applied, app.recipe_names()) or "no change"
    return f'# {done} of plan "{pushed.state.name}": {changes}\n{text}'

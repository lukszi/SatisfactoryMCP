"""The optimiser surface: plans, layouts, diffs, bills of materials.

Also plan persistence, since a stored plan is a stored planning REQUEST. Plans are
versioned: docs/mcp-surface.md §10.1k."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from ....core.filelock import LockTimeout
from ....core.gamedata.unlocks import granted_by_label
from ....core.schema import NewerSchema
from ....domain import advice
from ....domain.advice import store as advice_store
from ....domain.factories.select import SelectorError
from ....domain.planning import asks, compare, journal, manage, payback, pins, summary
from ....domain.planning import bom as bom_mod
from ....domain.planning import provenance as prov
from ....domain.planning import siting as siting_mod
from ....domain.planning.carrier import resolve_tiers
from ....domain.planning.commission import partition_id
from ....domain.planning.commission_service import build_commission_report
from ....domain.planning.diff_service import build_diff_report, plan_progress
from ....domain.planning.layout_service import LayoutReport, build_layout_report
from ....domain.planning.planlog import (
    Actor,
    AlreadyUndone,
    BaseRevRequired,
    Commit,
    Forgotten,
    InvalidOp,
    Outdated,
    PlanArgs,
    PlanLog,
    PlanLogError,
    Pushed,
    describe_commit,
    describe_op,
    factory_words,
)
from ....domain.planning.prepare import prepare
from ....domain.planning.recall import (
    PLAN_DEFAULTS,
    UNSAVED_OVERRIDE,
    overrides_of,
    plan_ref,
    with_overrides,
)
from ....domain.planning.recall import recall_plan as _plan_kwargs
from ....domain.planning.report import build_plan_report
from ....domain.planning.scenario import build_scenario
from ....domain.planning.sensitivity import sweep_unlocks
from ....domain.planning.store import PLAN_ARGS
from ....domain.world import pin
from ....presenters.text import advice as advice_text
from ....presenters.text import byproducts as byproducts_text
from ....presenters.text import primitives as render
from ....presenters.text.bom import render_bom
from ....presenters.text.commission import render_commission
from ....presenters.text.compare import render_comparison
from ....presenters.text.diff import ENERGISED_CAVEAT, RANGE_CAVEAT, render_diff
from ....presenters.text.layout import render_layout
from ....presenters.text.plan_factory import render_plan_factory
from ..app import AsOf, Biomass, Limit, _item_id, _state, actor, game, mcp, retired, shared

#: The stored-argument defaults, re-exported under their old home for ``server``. The
#: two stage caveats keep their old home too: they were read from here before they had
#: a presenter to live in.
_ = (PLAN_DEFAULTS, ENERGISED_CAVEAT, RANGE_CAVEAT)

BUSY = "! plans are busy (another writer held the lock 10 s); nothing written"
LAST_WIDTH = 36
CONTEXT_PLANS = 8
CONTEXT_COMMITS = 6
CONTEXT_JOURNAL = 8
FIRST_LOOK = 5
CONTEXT_BUDGET = 3800
CONTEXT_PINS = 8
CONTEXT_PIN_WIDTH = 90
CONTEXT_ASKS = 6
CONTEXT_ASK_WIDTH = 200

BaseRev = Annotated[
    int | None,
    Field(description="the plan version you read; needed to change an existing plan"),
]


def _arg_text(value) -> str:
    """One stored argument as the reader typed it, never as the solver resolved it."""
    if isinstance(value, dict):
        return ", ".join(
            f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in value.items()
        )
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _cut(text: str, width: int) -> str:
    """Truncate visibly: a silently clipped source list reads as the whole list."""
    return text if len(text) <= width else text[: width - 1] + "~"


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _log(st) -> PlanLog:
    return PlanLog(st.world_id, st.header.get("session_name") or "")


def _sav(st) -> str:
    try:
        return pin.check(st.header, None)
    except Exception:
        return ""


def _recipe_names() -> dict[str, str]:
    try:
        return {cls: r.name for cls, r in game().recipes.items()}
    except Exception:
        return {}


def _named(ops: list[dict], names: dict[str, str]) -> list[dict]:
    out = []
    for op in ops:
        if op.get("field") in ("banned", "required") and op.get("member") in names:
            op = {**op, "member": names[op["member"]]}
        out.append(op)
    return out


def _ops_text(ops: list[dict], names: dict[str, str]) -> str:
    return " · ".join(t for t in (describe_op(o) for o in _named(ops, names)) if t)


def _commit_text(commit: Commit, names: dict[str, str]) -> str:
    shown = copy.copy(commit)
    shown.ops = _named(commit.ops, names)
    return describe_commit(shown)


def _last_change(log: PlanLog, key: str, names: dict[str, str], now: float) -> str:
    for commit in reversed(log.commits(key)):
        text = _ops_text(commit.ops, names)
        if text:
            return _cut(f"{commit.actor.display()} {_age(now - commit.ts)}: {text}", LAST_WIDTH)
    return "-"


def _needs_base(name: str, head: int, nothing: str) -> str:
    return (
        f'! plan "{name}" exists at v{head}: read it (list_plans name="{name}") and pass '
        f"base_rev={head}; {nothing}"
    )


def _write(name: str, nothing: str, push: Callable[[], Pushed]) -> tuple[Pushed | None, str]:
    """Run one plan write and word its outcome: the merged note, or why nothing landed."""
    try:
        pushed = push()
    except Outdated as exc:
        return None, exc.text(name)
    except BaseRevRequired as exc:
        return None, _needs_base(name, exc.head, nothing)
    except Forgotten as exc:
        return None, (
            f'! plan "{name}" was forgotten in v{exc.rev}; plan_log name="{name}" '
            f"undo={exc.rev} brings it back; {nothing}"
        )
    except AlreadyUndone as exc:
        return None, f"! v{exc.rev} was already undone by v{exc.by}; {nothing}"
    except LockTimeout:
        return None, BUSY
    except PlanLogError as exc:
        return None, f"! {exc}; {nothing}"
    return pushed, pushed.text(pushed.state.name or name)


def _stamp(st) -> Callable:
    """What the store records on the new head: its solve-input hash and resolved field."""
    return summary.stamp_for(game(), st)


def _unknown(st, name: str) -> str:
    known = ", ".join(x.name for x in st.plans.plans) or "(none)"
    return f"! no saved plan named {name!r}. Saved: {known}"


def _plan_detail(st, stored) -> str:
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
    field = (stored.provenance or {}).get("selectors") or ()
    if field:
        parts.append(
            "# what each source selector resolved to WHEN SAVED\n"
            + render.table(
                ("selector", "nodes", "box(m)"),
                [
                    (
                        e.get("selector", ""),
                        e.get("count", 0),
                        ",".join(f"{v:g}" for v in e["bbox"]) if e.get("bbox") else "-",
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
        if build_scenario(st.game, st, **stored.kwargs()).plan_id != stored.plan_id:
            notes.append(
                "the WORLD has moved since this was saved (an unlock, a freed node, a new "
                "building), so re-solving it will not reproduce the plan_id above"
            )
        notes.extend(prov.notes(st.game, st, stored))
    except Exception as exc:  # a stored plan can outlive the thing it referenced
        notes.append(f"the staleness checks could not run: {type(exc).__name__}: {exc}")
    return render.envelope("\n".join(head), "\n".join(parts), notes)


@mcp.tool(structured_output=False)
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
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    if name:
        try:
            name, _echo = _plan_pin(st, name)
        except KeyError as exc:
            return f"! {exc.args[0]}"
        stored = st.plans.find(name)
        if stored is None:
            return _unknown(st, name)
        return _plan_detail(st, stored)
    if not st.plans.plans:
        return render.envelope(
            f"# no plans saved for world {st.plans.world_id!r}",
            "Pass save_as=<name> to plan_factory to store one.",
        )
    log = _log(st)
    names = _recipe_names()
    now = time.time()
    rows = []
    unrecorded = []
    drifted = False
    for stored in st.plans.plans:
        checked = manage.plan_status(st, stored)
        status = checked.flags
        drifted = drifted or checked.drift
        if not checked.broken and not checked.recorded:
            unrecorded.append(stored.name)
        args = stored.args
        sit = siting_mod.parse(stored)
        sited = "-"
        if sit is not None:
            sited = f"{sit.x_m:.0f},{sit.y_m:.0f}"
            sited += f" y{sit.yaw_deg:g}" if sit.yaw_deg else ""
            sited += f" {sit.width_m:g}x{sit.depth_m:g}" if sit.has_footprint else ""
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
                _cut(",".join(args.get("sources") or []), 36) or "whole map",
                _built_cell(st, stored),
                sited,
                "; ".join(status),
                _cut(stored.notes, 36),
            )
        )
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
        notes,
    )


@mcp.tool(structured_output=False)
def forget_plan(
    name: str,
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: Context | None = None,
) -> str:
    """Delete a saved plan. Nothing in the world is touched, and plan_log can undo it."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        name, _echo = _plan_pin(st, name)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    stored = st.plans.find(name)
    if stored is None:
        return _unknown(st, name)
    if base_rev is None:
        return _needs_base(stored.name, stored.rev, "nothing forgotten")
    pushed, text = _write(
        stored.name,
        "nothing forgotten",
        lambda: _log(st).push(
            stored.key, base_rev, [{"op": "forget"}], actor=actor(ctx), sav=_sav(st)
        ),
    )
    if pushed is None:
        return text
    return (
        f"forgot plan {stored.name!r} in v{pushed.rev}; plan_log name={stored.name!r} "
        f"undo={pushed.rev} base_rev={pushed.rev} brings it back\n{text}"
    )


@mcp.tool(structured_output=False)
def rename_plan(
    name: str,
    to: Annotated[str, Field(description="the new name")],
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: Context | None = None,
) -> str:
    """Rename a saved plan. Nothing is re-solved and nothing else about it changes.

    The plan keeps its key, its recorded field, its siting and its notes -- a name is the
    only thing here a player picked, and it was the only thing they could not correct
    without saving the plan again under a second name and forgetting the first.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        name, _echo = _plan_pin(st, name)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    stored = st.plans.find(name)
    if stored is None:
        return _unknown(st, name)
    try:
        wanted = _log(st).free_name(to, stored.key)
    except PlanLogError as exc:
        return f"! {exc}"
    was = stored.name
    if was == wanted:
        return f"plan {was!r} already has that name"
    if base_rev is None:
        return _needs_base(was, stored.rev, "nothing renamed")
    pushed, text = _write(
        was,
        "nothing renamed",
        lambda: _log(st).push(
            stored.key,
            base_rev,
            [{"op": "rename", "name": wanted}],
            actor=actor(ctx),
            sav=_sav(st),
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


@mcp.tool(structured_output=False)
def site_plan(
    plan: str,
    at: Annotated[
        str,
        Field(
            description="site origin (the footprint's CENTRE): 'x,y[,z]' in metres, "
            "'me', a factory name, 'slab:<n>' or a run id. Blank keeps the stored origin"
        ),
    ] = "",
    yaw_deg: Annotated[
        float | None,
        Field(description="degrees about world Z, positive +X towards +Y; omit to keep"),
    ] = None,
    footprint: Annotated[
        str,
        Field(
            description="'WxD' in metres ('96' = square). Blank keeps the stored one, "
            "or derives the layout's own square if none is stored"
        ),
    ] = "",
    clear: bool = False,
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: Context | None = None,
) -> str:
    """Record, update or clear WHERE a stored plan stands. Nothing is re-solved.

    A plan stores what to build; this stores where -- origin (x, y and optionally z, in
    metres, at the footprint's centre), orientation (yaw about world Z, the same
    convention the save stores machine facing with), and footprint (width x depth,
    metres). The footprint defaults to the square plan_layout budgets for the plan's
    largest floor, and the record keeps track of whether it was measured or derived.

    Once sited: plan recalls print the siting; ``diff_vs_save plan=<name>`` adds an
    approximate what-stands-on-the-pad census; ``show_on_map at='plan:<name>'``
    centres a map link on the origin.

    The siting is a RECORD of your decision, not a constraint on the solve -- re-running
    the plan neither reads nor moves it, and ``save_as`` over the same name keeps it.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        plan, _echo = _plan_pin(st, plan)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    stored = st.plans.find(plan)
    if stored is None:
        return _unknown(st, plan)

    def push(value: dict | None, nothing: str) -> tuple[Pushed | None, str]:
        return _write(
            stored.name,
            nothing,
            lambda: _log(st).push(
                stored.key,
                base_rev,
                [{"op": "site", "value": value}],
                actor=actor(ctx),
                sav=_sav(st),
            ),
        )

    if clear:
        if not stored.siting:
            return f"plan {stored.name!r} carries no siting; nothing to clear"
        if base_rev is None:
            return _needs_base(stored.name, stored.rev, "nothing cleared")
        pushed, text = push(None, "nothing cleared")
        if pushed is None:
            return text
        return f"cleared the siting of plan {stored.name!r}. The plan itself is untouched\n{text}"

    existing = siting_mod.parse(stored)
    if not at and existing is None:
        return (
            f"! plan {stored.name!r} has no siting yet, so there is no origin to keep -- "
            "pass at='x,y[,z]' in metres, 'me', a factory name, 'slab:<n>' or a run id"
        )
    if base_rev is None:
        return _needs_base(stored.name, stored.rev, "not sited")

    when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
    try:
        if at:
            sit = siting_mod.build_siting(
                g,
                st,
                at=at,
                yaw_deg=yaw_deg if yaw_deg is not None else (existing.yaw_deg if existing else 0.0),
                footprint=footprint
                or (
                    f"{existing.width_m:g}x{existing.depth_m:g}"
                    if existing and existing.has_footprint
                    else ""
                ),
                plan_kwargs=stored.kwargs(),
                when=when,
            )
        else:
            width, depth, source = existing.width_m, existing.depth_m, existing.source
            if footprint:
                width, depth = siting_mod.parse_footprint(footprint)
                source = "given"
            sit = siting_mod.Siting(
                x_m=existing.x_m,
                y_m=existing.y_m,
                z_m=existing.z_m,
                yaw_deg=yaw_deg if yaw_deg is not None else existing.yaw_deg,
                width_m=width,
                depth_m=depth,
                source=source,
                origin_label=existing.origin_label,
                when=when,
            )
    except ValueError as exc:
        return f"! {exc}"

    pushed, text = push(sit.to_dict(), "not sited")
    if pushed is None:
        return text
    path = PlanLog.dir_for(st.world_id)

    from ....domain.spatial import maplink, regions

    label = regions.load_regions().label_for(sit.x_m * 100, sit.y_m * 100)
    verb = "re-sited" if existing else "sited"
    return render.envelope(
        f"# {verb} plan {stored.name!r}: {sit.describe()}\n"
        f"# region: {label.describe()}\n"
        f"stored in {path}\n{text}",
        "map: "
        + maplink.local_map_url(sit.x_m, sit.y_m, world=st.plans.world_id)
        + "\n"
        + maplink.map_url(sit.x_m * 100, sit.y_m * 100),
        [
            (
                f"diff_vs_save plan={stored.name!r} now reports what stands inside this "
                f"footprint; show_on_map at='plan:{stored.name}' centres on it"
            ),
            (
                "the siting is a record, not a constraint: re-solving the plan neither "
                "reads nor moves it"
            ),
        ],
    )


def _refusal(exc: Exception) -> str:
    return exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)


def _plan_pin(st, plan: str | None) -> tuple[str | None, list[str]]:
    """``plan`` with a plan pin swapped for its key, and the echo. Raises ``KeyError``."""
    found, echo = plan_ref(st, plan)
    return found, [echo] if echo else []


def _live_named(st, name: str):
    wanted = name.strip().casefold()
    return next((p for p in st.plans.plans if p.name.casefold() == wanted), None)


def _resolve_required(entries: list[str] | None) -> tuple[list[str] | None, str]:
    """Recipe class ids for ``required``, by exact id or exact display name, or a refusal."""
    if not entries:
        return None, ""
    recipes = game().recipes
    out = []
    for raw in entries:
        text = str(raw).strip()
        if text in recipes:
            out.append(text)
            continue
        hits = sorted(r.cls for r in recipes.values() if r.name.casefold() == text.casefold())
        if not hits:
            return None, (
                f"! required: no recipe is called {raw!r} -- pass its exact name or class id "
                "(search_recipes shows both); nothing solved"
            )
        if len(hits) > 1:
            return None, (
                f"! required: {raw!r} names {len(hits)} recipes ({', '.join(hits[:4])}) -- "
                "pass the class id; nothing solved"
            )
        out.append(hits[0])
    return out, ""


def _resolve_rows(rows: dict | None) -> tuple[dict | None, str]:
    """``row_overclock`` keyed by recipe class id, as ``required`` resolves, or a refusal."""
    if not rows:
        return rows, ""
    out = {}
    for name, choice in rows.items():
        if choice not in ("last", "spread", "default", None):
            return None, (
                f"! row_overclock[{name!r}] must be 'last', 'spread' or 'default', not "
                f"{choice!r}; nothing solved"
            )
        ids, refused = _resolve_required([name])
        if refused:
            return None, refused.replace("! required:", "! row_overclock:", 1)
        out[ids[0]] = choice
    return out, ""


def _journal_args(plan_kwargs: dict, logistics_items: list[str] | None) -> dict | None:
    raw = {k: v for k, v in plan_kwargs.items() if v is not None}
    if logistics_items:
        raw["logistics_items"] = list(logistics_items)
    try:
        args = PlanArgs.from_dict(raw)
    except InvalidOp:
        return None
    blank = PlanArgs().to_dict()
    return {k: v for k, v in args.to_dict().items() if v != blank[k]}


def _solve_text(plan_kwargs: dict, feasible: bool, recalled) -> str:
    rates = plan_kwargs.get("export_minimums") or {}
    if rates:
        what = ", ".join(f"{k} {v:g}/min" for k, v in rates.items())
    else:
        what = plan_kwargs.get("target_item") or ", ".join(plan_kwargs.get("exports") or ["MW"])
    objective = plan_kwargs.get("objective") or "max_mw"
    on = f' plan "{recalled.name}" v{recalled.rev}:' if recalled is not None else ""
    return f"{'solved' if feasible else 'infeasible:'}{on} {what} ({objective})"


def _journal_view(st, plan: str | None, tool: str, ctx, args: dict | None = None) -> None:
    if not plan:
        return
    stored = st.plans.find(plan)
    if stored is None:
        return
    journal.append(
        st.world_id,
        "plan.view",
        actor=actor(ctx),
        sav=_sav(st),
        tool=tool,
        plan=stored.key,
        rev=stored.rev,
        args=args,
        text=f'{tool} on plan "{stored.name}" v{stored.rev}',
    )


_stages_seen: dict[tuple[str, str], tuple[str, int, int, int]] = {}


def _recalled(st, plan: str | None):
    """The stored version ``plan`` names, as the plan log holds it, or None."""
    if not plan:
        return None
    found = st.plans.find(plan)
    if found is None:
        return None
    try:
        return _log(st).state(found.key)
    except PlanLogError:
        return None


def _where(current: int, count: int) -> str:
    if not count:
        return "no startup order fits the headroom"
    if not current:
        return f"every stage of {count} built"
    return f"stage {current} of {count}"


def _renumbered(st, stored, tracking) -> str:
    """The note that the stages moved since this process last read this plan, or ''."""
    if stored is None or tracking is None:
        return ""
    count = len(tracking.stages) if tracking.ok else 0
    now = (partition_id(tracking), tracking.current if count else 0, count, stored.rev)
    seen = _stages_seen.get((st.world_id, stored.key))
    _stages_seen[(st.world_id, stored.key)] = now
    if seen is None or seen[0] == now[0]:
        return ""
    was = _where(seen[1], seen[2])
    was = f"you were in {was}" if seen[2] and seen[1] else f"before, {was}"
    return (
        f"the stages changed since you last read this plan (v{seen[3]} -> v{now[3]}): "
        f"{was}, now {_where(now[1], now[2])}"
    )


#: What ``factory=`` and ``for_factory=`` take besides a factory name.
_FACTORY_WORDS = {
    "auto": "",
    "automatic": "",
    "world": "/world",
    "whole world": "/world",
    "none": "/none",
    "nothing": "/none",
    "nothing built yet": "/none",
}


def _factory_value(text: str | None) -> str | None:
    """A ``factory`` argument as the stored value, or None when it was left blank."""
    if text is None or not text.strip():
        return None
    return _FACTORY_WORDS.get(text.strip().casefold(), text.strip())


def _built_cell(st, stored) -> str:
    """``12/16 @oil setup``, ``?`` or ``not placed`` for the plans table."""
    try:
        found = plan_progress(game(), st, stored)
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


def _save_new(st, name, plan_kwargs, logistics, labels, field, plan_id, sit, ctx) -> Pushed:
    notes, factory, when = labels
    args = dict(plan_kwargs)
    if logistics:
        args["logistics_items"] = list(logistics)
    return _log(st).create(
        name,
        args,
        actor=actor(ctx),
        sav=_sav(st),
        notes=notes,
        factory=factory,
        siting=sit.to_dict() if sit is not None else None,
        plan_id=plan_id,
        provenance=field,
        created=when,
    )


def _power_args(hours, overclock, price) -> dict:
    return {"payback_hours": hours, "overclock_last": overclock, "power_price": price}


def _payback_notes(g, st, prepared) -> list[str]:
    sol = prepared.solution
    draw = sum(-p["mw"] for p in sol.processes if p["mw"] < 0)
    view = summary.power_view(g, st, prepared.request, sol, round(sol.machines_total), draw)
    return payback.trade_text(view)


def _power_refusal(supplied: dict) -> str:
    try:
        PlanArgs.from_dict({k: supplied.get(k) for k in _power_args(0, 0, 0)})
    except InvalidOp as exc:
        return f"! {exc}; nothing solved"
    return ""


def _save_target(st, save_as: str, base_rev):
    """The stored plan ``save_as`` writes over, or None for a new one; or a refusal.

    A live name wins. Failing that, with ``base_rev``, the plan that carried that name at
    ``base_rev`` (renamed since) or whose key it is, so a rename merges instead of forking.
    """
    live = _live_named(st, save_as)
    if live is not None or base_rev is None:
        return live, ""
    log = _log(st)
    wanted = save_as.strip().casefold()
    hits = []
    for state in log.heads(include_forgotten=True):
        if state.key == wanted and not state.forgotten:
            return state, ""
        if not isinstance(base_rev, int) or not 1 <= base_rev <= state.rev:
            continue
        try:
            then = log.state(state.key, base_rev)
        except PlanLogError:
            continue
        if not then.forgotten and then.name.casefold() == wanted:
            hits.append(state)
    if len(hits) > 1:
        keys = ", ".join(f'"{s.name}" (key {s.key})' for s in hits)
        return None, (
            f'! no plan is called "{save_as}" now, and {len(hits)} plans were at '
            f"v{base_rev}: {keys}. Pass save_as=<key>; nothing saved"
        )
    return (hits[0] if hits else None), ""


def _save_over(st, existing, base_rev, plan_kwargs, logistics, labels, sit, ctx, overrides=None):
    notes, factory = labels
    log = _log(st)

    def push() -> Pushed:
        base = log.state(existing.key, base_rev)
        if overrides is None:
            args = dict(plan_kwargs)
        else:
            args = with_overrides(base.kwargs(), overrides)
        args["logistics_items"] = (
            list(logistics) if logistics is not None else list(base.args.logistics_items)
        )
        extra = [{"op": "set", "field": "notes", "value": notes}] if notes else []
        extra += (
            [{"op": "set", "field": "factory", "value": factory}] if factory is not None else []
        )
        extra += [{"op": "site", "value": sit.to_dict()}] if sit is not None else []
        return log.push_args(
            existing.key,
            base_rev,
            args,
            actor=actor(ctx),
            sav=_sav(st),
            extra=extra,
            stamp=_stamp(st),
        )

    return _write(existing.name, "nothing saved", push)


@mcp.tool(structured_output=False)
def plan_factory(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 15,
    logistics_items: Annotated[
        list[str] | None,
        Field(description="items whose belt/pipe rows to pin, whatever their volume"),
    ] = None,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
    recycle_once: Annotated[
        list[str] | None,
        Field(description="recipes that may run but must not feed each other, e.g. ['Recycled']"),
    ] = None,
    supplied: Annotated[
        dict[str, float] | None,
        Field(description="items another plan hands this one, {item: per-minute}"),
    ] = None,
    required: Annotated[
        list[str] | None,
        Field(description="recipes that must make their item; others for it are excluded"),
    ] = None,
    payback_hours: Annotated[
        float | Literal["default"] | None,
        Field(
            description="hours of play extra, slower machines must repay in saved power: 0 "
            "builds plainly; stops 1, 2, 5, 10, 20, 50, 100; 'default' follows the shared "
            "setting. Stored per plan"
        ),
    ] = None,
    overclock_last: Annotated[
        bool | Literal["default"] | None,
        Field(
            description="build a row one machine short, the last one overclocked (1-2 Power "
            "Shards, checked against shards in hand plus those craftable from slugs); 'default' "
            "follows the shared setting"
        ),
    ] = None,
    power_price: Annotated[
        float | Literal["default"] | None,
        Field(
            description="points per MWh the horizon prices power at; omit for the save's "
            "grid mix, 'default' puts a stored plan back on it"
        ),
    ] = None,
    row_overclock: Annotated[
        dict[str, Literal["last", "spread", "default"]] | None,
        Field(
            description="per recipe (exact name or class id): 'last' overclocks that row's last "
            "machine, 'spread' builds one more underclocked machine instead, 'default' follows "
            "overclock_last again. Overrides overclock_last for that row; rows not named keep "
            "their stored choice"
        ),
    ] = None,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    save_as: Annotated[str | None, Field(description="store this request under a name")] = None,
    base_rev: Annotated[
        int | None,
        Field(description="the plan version you read; needed to save over an existing plan"),
    ] = None,
    plan_notes_text: Annotated[str, Field(description="note stored with save_as")] = "",
    for_factory: Annotated[
        str,
        Field(description="factory this plan is built at; also 'auto', 'whole world', 'none'"),
    ] = "",
    site_at: Annotated[
        str | None,
        Field(
            description="with save_as: record where this plan will STAND -- 'x,y[,z]' in "
            "metres, 'me', a factory name, 'slab:<n>' or a run id (the footprint's centre)"
        ),
    ] = None,
    site_yaw_deg: Annotated[
        float,
        Field(description="site orientation: degrees about world Z, positive +X towards +Y"),
    ] = 0.0,
    site_footprint: Annotated[
        str,
        Field(description="site footprint 'WxD' in metres; blank = the layout's own square"),
    ] = "",
    ctx: Context | None = None,
) -> str:
    """Optimise a factory with an LP over this world's unlocked recipes.

    ``sources`` says which resource nodes may feed the plan, as a list of selectors --
    named regions, radii, grid cells, compass directions, or specific node ids::

        ["north"]                        everything in the northern half
        ["region:Northern Forest"]       one named region
        ["near:0,-2000@900"]             within 900 m of (0, -2000) metres
        ["node:BP_ResourceNode30_103"]   one exact node (repeatable)
        ["grid:X3Y4", "grid:X3Y5"]       specific grid cells
        ["north", "resource:Crude Oil"]  narrow a location to one resource

    Omit it and the whole map is in scope. Use search_resource_nodes to discover ids.

    Machine counts are whole buildings at a derived clock: a 52.8 machine-equivalent
    result is reported as 53 machines at 99.6%. That is exact, always a clean ratio,
    and provably the power-optimal way to run that throughput, so ordinary ratio
    underclocking is automatic and needs no parameter.

    ``extractor_clocks`` overclocks the SOURCE NODES only, e.g. [1.0, 1.5, 2.0, 2.5].
    That is the usual play: a node set is fixed, so speed is the only way to get more
    out of it, whereas overclocking production machines mostly burns power. Each
    machine above 100% needs Power Shards, which nothing here counts.

    ``clocks`` is only for asking a different question: passing [0.5, 1.0] lets the
    solver SPREAD throughput over more machines to save power, which is real but not
    free, so each machine is priced at ``machine_cost_mw`` (default 5 MW, just above
    the 2.58 MW/machine that trade was measured to be worth). Overclock modes are not
    offered by default because they consume Power Shards, which nothing here counts.

    objective: max_mw | max_item | min_raw | min_machines | min_power.
    Every item is balanced as an EQUALITY, so a byproduct with no consumer makes the
    plan infeasible rather than silently vanishing.

    ``exports`` is the whitelist of what may leave, and the single most load-bearing
    argument here; default is power only, which is often infeasible for crude oil::

        exports=["MW"]                        power out, plant must be self-powered
        exports=["Plastic", "Rubber"]         items out, NO power export
        exports=["MW", "Plastic", "Rubber"]   both -- MW must be listed explicitly

    Two things worth reading twice. The power token is **MW** (``mw``, ``power`` and
    ``Power`` all work too), not the item name of anything. And ``exports``
    **replaces** the default rather than extending it: naming an item drops MW, which
    is deliberate, because exporting MW also forbids drawing from the existing grid.
    A token matching no item is refused by name rather than solved around.

    ``sloops`` is a BUDGET, not a switch: it is how many Somersloops you will actually
    commit, and the solver spends up to that many wherever they buy the most. Default 0
    spends none, because only a fixed number exist on the whole map and a plan that
    quietly assumed them would be unbuildable. Each one costs 4x power for 2x output on
    its machine, so they are placed one at a time across many machines rather than
    filling one -- output is linear in sloops and power is quadratic, so spreading wins.

    ``payback_hours`` trades machines for power: a row is spread over more, slower machines
    while the power saved repays their build points within that many hours of play, at
    ``power_price`` points per MWh (the save's grid mix unless given). 0 is the plain build.
    ``overclock_last`` builds a row one machine short with the last one overclocked, weighed
    against the horizon and the shards in hand plus those craftable from slugs;
    ``row_overclock`` overrides it per row. Above 0 h,
    max_mw and min_power price each machine at its build points over the horizon instead of
    ``machine_cost_mw``, so they may switch recipes away from scarce buildings. Both are
    stored with the plan and follow the
    shared settings until set; "default" puts a recalled plan back on them. The notes say
    what the next stop would change. Extractors, generators and somersloop rows never move.

    ``required`` names recipes (exact name or class id) that must make their item: every
    other recipe whose main product is that item is excluded. A locked or banned one is
    refused by name.

    ``logistics_items`` pins named items into the belt/pipe table however small their
    flow, as rows ADDED to the ``limit`` biggest by volume. Without it, a two-item
    question can fall off the bottom of a big plan's flow table.

    ``save_as`` stores the request. Over an existing plan it needs ``base_rev``, the
    version you read (list_plans name=): edits to different settings since then merge,
    the same setting changed by someone else is refused as outdated and nothing is saved.

    ``site_at`` says where the plan will STAND. On its own it makes the plan's water
    assumption MEASURED rather than assumed: the terrain at that pad is read and the note
    quotes how much of it is under water, at what level, and how far below the dry ground.
    It never changes a number the LP produced -- how many extractors a body of water holds
    is placement geometry no data here carries. With ``save_as`` it is also recorded, with
    yaw and footprint, so later calls can answer "does what stands there match it"
    (diff_vs_save) and "show me" (show_on_map at='plan:<name>'); a recalled plan that
    was sited is measured at its own site without being told again. Use site_plan to set or
    move the siting of an already-stored plan.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    try:
        plan, pin_notes = _plan_pin(st, plan)
        sources, said = pins.canonical(st, "sources", sources) if sources else (sources, [])
        pin_notes += said
        required, said = pins.canonical(st, "required", required) if required else (required, [])
        pin_notes += said
        if exclude_recipes:
            exclude_recipes, said = pins.canonical(st, "exclude_recipes", exclude_recipes)
            pin_notes += said
    except (KeyError, pins.PinError) as exc:
        return f"! {_refusal(exc)}; nothing solved"

    required_ids, refused = _resolve_required(required)
    if refused:
        return refused
    row_overclock, refused = _resolve_rows(row_overclock)
    if refused:
        return refused

    existing, refusal = _save_target(st, save_as, base_rev) if save_as else (None, "")
    if refusal:
        return refusal
    if existing is not None and base_rev is None:
        return _needs_base(existing.name, existing.rev, "nothing saved")
    if plan and existing is not None and plan.strip().casefold() == save_as.strip().casefold():
        if st.plans.find(existing.key) is not None:
            plan = existing.key

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        required=required_ids,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
        recycle_once=recycle_once,
        supplied=supplied,
        **_power_args(payback_hours, overclock_last, power_price),
        row_overclock=row_overclock,
    )
    if refused := _power_refusal(supplied):
        return refused
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    plan_notes = [*pin_notes, *plan_notes]
    objective = plan_kwargs.get("objective") or objective

    # Its own pair, never written back over the arguments: a recalled plan's site is
    # measured here, and re-saving that plan must not turn its stored yaw and z into the
    # defaults this call happens to carry.
    measure_at, measure_pad = siting_mod.plan_site_args(st, plan, site_at or "", site_footprint)
    report = build_plan_report(
        g,
        st,
        plan_kwargs,
        logistics_items,
        objective=objective,
        site_at=measure_at,
        site_footprint=measure_pad,
    )

    # Persistence is an interface side effect, not part of the answer: the plan is stored
    # here and the resulting sentence handed to the presenter like any other note.
    save_as_note = ""
    tail = ""
    if save_as and report.prepared.failure is None:
        plan_id = report.prepared.request.plan_id
        # What the selectors resolved to, stored WITH the request. plan_id hashes the
        # extractor census, so it moves when the world does -- it cannot move when a
        # selector starts meaning a different part of the map.
        field = prov.record(g, st, plan_kwargs.get("sources"))
        when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
        sit = None
        if site_at:
            # Resolved BEFORE the store is touched, so a bad coordinate refuses the whole
            # save rather than leaving a half-written plan behind.
            try:
                sit = siting_mod.build_siting(
                    g,
                    st,
                    at=site_at,
                    yaw_deg=site_yaw_deg,
                    footprint=site_footprint,
                    solution=report.prepared.solution,
                    plan_kwargs=plan_kwargs,
                    when=when,
                )
            except ValueError as exc:
                return f"! {exc} -- nothing saved"
        path = PlanLog.dir_for(st.world_id)
        pinned = "; ".join(f"{e['selector']}={e['count']} node(s)" for e in field["selectors"])
        recall = (
            f"Recall with plan={(existing.name if existing else save_as.strip())!r} on plan_factory, plan_layout or diff_vs_save"
            + (f". Field recorded: {pinned}" if pinned else "")
            + (f". Sited: {sit.describe()}" if sit is not None else "")
        )
        if existing is None:
            try:
                made = _save_new(
                    st,
                    save_as,
                    plan_kwargs,
                    logistics_items,
                    (plan_notes_text, _factory_value(for_factory) or "", when),
                    field,
                    plan_id,
                    sit,
                    ctx,
                )
            except LockTimeout:
                return BUSY
            except PlanLogError as exc:
                return f"! {exc} -- nothing saved"
            save_as_note = (
                f'saved as "{made.state.name}" v1 (key {made.key}, plan_id {plan_id}) in '
                f"{path}. {recall}"
            )
            if base_rev is not None:
                save_as_note += f". base_rev={base_rev} was ignored: this is a new plan"
        else:
            recalled = st.plans.find(plan) if plan else None
            same = recalled is not None and recalled.key == existing.key
            pushed, tail = _save_over(
                st,
                existing,
                base_rev,
                plan_kwargs,
                logistics_items,
                (plan_notes_text, _factory_value(for_factory)),
                sit,
                ctx,
                overrides_of(supplied) if same else None,
            )
            if pushed is not None:
                save_as_note = f'saved over "{pushed.state.name}" in {path}. {recall}'
    elif site_at:
        save_as_note = (
            "site_at was measured but not RECORDED: a siting lives on a STORED plan. Pass "
            "save_as=<name> here, or site an existing plan with site_plan"
        )
    if not save_as:
        recalled = st.plans.find(plan) if plan else None
        kept = logistics_items
        if kept is None and recalled is not None:
            kept = list(_log(st).state(recalled.key, recalled.rev).args.logistics_items)
        journal.append(
            st.world_id,
            "plan.solve",
            actor=actor(ctx),
            sav=_sav(st),
            tool="plan_factory",
            plan=recalled.key if recalled is not None else None,
            rev=recalled.rev if recalled is not None else None,
            args=_journal_args(plan_kwargs, kept),
            text=_solve_text(plan_kwargs, report.prepared.failure is None, recalled),
        )

    if save_as_note.startswith("saved"):
        plan_notes = [n.replace(UNSAVED_OVERRIDE, "(saved by this call)") for n in plan_notes]
    elif save_as:
        plan_notes = [n.replace(UNSAVED_OVERRIDE, "(not saved: see below)") for n in plan_notes]
    out = render_plan_factory(
        g,
        st,
        report,
        objective=objective,
        only_free_nodes=only_free_nodes,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
        save_as_note=save_as_note,
    )
    return f"{out}\n{tail}" if tail else out


@mcp.tool(structured_output=False)
def plan_layout(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    show: Annotated[
        str, Field(description="floors | blocks | buses | trunks | materials | sites")
    ] = "floors",
    detail: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    # Without these plan_layout re-solves at defaults and schematises a DIFFERENT plan
    # than the one being laid out -- measured at 15,043 MW against the 83,737 MW plan it
    # was asked to draw, because base extraction is a sixth of overclocked.
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
    payback_hours: Annotated[
        float | Literal["default"] | None,
        Field(
            description="hours of play extra, slower machines must repay in saved power: 0 "
            "builds plainly; stops 1, 2, 5, 10, 20, 50, 100; 'default' follows the shared "
            "setting. Stored per plan"
        ),
    ] = None,
    overclock_last: Annotated[
        bool | Literal["default"] | None,
        Field(
            description="build a row one machine short, the last one overclocked (1-2 Power "
            "Shards, checked against shards in hand plus those craftable from slugs); 'default' "
            "follows the shared setting"
        ),
    ] = None,
    power_price: Annotated[
        float | Literal["default"] | None,
        Field(
            description="points per MWh the horizon prices power at; omit for the save's "
            "grid mix, 'default' puts a stored plan back on it"
        ),
    ] = None,
    row_overclock: Annotated[
        dict[str, Literal["last", "spread", "default"]] | None,
        Field(
            description="per recipe (exact name or class id): 'last' overclocks that row's last "
            "machine, 'spread' builds one more underclocked machine instead, 'default' follows "
            "overclock_last again. Overrides overclock_last for that row; rows not named keep "
            "their stored choice"
        ),
    ] = None,
    sites: Annotated[
        dict[str, list[str]] | None,
        Field(
            description=(
                'show="sites": {"rig": ["Heavy Oil Residue", ...], "hall": ["MW"]} '
                "-- MW/power claims every generator"
            )
        ),
    ] = None,
    max_floor_foundations: Annotated[
        int,
        Field(description="cap a deck at this many 8m foundations; 0 = one stage per deck"),
    ] = 0,
    order_floors_by: Annotated[
        str, Field(description='"chain" (build order) or "head" (minimise fluid lift)')
    ] = "chain",
    belt_tier: Annotated[
        str, Field(description="belt tier name; blank = the fastest you have unlocked")
    ] = "",
    pipe_tier: Annotated[
        str, Field(description="pipe tier name; blank = the fastest you have unlocked")
    ] = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    factory: Annotated[
        str | None,
        Field(description="fit the layout against this factory's existing platform"),
    ] = None,
    ctx: Context | None = None,
) -> str:
    """Turn a plan into a buildable schematic: blocks, buses and floors.

    Same arguments as plan_factory, plus ``show``: "floors" (default, the stack),
    "blocks" (every module with its size and rates), "buses" (item flows),
    "trunks" (which resource nodes share each pipe or belt run into the site),
    "materials" (what the whole thing costs to build, machines plus deck), or
    "sites" (cut the plan into named modules and report what crosses between them).

    This is a SCHEMATIC, not a blueprint. It gives modules, connections, floor
    assignment and a space budget. It deliberately does NOT give world coordinates or
    belt routing -- there is no terrain data here, so those would be invented.

    Blocks are split by throughput: 46 Refineries needing 1380 m3/min of crude cannot
    share one manifold when a Mk2 pipe carries 600, so that is 3 blocks. Floors follow
    chain depth, with a logistics deck between each pair of production floors.
    """
    g = game()
    if gone := retired(("detail", detail, "show")):
        return gone
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    row_overclock, refused = _resolve_rows(row_overclock)
    if refused:
        return refused
    tiers = resolve_tiers(g, st, belt_tier, pipe_tier)
    if tiers.errors:
        return render_layout(
            g,
            st,
            LayoutReport(prepared=None, tiers=tiers),
            objective=objective,
            show=show,
            limit=limit,
        )

    # Same solve-shaping arguments as plan_factory, so a layout can be asked for
    # directly rather than only via a saved plan.
    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        water_extractors=water_extractors,
        sloops=sloops,
        # Into the SCENARIO, not just into build_layout. Passing a tier to the schematic
        # while the solve kept the default is the same drift 8.5a documents: the trunk
        # view reads sc.pipe_m3min, so pipe_tier="Mk1" changed the block split and left
        # the trunk count untouched, describing two different plants in one response.
        #
        # Only when the caller ASKED for a tier, though. Passing the resolved default
        # through made every recalled plan report "overridden this call: belt_ipm,
        # pipe_m3min" -- an override the user never made, which is exactly the kind of
        # noise that trains a reader to skip the override line that does matter.
        belt_ipm=tiers.belt_ipm if tiers.asked_belt else None,
        pipe_m3min=tiers.pipe_m3min if tiers.asked_pipe else None,
        **_power_args(payback_hours, overclock_last, power_price),
        row_overclock=row_overclock,
    )
    if refused := _power_refusal(supplied):
        return refused
    try:
        plan, pin_notes = _plan_pin(st, plan)
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    plan_notes = [*pin_notes, *plan_notes]
    objective = plan_kwargs.get("objective") or objective

    try:
        report = build_layout_report(
            g,
            st,
            plan_kwargs,
            tiers,
            objective=objective,
            show=show,
            sites=sites,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
            factory=factory,
            plan=plan,
        )
    except SelectorError as exc:
        return f"! {exc}"
    _journal_view(st, plan, "plan_layout", ctx)
    if report.prepared is not None and report.prepared.ok:
        plan_notes += _payback_notes(g, st, report.prepared)

    return render_layout(
        g,
        st,
        report,
        objective=objective,
        show=show,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


def _shared_power(biomass: bool | None) -> tuple[bool, str, list[str]]:
    """``biomass`` or the shared setting, the shared stage headroom, and any unread note."""
    head, unread = shared("stage_headroom")
    notes = [unread] if unread else []
    if biomass is None:
        biomass, _ = shared("biomass")
    return biomass, head, notes


@mcp.tool(structured_output=False)
def diff_vs_save(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    show_cost: bool = True,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    stage: Annotated[
        int | None,
        Field(description="one startup stage's delta; 0 for the stage overview"),
    ] = None,
    factory: Annotated[
        str | None,
        Field(description="count this factory as built; also 'auto', 'world' or 'none'"),
    ] = None,
    biomass: Biomass = None,
    ctx: Context | None = None,
) -> str:
    """What to change to get from the factory you have to the one plan_factory plans.

    Takes exactly plan_factory's arguments and re-solves, because the server keeps no
    state. Both tools print a plan id hashed over the arguments AND the save-derived
    solve inputs, so two responses carrying the same id are provably the same plan.

    Machines are matched by IDENTITY, never by position: a manufacturer on (building,
    recipe), a generator on its building alone since its fuel is piped in rather than
    set on the machine, an extractor on the node it occupies. A Refinery running some
    other recipe is busy, not spare, so it never counts toward the plan.

    Actions are ordered free-first -- UNPAUSE, then SETRECIPE on machines that produce
    nothing today, then BUILD. Stages follow the plan's own chain depth and the power
    arithmetic is INCREMENTAL, charging only the machines you have yet to place. Where
    a machine cannot be identified at all (Water Extractors have no recipe and no
    resolvable node) the answer is a RANGE, never a number.

    Recall a stored plan with ``plan=`` and the diff is also grouped by STARTUP STAGE --
    the same partition commission_plan emits -- so it answers "which stage am I in".
    ``stage=<n>`` narrows to one stage's delta; ``stage=0`` asks for the overview
    without a stored plan, at the cost that the numbering moves when the arguments do.

    Built and energised are DIFFERENT states and the save separates them in one
    direction only: a machine that produced in the last 300s window certainly had
    power, while one that did not may be unpowered, starved, blocked or idle. Grid
    membership is not persisted at all, so a stage is never reported as "unpowered" --
    only as built with nothing proven running, which is exactly what a finished but
    not-yet-energised block looks like.

    Saves are read-only: this never proposes writing one, and there is no dismantle
    action. Machines standing among the plan but not in it are listed for you to judge.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    try:
        plan, pin_notes = _plan_pin(st, plan)
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    plan_notes = [*pin_notes, *plan_notes]
    objective = plan_kwargs.get("objective") or objective
    stored = _recalled(st, plan)
    biomass, default, unread = _shared_power(biomass)
    plan_notes += unread

    try:
        report = build_diff_report(
            g,
            st,
            plan_kwargs,
            objective=objective,
            plan=plan,
            plan_name=plan_name,
            stage=stage,
            factory=_factory_value(factory),
            biomass=biomass,
            headroom_mw=stored.headroom_mw if stored is not None else None,
            stored=stored,
            default=default,
        )
    except SelectorError as exc:
        return f"! {exc}"
    view = {"view": "track", "stage": stage if stage and stage >= 1 else None, "section": "stages"}
    _journal_view(st, plan, "diff_vs_save", ctx, view)
    moved = _renumbered(st, stored, report.tracking)
    if moved:
        plan_notes = [moved, *plan_notes]

    return render_diff(
        g,
        st,
        report,
        objective=objective,
        limit=limit,
        show_cost=show_cost,
        stage=stage,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


@mcp.tool(structured_output=False)
def explain_byproducts(
    objective: str = "max_mw",
    target_item: str | None = None,
    item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 12,
) -> str:
    """Explain which byproducts stall a plan, and what can legally consume them.

    Every item balance is an equality, so a byproduct with no consumer makes a plan
    INFEASIBLE rather than silently vanishing. This says WHICH item is stuck, whether
    it can be sunk (solids only -- a fluid must be consumed exactly or packaged
    first), and which recipes would absorb it, split into ones this world has
    unlocked and ones it does not.

    Pass ``item`` to focus on one byproduct instead of the whole plan.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    return byproducts_text.explain(
        g,
        st,
        objective=objective,
        target_item=target_item,
        item=_item_id(item) if item else None,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        limit=render.clamp(limit, default=12),
    )


@mcp.tool(structured_output=False)
def compare_recipe_options(
    item: str,
    rate: float = 100.0,
    per_resource: str | None = None,
    outlets: list[str] | None = None,
    allow_sinks: bool = True,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
) -> str:
    """Rank whole ROUTES to make an item by what each actually costs.

    Not a recipe list -- alternates_for_item already does that. Each route is solved
    end to end with the LP, so the comparison is Crude -> Alt HOR -> Diluted Fuel
    against Crude -> Fuel, priced in raw resource per unit, whole buildings, net
    power, and byproducts needing an outlet.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    iid = _item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    result = compare.compare_routes(
        g,
        st,
        iid,
        rate=rate,
        allow_sinks=allow_sinks,
        outlets=outlets,
        per_resource=_item_id(per_resource) if per_resource else None,
    )
    return render_comparison(result, limit=render.clamp(limit, default=10))


@mcp.tool(structured_output=False)
def bom(
    item: str,
    qty: float = 60.0,
    allow_sinks: bool = True,
    outlets: list[str] | None = None,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    offset: int = 0,
) -> str:
    """Flattened bill of materials: total raw and intermediate rates for qty/min of an item.

    ``qty`` is a RATE, per minute. Solved by the LP, never by expanding the recipe
    tree: Recycled Plastic and Recycled Rubber form a real 2-cycle, so an expansion
    has no correct depth limit. Every row names the recipe chosen for that item,
    because alternates change the totals materially.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        result = bom_mod.build_bom(
            g,
            st,
            item,
            qty=qty,
            allow_sinks=allow_sinks,
            outlets=outlets,
            exclude_recipes=exclude_recipes,
            only_recipes=only_recipes,
        )
    except ValueError as exc:
        return str(exc)
    return render_bom(result, limit=render.clamp(limit, default=20), offset=max(0, offset))


@mcp.tool(structured_output=False)
def commission_plan(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    headroom_mw: Annotated[
        float | None,
        Field(description="grid power free for startup; default reads it from the save"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    biomass: Biomass = None,
    ctx: Context | None = None,
) -> str:
    """In what order to switch a built plant on, without blowing the fuse.

    This is a STARTUP order, not a build order, and the difference removes most of the
    problem. Building costs materials, not power -- a machine draws only when it runs --
    so the whole plant can be constructed at leisure, drawing nothing, and then energised
    block by block. Nothing here tells you what to build first.

    The constraint is one line, and it is hard: at every step, energised consumer draw
    must stay under the headroom plus generation from generators already burning fuel.
    Exceeding it in Satisfactory does not degrade gracefully -- the fuse blows and the
    whole grid stops until it is reset by hand, including the plant that was feeding it.

    Generators are free to energise (0 MW draw, read from the dump), so a wave costs its
    consumers and refunds its generators, and that refund pays for the next wave.

    Takes plan_factory's arguments, or recall a saved plan with ``plan=``.
    """
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
    )
    try:
        plan, pin_notes = _plan_pin(st, plan)
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    plan_notes = [*pin_notes, *plan_notes]
    objective = plan_kwargs.get("objective") or objective

    stored = _recalled(st, plan)
    biomass, default, unread = _shared_power(biomass)
    plan_notes += unread
    report = build_commission_report(
        g,
        st,
        plan_kwargs,
        headroom_mw,
        objective=objective,
        biomass=biomass,
        stored=stored,
        default=default,
    )
    view = {"view": "track", "stage": None, "section": "startup"}
    _journal_view(st, plan, "commission_plan", ctx, view)
    moved = _renumbered(st, stored, report.tracking) if headroom_mw is None else ""
    if moved:
        plan_notes = [moved, *plan_notes]

    return render_commission(
        g,
        st,
        report,
        objective=objective,
        limit=limit,
        offset=offset,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


@mcp.tool(structured_output=False)
def rank_unlocks(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    query: Annotated[
        str | None, Field(description="only test alternates whose name matches")
    ] = None,
    search: Annotated[str | None, Field(description="retired -- write query= instead")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 15,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
) -> str:
    """What every locked alternate recipe would be worth to THIS plan.

    One counterfactual per candidate: solve the plan, solve it again with the recipe
    added, report the difference. It answers "which unlock should I chase" with a number
    in the plan's own units instead of a tier list, because a recipe's worth depends
    entirely on what you already have.

    A zero is an answer. Most candidates change nothing, and "you are not missing anything
    here" is a decision -- it is otherwise reached by walking the recipe tree by hand.

    Deltas are an UPPER bound: a candidate needing a machine you have not built is judged
    as if you had it, and the machine is named. Alternates currently offered by a pending
    hard drive are flagged, which is the difference between "worth having" and "claimable
    now".
    """
    if gone := retired(("search", search, "query")):
        return gone
    g = game()
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
    )
    try:
        plan, pin_notes = _plan_pin(st, plan)
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    plan_notes = [*pin_notes, *plan_notes]
    objective = plan_kwargs.get("objective") or objective

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        return render.envelope(
            f"# {prepared.failure.headline} -- nothing to rank against",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )

    pool = st.locked_alternates
    if query:
        needle = query.strip().casefold()
        pool = [r for r in pool if needle in r.name.casefold()]
        if not pool:
            return f"! no LOCKED alternate matches {query!r}"

    sweep = sweep_unlocks(prepared.request, st, pool)
    # The candidates by id, so the granted-by cell is answered off the same recipe the
    # sweep measured rather than a second lookup that could miss.
    swept = {r.cls: r for r in pool}
    # Which of these you could claim today. A recipe worth 14,540 MW that is sitting in a
    # pending drive is a different instruction from one that needs a drive you have not
    # found yet.
    on_offer: dict[str, int] = {}
    for offer in st.hard_drive_offers:
        for option in offer.options:
            for recipe in option["recipes"]:
                on_offer[recipe.cls] = offer.hard_drive_id

    movers = sweep.movers
    rows = [
        (
            render.num(r.gain),
            f"{r.gain / sweep.baseline:+.1%}" if sweep.baseline else "",
            r.name[:34],
            # The work you actually do, in the same words search_recipes and recipe_detail
            # use: a hard drive and a milestone are different evenings. Never truncated --
            # a cut-off schematic name is a name the reader cannot look up.
            granted_by_label(st.game, swept[r.recipe], width=40),
            render.num(r.machines),
            f"drive {on_offer[r.recipe]}" if r.recipe in on_offer else "",
            ", ".join(r.needs)[:18],
            ", ".join(r.activates)[:40],
        )
        for r in movers[: render.clamp(limit, default=15)]
    ]
    # A ranking, so no offset: what falls off the bottom is what changed this plan least.
    notes = [*plan_notes]
    notes.append(
        f"{sweep.tried} locked alternate(s) tested, {len(movers)} changed this plan. "
        "The rest are worth nothing HERE -- which is a result, not a gap: it is the "
        "answer you would otherwise get by walking the tree by hand"
    )
    notes.append(
        "deltas are an UPPER bound: a candidate is solved as if any machine it needs "
        "already existed, and that machine is named in 'needs'"
    )
    if sweep.unsolved:
        # Adding a recipe only ever widens the LP, so an unsolved counterfactual is a
        # solver failure and never a verdict on the recipe.
        shown = sweep.unsolved[:4]
        notes.append(
            f"INFEASIBLE: {len(sweep.unsolved)} candidate(s) did not solve with the recipe "
            "added, so their worth is UNKNOWN rather than zero -- "
            + ", ".join(r.name for r in shown)
            + (f" (+{len(sweep.unsolved) - len(shown)} more)" if len(sweep.unsolved) > 4 else "")
        )
    notes.append(
        "'activates' is what the gain DEPENDS on -- processes the counterfactual switches "
        "on that this plan does not currently use. A headline number that turns on "
        "reintroducing a chain you deleted is a decision, not a free win"
    )
    # The trap this tool set for its own author. Run with ad-hoc arguments it measures a
    # DIFFERENT plant from the one saved, and the answers genuinely differ: Turbo Blend
    # Fuel is worth +13.6% against unconstrained Spire Coast and exactly nothing against
    # the saved plan, which bans Turbofuel and coal generators.
    if not plan and st.plans.plans:
        notes.append(
            "measured against the ARGUMENTS GIVEN, not against a saved plan. This world "
            f"has {len(st.plans.plans)} saved plan(s) ("
            + ", ".join(x.name for x in st.plans.plans[:3])
            + ") whose exclusions may forbid these gains -- pass plan=<name> to rank "
            "against the architecture you actually chose"
        )
    claimable = [r for r in movers if r.recipe in on_offer]
    if claimable:
        notes.append(
            "claimable NOW from a pending hard drive: "
            + ", ".join(f"{r.name} (drive {on_offer[r.recipe]})" for r in claimable[:4])
            + " -- use advise_hard_drive_pick for that drive's full comparison"
        )
    return render.envelope(
        "\n".join(
            [
                f"# unlock value for {objective}" + (f" ({plan_name})" if plan_name else ""),
                f"# {st.age_note}",
                (
                    f"baseline={render.num(sweep.baseline)}  candidates={sweep.tried}  "
                    f"movers={len(movers)}"
                ),
            ]
        ),
        render.table(
            (
                "gain",
                "vs base",
                "alternate",
                "granted by",
                "machines",
                "on offer",
                "needs",
                "activates",
            ),
            rows,
            total=len(movers),
            limit=render.clamp(limit, default=15),
            hint="raise limit, or narrow with query= -- a ranking has no offset",
        ),
        notes,
    )


def _history(log: PlanLog, found, since: int | None, limit: int) -> str:
    names = _recipe_names()
    now = time.time()
    commits = log.commits(found.key)
    undone = manage.undone_by(commits)
    shown = [c for c in reversed(commits) if c.rev > (since or 0)]
    lines = []
    for commit in shown[:limit]:
        flags = [f"{_age(now - commit.ts)} ago"]
        if commit.merged_over:
            flags.append("merged over " + ",".join(f"v{r}" for r in commit.merged_over))
        if commit.rev in undone:
            flags.append(f"undone in v{undone[commit.rev]}")
        lines.append(f"{_commit_text(commit, names)}  ({'; '.join(flags)})")
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


@mcp.tool(structured_output=False)
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
    ctx: Context | None = None,
) -> str:
    """One plan's versions, newest first: who changed what, from chat or from the page.

    ``undo=<v>`` writes a new version that reverses that one; ``restore=<v>`` writes a new
    version equal to that one. Both take ``base_rev`` and merge like any other edit. A
    forgotten plan is found too, so its forget can be undone.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        name, _echo = _plan_pin(st, name)
    except KeyError as exc:
        return f"! {exc.args[0]}"
    log = _log(st)
    found = log.find(name, include_forgotten=True)
    if found is None:
        return _unknown(st, name)
    if undo is None and restore is None:
        try:
            return _history(log, found, since, render.clamp(limit, default=15))
        except PlanLogError as exc:
            return f"! {exc}"
    if undo is not None and restore is not None:
        return "! pass undo= or restore=, not both; nothing changed"
    verb = "undone" if undo is not None else "restored"
    if base_rev is None:
        return _needs_base(found.name, found.rev, f"nothing {verb}")
    who, sav, stamp = actor(ctx), _sav(st), _stamp(st)
    if undo is not None:
        pushed, text = _write(
            found.name,
            "nothing undone",
            lambda: log.undo(found.key, base_rev, undo, actor=who, sav=sav, stamp=stamp),
        )
        done = f"undid v{undo}"
    else:
        pushed, text = _write(
            found.name,
            "nothing restored",
            lambda: log.restore_to(found.key, base_rev, restore, actor=who, sav=sav, stamp=stamp),
        )
        done = f"restored v{restore}"
    if pushed is None:
        return text
    changes = _ops_text(pushed.applied, _recipe_names()) or "no change"
    return f'# {done} of plan "{pushed.state.name}": {changes}\n{text}'


_cursor: dict[str, tuple[float, dict[str, int]]] = {}


def _page_focus(world_id: str) -> tuple[dict | None, bool]:
    """What the page last said it had open, and whether its heartbeat is fresh."""
    from ....domain.planning import focus

    found = focus.read(world_id)
    return found, focus.is_open(found)


def _short(token: str) -> str:
    return token[:8] + "…" if len(token) > 8 else token


def _pin_text(pin: dict) -> str:
    text = f"{pin['id']} {pin['text']}"
    if pin["label"]:
        text += f" “{pin['label']}”"
    if pin["gone"]:
        text += " (gone)"
    return _cut(text, CONTEXT_PIN_WIDTH)


def _pins_line(rows: list[dict]) -> str:
    if not rows:
        return "pins: none"
    shown = sorted(rows, key=lambda p: p["n"])[-CONTEXT_PINS:]
    line = "pins: " + " · ".join(_pin_text(p) for p in shown)
    if len(rows) > CONTEXT_PINS:
        line += f" (+{len(rows) - CONTEXT_PINS} more)"
    return line


def _selected_pin(focus: dict, rows: list[dict]) -> str:
    picked = focus.get("selection")
    if not isinstance(picked, dict) or not picked.get("kind") or not picked.get("label"):
        return ""
    found = pins.match(rows, str(picked["kind"]), str(picked.get("ref") or ""), focus.get("plan"))
    return f" ({found['id']})" if found else ""


def _focus_line(focus: dict, log: PlanLog) -> str:
    parts = [str(focus.get("view") or "?")]
    if focus.get("plan"):
        try:
            state = log.find(str(focus["plan"]), include_forgotten=True)
        except PlanLogError:
            state = None
        rev = focus.get("rev")
        label = f'"{state.name}"' if state is not None else f"plan {focus['plan']}"
        label += f" v{rev}" if rev else ""
        if state is not None and rev and rev != state.rev:
            label += f" (head v{state.rev})"
        parts.append(label)
    elif focus.get("dash") and focus["dash"] != parts[0]:
        parts.append(str(focus["dash"]))
    tab = str(focus.get("tab") or "")
    if tab and tab != str(focus.get("dash") or "").split("/")[0]:
        parts.append(tab)
    line = "focus: " + " › ".join(parts)
    picked = focus.get("selection")
    if isinstance(picked, dict) and picked.get("label"):
        line += f'   selected: {picked.get("kind") or "item"} "{picked["label"]}"'
        if picked.get("ref"):
            line += f" ({picked['ref']})"
    return line


def _plan_news(log: PlanLog, cursor, names: dict[str, str], me: int) -> tuple[list[str], dict]:
    heads = {s.key: s for s in log.heads(include_forgotten=True)}
    fresh: list[tuple[str, Commit]] = []
    for key in heads:
        after = cursor[1].get(key, 0) if cursor else 0
        fresh += [(key, c) for c in log.commits(key, since=after) if c.actor.pid != me]
    if cursor is None:
        fresh = sorted(fresh, key=lambda kc: kc[1].ts)[-FIRST_LOOK:]
    by_plan: dict[str, list[Commit]] = {}
    for key, commit in fresh:
        by_plan.setdefault(key, []).append(commit)
    lines = []
    order = sorted(by_plan, key=lambda k: by_plan[k][-1].ts, reverse=True)
    for key in order[:CONTEXT_PLANS]:
        commits = sorted(by_plan[key], key=lambda c: c.rev)
        who = ", ".join(dict.fromkeys(c.actor.display() for c in commits))
        items = [
            f"v{c.rev} " + (_ops_text(c.ops, names) or c.note or "recorded")
            for c in commits[-CONTEXT_COMMITS:]
        ]
        if len(commits) > CONTEXT_COMMITS:
            items.insert(0, f"(+{len(commits) - CONTEXT_COMMITS} more)")
        state = heads[key]
        gone = " (forgotten)" if state.forgotten else ""
        start = f"v{commits[0].rev - 1}" if commits[0].rev > 1 else "new"
        lines.append(f'"{state.name}"{gone} {start} -> v{state.rev} by {who}: ' + " · ".join(items))
    if len(order) > CONTEXT_PLANS:
        lines.append(f"(+{len(order) - CONTEXT_PLANS} more plans: plan_log name=<plan>)")
    return lines, {key: state.rev for key, state in heads.items()}


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ask_text(row: dict, adv_ids: dict[str, str] | None = None) -> str:
    about = row["about"]
    text = f"{row['id']} {_quoted(row['text'])} about {about['kind']} {_quoted(about['label'])}"
    if about["kind"] == "advice" and (adv_ids or {}).get(about["ref"]):
        text += f" ({adv_ids[about['ref']]})"
    if row["plan_name"] and about["kind"] != "plan":
        text += f" in {_quoted(row['plan_name'])}"
    if about.get("rev"):
        text += f" v{about['rev']}"
    if row["state"] == "seen":
        text += " (seen)"
    return _cut(text, CONTEXT_ASK_WIDTH)


def _asks_lines(world_id: str, who: str, me, adv_ids: dict[str, str] | None = None) -> list[str]:
    """The ``asks`` line and its hint, marking every listed open ask seen."""
    try:
        waiting = [r for r in asks.live(world_id) if r["state"] in ("open", "seen")]
    except (NewerSchema, OSError) as exc:
        return [f"asks: unreadable ({type(exc).__name__})"]
    if not waiting:
        return ["asks: none waiting"]
    shown = waiting[-CONTEXT_ASKS:]
    line = f"asks ({len(waiting)} waiting): " + " · ".join(_ask_text(r, adv_ids) for r in shown)
    if len(waiting) > CONTEXT_ASKS:
        line += f" (+{len(waiting) - CONTEXT_ASKS} more)"
    fresh = [r["n"] for r in shown if r["state"] == "open"]
    try:
        seen = asks.mark_seen(world_id, fresh, who) if fresh else []
    except (LockTimeout, NewerSchema, OSError):
        seen = []
    if seen:
        journal.append(
            world_id,
            "ask.seen",
            actor=me,
            args={"n": seen},
            text="chat saw " + ", ".join(f"ask:{n}" for n in seen),
        )
    ids = ", ".join(f'"{r["id"]} <answer>"' for r in shown[:2])
    more = ", …" if len(shown) > 2 else ""
    hint = (
        f"answer them, then ui_context(answered=[{ids}{more}]) marks them done on the page "
        "with that one-line answer"
    )
    return [line, hint]


def _answer(world_id: str, answered: list[str], who: str, me) -> list[str]:
    """Mark ``answered`` asks done; the ``marked answered`` line and one line per refusal."""
    lines, wanted, said = [], [], {}
    try:
        data = asks.read(world_id)
    except NewerSchema as exc:
        return [f"! asks were saved by a newer version (schema {exc.found}); nothing marked"]
    top = data["next"] - 1
    by_n = {a["n"]: a for a in data["asks"]}
    for raw in answered:
        hit = asks.parse_answer(raw)
        n = hit[0] if hit else None
        if n is None:
            lines.append(f"! {raw!r} is not an ask id (ask:N)")
        elif n not in by_n:
            lines.append(f"! {asks.AskMissing(n, top=top)}")
        elif by_n[n].get("deleted"):
            lines.append(f"! {asks.AskMissing(n, deleted=True)}")
        else:
            wanted.append(n)
            if hit[1]:
                said[n] = hit[1]
    try:
        asks.mark_answered(world_id, wanted, who, said)
    except asks.AskMissing as exc:
        return [f"! {exc}; nothing marked", *lines]
    except (LockTimeout, NewerSchema, OSError) as exc:
        return [f"! asks are busy ({type(exc).__name__}); nothing marked", *lines]
    if wanted:
        journal.append(
            world_id,
            "ask.answered",
            actor=me,
            args={"n": wanted},
            text="chat answered "
            + ", ".join(f"ask:{n}" + (f": {said[n]}" if n in said else "") for n in wanted),
        )
        lines.insert(0, "marked answered: " + ", ".join(f"ask:{n}" for n in wanted))
    return lines


def _hide_advice(st, dismissed: list[str], me) -> list[str]:
    """Hide the advisories chat was asked to; one line for what was hidden, one per refusal."""
    lines, done = [], []
    try:
        cur = advice.current(st)
    except NewerSchema as exc:
        return [f"! hidden advisories were saved by a newer version (schema {exc.found})"]
    for raw in dismissed:
        hit = advice_text.parse_hide(raw)
        adv = cur.find(hit[0]) if hit else None
        if hit is None:
            lines.append(f'! {raw!r} is not an advisory id ("adv:3f9a" or "adv:3f9a snooze")')
            continue
        if adv is None:
            lines.append(f"! {hit[0]} does not fire on this save")
            continue
        _id, mode, hours = hit
        try:
            advice_store.hide(
                st.world_id,
                adv,
                mode,
                play_s=cur.play_s,
                by=me.to_dict(),
                hours=hours,
                firing=[a.key for a in cur.items],
            )
        except (advice_store.AdviceError, LockTimeout, NewerSchema, OSError) as exc:
            lines.append(f"! {adv.id} not hidden: {exc}")
            continue
        verb = "dismissed" if mode == "dismiss" else "snoozed"
        what = verb if mode == "dismiss" else f"{verb} for {hours:g} h of play"
        journal.append(
            st.world_id,
            "advice.hide",
            actor=me,
            args={"id": adv.id, "key": adv.key, "mode": mode},
            text=f"chat {verb} {adv.id} {advice.WORDS[adv.kind]}: {adv.text}",
        )
        done.append(f"{adv.id} ({what})")
    if done:
        lines.insert(0, "hidden on the page: " + ", ".join(done))
    return lines


def _advice_lines(st) -> tuple[list[str], dict[str, str]]:
    """The ``advice`` line and its hint, and every advisory key's id for the asks line."""
    try:
        cur = advice.current(st)
    except Exception as exc:
        return [f"advice: unavailable ({type(exc).__name__})"], {}
    return advice_text.context_lines(cur), {a.key: a.id for a in cur.items}


def _journal_who(raw: dict | None) -> str:
    who = Actor.from_dict(raw)
    return f"{who.display()} (other session)" if who.kind == "chat" else who.display()


def _journal_news(world_id: str, cursor, me: int) -> tuple[list[str], float]:
    since = cursor[0] if cursor else 0.0
    entries = journal.read(world_id, since_ts=since, limit=500)
    last = max([since, *(float(e.get("ts") or 0) for e in entries)])
    theirs = [e for e in entries if int((e.get("actor") or {}).get("pid") or 0) != me]
    if cursor is None:
        theirs = theirs[-FIRST_LOOK:]
    lines = []
    if len(theirs) > CONTEXT_JOURNAL:
        lines.append(f"  · journal: (+{len(theirs) - CONTEXT_JOURNAL} earlier)")
    for entry in theirs[-CONTEXT_JOURNAL:]:
        when = datetime.fromtimestamp(float(entry.get("ts") or 0), UTC).astimezone()
        when = when.strftime("%H:%M")
        text = _cut(str(entry.get("text") or ""), 160)
        lines.append(f"  · journal: {when} {_journal_who(entry.get('actor'))} {text}")
    return lines, last


@mcp.tool(structured_output=False)
def ui_context(
    save: str | None = None,
    world: str | None = None,
    answered: Annotated[
        list[str] | None,
        Field(description='ask:N ids you have answered, each may add a line: "ask:7 <answer>"'),
    ] = None,
    dismissed: Annotated[
        list[str] | None,
        Field(description='adv: ids to hide on the page; "adv:3f9a snooze" hides for 1 h of play'),
    ] = None,
    ctx: Context | None = None,
) -> str:
    """What the web page has open, and what changed in plans since this session last looked.

    Call it first when the user says "this", "here" or "what I have open", or quotes an
    ask: or pin: id: it names the page's view, plan and version, tab and selection, whether
    the page reads the same save as you, the asks queued for you, and every plan version
    and chat solve by someone else since your last look. Asks it lists are marked seen on
    the page; pass ``answered`` once you have answered them. It lists the advisories worth a
    look (adv: ids); ``dismissed`` hides one on the page, only when the user asks.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    world_id = st.world_id
    log = _log(st)
    me = os.getpid()
    shown_world = st.header.get("session_name") or world_id
    focus, is_open = _page_focus(world_id)

    ours = _sav(st)
    if focus is None:
        head = f'# page never opened for this world · world "{shown_world}"'
    else:
        beat = _age(time.time() - float(focus.get("heartbeat") or 0))
        if is_open:
            head = f"# page open (heartbeat {beat} ago)"
        else:
            head = f"# page closed (last heartbeat {beat} ago)"
        head += f' · world "{shown_world}"'
        theirs = str(focus.get("sav") or "")
        if theirs:
            same = "= yours" if theirs == ours else f"≠ yours ({_short(ours)})"
            head += f" · page {_short(theirs)} {same}"
    lines = [head]
    chat = actor(ctx)
    if answered:
        lines += _answer(world_id, list(answered), chat.display(), chat)
    if dismissed:
        lines += _hide_advice(st, list(dismissed), chat)
    try:
        pin_rows = pins.live(st)
        pin_line = _pins_line(pin_rows)
    except Exception as exc:
        pin_rows, pin_line = [], f"pins: unreadable ({type(exc).__name__})"
    if focus is not None:
        line = _focus_line(focus, log) + _selected_pin(focus, pin_rows)
        lines.append(line if is_open else "last " + line)
        lines.append(f"follow: {focus.get('follow') or 'follow'}")
    lines.append(pin_line)
    advice_lines, adv_ids = _advice_lines(st)
    lines += _asks_lines(world_id, chat.display(), chat, adv_ids)
    lines += advice_lines

    cursor = _cursor.get(world_id)
    try:
        plan_lines, revs = _plan_news(log, cursor, _recipe_names(), me)
    except PlanLogError as exc:
        plan_lines, revs = [f"! plans could not be read: {exc}"], dict(cursor[1] if cursor else {})
    journal_lines, journal_ts = _journal_news(world_id, cursor, me)
    _cursor[world_id] = (journal_ts, revs)

    label = "since you last looked"
    if cursor is None:
        label += f" (first look this session: last {FIRST_LOOK})"
    if not plan_lines and not journal_lines:
        lines.append(f"{label}: nothing new")
    else:
        lines.append(f"{label}: " + (plan_lines[0] if plan_lines else ""))
        lines += [f"  {line}" for line in plan_lines[1:]] + journal_lines
    out = "\n".join(lines)
    if len(out) > CONTEXT_BUDGET:
        out = out[: CONTEXT_BUDGET - 40].rsplit("\n", 1)[0] + "\n(+more: plan_log name=<plan>)"
    return out

"""Factory labels: naming a machine set, renaming, amending, listing and forgetting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated

from pydantic import Field

from .....core.filelock import LockTimeout
from .....domain.factories import candidates, edits, naming
from .....domain.factories import select as machine_select
from .....domain.factories.labels import Label, LabelError, LabelStore, edit_stamp
from .....domain.factories.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from .....domain.session import journal
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf

#: What every label write catches, so each of them words a refusal the same way.
LABEL_REFUSALS = (LabelError, LockTimeout)


def _label_refused(exc: Exception) -> str:
    if isinstance(exc, LockTimeout):
        return "! factory labels are busy (another writer held the lock); nothing written"
    return f"! {exc}; nothing written"


def _find_label(store: LabelStore, name: str) -> Label:
    """The label ``name`` names; a ``Refusal`` listing the known ones."""
    label = store.find(name)
    if label is None:
        known = ", ".join(x.name for x in store.labels) or "(none)"
        raise app.Refusal(f"! no label named {name!r}. Known: {known}")
    return label


def _overlaps(store: LabelStore, machines: Iterable[str], name: str) -> list[str]:
    """Which OTHER labels already hold the machines about to be named here."""
    return [
        f"overlaps {other!r} on {count} machine(s)"
        for other, count in store.overlaps(machines, name).items()
    ]


@app.tool()
def name_factory(
    name: str,
    select: Annotated[
        list[str], Field(description=f"selector terms, ANDed. {GRAPH_SELECTOR_HELP}")
    ],
    notes: str = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    split: Annotated[bool, Field(description="keep only the largest spatial cluster")] = False,
    expand: Annotated[bool, Field(description="pull in everything belted to the result")] = False,
    dry_run: bool = False,
) -> str:
    """Name a set of machines and persist it for this world.

    The label stores the machine instance ids, which are stable across saves, so it
    survives moving machines, adding to the factory, and autosave rotation. Calling
    this again with the same name RE-ANCHORS it to the current selection, dropping every
    machine the new selector misses -- `amend_factory` adds or drops a few without that,
    and `rename_factory` changes the name without touching the membership.
    """
    st = app.load_world(save, world, as_of)
    picked = machine_select.select_machines(select, st, split=split, expand=expand)
    if not picked:
        return "! that selector matched no machines; nothing named"

    store = st.labels
    candidate = candidates.describe(picked, st.graph, st.game, st.projection, "label")
    existing = store.find(name)
    if dry_run:
        verb = "would re-anchor" if existing else "would name"
    else:
        verb = "re-anchored" if existing else "named"
    head = (
        f"{verb} {candidate.size} machine(s) as {name!r} at "
        f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)} "
        f"(spread {candidate.spread_m:.0f}m): {candidate.name_hint()}"
    )
    warn = machine_select.pin_notes(select, st) + _overlaps(store, picked, name)
    if existing:
        kept = len(set(existing.anchors) & set(picked))
        warn.append(
            f"was {len(existing.anchors)} machine(s), {kept} kept, "
            f"{len(existing.anchors) - kept} dropped"
        )

    if dry_run:
        return render.envelope(f"# {head}", "", warn + ["dry run: nothing written"])

    try:
        edits.name_factory(
            st.world_id, st.session_name, name, candidate, notes=notes, when=edit_stamp(st.header)
        )
    except LABEL_REFUSALS as exc:
        return _label_refused(exc)
    path = store.path_for(store.world_id)
    return render.envelope(f"# {head}", f"stored in {path}", warn)


@app.tool()
def rename_factory(
    name: str,
    to: Annotated[str, Field(description="the new name")],
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Rename a factory label. The machines it holds are not touched and nothing re-anchors.

    The label keeps its anchors, notes, centroid, signature and dates. Stored plans scoped
    to this factory follow the new name. Renaming onto a name this world already uses is
    refused and says which label holds it.
    """
    st = app.load_world(save, world, as_of)
    label = _find_label(st.labels, name)
    if label.name == to.strip():
        return f"factory {label.name!r} already has that name"
    who = app.actor(ctx)
    try:
        done = edits.rename(st.world_id, st.session_name, label.name, to, actor=who, exact=True)
    except LABEL_REFUSALS as exc:
        return _label_refused(exc)
    journal.append(
        st.world_id,
        "label.rename",
        actor=who,
        args={"was": done.was, "to": done.name},
        text=f'renamed factory "{done.was}" to "{done.name}"',
    )
    notes = [
        (
            f"recall it as factory={done.name!r}; its {done.machines} anchor "
            "machine(s), notes and dates are untouched"
        )
    ]
    if done.plans:
        notes.append(f"{len(done.plans)} stored plan(s) followed it: {', '.join(done.plans)}")
    if done.stuck:
        notes.append(
            f"{len(done.stuck)} stored plan(s) still name {done.was!r} and did not follow "
            f"({done.stuck_reason}): {', '.join(done.stuck)}; save each again with "
            f"factory={done.name!r} to re-point it"
        )
    return render.envelope(
        f"# renamed factory {done.was!r} to {done.name!r}\nstored in {done.path}", "", notes
    )


@app.tool()
def amend_factory(
    name: str,
    add: Annotated[
        list[str] | None,
        Field(description=f"selector terms for what to ADD. {GRAPH_SELECTOR_HELP}"),
    ] = None,
    drop: Annotated[
        list[str] | None, Field(description="selector terms for what to DROP, same grammar")
    ] = None,
    prune_missing: Annotated[
        bool, Field(description="drop every anchor this save no longer has")
    ] = False,
    notes: str = "",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    dry_run: bool = False,
) -> str:
    """Add or drop individual machines on a label, without re-anchoring the rest of it.

    Every anchor `add` and `drop` do not name is left exactly as it was, including ids this
    save no longer has. `add` runs first, then `drop`, then `prune_missing`, which clears
    the anchors `list_factories` reports gone. One machine is `machine:<instance>` on either
    side. Dropping the last machine is refused -- deleting a label is `forget_factory`.
    """
    st = app.load_world(save, world, as_of)
    store = st.labels
    label = _find_label(store, name)
    if not (add or drop or prune_missing or notes):
        return "! nothing to amend: pass add=, drop=, prune_missing=true or notes="
    wanted = machine_select.select_machines(add, st) if add else []
    unwanted = machine_select.select_machines(drop, st) if drop else []

    alive = set(st.graph.machines())
    gone: set[str] = {m for m in label.anchors if m not in alive} if prune_missing else set()
    going = set(unwanted) | gone
    try:
        plan = edits.preview_amendment(store, label, wanted, going, alive)
    except LabelError as exc:
        return _label_refused(exc)
    warn = machine_select.pin_notes([*(add or ()), *(drop or ())], st)
    warn += [f"overlaps {other!r} on {count} machine(s)" for other, count in plan.overlaps.items()]
    if plan.named:
        warn.append(
            f"{len(plan.added)} added machine(s) already have a name -- covers(), which the "
            "map and propose_factories read for that same question"
        )
    added, dropped, standing = plan.added, plan.dropped, plan.standing

    pruned = sum(1 for m in dropped if m not in alive)
    candidate = candidates.describe(standing, st.graph, st.game, st.projection, "label")
    head = (
        f"{'would amend' if dry_run else 'amended'} {label.name!r}: "
        f"{len(plan.before)} -> {len(plan.after)} anchor(s), +{len(added)} -{len(dropped)}"
        + (f" ({pruned} of them already gone from this save)" if pruned else "")
    )
    if standing:
        head += (
            f"\n# {len(standing)} standing at "
            f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)} "
            f"(spread {candidate.spread_m:.0f}m): {candidate.name_hint()}"
        )
    if dry_run:
        return render.envelope(f"# {head}", "", warn + ["dry run: nothing written"])
    if not added and not dropped and not notes:
        return render.envelope(f"# {head}", "", warn + ["nothing changed, so nothing written"])

    try:
        label, _written = edits.amend(
            st.world_id,
            st.session_name,
            label.name,
            wanted,
            going,
            candidate=candidate if standing else None,
            notes=notes,
            when=edit_stamp(st.header),
        )
    except LABEL_REFUSALS as exc:
        return _label_refused(exc)
    path = store.path_for(store.world_id)
    if left := len(set(label.anchors) - alive):
        warn.append(
            f"{left} anchor(s) still name machines this save does not have; "
            "prune_missing=true drops them, and nothing else does it on its own"
        )
    return render.envelope(f"# {head}", f"stored in {path}", warn)


@app.tool()
def list_factories(save: str | None = None, world: str | None = None, as_of: AsOf = None) -> str:
    """Named factories for this world, with how much of each is still standing."""
    st = app.load_world(save, world, as_of)
    store = st.labels
    if not store.labels:
        return render.envelope(
            f"# {st.age_note}\n# no factories named yet for world {store.world_id!r}",
            "Run factory_map to see candidates, then name_factory to persist one.",
        )
    machines = set(st.graph.machines())
    rows: list[tuple[object, ...]] = []
    for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
        alive = sorted(set(label.anchors) & machines)
        candidate = candidates.describe(alive, st.graph, st.game, st.projection, "label")
        rows.append(
            (
                label.name,
                len(label.anchors),
                f"{len(alive)}/{len(label.anchors)}",
                f"{int(candidate.centroid[0] / 100)},{int(candidate.centroid[1] / 100)}",
                f"{candidate.spread_m:.0f}m",
                naming.lead_of(st, alive, candidate)[0][:40],
                label.notes[:40],
            )
        )
    loose = len(candidates.unassigned(st.graph, store.assigned()))
    # The file's path is printed: labels are hand-authored, so another tool will want them.
    return render.envelope(
        f"# {st.age_note}\n# {len(store.labels)} named, {loose} machine(s) unlabelled"
        f"\n# stored at {store.path_for(store.world_id)}"
        f"\n# also served as resource satisfactory://factories/labels",
        render.table(("name", "anchors", "alive", "x,y(m)", "spread", "makes", "notes"), rows),
        [f"{d['name']}: {d['status']} (recall {d['recall']})" for d in store.review(machines)],
    )


@app.tool()
def forget_factory(
    name: str, save: str | None = None, world: str | None = None, as_of: AsOf = None
) -> str:
    """Delete a factory label. The machines themselves are untouched."""
    st = app.load_world(save, world, as_of)
    label = _find_label(st.labels, name)
    try:
        edits.forget(st.world_id, st.session_name, label.name, exact=True)
    except LABEL_REFUSALS as exc:
        return _label_refused(exc)
    return f"forgot {label.name!r} ({len(label.anchors)} machine(s) released)"

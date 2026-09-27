"""Every write to a world's factory labels, each one locked and re-read (``LabelStore.editing``).

The MCP tools and the web routes both call these, so chat and page write identically.
docs/frontend_vision.md §9.6 has the concurrency rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ...core.filelock import LockTimeout
from ..planning.planlog import Actor, PlanLog, PlanLogError
from .labels import Label, LabelStore, UnknownLabel

__all__ = [
    "Amendment",
    "Renamed",
    "amend",
    "forget",
    "name",
    "plan_amend",
    "rename",
    "repoint_plans",
]


@dataclass(frozen=True)
class Renamed:
    """``plans`` followed the new name; ``stuck`` still name the old one, with why."""

    name: str
    was: str
    machines: int
    plans: list[str]
    version: int
    path: Path
    stuck: list[str]
    why: str


def _pick(store: LabelStore, name: str, exact: bool) -> Label:
    label = next((x for x in store.labels if x.name == name), None) if exact else store.find(name)
    if label is None:
        raise UnknownLabel(f"no factory named {name!r}")
    return label


def name(
    world_id: str,
    session: str,
    name: str,
    cand,
    notes: str = "",
    when: str = "",
    create: bool = False,
    expect: int | None = None,
) -> tuple[Label, list[str] | None, int]:
    """``LabelStore.name`` under the lock. Returns the label, the anchors it had before
    (``None`` when it is new) and the version written."""
    with LabelStore.editing(world_id, session, expect) as store:
        before = None if create else store.find(name)
        held = list(before.anchors) if before else None
        label = store.name(name, cand, notes=notes, when=when, create=create)
    return label, held, store.version


def forget(
    world_id: str, session: str, name: str, exact: bool = False, expect: int | None = None
) -> tuple[Label, int]:
    """Delete one label. Returns it and the version written."""
    with LabelStore.editing(world_id, session, expect) as store:
        label = _pick(store, name, exact)
        store.labels.remove(label)
    return label, store.version


def repoint_plans(
    world_id: str, session: str, was: str, now: str, actor: Actor
) -> tuple[list[str], list[str], str]:
    """Point stored plans scoped to factory ``was`` at ``now``.

    Returns the plans moved, the plans left behind and why the first of those failed. Each
    plan is its own write, so one that is busy does not stop the rest. ``Plan.factory`` is
    resolved by name whenever ``diff_vs_save`` or ``plan_layout`` scopes itself to one, so
    a rename that left it behind would orphan the plan. A mechanical follow-up, so it lands
    at the head (``PlanLog.push_at_head``).
    """
    log = PlanLog(world_id, session)
    moved: list[str] = []
    stuck: list[str] = []
    why = ""
    for state in log.heads():
        if not (state.factory and state.factory.casefold() == was.casefold()):
            continue
        try:
            log.push_at_head(
                state.key,
                [{"op": "set", "field": "factory", "value": now}],
                actor=actor,
                note=f"factory renamed {was!r} to {now!r}",
            )
        except (PlanLogError, LockTimeout, OSError) as exc:
            stuck.append(state.name)
            why = why or str(exc)
            continue
        moved.append(state.name)
    return moved, stuck, why


def rename(
    world_id: str,
    session: str,
    name: str,
    to: str,
    *,
    actor: Actor,
    exact: bool = False,
    expect: int | None = None,
) -> Renamed:
    """Rename one label, keeping its machines, then move the stored plans scoped to it.

    The label is written first: a plan re-pointed at a name no label holds yet would be
    orphaned by a label write that then failed. A plan that cannot follow is reported in
    ``Renamed.stuck`` rather than undoing a rename that did land.
    """
    with LabelStore.editing(world_id, session, expect) as store:
        label = _pick(store, name, exact)
        was = store.rename(label, to)
    moved, stuck, why = (
        repoint_plans(world_id, session, was, label.name, actor)
        if was != label.name
        else ([], [], "")
    )
    return Renamed(
        label.name,
        was,
        len(label.anchors),
        moved,
        store.version,
        LabelStore.path_for(world_id),
        stuck,
        why,
    )


@dataclass(frozen=True)
class Amendment:
    """What ``amend`` would do to one label, worked out without touching it.

    ``named`` is true when the machines being added are already a named set (``covers``);
    ``overlaps`` counts them per other label.
    """

    before: list[str]
    after: list[str]
    added: list[str]
    dropped: list[str]
    standing: list[str]
    overlaps: dict[str, int]
    named: bool


def plan_amend(
    store: LabelStore, label: Label, add: list[str], drop: set[str], alive: set[str]
) -> Amendment:
    """The dry run of ``amend``: ``add`` first, then ``drop``, on a copy of ``label``.

    Raises ``LabelError`` where ``amend`` would, when the label would be left empty.
    """
    before = list(label.anchors)
    fresh = sorted(set(add) - set(before))
    trial = replace(label, anchors=list(before))
    added = store.attach(trial, add)
    dropped = store.detach(trial, drop)
    return Amendment(
        before,
        list(trial.anchors),
        added,
        dropped,
        sorted(set(trial.anchors) & alive),
        store.overlaps(fresh, label.name),
        bool(fresh) and store.covers(fresh),
    )


def amend(
    world_id: str,
    session: str,
    name: str,
    add: list[str],
    drop: set[str],
    cand=None,
    notes: str = "",
    when: str = "",
    expect: int | None = None,
) -> tuple[Label, int]:
    """Attach ``add`` and detach ``drop`` on one label; ``cand`` refreshes its geometry.

    Returns the label and the version written.
    """
    with LabelStore.editing(world_id, session, expect) as store:
        label = _pick(store, name, exact=True)
        store.attach(label, add)
        store.detach(label, drop)
        if cand is not None:
            label.centroid = cand.centroid
            label.signature = dict(cand.buildings)
        if notes:
            label.notes = notes
        label.last_matched = when
    return label, store.version

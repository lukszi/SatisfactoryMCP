"""Every write to a world's factory labels, each one locked and re-read (``LabelStore.editing``).

The MCP tools and the web routes both call these, so chat and page write identically.
docs/frontend_vision.md §9.6 has the concurrency rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..planning.store import PlanStore
from .labels import Label, LabelError, LabelStore

__all__ = ["Renamed", "amend", "forget", "name", "rename", "repoint_plans"]


@dataclass(frozen=True)
class Renamed:
    name: str
    was: str
    machines: int
    plans: list[str]
    version: int
    path: Path


def _pick(store: LabelStore, name: str, exact: bool) -> Label:
    label = next((x for x in store.labels if x.name == name), None) if exact else store.find(name)
    if label is None:
        raise LabelError(f"no factory named {name!r}")
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


def repoint_plans(world_id: str, session: str, was: str, now: str) -> list[str]:
    """Point stored plans scoped to factory ``was`` at ``now``; returns their names.

    ``Plan.factory`` is resolved by name whenever ``diff_vs_save`` or ``plan_layout``
    scopes itself to one, so a rename that left it behind would orphan the plan.
    """
    if not any(
        p.factory and p.factory.casefold() == was.casefold()
        for p in PlanStore.load(world_id, session).plans
    ):
        return []
    moved = []
    with PlanStore.editing(world_id, session) as plans:
        for plan in plans.plans:
            if plan.factory and plan.factory.casefold() == was.casefold():
                plan.factory = now
                moved.append(plan.name)
    return moved


def rename(
    world_id: str,
    session: str,
    name: str,
    to: str,
    exact: bool = False,
    expect: int | None = None,
) -> Renamed:
    """Rename one label, keeping its machines, and move the stored plans scoped to it."""
    with LabelStore.editing(world_id, session, expect) as store:
        label = _pick(store, name, exact)
        was = store.rename(label, to)
        moved = repoint_plans(world_id, session, was, label.name) if was != label.name else []
    return Renamed(
        label.name, was, len(label.anchors), moved, store.version, LabelStore.path_for(world_id)
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
) -> Label:
    """Attach ``add`` and detach ``drop`` on one label; ``cand`` refreshes its geometry."""
    with LabelStore.editing(world_id, session) as store:
        label = _pick(store, name, exact=True)
        store.attach(label, add)
        store.detach(label, drop)
        if cand is not None:
            label.centroid = cand.centroid
            label.signature = dict(cand.buildings)
        if notes:
            label.notes = notes
        label.last_matched = when
    return label

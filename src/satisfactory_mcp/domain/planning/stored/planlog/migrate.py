"""Moving a world's legacy plan file into its plan log, once (docs/plan_log.md, "Migration")."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .....core import atomic, schema
from ..plan_args import PlanArgs
from ..store import PLAN_ARGS, Plan, PlanStore
from .records import SCHEMA, Actor, PlanState

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from .log import PlanLog

_log = logging.getLogger(__name__)
_warned: set[str] = set()


def _legacy_store_path(world_id: str) -> Path:
    return PlanStore.path_for(world_id)


def _backup(log: PlanLog, legacy: Path, version: str) -> Path:
    """Copy the legacy file to ``backup-v<version>/`` beside the log, once."""
    into = log.root / f"backup-v{version}"
    into.mkdir(parents=True, exist_ok=True)
    copy_to = into / legacy.name
    if not copy_to.is_file():
        shutil.copy2(legacy, copy_to)
    return copy_to


def _check_marker(marker: Path) -> None:
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    schema.check(raw, SCHEMA, marker)


def _unique_name(log: PlanLog, name: str) -> str:
    """``name``, or ``name (2)``, ``name (3)`` … when a live plan already holds it."""
    unique, n = name, 2
    while log.taken(unique):
        unique, n = f"{name} ({n})", n + 1
    return unique


def _legacy_plan_state(plan: Plan, name: str) -> PlanState:
    """A legacy plan as a v1 state; arguments that fail their check are kept in the notes."""
    refused: list[tuple[str, object]] = []
    args = PlanArgs.from_dict(
        {k: v for k, v in (plan.args or {}).items() if k in PLAN_ARGS}, lenient=refused
    )
    notes = plan.notes or ""
    if refused:
        kept = "; ".join(f"{k}={v!r}" for k, v in refused)
        notes = (notes + "\n" if notes else "") + f"migration could not keep: {kept}"
    return PlanState(
        key="",
        rev=1,
        name=name,
        notes=str(notes),
        factory=str(plan.factory or ""),
        created=str(plan.created or ""),
        plan_id=str(plan.plan_id or ""),
        provenance=copy.deepcopy(plan.provenance or {}),
        siting=copy.deepcopy(plan.siting or {}),
        args=args,
    )


def _write_marker(marker: Path, version: str, legacy: Path, keys: dict[str, str]) -> None:
    atomic.write_text(
        marker,
        json.dumps(
            {
                "schema": SCHEMA,
                "version": version,
                "from": legacy.name,
                "at": time.time(),
                "keys": keys,
            }
        ),
    )


def migrate(log: PlanLog) -> dict[str, str]:
    """Each legacy plan becomes a ``create`` commit; the legacy file is left untouched.

    The legacy file is backed up first, a legacy file or a log from a newer version is
    refused (``schema.NewerSchema``), and the marker records the version that migrated.
    """
    legacy, marker = _legacy_store_path(log.world_id), log.root / "migrated.json"
    if marker.is_file():
        _check_marker(marker)
    if not legacy.is_file():
        return {}
    if marker.is_file():
        _warn_if_newer(log.world_id, legacy, marker)
        return {}
    log.root.mkdir(parents=True, exist_ok=True)
    with log.world_lock():
        if marker.is_file():
            return {}
        old = PlanStore.load(log.world_id, log.session_name)
        version = schema.writer_version()
        _backup(log, legacy, version)
        done = {
            s.name: s.key for s in log.heads() if log.commits(s.key)[0].actor.kind == "migration"
        }
        keys: dict[str, str] = {}
        actor = Actor("migration", "", os.getpid())
        for plan in old.plans:
            name = (plan.name or "").strip() or "plan"
            if name in done:
                keys[name] = done[name]
                continue
            unique = _unique_name(log, name)
            state = _legacy_plan_state(plan, unique)
            log.create_locked(log.fresh_key(), state, actor, "", f"migrated from {legacy.name}")
            keys[unique] = state.key
        _write_marker(marker, version, legacy, keys)
    return keys


def _warn_if_newer(world_id: str, legacy: Path, marker: Path) -> None:
    if world_id in _warned:
        return
    try:
        at = float(json.loads(marker.read_text(encoding="utf-8")).get("at") or 0)
        newer = legacy.stat().st_mtime > at
    except (OSError, ValueError, AttributeError):
        return
    if newer:
        _warned.add(world_id)
        _log.warning(
            "%s changed after it was migrated; those changes are not in the plan log",
            legacy,
        )

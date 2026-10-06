"""One world's plan log on disk: reading heads, writing commits, merging by rule M1.

docs/planner_slice_contract.md §2-§5 and §7 are the specification, and docs/plan_log.md says
how this package maps onto it. Needs no game data.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from typing_extensions import TypedDict

from .....core import atomic, filelock, schema
from ..plan_args import PLAN_SCALARS, InvalidOp, PlanArgs, checked_text
from ..store import Plan, PlanStore, find_by_name
from ..views import PlanOp, PlanStamp, PlanStateRecord
from . import migrate as migration
from .ops import (
    apply,
    canonical_op,
    check_lifecycle,
    clash,
    diff_args,
    inverse,
    is_noop,
    merge_key,
    needs_world_lock,
    replay,
    revs_undo_ignores,
    standing_undo_of,
    with_was,
)
from .records import (
    SCHEMA,
    Actor,
    AlreadyUndone,
    BaseRevRequired,
    Commit,
    Conflict,
    Forgotten,
    NameTaken,
    Outdated,
    PlanState,
    Pushed,
    Stamp,
    UnknownPlan,
    json_copy,
)

SNAPSHOT_EVERY = 50

_log = logging.getLogger(__name__)


class _Snapshot(TypedDict):
    """``snap/<rev>.json`` as read back: one plan's state at ``rev``."""

    schema: int
    key: str
    rev: int
    ts: float
    state: PlanStateRecord


def _read_commits(path: Path) -> list[Commit]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    out: list[Commit] = []
    for line in data.split(b"\n")[:-1]:
        try:
            commit = Commit.from_dict(json.loads(line))
        except (ValueError, KeyError, TypeError):
            continue
        if commit.rev == len(out) + 1:
            out.append(commit)
    return out


def _append(path: Path, commit: Commit) -> None:
    if path.is_file() and path.stat().st_size:
        with open(path, "r+b") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0)
                data = handle.read()
                handle.seek(data.rfind(b"\n") + 1)
                handle.truncate()
    line = json.dumps(commit.to_dict(), separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(path, "ab") as handle:
        handle.write(line.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


_last_ts = 0.0


def _now() -> float:
    global _last_ts
    _last_ts = max(time.time(), _last_ts + 1e-6)
    return _last_ts


def _as_plan(state: PlanState) -> Plan:
    plan = Plan(
        name=state.name,
        args={"objective": state.args.objective, **state.kwargs()},
        notes=state.notes,
        plan_id=state.plan_id,
        factory=state.factory,
        created=state.created,
        provenance=copy.deepcopy(state.provenance),
        siting=copy.deepcopy(state.siting),
    )
    plan.key = state.key
    plan.rev = state.rev
    return plan


def _find_conflicts(mine: list[PlanOp], against: list[Commit]) -> list[Conflict]:
    """Every pair of my op and a commit's op that clash, in the order a refusal lists them."""
    conflicts: list[Conflict] = []
    for op in mine:
        for commit in against:
            for theirs in commit.ops:
                if clash(op, theirs) == "conflict":
                    conflicts.append(
                        Conflict(merge_key(op) or "", op, theirs, commit.rev, commit.actor)
                    )
    return conflicts


def _apply_ops(
    work: PlanState, mine: list[PlanOp], since: list[Commit]
) -> tuple[list[PlanOp], list[PlanOp]]:
    """Apply ``mine`` to ``work`` in order: (applied, each with its ``was``; dropped no-ops)."""
    applied: list[PlanOp] = []
    dropped: list[PlanOp] = []
    restored = any(t.get("op") == "restore" for c in since for t in c.ops)
    for op in mine:
        if is_noop(work, op) or (op["op"] == "restore" and restored and not work.forgotten):
            dropped.append(op)
            continue
        check_lifecycle(work, op)
        filled = with_was(work, op)
        apply(work, filled)
        applied.append(filled)
    return applied, dropped


@dataclass
class PlanView:
    """The read-only ``WorldState.plans``: live plans as ``Plan`` objects with key and rev."""

    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list)

    def find(self, name: str) -> Plan | None:
        by_key = [p for p in self.plans if p.key == name.strip().lower()]
        return by_key[0] if by_key else find_by_name(self.plans, lambda p: p.name, name)


class PlanLog:
    """A world's plan log; opening it migrates that world's legacy plan file once."""

    def __init__(self, world_id: str, session_name: str = "") -> None:
        self.world_id = world_id
        self.session_name = session_name
        self.root = self.dir_for(world_id)
        self.migrate()

    @staticmethod
    def dir_for(world_id: str) -> Path:
        return PlanStore.path_for(world_id).with_suffix("")

    def _ops(self, key: str) -> Path:
        return self.root / key / "ops.jsonl"

    def world_lock(self) -> AbstractContextManager[None]:
        return filelock.held(self.root / "world")

    def _plan_lock(self, key: str) -> AbstractContextManager[None]:
        return filelock.held(self._ops(key))

    def fresh_key(self) -> str:
        key = secrets.token_hex(4)
        while (self.root / key).exists():
            key = secrets.token_hex(4)
        return key

    def keys(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob("*/ops.jsonl"))

    def commits(self, key: str, since: int = 0, until: int | None = None) -> list[Commit]:
        return [c for c in self._all(key) if c.rev > since and (until is None or c.rev <= until)]

    def _all(self, key: str) -> list[Commit]:
        commits = _read_commits(self._ops(key))
        if not commits:
            raise UnknownPlan(key, [s.name for s in self.heads()] if self.root.is_dir() else [])
        return commits

    def head_rev(self, key: str) -> int:
        return self._all(key)[-1].rev

    def state(self, key: str, rev: int | None = None) -> PlanState:
        return self._state(key, self._all(key), rev)

    def _state(self, key: str, commits: list[Commit], rev: int | None = None) -> PlanState:
        head = commits[-1].rev
        rev = head if rev is None else rev
        if not 1 <= rev <= head:
            raise InvalidOp(f"plan {key} has no v{rev}; it is at v{head}")
        base = self._newest_snapshot(key, rev)
        start = base.rev if base is not None else 0
        state = replay(base, [c for c in commits if start < c.rev <= rev], key)
        if state is None:
            raise UnknownPlan(key, [])
        return state

    def _newest_snapshot(self, key: str, rev: int) -> PlanState | None:
        """The newest readable snapshot at or below ``rev`` whose own key and rev match."""
        snaps = self.root / key / "snap"
        revs = sorted((int(p.stem) for p in snaps.glob("*.json") if p.stem.isdigit()), reverse=True)
        for snap in (r for r in revs if r <= rev):
            try:
                raw: _Snapshot = json.loads((snaps / f"{snap}.json").read_text(encoding="utf-8"))
                schema.check(raw, SCHEMA, snaps / f"{snap}.json")
                if raw.get("rev") != snap or raw.get("key") != key:
                    continue
                return PlanState.from_dict(raw["state"], key=key, rev=snap)
            except (OSError, ValueError, KeyError, TypeError, InvalidOp):
                continue
        return None

    def heads(self, include_forgotten: bool = False) -> list[PlanState]:
        rows: list[tuple[float, str, PlanState]] = []
        for key in self.keys():
            commits = _read_commits(self._ops(key))
            if not commits:
                continue
            state = self._state(key, commits)
            if include_forgotten or not state.forgotten:
                rows.append((commits[0].ts, key, state))
        return [state for _, _, state in sorted(rows, key=lambda r: (r[0], r[1]))]

    def find(self, name_or_key: str, include_forgotten: bool = False) -> PlanState | None:
        states = self.heads(include_forgotten=include_forgotten)
        needle = name_or_key.strip().lower()
        for state in states:
            if state.key == needle:
                return state
        live = [s for s in states if not s.forgotten]
        hit = find_by_name(live, lambda s: s.name, name_or_key)
        if hit is None and include_forgotten:
            forgotten = list(reversed([s for s in states if s.forgotten]))
            hit = find_by_name(forgotten, lambda s: s.name, name_or_key)
        return hit

    def view(self) -> PlanView:
        return PlanView(self.world_id, self.session_name, [_as_plan(s) for s in self.heads()])

    def taken(self, name: str, key: str | None = None) -> bool:
        wanted = name.strip().casefold()
        return any(s.key != key and s.name.casefold() == wanted for s in self.heads())

    def free_name(self, name: str, key: str | None = None) -> str:
        """``name`` stripped, or ``InvalidOp`` / ``NameTaken`` saying why a plan other than
        ``key`` cannot take it. Case-insensitive, as ``find`` is. Unlocked: a write re-checks."""
        wanted = checked_text("name", name).strip()
        if not wanted:
            raise InvalidOp("a plan name cannot be blank")
        if self.taken(wanted, key):
            raise NameTaken(wanted)
        return wanted

    def _snapshot(self, state: PlanState) -> None:
        snaps = self.root / state.key / "snap"
        payload = {
            "schema": SCHEMA,
            "key": state.key,
            "rev": state.rev,
            "ts": time.time(),
            "state": state.to_dict(),
        }
        try:
            snaps.mkdir(parents=True, exist_ok=True)
            atomic.write_text(snaps / f"{state.rev}.json", json.dumps(payload, ensure_ascii=False))
        except OSError:
            pass

    def create(
        self,
        name: str,
        args: PlanArgs | Mapping[str, object],
        *,
        actor: Actor,
        sav: str = "",
        notes: str = "",
        factory: str = "",
        siting: Mapping[str, object] | None = None,
        plan_id: str = "",
        provenance: Mapping[str, object] | None = None,
        created: str = "",
        note: str = "",
    ) -> Pushed:
        wanted = self.free_name(name)
        state = PlanState(
            key="",
            rev=1,
            name=wanted,
            notes=checked_text("notes", notes),
            factory=checked_text("factory", factory),
            created=checked_text("created", created),
            plan_id=checked_text("plan_id", plan_id),
            provenance=json_copy(provenance or {}),
            siting=json_copy(siting or {}),
            args=args if isinstance(args, PlanArgs) else PlanArgs.from_dict(args),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with self.world_lock():
            if self.taken(wanted):
                raise NameTaken(wanted)
            return self.create_locked(self.fresh_key(), state, actor, sav, note)

    def create_locked(
        self, key: str, state: PlanState, actor: Actor, sav: str, note: str
    ) -> Pushed:
        state.key = key
        op: PlanOp = {"op": "create", "name": state.name, "state": json_copy(state.body())}
        commit = Commit(rev=1, base_rev=0, ts=_now(), actor=actor, sav=sav, ops=[op], note=note)
        (self.root / key).mkdir(parents=True, exist_ok=True)
        with self._plan_lock(key):
            _append(self._ops(key), commit)
            self._snapshot(state)
        return Pushed(
            key=key,
            rev=1,
            base_rev=0,
            applied=[op],
            dropped=[],
            merged_over=[],
            others=[],
            noop=False,
            state=state,
        )

    def push(
        self,
        key: str,
        base_rev: int | None,
        ops: Sequence[Mapping[str, object]],
        *,
        actor: Actor,
        sav: str = "",
        stamp: Stamp | None = None,
        note: str = "",
        extend: Callable[[PlanState], Sequence[Mapping[str, object]]] | None = None,
    ) -> Pushed:
        """``extend`` adds ops worked out from the head under the plan lock; they merge by
        M1 like the rest, so one that clashes with a commit since ``base_rev`` refuses."""
        mine = [canonical_op(op) for op in ops]

        def run(commits: list[Commit]) -> Pushed:
            more = [canonical_op(op) for op in extend(self._state(key, commits))] if extend else []
            return self._merge(
                key,
                commits,
                base_rev=base_rev,
                mine=mine + [op for op in more if op not in mine],
                actor=actor,
                sav=sav,
                stamp=stamp,
                note=note,
                undoes=None,
            )

        return self._locked(needs_world_lock(mine), key, run)

    def push_args(
        self,
        key: str,
        base_rev: int | None,
        args: Mapping[str, object],
        *,
        actor: Actor,
        sav: str = "",
        extra: Sequence[Mapping[str, object]] | None = None,
        stamp: Stamp | None = None,
        note: str = "",
    ) -> Pushed:
        base = self.state(key, self._valid_base(key, base_rev))
        ops: list[Mapping[str, object]] = [*diff_args(base.args, args, partial=False)]
        ops += extra or ()
        return self.push(key, base_rev, ops, actor=actor, sav=sav, stamp=stamp, note=note)

    def _valid_base(self, key: str, base_rev: object) -> int:
        return self._valid_base_in(self._all(key), base_rev)

    def undo(
        self,
        key: str,
        base_rev: int | None,
        rev: int | None,
        *,
        actor: Actor,
        sav: str = "",
        stamp: Stamp | None = None,
    ) -> Pushed:
        commits = self._all(key)
        if isinstance(rev, bool) or not isinstance(rev, int) or not 1 <= rev <= commits[-1].rev:
            raise InvalidOp(f"plan {key} has no v{rev} to undo")
        if rev == 1:
            raise InvalidOp("v1 created the plan and cannot be undone; forget the plan instead")
        target = commits[rev - 1]
        renames = needs_world_lock(inverse(target.ops))

        def run(current: list[Commit]) -> Pushed:
            by = standing_undo_of(current, rev)
            if by is not None:
                raise AlreadyUndone(rev, by.rev)
            return self._merge(
                key,
                current,
                base_rev=base_rev,
                mine=inverse([op for op in target.ops if op.get("op") != "record"]),
                actor=actor,
                sav=sav,
                stamp=stamp,
                note="",
                undoes=rev,
                window=revs_undo_ignores(current, rev),
            )

        return self._locked(renames, key, run)

    def restore_to(
        self,
        key: str,
        base_rev: int | None,
        rev: int,
        *,
        actor: Actor,
        sav: str = "",
        stamp: Stamp | None = None,
    ) -> Pushed:
        def run(current: list[Commit]) -> Pushed:
            head = self._state(key, current)
            then = self._state(key, current, rev)
            ops = diff_args(head.args, then.args.to_dict())
            ops += [
                {"op": "set", "field": f, "value": getattr(then, f)}
                for f in PLAN_SCALARS
                if getattr(head, f) != getattr(then, f)
            ]
            if (head.siting or None) != (then.siting or None):
                ops.append({"op": "site", "value": then.siting or None})
            if head.name != then.name:
                ops.append({"op": "rename", "name": then.name})
            pushed = self._merge(
                key,
                current,
                base_rev=base_rev,
                mine=[canonical_op(op) for op in ops],
                actor=actor,
                sav=sav,
                stamp=stamp,
                note=f"restore v{rev}",
                undoes=None,
            )
            if not pushed.noop:
                self._snapshot(pushed.state)
            return pushed

        return self._locked(True, key, run)

    def push_at_head(
        self, key: str, ops: Sequence[Mapping[str, object]], *, actor: Actor, note: str = ""
    ) -> Pushed:
        mine = [canonical_op(op) for op in ops]

        def run(current: list[Commit]) -> Pushed:
            return self._merge(
                key,
                current,
                base_rev=current[-1].rev,
                mine=mine,
                actor=actor,
                sav="",
                stamp=None,
                note=note,
                undoes=None,
            )

        return self._locked(needs_world_lock(mine), key, run)

    def _locked(self, world: bool, key: str, run: Callable[[list[Commit]], Pushed]) -> Pushed:
        self._all(key)
        if world:
            with self.world_lock(), self._plan_lock(key):
                return run(self._all(key))
        with self._plan_lock(key):
            return run(self._all(key))

    def _merge(
        self,
        key: str,
        commits: list[Commit],
        *,
        base_rev: object,
        mine: list[PlanOp],
        actor: Actor,
        sav: str,
        stamp: Stamp | None,
        note: str,
        undoes: int | None,
        window: set[int] | None = None,
    ) -> Pushed:
        head_rev = commits[-1].rev
        base = self._valid_base_in(commits, base_rev)
        base_state = self._state(key, commits, base)
        if base_state.forgotten and not any(op["op"] == "restore" for op in mine):
            forgot = max(
                c.rev for c in commits[:base] if any(o.get("op") == "forget" for o in c.ops)
            )
            raise Forgotten(key, forgot)
        since = commits[base:]
        if undoes is None:
            against = since
        else:
            ignored = window or set()
            against = [c for c in commits[undoes:] if c.rev not in ignored]
        conflicts = _find_conflicts(mine, against)
        head = self._state(key, commits)
        if conflicts:
            raise Outdated(head_rev, base, since, conflicts, head)

        work = copy.deepcopy(head)
        applied, dropped = _apply_ops(work, mine, since)
        if not applied:
            return Pushed(
                key=key,
                rev=head_rev,
                base_rev=base,
                applied=[],
                dropped=dropped,
                merged_over=[],
                others=[],
                noop=True,
                state=head,
            )
        if needs_world_lock(applied) and not work.forgotten and self.taken(work.name, key):
            raise NameTaken(work.name)
        applied += self._stamped(work, stamp)
        for op in applied:
            if op["op"] == "record":
                apply(work, op)
        work.rev = head_rev + 1
        merged = [c.rev for c in since]
        commit = Commit(
            rev=work.rev,
            base_rev=base,
            ts=_now(),
            actor=actor,
            sav=sav,
            ops=applied,
            merged_over=merged,
            undoes=undoes,
            note=note[:200],
        )
        _append(self._ops(key), commit)
        if work.rev % SNAPSHOT_EVERY == 0:
            self._snapshot(work)
        return Pushed(
            key=key,
            rev=work.rev,
            base_rev=base,
            applied=applied,
            dropped=dropped,
            merged_over=merged,
            others=list(since),
            noop=False,
            state=work,
        )

    @staticmethod
    def _valid_base_in(commits: list[Commit], base_rev: object) -> int:
        head = commits[-1].rev
        if base_rev is None:
            raise BaseRevRequired(head)
        if isinstance(base_rev, bool) or not isinstance(base_rev, int):
            raise InvalidOp(f"base_rev must be a whole number, not {base_rev!r}")
        if base_rev < 1 or base_rev > head:
            raise InvalidOp(f"base_rev v{base_rev} does not exist; the plan is at v{head}")
        return base_rev

    @staticmethod
    def _stamped(work: PlanState, stamp: Stamp | None) -> list[PlanOp]:
        if stamp is None:
            return []
        try:
            got: PlanStamp = stamp(copy.deepcopy(work)) or {}
        except Exception:
            _log.warning("could not stamp plan %s; its plan_id is cleared", work.key, exc_info=True)
            got = {"plan_id": ""}
        values: dict[str, object] = dict(got)
        out: list[PlanOp] = []
        for name in ("plan_id", "provenance"):
            if name in values and values[name] != getattr(work, name):
                try:
                    out.append(canonical_op({"op": "record", "field": name, "value": values[name]}))
                except InvalidOp:
                    continue
        return out

    def migrate(self) -> dict[str, str]:
        """Each legacy plan becomes a ``create`` commit; ``migrate.migrate`` has the rules."""
        return migration.migrate(self)

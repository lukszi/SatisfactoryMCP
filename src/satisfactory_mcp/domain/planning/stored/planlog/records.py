"""The plan log's records: a plan's state, a commit, who wrote it, and what a write answers."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from .....core.jsontypes import JsonObject
from ..plan_args import InvalidOp, PlanArgs, PlanLogError, checked_headroom, legacy_hours
from ..views import (
    ActorRecord,
    CommitRecord,
    ConflictRecord,
    PlanOp,
    PlanStamp,
    PlanStateRecord,
)
from .wording import (
    action_words,
    conflict_subject,
    describe_commit,
    other_text,
    value_word,
)

#: The version of every file the log writes: snapshots and the migration marker.
SCHEMA = 1


class UnknownPlan(PlanLogError, KeyError):
    def __init__(self, what: str, known: list[str]) -> None:
        super().__init__(f"no saved plan named {what!r}")
        self.what = what
        self.known = known

    def __str__(self) -> str:
        return self.args[0]


class NameTaken(InvalidOp):
    def __init__(self, name: str) -> None:
        super().__init__(f"this world already has a plan named {name!r}")
        self.name = name


class Forgotten(PlanLogError):
    def __init__(self, key: str, rev: int) -> None:
        super().__init__(f"plan {key} was forgotten in v{rev}")
        self.key = key
        self.rev = rev


class AlreadyUndone(PlanLogError):
    def __init__(self, rev: int, by: int) -> None:
        super().__init__(f"v{rev} was already undone by v{by}")
        self.rev = rev
        self.by = by


class BaseRevRequired(PlanLogError):
    def __init__(self, head: int) -> None:
        super().__init__(f"base_rev is required; the plan is at v{head}")
        self.head = head


def json_copy(record: Mapping[str, object]) -> JsonObject:
    """A deep copy of ``record`` as the plain JSON object it is: a TypedDict, or JSON read back."""
    return cast(JsonObject, copy.deepcopy(dict(record)))


def _stored_headroom(value: object) -> float | None:
    try:
        return checked_headroom("headroom_mw", value)
    except InvalidOp:
        return None


@dataclass
class PlanState:
    key: str
    rev: int
    name: str
    forgotten: bool = False
    notes: str = ""
    factory: str = ""
    created: str = ""
    plan_id: str = ""
    #: ``provenance.record``'s block as stored; empty means "not recorded".
    provenance: JsonObject = field(default_factory=dict)
    #: ``siting.normalise_record``'s record as stored; empty means "not sited".
    siting: JsonObject = field(default_factory=dict)
    args: PlanArgs = field(default_factory=PlanArgs)
    headroom_mw: float | None = None

    def kwargs(self) -> dict:
        return self.args.kwargs()

    def to_dict(self) -> PlanStateRecord:
        return {"key": self.key, "rev": self.rev, **self.body()}

    @classmethod
    def from_dict(cls, stored: Mapping[str, object], key: str = "", rev: int = 0) -> PlanState:
        """``stored`` is JSON from the log, of the shape ``PlanStateRecord`` declares."""
        raw = cast(PlanStateRecord, stored)
        return cls(
            key=raw.get("key") or key,
            rev=int(raw.get("rev") or rev),
            name=str(raw.get("name", "")),
            forgotten=bool(raw.get("forgotten", False)),
            notes=str(raw.get("notes") or ""),
            factory=str(raw.get("factory") or ""),
            created=str(raw.get("created") or ""),
            plan_id=str(raw.get("plan_id") or ""),
            provenance=copy.deepcopy(raw.get("provenance") or {}),
            siting=copy.deepcopy(raw.get("siting") or {}),
            args=PlanArgs.from_dict(raw.get("args")),
            headroom_mw=_stored_headroom(raw.get("headroom_mw")),
        )

    def body(self) -> PlanStateRecord:
        """The ``create`` op's ``state``: everything but key and rev."""
        return {
            "name": self.name,
            "forgotten": self.forgotten,
            "notes": self.notes,
            "factory": self.factory,
            "created": self.created,
            "plan_id": self.plan_id,
            "provenance": copy.deepcopy(self.provenance),
            "siting": copy.deepcopy(self.siting),
            "args": self.args.to_dict(),
            "headroom_mw": self.headroom_mw,
        }


_CLIENT_NAMES = {"claude-code": "Claude Code", "claude-ai": "Claude Desktop"}


@dataclass(frozen=True)
class Actor:
    kind: str
    client: str = ""
    pid: int = 0

    def to_dict(self) -> ActorRecord:
        return {"kind": self.kind, "client": self.client, "pid": self.pid}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object] | None) -> Actor:
        given = raw or {}
        pid = given.get("pid") or 0
        return cls(
            str(given.get("kind") or "system"),
            str(given.get("client") or ""),
            int(pid) if isinstance(pid, int | float | str) else 0,
        )

    def display(self) -> str:
        if self.kind == "chat":
            return _CLIENT_NAMES.get(self.client, self.client or "chat")
        if self.kind == "migration":
            return "migrated"
        return self.kind


def _legacy_op(op: PlanOp) -> PlanOp:
    """An old ``set power_priority`` op as the ``set payback_hours`` it now means."""
    if op.get("field") != "power_priority" or op.get("op") != "set":
        return op
    out: PlanOp = {"op": "set", "field": "payback_hours", "value": legacy_hours(op.get("value"))}
    if "was" in op:
        out["was"] = legacy_hours(op.get("was"))
    return out


@dataclass
class Commit:
    rev: int
    base_rev: int
    ts: float
    actor: Actor
    sav: str
    ops: list[PlanOp]
    merged_over: list[int] = field(default_factory=list)
    undoes: int | None = None
    note: str = ""

    def to_dict(self) -> CommitRecord:
        return {
            "rev": self.rev,
            "base_rev": self.base_rev,
            "ts": self.ts,
            "actor": self.actor.to_dict(),
            "sav": self.sav,
            "ops": copy.deepcopy(self.ops),
            "merged_over": list(self.merged_over),
            "undoes": self.undoes,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, stored: Mapping[str, object]) -> Commit:
        """``stored`` is one JSON line of the log, of the shape ``CommitRecord`` declares."""
        raw = cast(CommitRecord, stored)
        return cls(
            rev=int(raw["rev"]),
            base_rev=int(raw.get("base_rev") or 0),
            ts=float(raw.get("ts") or 0.0),
            actor=Actor.from_dict(raw.get("actor")),
            sav=str(raw.get("sav") or ""),
            ops=[_legacy_op(op) for op in raw["ops"]],
            merged_over=[int(r) for r in raw.get("merged_over") or ()],
            undoes=raw.get("undoes"),
            note=str(raw.get("note") or ""),
        )

    def text(self) -> str:
        return describe_commit(self)


@dataclass
class Conflict:
    key: str
    mine: PlanOp
    theirs: PlanOp
    theirs_rev: int
    theirs_actor: Actor

    def text(self) -> str:
        if self.mine["op"] in ("set", "put"):
            mine = value_word(self.mine)
        else:
            mine = action_words(self.mine)
        who = self.theirs_actor.display()
        return (
            f"{conflict_subject(self.mine)}: you {mine}, "
            f"{who} {action_words(self.theirs)} in v{self.theirs_rev}"
        )

    def to_dict(self) -> ConflictRecord:
        return {
            "key": self.key,
            "mine": copy.deepcopy(self.mine),
            "theirs": copy.deepcopy(self.theirs),
            "theirs_rev": self.theirs_rev,
            "theirs_actor": self.theirs_actor.to_dict(),
            "text": self.text(),
        }


@dataclass
class Pushed:
    key: str
    rev: int
    base_rev: int
    applied: list[PlanOp]
    dropped: list[PlanOp]
    merged_over: list[int]
    others: list[Commit]
    noop: bool
    state: PlanState

    def text(self, name: str) -> str:
        if self.noop:
            return f'nothing changed: plan "{name}" is still v{self.rev}'
        if self.merged_over:
            others = ", ".join(other_text(c) for c in self.others)
            return (
                f"merged onto v{self.rev - 1} (you were on v{self.base_rev}) -> now "
                f"v{self.rev}; others changed: {others}"
            )
        return f'plan "{name}" is now v{self.rev}'


class Outdated(PlanLogError):
    def __init__(
        self,
        head: int,
        base_rev: int,
        since: list[Commit],
        conflicts: list[Conflict],
        state: PlanState,
    ) -> None:
        super().__init__(f"outdated: the plan is at v{head}; the write was against v{base_rev}")
        self.head = head
        self.base_rev = base_rev
        self.since = since
        self.conflicts = conflicts
        self.state = state

    def text(self, name: str) -> str:
        return "\n".join(
            [
                (
                    f'! outdated: plan "{name}" is at v{self.head}; you wrote against '
                    f"v{self.base_rev}. Nothing was applied."
                ),
                "conflicts: " + "; ".join(c.text() for c in self.conflicts),
                f"since v{self.base_rev}: " + " · ".join(c.text() for c in self.since),
                f're-read (list_plans name="{name}") and push again with base_rev={self.head}',
            ]
        )


#: Works out a plan's ``plan_id`` and ``provenance`` from its new head, under the lock.
Stamp = Callable[[PlanState], PlanStamp]

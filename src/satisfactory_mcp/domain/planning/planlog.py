"""The plan log: each plan an append-only list of commits, with snapshots, merged by rule M1.

docs/planner_slice_contract.md §2-§5 and §7 are the specification, and docs/plan_log.md says
how this module maps onto it. Needs no game data.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path

from ...core import atomic, filelock, schema
from .store import PLAN_ARGS, Plan, PlanStore

__all__ = [
    "FACTORY_SENTINELS",
    "OBJECTIVES",
    "POWER",
    "SNAPSHOT_EVERY",
    "Actor",
    "AlreadyUndone",
    "BaseRevRequired",
    "Commit",
    "Conflict",
    "Forgotten",
    "InvalidOp",
    "NameTaken",
    "Outdated",
    "PlanArgs",
    "PlanLog",
    "PlanLogError",
    "PlanState",
    "PlanView",
    "Pushed",
    "Stamp",
    "UnknownPlan",
    "describe_commit",
    "describe_op",
    "diff_args",
    "factory_words",
    "inverse",
    "is_power",
    "merge_key",
    "use_recipe_names",
]

SNAPSHOT_EVERY = 50
SCHEMA = 1
OBJECTIVES = ("max_mw", "max_item", "min_raw", "min_machines", "min_power")
MINUS = "−"
ARROW = "→"
POWER = "MW"
_POWER_SPELLINGS = frozenset({"mw", "power", "__mw__"})
_POWER_FIELDS = frozenset({"exports", "export_minimums"})

_log = logging.getLogger(__name__)

KINDS: dict[str, str] = {
    "objective": "scalar",
    "target_item": "scalar",
    "sources": "set",
    "exports": "set",
    "export_minimums": "map",
    "only_free_nodes": "scalar",
    "allow_sinks": "scalar",
    "clocks": "set",
    "extractor_clocks": "set",
    "machine_cost_mw": "scalar",
    "banned": "set",
    "required": "set",
    "only_recipes": "set",
    "water_extractors": "scalar",
    "sloops": "scalar",
    "belt_ipm": "scalar",
    "pipe_m3min": "scalar",
    "recycle_once": "set",
    "supplied": "map",
    "logistics_items": "set",
    "payback_hours": "scalar",
    "overclock_last": "scalar",
    "power_price": "scalar",
}
FLOAT_SETS = frozenset({"clocks", "extractor_clocks"})
PLAN_SCALARS = ("notes", "factory", "headroom_mw")
HEADROOM_MAX_MW = 1_000_000.0
PAYBACK_MAX_H = 100.0
PRICE_MAX = 100_000.0
#: A stored ``power_priority`` step as the horizon where a Refinery's best clock equals the
#: old cap (docs/planner-payback-horizon_contract.md §8); step 0 inherits.
LEGACY_PRIORITY_HOURS = (None, 4.0, 7.0, 11.0, 17.0)
KWARG_NAME = {"banned": "exclude_recipes"}


class PlanLogError(Exception):
    """Anything the plan log refuses."""


class InvalidOp(PlanLogError, ValueError):
    pass


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


def _fail(message: str) -> InvalidOp:
    return InvalidOp(message)


def is_power(name) -> bool:
    """Whether ``name`` means grid power: ``MW``, ``mw``, ``power`` or the solver's ``__MW__``."""
    return isinstance(name, str) and name.strip().casefold() in _POWER_SPELLINGS


def _canon(fieldname: str, name):
    """``POWER`` for any spelling of power in the fields that take it, else ``name``."""
    return POWER if fieldname in _POWER_FIELDS and is_power(name) else name


def _number(name: str, value, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(f"{name} must be a number, not {json.dumps(value, default=str)}")
    if not math.isfinite(value):
        raise _fail(f"{name} must be finite, not {value!r}")
    return float(value)


def _count(name: str, value, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{name} must be a whole number, not {json.dumps(value, default=str)}")
    if value < 0:
        raise _fail(f"{name} must be 0 or more, not {value}")
    return value


def _text(name: str, value, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _fail(f"{name} must be text, not {value!r}")
    return value


#: ``factory`` values that are not a factory name: count the whole world, or nothing yet.
FACTORY_SENTINELS = ("/world", "/none")


def _factory(name: str, value) -> str:
    text = _text(name, value)
    if text.startswith("/") and text not in FACTORY_SENTINELS:
        raise _fail(f"{name} is a factory name, {' or '.join(FACTORY_SENTINELS)}, not {text!r}")
    return text


def _flag(name: str, value) -> bool:
    if not isinstance(value, bool):
        raise _fail(f"{name} must be true or false, not {value!r}")
    return value


def _headroom(name: str, value) -> float | None:
    number = _number(name, value, optional=True)
    if number is not None and not 0 < number <= HEADROOM_MAX_MW:
        raise _fail(f"{name} must be above 0 and at most {HEADROOM_MAX_MW:,.0f} MW, not {value!r}")
    return number


def _inherits(value) -> bool:
    return value is None or value == "default"


def _hours(name: str, value) -> float | None:
    if _inherits(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PAYBACK_MAX_H:
        raise _fail(f"{name} must be 0 to {PAYBACK_MAX_H:g} hours or default, not {value!r}")
    return number


def _price(name: str, value) -> float | None:
    if _inherits(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PRICE_MAX:
        raise _fail(
            f"{name} must be 0 to {PRICE_MAX:,.0f} points per MWh or default, not {value!r}"
        )
    return number


def _switch(name: str, value) -> bool | None:
    return None if _inherits(value) else _flag(name, value)


def legacy_hours(step) -> float | None:
    """A stored ``power_priority`` step read as its payback horizon."""
    if isinstance(step, bool) or not isinstance(step, int | float) or step != int(step):
        return None
    return LEGACY_PRIORITY_HOURS[int(step)] if 0 <= step < len(LEGACY_PRIORITY_HOURS) else None


def _legacy_op(op):
    """An old ``set power_priority`` op as the ``set payback_hours`` it now means."""
    if not isinstance(op, dict) or op.get("field") != "power_priority" or op.get("op") != "set":
        return op
    out = {"op": "set", "field": "payback_hours", "value": legacy_hours(op.get("value"))}
    if "was" in op:
        out["was"] = legacy_hours(op.get("was"))
    return out


def _objective(name: str, value) -> str:
    if value not in OBJECTIVES:
        raise _fail(f"objective must be one of {', '.join(OBJECTIVES)}, not {value!r}")
    return value


_SCALAR_CHECK: dict[str, Callable] = {
    "objective": _objective,
    "target_item": lambda n, v: _text(n, v, optional=True),
    "only_free_nodes": _flag,
    "allow_sinks": _flag,
    "machine_cost_mw": _number,
    "water_extractors": lambda n, v: _count(n, v, optional=True),
    "sloops": _count,
    "belt_ipm": lambda n, v: _number(n, v, optional=True),
    "pipe_m3min": lambda n, v: _number(n, v, optional=True),
    "notes": _text,
    "factory": _factory,
    "headroom_mw": _headroom,
    "payback_hours": _hours,
    "overclock_last": _switch,
    "power_price": _price,
}


def _member(fieldname: str, value):
    if fieldname in FLOAT_SETS:
        return _number(f"{fieldname} member", value)
    text = _text(f"{fieldname} member", value)
    if not text:
        raise _fail(f"{fieldname} member cannot be blank")
    return _canon(fieldname, text)


def _ident(fieldname: str, member) -> str:
    return f"{member:g}" if fieldname in FLOAT_SETS else _canon(fieldname, member)


def _item(fieldname: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{fieldname} needs an item name, not {value!r}")
    return _canon(fieldname, value)


def _check_field(name: str, value):
    kind = KINDS[name]
    if kind == "scalar":
        return _SCALAR_CHECK[name](name, value)
    if kind == "set":
        if not isinstance(value, list | tuple):
            raise _fail(f"{name} must be a list, not {value!r}")
        out, seen = [], set()
        for raw in value:
            member = _member(name, raw)
            if _ident(name, member) not in seen:
                seen.add(_ident(name, member))
                out.append(member)
        return out
    if not isinstance(value, dict):
        raise _fail(f"{name} must be a mapping, not {value!r}")
    return {_item(name, k): _number(f"{name}[{k}]", v) for k, v in value.items()}


@dataclass
class PlanArgs:
    objective: str = "max_mw"
    target_item: str | None = None
    sources: list = field(default_factory=list)
    exports: list = field(default_factory=list)
    export_minimums: dict = field(default_factory=dict)
    only_free_nodes: bool = False
    allow_sinks: bool = True
    clocks: list = field(default_factory=list)
    extractor_clocks: list = field(default_factory=list)
    machine_cost_mw: float = 5.0
    banned: list = field(default_factory=list)
    required: list = field(default_factory=list)
    only_recipes: list = field(default_factory=list)
    water_extractors: int | None = None
    sloops: int = 0
    belt_ipm: float | None = None
    pipe_m3min: float | None = None
    recycle_once: list = field(default_factory=list)
    supplied: dict = field(default_factory=dict)
    logistics_items: list = field(default_factory=list)
    payback_hours: float | None = None
    overclock_last: bool | None = None
    power_price: float | None = None

    @classmethod
    def from_dict(cls, raw: dict | None, lenient: list | None = None) -> PlanArgs:
        """Absent, None, [] and {} mean the default. ``lenient`` collects refused fields
        as ``(name, value)`` instead of raising, for migration."""
        raw = dict(raw or {})
        step = raw.pop("power_priority", None)
        if raw.get("payback_hours") is None and step is not None:
            raw["payback_hours"] = legacy_hours(step)
        if "exclude_recipes" in raw:
            spelled = raw.pop("exclude_recipes")
            if raw.get("banned") in (None, [], {}):
                raw["banned"] = spelled
        out = cls()
        for name in KINDS:
            value = raw.get(name)
            if value is None or (isinstance(value, list | tuple | dict) and not value):
                continue
            try:
                setattr(out, name, _check_field(name, value))
            except InvalidOp:
                if lenient is None:
                    raise
                lenient.append((name, value))
        return out

    def to_dict(self) -> dict:
        return {f.name: copy.deepcopy(getattr(self, f.name)) for f in fields(self)}

    def kwargs(self) -> dict:
        blank = PlanArgs()
        out = {}
        for name in KINDS:
            value = getattr(self, name)
            if name == "logistics_items" or value == getattr(blank, name):
                continue
            out[KWARG_NAME.get(name, name)] = copy.deepcopy(value)
        return out


def _stored_headroom(value) -> float | None:
    try:
        return _headroom("headroom_mw", value)
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
    provenance: dict = field(default_factory=dict)
    siting: dict = field(default_factory=dict)
    args: PlanArgs = field(default_factory=PlanArgs)
    headroom_mw: float | None = None

    def kwargs(self) -> dict:
        return self.args.kwargs()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "rev": self.rev,
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

    @classmethod
    def from_dict(cls, raw: dict, key: str = "", rev: int = 0) -> PlanState:
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

    def body(self) -> dict:
        """The ``create`` op's ``state``: everything but key and rev."""
        out = self.to_dict()
        del out["key"], out["rev"]
        return out


_CLIENT_NAMES = {"claude-code": "Claude Code", "claude-ai": "Claude Desktop"}


@dataclass(frozen=True)
class Actor:
    kind: str
    client: str = ""
    pid: int = 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "client": self.client, "pid": self.pid}

    @classmethod
    def from_dict(cls, raw: dict | None) -> Actor:
        raw = raw or {}
        return cls(
            str(raw.get("kind") or "system"), str(raw.get("client") or ""), int(raw.get("pid") or 0)
        )

    def display(self) -> str:
        if self.kind == "chat":
            return _CLIENT_NAMES.get(self.client, self.client or "chat")
        if self.kind == "migration":
            return "migrated"
        return self.kind


def _fmt(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


_namer: list[Callable[[], dict[str, str]]] = []
_POWER_WORDS = ("payback_hours", "overclock_last", "power_price")


def use_recipe_names(source: Callable[[], dict[str, str]] | None) -> None:
    _namer[:] = [source] if source is not None else []


def _member_name(field_name: str, member) -> str:
    if not _namer or field_name not in ("banned", "required"):
        return _fmt(member)
    try:
        return _namer[0]().get(member, _fmt(member))
    except Exception:
        return _fmt(member)


def factory_words(value) -> str:
    """A stored ``factory`` value in words."""
    text = str(value or "")
    return {"": "found automatically", "/world": "whole world", "/none": "nothing yet"}.get(
        text, text
    )


def _hours_text(value: float) -> str:
    return f"{value:,.1f}".rstrip("0").rstrip(".") + " h"


def _power_words(name: str, value) -> str:
    if name == "payback_hours":
        return "payback: shared default" if value is None else "payback " + _hours_text(value)
    if name == "overclock_last":
        word = "shared default" if value is None else _fmt(value)
        return "overclock last machine: " + word
    return "power price: grid mix" if value is None else f"power price {_fmt(value)} pts/MWh"


def describe_op(op: dict) -> str:
    kind, name = op.get("op"), op.get("field", "")
    if kind == "set":
        if name == "notes":
            return "notes changed"
        if name == "factory":
            return "count as built: " + factory_words(op.get("value"))
        if name == "headroom_mw":
            value = op.get("value")
            if value is None:
                return "startup headroom: save default"
            return f"startup headroom {_fmt(float(value))} MW"
        if name in _POWER_WORDS:
            return _power_words(name, op.get("value"))
        return f"{name} {_fmt(op.get('was'))}{ARROW}{_fmt(op.get('value'))}"
    if kind in ("put", "del"):
        word = "rate" if name == "export_minimums" else name
        item = op.get("item", "")
        if kind == "del":
            return f"{MINUS}{word} {item}"
        was = op.get("was")
        if was is None and name == "export_minimums":
            return f"+{word} {item} {_fmt(op.get('value'))}/min"
        return (
            f"{word} {item} {_fmt(0.0 if was is None else was)}{ARROW}{_fmt(op.get('value'))}/min"
        )
    if kind in ("add", "remove"):
        sign = "+" if kind == "add" else MINUS
        return f"{sign}{name} {_member_name(name, op.get('member'))}"
    if kind == "site":
        if not op.get("value"):
            return "site cleared"
        return "site moved" if op.get("was") else "site set"
    if kind == "rename":
        return f'renamed "{op.get("was", "")}"{ARROW}"{op.get("name", "")}"'
    return {"create": "created", "forget": "forgotten", "restore": "restored"}.get(kind, "")


def _ops_text(ops: list[dict]) -> str:
    return " · ".join(t for t in (describe_op(o) for o in ops) if t)


@dataclass
class Commit:
    rev: int
    base_rev: int
    ts: float
    actor: Actor
    sav: str
    ops: list[dict]
    merged_over: list[int] = field(default_factory=list)
    undoes: int | None = None
    note: str = ""

    def to_dict(self) -> dict:
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
    def from_dict(cls, raw: dict) -> Commit:
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


def describe_commit(commit: Commit) -> str:
    ops = _ops_text(commit.ops)
    head = f"v{commit.rev} {commit.actor.display()}"
    if commit.undoes is not None:
        return f"{head}: undo v{commit.undoes}" + (f" ({ops})" if ops else "")
    return f"{head}: {ops or commit.note or 'recorded'}"


def merge_key(op: dict) -> str | None:
    kind, name = op.get("op"), op.get("field", "")
    if kind == "set":
        return name
    if kind in ("put", "del"):
        return f"{name}[{_canon(name, op.get('item'))}]"
    if kind in ("add", "remove"):
        return f"{name}{{{_ident(name, op.get('member'))}}}"
    if kind == "site":
        return "site"
    if kind == "rename":
        return "name"
    if kind in ("create", "forget", "restore"):
        return "lifecycle"
    return None


_INVERSE_KIND = {"add": "remove", "remove": "add", "forget": "restore", "restore": "forget"}


def _inverse_one(op: dict) -> dict | None:
    kind = op["op"]
    if kind == "set":
        return {"op": "set", "field": op["field"], "value": op.get("was")}
    if kind == "put":
        if op.get("was") is None:
            return {"op": "del", "field": op["field"], "item": op["item"]}
        return {"op": "put", "field": op["field"], "item": op["item"], "value": op["was"]}
    if kind == "del":
        return {"op": "put", "field": op["field"], "item": op["item"], "value": op.get("was")}
    if kind in ("add", "remove"):
        return {"op": _INVERSE_KIND[kind], "field": op["field"], "member": op["member"]}
    if kind == "site":
        return {"op": "site", "value": op.get("was") or None}
    if kind == "rename":
        return {"op": "rename", "name": op.get("was", "")}
    if kind in ("forget", "restore"):
        return {"op": _INVERSE_KIND[kind]}
    if kind == "create":
        return {"op": "forget"}
    return None


def inverse(ops: list[dict]) -> list[dict]:
    """The ops that take a state back over ``ops``, in reverse order; records are skipped."""
    return [inv for inv in (_inverse_one(op) for op in reversed(ops)) if inv is not None]


def _check_op(op: dict) -> dict:
    """A canonical copy of a writer's op, type-checked; ``was`` is the store's to fill."""
    if not isinstance(op, dict):
        raise _fail(f"an op must be an object, not {op!r}")
    kind = op.get("op")
    name = op.get("field")
    if kind == "set":
        if name not in _SCALAR_CHECK:
            raise _fail(f"set does not apply to {name!r}")
        return {"op": "set", "field": name, "value": _SCALAR_CHECK[name](name, op.get("value"))}
    if kind in ("put", "del"):
        if KINDS.get(name) != "map":
            raise _fail(f"{kind} does not apply to {name!r}")
        out = {"op": kind, "field": name, "item": _item(name, op.get("item"))}
        if kind == "put":
            out["value"] = _number(f"{name}[{out['item']}]", op.get("value"))
        return out
    if kind in ("add", "remove"):
        if KINDS.get(name) != "set":
            raise _fail(f"{kind} does not apply to {name!r}")
        return {"op": kind, "field": name, "member": _member(name, op.get("member"))}
    if kind == "site":
        value = op.get("value")
        if value is not None and not isinstance(value, dict):
            raise _fail(f"site takes a siting object or null, not {value!r}")
        return {"op": "site", "value": copy.deepcopy(value) or None}
    if kind == "rename":
        wanted = op.get("name")
        if not isinstance(wanted, str) or not wanted.strip():
            raise _fail("a plan name cannot be blank")
        return {"op": "rename", "name": wanted.strip()}
    if kind in ("forget", "restore"):
        return {"op": kind}
    if kind == "record":
        if name not in ("plan_id", "provenance"):
            raise _fail(f"record does not apply to {name!r}")
        value = op.get("value")
        if name == "plan_id" and not isinstance(value, str):
            raise _fail("plan_id must be text")
        if name == "provenance" and not isinstance(value, dict):
            raise _fail("provenance must be an object")
        return {"op": "record", "field": name, "value": copy.deepcopy(value)}
    if kind == "create":
        raise _fail("create happens only at rev 1")
    raise _fail(f"unknown op {kind!r}")


def _current(state: PlanState, op: dict):
    kind, name = op["op"], op.get("field")
    if kind == "set":
        return getattr(state, name) if name in PLAN_SCALARS else getattr(state.args, name)
    if kind in ("put", "del"):
        return getattr(state.args, name).get(_canon(name, op["item"]))
    if kind in ("add", "remove"):
        return any(_ident(name, m) == _ident(name, op["member"]) for m in getattr(state.args, name))
    if kind == "site":
        return copy.deepcopy(state.siting) or None
    if kind == "rename":
        return state.name
    if kind in ("forget", "restore"):
        return state.forgotten
    if kind == "record":
        return getattr(state, name)
    return None


def _is_noop(state: PlanState, op: dict) -> bool:
    kind, now = op["op"], _current(state, op)
    if kind in ("set", "put", "record"):
        return now == op["value"]
    if kind == "del":
        return now is None
    if kind == "add":
        return now
    if kind == "remove":
        return not now
    if kind == "site":
        return now == (op["value"] or None)
    if kind == "rename":
        return now == op["name"]
    return False


def _apply(state: PlanState, op: dict) -> None:
    kind, name = op["op"], op.get("field")
    if kind == "set":
        setattr(state if name in PLAN_SCALARS else state.args, name, copy.deepcopy(op["value"]))
    elif kind == "put":
        getattr(state.args, name)[_canon(name, op["item"])] = op["value"]
    elif kind == "del":
        getattr(state.args, name).pop(_canon(name, op["item"]), None)
    elif kind == "add":
        if not _current(state, op):
            getattr(state.args, name).append(_canon(name, op["member"]))
    elif kind == "remove":
        wanted = _ident(name, op["member"])
        setattr(
            state.args, name, [m for m in getattr(state.args, name) if _ident(name, m) != wanted]
        )
    elif kind == "site":
        state.siting = copy.deepcopy(op.get("value")) or {}
    elif kind == "rename":
        state.name = op["name"]
    elif kind in ("forget", "restore"):
        state.forgotten = kind == "forget"
    elif kind == "record":
        setattr(state, name, copy.deepcopy(op["value"]))


def _with_was(state: PlanState, op: dict) -> dict:
    out = dict(op)
    if op["op"] in ("set", "put", "del", "site"):
        out["was"] = _current(state, op)
    elif op["op"] == "rename":
        out["was"] = state.name
    return out


def _value(op: dict):
    if op["op"] == "rename":
        return op["name"]
    return op.get("value")


def _clash(mine: dict, theirs: dict) -> str | None:
    """``"conflict"``, ``"same"`` (mine is already applied) or None, per contract §4.1."""
    mk, tk = merge_key(mine), merge_key(theirs)
    if mk is None or tk is None:
        return None
    if theirs["op"] == "forget" or mine["op"] == "forget":
        return "conflict"
    if (
        mine["op"] == "add"
        and theirs["op"] == "add"
        and {mine["field"], theirs["field"]} == {"required", "banned"}
        and mine["member"] == theirs["member"]
    ):
        return "conflict"
    if mk != tk:
        return None
    if mine["op"] != theirs["op"]:
        return "conflict"
    if mine["op"] in ("set", "put", "site", "rename"):
        return "same" if _value(mine) == _value(theirs) else "conflict"
    return "same"


def _label(op: dict) -> str:
    kind, name = op["op"], op.get("field", "")
    if kind in ("put", "del"):
        return f"{'rate' if name == 'export_minimums' else name} {op['item']}"
    if kind in ("add", "remove"):
        return f"{name} {_fmt(op['member'])}"
    if kind == "set":
        return "startup headroom" if name == "headroom_mw" else name
    if kind == "site":
        return "site"
    if kind == "rename":
        return "name"
    return "plan"


def _value_word(op: dict) -> str:
    if op.get("field") in _POWER_WORDS:
        return _power_words(op["field"], op.get("value")).split(": ")[-1]
    if op.get("field") == "headroom_mw":
        value = op.get("value")
        return "save default" if value is None else f"{_fmt(float(value))} MW"
    return _fmt(op["value"])


def _did(op: dict) -> str:
    kind = op["op"]
    if kind in ("set", "put"):
        return f"set {_value_word(op)}"
    if kind == "del":
        return "removed it"
    if kind == "add":
        return f"added {op['field']} {_fmt(op['member'])}"
    if kind == "remove":
        return f"removed {op['field']} {_fmt(op['member'])}"
    if kind == "site":
        return "moved the site" if op.get("value") else "cleared the site"
    if kind == "rename":
        return f'renamed it "{op["name"]}"'
    return {"forget": "forgot the plan", "restore": "restored the plan"}.get(kind, kind)


@dataclass
class Conflict:
    key: str
    mine: dict
    theirs: dict
    theirs_rev: int
    theirs_actor: Actor

    def text(self) -> str:
        mine = _value_word(self.mine) if self.mine["op"] in ("set", "put") else _did(self.mine)
        who = self.theirs_actor.display()
        return f"{_label(self.mine)}: you {mine}, {who} {_did(self.theirs)} in v{self.theirs_rev}"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "mine": copy.deepcopy(self.mine),
            "theirs": copy.deepcopy(self.theirs),
            "theirs_rev": self.theirs_rev,
            "theirs_actor": self.theirs_actor.to_dict(),
            "text": self.text(),
        }


def _other_text(commit: Commit) -> str:
    return f"v{commit.rev} {_ops_text(commit.ops) or 'recorded'} ({commit.actor.display()})"


@dataclass
class Pushed:
    key: str
    rev: int
    base_rev: int
    applied: list[dict]
    dropped: list[dict]
    merged_over: list[int]
    others: list[Commit]
    noop: bool
    state: PlanState

    def text(self, name: str) -> str:
        if self.noop:
            return f'nothing changed: plan "{name}" is still v{self.rev}'
        if self.merged_over:
            others = ", ".join(_other_text(c) for c in self.others)
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


def _norm_new(new: dict) -> tuple[PlanArgs, set[str]]:
    raw = {k: v for k, v in dict(new).items() if k in KINDS or k == "exclude_recipes"}
    present = {("banned" if k == "exclude_recipes" else k) for k in raw}
    return PlanArgs.from_dict(raw), present


def diff_args(base: PlanArgs | dict, new: dict, partial: bool = False) -> list[dict]:
    """Ops that turn ``base`` into ``new``; with ``partial`` only fields ``new`` names count."""
    old = base if isinstance(base, PlanArgs) else PlanArgs.from_dict(base)
    target, present = _norm_new(new)
    ops: list[dict] = []
    for name, kind in KINDS.items():
        if partial and name not in present:
            continue
        before, after = getattr(old, name), getattr(target, name)
        if kind == "scalar":
            if before != after:
                ops.append({"op": "set", "field": name, "value": after})
        elif kind == "set":
            had = {_ident(name, m) for m in before}
            has = {_ident(name, m) for m in after}
            ops += [
                {"op": "add", "field": name, "member": m}
                for m in after
                if _ident(name, m) not in had
            ]
            ops += [
                {"op": "remove", "field": name, "member": m}
                for m in before
                if _ident(name, m) not in has
            ]
        else:
            ops += [
                {"op": "put", "field": name, "item": k, "value": v}
                for k, v in after.items()
                if before.get(k) != v
            ]
            ops += [{"op": "del", "field": name, "item": k} for k in before if k not in after]
    return ops


def _replay(state: PlanState | None, commits: list[Commit], key: str) -> PlanState | None:
    for commit in commits:
        for op in commit.ops:
            if op.get("op") == "create":
                state = PlanState.from_dict(op["state"], key=key, rev=commit.rev)
                state.key = key
            elif state is not None:
                _apply(state, op)
        if state is not None:
            state.rev = commit.rev
    return state


def _read_commits(path: Path) -> list[Commit]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    out = []
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


def _undoers(commits: list[Commit], rev: int) -> Commit | None:
    """The commit that undid ``rev`` and still stands, or None."""
    for later in reversed(commits):
        if later.undoes == rev and _undoers(commits, later.rev) is None:
            return later
    return None


def _chain(commits: list[Commit], rev: int) -> set[int]:
    out = {rev}
    for commit in commits:
        if commit.undoes in out:
            out.add(commit.rev)
    return out


def _skipped(commits: list[Commit], rev: int) -> set[int]:
    """Revs an undo of ``rev`` ignores: its own chain, and every later commit that stands
    undone together with its chain, since the two cancel out (docs/plan_log.md, Undo)."""
    out = _chain(commits, rev)
    for commit in commits[rev:]:
        if commit.rev not in out and _undoers(commits, commit.rev) is not None:
            out |= _chain(commits, commit.rev)
    return out


def _names(ops: list[dict]) -> bool:
    """Whether ``ops`` can clash on a name across plans, and so need the world lock."""
    return any(op.get("op") in ("rename", "restore") for op in ops)


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


def _pick(items: list, name_of: Callable, needle: str):
    wanted = needle.strip().casefold()
    exact = [x for x in items if name_of(x).casefold() == wanted]
    if exact:
        return exact[0]
    hits = [x for x in items if wanted in name_of(x).casefold()]
    return hits[0] if len(hits) == 1 else None


@dataclass
class PlanView:
    """The read-only ``WorldState.plans``: live plans as ``Plan`` objects with key and rev."""

    world_id: str
    session_name: str = ""
    plans: list[Plan] = field(default_factory=list)

    def find(self, name: str) -> Plan | None:
        by_key = [p for p in self.plans if p.key == name.strip().lower()]
        return by_key[0] if by_key else _pick(self.plans, lambda p: p.name, name)


Stamp = Callable[[PlanState], dict]

_warned: set[str] = set()


class PlanLog:
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

    def _world_lock(self):
        return filelock.held(self.root / "world")

    def _plan_lock(self, key: str):
        return filelock.held(self._ops(key))

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
            raise _fail(f"plan {key} has no v{rev}; it is at v{head}")
        base = None
        snaps = self.root / key / "snap"
        revs = sorted((int(p.stem) for p in snaps.glob("*.json") if p.stem.isdigit()), reverse=True)
        for snap in (r for r in revs if r <= rev):
            try:
                raw = json.loads((snaps / f"{snap}.json").read_text(encoding="utf-8"))
                schema.check(raw, SCHEMA, snaps / f"{snap}.json")
                if raw.get("rev") != snap or raw.get("key") != key:
                    continue
                base = PlanState.from_dict(raw["state"], key=key, rev=snap)
                break
            except (OSError, ValueError, KeyError, TypeError, InvalidOp):
                continue
        start = base.rev if base is not None else 0
        state = _replay(base, [c for c in commits if start < c.rev <= rev], key)
        if state is None:
            raise UnknownPlan(key, [])
        return state

    def heads(self, include_forgotten: bool = False) -> list[PlanState]:
        rows = []
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
        hit = _pick(live, lambda s: s.name, name_or_key)
        if hit is None and include_forgotten:
            hit = _pick(
                list(reversed([s for s in states if s.forgotten])), lambda s: s.name, name_or_key
            )
        return hit

    def view(self) -> PlanView:
        return PlanView(self.world_id, self.session_name, [_as_plan(s) for s in self.heads()])

    def _taken(self, name: str, key: str | None = None) -> bool:
        wanted = name.strip().casefold()
        return any(s.key != key and s.name.casefold() == wanted for s in self.heads())

    def free_name(self, name: str, key: str | None = None) -> str:
        """``name`` stripped, or ``InvalidOp`` / ``NameTaken`` saying why a plan other than
        ``key`` cannot take it. Case-insensitive, as ``find`` is. Unlocked: a write re-checks."""
        wanted = _text("name", name).strip()
        if not wanted:
            raise _fail("a plan name cannot be blank")
        if self._taken(wanted, key):
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
        args: dict,
        *,
        actor: Actor,
        sav: str = "",
        notes: str = "",
        factory: str = "",
        siting: dict | None = None,
        plan_id: str = "",
        provenance: dict | None = None,
        created: str = "",
        note: str = "",
    ) -> Pushed:
        wanted = self.free_name(name)
        state = PlanState(
            key="",
            rev=1,
            name=wanted,
            notes=_text("notes", notes),
            factory=_text("factory", factory),
            created=_text("created", created),
            plan_id=_text("plan_id", plan_id),
            provenance=copy.deepcopy(provenance or {}),
            siting=copy.deepcopy(siting or {}),
            args=args if isinstance(args, PlanArgs) else PlanArgs.from_dict(args),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with self._world_lock():
            if self._taken(wanted):
                raise NameTaken(wanted)
            key = secrets.token_hex(4)
            while (self.root / key).exists():
                key = secrets.token_hex(4)
            return self._create_locked(key, state, actor, sav, note)

    def _create_locked(
        self, key: str, state: PlanState, actor: Actor, sav: str, note: str
    ) -> Pushed:
        state.key = key
        op = {"op": "create", "name": state.name, "state": state.body()}
        commit = Commit(1, 0, _now(), actor, sav, [op], [], None, note)
        (self.root / key).mkdir(parents=True, exist_ok=True)
        with self._plan_lock(key):
            _append(self._ops(key), commit)
            self._snapshot(state)
        return Pushed(key, 1, 0, [op], [], [], [], False, state)

    def push(
        self,
        key: str,
        base_rev: int,
        ops: list[dict],
        *,
        actor: Actor,
        sav: str = "",
        stamp: Stamp | None = None,
        note: str = "",
        extend: Callable[[PlanState], list[dict]] | None = None,
    ) -> Pushed:
        """``extend`` adds ops worked out from the head under the plan lock; they merge by
        M1 like the rest, so one that clashes with a commit since ``base_rev`` refuses."""
        mine = [_check_op(op) for op in ops]

        def run(commits: list[Commit]) -> Pushed:
            more = [_check_op(op) for op in extend(self._state(key, commits))] if extend else []
            return self._merge(
                key,
                commits,
                base_rev,
                mine + [op for op in more if op not in mine],
                actor,
                sav,
                stamp,
                note,
                None,
            )

        return self._locked(_names(mine), key, run)

    def push_args(
        self,
        key: str,
        base_rev: int,
        args: dict,
        *,
        actor: Actor,
        sav: str = "",
        extra: list[dict] | None = None,
        stamp: Stamp | None = None,
        note: str = "",
    ) -> Pushed:
        base = self.state(key, self._valid_base(key, base_rev))
        ops = diff_args(base.args, args, partial=False) + list(extra or ())
        return self.push(key, base_rev, ops, actor=actor, sav=sav, stamp=stamp, note=note)

    def _valid_base(self, key: str, base_rev) -> int:
        return self._valid_base_in(self._all(key), base_rev)

    def undo(
        self,
        key: str,
        base_rev: int,
        rev: int,
        *,
        actor: Actor,
        sav: str = "",
        stamp: Stamp | None = None,
    ) -> Pushed:
        commits = self._all(key)
        if isinstance(rev, bool) or not isinstance(rev, int) or not 1 <= rev <= commits[-1].rev:
            raise _fail(f"plan {key} has no v{rev} to undo")
        if rev == 1:
            raise _fail("v1 created the plan and cannot be undone; forget the plan instead")
        target = commits[rev - 1]
        renames = _names(inverse(target.ops))

        def run(current: list[Commit]) -> Pushed:
            by = _undoers(current, rev)
            if by is not None:
                raise AlreadyUndone(rev, by.rev)
            mine = inverse([op for op in target.ops if op.get("op") != "record"])
            window = _skipped(current, rev)
            return self._merge(key, current, base_rev, mine, actor, sav, stamp, "", rev, window)

        return self._locked(renames, key, run)

    def restore_to(
        self,
        key: str,
        base_rev: int,
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
            mine = [_check_op(op) for op in ops]
            pushed = self._merge(
                key, current, base_rev, mine, actor, sav, stamp, f"restore v{rev}", None
            )
            if not pushed.noop:
                self._snapshot(pushed.state)
            return pushed

        return self._locked(True, key, run)

    def push_at_head(self, key: str, ops: list[dict], *, actor: Actor, note: str = "") -> Pushed:
        mine = [_check_op(op) for op in ops]

        def run(current: list[Commit]) -> Pushed:
            head = current[-1].rev
            return self._merge(key, current, head, mine, actor, "", None, note, None)

        return self._locked(_names(mine), key, run)

    def _locked(self, world: bool, key: str, run: Callable[[list[Commit]], Pushed]) -> Pushed:
        self._all(key)
        if world:
            with self._world_lock(), self._plan_lock(key):
                return run(self._all(key))
        with self._plan_lock(key):
            return run(self._all(key))

    def _merge(
        self,
        key: str,
        commits: list[Commit],
        base_rev,
        mine: list[dict],
        actor: Actor,
        sav: str,
        stamp: Stamp | None,
        note: str,
        undoes: int | None,
        window: set[int] | None = None,
    ) -> Pushed:
        head_rev = commits[-1].rev
        base_rev = self._valid_base_in(commits, base_rev)
        base_state = self._state(key, commits, base_rev)
        if base_state.forgotten and not any(op["op"] == "restore" for op in mine):
            forgot = max(
                c.rev for c in commits[:base_rev] if any(o.get("op") == "forget" for o in c.ops)
            )
            raise Forgotten(key, forgot)
        since = commits[base_rev:]
        if undoes is None:
            against = since
        else:
            against = [c for c in commits[undoes:] if c.rev not in window]
        conflicts: list[Conflict] = []
        for op in mine:
            for commit in against:
                for theirs in commit.ops:
                    if _clash(op, theirs) == "conflict":
                        conflicts.append(
                            Conflict(merge_key(op) or "", op, theirs, commit.rev, commit.actor)
                        )
        head = self._state(key, commits)
        if conflicts:
            raise Outdated(head_rev, base_rev, since, conflicts, head)

        work = copy.deepcopy(head)
        applied, dropped = [], []
        restored = any(t.get("op") == "restore" for c in since for t in c.ops)
        for op in mine:
            if _is_noop(work, op) or (op["op"] == "restore" and restored and not work.forgotten):
                dropped.append(op)
                continue
            self._validate_against(work, op, key)
            filled = _with_was(work, op)
            _apply(work, filled)
            applied.append(filled)
        if not applied:
            return Pushed(key, head_rev, base_rev, [], dropped, [], [], True, head)
        if _names(applied) and not work.forgotten and self._taken(work.name, key):
            raise NameTaken(work.name)
        applied += self._stamped(work, stamp)
        for op in applied:
            if op["op"] == "record":
                _apply(work, op)
        work.rev = head_rev + 1
        merged = [c.rev for c in since]
        commit = Commit(work.rev, base_rev, _now(), actor, sav, applied, merged, undoes, note[:200])
        _append(self._ops(key), commit)
        if work.rev % SNAPSHOT_EVERY == 0:
            self._snapshot(work)
        return Pushed(key, work.rev, base_rev, applied, dropped, merged, list(since), False, work)

    @staticmethod
    def _valid_base_in(commits: list[Commit], base_rev) -> int:
        head = commits[-1].rev
        if base_rev is None:
            raise BaseRevRequired(head)
        if isinstance(base_rev, bool) or not isinstance(base_rev, int):
            raise _fail(f"base_rev must be a whole number, not {base_rev!r}")
        if base_rev < 1 or base_rev > head:
            raise _fail(f"base_rev v{base_rev} does not exist; the plan is at v{head}")
        return base_rev

    def _validate_against(self, work: PlanState, op: dict, key: str) -> None:
        if op["op"] == "forget" and work.forgotten:
            raise _fail("the plan is already forgotten")
        if op["op"] == "restore" and not work.forgotten:
            raise _fail("the plan is not forgotten")

    @staticmethod
    def _stamped(work: PlanState, stamp: Stamp | None) -> list[dict]:
        if stamp is None:
            return []
        try:
            got = stamp(copy.deepcopy(work)) or {}
        except Exception:
            _log.warning("could not stamp plan %s; its plan_id is cleared", work.key, exc_info=True)
            got = {"plan_id": ""}
        out = []
        for name in ("plan_id", "provenance"):
            if name in got and got[name] != getattr(work, name):
                try:
                    out.append(_check_op({"op": "record", "field": name, "value": got[name]}))
                except InvalidOp:
                    continue
        return out

    def _legacy(self) -> Path:
        return PlanStore.path_for(self.world_id)

    def _backup(self, legacy: Path, version: str) -> Path:
        """Copy the legacy file to ``backup-v<version>/`` beside the log, once."""
        into = self.root / f"backup-v{version}"
        into.mkdir(parents=True, exist_ok=True)
        copy_to = into / legacy.name
        if not copy_to.is_file():
            shutil.copy2(legacy, copy_to)
        return copy_to

    def _check_marker(self, marker: Path) -> None:
        try:
            raw = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        schema.check(raw, SCHEMA, marker)

    def migrate(self) -> dict[str, str]:
        """Each legacy plan becomes a ``create`` commit; the legacy file is left untouched.

        The legacy file is backed up first, a legacy file or a log from a newer version is
        refused (``schema.NewerSchema``), and the marker records the version that migrated.
        """
        legacy, marker = self._legacy(), self.root / "migrated.json"
        if marker.is_file():
            self._check_marker(marker)
        if not legacy.is_file():
            return {}
        if marker.is_file():
            self._warn_if_newer(legacy, marker)
            return {}
        self.root.mkdir(parents=True, exist_ok=True)
        with self._world_lock():
            if marker.is_file():
                return {}
            old = PlanStore.load(self.world_id, self.session_name)
            version = schema.writer_version()
            self._backup(legacy, version)
            done = {
                s.name: s.key
                for s in self.heads()
                if _read_commits(self._ops(s.key))[0].actor.kind == "migration"
            }
            keys: dict[str, str] = {}
            actor = Actor("migration", "", os.getpid())
            for plan in old.plans:
                name = (plan.name or "").strip() or "plan"
                if name in done:
                    keys[name] = done[name]
                    continue
                unique, n = name, 2
                while self._taken(unique):
                    unique, n = f"{name} ({n})", n + 1
                refused: list = []
                args = PlanArgs.from_dict(
                    {k: v for k, v in (plan.args or {}).items() if k in PLAN_ARGS}, lenient=refused
                )
                notes = plan.notes or ""
                if refused:
                    kept = "; ".join(f"{k}={v!r}" for k, v in refused)
                    notes = (notes + "\n" if notes else "") + f"migration could not keep: {kept}"
                state = PlanState(
                    key="",
                    rev=1,
                    name=unique,
                    notes=str(notes),
                    factory=str(plan.factory or ""),
                    created=str(plan.created or ""),
                    plan_id=str(plan.plan_id or ""),
                    provenance=copy.deepcopy(plan.provenance or {}),
                    siting=copy.deepcopy(plan.siting or {}),
                    args=args,
                )
                key = secrets.token_hex(4)
                while (self.root / key).exists():
                    key = secrets.token_hex(4)
                self._create_locked(key, state, actor, "", f"migrated from {legacy.name}")
                keys[unique] = key
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
        return keys

    def _warn_if_newer(self, legacy: Path, marker: Path) -> None:
        if self.world_id in _warned:
            return
        try:
            at = float(json.loads(marker.read_text(encoding="utf-8")).get("at") or 0)
            newer = legacy.stat().st_mtime > at
        except (OSError, ValueError, AttributeError):
            return
        if newer:
            _warned.add(self.world_id)
            _log.warning(
                "%s changed after it was migrated; those changes are not in the plan log",
                legacy,
            )

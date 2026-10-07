"""Plan-log ops: the canonical form, how one applies, inverts and clashes, and undo chains.

docs/plan_log.md, "The merge, in detail" and "Undo", has the rules these implement.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence

from .....core.jsontypes import JsonValue, is_object_dict
from ..plan_args import (
    KINDS,
    PLAN_SCALARS,
    SCALAR_CHECK,
    InvalidOp,
    Member,
    PlanArgs,
    canonical_power,
    checked_map_key,
    checked_map_value,
    checked_member,
    member_key,
)
from ..views import PlanOp
from .records import Commit, PlanState, json_copy
from .wording import op_text


def _member(op: PlanOp) -> Member:
    """An ``add`` or ``remove`` op's member: a name, or a clock in the clock fields."""
    value = op.get("member")
    return value if isinstance(value, str | int | float) else str(value)


def merge_key(op: PlanOp) -> str | None:
    kind, name = op.get("op"), op_text(op, "field")
    if kind == "set":
        return name
    if kind in ("put", "del"):
        return f"{name}[{canonical_power(name, op.get('item'))}]"
    if kind in ("add", "remove"):
        return f"{name}{{{member_key(name, _member(op))}}}"
    if kind == "site":
        return "site"
    if kind == "rename":
        return "name"
    if kind in ("create", "forget", "restore"):
        return "lifecycle"
    return None


_INVERSE_KIND = {"add": "remove", "remove": "add", "forget": "restore", "restore": "forget"}


def created_state(op: PlanOp) -> PlanOp:
    """The state a ``create`` op carries; ``KeyError`` when it carries none."""
    state = op.get("state")
    if not isinstance(state, dict):
        raise KeyError("state")
    return state


def _inverse_one(op: PlanOp) -> PlanOp | None:
    kind = op_text(op, "op")
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


def inverse(ops: Sequence[PlanOp]) -> list[PlanOp]:
    """The ops that take a state back over ``ops``, in reverse order; records are skipped."""
    return [inv for inv in (_inverse_one(op) for op in reversed(ops)) if inv is not None]


def _canonical_site(value: object) -> PlanOp:
    from ...siting import normalise_record

    if value is None:
        return {"op": "site", "value": None}
    try:
        return {"op": "site", "value": json_copy(normalise_record(value))}
    except ValueError as exc:
        raise InvalidOp(str(exc)) from None


def _canonical_record(name: object, value: object) -> PlanOp:
    if name == "plan_id":
        if not isinstance(value, str):
            raise InvalidOp("plan_id must be text")
        return {"op": "record", "field": "plan_id", "value": value}
    if name == "provenance":
        if not is_object_dict(value):
            raise InvalidOp("provenance must be an object")
        return {"op": "record", "field": "provenance", "value": json_copy(value)}
    raise InvalidOp(f"record does not apply to {name!r}")


def canonical_op(op: Mapping[str, object]) -> PlanOp:
    """A canonical copy of a writer's op, type-checked; ``was`` is the store's to fill."""
    if not isinstance(op, dict):
        raise InvalidOp(f"an op must be an object, not {op!r}")
    kind = op.get("op")
    name = op.get("field")
    if not isinstance(kind, str):
        raise InvalidOp(f"unknown op {kind!r}")
    if kind == "set":
        if not isinstance(name, str) or name not in SCALAR_CHECK:
            raise InvalidOp(f"set does not apply to {name!r}")
        return {"op": "set", "field": name, "value": SCALAR_CHECK[name](name, op.get("value"))}
    if kind in ("put", "del"):
        if not isinstance(name, str) or KINDS.get(name) != "map":
            raise InvalidOp(f"{kind} does not apply to {name!r}")
        item = checked_map_key(name, op.get("item"))
        out: PlanOp = {"op": kind, "field": name, "item": item}
        if kind == "put":
            out["value"] = checked_map_value(name, item, op.get("value"))
        return out
    if kind in ("add", "remove"):
        if not isinstance(name, str) or KINDS.get(name) != "set":
            raise InvalidOp(f"{kind} does not apply to {name!r}")
        return {"op": kind, "field": name, "member": checked_member(name, op.get("member"))}
    if kind == "site":
        return _canonical_site(op.get("value"))
    if kind == "rename":
        wanted = op.get("name")
        if not isinstance(wanted, str) or not wanted.strip():
            raise InvalidOp("a plan name cannot be blank")
        return {"op": "rename", "name": wanted.strip()}
    if kind in ("forget", "restore"):
        return {"op": kind}
    if kind == "record":
        return _canonical_record(name, op.get("value"))
    if kind == "create":
        raise InvalidOp("create happens only at rev 1")
    raise InvalidOp(f"unknown op {kind!r}")


def _current(state: PlanState, op: PlanOp) -> JsonValue:
    kind, name = op["op"], op_text(op, "field")
    if kind == "set":
        return getattr(state, name) if name in PLAN_SCALARS else getattr(state.args, name)
    if kind in ("put", "del"):
        return getattr(state.args, name).get(canonical_power(name, op["item"]))
    if kind in ("add", "remove"):
        wanted = member_key(name, _member(op))
        return any(member_key(name, m) == wanted for m in getattr(state.args, name))
    if kind == "site":
        return copy.deepcopy(state.siting) or None
    if kind == "rename":
        return state.name
    if kind in ("forget", "restore"):
        return state.forgotten
    if kind == "record":
        return getattr(state, name)
    return None


def is_noop(state: PlanState, op: PlanOp) -> bool:
    kind, now = op["op"], _current(state, op)
    if kind in ("set", "put", "record"):
        return now == op["value"]
    if kind == "del":
        return now is None
    if kind == "add":
        return bool(now)
    if kind == "remove":
        return not now
    if kind == "site":
        return now == (op["value"] or None)
    if kind == "rename":
        return now == op["name"]
    return False


def apply(state: PlanState, op: PlanOp) -> None:
    kind, name = op["op"], op_text(op, "field")
    if kind == "set":
        setattr(state if name in PLAN_SCALARS else state.args, name, copy.deepcopy(op["value"]))
    elif kind == "put":
        getattr(state.args, name)[canonical_power(name, op["item"])] = op["value"]
    elif kind == "del":
        getattr(state.args, name).pop(canonical_power(name, op["item"]), None)
    elif kind == "add":
        if not _current(state, op):
            getattr(state.args, name).append(canonical_power(name, op["member"]))
    elif kind == "remove":
        wanted = member_key(name, _member(op))
        kept = [m for m in getattr(state.args, name) if member_key(name, m) != wanted]
        setattr(state.args, name, kept)
    elif kind == "site":
        value = op.get("value")
        state.siting = copy.deepcopy(value) if isinstance(value, dict) else {}
    elif kind == "rename":
        state.name = op_text(op, "name")
    elif kind in ("forget", "restore"):
        state.forgotten = kind == "forget"
    elif kind == "record":
        setattr(state, name, copy.deepcopy(op["value"]))


def with_was(state: PlanState, op: PlanOp) -> PlanOp:
    out = dict(op)
    if op["op"] in ("set", "put", "del", "site"):
        out["was"] = _current(state, op)
    elif op["op"] == "rename":
        out["was"] = state.name
    return out


def check_lifecycle(work: PlanState, op: PlanOp) -> None:
    if op["op"] == "forget" and work.forgotten:
        raise InvalidOp("the plan is already forgotten")
    if op["op"] == "restore" and not work.forgotten:
        raise InvalidOp("the plan is not forgotten")


def _value(op: PlanOp) -> JsonValue:
    if op["op"] == "rename":
        return op["name"]
    return op.get("value")


def clash(mine: PlanOp, theirs: PlanOp) -> str | None:
    """``"conflict"``, ``"same"`` (mine is already applied) or None, per contract §4.1."""
    mk, tk = merge_key(mine), merge_key(theirs)
    if mk is None or tk is None:
        return None
    if theirs["op"] == "forget" or mine["op"] == "forget":
        return "conflict"
    if (
        mine["op"] == "add"
        and theirs["op"] == "add"
        and {op_text(mine, "field"), op_text(theirs, "field")} == {"required", "banned"}
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


def needs_world_lock(ops: Sequence[PlanOp]) -> bool:
    """Whether ``ops`` can clash on a name across plans, and so need the world lock."""
    return any(op.get("op") in ("rename", "restore") for op in ops)


def _target_args(new: Mapping[str, object]) -> tuple[PlanArgs, set[str]]:
    """``new`` as checked arguments, and which fields it names (``exclude_recipes`` as banned)."""
    raw = {k: v for k, v in dict(new).items() if k in KINDS or k == "exclude_recipes"}
    present = {("banned" if k == "exclude_recipes" else k) for k in raw}
    return PlanArgs.from_dict(raw), present


def _set_ops(name: str, before: list[Member], after: list[Member]) -> list[PlanOp]:
    """``add`` and ``remove`` ops that turn one set field into another."""
    had = {member_key(name, m) for m in before}
    has = {member_key(name, m) for m in after}
    added: list[PlanOp] = [
        {"op": "add", "field": name, "member": m} for m in after if member_key(name, m) not in had
    ]
    removed: list[PlanOp] = [
        {"op": "remove", "field": name, "member": m}
        for m in before
        if member_key(name, m) not in has
    ]
    return added + removed


def _map_ops(name: str, before: dict[str, Member], after: dict[str, Member]) -> list[PlanOp]:
    """``put`` and ``del`` ops that turn one map field into another."""
    put: list[PlanOp] = [
        {"op": "put", "field": name, "item": k, "value": v}
        for k, v in after.items()
        if before.get(k) != v
    ]
    deleted: list[PlanOp] = [
        {"op": "del", "field": name, "item": k} for k in before if k not in after
    ]
    return put + deleted


def diff_args(
    base: PlanArgs | Mapping[str, object], new: Mapping[str, object], partial: bool = False
) -> list[PlanOp]:
    """Ops that turn ``base`` into ``new``; with ``partial`` only fields ``new`` names count."""
    old = base if isinstance(base, PlanArgs) else PlanArgs.from_dict(base)
    target, present = _target_args(new)
    ops: list[PlanOp] = []
    for name, kind in KINDS.items():
        if partial and name not in present:
            continue
        before, after = getattr(old, name), getattr(target, name)
        if kind == "scalar":
            if before != after:
                ops.append({"op": "set", "field": name, "value": after})
        elif kind == "set":
            ops += _set_ops(name, before, after)
        else:
            ops += _map_ops(name, before, after)
    return ops


def replay(state: PlanState | None, commits: list[Commit], key: str) -> PlanState | None:
    for commit in commits:
        for op in commit.ops:
            if op.get("op") == "create":
                state = PlanState.from_dict(created_state(op), key=key, rev=commit.rev)
                state.key = key
            elif state is not None:
                apply(state, op)
        if state is not None:
            state.rev = commit.rev
    return state


def standing_undo_of(commits: list[Commit], rev: int) -> Commit | None:
    """The commit that undid ``rev`` and still stands, or None."""
    for later in reversed(commits):
        if later.undoes == rev and standing_undo_of(commits, later.rev) is None:
            return later
    return None


def _undo_chain(commits: list[Commit], rev: int) -> set[int]:
    """``rev``, the commits that undo it, the commits that undo those, and so on."""
    out = {rev}
    for commit in commits:
        if commit.undoes in out:
            out.add(commit.rev)
    return out


def revs_undo_ignores(commits: list[Commit], rev: int) -> set[int]:
    """Revs an undo of ``rev`` ignores: its own chain, and every later commit that stands
    undone together with its chain, since the two cancel out (docs/plan_log.md, Undo)."""
    out = _undo_chain(commits, rev)
    for commit in commits[rev:]:
        if commit.rev not in out and standing_undo_of(commits, commit.rev) is not None:
            out |= _undo_chain(commits, commit.rev)
    return out

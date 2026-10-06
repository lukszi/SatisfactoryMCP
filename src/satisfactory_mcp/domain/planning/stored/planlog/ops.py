"""Plan-log ops: the canonical form, how one applies, inverts and clashes, and undo chains.

docs/plan_log.md, "The merge, in detail" and "Undo", has the rules these implement.
"""

from __future__ import annotations

import copy

from ..plan_args import (
    _SCALAR_CHECK,
    KINDS,
    PLAN_SCALARS,
    InvalidOp,
    PlanArgs,
    _canonical_power,
    _checked_map_key,
    _map_value,
    _member,
    _member_key,
)
from .records import Commit, PlanState


def merge_key(op: dict) -> str | None:
    kind, name = op.get("op"), op.get("field", "")
    if kind == "set":
        return name
    if kind in ("put", "del"):
        return f"{name}[{_canonical_power(name, op.get('item'))}]"
    if kind in ("add", "remove"):
        return f"{name}{{{_member_key(name, op.get('member'))}}}"
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


def _canonical_site(value) -> dict:
    from ...siting import normalise_record

    if value is None:
        return {"op": "site", "value": None}
    try:
        return {"op": "site", "value": normalise_record(value)}
    except ValueError as exc:
        raise InvalidOp(str(exc)) from None


def _canonical_record(name, value) -> dict:
    if name not in ("plan_id", "provenance"):
        raise InvalidOp(f"record does not apply to {name!r}")
    if name == "plan_id" and not isinstance(value, str):
        raise InvalidOp("plan_id must be text")
    if name == "provenance" and not isinstance(value, dict):
        raise InvalidOp("provenance must be an object")
    return {"op": "record", "field": name, "value": copy.deepcopy(value)}


def _canonical_op(op: dict) -> dict:
    """A canonical copy of a writer's op, type-checked; ``was`` is the store's to fill."""
    if not isinstance(op, dict):
        raise InvalidOp(f"an op must be an object, not {op!r}")
    kind = op.get("op")
    name = op.get("field")
    if kind == "set":
        if name not in _SCALAR_CHECK:
            raise InvalidOp(f"set does not apply to {name!r}")
        return {"op": "set", "field": name, "value": _SCALAR_CHECK[name](name, op.get("value"))}
    if kind in ("put", "del"):
        if KINDS.get(name) != "map":
            raise InvalidOp(f"{kind} does not apply to {name!r}")
        out = {"op": kind, "field": name, "item": _checked_map_key(name, op.get("item"))}
        if kind == "put":
            out["value"] = _map_value(name, out["item"], op.get("value"))
        return out
    if kind in ("add", "remove"):
        if KINDS.get(name) != "set":
            raise InvalidOp(f"{kind} does not apply to {name!r}")
        return {"op": kind, "field": name, "member": _member(name, op.get("member"))}
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


def _current(state: PlanState, op: dict):
    kind, name = op["op"], op.get("field")
    if kind == "set":
        return getattr(state, name) if name in PLAN_SCALARS else getattr(state.args, name)
    if kind in ("put", "del"):
        return getattr(state.args, name).get(_canonical_power(name, op["item"]))
    if kind in ("add", "remove"):
        wanted = _member_key(name, op["member"])
        return any(_member_key(name, m) == wanted for m in getattr(state.args, name))
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
        getattr(state.args, name)[_canonical_power(name, op["item"])] = op["value"]
    elif kind == "del":
        getattr(state.args, name).pop(_canonical_power(name, op["item"]), None)
    elif kind == "add":
        if not _current(state, op):
            getattr(state.args, name).append(_canonical_power(name, op["member"]))
    elif kind == "remove":
        wanted = _member_key(name, op["member"])
        kept = [m for m in getattr(state.args, name) if _member_key(name, m) != wanted]
        setattr(state.args, name, kept)
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


def _check_lifecycle(work: PlanState, op: dict) -> None:
    if op["op"] == "forget" and work.forgotten:
        raise InvalidOp("the plan is already forgotten")
    if op["op"] == "restore" and not work.forgotten:
        raise InvalidOp("the plan is not forgotten")


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


def _needs_world_lock(ops: list[dict]) -> bool:
    """Whether ``ops`` can clash on a name across plans, and so need the world lock."""
    return any(op.get("op") in ("rename", "restore") for op in ops)


def _target_args(new: dict) -> tuple[PlanArgs, set[str]]:
    """``new`` as checked arguments, and which fields it names (``exclude_recipes`` as banned)."""
    raw = {k: v for k, v in dict(new).items() if k in KINDS or k == "exclude_recipes"}
    present = {("banned" if k == "exclude_recipes" else k) for k in raw}
    return PlanArgs.from_dict(raw), present


def diff_args(base: PlanArgs | dict, new: dict, partial: bool = False) -> list[dict]:
    """Ops that turn ``base`` into ``new``; with ``partial`` only fields ``new`` names count."""
    old = base if isinstance(base, PlanArgs) else PlanArgs.from_dict(base)
    target, present = _target_args(new)
    ops: list[dict] = []
    for name, kind in KINDS.items():
        if partial and name not in present:
            continue
        before, after = getattr(old, name), getattr(target, name)
        if kind == "scalar":
            if before != after:
                ops.append({"op": "set", "field": name, "value": after})
        elif kind == "set":
            had = {_member_key(name, m) for m in before}
            has = {_member_key(name, m) for m in after}
            ops += [
                {"op": "add", "field": name, "member": m}
                for m in after
                if _member_key(name, m) not in had
            ]
            ops += [
                {"op": "remove", "field": name, "member": m}
                for m in before
                if _member_key(name, m) not in has
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


def _standing_undo_of(commits: list[Commit], rev: int) -> Commit | None:
    """The commit that undid ``rev`` and still stands, or None."""
    for later in reversed(commits):
        if later.undoes == rev and _standing_undo_of(commits, later.rev) is None:
            return later
    return None


def _undo_chain(commits: list[Commit], rev: int) -> set[int]:
    """``rev``, the commits that undo it, the commits that undo those, and so on."""
    out = {rev}
    for commit in commits:
        if commit.undoes in out:
            out.add(commit.rev)
    return out


def _revs_undo_ignores(commits: list[Commit], rev: int) -> set[int]:
    """Revs an undo of ``rev`` ignores: its own chain, and every later commit that stands
    undone together with its chain, since the two cancel out (docs/plan_log.md, Undo)."""
    out = _undo_chain(commits, rev)
    for commit in commits[rev:]:
        if commit.rev not in out and _standing_undo_of(commits, commit.rev) is not None:
            out |= _undo_chain(commits, commit.rev)
    return out

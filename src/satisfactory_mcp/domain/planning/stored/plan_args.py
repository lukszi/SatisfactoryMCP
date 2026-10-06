"""A stored plan's arguments: the fields, the check each one passes, and the call defaults.

The leaf of ``stored``: ``store`` and the plan log read it, and it reads neither.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field, fields

__all__ = [
    "FACTORY_SENTINELS",
    "KINDS",
    "KWARG_NAME",
    "OBJECTIVES",
    "PAYBACK_MAX_H",
    "PLAN_ARGS",
    "PLAN_DEFAULTS",
    "PLAN_SCALARS",
    "POWER",
    "ROW_CHOICES",
    "InvalidOp",
    "PlanArgs",
    "PlanLogError",
    "inherits_default",
    "is_power",
    "legacy_hours",
]

OBJECTIVES = ("max_mw", "max_item", "min_raw", "min_machines", "min_power")
POWER = "MW"
_POWER_SPELLINGS = frozenset({"mw", "power", "__mw__"})
_POWER_FIELDS = frozenset({"exports", "export_minimums"})
FLOAT_SETS = frozenset({"clocks", "extractor_clocks"})
#: Settable fields of the plan itself rather than of its arguments.
PLAN_SCALARS = ("notes", "factory", "headroom_mw")
HEADROOM_MAX_MW = 1_000_000.0
PAYBACK_MAX_H = 100.0
PRICE_MAX = 100_000.0
#: A stored ``power_priority`` step as the horizon where a Refinery's best clock equals the
#: old cap (docs/planner-payback-horizon_contract.md §8); step 0 inherits.
LEGACY_PRIORITY_HOURS = (None, 4.0, 7.0, 11.0, 17.0)
#: A row's own overclock-last choice, keyed by recipe id: overclock the last machine, or one
#: more underclocked machine. Absent follows the plan's ``overclock_last``.
ROW_CHOICES = {"last": "overclock last", "spread": "one more underclocked machine"}
KWARG_NAME = {"banned": "exclude_recipes"}
#: ``factory`` values that are not a factory name: count the whole world, or nothing yet.
FACTORY_SENTINELS = ("/world", "/none")


class PlanLogError(Exception):
    """Anything the plan log refuses."""


class InvalidOp(PlanLogError, ValueError):
    pass


def is_power(name) -> bool:
    """Whether ``name`` means grid power: ``MW``, ``mw``, ``power`` or the solver's ``__MW__``."""
    return isinstance(name, str) and name.strip().casefold() in _POWER_SPELLINGS


def _canonical_power(fieldname: str, name):
    """``POWER`` for any spelling of power in the fields that take it, else ``name``."""
    return POWER if fieldname in _POWER_FIELDS and is_power(name) else name


def _number(name: str, value, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidOp(f"{name} must be a number, not {json.dumps(value, default=str)}")
    if not math.isfinite(value):
        raise InvalidOp(f"{name} must be finite, not {value!r}")
    return float(value)


def _count(name: str, value, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidOp(f"{name} must be a whole number, not {json.dumps(value, default=str)}")
    if value < 0:
        raise InvalidOp(f"{name} must be 0 or more, not {value}")
    return value


def _text(name: str, value, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise InvalidOp(f"{name} must be text, not {value!r}")
    return value


def _factory(name: str, value) -> str:
    text = _text(name, value)
    if text.startswith("/") and text not in FACTORY_SENTINELS:
        raise InvalidOp(f"{name} is a factory name, {' or '.join(FACTORY_SENTINELS)}, not {text!r}")
    return text


def _flag(name: str, value) -> bool:
    if not isinstance(value, bool):
        raise InvalidOp(f"{name} must be true or false, not {value!r}")
    return value


def _headroom(name: str, value) -> float | None:
    number = _number(name, value, optional=True)
    if number is not None and not 0 < number <= HEADROOM_MAX_MW:
        raise InvalidOp(
            f"{name} must be above 0 and at most {HEADROOM_MAX_MW:,.0f} MW, not {value!r}"
        )
    return number


def inherits_default(value) -> bool:
    """Whether ``value`` asks for the shared default: None, or the word ``default``."""
    return value is None or value == "default"


def _hours(name: str, value) -> float | None:
    if inherits_default(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PAYBACK_MAX_H:
        raise InvalidOp(f"{name} must be 0 to {PAYBACK_MAX_H:g} hours or default, not {value!r}")
    return number


def _price(name: str, value) -> float | None:
    if inherits_default(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PRICE_MAX:
        raise InvalidOp(
            f"{name} must be 0 to {PRICE_MAX:,.0f} points per MWh or default, not {value!r}"
        )
    return number


def _switch(name: str, value) -> bool | None:
    return None if inherits_default(value) else _flag(name, value)


def _row_choice(name: str, value) -> str:
    if value not in ROW_CHOICES:
        raise InvalidOp(f"{name} must be {' or '.join(map(repr, ROW_CHOICES))}, not {value!r}")
    return value


#: What a map field's values are; a rate per minute unless named here.
_MAP_VALUE: dict[str, Callable] = {"row_overclock": _row_choice}


def _map_value(fieldname: str, item: str, value):
    return _MAP_VALUE.get(fieldname, _number)(f"{fieldname}[{item}]", value)


def legacy_hours(step) -> float | None:
    """A stored ``power_priority`` step read as its payback horizon."""
    if isinstance(step, bool) or not isinstance(step, int | float) or step != int(step):
        return None
    return LEGACY_PRIORITY_HOURS[int(step)] if 0 <= step < len(LEGACY_PRIORITY_HOURS) else None


def _objective(name: str, value) -> str:
    if value not in OBJECTIVES:
        raise InvalidOp(f"objective must be one of {', '.join(OBJECTIVES)}, not {value!r}")
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
        raise InvalidOp(f"{fieldname} member cannot be blank")
    return _canonical_power(fieldname, text)


def _member_key(fieldname: str, member) -> str:
    """What two members of a set field are compared on: floats by value, names by spelling."""
    return f"{member:g}" if fieldname in FLOAT_SETS else _canonical_power(fieldname, member)


def _checked_map_key(fieldname: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOp(f"{fieldname} needs an item name, not {value!r}")
    return _canonical_power(fieldname, value)


def _check_field(name: str, value):
    kind = KINDS[name]
    if kind == "scalar":
        return _SCALAR_CHECK[name](name, value)
    if kind == "set":
        if not isinstance(value, list | tuple):
            raise InvalidOp(f"{name} must be a list, not {value!r}")
        out, seen = [], set()
        for raw in value:
            member = _member(name, raw)
            if _member_key(name, member) not in seen:
                seen.add(_member_key(name, member))
                out.append(member)
        return out
    if not isinstance(value, dict):
        raise InvalidOp(f"{name} must be a mapping, not {value!r}")
    return {_checked_map_key(name, k): _map_value(name, k, v) for k, v in value.items()}


#: Each field's kind, as ``PlanArgs`` metadata: how it is checked, edited and merged.
_SCALAR = {"kind": "scalar"}
_SET = {"kind": "set"}
_MAP = {"kind": "map"}


@dataclass
class PlanArgs:
    objective: str = field(default="max_mw", metadata=_SCALAR)
    target_item: str | None = field(default=None, metadata=_SCALAR)
    sources: list = field(default_factory=list, metadata=_SET)
    exports: list = field(default_factory=list, metadata=_SET)
    export_minimums: dict = field(default_factory=dict, metadata=_MAP)
    only_free_nodes: bool = field(default=False, metadata=_SCALAR)
    allow_sinks: bool = field(default=True, metadata=_SCALAR)
    clocks: list = field(default_factory=list, metadata=_SET)
    extractor_clocks: list = field(default_factory=list, metadata=_SET)
    machine_cost_mw: float = field(default=5.0, metadata=_SCALAR)
    banned: list = field(default_factory=list, metadata=_SET)
    required: list = field(default_factory=list, metadata=_SET)
    only_recipes: list = field(default_factory=list, metadata=_SET)
    water_extractors: int | None = field(default=None, metadata=_SCALAR)
    sloops: int = field(default=0, metadata=_SCALAR)
    belt_ipm: float | None = field(default=None, metadata=_SCALAR)
    pipe_m3min: float | None = field(default=None, metadata=_SCALAR)
    recycle_once: list = field(default_factory=list, metadata=_SET)
    supplied: dict = field(default_factory=dict, metadata=_MAP)
    logistics_items: list = field(default_factory=list, metadata=_SET)
    payback_hours: float | None = field(default=None, metadata=_SCALAR)
    overclock_last: bool | None = field(default=None, metadata=_SCALAR)
    power_price: float | None = field(default=None, metadata=_SCALAR)
    row_overclock: dict = field(default_factory=dict, metadata=_MAP)

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
            if name in _NOT_SOLVE_ARGS or value == getattr(blank, name):
                continue
            out[KWARG_NAME.get(name, name)] = copy.deepcopy(value)
        return out


KINDS: dict[str, str] = {f.name: f.metadata["kind"] for f in fields(PlanArgs)}

#: Stored with the plan for ``plan_factory``'s report, but not an argument of the solve.
_NOT_SOLVE_ARGS = frozenset({"logistics_items"})

#: Argument names a plan captures: everything build_scenario takes that changes the
#: answer, and so not `limit` (presentation) or `save`/`world` (which save was read).
PLAN_ARGS = tuple(KWARG_NAME.get(name, name) for name in KINDS if name not in _NOT_SOLVE_ARGS)

#: Each argument's declared default; a supplied value overrides a recalled plan only when it
#: differs, because MCP fills defaults in first (docs/mcp-surface.md, "The defaults trap").
PLAN_DEFAULTS: dict = {
    KWARG_NAME.get(f.name, f.name): f.default if f.metadata["kind"] == "scalar" else None
    for f in fields(PlanArgs)
    if f.name not in _NOT_SOLVE_ARGS
}

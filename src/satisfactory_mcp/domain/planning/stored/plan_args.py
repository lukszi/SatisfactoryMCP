"""A stored plan's arguments: the fields, the check each one passes, and the call defaults.

The leaf of ``stored``: ``store`` and the plan log read it, and it reads neither.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, TypeAlias, TypeGuard, TypeVar, cast

from .views import PlanArgsBody

if TYPE_CHECKING:
    from ..solver.scenario import PlanKwargs

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
    "SCALAR_CHECK",
    "InvalidOp",
    "PlanArgs",
    "PlanLogError",
    "canonical_power",
    "checked_headroom",
    "checked_map_key",
    "checked_map_value",
    "checked_member",
    "checked_text",
    "inherits_default",
    "is_power",
    "legacy_hours",
    "member_key",
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

_Name = TypeVar("_Name")
#: What a scalar field holds once checked.
Scalar: TypeAlias = str | float | int | bool | None
#: A member of a set field: an item or recipe name, or a clock.
Member: TypeAlias = str | float
#: The check a field's raw value passes: ``(field name, value)`` to the value it stores.
Check: TypeAlias = Callable[[str, object], Scalar]


class PlanLogError(Exception):
    """Anything the plan log refuses."""


class InvalidOp(PlanLogError, ValueError):
    pass


def is_power(name: object) -> bool:
    """Whether ``name`` means grid power: ``MW``, ``mw``, ``power`` or the solver's ``__MW__``."""
    return isinstance(name, str) and name.strip().casefold() in _POWER_SPELLINGS


def canonical_power(fieldname: str, name: _Name) -> _Name | str:
    """``POWER`` for any spelling of power in the fields that take it, else ``name``."""
    return POWER if fieldname in _POWER_FIELDS and is_power(name) else name


def _number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidOp(f"{name} must be a number, not {json.dumps(value, default=str)}")
    if not math.isfinite(value):
        raise InvalidOp(f"{name} must be finite, not {value!r}")
    return float(value)


def _optional_number(name: str, value: object) -> float | None:
    return None if value is None else _number(name, value)


def _count(name: str, value: object) -> int:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidOp(f"{name} must be a whole number, not {json.dumps(value, default=str)}")
    if value < 0:
        raise InvalidOp(f"{name} must be 0 or more, not {value}")
    return value


def _optional_count(name: str, value: object) -> int | None:
    return None if value is None else _count(name, value)


def checked_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise InvalidOp(f"{name} must be text, not {value!r}")
    return value


def _optional_text(name: str, value: object) -> str | None:
    return None if value is None else checked_text(name, value)


def _factory(name: str, value: object) -> str:
    text = checked_text(name, value)
    if text.startswith("/") and text not in FACTORY_SENTINELS:
        raise InvalidOp(f"{name} is a factory name, {' or '.join(FACTORY_SENTINELS)}, not {text!r}")
    return text


def _flag(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise InvalidOp(f"{name} must be true or false, not {value!r}")
    return value


def checked_headroom(name: str, value: object) -> float | None:
    number = _optional_number(name, value)
    if number is not None and not 0 < number <= HEADROOM_MAX_MW:
        raise InvalidOp(
            f"{name} must be above 0 and at most {HEADROOM_MAX_MW:,.0f} MW, not {value!r}"
        )
    return number


def inherits_default(value: object) -> bool:
    """Whether ``value`` asks for the shared default: None, or the word ``default``."""
    return value is None or value == "default"


def _hours(name: str, value: object) -> float | None:
    if inherits_default(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PAYBACK_MAX_H:
        raise InvalidOp(f"{name} must be 0 to {PAYBACK_MAX_H:g} hours or default, not {value!r}")
    return number


def _price(name: str, value: object) -> float | None:
    if inherits_default(value):
        return None
    number = _number(name, value)
    if not 0 <= number <= PRICE_MAX:
        raise InvalidOp(
            f"{name} must be 0 to {PRICE_MAX:,.0f} points per MWh or default, not {value!r}"
        )
    return number


def _switch(name: str, value: object) -> bool | None:
    return None if inherits_default(value) else _flag(name, value)


def _row_choice(name: str, value: object) -> str:
    if value not in ROW_CHOICES:
        raise InvalidOp(f"{name} must be {' or '.join(map(repr, ROW_CHOICES))}, not {value!r}")
    return str(value)


#: What a map field's values are; a rate per minute unless named here.
_MAP_VALUE: dict[str, Callable[[str, object], Member]] = {"row_overclock": _row_choice}


def checked_map_value(fieldname: str, item: str, value: object) -> Member:
    return _MAP_VALUE.get(fieldname, _number)(f"{fieldname}[{item}]", value)


def legacy_hours(step: object) -> float | None:
    """A stored ``power_priority`` step read as its payback horizon."""
    if isinstance(step, bool) or not isinstance(step, int | float) or step != int(step):
        return None
    return LEGACY_PRIORITY_HOURS[int(step)] if 0 <= step < len(LEGACY_PRIORITY_HOURS) else None


def _objective(name: str, value: object) -> str:
    if value not in OBJECTIVES:
        raise InvalidOp(f"objective must be one of {', '.join(OBJECTIVES)}, not {value!r}")
    return str(value)


SCALAR_CHECK: dict[str, Check] = {
    "objective": _objective,
    "target_item": _optional_text,
    "only_free_nodes": _flag,
    "allow_sinks": _flag,
    "machine_cost_mw": _number,
    "water_extractors": _optional_count,
    "sloops": _count,
    "belt_ipm": _optional_number,
    "pipe_m3min": _optional_number,
    "notes": checked_text,
    "factory": _factory,
    "headroom_mw": checked_headroom,
    "payback_hours": _hours,
    "overclock_last": _switch,
    "power_price": _price,
}


def checked_member(fieldname: str, value: object) -> Member:
    if fieldname in FLOAT_SETS:
        return _number(f"{fieldname} member", value)
    text = checked_text(f"{fieldname} member", value)
    if not text:
        raise InvalidOp(f"{fieldname} member cannot be blank")
    return canonical_power(fieldname, text)


def member_key(fieldname: str, member: Member) -> str:
    """What two members of a set field are compared on: floats by value, names by spelling."""
    if fieldname in FLOAT_SETS:
        return f"{member:g}"
    return str(canonical_power(fieldname, member))


def checked_map_key(fieldname: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOp(f"{fieldname} needs an item name, not {value!r}")
    return canonical_power(fieldname, value)


def _is_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list | tuple)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, dict)


def _checked_set(name: str, value: object) -> list[Member]:
    if not _is_list(value):
        raise InvalidOp(f"{name} must be a list, not {value!r}")
    out: list[Member] = []
    seen: set[str] = set()
    for raw in value:
        member = checked_member(name, raw)
        if member_key(name, member) not in seen:
            seen.add(member_key(name, member))
            out.append(member)
    return out


def _checked_map(name: str, value: object) -> dict[str, Member]:
    if not _is_mapping(value):
        raise InvalidOp(f"{name} must be a mapping, not {value!r}")
    return {checked_map_key(name, k): checked_map_value(name, str(k), v) for k, v in value.items()}


def _check_field(name: str, value: object) -> Scalar | list[Member] | dict[str, Member]:
    kind = KINDS[name]
    if kind == "scalar":
        return SCALAR_CHECK[name](name, value)
    if kind == "set":
        return _checked_set(name, value)
    return _checked_map(name, value)


#: Each field's kind, as ``PlanArgs`` metadata: how it is checked, edited and merged.
_SCALAR = {"kind": "scalar"}
_SET = {"kind": "set"}
_MAP = {"kind": "map"}


@dataclass
class PlanArgs:
    objective: str = field(default="max_mw", metadata=_SCALAR)
    target_item: str | None = field(default=None, metadata=_SCALAR)
    sources: list[str] = field(default_factory=list[str], metadata=_SET)
    exports: list[str] = field(default_factory=list[str], metadata=_SET)
    export_minimums: dict[str, float] = field(default_factory=dict[str, float], metadata=_MAP)
    only_free_nodes: bool = field(default=False, metadata=_SCALAR)
    allow_sinks: bool = field(default=True, metadata=_SCALAR)
    clocks: list[float] = field(default_factory=list[float], metadata=_SET)
    extractor_clocks: list[float] = field(default_factory=list[float], metadata=_SET)
    machine_cost_mw: float = field(default=5.0, metadata=_SCALAR)
    banned: list[str] = field(default_factory=list[str], metadata=_SET)
    required: list[str] = field(default_factory=list[str], metadata=_SET)
    only_recipes: list[str] = field(default_factory=list[str], metadata=_SET)
    water_extractors: int | None = field(default=None, metadata=_SCALAR)
    sloops: int = field(default=0, metadata=_SCALAR)
    belt_ipm: float | None = field(default=None, metadata=_SCALAR)
    pipe_m3min: float | None = field(default=None, metadata=_SCALAR)
    recycle_once: list[str] = field(default_factory=list[str], metadata=_SET)
    supplied: dict[str, float] = field(default_factory=dict[str, float], metadata=_MAP)
    logistics_items: list[str] = field(default_factory=list[str], metadata=_SET)
    payback_hours: float | None = field(default=None, metadata=_SCALAR)
    overclock_last: bool | None = field(default=None, metadata=_SCALAR)
    power_price: float | None = field(default=None, metadata=_SCALAR)
    row_overclock: dict[str, str] = field(default_factory=dict[str, str], metadata=_MAP)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, object] | None, lenient: list[tuple[str, object]] | None = None
    ) -> PlanArgs:
        """Absent, None, [] and {} mean the default. ``lenient`` collects refused fields
        as ``(name, value)`` instead of raising, for migration."""
        given: dict[str, object] = dict(raw) if raw else {}
        step = given.pop("power_priority", None)
        if given.get("payback_hours") is None and step is not None:
            given["payback_hours"] = legacy_hours(step)
        if "exclude_recipes" in given:
            spelled = given.pop("exclude_recipes")
            if given.get("banned") in (None, [], {}):
                given["banned"] = spelled
        out = cls()
        for name in KINDS:
            value = given.get(name)
            if value is None or _empty_container(value):
                continue
            try:
                setattr(out, name, _check_field(name, value))
            except InvalidOp:
                if lenient is None:
                    raise
                lenient.append((name, value))
        return out

    def to_dict(self) -> PlanArgsBody:
        return {
            "objective": self.objective,
            "target_item": self.target_item,
            "sources": list(self.sources),
            "exports": list(self.exports),
            "export_minimums": dict(self.export_minimums),
            "only_free_nodes": self.only_free_nodes,
            "allow_sinks": self.allow_sinks,
            "clocks": list(self.clocks),
            "extractor_clocks": list(self.extractor_clocks),
            "machine_cost_mw": self.machine_cost_mw,
            "banned": list(self.banned),
            "required": list(self.required),
            "only_recipes": list(self.only_recipes),
            "water_extractors": self.water_extractors,
            "sloops": self.sloops,
            "belt_ipm": self.belt_ipm,
            "pipe_m3min": self.pipe_m3min,
            "recycle_once": list(self.recycle_once),
            "supplied": dict(self.supplied),
            "logistics_items": list(self.logistics_items),
            "payback_hours": self.payback_hours,
            "overclock_last": self.overclock_last,
            "power_price": self.power_price,
            "row_overclock": dict(self.row_overclock),
        }

    def kwargs(self) -> PlanKwargs:
        """The non-default fields as ``build_scenario``'s keyword arguments."""
        blank = PlanArgs()
        out: dict[str, object] = {}
        for name in KINDS:
            value: object = getattr(self, name)
            if name in _NOT_SOLVE_ARGS or value == getattr(blank, name):
                continue
            out[KWARG_NAME.get(name, name)] = copy.deepcopy(value)
        # Every key is a field ``_check_field`` held to its kind, renamed as the solve spells it.
        return cast("PlanKwargs", out)


KINDS: dict[str, str] = {f.name: f.metadata["kind"] for f in fields(PlanArgs)}


def _empty_container(value: object) -> bool:
    """``[]``, ``()`` or ``{}``: what a field holds when it says nothing."""
    return isinstance(value, list | tuple | dict) and not value


#: Stored with the plan for ``plan_factory``'s report, but not an argument of the solve.
_NOT_SOLVE_ARGS = frozenset({"logistics_items"})

#: Argument names a plan captures: everything build_scenario takes that changes the
#: answer, and so not `limit` (presentation) or `save`/`world` (which save was read).
PLAN_ARGS = tuple(KWARG_NAME.get(name, name) for name in KINDS if name not in _NOT_SOLVE_ARGS)

#: Each argument's declared default; a supplied value overrides a recalled plan only when it
#: differs, because MCP fills defaults in first (docs/mcp-surface.md, "The defaults trap").
PLAN_DEFAULTS: dict[str, object] = {
    KWARG_NAME.get(f.name, f.name): f.default if f.metadata["kind"] == "scalar" else None
    for f in fields(PlanArgs)
    if f.name not in _NOT_SOLVE_ARGS
}

"""Plan-log ops and commits in words: the history line, the conflict line, the merge note."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .....core.text import hours
from ..plan_args import ROW_CHOICES

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from .records import Commit

MINUS = "−"
ARROW = "→"

_recipe_name_source: list[Callable[[], dict[str, str]]] = []
_POWER_WORDS = ("payback_hours", "overclock_last", "power_price")


def _fmt(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def use_recipe_names(source: Callable[[], dict[str, str]] | None) -> None:
    _recipe_name_source[:] = [source] if source is not None else []


def _member_name(field_name: str, member) -> str:
    if not _recipe_name_source or field_name not in ("banned", "required", "row_overclock"):
        return _fmt(member)
    try:
        return _recipe_name_source[0]().get(member, _fmt(member))
    except Exception:
        return _fmt(member)


def factory_words(value) -> str:
    """A stored ``factory`` value in words."""
    text = str(value or "")
    return {"": "found automatically", "/world": "whole world", "/none": "nothing yet"}.get(
        text, text
    )


def _power_words(name: str, value) -> str:
    if name == "payback_hours":
        return "payback: shared default" if value is None else "payback " + hours(value)
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
    if kind in ("put", "del") and name == "row_overclock":
        row = _member_name(name, op.get("item", ""))
        if kind == "del":
            return f"{row}: follows the plan's overclock setting"
        return f"{row}: {ROW_CHOICES.get(op.get('value'), _fmt(op.get('value')))}"
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
        return "site " + _site_words(op)
    if kind == "rename":
        return f'renamed "{op.get("was", "")}"{ARROW}"{op.get("name", "")}"'
    return {"create": "created", "forget": "forgotten", "restore": "restored"}.get(kind, "")


def _site_words(op: dict) -> str:
    """``set at 1,476, -2,098 (Rocky Desert)``, ``moved 1,503 m west, turned 30°``, ``cleared``."""
    from ...siting import record as siting_record

    value, was = op.get("value"), op.get("was")
    if not value:
        return "cleared"
    sit = siting_record.Siting.from_record(value)
    if not was:
        if sit is None:
            return "set"
        return f"set at {sit.x_m:,.0f}, {sit.y_m:,.0f}" + _region(sit.x_m, sit.y_m)
    return siting_record.move_words(was, value) or "moved"


def _region(x_m: float, y_m: float) -> str:
    try:
        from ....spatial.regions import load_regions

        name = load_regions().label_for(x_m * 100.0, y_m * 100.0).name
    except Exception:
        return ""
    return f" ({name})" if name else ""


def _ops_text(ops: list[dict]) -> str:
    return " · ".join(t for t in (describe_op(o) for o in ops) if t)


def describe_commit(commit: Commit) -> str:
    ops = _ops_text(commit.ops)
    head = f"v{commit.rev} {commit.actor.display()}"
    if commit.undoes is not None:
        return f"{head}: undo v{commit.undoes}" + (f" ({ops})" if ops else "")
    return f"{head}: {ops or commit.note or 'recorded'}"


def conflict_subject(op: dict) -> str:
    """What a conflict line says both sides touched: ``rate Plastic``, ``site``, ``name``."""
    kind, name = op["op"], op.get("field", "")
    if kind in ("put", "del") and name == "row_overclock":
        return f"overclock on {_member_name(name, op['item'])}"
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


def value_word(op: dict) -> str:
    if op.get("field") == "row_overclock":
        return ROW_CHOICES.get(op.get("value"), _fmt(op.get("value")))
    if op.get("field") in _POWER_WORDS:
        return _power_words(op["field"], op.get("value")).split(": ")[-1]
    if op.get("field") == "headroom_mw":
        value = op.get("value")
        return "save default" if value is None else f"{_fmt(float(value))} MW"
    return _fmt(op["value"])


def action_words(op: dict) -> str:
    """What one side of a conflict did, as a verb phrase: ``set 2,000 MW``, ``renamed it "x"``."""
    kind = op["op"]
    if kind in ("set", "put"):
        return f"set {value_word(op)}"
    if kind == "del" and op.get("field") == "row_overclock":
        return "put it back on the plan's setting"
    if kind == "del":
        return "removed it"
    if kind == "add":
        return f"added {op['field']} {_fmt(op['member'])}"
    if kind == "remove":
        return f"removed {op['field']} {_fmt(op['member'])}"
    if kind == "site":
        words = _site_words(op)
        verb, _, rest = words.partition(" ")
        return f"{verb} the site" + (f" {rest}" if rest else "")
    if kind == "rename":
        return f'renamed it "{op["name"]}"'
    return {"forget": "forgot the plan", "restore": "restored the plan"}.get(kind, kind)


def other_text(commit: Commit) -> str:
    return f"v{commit.rev} {_ops_text(commit.ops) or 'recorded'} ({commit.actor.display()})"

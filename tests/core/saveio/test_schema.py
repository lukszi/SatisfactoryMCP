"""``core/saveio/schema.py`` against the committed projection: every key, every value's type.

The schema is what the typed readers trust in place of guards, so a field the writer emits
and the schema does not declare, or one declared with the wrong type, has to fail here
rather than in a reader. The check is structural and strict: an undeclared key fails, and so
does a declared required key the projection lacks.
"""

from __future__ import annotations

import types
import typing

import pytest
import typing_extensions

from satisfactory_mcp.core.saveio import schema

_SCALARS = {str: (str,), int: (int,), float: (int, float), bool: (bool,), type(None): (type(None),)}


def _problems(value: object, hint: object, path: str) -> list[str]:
    """Every place ``value`` does not fit ``hint``, as ``path: what`` lines."""
    if typing_extensions.is_typeddict(hint):
        return _typeddict_problems(value, hint, path)
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        found = [_problems(value, arm, path) for arm in args]
        return [] if any(not p for p in found) else [f"{path}: {value!r:.60} fits no arm of {hint}"]
    if origin is typing.Literal:
        return [] if value in args else [f"{path}: {value!r} not in {args}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: {type(value).__name__}, not a list"]
        return [p for i, item in enumerate(value) for p in _problems(item, args[0], f"{path}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: {type(value).__name__}, not a dict"]
        return [p for k, v in value.items() for p in _problems(v, args[1], f"{path}[{k!r}]")]
    allowed = _SCALARS[hint]
    if isinstance(value, bool) and bool not in allowed:
        return [f"{path}: a bool where {hint} is declared"]
    return [] if isinstance(value, allowed) else [f"{path}: {type(value).__name__}, not {hint}"]


_OPTIONAL = {typing.NotRequired, typing_extensions.NotRequired}
_REQUIRED = {typing.Required, typing_extensions.Required}


def _required_keys(cls: type) -> set[str]:
    """Read off the hints: ``__required_keys__`` is wrong under postponed annotations."""
    keys = set()
    for key, hint in typing_extensions.get_type_hints(cls, include_extras=True).items():
        marker = typing.get_origin(hint)
        if marker in _REQUIRED or (marker not in _OPTIONAL and cls.__total__):
            keys.add(key)
    return keys


def _typeddict_problems(value: object, cls: type, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: {type(value).__name__}, not {cls.__name__}"]
    hints = typing_extensions.get_type_hints(cls)
    out = [f"{path}: {cls.__name__} lacks {key!r}" for key in _required_keys(cls) - set(value)]
    out += [f"{path}: {key!r} is not declared on {cls.__name__}" for key in set(value) - set(hints)]
    for key in set(value) & set(hints):
        out += _problems(value[key], hints[key], f"{path}.{key}")
    return out


def test_the_committed_projection_fits_the_schema(projection):
    problems = _problems(projection, schema.Projection, "projection")
    assert not problems, "\n".join(problems[:20])


def test_the_schema_declares_the_writers_keys_in_its_order(projection):
    assert list(typing_extensions.get_type_hints(schema.Projection)) == list(projection)


_MACHINE = {"cls": "Build_X_C", "instance": "i", "pos": None, "yaw": None, "recipe": None}


@pytest.mark.parametrize(
    ("value", "cls"),
    [
        ({**_MACHINE, "recipe": 3}, schema.MachineRecord),
        ({k: v for k, v in _MACHINE.items() if k != "yaw"}, schema.MachineRecord),
        ({**_MACHINE, "undeclared": 1}, schema.MachineRecord),
        ({**_MACHINE, "paused": 1}, schema.MachineRecord),
        ({"in": {"items": {}, "slots": "2"}}, schema.Buffers),
    ],
)
def test_the_check_refuses_what_the_schema_does_not_say(value, cls):
    assert not _problems(_MACHINE, schema.MachineRecord, "machine")
    assert _problems(value, cls, "row")

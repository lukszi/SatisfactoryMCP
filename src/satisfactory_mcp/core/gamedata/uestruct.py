"""Parser for the UE struct strings used throughout Docs/en-US.json.

Grammar, exhaustive over the 14,666 struct-shaped fields in the dump::

    value := list | quoted | bare
    list  := '(' entry (',' entry)* ')'
    entry := (KEY '=')? value

Keyed lists become dicts, positional lists become lists, leaves stay strings, so a parsed
struct is JSON, and the three fields that arrive as real JSON pass through as they are.
Recursive descent and quote-aware, because ``(``, ``)`` and ``,`` all occur inside
quoted asset paths -- ``split(',')`` and regex approaches are wrong.
"""

from __future__ import annotations

from ..jsontypes import JsonArray, JsonObject, JsonValue

__all__ = ["amount", "as_float", "as_list", "obj_class", "parse_struct"]

_WS = " \t\r\n"


class UeStructError(ValueError):
    """Raised when a struct string is malformed."""


def parse_struct(text: JsonValue) -> JsonValue:
    """Parse a UE struct string.

    Accepts any Docs.json value because three fields arrive as real JSON rather than struct
    strings (``mUnlocks``, ``mSchematicDependencies``, ``mFuel``); their *inner* fields are
    still struct strings. Passing those through unchanged lets callers treat every field
    uniformly. A number or a bool is no struct string, and raises ``TypeError``.
    """
    if text is None:
        return None
    if isinstance(text, (list, dict)):
        return text
    if not isinstance(text, str):
        raise TypeError(f"a struct string, not {type(text).__name__}")
    s = text.strip()
    if not s:
        return None
    value, idx = _parse_value(s, 0)
    idx = _skip_ws(s, idx)
    if idx != len(s):
        raise UeStructError(f"trailing input at {idx}: {s[idx : idx + 40]!r}")
    return value


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _parse_value(s: str, i: int) -> tuple[JsonValue, int]:
    i = _skip_ws(s, i)
    if i >= len(s):
        return "", i
    if s[i] == "(":
        return _parse_list(s, i)
    if s[i] == '"':
        return _parse_quoted(s, i)
    return _parse_bare(s, i)


def _parse_list(s: str, i: int) -> tuple[JsonValue, int]:
    assert s[i] == "("
    i += 1
    items: JsonArray = []
    keys: list[str | None] = []
    i = _skip_ws(s, i)
    if i < len(s) and s[i] == ")":
        return {}, i + 1  # empty struct "()"
    while True:
        i = _skip_ws(s, i)
        key, i = _try_parse_key(s, i)
        value, i = _parse_value(s, i)
        keys.append(key)
        items.append(value)
        i = _skip_ws(s, i)
        if i >= len(s):
            raise UeStructError("unterminated list")
        if s[i] == ",":
            i += 1
            continue
        if s[i] == ")":
            i += 1
            break
        raise UeStructError(f"expected ',' or ')' at {i}: {s[i - 20 : i + 20]!r}")
    # Keyed entries become a dict; a mix (never observed) prefers the dict for named ones.
    if any(k is not None for k in keys):
        return {k: v for k, v in zip(keys, items) if k is not None}, i
    return items, i


def _try_parse_key(s: str, i: int) -> tuple[str | None, int]:
    """Look ahead for ``KEY=``; returns (None, i) if this entry is positional."""
    j = i
    while j < len(s) and (s[j].isalnum() or s[j] in "_"):
        j += 1
    k = _skip_ws(s, j)
    if j > i and k < len(s) and s[k] == "=":
        return s[i:j], k + 1
    return None, i


def _parse_quoted(s: str, i: int) -> tuple[str, int]:
    assert s[i] == '"'
    i += 1
    out: list[str] = []
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise UeStructError("unterminated quoted string")


def _parse_bare(s: str, i: int) -> tuple[str, int]:
    start = i
    depth = 0
    while i < len(s):
        c = s[i]
        if c == '"':
            _, i = _parse_quoted(s, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    return s[start:i].strip(), i


def as_list(value: JsonValue) -> JsonArray:
    """Normalise a parsed value to a list.

    ``((A=1))`` parses to ``[{...}]`` but a bare ``(A=1)`` parses to ``{...}``.
    Callers that iterate entries must go through this.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def obj_class(ref: JsonValue) -> str | None:
    """Extract the class name from any of the reference shapes Docs.json uses.

    Handles both asset paths (943 occurrences) and raw native names (51)::

        "/Script/Engine.BlueprintGeneratedClass'/Game/.../Desc_Rubber.Desc_Rubber_C'" -> Desc_Rubber_C
        /Game/FactoryGame/.../Build_OilRefinery.Build_OilRefinery_C                   -> Build_OilRefinery_C
        /Script/FactoryGame.FGBuildableAutomatedWorkBench                             -> FGBuildableAutomatedWorkBench
    """
    if not ref:
        return None
    s = str(ref).strip().strip('"')
    if "'" in s:  # BlueprintGeneratedClass'...' wrapper
        inner = s.split("'")
        s = inner[1] if len(inner) > 1 and inner[1] else inner[0]
    s = s.rstrip("'").strip()
    if not s:
        return None
    return s.rsplit(".", 1)[-1] if "." in s else s


def as_float(raw: JsonValue, default: float = 0.0) -> float:
    """A float from a Docs field, tolerating absence and struct-valued fields.

    ``Desc_Locomotive_C.mPowerConsumption`` is ``(Min=25,Max=110)``, so a blanket ``float()``
    over every class crashes; a range reads as its ``Max``. A list or a dict is not a
    number and reads as ``default``, as ``float()``'s ``TypeError`` did.
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, str) and raw.startswith("("):
        parsed = parse_struct(raw)
        if isinstance(parsed, dict):
            return as_float(parsed.get("Max") or parsed.get("Min"), default)
        return default
    if isinstance(raw, (list, dict)):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def amount(entry: JsonObject, key: str = "Amount", default: float = 0.0) -> float:
    """Read a numeric struct member, defaulting when UE omitted it.

    UE omits struct members equal to their default, so ``Schematic_Goat_C.mCost``
    has an ``ItemClass`` but no ``Amount``. Indexing would KeyError.
    """
    return as_float(entry.get(key), default)

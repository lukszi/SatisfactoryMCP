"""How ``_meta`` carries numbers: a distance spread, a count table, and typed values as JSON."""

from __future__ import annotations

import collections
from collections.abc import Iterable, Mapping

from satisfactory_mcp.core.jsontypes import JsonArray, JsonObject, JsonValue


def spread(values: list[float]) -> JsonObject:
    """Matched count plus the distribution of a list of distances."""
    ordered = sorted(values)
    return {
        "matched": len(ordered),
        "median_cm": round(ordered[len(ordered) // 2], 6) if ordered else None,
        "p90_cm": round(ordered[int(0.9 * (len(ordered) - 1))], 6) if ordered else None,
        "max_cm": round(ordered[-1], 3) if ordered else None,
        "over_1cm": sum(1 for v in ordered if v > 1),
        "over_1m": sum(1 for v in ordered if v > 100),
    }


def ranked(counter: collections.Counter[str]) -> list[tuple[str, int]]:
    """Biggest first, ties by name: the order ``_meta`` lists counts in."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def by_count(counter: collections.Counter[str]) -> JsonObject:
    """A counter as a plain dict, biggest first, so ``_meta`` reads in a stable order."""
    return dict(ranked(counter))


def json_array(values: Iterable[JsonValue]) -> JsonArray:
    """``values`` as a JSON array: to a type checker a ``list[int]`` is no ``list[JsonValue]``."""
    return list(values)


def json_object(values: Mapping[str, JsonValue]) -> JsonObject:
    """``values`` as a JSON object, for the reason ``json_array`` gives."""
    return dict(values)

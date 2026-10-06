"""The two shapes ``_meta`` summarises numbers in: a distance spread and a count table."""

from __future__ import annotations

import collections


def spread(values: list[float]) -> dict[str, float | int | None]:
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


def by_count(counter: collections.Counter) -> dict[str, int]:
    """A counter as a plain dict, biggest first, so ``_meta`` reads in a stable order."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

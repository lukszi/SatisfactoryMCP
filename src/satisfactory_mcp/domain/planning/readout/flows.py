"""One item's flows from its suppliers to its users: a deterministic decomposition.

The LP keeps one balance row per item, never who feeds whom, so the graph splits each balance
by the north-west corner rule over both sides in a fixed order. docs/planning.md §8.5o says
why that rule and what the order is.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["Flow", "decompose"]

#: ``(source, target, per_min)``.
Flow = tuple[str, str, float]

_EPS = 1e-9


def decompose(
    supply: Sequence[tuple[str, float]], demand: Sequence[tuple[str, float]]
) -> list[Flow]:
    """Pair ``supply`` with ``demand`` in the order given, each side used up in turn.

    Both sides must total the same; every flow is positive, there are at most
    ``len(supply) + len(demand) - 1`` of them, and each side's flows sum to its amounts.
    """
    flows: list[Flow] = []
    left = [(node, amount) for node, amount in demand if amount > _EPS]
    j, need = 0, left[0][1] if left else 0.0
    for node, amount in supply:
        have = amount
        while have > _EPS and j < len(left):
            moved = min(have, need)
            flows.append((node, left[j][0], moved))
            have -= moved
            need -= moved
            if need <= _EPS:
                j += 1
                need = left[j][1] if j < len(left) else 0.0
    return flows

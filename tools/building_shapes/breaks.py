"""Natural breaks (Fisher-Jenks) of a weighted, sorted series, with a veto per group."""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.arrays import BoolMask, F64Grid

__all__ = ["natural_breaks"]


class _Prefix:
    """Prefix sums of a weighted series: any run's squared spread in O(1)."""

    def __init__(self, values: F64Grid, weights: F64Grid) -> None:
        self.w = np.concatenate([[0.0], np.cumsum(weights)])
        self.wv = np.concatenate([[0.0], np.cumsum(weights * values)])
        self.wvv = np.concatenate([[0.0], np.cumsum(weights * values * values)])

    def spread(self, first: np.ndarray, last: int) -> np.ndarray:
        """Weighted squared deviation of ``values[first:last]`` from its mean, per ``first``."""
        mass = self.w[last] - self.w[first]
        total = self.wv[last] - self.wv[first]
        with np.errstate(invalid="ignore", divide="ignore"):
            share = np.where(mass > 0, total * total / mass, 0.0)
        return self.wvv[last] - self.wvv[first] - share


def natural_breaks(
    values: F64Grid, weights: F64Grid, classes: int, allowed: BoolMask | None = None
) -> list[int]:
    """Where to cut sorted ``values`` into at most ``classes`` tight groups.

    ``allowed[first, last]`` says whether ``values[first:last]`` may be one group (all runs
    when None). The most groups that can be cut from allowed runs win, each as tight around its
    weighted mean as possible (least total weighted squared deviation). Returns the index each
    group past the first starts at; empty when not even two groups fit.
    """
    n = len(values)
    if allowed is None:
        allowed = np.ones((n + 1, n + 1), dtype=bool)
    run = _Prefix(values, weights)
    for count in range(min(classes, n), 1, -1):
        cuts = _exact_breaks(run, n, count, allowed)
        if cuts is not None:
            return cuts
    return []


def _exact_breaks(run: _Prefix, n: int, classes: int, allowed: BoolMask) -> list[int] | None:
    """``natural_breaks`` for exactly ``classes`` groups, or None when no cut is allowed."""
    # cost[k, j]: least spread of values[:j] in k + 1 groups; start[k, j]: where the last begins.
    cost = np.full((classes, n + 1), np.inf)
    start = np.zeros((classes, n + 1), dtype=np.int64)
    for j in range(1, n + 1):
        if allowed[0, j]:
            cost[0, j] = run.spread(np.array([0]), j)[0]
    for k in range(1, classes):
        for j in range(k + 1, n + 1):
            firsts = np.arange(k, j)
            totals = np.where(
                allowed[firsts, j], cost[k - 1, firsts] + run.spread(firsts, j), np.inf
            )
            best = int(np.argmin(totals))
            cost[k, j], start[k, j] = totals[best], firsts[best]
    if not np.isfinite(cost[classes - 1, n]):
        return None
    cuts: list[int] = []
    j = n
    for k in range(classes - 1, 0, -1):
        j = int(start[k, j])
        cuts.append(j)
    return cuts[::-1]

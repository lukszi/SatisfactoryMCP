"""A union-find over any hashable keys, shared by every grouping pass in the domain."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Generic, TypeVar

__all__ = ["UnionFind"]

K = TypeVar("K", bound=Hashable)


class UnionFind(Generic[K]):
    """Disjoint sets, created on first sight of a key."""

    def __init__(self) -> None:
        self._parent: dict[K, K] = {}

    def find(self, key: K) -> K:
        """The root of ``key``'s set, halving the path on the way up."""
        parent = self._parent
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(self, a: K, b: K) -> None:
        """Join two sets; ``a``'s root goes under ``b``'s, so ``b``'s root names the result."""
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b

"""The name tables behind the projection's interned columns."""

from __future__ import annotations

__all__ = ["Interner"]


class Interner:
    """Names to dense indices in first-seen order; a row stores the index, the table the name.

    ``freeze`` stops new names: once ``graph["actors"]`` is written, minting an actor index
    would point past the end of the list a reader joins against.
    """

    def __init__(self) -> None:
        self._index: dict[str, int] = {}
        self._names: list[str] = []
        self._frozen = False

    def intern(self, name: str) -> int:
        index = self._index.get(name)
        if index is None:
            if self._frozen:
                raise RuntimeError(f"cannot intern {name!r}: the table is frozen")
            index = self._index[name] = len(self._names)
            self._names.append(name)
        return index

    def index_of(self, name: str, missing: int = -1) -> int:
        return self._index.get(name, missing)

    def names(self) -> list[str]:
        return list(self._names)

    def freeze(self) -> None:
        self._frozen = True

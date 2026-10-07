"""Object references: a level name and a path name, the pointer every layer above the reader
decodes the same way."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import expect
from .reader import Reader

__all__ = ["ObjectReference", "read_reference", "read_references", "read_soft_reference"]

#: Two zero-length strings, the shortest reference the bytes can hold.
_MIN_REFERENCE_BYTES = 8


@dataclass(slots=True)
class ObjectReference:
    """A reference to another object: the level it lives in and its full path.

    ``pathName`` is the spelling the projection reads, and ``__str__`` returns it so that a
    reference formats as the thing it points at.
    """

    level_name: str
    path_name: str

    @property
    def pathName(self) -> str:
        return self.path_name

    @property
    def levelName(self) -> str:
        return self.level_name

    def __str__(self) -> str:
        return self.path_name


def read_reference(r: Reader) -> ObjectReference:
    """A level name and a path name, in that order; an unset one reads back as ``("", "")``."""
    return ObjectReference(r.string(), r.string())


def read_soft_reference(r: Reader) -> list[ObjectReference | str]:
    """FSoftObjectPath: a package name, an asset name, and a sub-path.

    THREE strings, not two, which is what distinguishes it from ``ObjectProperty``: read as a
    plain reference it leaves 4 bytes over and fails the size check.
    """
    return [ObjectReference(r.string(), r.string()), r.string()]


def read_references(r: Reader, limit: int) -> list[ObjectReference]:
    """A counted list of references, bounded by what the bytes up to ``limit`` can hold.

    A flat ceiling is too generous to be a check: the count lives inside the payload, so a torn
    file can make it anything.
    """
    count = r.i32()
    expect(
        0 <= count <= (limit - r.pos) // _MIN_REFERENCE_BYTES,
        r.pos - 4,
        f"a reference list claims {count} entries with {limit - r.pos} bytes of payload "
        "left, and a reference is at least eight",
    )
    return [read_reference(r) for _ in range(count)]

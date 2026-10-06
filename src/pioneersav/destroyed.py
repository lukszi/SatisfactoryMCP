"""The destroyed-actor lists: which map-placed actors a save records as gone.

Three lists in three places of the body -- after each level's headers, in each sub-level's
trailer, and closing the body -- all built from one ``[i32 count][refs]`` shape. Their overlap
and what they record: docs/savparse-notes.md, ``#### The destroyed-actor lists``.
"""

from __future__ import annotations

from .errors import expect
from .reader import Reader
from .versions import FIRST_MODERN_BODY

__all__ = ["read_closing_destroyed_table", "read_destroyed_block", "read_destroyed_refs"]


def read_destroyed_refs(r: Reader, where: str, limit: int) -> list[tuple[str, str]]:
    """A count, then that many ``(level name, actor path)`` pairs."""
    at = r.pos
    count = r.i32()
    expect(
        0 <= count <= 1_000_000 and r.pos + count * 8 <= limit,
        at,
        f"{where}: {count} destroyed actors do not fit in the {limit - r.pos} bytes left",
    )
    return [(r.string(), r.string()) for _ in range(count)]


def read_destroyed_block(r: Reader, name: str, end: int, *, grouped: bool) -> list[tuple[str, str]]:
    """The destroyed-actor list trailing a level's header block.

    A sub-level writes one bare ``[i32 count][refs]``; the persistent level groups it by
    partition cell, ``[i32 groups][str cell][i32 count][refs]``. Nothing in the file says
    which, so reading the wrong shape lands off the block's end, which the caller checks.
    """
    if not grouped:
        return read_destroyed_refs(r, f"level {name!r}", end)
    at = r.pos
    groups = r.i32()
    expect(
        0 <= groups <= 100_000,
        at,
        f"level {name!r}: its destroyed-actor list claims {groups} cell groups",
    )
    refs: list[tuple[str, str]] = []
    for _ in range(groups):
        cell = r.string()
        refs.extend(read_destroyed_refs(r, f"level {name!r} cell {cell!r}", end))
    return refs


def read_closing_destroyed_table(
    r: Reader, warnings: list[tuple[int, str]], save_version: int
) -> list[tuple[str, str]]:
    """The body's last structure: destroyed actors grouped by level name, two lists per group --
    looted drop pods and crashed ships, then Mercer shrines. Below saveVersion 52, one bare list.

    Parsed rather than skipped because landing exactly on the last byte is what proves every
    count and size before it was right.
    """
    refs: list[tuple[str, str]]
    if save_version < FIRST_MODERN_BODY:
        refs = read_destroyed_refs(r, "the closing destroyed-actor list", len(r.data))
        if r.remaining:
            warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor list"))
        return refs
    at = r.pos
    groups = r.i32()
    expect(0 <= groups <= 100_000, at, f"the closing table claims {groups} level groups")
    refs = []
    for _ in range(groups):
        name = r.string()
        for which in (1, 2):
            refs.extend(
                read_destroyed_refs(r, f"closing table, level {name!r}, list {which}", len(r.data))
            )
    if r.remaining:
        warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor table"))
    return refs

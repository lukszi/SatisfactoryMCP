"""What an extraction could not read, drained into the projection's ``warnings``.

Every guard in the walk ``continue``s past what it cannot read, so that one undecodable spline
does not cost the other pipes. Silent, they would publish a smaller world and call it the
world, so each guard counts its drop and the censuses here say so.
"""

from __future__ import annotations

import collections
from collections.abc import Container
from typing import Final, TypeAlias

from ..schema import Projection
from .registers import DISMISSED_FACTORY_CLASSES

__all__ = [
    "CHAIN_NOTES_SHOWN",
    "DROP_REASONS_SHOWN",
    "UNFILED_CLASSES_SHOWN",
    "Drops",
    "drop_notes",
    "null_yaw_note",
    "unfiled_notes",
]

#: What a run threw away, keyed by a sentence that reads with a count in front of it.
Drops: TypeAlias = collections.Counter[str]

#: A broken parser produces many reasons at once, and the first few say so as well as all.
DROP_REASONS_SHOWN = 6

#: Per-chain stderr lines before they stop: a version bump makes every chain unreadable.
CHAIN_NOTES_SHOWN = 5

#: One update renames a handful of buildings; a longer list is a parser that lost the format.
UNFILED_CLASSES_SHOWN = 8

#: The record lists carrying a placement yaw.
_PLACED_RECORD_KEYS: Final = (
    "machines",
    "extractors",
    "generators",
    "attachments",
    "storage",
    "crates",
)


def drop_notes(drops: Drops) -> list[str]:
    """One ``warnings`` sentence per kind of record this extraction threw away."""
    notes = [f"{count} {reason}" for reason, count in drops.most_common(DROP_REASONS_SHOWN)]
    rest = len(drops) - DROP_REASONS_SHOWN
    if rest > 0:
        notes.append(f"and {rest} further kind(s) of unreadable record, not listed")
    return notes


def unfiled_notes(unfiled: dict[str, str], factoryish: Container[str]) -> list[str]:
    """The buildings this run recognised as production and filed nowhere.

    ``factoryish`` is every instanceName carrying a productivity monitor or a machine buffer,
    the game's own mark of a factory building, so walls and foundations stay quiet without a
    list of them; ``DISMISSED_FACTORY_CLASSES`` silences the ones that carry it on purpose.
    """
    census = collections.Counter(
        cls
        for instance, cls in unfiled.items()
        if instance in factoryish and cls not in DISMISSED_FACTORY_CLASSES
    )
    if not census:
        return []
    named = ", ".join(f"{count}x {cls}" for cls, count in census.most_common(UNFILED_CLASSES_SHOWN))
    rest = len(census) - UNFILED_CLASSES_SHOWN
    return [
        f"{len(census)} building class(es) carry a productivity monitor or a machine buffer "
        f"and this projection filed no record for any of them, so they are missing from "
        f"machines, extractors and generators: {named}"
        + (f", and {rest} more" if rest > 0 else "")
        + ". Each belongs in a hint list in extract/registers.py, or in "
        "DISMISSED_FACTORY_CLASSES beside them"
    ]


def null_yaw_note(projection: Projection) -> list[str]:
    """The placements whose rotation would not read, counted off the finished payload.

    Off the payload rather than inside ``yaw_of``, so the number is exactly the null yaws a
    reader can go and find; every list carrying a placement is counted.
    """
    unread = (
        sum(
            1
            for key in _PLACED_RECORD_KEYS
            for record in projection[key]
            if record.get("yaw") is None
        )
        + sum(1 for row in projection["structures"]["instances"] if len(row) > 4 and row[4] is None)
        + sum(1 for row in projection["power"]["poles"]["instances"] if row[4] is None)
    )
    if not unread:
        return []
    return [
        (
            f"{unread} placement(s) carry a rotation this parser could not read; "
            "their yaw is null rather than 0, which would have meant axis-aligned"
        )
    ]

"""What ``/api/events`` sends: the event names, and the record a publisher hands the watcher.

The names and their data are listed in docs/web-wire.md ("The event stream").
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "KINDS",
    "KIND_ACTIVITY",
    "KIND_MAPS",
    "KIND_NOTES",
    "KIND_PLANS",
    "KIND_SAVE",
    "KIND_SETTINGS",
    "WatchEvent",
]

#: The game wrote a save.
KIND_SAVE = "save"

#: This project wrote a factory label, or the legacy top-level plan file.
KIND_NOTES = "notes"

#: New commits in one plan's log: one event per plan per tail tick.
KIND_PLANS = "plans"

#: One new activity-journal entry.
KIND_ACTIVITY = "activity"

#: The shared settings file changed.
KIND_SETTINGS = "settings"

#: A map generation job moved, or the map registry did. State rather than a journal: the
#: newest one is all a page needs, and it is replayed to every new subscriber.
KIND_MAPS = "maps"

#: Every kind, in the order a newly connected browser is told about them.
KINDS = (KIND_SAVE, KIND_NOTES, KIND_PLANS, KIND_ACTIVITY, KIND_SETTINGS, KIND_MAPS)


@dataclass(frozen=True)
class WatchEvent:
    """One thing that moved: a file's newest mtime, or a tailed entry with its data.

    ``kind`` is the SSE event NAME and is deliberately not in ``as_dict``: the wire carries
    it as ``event:``, and a copy in the data would be two places to read one fact.
    """

    kind: str
    filename: str
    mtime: float
    data: dict | None = field(default=None, compare=False)

    def as_dict(self) -> dict:
        if self.data is not None:
            return dict(self.data)
        return {"filename": self.filename, "mtime": self.mtime}

"""One small cached row per save, so a world can be compared to its own past.

A row is built from a projection and then the projection is dropped; the whole point is that
45 saves cost 45 parses ONCE and every later question is answered from ~2 kB per save. Rows
key on save IDENTITY, never on the filename, because autosave names rotate. Two axes exist
and only one of them is production: see `Timeline.window_note`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from ... import config
from ...core import atomic
from ...core.saveio import projection as proj
from ...core.saveio.schema import SaveHeader
from ...core.text import format_playtime

if TYPE_CHECKING:
    from .state import WorldState

__all__ = [
    "INDEX_SCHEMA",
    "Timeline",
    "TimelineDelta",
    "TimelineRow",
    "build_row",
    "load_timeline",
    "row_key",
    "save_row",
]

#: Bumped when a row gains or corrects a field. Part of the row key, so an old row misses
#: rather than being served thin. Separate from the projection's schema, which is ALSO in the
#: key: a projection bump that only adds geometry leaves every field here untouched, but the
#: row was still computed by different code and nothing cheap can prove the difference.
INDEX_SCHEMA = 2


class TimelinePhase(TypedDict):
    game_phase: str | None
    highest_complete_tier: int | None
    purchased_schematics: int


class TimelineRow(TypedDict):
    """One save's index row, as ``build_row`` writes it and the index file keeps it."""

    key: str
    index_schema: int
    projection_schema: int
    world_id: str
    filename: str
    play_duration_s: int
    mtime_ns: int
    save_version: int
    build_version: int
    counts: dict[str, int]
    #: ``installed_mw``, ``drawn_mw`` and ``measured_mw``; compared key by key.
    power: dict[str, float]
    phase: TimelinePhase
    stock: dict[str, float]
    sites: dict[str, int]


#: ``Timeline.changed_since``'s answer; ``from`` is a keyword, hence the call form. Each
#: ``counts`` entry is ``(then, now, change)``, each ``sites`` entry ``(then, now)``.
TimelineDelta = TypedDict(
    "TimelineDelta",
    {
        "from": str,
        "to": str,
        "played_s": int,
        "wall_clock_s": float,
        "counts": dict[str, tuple[int, int, int]],
        "power": dict[str, tuple[float, float, float]],
        "phase": tuple[TimelinePhase, TimelinePhase],
        "sites": dict[str, tuple[int, int]],
        "builds_crossed": list[int],
        "window": str,
    },
)


def row_key(header: SaveHeader) -> str:
    """Identity of one save, as a world state rather than as a file.

    ``mtime_ns`` and ``size`` are what separate two states that share a filename: the game
    recycles ``autosave_0``/``_1``/``_2``, so a name alone names three different worlds an
    hour apart and, later the same session, three others.
    """
    raw = "|".join(
        [
            str(header.get("save_identifier") or f"session:{header.get('session_name')}"),
            str(header.get("play_duration_s")),
            str(header["mtime_ns"]),
            str(header["size"]),
            str(INDEX_SCHEMA),
            str(proj.SCHEMA_VERSION),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


#: How many of the largest spendable stocks a row keeps.
_STOCK_ITEMS = 30


def build_row(state: WorldState) -> TimelineRow:
    """The index row for one ``WorldState``. Costs ~40 ms once the save is parsed."""
    header = state.projection.get("header") or {}
    projection = state.projection
    power = state.power_report(biomass=True)
    progress = state.progression()
    stock = state.stock()
    return {
        "key": row_key(header),
        "index_schema": INDEX_SCHEMA,
        "projection_schema": projection.get("schema_version"),
        "world_id": state.world_id,
        "filename": header.get("filename"),
        "play_duration_s": header.get("play_duration_s"),
        "mtime_ns": header.get("mtime_ns"),
        "save_version": header.get("save_version"),
        # In the row and not merely in the header dump: a series that crosses a build
        # boundary can move for reasons no player caused, and only this field says where.
        "build_version": header.get("build_version"),
        "counts": {
            "machines": len(projection.get("machines") or ()),
            "extractors": len(projection.get("extractors") or ()),
            "generators": len(projection.get("generators") or ()),
            "attachments": len(projection.get("attachments") or ()),
            "storage": len(projection.get("storage") or ()),
            "crates": len(projection.get("crates") or ()),
        },
        "power": {
            "installed_mw": round(power.get("generation_mw") or 0.0, 1),
            "drawn_mw": round(power.get("draw_mw") or 0.0, 1),
            "measured_mw": round(power.get("measured_draw_mw") or 0.0, 1),
        },
        "phase": {
            "game_phase": progress.get("game_phase"),
            "highest_complete_tier": progress.get("highest_complete_tier"),
            "purchased_schematics": progress.get("purchased_schematics"),
        },
        "stock": {
            k: round(v, 1) for k, v in sorted(stock.items(), key=lambda kv: -kv[1])[:_STOCK_ITEMS]
        },
        "sites": {f"{s['grid']}": s["count"] for s in state.sites()},
    }


def _index_path(world_id: str) -> Path:
    directory = config.cache_dir() / "timeline"
    directory.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(world_id.encode("utf-8")).hexdigest()[:16]
    return directory / f"timeline-{stem}.json"


@dataclass
class Timeline:
    """The rows this machine can still see for one world, oldest first."""

    world_id: str
    rows: list[TimelineRow]

    def __post_init__(self) -> None:
        self.rows.sort(key=lambda r: (r.get("play_duration_s") or 0, r.get("mtime_ns") or 0))

    def window_note(self) -> str:
        """What this index can actually see, said before any answer drawn from it.

        Playtime, not wall clock, is the axis: the reference world has two saves 232 days
        apart that are 5.6 hours of play apart, and a wall-clock series would draw seven
        months of stall between them.
        """
        if not self.rows:
            return f"no indexed saves for world {self.world_id}"
        first, last = self.rows[0], self.rows[-1]
        played = (last["play_duration_s"] or 0) - (first["play_duration_s"] or 0)
        builds = sorted({r["build_version"] for r in self.rows if r.get("build_version")})
        note = (
            f"window: {len(self.rows)} save(s) still on disk, playtime "
            f"{format_playtime(first['play_duration_s'])} to {format_playtime(last['play_duration_s'])} "
            f"({format_playtime(played)} of play). History is lossy -- deleted and rotated saves "
            f"leave gaps this index cannot see."
        )
        if len(builds) > 1:
            note += (
                f" It spans game builds {builds[0]}..{builds[-1]}, so a series crossing one "
                "can move without the player touching anything."
            )
        return note

    def at(self, filename: str) -> TimelineRow | None:
        """The NEWEST row with this filename. A rotating autosave name has several."""
        rows = [r for r in self.rows if r.get("filename") == filename]
        return rows[-1] if rows else None

    def changed_since(self, anchor: TimelineRow) -> TimelineDelta:
        """Aggregate change from ``anchor`` to the newest row, on both axes."""
        last = self.rows[-1]
        crossed = sorted(
            {
                r["build_version"]
                for r in self.rows
                if r.get("build_version")
                and anchor["play_duration_s"] <= r["play_duration_s"] <= last["play_duration_s"]
            }
        )
        return {
            "from": anchor["filename"],
            "to": last["filename"],
            "played_s": (last["play_duration_s"] or 0) - (anchor["play_duration_s"] or 0),
            "wall_clock_s": ((last["mtime_ns"] or 0) - (anchor["mtime_ns"] or 0)) / 1e9,
            "counts": {
                k: (anchor["counts"].get(k, 0), v, v - anchor["counts"].get(k, 0))
                for k, v in last["counts"].items()
                if v != anchor["counts"].get(k, 0)
            },
            "power": {
                k: (anchor["power"].get(k, 0.0), v, round(v - anchor["power"].get(k, 0.0), 1))
                for k, v in last["power"].items()
            },
            "phase": (anchor["phase"], last["phase"]),
            "sites": {
                g: (anchor["sites"].get(g, 0), last["sites"].get(g, 0))
                for g in sorted(set(anchor["sites"]) | set(last["sites"]))
                if anchor["sites"].get(g, 0) != last["sites"].get(g, 0)
            },
            "builds_crossed": crossed,
            "window": self.window_note(),
        }


def load_timeline(world_id: str) -> Timeline:
    """Rows cached for this world. Rows keyed by a stale schema are dropped on read."""
    index_path = _index_path(world_id)
    rows: list[TimelineRow] = []
    if index_path.is_file():
        try:
            rows = [
                r
                for r in json.loads(index_path.read_text(encoding="utf-8"))
                if r.get("index_schema") == INDEX_SCHEMA
                and r.get("projection_schema") == proj.SCHEMA_VERSION
            ]
        except (OSError, ValueError):
            rows = []
    return Timeline(world_id=world_id, rows=rows)


def save_row(timeline: Timeline, row: TimelineRow) -> Timeline:
    """Merge one row in and write the index. Best-effort, like every other cache here."""
    rows = [r for r in timeline.rows if r["key"] != row["key"]]
    rows.append(row)
    merged = Timeline(world_id=timeline.world_id, rows=rows)
    try:
        atomic.write_bytes(
            _index_path(timeline.world_id),
            json.dumps(merged.rows, separators=(",", ":")).encode("utf-8"),
        )
    except OSError:
        pass
    return merged

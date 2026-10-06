"""Reading the saves: per save, which map actors it holds live, which it calls gone, and the
respawn properties of the harvestable plants."""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pioneersav import (
    FIRST_MODERN_BODY,
    ActorHeader,
    ParseError,
    decompress_body,
    read_body,
    read_info_bytes,
    read_object,
)
from tools.collectibles.catalog import (
    CATEGORIES,
    DROP_POD_CLASS,
    LOOTED_PROPERTY,
    RESPAWN_PROBE,
    RESPAWN_PROPERTIES,
    ActorKey,
    Position,
)

#: .NET ticks are 100 ns intervals since 0001-01-01, which is what the save header stores.
_TICK_EPOCH = datetime(1, 1, 1, tzinfo=UTC)

#: (class, position, looted-or-None) of one live actor of an emitted class.
LiveRecord = tuple[str, Position, bool | None]

#: (cell, instance) -> (class, mNumRespawns), where a plant's record carries the counter.
FloraCounter = dict[ActorKey, tuple[str, int]]


@dataclass
class SaveFacts:
    """One save's contribution, without keeping its 44 MB body alive."""

    path: Path
    name: str
    save_version: int
    build_version: int
    session: str
    play_seconds: int
    ticks: int
    #: (cell, instance leaf) -> the live record, for the emitted classes.
    live: dict[ActorKey, LiveRecord]
    #: (cell, path leaf) for every map actor this save records as gone.
    destroyed: set[ActorKey]
    #: Cells the save has a level record for, and cells the grid table declares.
    recorded_cells: set[str] = field(default_factory=set)
    declared_cells: set[str] = field(default_factory=set)
    #: Every live actor of ANY map-placed class; kept for the newest save only.
    live_any_class: set[ActorKey] = field(default_factory=set)
    #: flora class -> how many live records of it this save holds.
    flora_records: collections.Counter = field(default_factory=collections.Counter)
    #: (flora class, property) -> how many records carry that property at all.
    flora_property_records: collections.Counter = field(default_factory=collections.Counter)
    #: (flora class, property, value) -> count, for the properties in RESPAWN_PROPERTIES.
    flora_values: collections.Counter = field(default_factory=collections.Counter)
    #: The series whose monotonicity is the respawn test.
    flora_counter: FloraCounter = field(default_factory=dict)
    #: Flora records whose property block would not decode, so missing is not unreadable.
    flora_unreadable: int = 0

    @property
    def when(self) -> datetime:
        return _TICK_EPOCH + timedelta(microseconds=self.ticks / 10)


def find_saves(root: Path) -> list[Path]:
    """Every ``.sav`` in *root* AND in the per-account subdirectory Steam uses.

    Both: the game keeps the saves in the account directory and drops a 105-byte
    ``ServerManager_V2.sav`` in the root, which the reader then skips as unreadable.
    """
    return sorted(set(root.glob("*.sav")) | set(root.glob("*/*.sav")))


def read_save_facts(path: Path, map_classes: set[str], keep_all_classes: bool) -> SaveFacts | None:
    """Pull one save's collectible facts, or return None with a printed reason.

    Only the level walk is eager; property blocks are decoded by offset, and only for the
    drop pods and the probed plants.
    """
    try:
        data = path.read_bytes()
        info = read_info_bytes(data)
        old = info.save_version < FIRST_MODERN_BODY
        inflated = decompress_body(data, info.body_offset, old=old)
        body = read_body(inflated, info.save_version)
    except (ParseError, OSError) as exc:
        print(f"  skip {path.name}: {exc}")
        return None

    facts = SaveFacts(
        path=path,
        name=path.name,
        save_version=info.save_version,
        build_version=info.build_version,
        session=info.session_name,
        play_seconds=info.play_duration_s,
        ticks=info.save_datetime_ticks,
        live={},
        destroyed={(cell, name.rsplit(".", 1)[-1]) for cell, name in body.destroyed_actors},
        recorded_cells={level.name for level in body.levels},
        declared_cells={cell for grid in body.preamble.grids for cell in grid.cell_names},
    )
    for level in body.levels:
        for header, slot in zip(level.headers, level.objects, strict=True):
            if not isinstance(header, ActorHeader):
                continue
            cls = header.type_path.rsplit(".", 1)[-1]
            key = (level.name, header.instance_name.rsplit(".", 1)[-1])
            if keep_all_classes and cls in map_classes:
                facts.live_any_class.add(key)
            probed = cls in RESPAWN_PROBE
            if cls not in CATEGORIES and not probed:
                continue
            properties = None
            if probed or cls == DROP_POD_CLASS:
                properties = _read_properties(inflated, slot, info.save_version)
            if probed:
                _record_flora(facts, key, cls, properties)
            if cls in CATEGORIES:
                looted = None
                if cls == DROP_POD_CLASS and properties is not None:
                    looted = bool(properties.get(LOOTED_PROPERTY))
                facts.live[key] = (cls, tuple(header.position), looted)
    return facts


def _read_properties(inflated: bytes, slot, save_version: int) -> dict | None:
    try:
        return dict(read_object(inflated, slot, actor=True, save_version=save_version).properties)
    except ParseError:
        return None


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _record_flora(facts: SaveFacts, key: ActorKey, cls: str, properties: dict | None) -> None:
    """Tally one probed plant's respawn properties: presence, values and the counter."""
    facts.flora_records[cls] += 1
    if properties is None:
        facts.flora_unreadable += 1
        return
    for name in RESPAWN_PROPERTIES:
        if name not in properties:
            continue
        facts.flora_property_records[(cls, name)] += 1
        value = properties[name]
        if _is_count(value):
            facts.flora_values[(cls, name, value)] += 1
    respawns = properties.get("mNumRespawns")
    if _is_count(respawns):
        facts.flora_counter[key] = (cls, respawns)

"""Game-version skew: what a node table cut on an older build gets wrong on a newer save.

Resource nodes MOVE when the map changes in a game update, and one has already been
renamed, so a stale table is a recurring condition rather than an anomaly. It breaks two
things silently: a join by instance name finds nothing after a rename, so the save's
mResourcesLeft is unreadable while per-kind count checks still pass; and a position can be
wrong by up to a metre, so "the node nearest to X" answers confidently and wrongly.

The artifact measures both per row under ``_meta.cross_validation.positions``. Every number
is read out of there and none is restated here, because a refresh changes all of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from ....core.jsontypes import JsonObject, JsonValue
from ....core.saveio.records import instance_leaf
from . import table as node_table
from .views import TableAge

__all__ = [
    "TableSkew",
    "drifted_leaf_names",
    "identity_notes",
    "position_notes",
    "skew_for_save",
    "skew_from_meta",
    "skew_notes",
    "table_age",
]

#: The version markers the artifact writes into a comparison block's human-readable
#: ``build`` string, paired with the save-header field of the same name. The block records
#: prose and not a version field, so the number has to be parsed out of the prose.
_VERSION_MARKERS = (("saveVersion", "save_version"), ("buildVersion", "build_version"))

_VERSION_RE = re.compile(r"\b(saveVersion|buildVersion)\s+(\d+)", re.IGNORECASE)

_FIELD_FOR_MARKER = {marker.casefold(): field for marker, field in _VERSION_MARKERS}


def _versions_in(text: object) -> dict[str, int]:
    """Save-header fields and values named in a ``build`` string, e.g. ``saveVersion 52``."""
    if not isinstance(text, str):
        return {}
    return {
        _FIELD_FOR_MARKER[m.group(1).casefold()]: int(m.group(2))
        for m in _VERSION_RE.finditer(text)
    }


@dataclass(frozen=True)
class TableSkew:
    """What this node table is known to get wrong on a build newer than its own.

    Constructed only from the artifact's own ``_meta``, and only when a save is actually
    past the build the table matches -- so on a matching build this is ``None`` and every
    tool stays silent.
    """

    #: Version markers of the build the table matches exactly.
    pin: dict[str, int]
    #: Version markers of the newer build the drift was measured against.
    measured_against: dict[str, int]
    #: Version markers of the save that triggered this report.
    save: dict[str, int]
    #: table instance -> how far it moved in cm; only rows past the rounding floor.
    moved_cm: dict[str, float]
    #: table instance -> signed z delta, the newer build's z minus this table's.
    dz_cm: dict[str, float]
    #: Table rows the newer build has no row for under that name. A join by instance
    #: name MISSES on these, which is invisible unless it is said out loud.
    unjoinable: tuple[str, ...]
    #: table instance -> the name the newer build uses, where the pairing is forced.
    renamed_to: dict[str, str]
    #: How far a renamed row moved, cm.
    renamed_moved_cm: float | None
    #: Whether every recorded move is vertical, so x,y still lands on the right node.
    vertical_only: bool
    #: Whether the newer build confirmed resource and purity on every compared row.
    resource_and_purity_verified: bool

    def __bool__(self) -> bool:
        return bool(self.moved_cm or self.unjoinable)

    @property
    def max_moved_cm(self) -> float:
        return max(self.moved_cm.values(), default=0.0)

    @property
    def gap(self) -> str:
        """How far behind the table is, in whichever marker both sides actually carry."""
        for marker, field in _VERSION_MARKERS:
            if field in self.pin and field in self.save:
                return f"{marker} {self.pin[field]} -> {self.save[field]}"
        for marker, field in _VERSION_MARKERS:
            if field in self.measured_against:
                return f"before {marker} {self.measured_against[field]}"
        return "an older build"

    def scope(self, instances: Iterable[str] | None) -> TableSkew:
        """Narrow to the rows appearing in one answer, so the warning stays proportionate.

        Matching is on the leaf name, and for an unjoinable row on EITHER name: callers hold
        table names in some places and the save's own names in others, and the whole point of
        a rename is that those differ.
        """
        if instances is None:
            return self
        wanted = {instance_leaf(i) for i in instances}
        moved = {k: v for k, v in self.moved_cm.items() if instance_leaf(k) in wanted}
        unjoinable = tuple(
            k
            for k in self.unjoinable
            if instance_leaf(k) in wanted or instance_leaf(self.renamed_to.get(k, "\0")) in wanted
        )
        return replace(
            self,
            moved_cm=moved,
            dz_cm={k: v for k, v in self.dz_cm.items() if k in moved},
            unjoinable=unjoinable,
            renamed_to={k: v for k, v in self.renamed_to.items() if k in unjoinable},
            renamed_moved_cm=self.renamed_moved_cm if unjoinable else None,
        )


def _object(value: JsonValue) -> JsonObject:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else {}


def _number(value: JsonValue) -> float:
    """``float(value or 0.0)``, as the artifact's numbers are read."""
    if not value:
        return 0.0
    if isinstance(value, list | dict):
        raise TypeError(f"not a number: {value!r}")
    return float(value)


def _names(value: JsonValue) -> list[str]:
    """The strings of a JSON list of instance names; nothing for null."""
    return [name for name in value if isinstance(name, str)] if isinstance(value, list) else []


def _pin_and_drift(positions: JsonObject) -> tuple[JsonObject | None, JsonObject | None]:
    """Which comparison the table MATCHES, and which one it lags.

    Chosen by what each block measured and never by its key: a block whose worst delta is
    inside its own rounding floor and which finds no row missing on either side is the build
    this table IS. Keying off block names would make this stop reporting the day the
    generator renames one.
    """
    pin: JsonObject | None = None
    drift: JsonObject | None = None
    worst_seen = -1.0
    for block in positions.values():
        if not isinstance(block, dict):
            continue
        floor = _number(block.get("rounding_floor_cm"))
        worst = _number(block.get("max_position_delta_cm"))
        only_in = [k for k in block if k.startswith("rows_only_in")]
        if worst > floor or any(block.get(k) for k in only_in):
            if worst > worst_seen:
                worst_seen, drift = worst, block
        elif pin is None:
            pin = block
    return pin, drift


def _save_versions(header: Mapping[str, object] | None) -> dict[str, int]:
    """The version markers a save header states, by header field."""
    stated = header or {}
    out: dict[str, int] = {}
    for _marker, field in _VERSION_MARKERS:
        value = stated.get(field)
        if isinstance(value, int):
            out[field] = value
    return out


def _save_is_affected(save: dict[str, int], pin: dict[str, int], against: dict[str, int]) -> bool:
    """Whether a save is past the table's pin, or on exactly the build the drift was
    measured against, which affects it whether or not the pin states a comparable marker."""
    newer = any(save[f] > v for f, v in pin.items() if f in save)
    older = any(save[f] < v for f, v in pin.items() if f in save)
    at_drift_build = bool(against) and any(save.get(f) == v for f, v in against.items())
    if older and not newer:
        return False
    return newer or at_drift_build


def _forced_rename(drift: JsonObject, unjoinable: tuple[str, ...]) -> dict[str, str]:
    """Table name -> the newer build's name, only where the pairing is forced.

    Two lists of names are not a mapping: with one name on each side and a recorded
    distance between them the pairing is forced, and with more it would be invented.
    """
    only_in_build = [
        name
        for key in drift
        if key.startswith("rows_only_in") and key != "rows_only_in_this_table"
        for name in _names(drift.get(key))
    ]
    if (
        len(unjoinable) == 1
        and len(only_in_build) == 1
        and drift.get("renamed_row_moved_cm") is not None
    ):
        return {unjoinable[0]: only_in_build[0]}
    return {}


def _moves_are_vertical(
    instances: list[str], moved_cm: dict[str, float], dz_cm: dict[str, float], floor: float
) -> bool:
    """Whether every recorded move is z alone: a delta the row's own dz cannot account for
    is horizontal, and then x,y no longer lands on the right node."""
    return bool(instances) and all(abs(dz_cm.get(i, 0.0)) >= moved_cm[i] - floor for i in instances)


def _drifted_rows(drift: JsonObject) -> list[tuple[str, JsonObject]]:
    """The rows past the rounding floor that name their instance, by that instance."""
    rows = drift.get("rows_past_the_rounding_floor")
    return [
        (instance, row)
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(instance := row.get("instance"), str) and instance
    ]


def skew_from_meta(meta: JsonObject, header: Mapping[str, object] | None) -> TableSkew | None:
    """The recorded skew, but only when ``header`` names a build past the table's pin.

    Returns ``None`` when there is nothing to say: the artifact records no drift (which
    is what a refresh produces), the save is on the pinned build or older, or the save
    names no version at all. Silence is the default, so a matching build costs nothing.
    """
    positions = _object(_object(meta.get("cross_validation")).get("positions"))
    pin_block, drift = _pin_and_drift(positions)
    if drift is None:
        return None

    pin = _versions_in((pin_block or {}).get("build"))
    against = _versions_in(drift.get("build"))
    save = _save_versions(header)
    if not save or not _save_is_affected(save, pin, against):
        return None

    rows = _drifted_rows(drift)
    floor = _number(drift.get("rounding_floor_cm"))
    moved_cm = {i: _number(r["delta_cm"]) for i, r in rows if r.get("delta_cm") is not None}
    dz_cm = {i: _number(r["dz_cm"]) for i, r in rows if r.get("dz_cm") is not None}
    unjoinable = tuple(_names(drift.get("rows_only_in_this_table")))
    renamed_moved_cm = drift.get("renamed_row_moved_cm")

    skew = TableSkew(
        pin=pin,
        measured_against=against,
        save=save,
        moved_cm=moved_cm,
        dz_cm=dz_cm,
        unjoinable=unjoinable,
        renamed_to=_forced_rename(drift, unjoinable),
        renamed_moved_cm=_number(renamed_moved_cm) if renamed_moved_cm is not None else None,
        vertical_only=_moves_are_vertical([i for i, _row in rows], moved_cm, dz_cm, floor),
        resource_and_purity_verified=drift.get("purity_mismatches") == []
        and drift.get("resource_mismatches") == [],
    )
    return skew or None


def skew_for_save(
    header: Mapping[str, object] | None, table: node_table.NodeTable | None = None
) -> TableSkew | None:
    """``skew_from_meta`` against the shipped table. ``None`` when there is nothing to say."""
    return skew_from_meta((table if table is not None else node_table.load_nodes()).meta, header)


def position_notes(
    skew: TableSkew | None,
    instances: Iterable[str] | None = None,
    *,
    name_limit: int = 3,
) -> list[str]:
    """At most one line, naming the rows in this answer whose position is stale.

    For tools that quote a coordinate or an elevation. Empty when no drifted row is in
    scope, which is the common case.
    """
    if skew is None:
        return []
    scoped = skew.scope(instances)
    if not scoped.moved_cm:
        return []
    ranked = sorted(scoped.moved_cm.items(), key=lambda kv: -kv[1])
    shown = ranked[:name_limit]
    # A direction is only quoted where the artifact recorded one. Deriving a sign from the
    # magnitude would be an invented number, and "the node is 80 cm LOWER than shown" is
    # the half of this a planner acts on.
    signed = all(i in scoped.dz_cm for i, _ in shown)
    named = ", ".join(
        f"{instance_leaf(i)} {scoped.dz_cm[i]:+.0f}cm"
        if i in scoped.dz_cm
        else f"{instance_leaf(i)} {d:.0f}cm"
        for i, d in shown
    )
    if len(ranked) > name_limit:
        named += f", +{len(ranked) - name_limit} more"
    axis = "z" if scoped.vertical_only else "position"
    convention = (
        "; a negative sign means the game sits that much lower than the z shown"
        if signed and scoped.vertical_only
        else ""
    )
    unaffected = (
        " x,y, resource and purity still match the newer build."
        if scoped.vertical_only and scoped.resource_and_purity_verified
        else ""
    )
    note = (
        f"{len(ranked)} node(s) here moved in a game update after this node table was cut "
        f"({scoped.gap}): {axis} is up to {scoped.max_moved_cm:.0f}cm stale -- {named}{convention}."
        f"{unaffected}"
    )
    return [note]


def identity_notes(skew: TableSkew | None, instances: Iterable[str] | None = None) -> list[str]:
    """One line per node in this answer that the save does not have under that name.

    For tools that join the save by instance name -- ``mResourcesLeft``, occupancy, the
    resource and purity behind an extractor -- where the failure is a MISS rather than a
    wrong number, and so shows up as a blank nobody can explain. The note states both
    directions of the lost join, because each loses something different.
    """
    if skew is None:
        return []
    out: list[str] = []
    for inst in skew.scope(instances).unjoinable:
        new = skew.renamed_to.get(inst)
        moved = skew.renamed_moved_cm
        if new:
            how = f"renamed it to {instance_leaf(new)}"
            if moved:
                how += f", {moved:.0f}cm away"
        else:
            how = "dropped that name"
        out.append(
            f"{instance_leaf(inst)} is not in this save under that name: a game update after this "
            f"node table was cut ({skew.gap}) {how}. Nothing joins across that gap, so this "
            "node reads as free whether or not an extractor sits on it, and an extractor on "
            "it reads as having no resource or purity. It is listed rather than dropped -- it "
            "exists in game, and its resource and purity here are verified against the newer "
            "build."
        )
    return out


def skew_notes(
    skew: TableSkew | None,
    instances: Iterable[str] | None = None,
    *,
    name_limit: int = 3,
) -> list[str]:
    """Both halves, for tools that surface positions AND join the save by name."""
    return position_notes(skew, instances, name_limit=name_limit) + identity_notes(skew, instances)


def drifted_leaf_names(skew: TableSkew | None, instances: Iterable[str] | None = None) -> set[str]:
    """Leaf names of the rows in ``instances`` whose position or name the newer build moved."""
    if skew is None:
        return set()
    scoped = skew.scope(instances)
    return {instance_leaf(i) for i in scoped.moved_cm} | {
        instance_leaf(i) for i in scoped.unjoinable
    }


def table_age(
    header: Mapping[str, object] | None,
    table: node_table.NodeTable | None = None,
    instances: Iterable[str] | None = None,
) -> TableAge | None:
    """The node table's age against this save, scoped to ``instances``; ``None`` when current."""
    skew = skew_for_save(header, table)
    if skew is None:
        return None
    scoped = skew.scope(instances)
    return {
        "table": "nodes",
        "behind": True,
        "gap": skew.gap,
        "moved": len(scoped.moved_cm),
        "unjoinable": len(scoped.unjoinable),
        "observed_from": None,
        "observed_matches": None,
        "notes": skew_notes(skew, instances),
    }

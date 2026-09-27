"""Resource nodes: the static table, and which ones the save shows as tapped."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from ... import config
from ...core.gamedata.model import GameData
from . import geo

__all__ = [
    "EXTRACTOR_FOR_KIND",
    "NodeTable",
    "TableSkew",
    "blocking_buildings",
    "drifted",
    "identity_notes",
    "load_nodes",
    "occupancy",
    "position_notes",
    "skew_for_save",
    "skew_from_meta",
    "skew_notes",
    "table_age",
]

#: Extractor class -> what it can tap. A well satellite needs a Well Extractor AND
#: a Pressurizer on its parent core, so it is not interchangeable with a plain node.
EXTRACTOR_FOR_KIND = {
    "node": ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C", "Build_OilPump_C"),
    "well_sat": ("Build_FrackingExtractor_C",),
    "geyser": (),
}

#: What a node needs BESIDES an extractor, and cannot work without. The Pressurizer
#: produces nothing itself, so it never appears in EXTRACTOR_FOR_KIND -- but with no
#: Pressurizer on the core every satellite of that well yields exactly zero.
EXTRA_FOR_KIND = {"well_sat": ("Build_FrackingSmasher_C",)}

#: A geyser is not extracted at all; it is a placement target for this generator.
GEYSER_CONSUMER = "Build_GeneratorGeoThermal_C"


@dataclass
class NodeTable:
    nodes: list[dict]
    meta: dict

    def __len__(self) -> int:
        return len(self.nodes)

    def by_resource(self, resource: str) -> list[dict]:
        return [n for n in self.nodes if n["resource"] == resource]

    def by_instance(self) -> dict[str, dict]:
        return {n["instance"]: n for n in self.nodes}

    def filter(
        self,
        resource: str | None = None,
        kind: str | None = None,
        purity: str | None = None,
        direction: str | None = None,
        origin: tuple[float, float] | None = None,
        half_angle: float = 60.0,
        center: tuple[float, float] | None = None,
        radius_m: float | None = None,
    ) -> list[dict]:
        out = self.nodes
        if resource:
            out = [n for n in out if n["resource"] == resource]
        if kind:
            out = [n for n in out if n["kind"] == kind]
        if purity:
            out = [n for n in out if n["purity"] == purity]
        if direction:
            out = [
                n for n in out if geo.in_direction(n["x"], n["y"], direction, origin, half_angle)
            ]
        if center is not None and radius_m is not None:
            out = [n for n in out if geo.distance_m((n["x"], n["y"]), center) <= radius_m]
        return list(out)


#: The loaded table, keyed by the file and its mtime: ``tools/gen_resource_nodes.py``
#: regenerates the artifact on the reader's own machine, and keying on the mtime is what
#: picks the new one up without a restart. One entry only -- the previous table is dead the
#: moment a newer one is read.
_TABLE: dict[tuple[str, int], NodeTable] = {}


def load_nodes() -> NodeTable:
    path = config.data_dir() / "resource_nodes.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_resource_nodes.py")
    key = (str(path), path.stat().st_mtime_ns)
    hit = _TABLE.get(key)
    if hit is not None:
        return hit
    payload = json.loads(path.read_text(encoding="utf-8"))
    table = NodeTable(nodes=payload["nodes"], meta=payload.get("_meta", {}))
    _TABLE.clear()
    _TABLE[key] = table
    return table


# --------------------------------------------------------------- game-version skew
#
# Resource nodes MOVE when the map changes in a game update, and one has already been
# renamed, so a stale table is a recurring condition rather than an anomaly. It breaks two
# things silently: a join by instance name finds nothing after a rename, so the save's
# mResourcesLeft is unreadable while per-kind count checks still pass; and a position can be
# wrong by up to a metre, so "the node nearest to X" answers confidently and wrongly.
#
# The artifact measures both per row under ``_meta.cross_validation.positions``. Every number
# is read out of there and none is restated here, because a refresh changes all of them.


def _short(instance: str) -> str:
    return str(instance).rsplit(".", 1)[-1]


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
        wanted = {_short(i) for i in instances}
        moved = {k: v for k, v in self.moved_cm.items() if _short(k) in wanted}
        unjoinable = tuple(
            k
            for k in self.unjoinable
            if _short(k) in wanted or _short(self.renamed_to.get(k, "\0")) in wanted
        )
        return replace(
            self,
            moved_cm=moved,
            dz_cm={k: v for k, v in self.dz_cm.items() if k in moved},
            unjoinable=unjoinable,
            renamed_to={k: v for k, v in self.renamed_to.items() if k in unjoinable},
            renamed_moved_cm=self.renamed_moved_cm if unjoinable else None,
        )


def _pin_and_drift(positions: dict) -> tuple[dict | None, dict | None]:
    """Which comparison the table MATCHES, and which one it lags.

    Chosen by what each block measured and never by its key: a block whose worst delta is
    inside its own rounding floor and which finds no row missing on either side is the build
    this table IS. Keying off block names would make this stop reporting the day the
    generator renames one.
    """
    pin: dict | None = None
    drift: dict | None = None
    worst_seen = -1.0
    for block in positions.values():
        if not isinstance(block, dict):
            continue
        floor = float(block.get("rounding_floor_cm") or 0.0)
        worst = float(block.get("max_position_delta_cm") or 0.0)
        only_in = [k for k in block if k.startswith("rows_only_in")]
        if worst > floor or any(block.get(k) for k in only_in):
            if worst > worst_seen:
                worst_seen, drift = worst, block
        elif pin is None:
            pin = block
    return pin, drift


def skew_from_meta(meta: dict, header: dict | None) -> TableSkew | None:
    """The recorded skew, but only when ``header`` names a build past the table's pin.

    Returns ``None`` when there is nothing to say: the artifact records no drift (which
    is what a refresh produces), the save is on the pinned build or older, or the save
    names no version at all. Silence is the default, so a matching build costs nothing.
    """
    positions = ((meta.get("cross_validation") or {}).get("positions")) or {}
    pin_block, drift = _pin_and_drift(positions)
    if drift is None:
        return None

    pin = _versions_in((pin_block or {}).get("build"))
    against = _versions_in(drift.get("build"))
    save = {
        field: header[field]
        for _marker, field in _VERSION_MARKERS
        if isinstance((header or {}).get(field), int)
    }
    if not save:
        return None

    newer = any(save[f] > v for f, v in pin.items() if f in save)
    older = any(save[f] < v for f, v in pin.items() if f in save)
    # A save on exactly the build the drift was measured against is affected by all of
    # it, whether or not the pin block happens to state a comparable marker.
    at_drift_build = bool(against) and any(save.get(f) == v for f, v in against.items())
    if older and not newer:
        return None
    if not (newer or at_drift_build):
        return None

    rows = [r for r in (drift.get("rows_past_the_rounding_floor") or ()) if r.get("instance")]
    floor = float(drift.get("rounding_floor_cm") or 0.0)
    moved_cm = {r["instance"]: float(r["delta_cm"]) for r in rows if r.get("delta_cm") is not None}
    dz_cm = {r["instance"]: float(r["dz_cm"]) for r in rows if r.get("dz_cm") is not None}

    unjoinable = tuple(drift.get("rows_only_in_this_table") or ())
    only_in_build = [
        name
        for key in drift
        if key.startswith("rows_only_in") and key != "rows_only_in_this_table"
        for name in (drift.get(key) or ())
    ]
    # Two lists of names are not a mapping. With one name on each side and a recorded
    # distance between them the pairing is forced; with more it would be invented, so
    # extra rows are reported as unjoinable without naming a replacement.
    renamed_moved_cm = drift.get("renamed_row_moved_cm")
    renamed_to: dict[str, str] = {}
    if len(unjoinable) == 1 and len(only_in_build) == 1 and renamed_moved_cm is not None:
        renamed_to = {unjoinable[0]: only_in_build[0]}

    skew = TableSkew(
        pin=pin,
        measured_against=against,
        save=save,
        moved_cm=moved_cm,
        dz_cm=dz_cm,
        unjoinable=unjoinable,
        renamed_to=renamed_to,
        renamed_moved_cm=float(renamed_moved_cm) if renamed_moved_cm is not None else None,
        # Only claim "the x,y still lands on the right node" when the recorded deltas
        # actually say so: a delta the row's own dz cannot account for is horizontal.
        vertical_only=bool(rows)
        and all(
            abs(dz_cm.get(r["instance"], 0.0)) >= moved_cm[r["instance"]] - floor for r in rows
        ),
        resource_and_purity_verified=drift.get("purity_mismatches") == []
        and drift.get("resource_mismatches") == [],
    )
    return skew or None


def skew_for_save(header: dict | None, table: NodeTable | None = None) -> TableSkew | None:
    """``skew_from_meta`` against the shipped table. ``None`` when there is nothing to say."""
    return skew_from_meta((table if table is not None else load_nodes()).meta, header)


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
    s = skew.scope(instances)
    if not s.moved_cm:
        return []
    ranked = sorted(s.moved_cm.items(), key=lambda kv: -kv[1])
    shown = ranked[:name_limit]
    # A direction is only quoted where the artifact recorded one. Deriving a sign from the
    # magnitude would be an invented number, and "the node is 80 cm LOWER than shown" is
    # the half of this a planner acts on.
    signed = all(i in s.dz_cm for i, _ in shown)
    named = ", ".join(
        f"{_short(i)} {s.dz_cm[i]:+.0f}cm" if i in s.dz_cm else f"{_short(i)} {d:.0f}cm"
        for i, d in shown
    )
    if len(ranked) > name_limit:
        named += f", +{len(ranked) - name_limit} more"
    axis = "z" if s.vertical_only else "position"
    convention = (
        "; a negative sign means the game sits that much lower than the z shown"
        if signed and s.vertical_only
        else ""
    )
    unaffected = (
        " x,y, resource and purity still match the newer build."
        if s.vertical_only and s.resource_and_purity_verified
        else ""
    )
    note = (
        f"{len(ranked)} node(s) here moved in a game update after this node table was cut "
        f"({s.gap}): {axis} is up to {s.max_moved_cm:.0f}cm stale -- {named}{convention}."
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
    out = []
    for inst in skew.scope(instances).unjoinable:
        new = skew.renamed_to.get(inst)
        moved = skew.renamed_moved_cm
        if new:
            how = f"renamed it to {_short(new)}"
            if moved:
                how += f", {moved:.0f}cm away"
        else:
            how = "dropped that name"
        out.append(
            f"{_short(inst)} is not in this save under that name: a game update after this "
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


def drifted(skew: TableSkew | None, instances: Iterable[str] | None = None) -> set[str]:
    """Leaf names of the rows in ``instances`` whose position or name the newer build moved."""
    if skew is None:
        return set()
    s = skew.scope(instances)
    return {_short(i) for i in s.moved_cm} | {_short(i) for i in s.unjoinable}


def table_age(
    header: dict | None, table: NodeTable | None = None, instances: Iterable[str] | None = None
) -> dict | None:
    """The node table's age against this save, scoped to ``instances``; ``None`` when current."""
    skew = skew_for_save(header, table)
    if skew is None:
        return None
    s = skew.scope(instances)
    return {
        "table": "nodes",
        "behind": True,
        "gap": skew.gap,
        "moved": len(s.moved_cm),
        "unjoinable": len(s.unjoinable),
        "observed_from": None,
        "observed_matches": None,
        "notes": skew_notes(skew, instances),
    }


def node_rate(node: dict, game: GameData, extractor_cls: str | None = None) -> float:
    """Extraction rate for one node at 100% clock, in items/min or m3/min.

    Uses the best extractor the player could place unless one is named. Geysers have
    no extractor -- they are consumed by a Geothermal Generator instead.
    """
    kind = node["kind"]
    if kind == "geyser" or game is None:
        return 0.0
    candidates = (extractor_cls,) if extractor_cls else EXTRACTOR_FOR_KIND.get(kind, ())
    best = 0.0
    for cls in candidates:
        b = game.buildings.get(cls)
        if b is None or not b.base_extract_rate:
            continue
        # FORM first, and never mAllowedResources alone: that field is only populated when
        # mOnlyAllowCertainResources is True, which is False on every miner, so filtering on
        # it leaves miners unrestricted and a Miner Mk.3 (base 240) out-bids the Oil
        # Extractor (base 120) on crude -- every oil node at double its real rate.
        # mAllowedResourceForms is what encodes it: RF_SOLID on miners, RF_LIQUID on pumps.
        item = game.items.get(node["resource"])
        if b.allowed_forms and item is not None and item.form not in b.allowed_forms:
            continue
        if b.allowed_resources and node["resource"] not in b.allowed_resources:
            continue
        best = max(best, b.extract_rate(node["purity"]))
    return best


def occupancy(projection: dict) -> dict[str, dict]:
    """node instanceName -> the extractor sitting on it.

    Resolution is PARTIAL: water pumps point at FGWaterVolume objects, which are not node
    keys, and some miners carry no mExtractableResource property at all -- only 40 of the
    reference save's 66 extractors resolve. An absent link is UNKNOWN, never free.
    """
    out: dict[str, dict] = {}
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if not node:
            continue
        out[node] = {
            "extractor": e["cls"],
            "instance": e.get("instance"),
            "clock": e.get("clock", 1.0),
            "paused": e.get("paused", False),
            "pos": e.get("pos"),
        }
    return out


def unresolved_extractors(projection: dict) -> list[dict]:
    """Extractors whose node could not be resolved, so capacity is uncertain."""
    table = load_nodes().by_instance()
    # A node the newer build renamed lands here too, and "target not a node" is the wrong
    # diagnosis: the target IS a node, it is this table that is behind.
    skew = skew_for_save(projection.get("header"))
    was = {_short(new): old for old, new in (skew.renamed_to.items() if skew else ())}
    out = []
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if node is None:
            out.append({**e, "reason": "no mExtractableResource property"})
        elif node not in table:
            leaf = _short(node)
            old = was.get(leaf)
            reason = (
                f"node renamed by a game update after this table was cut "
                f"(this table calls it {_short(old)})"
                if old
                else f"target not a node ({leaf})"
            )
            out.append({**e, "reason": reason})
    return out


def reachable(node: dict, unlocked_buildings: set[str] | None) -> bool:
    """Whether the player can actually exploit this node yet.

    Without it a node table counts resource-well satellites needing a Pressurizer the player
    has not unlocked, which overstates crude on the reference save by 1,080 of 5,040 m3/min.
    """
    if unlocked_buildings is None:
        return True
    kind = node["kind"]
    if kind == "geyser":
        return GEYSER_CONSUMER in unlocked_buildings
    if not set(EXTRA_FOR_KIND.get(kind, ())) <= unlocked_buildings:
        return False
    return any(cls in unlocked_buildings for cls in EXTRACTOR_FOR_KIND.get(kind, ()))


def _can_tap(building, resource: str, game: GameData) -> bool:
    """Whether this extractor could tap this resource, unlocks aside.

    Mirrors the rule build_scenario applies when it turns nodes into extractor
    columns, and must keep mirroring it: a building with no `mAllowedResourceForms`
    of its own is a solid miner, so a fluid node is not its to take.
    """
    if building is None or not building.base_extract_rate:
        return False
    if building.allowed_resources:
        return resource in building.allowed_resources
    item = game.items.get(resource)
    return item is not None and not item.is_fluid


def blocking_buildings(
    node: dict, game: GameData, unlocked_buildings: set[str] | None
) -> tuple[str, ...]:
    """Building classes that must be unlocked before this node yields anything.

    Empty when nothing is in the way. Sharper than `reachable`, which asks only whether an
    extractor of the right KIND is unlocked: an unlocked Miner Mk2 makes a crude oil node
    read as reachable while nothing on the map can pump it.
    """
    if unlocked_buildings is None:
        return ()
    kind = node["kind"]
    if kind == "geyser":
        return () if GEYSER_CONSUMER in unlocked_buildings else (GEYSER_CONSUMER,)
    options = tuple(
        cls
        for cls in EXTRACTOR_FOR_KIND.get(kind, ())
        if _can_tap(game.buildings.get(cls), node["resource"], game)
    )
    missing = () if any(cls in unlocked_buildings for cls in options) else options
    extra = tuple(c for c in EXTRA_FOR_KIND.get(kind, ()) if c not in unlocked_buildings)
    return missing + extra


def annotate(
    nodes: list[dict],
    game: GameData,
    projection: dict | None = None,
    unlocked_buildings: set[str] | None = None,
) -> list[dict]:
    """Attach rate, grid cell, occupancy and reachability to node rows.

    The whole occupancy record travels, not a boolean: which extractor stands there, at
    what clock, whether it is switched off and where it is are what "is this node worth
    reclaiming" is answered from, and ``occupancy`` computes all of it anyway.
    """
    occ = occupancy(projection) if projection else {}
    out = []
    for n in nodes:
        taken = occ.get(n["instance"]) or {}
        out.append(
            {
                **n,
                "rate": node_rate(n, game),
                "grid": geo.grid_cell(n["x"], n["y"]),
                "tapped": bool(taken),
                "tapped_by": taken.get("extractor"),
                "tapped_clock": taken.get("clock"),
                "tapped_paused": taken.get("paused"),
                "tapped_instance": taken.get("instance"),
                "tapped_pos": taken.get("pos"),
                "reachable": reachable(n, unlocked_buildings),
            }
        )
    return out


def capacity(rows: list[dict], only_free: bool = False, only_reachable: bool = True) -> float:
    """Total rate across rows.

    Defaults to reachable nodes only, because unreachable capacity is not a plan.
    """
    total = 0.0
    for r in rows:
        if only_free and r.get("tapped"):
            continue
        if only_reachable and not r.get("reachable", True):
            continue
        total += r["rate"]
    return total

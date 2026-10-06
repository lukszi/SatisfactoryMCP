"""Reading the map: every actor the cooked world packages place, and the two ``_meta`` blocks
about the read itself -- ``source.placements`` and the pickup ``class_census``."""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field

from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.levels import (
    LEVEL_SUFFIX,
    WORLD_LEVEL_DIR,
    level_paths,
    walk_levels,
)
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from tools.collectibles.catalog import (
    CATEGORIES,
    DROP_POD_CLASS,
    GAS_PILLAR,
    HAZARD_CLASSES,
    LOOT_CACHE_CLASS,
    MUSHROOM_CLASS,
    PLACEMENT_ID_MARK,
    ActorKey,
    Position,
)
from tools.collectibles.hazards import Hazard, read_hazard
from tools.collectibles.rows import PickupContents, UnlockCost
from tools.collectibles.stats import by_count, json_array

#: The classes whose rows carry ``contents``.
_PICKUPS = (LOOT_CACHE_CLASS, MUSHROOM_CLASS)


@dataclass
class Placement:
    """One map-placed collectible: what it is, where it is, and what it hangs off."""

    instance: str
    cell: str
    cls: str
    class_path: str
    position: Position
    attached_to: str | None
    #: Class-specific facts read off this very actor: a cache's contents, a pod's cost.
    contents: PickupContents | None = None
    unlock_cost: UnlockCost | None = None


@dataclass
class MapWorld:
    placements: list[Placement]
    hazards: list[Hazard]
    #: class name -> how many the map places, over EVERY actor class in the world level.
    class_counts: collections.Counter[str]
    #: The same, restricted to blueprint (``/Game/``) classes: what a prefix walk can see.
    game_class_counts: collections.Counter[str]
    #: (cell, instance) -> class, over every map-placed actor and not only the emitted ones.
    class_by_key: dict[ActorKey, str]
    #: Instance names carrying the map's own _UAID_ placement id, and how many are distinct.
    placement_id_names: int
    placement_id_names_distinct: int
    #: class -> how many of its actors reuse an instance name another actor already has.
    name_repeats_by_class: collections.Counter[str]
    actor_count: int
    distinct_keys: int
    distinct_names: int
    names_with_two_classes: int
    packages_read: int
    packages_without_a_level: int
    #: Exception type -> how many world packages raised it and were skipped whole.
    packages_failed: collections.Counter[str]
    #: Why an actor of a row or hazard class got no position, by reason and class.
    actors_without_transform: collections.Counter[str]
    unresolved_script_classes: int
    seconds: float


@dataclass
class _ActorCensus:
    """The class histogram and the identity counts, over every map-placed actor."""

    class_counts: collections.Counter[str] = field(default_factory=collections.Counter[str])
    game_class_counts: collections.Counter[str] = field(default_factory=collections.Counter[str])
    class_by_key: dict[ActorKey, str] = field(default_factory=dict[ActorKey, str])
    classes_by_name: dict[str, set[str]] = field(
        default_factory=lambda: collections.defaultdict[str, set[str]](set)
    )
    name_repeats: collections.Counter[str] = field(default_factory=collections.Counter[str])
    placement_id_names: list[str] = field(default_factory=list[str])
    unresolved_scripts: int = 0

    def count(self, cell: str, instance: str, cls: str, class_path: str | None) -> None:
        self.class_counts[cls] += 1
        if class_path and class_path.startswith("/Game/"):
            self.game_class_counts[cls] += 1
        elif class_path and "<unresolved:" in class_path:
            self.unresolved_scripts += 1
        self.class_by_key[(cell, instance)] = cls
        if instance in self.classes_by_name:
            self.name_repeats[cls] += 1
        self.classes_by_name[instance].add(cls)
        if PLACEMENT_ID_MARK in instance:
            self.placement_id_names.append(instance)


def _int_or_none(value: JsonValue) -> int | None:
    """An ``IntProperty`` as ``decode_struct`` gives it: an int, or None where unreadable."""
    return value if isinstance(value, int) else None


def _pickup_contents(view: PackageView, actor: int) -> PickupContents | None:
    """``mPickupItems`` -> what the cache holds. A pickup whose contents will not read is still
    a row, counted as ``placed`` minus ``contents_read`` in its category's tally.

    ``FInventoryItem`` has no tagged members: its payload starts with an ``int32 ItemClass``
    that is an ``FPackageIndex`` into the import map, so the item class is read through it.
    """
    payload = view.props(actor).get("mPickupItems")
    if payload is None:
        return None
    fields: JsonObject = view.decode_struct(payload)
    item = fields.get("Item")
    raw_hex = item.get("_raw") if isinstance(item, dict) else None
    path = None
    if isinstance(raw_hex, str):
        raw = bytes.fromhex(raw_hex)
        if len(raw) >= 4:
            path = view.import_path(raw[0:4])
    count = _int_or_none(fields.get("NumItems"))
    if path is None and count is None:
        return None
    return {
        "item": class_name_of(path) if path else None,
        "item_path": path,
        "count": count,
    }


def _pod_unlock_cost(view: PackageView, actor: int) -> UnlockCost | None:
    """``mUnlockCost`` -> ``{cost_type, item, amount, power_mw}``.

    ``cost_type`` is null where the pod does not serialise it: the class default is a third
    value whose name is nowhere in the cooked assets.
    """
    payload = view.props(actor).get("mUnlockCost")
    if payload is None:
        return None
    fields: JsonObject = view.decode_struct(payload)
    item_cost = fields.get("ItemCost")
    cost_type = fields.get("CostType")
    out: UnlockCost = {"cost_type": cost_type if isinstance(cost_type, str) else None}
    if isinstance(item_cost, dict) and "_raw" not in item_cost:
        path = item_cost.get("ItemClass")
        out["item"] = class_name_of(path) if isinstance(path, str) else None
        out["amount"] = _int_or_none(item_cost.get("Amount"))
    power = fields.get("PowerConsumption")
    if isinstance(power, int | float) and power:
        out["power_mw"] = round(power, 3)
    return out


def read_map(store: IoStore, scripts: ScriptObjects, progress: bool = True) -> MapWorld:
    """Every actor the cooked world packages place, with a transform where one is needed.

    Transforms are composed only for the emitted and the hazard classes: the export table
    alone settles the class histogram, and property parsing is most of the cost.
    """
    index = AssetIndex(store)
    classes = ClassFacts(store, index)
    census = _ActorCensus()
    placements: list[Placement] = []
    hazards: list[Hazard] = []
    packages_failed: collections.Counter[str] = collections.Counter()
    without_transform: collections.Counter[str] = collections.Counter()
    no_level = 0
    started = time.time()

    def unreadable(_path: str, exc: Exception) -> None:
        # One bad package must not lose the rest; the exception type says which kind it is.
        packages_failed[type(exc).__name__] += 1

    packages = level_paths(store, contains=WORLD_LEVEL_DIR)
    for number, total, path, view in walk_levels(
        store, scripts, paths=packages, on_unreadable=unreadable
    ):
        cell = path.rsplit("/", 1)[-1].removesuffix(LEVEL_SUFFIX)
        if not view.level_slots:
            no_level += 1
            continue
        for export in view.exports:
            slot = export["slot"]
            if view.outer_of.get(slot) not in view.level_slots:
                continue
            class_path = view.class_of.get(slot)
            cls = class_name_of(class_path)
            instance = export["name"]
            census.count(cell, instance, cls, class_path)

            is_row = cls in CATEGORIES
            is_hazard = cls in HAZARD_CLASSES or bool(GAS_PILLAR.match(cls))
            if not (is_row or is_hazard):
                continue
            root = root_component(view, slot)
            if root is None:
                without_transform[f"no root component: {cls}"] += 1
                continue
            transform, parent = world_transform(view, root, classes)
            if transform is None:
                without_transform[f"unresolvable attach chain: {cls}"] += 1
                continue
            if is_row:
                placements.append(
                    Placement(
                        instance=instance,
                        cell=cell,
                        cls=cls,
                        class_path=f"{class_path}.{cls}"
                        if class_path and class_path.startswith("/Game/")
                        else str(class_path),
                        position=transform[0],
                        attached_to=view.exports[parent]["name"] if parent is not None else None,
                        contents=_pickup_contents(view, slot) if cls in _PICKUPS else None,
                        unlock_cost=_pod_unlock_cost(view, slot) if cls == DROP_POD_CLASS else None,
                    )
                )
            if is_hazard:
                hazard = read_hazard(view, slot, cls, transform[0], classes)
                if hazard is not None:
                    hazards.append(hazard)
        if progress and number % 1000 == 0:
            print(
                f"  {number:>5}/{total} packages  {time.time() - started:>5.1f}s"
                f"  {len(placements)} collectibles  {len(hazards)} hazard actors",
                flush=True,
            )

    return MapWorld(
        placements=placements,
        hazards=hazards,
        class_counts=census.class_counts,
        game_class_counts=census.game_class_counts,
        class_by_key=census.class_by_key,
        placement_id_names=len(census.placement_id_names),
        placement_id_names_distinct=len(set(census.placement_id_names)),
        name_repeats_by_class=census.name_repeats,
        actor_count=sum(census.class_counts.values()),
        distinct_keys=len(census.class_by_key),
        distinct_names=len(census.classes_by_name),
        names_with_two_classes=sum(1 for v in census.classes_by_name.values() if len(v) > 1),
        packages_read=len(packages),
        packages_without_a_level=no_level,
        packages_failed=packages_failed,
        actors_without_transform=without_transform,
        unresolved_script_classes=census.unresolved_scripts,
        seconds=time.time() - started,
    )


def read_other_levels(store: IoStore, scripts: ScriptObjects) -> list[JsonObject]:
    """Every ``.umap`` outside the world level, walked with the same actor rule.

    A collectible placed by another level would otherwise be missing with nothing to show,
    so each one reports how many actors of an emitted class it places.
    """
    out: list[JsonObject] = []
    for path in sorted(
        p for p in store.by_path if p.endswith(LEVEL_SUFFIX) and WORLD_LEVEL_DIR not in p
    ):
        entry: JsonObject = {"package": path.rsplit("/", 1)[-1]}
        try:
            view = PackageView(store.read_path(path), scripts)
            actors = [
                e["slot"] for e in view.exports if view.outer_of.get(e["slot"]) in view.level_slots
            ]
            counts = collections.Counter(class_name_of(view.class_of[slot]) for slot in actors)
        except Exception as exc:
            # A side level that will not parse is a fact worth reporting, not a crash.
            entry["read"] = f"failed: {type(exc).__name__}"
            out.append(entry)
            continue
        entry["actors"] = len(actors)
        entry["actor_classes"] = len(counts)
        entry["actors_of_an_emitted_class"] = {
            cls: n for cls, n in sorted(counts.items()) if cls in CATEGORIES
        }
        entry["largest_classes"] = {cls: n for cls, n in counts.most_common(5)}
        out.append(entry)
    return out


def class_census_meta(world: MapWorld) -> JsonObject:
    """``_meta.class_census``: the narrow regression guard for a pickup class gone missing."""
    census: JsonObject = {
        cls: {
            "placed_by_the_map": count,
            "native_class": cls not in world.game_class_counts,
            "emitted_as": CATEGORIES.get(cls),
        }
        for cls, count in sorted(world.class_counts.items())
        if "Pickup" in cls or "pickup" in cls
    }
    return {
        "what": (
            "a narrow regression guard, and it is worth being exact about how narrow. "
            "The bug it exists for was a missing CLASS rather than a missing row: a "
            "native class cannot appear in a /Game/ walk, so every loot cache was absent "
            "with nothing in the file to show it. What this section catches is that "
            "specific shape of regression -- a class whose NAME CONTAINS 'pickup' that "
            "stops being emitted, or a new one the map gains. emitted_as null on one of "
            "them is either a deliberate exclusion or a bug."
        ),
        "net": (
            "a substring test on the class name, which most collectible classes fail: "
            "BP_Crystal_C, BP_WAT1_C, BP_WAT2_C and BP_Shroom_01_C are all rows in this "
            "file and none of them would ever appear below. This is NOT a list of the "
            "world's collectible classes and it is not a completeness proof for the row "
            "set. The wider guard is accounting, which forces every class the map places "
            "into a category, excluded or not_classified and checks the three sum to the "
            "map's own actor count; other_levels_in_the_container is the guard for "
            "collectibles placed outside the level this file reads."
        ),
        "classes": census,
    }


def placements_source_meta(
    world: MapWorld,
    store: IoStore,
    scripts: ScriptObjects,
    *,
    game_build: str | None,
    pyooz_version: str,
    other_levels: list[JsonObject],
) -> JsonObject:
    """``_meta.source.placements``: the container, the walk and the decompressor."""
    return {
        "kind": "the game's own cooked map assets, read from the installed game",
        "container": (f"{store.cas_path.parent.name}/{store.cas_path.stem}.utoc + .ucas"),
        "container_utoc_version": store.version,
        "container_flags": hex(store.flags),
        "container_flags_note": (
            "0x0d is Compressed|Signed|Indexed. The Encrypted bit is unset and the "
            "encryption key GUID is null: signed is not encrypted, and no DRM is "
            "circumvented by reading it."
        ),
        "container_bytes": [store.toc_bytes, store.cas_bytes],
        "container_modified_utc": store.toc_mtime.isoformat(timespec="seconds"),
        "game_build": game_build,
        "packages_read": world.packages_read,
        "packages_with_no_level_export": world.packages_without_a_level,
        "packages_that_failed_to_parse": by_count(world.packages_failed),
        "packages_that_failed_to_parse_note": (
            "world packages the reader refused, by exception type, each skipped whole. "
            "Their actors are in neither the rows nor the class histogram, so accounting "
            "cannot see them; empty means every package listed in packages_read was read."
        ),
        "map_actors_placed": world.actor_count,
        "actor_classes_placed": len(world.class_counts),
        "level_read": WORLD_LEVEL_DIR,
        "other_levels_in_the_container": json_array(other_levels),
        "other_levels_note": (
            "that the level above is the whole world was once a sentence in the "
            "source; it is this list instead. Every other .umap the container holds "
            "is walked with the identical actor rule and reported with its actor "
            "count and, the point of it, how many actors of a class this file emits "
            "it places -- actors_of_an_emitted_class. All of them empty is what "
            "makes 'the row set is the map's placement list' a statement about the "
            "game rather than about one directory. A non-empty one would mean rows "
            "are missing. Note what these levels ARE: a HUB audio sub-level, the "
            "dedicated-server entry, the two main-menu backdrops and a developer "
            "test map -- the test map does place resource nodes, which is why the "
            "check is run against emitted classes rather than eyeballed."
        ),
        "actors_read_but_given_no_transform": by_count(world.actors_without_transform),
        "actors_read_but_given_no_transform_note": (
            "an actor of a class this file reads whose root component or attach chain "
            "could not be resolved, so it has no position and is NOT a row. Empty "
            "means every one was placed. This is one of the two ways a collectible could "
            "go missing without the accounting noticing, since the class histogram counts "
            "it either way -- hence a number here rather than nothing. "
            "packages_that_failed_to_parse is the other."
        ),
        "actor_rule": (
            "an export whose Outer is the package's /Script/Engine.Level export. A "
            "class-path prefix test cannot be used: a native class is a 62-bit hash "
            "rather than a path, so a /Game/ test silently drops every one of them."
        ),
        "script_objects": {
            "source": "global.utoc chunk type 5 (ScriptObjects)",
            "chunk_bytes": scripts.chunk_bytes,
            "objects": scripts.object_count,
            "script_packages": scripts.package_count,
            "unresolved_class_hashes_in_the_map": world.unresolved_script_classes,
            "role": (
                "turns a script-import hash back into /Script/FactoryGame.<Class>. "
                "With 0 unresolved hashes above, every actor class in the map has a "
                "name."
            ),
        },
        "seconds": round(world.seconds, 1),
        "decompressor": {
            "name": "pyooz",
            "version": pyooz_version,
            "import_name": "ooz",
            "licence": "GPL-3.0",
            "role": (
                "Oodle block decompression, offline, at generation time only. An "
                "OPTIONAL dependency: the `gen` extra in pyproject.toml, pinned "
                "exactly because it decides these bytes, and asked for on the "
                "command line -- `uv run --extra gen python "
                "tools/gen_world_collectibles.py`. It is imported at module scope "
                "nowhere, and lazily inside one function of "
                "satisfactory_mcp.core.gameassets.iostore, so the server and the "
                "test suite run with it absent. No part of it is present in this "
                "file."
            ),
        },
        "licence": (
            "the coordinates are facts about Coffee Stain's map, read out of the "
            "installed game. No third-party world table contributed to this file."
        ),
    }

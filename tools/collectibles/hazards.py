"""Hazard context: the map actors read for context rather than as rows, the geometry between
them and a collectible, and the ``_meta.hazard_context`` block that describes both."""

from __future__ import annotations

import collections
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Generic, Literal, NamedTuple, TypeVar

from satisfactory_mcp.core.collectible_rows import HazardContext, MapPlacement
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    class_name_of,
    read_float,
    read_vector_array,
)
from satisfactory_mcp.core.jsontypes import JsonObject
from tools.collectibles.catalog import GAS_PILLAR, HAZARD_RADIUS_CM, NUCLEAR_HOG, Position
from tools.collectibles.stats import by_count, json_object, spread

#: What ``read_hazard`` makes of a map actor.
HazardKind = Literal[
    "creature_spawner", "hatcher", "spore_flower", "gas_field", "resource", "damage_volume"
]

#: What ``hazard_context`` tests a source as.
SourceKind = Literal["hostile", "spore_flower", "gas_field", "radioactive"]

#: Whatever a ``SpatialIndex`` files under a position.
Item = TypeVar("Item")


@dataclass
class Hazard:
    """One map actor read for context rather than as a row."""

    kind: HazardKind
    cls: str
    position: Position
    #: What it is, in game terms: a creature descriptor, or a resource class.
    label: str | None = None
    #: How many creatures the spawner holds, where it says.
    count: int | None = None
    #: A radius the actor or its class declares, in cm. None where none is declared.
    radius: float | None = None
    #: How far a gas volume's own pillar list reaches. Sizes the reporting horizon; it is
    #: not a containment radius.
    span: float | None = None


class HazardSource(NamedTuple):
    """One indexed source, as ``hazard_context`` tests it against a collectible."""

    kind: SourceKind
    label: str
    creatures: int
    radius: float | None


def read_hazard(
    view: PackageView, slot: int, cls: str, position: Position, classes: ClassFacts
) -> Hazard | None:
    """One hazard actor's kind, what it holds and the radius its own class declares."""
    props = view.props(slot)
    class_package = view.class_of.get(slot) or ""
    if cls == "BP_CreatureSpawner_C":
        creature = view.import_path(props.get("mCreatureClass", b""))
        spawn = props.get("mSpawnData")
        radius = read_float(props.get("mSpawnRadius", b"")) or read_float(
            classes.defaults(class_package).get("mSpawnRadius", b"")
        )
        return Hazard(
            kind="creature_spawner",
            cls=cls,
            position=position,
            label=creature,
            count=struct.unpack_from("<I", spawn, 0)[0] if spawn and len(spawn) >= 4 else None,
            radius=radius,
        )
    if cls in ("Char_CrabHatcher_C", "Char_BigCrabHatcher_C"):
        return Hazard(
            kind="hatcher",
            cls=cls,
            position=position,
            label=class_package,
            count=1,
            radius=read_float(classes.defaults(class_package).get("mDetectionRadius", b"")),
        )
    if cls == "BP_SporeFlower_C":
        return Hazard(
            kind="spore_flower",
            cls=cls,
            position=position,
            radius=classes.component_float(class_package, "DamageSphere", "SphereRadius"),
        )
    if cls == "BP_VolumeGas_01_C":
        # The only statement the assets make about how far a gas field reaches.
        pillars = read_vector_array(props.get("mProximityPillarWorldLocations"))
        return Hazard(
            kind="gas_field",
            cls=cls,
            position=position,
            span=max((math.dist(position, p) for p in pillars), default=None),
        )
    if GAS_PILLAR.match(cls):
        return Hazard(kind="gas_field", cls=cls, position=position)
    if cls in ("BP_ResourceNode_C", "BP_ResourceDeposit_C"):
        payload = props.get("mResourceClass") or props.get("mOverrideResourceClass")
        resource = view.import_path(payload) if payload else None
        return Hazard(kind="resource", cls=cls, position=position, label=resource)
    if cls == "FGDamageOverTimeVolume":
        # Its damage type is ``mDotClass`` on a child component, not on the volume.
        dot = None
        for child in view.children.get(slot, []):
            payload = view.props(child).get("mDotClass")
            if payload is not None:
                dot = view.import_path(payload) or dot
        return Hazard(kind="damage_volume", cls=cls, position=position, label=dot)
    return None


class CreatureCatalog:
    """``Char_*`` class -> the ``Desc_*`` descriptor the game labels it with, and passivity.

    Both are read from the assets: the descriptor mapping inverts every
    ``CreatureDescriptors/Desc_*.mCreatureClass``, and passivity is ``mIsPassiveCreature`` on
    the creature's class default object.
    """

    FOLDER = "/CreatureDescriptors/"

    def __init__(self, store: IoStore, index: AssetIndex, classes: ClassFacts) -> None:
        self.classes = classes
        self.by_char: dict[str, str] = {}
        self.descriptors = 0
        self.unreadable_descriptors = 0
        for path in store.by_path:
            leaf = path.rsplit("/", 1)[-1]
            if self.FOLDER not in path or not path.endswith(".uasset"):
                continue
            if not leaf.startswith("Desc_"):
                continue
            self.descriptors += 1
            try:
                view = PackageView(store.read_path(path))
            except Exception as exc:
                print(f"  WARNING: creature descriptor {leaf}: {type(exc).__name__} {exc}")
                self.unreadable_descriptors += 1
                continue
            for export in view.exports:
                if not export["name"].startswith("Default__"):
                    continue
                creature = view.import_path(view.props(export["slot"]).get("mCreatureClass", b""))
                if creature:
                    self.by_char[creature] = leaf[: -len(".uasset")] + "_C"
                break
        self._passive: dict[str, bool | None] = {}
        self.index = index

    def label(self, creature_package: str | None) -> str:
        if not creature_package:
            return "<unresolved creature>"
        return self.by_char.get(creature_package) or class_name_of(creature_package)

    def is_passive(self, creature_package: str | None) -> bool | None:
        """True, False, or None where the class asset could not be read at all."""
        if not creature_package:
            return None
        if creature_package not in self._passive:
            if self.index.path_for(creature_package) is None:
                self._passive[creature_package] = None
            else:
                flag = self.classes.flag(creature_package, "mIsPassiveCreature")
                # Absent means the class default, which is hostile: only the passive
                # creatures serialise the flag.
                self._passive[creature_package] = bool(flag)
        return self._passive[creature_package]


class Radioactivity:
    """Which resource classes are radioactive, over the ones the ground actually holds.

    ``mRadioactiveDecay`` on an item descriptor is the whole model. Anything radioactive in
    the world is a resource some node or deposit carries, so the set is closed.
    """

    def __init__(self, classes: ClassFacts, index: AssetIndex, resources: set[str]) -> None:
        self.decay: dict[str, float] = {}
        self.checked = 0
        self.unreadable = 0
        for package in sorted(resources):
            if index.path_for(package) is None:
                self.unreadable += 1
                continue
            self.checked += 1
            value = read_float(classes.defaults(package).get("mRadioactiveDecay", b""))
            if value and value > 0:
                self.decay[package] = value

    def is_radioactive(self, package: str | None) -> bool:
        return bool(package) and package in self.decay


class SpatialIndex(Generic[Item]):
    """A uniform grid over points, so a neighbour search is linear rather than N*M.

    ``near`` looks at the 27 cells around a point, so it finds everything within one cell
    edge: the cell must be at least the largest radius any query uses.
    """

    def __init__(self, cell_cm: float) -> None:
        self.cell = cell_cm
        self.buckets: dict[tuple[int, int, int], list[tuple[Position, Item]]] = (
            collections.defaultdict(list)
        )

    def add(self, position: Position, value: Item) -> None:
        self.buckets[self._key(position)].append((position, value))

    def _key(self, position: Position) -> tuple[int, int, int]:
        return (
            int(position[0] // self.cell),
            int(position[1] // self.cell),
            int(position[2] // self.cell),
        )

    def near(self, position: Position) -> Iterator[tuple[float, Item]]:
        """``(distance, value)`` for everything in the 27 cells around ``position``."""
        cx, cy, cz = self._key(position)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other, value in self.buckets.get((cx + dx, cy + dy, cz + dz), ()):
                        yield math.dist(position, other), value


@dataclass
class HazardWorld:
    """The hazard sources, resolved and indexed, plus the counts that describe them."""

    index: SpatialIndex[HazardSource]
    spawn_radius_declared: int
    spawn_radius_missing: int
    hostile_placements: int
    passive_placements: int
    unknown_passivity: int
    species: collections.Counter[str]
    spore_flowers: int
    gas_fields: int
    #: class -> every distinct radius its placements declare. Per class because each source
    #: is tested against its own radius, and one kind can be two classes.
    class_declared_radius_cm: dict[str, JsonObject]
    spawner_radius_cm: JsonObject
    #: What the lookup grid is sized to.
    widest_declared_radius_cm: float
    #: What every FGDamageOverTimeVolume deals damage with: the world boundary, not gas.
    damage_volume_classes: collections.Counter[str]
    #: How far a gas volume's own pillar list reaches; the reporting horizon is sized on it.
    gas_field_span_cm: JsonObject
    uranium_sources: int
    resource_classes_checked: int
    radioactive_classes: dict[str, float]
    deposits_without_a_resource: int


def build_hazards(
    sources: list[Hazard], creatures: CreatureCatalog, decay: Radioactivity
) -> HazardWorld:
    """Resolve every hazard actor into an indexed source, and count what was resolved."""
    widest = max([HAZARD_RADIUS_CM, *(h.radius for h in sources if h.radius)])
    grid = SpatialIndex[HazardSource](widest)
    species: collections.Counter[str] = collections.Counter()
    hostile = passive = unknown = declared = missing = 0
    spore_flowers = gas_fields = uranium = no_resource = 0
    damage_volumes: collections.Counter[str] = collections.Counter()
    radii: dict[str, collections.Counter[float | None]] = collections.defaultdict(
        collections.Counter
    )
    spawner_radii: list[float] = []
    spans: list[float] = []
    for source in sources:
        if source.kind == "creature_spawner":
            state = creatures.is_passive(source.label)
            if state is None:
                unknown += 1
            if state:
                passive += 1
                continue
            hostile += 1
            if source.radius:
                declared += 1
                spawner_radii.append(source.radius)
            else:
                missing += 1
            label = creatures.label(source.label)
            species[label] += 1
            grid.add(
                source.position, HazardSource("hostile", label, source.count or 1, source.radius)
            )
        elif source.kind == "hatcher":
            hostile += 1
            radii[source.cls][source.radius] += 1
            species[source.cls] += 1
            grid.add(source.position, HazardSource("hostile", source.cls, 1, source.radius))
        elif source.kind == "spore_flower":
            spore_flowers += 1
            radii[source.cls][source.radius] += 1
            grid.add(source.position, HazardSource("spore_flower", source.cls, 1, source.radius))
        elif source.kind == "gas_field":
            gas_fields += 1
            if source.span is not None:
                spans.append(source.span)
            grid.add(source.position, HazardSource("gas_field", source.cls, 1, None))
        elif source.kind == "damage_volume":
            damage_volumes[class_name_of(source.label) if source.label else "no mDotClass"] += 1
        elif source.kind == "resource":
            if source.label is None:
                no_resource += 1
            elif decay.is_radioactive(source.label):
                uranium += 1
                resource = class_name_of(source.label)
                grid.add(source.position, HazardSource("radioactive", resource, 1, None))
    return HazardWorld(
        index=grid,
        spawn_radius_declared=declared,
        spawn_radius_missing=missing,
        hostile_placements=hostile,
        passive_placements=passive,
        unknown_passivity=unknown,
        species=species,
        spore_flowers=spore_flowers,
        gas_fields=gas_fields,
        class_declared_radius_cm=_declared_radius_table(radii),
        spawner_radius_cm=spread(spawner_radii),
        widest_declared_radius_cm=widest,
        damage_volume_classes=damage_volumes,
        gas_field_span_cm=spread(spans),
        uranium_sources=uranium,
        resource_classes_checked=decay.checked,
        radioactive_classes={class_name_of(k): round(v, 3) for k, v in decay.decay.items()},
        deposits_without_a_resource=no_resource,
    )


def _declared_radius_table(
    radii: dict[str, collections.Counter[float | None]],
) -> dict[str, JsonObject]:
    return {
        cls: {
            "placements": sum(seen.values()),
            "distinct_radii": len(seen),
            "radius_cm": [None if r is None else round(r, 3) for r in sorted(seen, key=str)],
            "placements_declaring_no_radius": seen.get(None, 0),
        }
        for cls, seen in sorted(radii.items())
    }


def hazard_context(position: Position, hazards: HazardWorld) -> HazardContext:
    """The hazard block for one collectible. Distances are facts; verdicts are not made."""
    hostiles: collections.Counter[str] = collections.Counter()
    spawns_here: set[str] = set()
    nearest_hostile: tuple[float, str] | None = None
    gas: tuple[float, str] | None = None
    inside_spore_flower = False
    nearest_uranium: float | None = None
    nearest_hog: float | None = None

    for distance, source in hazards.index.near(position):
        in_horizon = distance <= HAZARD_RADIUS_CM
        if source.kind == "hostile":
            if in_horizon:
                hostiles[source.label] += source.creatures
                if nearest_hostile is None or distance < nearest_hostile[0]:
                    nearest_hostile = (distance, source.label)
            if source.radius is not None and distance <= source.radius:
                spawns_here.add(source.label)
            if source.label == NUCLEAR_HOG and in_horizon:
                nearest_hog = distance if nearest_hog is None else min(nearest_hog, distance)
        elif source.kind in ("spore_flower", "gas_field"):
            if (
                source.kind == "spore_flower"
                and source.radius is not None
                and distance <= source.radius
            ):
                inside_spore_flower = True
            if in_horizon and (gas is None or distance < gas[0]):
                gas = (distance, source.label)
        elif source.kind == "radioactive" and in_horizon:
            nearest_uranium = (
                distance if nearest_uranium is None else min(nearest_uranium, distance)
            )

    out: HazardContext = {}
    if hostiles:
        out["hostiles_nearby"] = dict(sorted(hostiles.items()))
    if spawns_here:
        out["spawns_here"] = sorted(spawns_here)
    if nearest_hostile is not None:
        out["nearest_hostile_cm"] = round(nearest_hostile[0], 1)
    if gas is not None:
        out["nearest_gas_cm"] = round(gas[0], 1)
        out["nearest_gas_class"] = gas[1]
    if inside_spore_flower:
        out["inside_spore_flower_damage_sphere"] = True
    if nearest_uranium is not None:
        out["nearest_uranium_cm"] = round(nearest_uranium, 1)
    if nearest_hog is not None:
        out["nearest_nuclear_hog_spawner_cm"] = round(nearest_hog, 1)
    return out


def hazard_context_meta(hazards: HazardWorld, rows: list[MapPlacement]) -> JsonObject:
    """``_meta.hazard_context``: what each hazard key means, its sources, and its reach."""
    return {
        **_hazard_key_meanings(),
        "sources": _hazard_sources(hazards),
        "reporting_radius_cm": HAZARD_RADIUS_CM,
        "rows_touched": _rows_touched(rows),
    }


def _hazard_key_meanings() -> dict[str, str]:
    """What each key of a row's ``hazard`` object means, and whether it is fact or geometry."""
    return {
        "what": (
            "derived context, kept in each row's own 'hazard' object so it can never be "
            "mistaken for a placement. Nothing here is a fact about the collectible: it "
            "is geometry between it and other map actors."
        ),
        "hostiles_nearby": (
            "creature descriptor -> how many creatures the spawners within "
            f"reporting_radius_cm ({HAZARD_RADIUS_CM:.0f} cm) of this row hold, from each "
            "spawner's mCreatureClass and the length of its mSpawnData, plus crab "
            "hatchers placed directly. That radius is THIS FILE'S and nothing the game "
            "declares, which is why the row also carries nearest_hostile_cm and lets a "
            "consumer draw its own line."
        ),
        "spawns_here": (
            "the subset whose own declared radius contains this row -- a spawner's "
            "mSpawnRadius, a hatcher's mDetectionRadius. This one IS a map-declared "
            "fact rather than a chosen threshold."
        ),
        "passive_creatures_excluded": (
            "hostile means: mIsPassiveCreature is NOT set on the creature's own class "
            "default object. That is the game's own flag rather than a list kept here, "
            "but the split is definitional and not cross-validated -- nothing else in "
            "the cooked assets was checked against it, so a hostile creature that "
            "happens to carry the flag would be dropped silently. What IS measured is "
            "how often the flag could not be read at all: "
            "creature_classes_whose_passivity_is_unknown, which is the number that would "
            "make the split unsafe."
        ),
        "nearest_gas_cm": (
            "distance to the nearest gas actor of any of the three kinds -- spore "
            f"flower, gas pillar, gas perimeter volume -- within {HAZARD_RADIUS_CM:.0f} cm. "
            "A distance, not a verdict: a gas field's own extent scales a box whose base "
            "size is not in the cooked data, so no radius can honestly be derived for it. "
            "gas_field_own_span_cm under sources is the one thing the field does say "
            "about its own reach, and it is what sizes the reporting horizon."
        ),
        "inside_spore_flower_damage_sphere": (
            "the one gas containment test that IS a fact: BP_SporeFlower_C's class "
            "carries a DamageSphere whose SphereRadius the game itself states. The value "
            "is not repeated here -- see class_declared_radius_cm under sources, which "
            "lists it per class with the number of placements behind it, because each "
            "source is tested against its own radius and a single figure quoted in prose "
            "cannot say that."
        ),
        "nearest_uranium_cm": (
            "distance to the nearest resource node or deposit whose resource class "
            "carries mRadioactiveDecay. Scope, exactly: the resource classes the map's "
            "own nodes and deposits name were read and checked -- "
            "resource_classes_checked and radioactive_resource_classes under sources say "
            "how many and which. It is NOT a scan of every item descriptor in the game, "
            "so this is 'the radiation the map places in the ground', not 'all radiation "
            "in Satisfactory'. Radioactive manufactured parts are outside it by "
            "construction: they do not exist until a player makes them, and no map actor "
            "holds one."
        ),
        "nearest_nuclear_hog_spawner_cm": (
            "a SEPARATE reason, never folded into the uranium one. Char_NuclearHog "
            "carries no mRadioactiveDecay of its own, so this is an empirical correlate "
            "-- the designers put nuclear hogs on uranium -- and not a modelled fact."
        ),
    }


def _hazard_sources(hazards: HazardWorld) -> JsonObject:
    """The counts and declared radii behind the keys, each with what it can and cannot say."""
    return {
        "hostile_placements": hazards.hostile_placements,
        "passive_placements_excluded": hazards.passive_placements,
        "creature_classes_whose_passivity_is_unknown": hazards.unknown_passivity,
        "hostile_species": by_count(hazards.species),
        "hostile_species_note": (
            "placements, not creatures, and two kinds of key: a Desc_* is the "
            "creature descriptor a BP_CreatureSpawner_C names, while a Char_* is a "
            "creature the map places directly with no spawner around it. Both are "
            "hostiles_nearby sources; only the first has an mSpawnData to say how "
            "many creatures one placement holds."
        ),
        "creature_spawners_declaring_their_own_radius": hazards.spawn_radius_declared,
        "creature_spawners_with_no_radius": hazards.spawn_radius_missing,
        "creature_spawner_radius_cm": hazards.spawner_radius_cm,
        "creature_spawner_radius_note": (
            "the two counts above are BP_CreatureSpawner_C only -- the directly "
            "placed Char_* hatchers are in class_declared_radius_cm below and are "
            "not spawners. mSpawnRadius varies per placement, so the spread is here "
            "rather than one number: 'N spawners declare a radius' says nothing "
            "about how far those radii reach, and it is the radius that decides "
            "spawns_here."
        ),
        "spore_flowers": hazards.spore_flowers,
        "class_declared_radius_cm": json_object(hazards.class_declared_radius_cm),
        "class_declared_radius_note": (
            "per class, every distinct radius its placements declare and how many "
            "placements there are. Per class because a single number was wrong in "
            "both directions: the hatchers are TWO classes with different placement "
            "counts, and one figure taken from whichever placement happened to be "
            "read first spoke for both of them -- and it spoke wrongly, because "
            "Char_BigCrabHatcher_C's class declares NO mDetectionRadius at all, so "
            "its 151 placements can never contribute to spawns_here however close a "
            "collectible sits. A radius_cm of [null] means exactly that: the class is "
            "a hostiles_nearby source and not a containment test. distinct_radii > 1 "
            "would mean the class does not have one radius; spawns_here would still "
            "be right, since it tests every source against its own, while any single "
            "number quoted for the class would be wrong."
        ),
        "gas_field_actors": hazards.gas_fields,
        "gas_field_own_span_cm": hazards.gas_field_span_cm,
        "gas_field_own_span_note": (
            "how far the furthest pillar a BP_VolumeGas_01_C names in its own "
            "mProximityPillarWorldLocations sits from the volume, over the volumes "
            f"that populate it. The {HAZARD_RADIUS_CM:.0f} cm reporting horizon is "
            "sized against this median rather than fitted to anything."
        ),
        "widest_declared_radius_cm": hazards.widest_declared_radius_cm,
        "damage_over_time_volume_classes": by_count(hazards.damage_volume_classes),
        "damage_over_time_volume_note": (
            "FGDamageOverTimeVolume is a native map actor carrying an mDotClass, so "
            "it is the obvious candidate for the gas channel. It is not one: these "
            "are what its placements actually deal damage with, resolved per run, "
            "and they are the box that kills a player who leaves the map."
        ),
        "widest_declared_radius_note": (
            "the widest radius any source declares, and what the lookup grid is "
            "sized to. It exceeds the reporting radius, so spawns_here can and does "
            "fire further out than hostiles_nearby."
        ),
        "radioactive_sources_in_the_ground": hazards.uranium_sources,
        "resource_classes_checked_for_radioactivity": hazards.resource_classes_checked,
        "radioactive_resource_classes": json_object(hazards.radioactive_classes),
        "deposits_with_no_resource_class": hazards.deposits_without_a_resource,
        "deposits_note": (
            "a deposit that does not serialise mOverrideResourceClass holds its "
            "class default, which is null, so its resource is unknown rather than "
            "assumed. Those deposits contribute no radiation."
        ),
    }


def _rows_touched(rows: list[MapPlacement]) -> JsonObject:
    """How many rows carry each hazard key."""

    def _rows_with_hazard_key(key: str) -> int:
        return sum(1 for r in rows if r.get("hazard", {}).get(key) is not None)

    return {
        "rows": len(rows),
        "with_a_hostile_in_reporting_radius": _rows_with_hazard_key("hostiles_nearby"),
        "with_a_hostile_whose_own_radius_contains_them": _rows_with_hazard_key("spawns_here"),
        "inside_a_spore_flower_damage_sphere": _rows_with_hazard_key(
            "inside_spore_flower_damage_sphere"
        ),
        "with_gas_in_reporting_radius": _rows_with_hazard_key("nearest_gas_cm"),
        "with_uranium_in_reporting_radius": _rows_with_hazard_key("nearest_uranium_cm"),
        "with_a_nuclear_hog_spawner_in_reporting_radius": _rows_with_hazard_key(
            "nearest_nuclear_hog_spawner_cm"
        ),
        "with_no_hazard_context_at_all": sum(1 for r in rows if not r.get("hazard")),
    }

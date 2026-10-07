"""The object walk that turns one parsed save into the projection dict.

One pass files every object by class. Whatever needs a component written after its owner, or
a pipe network written after its pipes, is held during the pass and joined in ``finish``. A
value the projection carries as the save wrote it is passed through as read, and its ``cast``
names the type the save writes there.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal, NamedTuple, cast

from ..projection import SCHEMA_VERSION
from ..schema import (
    Buffers,
    BuildableRecord,
    ExtractorRecord,
    GeneratorRecord,
    MachineRecord,
    MaterialEdge,
    Projection,
    Uptime,
)
from . import census, inventories, power, progression, routes, structures
from .census import Drops
from .interning import Interner
from .inventories import Contents, CrateActor, StorageActor
from .parser import (
    ActorHeader,
    ComponentHeader,
    ParsedObject,
    ParsedSave,
    SaveValue,
    header_info,
    read_full_save,
)
from .power import PoleActor, WireSpan
from .readers import (
    as_sequence,
    class_from_type_path,
    iter_objects,
    optional_int,
    position_of,
    properties_of,
    ref_class,
    ref_path,
    to_float,
    to_int,
    truthy,
    yaw_of,
)
from .registers import (
    ATTACHMENT_HINTS,
    CRATE_CLASSES,
    EXTRACTOR_HINTS,
    FLUID_BUFFER_CLASSES,
    GENERATOR_HINTS,
    MANUFACTURER_HINTS,
    PIPE_CLASSES,
    POWER_POLE_CLASSES,
    STORAGE_CLASSES,
)
from .routes import ChainActor, HeldNetwork, PipeActor

__all__ = ["extract_projection"]

#: The inventory roles that are a machine's own buffers, and the side each one feeds. A
#: generator has no InputInventory: its intake is FuelInventory.
_BUFFER_SIDES: dict[str, Literal["in", "out", "fuel"]] = {
    "InputInventory": "in",
    "OutputInventory": "out",
    "FuelInventory": "fuel",
}

#: ``BP_UnlockSubsystem_C`` flags. Written only once true, so ABSENT means not researched.
_UNLOCK_FLAGS: Final = (
    "mIsMapUnlocked",
    "mIsBuildingOverclockUnlocked",
    "mIsBuildingProductionBoostUnlocked",
    "mIsBuildingEfficiencyUnlocked",
    "mIsBlueprintsUnlocked",
    "mIsCustomizerUnlocked",
)
_UNLOCK_COUNTS: Final = ("mNumTotalInventorySlots", "mNumTotalArmEquipmentSlots")

#: Somersloop runtime property names are UNVERIFIED -- in neither the save nor Docs.json.
_BOOST_PROPERTIES = ("mProductionBoost", "mCurrentProductionBoost", "mPendingProductionBoost")


class _Object(NamedTuple):
    """One object of the save, as the walk sees it."""

    cls: str
    instance: str
    header: ActorHeader | ComponentHeader
    obj: ParsedObject
    properties: dict[str, SaveValue]


def _empty_projection(path: str) -> Projection:
    """The projection's keys in output order; ``docs/save-projection.md`` describes each."""
    return {
        "schema_version": SCHEMA_VERSION,
        "header": header_info(path),
        "progression": {},
        "research": {"unclaimed_hard_drives": [], "ongoing": [], "unlocked_trees": []},
        "unlock_flags": {},
        "building_counts": {},
        "lightweight_counts": {},
        "removed": {"cells": [], "instances": [], "counts": {}},  # §6.11
        "structures": {"classes": [], "instances": []},
        "belts": {"classes": [], "segments": []},  # §6.16
        "pipes": {"classes": [], "networks": [], "segments": []},  # §6.16
        "power": {"poles": {"classes": [], "instances": []}, "wires": []},  # §6.12
        "machines": [],
        "extractors": [],
        "generators": [],
        "attachments": [],
        "storage": [],
        "crates": [],  # §6.13
        "pipe_networks": [],
        "depot": {},
        # Split by owner: lumping machine buffers in with carried stock overstates everything.
        # Fluids are raw litres here; the server scales them.
        "inventories": {"player": {}, "storage": {}, "machine": {}, "crate": {}},
        "node_state": {},
        # Char_Player_C's transform; BP_PlayerState_C sits at the origin and is not a position.
        "players": [],
        # Interned, because repeated instanceNames would be megabytes.
        "graph": {"actors": [], "roles": [], "material": [], "power": []},
        "warnings": [],
        # Counted in ``finish``; the key is here so that it keeps its place, last.
        "n_objects": 0,
    }


def _class_names(refs: SaveValue) -> list[str]:
    """Sorted class names of a reference array; absent means empty."""
    return sorted(filter(None, (ref_class(ref) for ref in as_sequence(refs or []))))


def _owner_of(instance: str) -> str:
    """A component's owner as its FULL instanceName, which is what the actor record uses."""
    return str(instance).rpartition(".")[0]


def _contents(stacks: SaveValue) -> Contents:
    """An inventory component as ``(totals, slotCount)``."""
    return inventories.inventory_totals(stacks), len(as_sequence(stacks or []))


@dataclass
class _ProjectionWalk:
    """The accumulators of one pass over a save, and the handler for each kind of object."""

    out: Projection
    #: Every guard's count of what it skipped, drained into ``warnings`` at the end.
    drops: Drops = field(default_factory=Drops)
    #: instanceName -> class for every actor filed nowhere; the census decides which matter.
    unfiled: dict[str, str] = field(default_factory=dict[str, str])
    building_counts: dict[str, int] = field(default_factory=dict[str, int])
    object_count: int = 0
    #: Per conveyor chain; its trailing bytes decode lazily in ``routes``.
    chain_actors: list[ChainActor] = field(default_factory=list[ChainActor])
    #: Per pipe, and per network: a pipe's fluid comes off a network written after it.
    pipe_actors: list[PipeActor] = field(default_factory=list[PipeActor])
    pipe_networks: list[HeldNetwork] = field(default_factory=list[HeldNetwork])
    #: Per container or buffer, and owner -> contents per ``StorageInventory``, a component
    #: written after its owner.
    storage_actors: list[StorageActor] = field(default_factory=list[StorageActor])
    storage_inventories: dict[str, Contents] = field(default_factory=dict[str, Contents])
    #: The same for crates and every component named ``Inventory``.
    crate_actors: list[CrateActor] = field(default_factory=list[CrateActor])
    inventory_components: dict[str, Contents] = field(default_factory=dict[str, Contents])
    #: Per pole, and wire short name -> its two drawn ends.
    pole_actors: list[PoleActor] = field(default_factory=list[PoleActor])
    wire_geometry: dict[str, WireSpan] = field(default_factory=dict[str, WireSpan])
    #: Every ``Build_`` actor's short name -> (x, y, z), to pair wire ends with actors.
    actor_positions: dict[str, tuple[float, ...]] = field(
        default_factory=dict[str, tuple[float, ...]]
    )
    actors: Interner = field(default_factory=Interner)
    roles: Interner = field(default_factory=Interner)
    uptime: dict[str, Uptime] = field(default_factory=dict[str, Uptime])
    buffers: dict[str, Buffers] = field(default_factory=dict[str, Buffers])
    #: owner -> {itemClass: count} slotted into its InventoryPotential.
    potential_slots: dict[str, dict[str, float]] = field(
        default_factory=dict[str, dict[str, float]]
    )
    record_by_instance: dict[str, BuildableRecord] = field(
        default_factory=dict[str, BuildableRecord]
    )
    material_edges: list[MaterialEdge] = field(default_factory=list[MaterialEdge])
    wire_ends: dict[str, list[tuple[str, str]]] = field(
        default_factory=dict[str, list[tuple[str, str]]]
    )

    def visit(
        self, type_path: str, header: ActorHeader | ComponentHeader, obj: ParsedObject
    ) -> None:
        self.object_count += 1
        properties = properties_of(obj)
        instance: str = getattr(header, "instanceName", None) or getattr(obj, "instanceName", "")
        seen = _Object(class_from_type_path(type_path), instance, header, obj, properties)

        # Components have no typePath, so these run before the empty-class guard.
        if "mInventoryStacks" in properties:
            self._on_inventory_component(str(instance), properties["mInventoryStacks"])
        if "mLastProductivityMeasurementDuration" in properties:
            self._on_productivity(str(instance), properties)
        self._on_connections(instance, properties)
        if "mWireInstances" in properties:
            self._on_wire_geometry(instance, properties["mWireInstances"])

        if not seen.cls:
            return
        handler = _CLASS_HANDLERS.get(seen.cls)
        if handler is not None:
            handler(self, seen)
        # The three ``_RepSize*`` variants are the same actor with the identical record.
        elif seen.cls.startswith("FGConveyorChainActor"):
            self.chain_actors.append((getattr(header, "position", None), obj))
        elif seen.cls.startswith(("BP_ResourceNode", "BP_Fracking")):
            self._on_resource_node(seen)
        elif seen.cls in CRATE_CLASSES:
            self._on_crate(seen)
        elif not seen.cls.startswith("Build_"):
            self.unfiled[str(instance)] = seen.cls
        else:
            self._on_buildable(seen)

    # ---- components ----------------------------------------------------

    def _on_inventory_component(self, instance: str, stacks: SaveValue) -> None:
        bucket = inventories.inventory_bucket(instance)
        inventories.accumulate_inventory(stacks, self.out["inventories"][bucket])
        role = instance.rsplit(".", 1)[-1]
        if role in _BUFFER_SIDES:
            # Per ITEM: "is the output backed up" needs the item's stack size.
            self.buffers.setdefault(_owner_of(instance), {})[_BUFFER_SIDES[role]] = {
                "items": inventories.inventory_totals(stacks),
                "slots": len(as_sequence(stacks or [])),
            }
        elif role == "StorageInventory":
            # Splitters own one too; `storage` looks up only container owners.
            self.storage_inventories[_owner_of(instance)] = _contents(stacks)
        elif role.lower() == "inventory":
            # Case-folded: spelled `.inventory` on some save versions and `.Inventory` on
            # others. Pawns and drop pods own one too; `crates` looks up only crate owners.
            self.inventory_components[_owner_of(instance)] = _contents(stacks)
        elif role == "InventoryPotential":
            # The only record of a committed Power Shard: a shard raises the maximum clock,
            # so the clock cannot say how many are slotted.
            totals = inventories.inventory_totals(stacks)
            if totals:
                self.potential_slots[_owner_of(instance)] = totals

    def _on_productivity(self, instance: str, properties: dict[str, SaveValue]) -> None:
        # A fixed 300 s window. ProduceDuration is ABSENT when zero, so missing is a real 0.
        window = properties.get("mLastProductivityMeasurementDuration") or 0.0
        produce = properties.get("mLastProductivityMeasurementProduceDuration", 0.0) or 0.0
        current_window = properties.get("mCurrentProductivityMeasurementDuration", 0.0) or 0.0
        current_produce = (
            properties.get("mCurrentProductivityMeasurementProduceDuration", 0.0) or 0.0
        )
        self.uptime[instance] = {
            "window_s": round(to_float(window), 2),
            "produce_s": round(to_float(produce), 2),
            "cur_window_s": round(to_float(current_window), 2),
            "cur_produce_s": round(to_float(current_produce), 2),
            "producing": truthy(properties.get("mIsProducing", 0)),
        }

    def _on_connections(self, instance: str, properties: dict[str, SaveValue]) -> None:
        # A connection component is "<...>.Build_X_C_123.Output1": the owner is the
        # second-to-last segment and the connector role, which orients the edge, the last.
        target_path = ref_path(properties.get("mConnectedComponent"))
        if target_path and "." in instance:
            self.material_edges.append(
                [
                    self.actors.intern(instance.rsplit(".", 2)[-2]),
                    self.actors.intern(target_path.rsplit(".", 2)[-2]),
                    self.roles.intern(instance.rsplit(".", 1)[-1]),
                    self.roles.intern(target_path.rsplit(".", 1)[-1]),
                ]
            )
        for wire in as_sequence(properties.get("mWires") or []):
            wire_path = ref_path(wire)
            if wire_path and "." in instance:
                self.wire_ends.setdefault(wire_path, []).append(
                    (instance.rsplit(".", 2)[-2], instance.rsplit(".", 1)[-1])
                )

    def _on_wire_geometry(self, instance: str, raw: SaveValue) -> None:
        # Keyed on the property, so a modded power line is read too. Not a drop when it will
        # not read: the wire keeps its edge and a null row, and older saves lack the property.
        span = power.wire_span(raw)
        if span is not None:
            self.wire_geometry[str(instance).rsplit(".", 1)[-1]] = span

    # ---- singletons ------------------------------------------------------

    def on_recipe_manager(self, seen: _Object) -> None:
        self.out["progression"]["available_recipes"] = _class_names(
            seen.properties.get("mAvailableRecipes")
        )

    def on_schematic_manager(self, seen: _Object) -> None:
        properties = seen.properties
        self.out["progression"]["purchased_schematics"] = _class_names(
            properties.get("mPurchasedSchematics")
        )
        self.out["progression"]["last_active_schematic"] = ref_class(
            properties.get("mLastActiveSchematic")
        )

    def on_game_phase_manager(self, seen: _Object) -> None:
        properties = seen.properties
        progress = self.out["progression"]
        progress["game_phase"] = ref_class(properties.get("mCurrentGamePhase")) or str(
            properties.get("mCurrentGamePhase") or ""
        )
        progress["target_phase"] = ref_class(properties.get("mTargetGamePhase")) or str(
            properties.get("mTargetGamePhase") or ""
        )
        progress["phase_costs_remaining"] = progression.phase_costs(
            properties.get("mGamePhaseCosts")
        )
        # The live delivery record; mGamePhaseCosts above is deprecated and frozen (§6.4).
        progress["paid_off_target"] = progression.cost_amounts(
            properties.get("mTargetGamePhasePaidOffCosts")
        )

    def on_research_manager(self, seen: _Object) -> None:
        properties = seen.properties
        research = self.out["research"]
        research["unclaimed_hard_drives"] = progression.hard_drives(
            properties.get("mUnclaimedHardDriveData")
        )
        research["last_used_hard_drive_id"] = optional_int(properties.get("mLastUsedHardDriveID"))
        research["unlocked_trees"] = _class_names(properties.get("mUnlockedResearchTrees"))
        research["ongoing"] = progression.ongoing(properties.get("mSavedOngoingResearch"))

    def on_unlock_subsystem(self, seen: _Object) -> None:
        flags = self.out["unlock_flags"]
        for name in _UNLOCK_FLAGS:
            if name in seen.properties:
                flags[name] = truthy(seen.properties[name])
        for name in _UNLOCK_COUNTS:
            if name in seen.properties:
                flags[name] = to_int(seen.properties[name])

    def on_central_storage(self, seen: _Object) -> None:
        self.out["depot"] = progression.stored_items(seen.properties.get("mStoredItems"))

    def on_lightweight_subsystem(self, seen: _Object) -> None:
        self.out["lightweight_counts"] = structures.lightweight(seen.obj)
        self.out["structures"] = structures.structures(seen.obj, self.drops)

    def on_pipe_network(self, seen: _Object) -> None:
        properties = seen.properties
        fluid = ref_class(properties.get("mFluidDescriptor"))
        if fluid:
            self.out["pipe_networks"].append({"instance": seen.instance, "fluid": fluid})
        # Held even without a fluid: it still owns its pipes, and drawn-fluid-unknown beats
        # not drawn.
        self.pipe_networks.append(
            (
                properties.get("mPipeNetworkID"),
                fluid,
                [
                    ref_path(m)
                    for m in as_sequence(properties.get("mFluidIntegrantScriptInterfaces") or [])
                ],
            )
        )

    def on_player(self, seen: _Object) -> None:
        self.out["players"].append(
            {
                "instance": seen.instance,
                "pos": position_of(seen.header),
                # Present only while held: a hint at the active pawn in a co-op save.
                "has_build_gun": "mBuildGun" in seen.properties,
            }
        )

    # ---- placed actors ---------------------------------------------------

    def _on_resource_node(self, seen: _Object) -> None:
        self.building_counts[seen.cls] = self.building_counts.get(seen.cls, 0) + 1
        left = seen.properties.get("mResourcesLeft")
        if left is not None and left != -1:
            self.out["node_state"][seen.instance] = {"resources_left": to_int(left)}

    def _on_crate(self, seen: _Object) -> None:
        # Not a ``Build_`` actor, so it owes ``building_counts`` nothing.
        self.crate_actors.append(
            (
                seen.cls,
                seen.instance,
                position_of(seen.header),
                yaw_of(getattr(seen.header, "rotation", None)),
                seen.properties.get("mCrateType"),
            )
        )

    def _on_buildable(self, seen: _Object) -> None:
        cls, instance, header, properties = seen.cls, seen.instance, seen.header, seen.properties
        self.building_counts[cls] = self.building_counts.get(cls, 0) + 1
        # Every buildable, not only poles: a wire ends on a machine nearly as often.
        at = position_of(header)
        if at is not None:
            self.actor_positions[str(instance).rsplit(".", 1)[-1]] = tuple(at)

        # Held for a later table AND still counted and recorded below.
        held = (
            cls in POWER_POLE_CLASSES
            or cls in PIPE_CLASSES
            or cls in STORAGE_CLASSES
            or cls in FLUID_BUFFER_CLASSES
        )
        if cls in POWER_POLE_CLASSES:
            self.pole_actors.append(
                (cls, instance, position_of(header), yaw_of(getattr(header, "rotation", None)))
            )
        if cls in PIPE_CLASSES:
            self.pipe_actors.append(
                (cls, instance, getattr(header, "position", None), properties.get("mSplineData"))
            )
        if cls in STORAGE_CLASSES or cls in FLUID_BUFFER_CLASSES:
            self.storage_actors.append(
                (
                    cls,
                    instance,
                    position_of(header),
                    yaw_of(getattr(header, "rotation", None)),
                    properties.get("mFluidBox"),
                )
            )

        record = self._buildable_record(seen)
        self.record_by_instance[str(instance)] = record
        self._file_record(seen, record, held)

    def _buildable_record(self, seen: _Object) -> BuildableRecord:
        properties = seen.properties
        record: BuildableRecord = {
            "cls": seen.cls,
            "instance": seen.instance,
            "pos": position_of(seen.header),
            # Always emitted, unlike the fields below: an absent yaw means an old projection,
            # a null one a rotation that would not read.
            "yaw": yaw_of(getattr(seen.header, "rotation", None)),
        }
        if "mCurrentPotential" in properties:
            record["clock"] = round(to_float(properties["mCurrentPotential"]), 6)
        if "mPendingPotential" in properties:
            record["pending_clock"] = round(to_float(properties["mPendingPotential"]), 6)
        if "mIsProductionPaused" in properties:
            record["paused"] = truthy(properties["mIsProductionPaused"])
        for name in _BOOST_PROPERTIES:
            if name in properties:
                record["production_boost"] = to_float(properties[name])
                record["production_boost_field"] = name
        # Uptime is the actor's own property; buffers are components and come later.
        live = self.uptime.get(str(seen.instance))
        if live:
            record["uptime"] = live
        return record

    def _file_record(self, seen: _Object, record: BuildableRecord, held: bool) -> None:
        """File the record under its kind, which adds that kind's own field in place: the
        record is the same object ``record_by_instance`` holds for ``finish``."""
        cls, properties = seen.cls, seen.properties
        if any(hint in cls for hint in MANUFACTURER_HINTS):
            machine = cast("MachineRecord", record)
            machine["recipe"] = ref_class(properties.get("mCurrentRecipe"))
            self.out["machines"].append(machine)
        elif any(hint in cls for hint in EXTRACTOR_HINTS):
            extractor = cast("ExtractorRecord", record)
            extractor["node"] = ref_path(properties.get("mExtractableResource"))
            self.out["extractors"].append(extractor)
        elif any(hint in cls for hint in GENERATOR_HINTS):
            generator = cast("GeneratorRecord", record)
            generator["fuel"] = ref_class(properties.get("mCurrentFuelClass"))
            self.out["generators"].append(generator)
        elif any(hint in cls for hint in ATTACHMENT_HINTS):
            self.out["attachments"].append(record)
        elif not held:
            # Ordinary for a wall; the point of the census for a class that runs a recipe.
            self.unfiled[str(seen.instance)] = cls

    # ---- after the pass --------------------------------------------------

    def _attach_component_records(self) -> None:
        for owner, sides in self.buffers.items():
            record = self.record_by_instance.get(owner)
            if record is not None:
                record["buffers"] = sides
        for owner, slotted in self.potential_slots.items():
            record = self.record_by_instance.get(owner)
            if record is not None:
                record["potential_slots"] = slotted

    def finish(self, save: ParsedSave) -> None:
        out = self.out
        self._attach_component_records()
        # Freezes ``actors``: every table after this joins against the snapshot below.
        power_edges, out["power"] = power.power_network(
            self.wire_ends,
            self.wire_geometry,
            self.pole_actors,
            self.actor_positions,
            self.actors,
            self.drops,
        )
        out["graph"] = {
            "actors": self.actors.names(),
            "roles": self.roles.names(),
            "material": self.material_edges,
            "power": power_edges,
        }
        out["building_counts"] = dict(sorted(self.building_counts.items()))
        out["belts"] = routes.belts(self.chain_actors, self.actors, self.drops)
        out["pipes"] = routes.pipes(self.pipe_actors, self.pipe_networks, self.actors, self.drops)
        out["storage"] = inventories.storage(
            self.storage_actors, self.storage_inventories, self.pipe_networks
        )
        out["crates"] = inventories.crates(self.crate_actors, self.inventory_components)
        out["removed"] = structures.removed(save)
        out["n_objects"] = self.object_count
        out["progression"].setdefault("available_recipes", [])
        out["progression"].setdefault("purchased_schematics", [])

        out["warnings"].extend(census.null_yaw_note(out))
        out["warnings"].extend(
            census.unfiled_notes(self.unfiled, self.uptime.keys() | self.buffers.keys())
        )
        out["warnings"].extend(census.drop_notes(self.drops))


_CLASS_HANDLERS: dict[str, Callable[[_ProjectionWalk, _Object], None]] = {
    "FGRecipeManager": _ProjectionWalk.on_recipe_manager,
    "BP_SchematicManager_C": _ProjectionWalk.on_schematic_manager,
    "BP_GamePhaseManager_C": _ProjectionWalk.on_game_phase_manager,
    "BP_ResearchManager_C": _ProjectionWalk.on_research_manager,
    "BP_UnlockSubsystem_C": _ProjectionWalk.on_unlock_subsystem,
    "FGCentralStorageSubsystem": _ProjectionWalk.on_central_storage,
    # Holds Build_* classes that appear in no actor header.
    "FGLightweightBuildableSubsystem": _ProjectionWalk.on_lightweight_subsystem,
    "FGPipeNetwork": _ProjectionWalk.on_pipe_network,
    "Char_Player_C": _ProjectionWalk.on_player,
}


def extract_projection(path: str) -> Projection:
    """The JSON projection of the save at ``path``."""
    save = read_full_save(path)
    # stdout is the projection; ``projection._run_sidecar`` folds stderr into ``warnings``.
    notes: list[tuple[int, str]] = getattr(save, "warnings", None) or []
    for offset, what in notes:
        print(f"pioneersav: at body offset {offset}: {what}", file=sys.stderr)
    walk = _ProjectionWalk(out=_empty_projection(path))
    for type_path, header, obj in iter_objects(save):
        walk.visit(type_path, header, obj)
    walk.finish(save)
    return walk.out

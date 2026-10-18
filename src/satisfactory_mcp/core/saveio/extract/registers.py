"""The class registers the walk files actors by.

Most are written-out lists rather than name patterns, because the game's names lie in both
directions: a conveyor pole is a "pole" that carries no power, and a splitter owns a
component literally named ``StorageInventory``.
"""

from __future__ import annotations

from ..schema import CrateKind

#: The actor the game drops when items have nowhere else to go; death and dismantle crates
#: are one class told apart by ``mCrateType``. See ``docs/save-projection.md`` §6.13.
CRATE_CLASSES = ("BP_Crate_C",)

#: ``EFGCrateType`` with the ``CT_`` dropped and lowercased; ``none`` is the game's own
#: value for a crate older than the property, not a parse failure (§6.13).
CRATE_KINDS: dict[str, CrateKind] = {
    "CT_None": "none",
    "CT_DismantleCrate": "dismantle",
    "CT_DeathCrate": "death",
}

#: Where a wire can end at something placed to end wires at it (§6.12). Listed: the conveyor
#: pole and pipeline support are "poles" with no power, and the tower platform has no "Pole"
#: in its name. Switches and Power Storage are facilities, not poles; their wires still draw,
#: since the geometry comes off the wire actor.
POWER_POLE_CLASSES = (
    "Build_PowerPoleMk1_C",
    "Build_PowerPoleMk2_C",
    "Build_PowerPoleMk3_C",
    "Build_PowerPoleWall_C",
    "Build_PowerPoleWall_Mk2_C",
    "Build_PowerPoleWallDouble_Mk2_C",
    "Build_PowerTowerPlatform_C",
)

#: The fluid pipes that carry an ``mSplineData``, with their ``NoIndicator`` variants.
#: Listed: ``Build_PipeHyper_C`` carries the same property and is a hypertube.
PIPE_CLASSES = (
    "Build_Pipeline_C",
    "Build_PipelineMK2_C",
    "Build_Pipeline_NoIndicator_C",
    "Build_PipelineMK2_NoIndicator_C",
)

#: The hypertube pieces, which carry an ``mSplineData`` the pipe way and move the player.
HYPERTUBE_CLASSES = ("Build_PipeHyper_C",)

#: Where a hypertube run starts: filed so the map can draw it, and powered, which is why its
#: productivity monitor once had it dismissed below.
HYPERTUBE_ENTRANCE_CLASSES = ("Build_PipeHyperStart_C",)

MANUFACTURER_HINTS = (
    "ConstructorMk1",
    "SmelterMk1",
    "FoundryMk1",
    "OilRefinery",
    "Packager",
    "ManufacturerMk1",
    "AssemblerMk1",
    "Blender",
    "HadronCollider",
    "Converter",
    "QuantumEncoder",
)
EXTRACTOR_HINTS = (
    "MinerMk1",
    "MinerMk2",
    "MinerMk3",
    "OilPump",
    "WaterPump",
    "FrackingExtractor",
    "FrackingSmasher",
)
GENERATOR_HINTS = (
    "GeneratorCoal",
    "GeneratorFuel",
    "GeneratorNuclear",
    "GeneratorBiomass",
    "GeneratorGeoThermal",
    "GeneratorIntegratedBiomass",
)
#: The splitters and mergers a belt run passes through, without which a drawn run has a hole
#: at every junction. Not ``Build_ConveyorCeilingAttachment_C``: that is a pole.
ATTACHMENT_HINTS = (
    "ConveyorAttachmentSplitter",
    "ConveyorAttachmentMerger",
)

#: The pieces a pipe run passes through: junctions, pumps and valves. Filed so the map can draw
#: them; none runs a recipe, and the flow they carry is the ``pipes`` block's.
FITTING_HINTS = (
    "PipelineJunction",
    "PipelinePump",
    "Build_Valve",
)

#: One-off buildings that are neither machines nor storage: drawn on the map, counted nowhere
#: else. Written out, because "Mam" or "Sink" as a pattern would catch other names.
FACILITY_CLASSES = (
    "Build_TradingPost_C",  # the HUB
    "Build_SpaceElevator_C",
    "Build_Mam_C",
    "Build_ResourceSink_C",
    "Build_ResourceSinkShop_C",
    "Build_Workshop_C",
    "Build_WorkBench_C",
    "Build_BlueprintDesigner_C",
    "Build_BlueprintDesigner_Mk2_C",
    "Build_BlueprintDesigner_Mk3_C",
    "Build_RadarTower_C",
    "Build_PowerStorageMk1_C",
    "Build_AlienPowerBuilding_C",
    "Build_TreeGiftProducer_C",
    "Build_PowerSwitch_C",
    "Build_PriorityPowerSwitch_C",
)

#: The circuit bridges whose ``mIsSwitchOn`` decides whether two circuits are one grid; the
#: property is written only while on (save-projection.md §6.12a).
SWITCH_CLASSES = ("Build_PowerSwitch_C", "Build_PriorityPowerSwitch_C")

#: The battery, whose ``mPowerStore`` is the charge it holds in MWh, written only when above 0.
POWER_STORAGE_CLASSES = ("Build_PowerStorageMk1_C",)

#: The classes whose whole point is to hold items in a ``StorageInventory``. Listed, because
#: every splitter and merger owns a component by that name holding items in transit. Machine
#: buffers, the AWESOME Shop and the Space Elevator intake are reported elsewhere.
STORAGE_CLASSES = (
    "Build_StorageContainerMk1_C",
    "Build_StorageContainerMk2_C",
    # Personal Storage Box, the HUB's container and the Blueprint Designer's.
    "Build_StoragePlayer_C",
    "Build_StorageIntegrated_C",
    "Build_StorageBlueprint_C",
    # The Dimensional Depot UPLOADER, whose contents are not the depot's central total.
    "Build_CentralStorage_C",
)

#: Storage owners matched by name: a Freight Wagon is a vehicle with no ``Build_`` class,
#: and no save here holds one to read its class name off.
STORAGE_OWNER_HINTS = ("FreightWagon",)

#: The fluid buffers. A class list, because every pipe, pump and valve carries an
#: ``mFluidBox`` too; the fluid's identity comes off the claiming ``FGPipeNetwork``.
FLUID_BUFFER_CLASSES = (
    "Build_PipeStorageTank_C",  # Fluid Buffer, 400 m3
    "Build_IndustrialTank_C",  # Industrial Fluid Buffer, 2,400 m3
)

#: Classes carrying a factory building's evidence (a productivity monitor or a machine
#: buffer) that deliberately get no record. **Edit this list** when the unfiled-class census
#: names a class: adding it here claims its record is not wanted, a hint list claims it is.
#: Across 18 saves (save versions 25 to 60) this and the pump, the HUB, the Space Elevator,
#: the AWESOME Shop and the hypertube entrance were the only such classes; those five are
#: filed now, as a fitting, as facilities and as ``hypertube_entrances``.
DISMISSED_FACTORY_CLASSES = frozenset(
    {
        # The Portable Miner extracts, but has no ``Build_`` class or descriptor, so nothing
        # downstream could cost, size or draw it.
        "BP_PortableMiner_C",
    }
)

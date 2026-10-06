"""The class registers the walk files actors by.

Most are written-out lists rather than name patterns, because the game's names lie in both
directions: a conveyor pole is a "pole" that carries no power, and a splitter owns a
component literally named ``StorageInventory``.
"""

from __future__ import annotations

#: The actor the game drops when items have nowhere else to go; death and dismantle crates
#: are one class told apart by ``mCrateType``. See ``docs/save-projection.md`` §6.13.
CRATE_CLASSES = ("BP_Crate_C",)

#: ``EFGCrateType`` with the ``CT_`` dropped and lowercased; ``none`` is the game's own
#: value for a crate older than the property, not a parse failure (§6.13).
CRATE_KINDS = {"CT_None": "none", "CT_DismantleCrate": "dismantle", "CT_DeathCrate": "death"}

#: Where a wire can end at something placed to end wires at it (§6.12). Listed: the conveyor
#: pole and pipeline support are "poles" with no power, and the tower platform has no "Pole"
#: in its name. Switches and Power Storage are absent because no save here holds one; their
#: wires still draw, since the geometry comes off the wire actor.
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
#: Across 18 saves (save versions 25 to 60) these were the only such classes.
DISMISSED_FACTORY_CLASSES = frozenset(
    {
        # Moves fluid along a pipe the ``pipes`` layer already draws, and runs no recipe.
        "Build_PipelinePump_C",
        # A hypertube entrance. It moves the player, which is not production.
        "Build_PipeHyperStart_C",
        # The HUB. Its milestone intake is ``progression``'s business.
        "Build_TradingPost_C",
        # Its intake is already reported against the phase, by ``progression``.
        "Build_SpaceElevator_C",
        # The AWESOME Shop. Its inventory is a catalogue, not stock the player owns.
        "Build_ResourceSinkShop_C",
        # The Portable Miner extracts, but has no ``Build_`` class or descriptor, so nothing
        # downstream could cost, size or draw it.
        "BP_PortableMiner_C",
    }
)

"""The line colour family of each item on the production graph: one fixed table.

docs/planner_vision.md §4.2 lists the groups; an item missing here is ``other``.
"""

from __future__ import annotations

from ..solver.model import MW

__all__ = ["GROUPS", "ITEM_GROUPS", "item_group"]

GROUPS = ("oil", "water", "gas", "ore", "metal", "component", "nuclear", "power", "other")

_BY_GROUP: dict[str, tuple[str, ...]] = {
    "oil": (
        "LiquidOil",
        "HeavyOilResidue",
        "LiquidFuel",
        "LiquidTurboFuel",
        "LiquidBiofuel",
        "RocketFuel",
        "IonizedFuel",
        "PolymerResin",
        "Plastic",
        "Rubber",
        "PetroleumCoke",
        "PackagedOil",
        "PackagedOilResidue",
        "Fuel",
        "TurboFuel",
        "PackagedBiofuel",
        "PackagedRocketFuel",
        "PackagedIonizedFuel",
    ),
    "water": ("Water", "PackagedWater"),
    "gas": (
        "NitrogenGas",
        "PackagedNitrogenGas",
        "DarkEnergy",
        "QuantumEnergy",
    ),
    "ore": (
        "OreIron",
        "OreCopper",
        "OreGold",
        "OreBauxite",
        "Stone",
        "Coal",
        "CompactedCoal",
        "Sulfur",
        "RawQuartz",
        "SAM",
    ),
    "metal": (
        "IronIngot",
        "CopperIngot",
        "GoldIngot",
        "SteelIngot",
        "AluminumIngot",
        "FicsiteIngot",
        "IronPlate",
        "IronRod",
        "IronScrew",
        "Wire",
        "Cable",
        "HighSpeedWire",
        "CopperSheet",
        "CopperDust",
        "SteelPipe",
        "SteelPlate",
        "AluminumPlate",
        "AluminumCasing",
        "AluminumScrap",
        "FicsiteMesh",
    ),
    "component": (
        "IronPlateReinforced",
        "ModularFrame",
        "ModularFrameHeavy",
        "ModularFrameFused",
        "ModularFrameLightweight",
        "SteelPlateReinforced",
        "Rotor",
        "Stator",
        "Motor",
        "MotorLightweight",
        "CircuitBoard",
        "CircuitBoardHighSpeed",
        "HighSpeedConnector",
        "Computer",
        "ComputerSuper",
        "CoolingSystem",
        "AluminumPlateReinforced",
        "Battery",
        "Cement",
        "Silica",
        "QuartzCrystal",
        "Diamond",
        "TimeCrystal",
        "DarkMatter",
        "SAMIngot",
        "SAMFluctuator",
        "Fabric",
        "GasTank",
        "FluidCanister",
        "Gunpowder",
        "GunpowderMK2",
        "PressureConversionCube",
        "QuantumOscillator",
        "TemporalProcessor",
        "SingularityCell",
        "Biofuel",
        "SpaceElevatorPart_1",
        "SpaceElevatorPart_2",
        "SpaceElevatorPart_3",
        "SpaceElevatorPart_4",
        "SpaceElevatorPart_5",
        "SpaceElevatorPart_6",
        "SpaceElevatorPart_7",
        "SpaceElevatorPart_8",
        "SpaceElevatorPart_9",
        "SpaceElevatorPart_10",
        "SpaceElevatorPart_11",
        "SpaceElevatorPart_12",
    ),
    "nuclear": (
        "OreUranium",
        "UraniumCell",
        "NuclearFuelRod",
        "NuclearWaste",
        "NonFissibleUranium",
        "PlutoniumPellet",
        "PlutoniumCell",
        "PlutoniumFuelRod",
        "PlutoniumWaste",
        "Ficsonium",
        "FicsoniumFuelRod",
        "ElectromagneticControlRod",
    ),
}

#: Item class id to group; power is ``MW``.
ITEM_GROUPS: dict[str, str] = {
    f"Desc_{stem}_C": group for group, stems in _BY_GROUP.items() for stem in stems
} | {MW: "power"}


def item_group(item_id: str | None) -> str:
    """The line colour group of ``item_id``; ``other`` when it is not in the table."""
    return ITEM_GROUPS.get(item_id or "", "other")

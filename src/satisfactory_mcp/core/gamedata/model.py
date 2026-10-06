"""Normalized game-data model.

Every field here is derived from Docs.json except the four in ``constants.py``.
Rates are per minute at 100% clock with no somersloops; fluids are in m3 (already
divided by 1000).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .constants import BUILDING_CLASS_ALIASES, CLASS_NAMES
from .footprint import Footprint

__all__ = [
    "MANUFACTURER_NATIVES",
    "Building",
    "Flow",
    "Fuel",
    "GameData",
    "Item",
    "Recipe",
    "Schematic",
    "pretty_class",
]

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def pretty_class(cls: str | None) -> str | None:
    """An engine class id as words: ``Build_GeneratorIntegratedBiomass_C`` ->
    ``Generator Integrated Biomass``.

    The LAST resort, for the ids the docs dump has no entry for -- the biomass burners, the
    synthetic recipe strings. It is not a display name and cannot become one; what it is, is
    the same language the display names are in. A response that prints ``Fuel-Powered
    Generator`` in one row and ``Build_GeneratorIntegratedBiomass_C`` in the next teaches its
    reader two vocabularies for one kind of object.
    """
    if not cls:
        return None
    leaf = re.sub(r"^(Build|Desc|Recipe|BP)_((Equipment|Item)Descriptor)?", "", str(cls))
    leaf = re.sub(r"_C$", "", leaf)
    words = _CAMEL.sub(" ", leaf.replace("_", " ")).strip()
    return words or str(cls)


#: The 11 classes that make a recipe automatable. Both natives are required --
#: omitting FGBuildableManufacturerVariablePower silently loses the 43
#: Particle-Accelerator / Converter / Quantum-Encoder recipes.
MANUFACTURER_NATIVES = ("FGBuildableManufacturer", "FGBuildableManufacturerVariablePower")


@dataclass(frozen=True)
class Item:
    cls: str
    name: str
    native: str
    form: str  # RF_SOLID | RF_LIQUID | RF_GAS | RF_INVALID
    energy_mj: float  # per m3 for fluids, per item for solids; 0 if not a fuel
    stack_size: str
    sink_points: int
    can_be_discarded: bool
    is_resource: bool
    #: ``mExtraPotential``: how much max clock ONE of these adds when slotted into a
    #: building's InventoryPotential. 0.5 for the Power Shard, 0 for everything else
    #: including the Somersloop, which shares the same native class but boosts production
    #: rather than clock -- so filtering on this field is what keeps ``Desc_CrystalShard_C``
    #: out of the code.
    extra_potential: float = 0.0

    @property
    def is_fluid(self) -> bool:
        return self.form in ("RF_LIQUID", "RF_GAS")

    @property
    def unit(self) -> str:
        return "m3/min" if self.is_fluid else "/min"

    @property
    def sinkable(self) -> bool:
        """Whether an AWESOME Sink can consume this. Fluids are excluded against what the
        dump says -- see ``constants.FLUIDS_CANNOT_BE_SUNK``."""
        from .constants import FLUIDS_CANNOT_BE_SUNK

        if self.is_fluid and FLUIDS_CANNOT_BE_SUNK:
            return False
        return self.sink_points > 0 and self.can_be_discarded


@dataclass(frozen=True)
class Flow:
    """One side of a recipe: an item and its rate."""

    item: str
    amount: float  # per cycle, already m3 for fluids
    per_min: float


@dataclass(frozen=True)
class Recipe:
    cls: str
    name: str
    kind: str  # part | building | manual
    machine: str | None  # Build_* class; None for manual/building recipes
    duration_s: float
    ingredients: tuple[Flow, ...]
    products: tuple[Flow, ...]
    manual_mult: float
    power_min_mw: float  # variable-power machines only; 0 otherwise
    power_max_mw: float
    unlocked_by: tuple[str, ...] = ()
    is_alternate: bool = False
    events: str = ""

    @property
    def power_avg_mw(self) -> float:
        return (self.power_min_mw + self.power_max_mw) / 2

    @property
    def is_variable_power(self) -> bool:
        return self.power_max_mw > 0

    @property
    def is_event(self) -> bool:
        return bool(self.events)

    @property
    def main_product(self) -> str | None:
        """The item the recipe is named for: its first product, or None with no products."""
        return self.products[0].item if self.products else None

    def rate_of(self, item: str) -> float:
        """Net per-minute rate of ``item`` for one machine at 100% clock."""
        out = sum(f.per_min for f in self.products if f.item == item)
        inn = sum(f.per_min for f in self.ingredients if f.item == item)
        return out - inn


@dataclass(frozen=True)
class Fuel:
    fuel_class: str
    supplemental_class: str | None
    byproduct_class: str | None
    byproduct_amount: float


@dataclass(frozen=True)
class Building:
    cls: str
    name: str
    native: str
    power_mw: float
    power_exponent: float  # ALWAYS read per building: 1.321929 vs 1.600000
    boost_power_exponent: float
    can_overclock: bool
    min_clock: float
    base_max_clock: float
    can_boost: bool
    sloop_slots: int
    sloop_mult: float
    base_boost: float
    mfg_speed: float
    descriptor: str | None = None
    build_cost: tuple[Flow, ...] = ()
    unlocked_by: tuple[str, ...] = ()
    #: Axis-aligned footprint from mClearanceData, in metres. None for a few
    #: buildables that declare no clearance at all.
    footprint: Footprint | None = None

    # extractor
    items_per_cycle: float = 0.0
    extract_cycle_s: float = 0.0
    base_extract_rate: float = 0.0  # per min at normal purity, 100% clock
    allowed_resources: tuple[str, ...] = ()
    allowed_forms: tuple[str, ...] = ()

    # generator
    power_production_mw: float = 0.0
    variable_power_factor: float = 0.0
    requires_supplemental: bool = False
    supplemental_ratio: float = 0.0
    fuels: tuple[Fuel, ...] = ()

    # logistics
    items_per_min: float = 0.0
    flow_m3_min: float = 0.0
    #: How much fluid a buffer holds, in cubic metres, from ``mStorageCapacity`` -- 400 on the
    #: Fluid Buffer and 2,400 on the Industrial one. 0.0 for everything that is not a
    #: reservoir: the field is on ``FGBuildablePipeReservoir`` alone. Here because a level is
    #: not a reading without a capacity beside it, and the save records only a bare
    #: ``mFluidBox`` float.
    storage_capacity_m3: float = 0.0
    #: Metres of head a pipeline pump lifts, from ``mDesignPressure``. 20 on Mk1, 50 on Mk2.
    #: ``mMaxPressure`` is higher (22 and 55) and is the point of failure rather than the
    #: rating, so the design figure is the one to plan against.
    head_lift_m: float = 0.0
    max_head_lift_m: float = 0.0
    #: Metres of head this class states in ``mDescription`` prose, 0.0 where it states none.
    #: Six non-pump classes say "Head Lift: 10 m" and the two pumps repeat their
    #: ``mDesignPressure``. SEPARATE from ``head_lift_m`` on purpose: that field means "this
    #: is a pipeline pump", and the pump picker and the logistics filter both read it that
    #: way, so folding a refinery's 10 m into it would make a refinery a pump.
    machine_head_lift_m: float = 0.0

    @property
    def max_clock(self) -> float:
        from .constants import POTENTIAL_SHARD_SLOTS

        if not self.can_overclock:
            return 1.0
        return self.base_max_clock + POTENTIAL_SHARD_SLOTS * 0.5

    @property
    def is_manufacturer(self) -> bool:
        return self.native in MANUFACTURER_NATIVES

    @property
    def is_extractor(self) -> bool:
        return self.base_extract_rate > 0 or self.native in (
            "FGBuildableResourceExtractor",
            "FGBuildableWaterPump",
            "FGBuildableFrackingExtractor",
        )

    @property
    def is_generator(self) -> bool:
        return self.native.startswith("FGBuildableGenerator")

    def power_at(self, clock: float = 1.0, sloops: int = 0) -> float:
        """Consumption in MW at a given clock and somersloop count."""
        boost = self.boost_for(sloops)
        return self.power_mw * (clock**self.power_exponent) * (boost**self.boost_power_exponent)

    def boost_for(self, sloops: int) -> float:
        """Output multiplier from ``sloops`` somersloops. Capped at 2x everywhere."""
        from .constants import MAX_PRODUCTION_BOOST

        if not self.can_boost or sloops <= 0:
            return self.base_boost
        n = min(sloops, self.sloop_slots)
        return min(self.base_boost + n * self.sloop_mult, MAX_PRODUCTION_BOOST)

    def extract_rate(self, purity: str = "normal", clock: float = 1.0) -> float:
        from .constants import PURITY_MULT

        return self.base_extract_rate * PURITY_MULT[purity] * clock

    def fuel_rate_per_min(self, item: Item) -> float:
        """Fuel consumption per minute at 100% clock, in m3/min or items/min."""
        if not self.power_production_mw or not item.energy_mj:
            return 0.0
        return self.power_production_mw / item.energy_mj * 60

    def supplemental_m3_min(self) -> float:
        """Water (or other supplemental) demand in m3/min at 100% clock."""
        if not self.requires_supplemental:
            return 0.0
        return self.power_production_mw * self.supplemental_ratio * 60 / 1000


@dataclass(frozen=True)
class Schematic:
    cls: str
    name: str
    type: str  # EST_Milestone | EST_Alternate | EST_MAM | EST_ResourceSink | ...
    tier: int
    time_s: float
    cost: tuple[Flow, ...]
    unlocks_recipes: tuple[str, ...]
    unlocks_schematics: tuple[str, ...]
    dependencies: tuple[str, ...]
    events: str
    grants_inventory_slots: int = 0

    @property
    def is_alternate(self) -> bool:
        return self.type == "EST_Alternate"

    @property
    def is_retired(self) -> bool:
        """A node the game ships but no longer offers: named "Discontinued - ...", the
        "SPWN" prototype, or carrying no name at all."""
        return self.name in ("", self.cls, "SPWN") or self.name.startswith("Discontinued")


@dataclass
class GameData:
    items: dict[str, Item]
    recipes: dict[str, Recipe]
    buildings: dict[str, Building]
    schematics: dict[str, Schematic]
    docs_sha256: str
    warnings: list[str] = field(default_factory=list)
    _events: frozenset[str] | None = field(default=None, init=False, repr=False, compare=False)

    # ---- lookups -------------------------------------------------------

    def item_name(self, cls: str) -> str:
        """The item's display name; a building's for a building class; else the id in words."""
        it = self.items.get(cls)
        if it is not None and it.name != cls:
            return it.name
        return self.building_name(cls) or cls

    def event_items(self) -> frozenset[str]:
        """Items that belong to a seasonal event: made only by event recipes, or named for
        FICSMAS, which covers the Gift that no recipe makes."""
        if self._events is None:
            makers: dict[str, list[Recipe]] = {}
            for r in self.recipes.values():
                for f in r.products:
                    makers.setdefault(f.item, []).append(r)
            self._events = frozenset(
                cls
                for cls, item in self.items.items()
                if "FICSMAS" in item.name
                or (cls in makers and all(r.is_event for r in makers[cls]))
            )
        return self._events

    def building_name(self, cls: str | None) -> str | None:
        """The building's display name, or a readable rendering of its class id.

        ``None`` in, ``None`` out -- an occupant that is not there is not a building with an
        unknown name, and the callers that pass an optional id keep the difference.
        """
        b = self.buildings.get(cls or "") or self.buildings.get(
            BUILDING_CLASS_ALIASES.get(cls or "", "")
        )
        if b is not None:
            return b.name
        it = self.items.get(cls or "")
        if it is not None and it.name != cls:
            return it.name
        return CLASS_NAMES.get(cls or "") or pretty_class(cls)

    def clock_shards(self) -> dict[str, float]:
        """Item class -> max-clock added per unit slotted, for every shard that overclocks.
        ``FGPowerShardDescriptor`` holds two classes and only the Power Shard has
        ``mExtraPotential > 0``, so the filter excludes the Somersloop by data."""
        return {c: it.extra_potential for c, it in self.items.items() if it.extra_potential > 0}

    def slug_yields(self) -> dict[str, float]:
        """Item class -> Power Shards it crafts into, for every single-ingredient shard recipe.

        Restricted to ONE ingredient: Synthetic Power Shard also makes shards, but from Time
        Crystal, Dark Matter Crystal, Quartz and Photonic Matter, which is a production chain
        rather than something lying in a crate.
        """
        shard_items = set(self.clock_shards())
        out: dict[str, float] = {}
        for recipe in self.recipes.values():
            if recipe.kind != "part" or len(recipe.ingredients) != 1:
                continue
            made = sum(f.amount for f in recipe.products if f.item in shard_items)
            if not made:
                continue
            source = recipe.ingredients[0]
            if source.item in shard_items or not source.amount:
                continue
            out[source.item] = max(out.get(source.item, 0.0), made / source.amount)
        return out

    def part_recipes(self) -> list[Recipe]:
        return [r for r in self.recipes.values() if r.kind == "part"]

    def automatable(self, include_events: bool = False) -> list[Recipe]:
        return [r for r in self.part_recipes() if include_events or not r.is_event]

    def producers_of(self, item_cls: str, kind: str = "part") -> list[Recipe]:
        return [
            r
            for r in self.recipes.values()
            if r.kind == kind and any(f.item == item_cls for f in r.products)
        ]

    def consumers_of(self, item_cls: str, kind: str = "part") -> list[Recipe]:
        return [
            r
            for r in self.recipes.values()
            if r.kind == kind and any(f.item == item_cls for f in r.ingredients)
        ]

    def alternates(self) -> list[Recipe]:
        return [r for r in self.recipes.values() if r.is_alternate]

    def machine(self, recipe: Recipe) -> Building | None:
        return self.buildings.get(recipe.machine) if recipe.machine else None

    def recipe_power_mw(self, recipe: Recipe, clock: float = 1.0, sloops: int = 0) -> float:
        """MW drawn by one machine running ``recipe``.

        Variable-power machines (Particle Accelerator, Converter, Quantum Encoder)
        have mPowerConsumption == 0 and take their draw from the recipe instead.
        """
        b = self.machine(recipe)
        if b is None:
            return 0.0
        if recipe.is_variable_power:
            base = recipe.power_avg_mw
            boost = b.boost_for(sloops)
            return base * (clock**b.power_exponent) * (boost**b.boost_power_exponent)
        return b.power_at(clock, sloops)

    def builds(self, building_cls: str) -> Recipe | None:
        """The build-gun recipe that constructs a building, for build costs."""
        b = self.buildings.get(building_cls)
        if b is None or not b.descriptor:
            return None
        for r in self.recipes.values():
            if r.kind == "building" and any(f.item == b.descriptor for f in r.products):
                return r
        return None

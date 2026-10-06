"""The game values this project uses that are absent from or overriding Docs.json.

Every other rate, power figure and capacity is a cited Docs.json field. Each value here is
tagged with where it comes from -- ``[WIKI]``, ``[MEASURED]`` or the data that proves it -- and
the evidence lives in DESIGN.md §5.6, ``docs/fluids_model.md`` and ``docs/planning.md``.
"""

from __future__ import annotations

import math

#: Extraction multiplier by node purity; proven by every extractor's ``mDescription`` (§5.6).
PURITY_MULT: dict[str, float] = {"impure": 0.5, "normal": 1.0, "pure": 2.0}

#: Power Shard slots per building [WIKI]: ``mPotentialShardSlots`` is 0 everywhere (§5.6).
POTENTIAL_SHARD_SLOTS: int = 3


def max_clock(extra_potential_per_shard: float, base: float = 1.0) -> float:
    """Max clock with every shard slot filled: ``base`` plus what the slotted shards add."""
    return base + POTENTIAL_SHARD_SLOTS * extra_potential_per_shard


def shards_for_clock(clock: float, extra_potential_per_shard: float) -> int:
    """Shards a building needs slotted to be *allowed* to run at ``clock``: a lower bound,
    because a shard raises the maximum and the slider goes anywhere below it."""
    if extra_potential_per_shard <= 0 or clock <= 1.0:
        return 0
    # round() before ceil(): a saved 2.0 arrives as 1.9999999 often enough to bill a 4th shard.
    need = math.ceil(round((clock - 1.0) / extra_potential_per_shard, 6))
    return min(need, POTENTIAL_SHARD_SLOTS)


#: Conveyor ``mSpeed`` -> items/min; cross-checked against each belt's ``mDescription``.
BELT_SPEED_TO_IPM: float = 0.5

#: Overrides Docs.json, which gives fluids sink points: the AWESOME Sink is conveyor-only (§5.6).
FLUIDS_CANNOT_BE_SUNK: bool = True

#: Power draw of one AWESOME Sink, from Docs.json; here so the sink model reads in one place.
AWESOME_SINK_MW: float = 30.0

#: Somersloop amplification cap on every building: 2x output for 4x power.
MAX_PRODUCTION_BOOST: float = 2.0

#: Stack sizes per ``mStackSize`` enum, which tell a starved machine from a blocked one;
#: Docs.json gives only the symbol. A machine's fluid buffer holds 50 m3, in save litres.
STACK_SIZE: dict[str, int] = {
    "SS_ONE": 1,
    "SS_SMALL": 50,
    "SS_MEDIUM": 100,
    "SS_BIG": 200,
    "SS_HUGE": 500,
    "SS_FLUID": 50_000,
}

#: Save building class -> the class Docs.json describes it under. The HUB's integrated burner
#: is NOT aliased: it has no build recipe, and folding it in credits generators never placed.
BUILDING_CLASS_ALIASES: dict[str, str] = {
    "Build_GeneratorBiomass_C": "Build_GeneratorBiomass_Automated_C",
}

#: Display names for classes a save holds and Docs.json never describes; every other
#: undescribed class reads as its own id in words, through ``pretty_class``.
CLASS_NAMES: dict[str, str] = {
    "Desc_ResourceSinkCoupon_C": "FICSIT Coupon",
    "Build_GeneratorIntegratedBiomass_C": "HUB Biomass Burner",
    "Build_StorageIntegrated_C": "HUB Storage",
    "Build_StorageBlueprint_C": "Blueprint Designer Storage",
}

#: MAM research that grants each gated capability. A cross-check: the save's own unlock flags
#: are authoritative where present (``docs/save-projection.md`` §6.9).
CAPABILITY_SCHEMATICS: dict[str, str] = {
    #: Somersloops in production machines: 2x output for 4x power.
    "production_boost": "Research_Alien_ProductionBooster_C",
    #: The Alien Power Augmenter building, which is a different use of the same item.
    "power_augmenter": "Research_Alien_PowerBooster_C",
}

#: Head lift, in metres, a fluid buffer needs to push out as fast as it takes in [WIKI]
#: (``docs/plumbing.md``).
BUFFER_BALANCE_HEAD_M: float = 1.5

#: Fill fraction at and above which a buffer passes incoming head lift on unchanged
#: [MEASURED] (``docs/fluids_model.md``). Spelled ``>=`` and never an equality: the game
#: overfills, and every reading measured transmitting is strictly above 1.0.
BUFFER_TRANSMITS_ABOVE_FILL: float = 1.0

#: The fills the step was measured between, off then on; ``HeadLift.undecided_buffers``
#: counts the verdicts that rested on a fill inside it [MEASURED].
BUFFER_TRANSMIT_BRACKET: tuple[float, float] = (0.99903, 1.00604)

#: Head lift, in metres, of any machine that is not a pipeline pump [WIKI]; Docs.json exports
#: the ``FluidBox`` struct empty (``docs/plumbing.md`` §24.2).
MACHINE_HEAD_LIFT_M: float = 10.0

#: Height, in metres, at which a machine's flow drops to zero [MEASURED]
#: (``docs/fluids_model.md``, "A machine's ceiling is ≈ 11 m, not 12").
MACHINE_MAX_HEAD_LIFT_M: float = 11.020

#: How high a pump's fluid actually stands above its centre, per build class [MEASURED]; the
#: declared ``mMaxPressure`` stays what ``list_buildings`` reports, and a class absent here is
#: not extrapolated (``docs/fluids_model.md``, "A pump exceeds even its ceiling").
PUMP_MEASURED_REACH_M: dict[str, float] = {
    "Build_PipelinePump_C": 22.801,
}

#: Water Extractors the planner assumes can be sited when the caller does not say: the one
#: number with no data behind it, and NOT a capacity (``docs/planning.md``).
WATER_EXTRACTOR_CAP_ASSUMED: int = 200

#: Extractors in one plan above which the count is called an assumption and the platform
#: cost quoted (``docs/planning.md``).
WATER_EXTRACTOR_WARN_AT: int = 30

#: Water and the building that pumps it: water has no nodes, only water volumes.
WATER: str = "Desc_Water_C"
WATER_PUMP: str = "Build_WaterPump_C"

#: The extractor key every Water Extractor is counted under: water has no purity.
WATER_EXTRACTOR_KEY: tuple[str, str, str] = (WATER_PUMP, WATER, "normal")

#: Stand-in for an unlimited raw-input rate: a cap the LP can price without being unbounded.
UNLIMITED_RATE: float = 1e7

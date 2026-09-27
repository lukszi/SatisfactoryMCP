"""The only values in this project that are NOT read from game data.

Every other rate, power figure and capacity is a cited Docs.json field; the values here are
absent from the dump altogether, so they are pinned here and unit-tested. Do not add to this
register without the same treatment.
"""

from __future__ import annotations

import math

#: Extraction rate multiplier by node purity. Not in Docs.json, but proven by it: every
#: extractor's ``mDescription`` states its base rate as the *normal*-node rate, which
#: ``mItemsPerCycle * 60 / mExtractCycleTime`` reproduces exactly. Asserted at build time.
PURITY_MULT: dict[str, float] = {"impure": 0.5, "normal": 1.0, "pure": 2.0}

#: Power Shard slots per building, giving a 250% max clock. [WIKI]: ``mPotentialShardSlots``
#: is 0 on every building in the dump. The two functions below are the only places this is
#: combined with data and stay beside it rather than at the bottom of the file.
POTENTIAL_SHARD_SLOTS: int = 3


#: Max clock a building can be set to. A formula rather than a literal 2.5 so a patch to
#: ``mExtraPotential`` flows through.
def max_clock(extra_potential_per_shard: float) -> float:
    return 1.0 + POTENTIAL_SHARD_SLOTS * extra_potential_per_shard


#: Shards a building needs slotted to be *allowed* to run at ``clock`` -- a lower bound on
#: what is installed, never an equality. A shard raises the MAXIMUM potential and the player
#: then drags the slider anywhere below it, so committed shards are read from
#: ``InventoryPotential`` rather than derived from a clock.
def shards_for_clock(clock: float, extra_potential_per_shard: float) -> int:
    if extra_potential_per_shard <= 0 or clock <= 1.0:
        return 0
    # round() before ceil(): saved clocks are floats, and 2.0 arrives as 1.9999999 often
    # enough that a bare ceil() would demand a fourth shard for a 200% machine.
    need = math.ceil(round((clock - 1.0) / extra_potential_per_shard, 6))
    return min(need, POTENTIAL_SHARD_SLOTS)


#: Conveyor ``mSpeed`` -> items/min. Cross-checked against each belt's own ``mDescription``
#: prose at build time, so the assertion is self-contained.
BELT_SPEED_TO_IPM: float = 0.5

#: Fluids cannot be sunk or discarded, so a fluid byproduct must be consumed exactly. This
#: CONTRADICTS Docs.json, which gives Heavy Oil Residue 30 sink points and
#: ``mCanBeDiscarded = True``: the AWESOME Sink's input is conveyor-only, so a fluid must be
#: packaged into a solid first. Confirmed by the player.
FLUIDS_CANNOT_BE_SUNK: bool = True

#: Power draw of one AWESOME Sink, charged whenever a plan sinks anything. From Docs.json;
#: kept here so the sink model reads in one place.
AWESOME_SINK_MW: float = 30.0

#: Somersloop amplification is capped at 2x output for 4x power on every building.
MAX_PRODUCTION_BOOST: float = 2.0


#: Stack sizes by the enum Docs.json reports. The dump gives only the symbol, so the numbers
#: are game knowledge. Needed to tell a STARVED machine from a BLOCKED one, which need
#: opposite fixes.
STACK_SIZE: dict[str, int] = {
    "SS_ONE": 1,
    "SS_SMALL": 50,
    "SS_MEDIUM": 100,
    "SS_BIG": 200,
    "SS_HUGE": 500,
    # Fluid buffers are quoted in litres in the save, and a machine's fluid buffer holds 50 m3.
    "SS_FLUID": 50_000,
}


#: Save building class -> the class Docs.json uses for the same building. The dump names the
#: Biomass Burner by its build recipe's Build_GeneratorBiomass_Automated_C while the save
#: stores standing burners as Build_GeneratorBiomass_C. Build_GeneratorIntegratedBiomass_C is
#: NOT aliased: it is the HUB's built-in burner, has no build recipe, and folding it in would
#: credit the player with generators they never placed.
BUILDING_CLASS_ALIASES: dict[str, str] = {
    "Build_GeneratorBiomass_C": "Build_GeneratorBiomass_Automated_C",
}


#: Display names for classes a save holds and Docs.json never describes: the coupon is only
#: ever referenced there, and the three HUB and Blueprint Designer parts ship no entry. Every
#: other undescribed class reads as its own id in words, through ``pretty_class``.
CLASS_NAMES: dict[str, str] = {
    "Desc_ResourceSinkCoupon_C": "FICSIT Coupon",
    "Build_GeneratorIntegratedBiomass_C": "HUB Biomass Burner",
    "Build_StorageIntegrated_C": "HUB Storage",
    "Build_StorageBlueprint_C": "Blueprint Designer Storage",
}


#: Capabilities the game gates behind MAM research, and the schematic that grants each. A
#: cross-check, not the primary source: ``BP_UnlockSubsystem_C``'s own flags are authoritative
#: where present (§6, `docs/save-projection.md`). This register answers the other half --
#: *which research to go and do* -- and covers a projection written before the flag existed.
CAPABILITY_SCHEMATICS: dict[str, str] = {
    #: Somersloops in production machines: 2x output for 4x power.
    "production_boost": "Research_Alien_ProductionBooster_C",
    #: The Alien Power Augmenter building, which is a different use of the same item.
    "power_augmenter": "Research_Alien_PowerBooster_C",
}

#: Head lift a fluid buffer needs to push out as fast as it takes in, in metres. A buffer's
#: own head lift is the height of the fluid standing in it, so this converts to a level
#: through the class's capacity and height -- both of which Docs.json does state. [WIKI]:
#: the FICSIT Plumbing Manual; nothing in the dump carries it. See `docs/plumbing.md`.
BUFFER_BALANCE_HEAD_M: float = 1.5

#: Fill fraction at and above which a buffer passes INCOMING head lift on unchanged. Below it
#: the line above a buffer gets only the buffer's own head -- its fill-proportional surface or
#: its connectors 1.75 m up its side, whichever is the higher, since ``BUF_OUT`` measured one
#: delivering at 4.3% fill. A step rather than a blend, and a blend is excluded arithmetically
#: by 11 m. [MEASURED], see `docs/fluids_model.md`.
#: The bracket below is 0.7 points wide and CONTAINS this value, so
#: capacity is where the step was measured rather than a conservative reading of a wide
#: bracket. Spelled ``>=`` and never an equality: the game overfills, every reading measured
#: transmitting is strictly above 1.0 and the highest measured not transmitting is 0.99903.
BUFFER_TRANSMITS_ABOVE_FILL: float = 1.0

#: The two fills the step was measured between: off at the first, on at the second. A buffer
#: inside this band is decided by the constant above rather than by a measurement, and
#: ``HeadLift.undecided_buffers`` counts how many the verdict rested on.
BUFFER_TRANSMIT_BRACKET: tuple[float, float] = (0.99903, 1.00604)

#: Head lift any machine that is not a pipeline pump gives, in metres. [WIKI]: the FICSIT
#: Plumbing Manual -- head lift outside a pump lives in the ``FluidBox`` struct, which
#: Docs.json exports empty, so ``mDesignPressure`` exists on the pump class and on no other
#: of 2,868. See `docs/plumbing.md` §24.2.
MACHINE_HEAD_LIFT_M: float = 10.0

#: The height at which that flow drops to zero. [MEASURED], not read and not the rating times
#: a tolerance: a capped dead-end column off a Water Extractor settled 11.020 m +-0.26 above
#: its pipe connection (`docs/fluids_model.md`). "12 m", which this was, appears in none of
#: the dump's 2,868 classes. SCOPE: the dump states the same 10 m rating for six classes, and
#: an Oil Refinery on a different fluid measured 11.087 -- 67 mm away, inside the bar -- so
#: one number for all six rests on two classes and two fluids rather than on one of each.
MACHINE_MAX_HEAD_LIFT_M: float = 11.020

#: How high a pump's fluid actually stands above its centre, per BUILD class, where that has
#: been measured. NOT a correction to the dump: ``mMaxPressure`` stays what the game DECLARES
#: and keeps being what `list_buildings` reports, while this is what the game was observed to
#: DO, and the two differ -- a Mk1 reaches 0.80 m past its own declared 22 m ceiling
#: (`docs/fluids_model.md`).
#:
#: Per class because no single multiplier fits: the machine sits at x1.102 of its rating and
#: the Mk1 at x1.140 of its. A class absent here is NOT extrapolated -- the Mk2 measured
#: 55.564 against its declared 55 and is still left out, because both pump readings are
#: quantised by the 4 m pieces they were taken on and a finer rig puts the Mk1 nearer 23.0
#: (`docs/fluids_model.md`). Understating a reach only reports a climb as harder than it is.
PUMP_MEASURED_REACH_M: dict[str, float] = {
    "Build_PipelinePump_C": 22.801,
}

#: Water Extractors the planner assumes can be sited, when the caller does not say.
#:
#: The only number here with no data behind it, and DANGEROUS to read as capacity. Water is
#: drawn from FGWaterVolume objects -- ocean, lakes -- which carry no node entry, no purity
#: and no geometry, so this exists only to keep the column bounded. It is NOT too high to
#: bind: a whole-map ``max_mw`` takes every one of the 200 and wants 246. Terrain does not
#: fix that -- submerged area is not an extractor count, since shoreline geometry, clearance
#: and overlap are level data nothing here reads -- so the number stays an assumption and
#: every plan that pumps says so, says whether it is binding, and quotes what was measured
#: at its site. Pass ``water_extractors`` to replace it with a number the player measured.
WATER_EXTRACTOR_CAP_ASSUMED: int = 200

#: Above this many extractors in one plan, say plainly that the count is an assumption and
#: quote what the platform costs. Not a danger threshold -- platforming for hundreds is
#: ordinary play -- just where the concrete stops being a rounding error.
WATER_EXTRACTOR_WARN_AT: int = 30

"""Turn a DocsDump into the normalized GameData model.

Cold build is ~80 ms for the whole 10.6 MB dump, so this runs at server startup and
needs no disk cache.

Invariant drift is collected into ``GameData.warnings`` rather than raised: a game
patch should degrade the server, not kill it. The test suite asserts warnings is
empty, so drift is still caught loudly in CI.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..jsontypes import JsonObject, JsonValue
from .constants import BELT_SPEED_TO_IPM, PURITY_MULT, max_clock
from .footprint import extract_footprint
from .loader import DocsDump
from .model import (
    MANUFACTURER_NATIVES,
    Building,
    Flow,
    Fuel,
    GameData,
    Item,
    Recipe,
    Schematic,
)
from .uestruct import amount, as_float, as_list, obj_class, parse_struct

__all__ = ["normalize"]

_CLASS_RE = re.compile(r"[\w/\-.]+?\.(\w+_C)\b")
_FLUID_FORMS = ("RF_LIQUID", "RF_GAS")


def _text(docs_class: JsonObject, key: str) -> str:
    """A field the dump writes as a string, such as ``ClassName``; ``""`` when absent."""
    value = docs_class.get(key)
    return value if isinstance(value, str) else ""


def _as_bool(raw: JsonValue, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() == "true"


def _as_int(raw: JsonValue, default: int = 0) -> int:
    return int(as_float(raw, default))


def _classes_in(raw: JsonValue) -> tuple[str, ...]:
    """Extract every ``*_C`` class name from a UE path list.

    Tries the struct parser first and falls back to a regex, because a few of these
    fields are bare comma-separated paths rather than parenthesised lists.
    """
    if not raw:
        return ()
    out: list[str] = []
    try:
        parsed = parse_struct(raw)
    except Exception:
        parsed = None
    if parsed is not None:
        for entry in as_list(parsed):
            if isinstance(entry, str):
                class_name = obj_class(entry)
                if class_name:
                    out.append(class_name)
            elif isinstance(entry, dict):
                for value in entry.values():
                    class_name = obj_class(value) if isinstance(value, str) else None
                    if class_name:
                        out.append(class_name)
    if not out and isinstance(raw, str):
        out = _CLASS_RE.findall(raw)
    seen: dict[str, None] = {}
    for class_name in out:
        seen.setdefault(class_name, None)
    return tuple(seen)


# --------------------------------------------------------------------------- items


def _build_items(dump: DocsDump) -> dict[str, Item]:
    """Every class carrying ``mForm``.

    Scans all natives, not just FGItemDescriptor/FGResourceDescriptor: 13 natives
    carry mForm, and restricting would miss Desc_LiquidBiofuel_C (biomass) and the
    three nuclear fuel rods. RF_INVALID classes (building/vehicle descriptors) are
    kept because building recipes reference them as products.
    """
    items: dict[str, Item] = {}
    shown = {
        name: str(docs_class["mDisplayName"])
        for classes in dump.by_native.values()
        for docs_class in classes
        if (name := _text(docs_class, "ClassName")) and docs_class.get("mDisplayName")
    }
    for native, classes in dump.by_native.items():
        for docs_class in classes:
            if "mForm" not in docs_class or "ClassName" not in docs_class:
                continue
            form = str(docs_class.get("mForm") or "RF_INVALID")
            energy = as_float(docs_class.get("mEnergyValue"))
            if form in _FLUID_FORMS:
                energy *= 1000  # mEnergyValue is MJ per litre for fluids
            cls = _text(docs_class, "ClassName")
            built = "Build_" + cls[len("Desc_") :] if cls.startswith("Desc_") else ""
            items[cls] = Item(
                cls=cls,
                name=str(docs_class.get("mDisplayName") or shown.get(built) or cls),
                native=native,
                form=form,
                energy_mj=energy,
                stack_size=str(docs_class.get("mStackSize") or ""),
                sink_points=_as_int(docs_class.get("mResourceSinkPoints")),
                can_be_discarded=_as_bool(docs_class.get("mCanBeDiscarded"), True),
                is_resource=native == "FGResourceDescriptor",
                # Absent on all but the two FGPowerShardDescriptor classes, so the
                # default 0.0 is the right answer everywhere else.
                extra_potential=as_float(docs_class.get("mExtraPotential")),
            )
    return items


# ----------------------------------------------------------------------- buildings


def _descriptor_for(build_cls: str, descriptors: dict[str, Item]) -> str | None:
    """Join ``Build_X_C`` to its ``Desc_X_C``.

    No linking field exists in Docs.json. The name convention hits 516/547; a
    sorted-token fallback (Desc_Wall_Concrete_8x1_Tris_C <-> Build_Wall_Concrete_Tris_8x1_C)
    adds 18. The 13 that never map are pure cosmetics; all production buildables map.
    """
    if not build_cls.startswith("Build_"):
        return None
    direct = "Desc_" + build_cls[len("Build_") :]
    if direct in descriptors:
        return direct
    want = frozenset(build_cls[len("Build_") : -2].lower().split("_"))
    for cls in descriptors:
        if frozenset(cls[len("Desc_") : -2].lower().split("_")) == want:
            return cls
    return None


def _fuels(raw: JsonValue) -> tuple[Fuel, ...]:
    """Parse ``mFuel``, which arrives as real JSON (a list of dicts)."""
    out: list[Fuel] = []
    for entry in as_list(raw if isinstance(raw, (list, dict)) else parse_struct(raw)):
        if not isinstance(entry, dict):
            continue
        fuel_class = obj_class(entry.get("mFuelClass"))
        if not fuel_class:
            continue
        out.append(
            Fuel(
                fuel_class=fuel_class,
                supplemental_class=obj_class(entry.get("mSupplementalResourceClass")),
                byproduct_class=obj_class(entry.get("mByproduct")),
                byproduct_amount=as_float(entry.get("mByproductAmount")),
            )
        )
    return tuple(out)


#: "Head Lift: 10 m" as the dump actually writes it. The space before the unit is U+202F, a
#: NARROW NO-BREAK SPACE, so ``\s`` is required here and a literal " m" matches nothing.
_HEAD_LIFT_RE = re.compile(r"Head\s*Lift:\s*([\d.]+)\s*m", re.IGNORECASE)


def _stated_head_lift(desc: JsonValue) -> float:
    found = _HEAD_LIFT_RE.search(str(desc or ""))
    return float(found.group(1)) if found else 0.0


#: Natives outside the FGBuildable* family whose classes are still placed buildings.
_OTHER_BUILDABLES = ("FGCentralStorageContainer",)


def _build_buildings(dump: DocsDump, items: dict[str, Item]) -> dict[str, Building]:
    descriptors = {
        cls: item for cls, item in items.items() if item.native == "FGBuildingDescriptor"
    }
    # The Power Shard's ``mExtraPotential``; filtering on > 0 leaves the Somersloop out.
    per_shard = max(
        (item.extra_potential for item in items.values() if item.extra_potential > 0), default=0.0
    )
    out: dict[str, Building] = {}
    for native, classes in dump.by_native.items():
        if not native.startswith("FGBuildable") and native not in _OTHER_BUILDABLES:
            continue
        for docs_class in classes:
            cls = _text(docs_class, "ClassName")
            if not cls:
                continue

            # Somersloop slots: read the override flag, because Smelter carries a
            # stale 0/False pair that would otherwise zero its single slot.
            override = _as_bool(docs_class.get("mOverrideProductionShardSlotSize"))
            slots = _as_int(docs_class.get("mProductionShardSlotSize"), 1) if override else 1
            mult = (
                as_float(docs_class.get("mProductionShardBoostMultiplier"), 1.0)
                if override
                else 1.0
            )

            items_per_cycle = as_float(docs_class.get("mItemsPerCycle"))
            cycle_s = as_float(docs_class.get("mExtractCycleTime"))
            forms = tuple(
                form
                for form in _split_enum(docs_class.get("mAllowedResourceForms"))
                if form.startswith("RF_")
            )
            base_rate = 0.0
            if items_per_cycle and cycle_s:
                base_rate = items_per_cycle * 60 / cycle_s
                # The /1000 is a fluid-only unit conversion. Applying it to the solid
                # miners in the same native group is 1000x wrong (60 -> 0.06).
                if any(f in _FLUID_FORMS for f in forms):
                    base_rate /= 1000

            speed = as_float(docs_class.get("mSpeed"))
            can_overclock = _as_bool(docs_class.get("mCanChangePotential"))
            base_max_clock = as_float(docs_class.get("mMaxPotential"), 1.0)
            out[cls] = Building(
                cls=cls,
                name=str(docs_class.get("mDisplayName") or cls),
                native=native,
                power_mw=as_float(docs_class.get("mPowerConsumption")),
                power_exponent=as_float(docs_class.get("mPowerConsumptionExponent"), 1.0),
                boost_power_exponent=as_float(
                    docs_class.get("mProductionBoostPowerConsumptionExponent"), 1.0
                ),
                can_overclock=can_overclock,
                min_clock=as_float(docs_class.get("mMinPotential"), 0.01),
                base_max_clock=base_max_clock,
                can_boost=_as_bool(docs_class.get("mCanChangeProductionBoost")),
                sloop_slots=slots,
                sloop_mult=mult,
                base_boost=as_float(docs_class.get("mBaseProductionBoost"), 1.0),
                mfg_speed=as_float(docs_class.get("mManufacturingSpeed"), 1.0),
                max_clock=max_clock(per_shard, base_max_clock) if can_overclock else 1.0,
                descriptor=_descriptor_for(cls, descriptors),
                items_per_cycle=items_per_cycle,
                extract_cycle_s=cycle_s,
                base_extract_rate=base_rate,
                allowed_resources=_classes_in(docs_class.get("mAllowedResources")),
                allowed_forms=forms,
                power_production_mw=as_float(docs_class.get("mPowerProduction")),
                variable_power_factor=as_float(docs_class.get("mVariablePowerProductionFactor")),
                requires_supplemental=_as_bool(docs_class.get("mRequiresSupplementalResource")),
                supplemental_ratio=as_float(docs_class.get("mSupplementalToPowerRatio")),
                fuels=_fuels(docs_class.get("mFuel")),
                items_per_min=speed * BELT_SPEED_TO_IPM if speed else 0.0,
                flow_m3_min=as_float(docs_class.get("mFlowLimit")) * 60,
                storage_capacity_m3=as_float(docs_class.get("mStorageCapacity")),
                head_lift_m=as_float(docs_class.get("mDesignPressure")),
                max_head_lift_m=as_float(docs_class.get("mMaxPressure")),
                machine_head_lift_m=_stated_head_lift(docs_class.get("mDescription")),
                footprint=extract_footprint(docs_class.get("mClearanceData")),
            )
    return out


def _split_enum(raw: JsonValue) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = parse_struct(raw)
    except Exception:
        return ()
    values = as_list(parsed)
    out: list[str] = []
    for value in values:
        if isinstance(value, str):
            out.append(value.strip())
        elif isinstance(value, dict):
            out.extend(str(member).strip() for member in value.values() if isinstance(member, str))
    return tuple(out)


# ---------------------------------------------------------------------- schematics


def _build_schematics(dump: DocsDump) -> dict[str, Schematic]:
    out: dict[str, Schematic] = {}
    for docs_class in dump.classes("FGSchematic"):
        cls = _text(docs_class, "ClassName")
        if not cls:
            continue
        recipes: list[str] = []
        schematics: list[str] = []
        slots = 0
        # mUnlocks arrives as real JSON: a list of dicts, each with a 'Class' key.
        for unlock in as_list(docs_class.get("mUnlocks") or []):
            if not isinstance(unlock, dict):
                continue
            unlock_class = str(unlock.get("Class") or "")
            if unlock_class in ("BP_UnlockRecipe_C", "BP_UnlockBlueprints_C"):
                recipes.extend(_classes_in(unlock.get("mRecipes")))
            elif unlock_class == "BP_UnlockSchematic_C":
                # Recorded, never expanded here: recursive expansion pulls in 23
                # CBG_* customization schematics and inflates the derived recipe
                # set to 514, of which 113 are not actually unlocked.
                schematics.extend(_classes_in(unlock.get("mSchematics")))
            elif unlock_class == "BP_UnlockInventorySlot_C":
                slots += _as_int(unlock.get("mNumInventorySlotsToUnlock"))

        deps: list[str] = []
        for dependency in as_list(docs_class.get("mSchematicDependencies") or []):
            if (
                isinstance(dependency, dict)
                and dependency.get("Class") == "BP_SchematicPurchasedDependency_C"
            ):
                deps.extend(_classes_in(dependency.get("mSchematics")))

        cost = tuple(
            Flow(
                item=obj_class(cost_entry.get("ItemClass")) or "?",
                amount=amount(cost_entry),
                per_min=0.0,
            )
            for cost_entry in as_list(parse_struct(docs_class.get("mCost")))
            if isinstance(cost_entry, dict)
        )
        out[cls] = Schematic(
            cls=cls,
            name=str(docs_class.get("mDisplayName") or cls),
            type=str(docs_class.get("mType") or ""),
            tier=_as_int(docs_class.get("mTechTier")),
            time_s=as_float(docs_class.get("mTimeToComplete")),
            cost=cost,
            unlocks_recipes=tuple(dict.fromkeys(recipes)),
            unlocks_schematics=tuple(dict.fromkeys(schematics)),
            dependencies=tuple(dict.fromkeys(deps)),
            events=str(docs_class.get("mRelevantEvents") or ""),
            grants_inventory_slots=slots,
        )
    return out


# ------------------------------------------------------------------------- recipes


def _flows(raw: JsonValue, items: dict[str, Item], duration: float) -> tuple[Flow, ...]:
    out: list[Flow] = []
    for entry in as_list(parse_struct(raw)):
        if not isinstance(entry, dict):
            continue
        cls = obj_class(entry.get("ItemClass"))
        if not cls:
            continue
        quantity = amount(entry)
        item = items.get(cls)
        if item is not None and item.is_fluid:
            quantity = quantity / 1000.0  # float: Recipe_Battery_C has SulfuricAcid=2500
        per_min = quantity * 60 / duration if duration else 0.0
        out.append(Flow(item=cls, amount=quantity, per_min=per_min))
    return tuple(out)


def _build_recipes(
    dump: DocsDump, items: dict[str, Item], buildings: dict[str, Building]
) -> dict[str, Recipe]:
    building_descs = {cls for cls, item in items.items() if item.native == "FGBuildingDescriptor"}
    manufacturers = {
        building_cls
        for building_cls, building in buildings.items()
        if building.native in MANUFACTURER_NATIVES
    }
    variable = {
        building_cls
        for building_cls, building in buildings.items()
        if building.native == "FGBuildableManufacturerVariablePower"
    }

    out: dict[str, Recipe] = {}
    for docs_class in dump.classes("FGRecipe"):
        cls = _text(docs_class, "ClassName")
        if not cls:
            continue
        duration = as_float(docs_class.get("mManufactoringDuration"))  # typo is in the game data
        products = _flows(docs_class.get("mProduct"), items, duration)
        ingredients = _flows(docs_class.get("mIngredients"), items, duration)
        produced_in = _classes_in(docs_class.get("mProducedIn"))

        machines = [p for p in produced_in if p in manufacturers]
        if products and products[0].item in building_descs:
            kind, machine = "building", None
        elif machines:
            kind, machine = "part", machines[0]
        else:
            kind, machine = "manual", None

        power_min = power_max = 0.0
        if machine in variable:
            const = as_float(docs_class.get("mVariablePowerConsumptionConstant"))
            factor = as_float(docs_class.get("mVariablePowerConsumptionFactor"))
            # Factor is a RANGE, not a multiplier: building-level
            # mEstimatedMininum/MaximumPowerConsumption exactly bracket const..const+factor.
            power_min, power_max = const, const + factor

        out[cls] = Recipe(
            cls=cls,
            name=str(docs_class.get("mDisplayName") or cls),
            kind=kind,
            machine=machine,
            duration_s=duration,
            ingredients=ingredients,
            products=products,
            manual_mult=as_float(docs_class.get("mManualManufacturingMultiplier"), 1.0),
            power_min_mw=power_min,
            power_max_mw=power_max,
            events=str(docs_class.get("mRelevantEvents") or ""),
        )
    return out


# ---------------------------------------------------------------------- assertions


def _warn_recipe_partition(data: GameData) -> list[str]:
    warnings: list[str] = []
    counts: dict[str, int] = {}
    for recipe in data.recipes.values():
        counts[recipe.kind] = counts.get(recipe.kind, 0) + 1
    total = len(data.recipes)
    if total != sum(counts.values()):
        warnings.append(f"recipe partition lost entries: {total} != {counts}")
    if counts.get("part", 0) == 0:
        warnings.append("no part recipes found -- mProducedIn / manufacturer join is broken")
    return warnings


def _warn_belt_rates(data: GameData, dump: DocsDump) -> list[str]:
    """Belts state their own rate in prose, so ``BELT_SPEED_TO_IPM`` is self-checking."""
    warnings: list[str] = []
    for entry in dump.classes("FGBuildableConveyorBelt"):
        belt = data.buildings.get(_text(entry, "ClassName"))
        desc = str(entry.get("mDescription") or "")
        found = re.search(r"(\d[\d\s,]*)\s*(?:items|resources)?\s*per minute", desc, re.IGNORECASE)
        if belt and found:
            stated = float(found.group(1).replace(",", "").replace(" ", ""))
            if abs(stated - belt.items_per_min) > 0.5:
                warnings.append(
                    f"{belt.cls}: computed {belt.items_per_min}/min but description says {stated}"
                )
    return warnings


def _warn_pipe_rates(data: GameData, dump: DocsDump) -> list[str]:
    warnings: list[str] = []
    for entry in dump.classes("FGBuildablePipeline"):
        pipe = data.buildings.get(_text(entry, "ClassName"))
        desc = str(entry.get("mDescription") or "")
        found = re.search(r"(\d+)\s*m.{0,4}\s*of fluid per minute", desc, re.IGNORECASE)
        if pipe and found and abs(float(found.group(1)) - pipe.flow_m3_min) > 0.5:
            warnings.append(
                f"{pipe.cls}: computed {pipe.flow_m3_min} m3/min, description says {found.group(1)}"
            )
    return warnings


def _warn_pump_head_lift(data: GameData, dump: DocsDump) -> list[str]:
    """The prose parse that rates every machine, checked on the classes that also carry
    ``mDesignPressure``."""
    warnings: list[str] = []
    for entry in dump.classes("FGBuildablePipelinePump"):
        pump = data.buildings.get(_text(entry, "ClassName"))
        if (
            pump
            and pump.machine_head_lift_m
            and abs(pump.machine_head_lift_m - pump.head_lift_m) > 0.01
        ):
            warnings.append(
                f"{pump.cls}: mDesignPressure {pump.head_lift_m} m but description says "
                f"{pump.machine_head_lift_m} m"
            )
    return warnings


def _warn_extractor_rates(data: GameData, dump: DocsDump) -> list[str]:
    """Extractor descriptions state the NORMAL-purity rate, which is what pins
    ``PURITY_MULT``."""
    warnings: list[str] = []
    for native in ("FGBuildableResourceExtractor", "FGBuildableWaterPump"):
        for entry in dump.classes(native):
            extractor = data.buildings.get(_text(entry, "ClassName"))
            desc = str(entry.get("mDescription") or "")
            found = re.search(
                r"(\d+)\s*(?:resources|m.{0,4} of \w+)\s*per minute", desc, re.IGNORECASE
            )
            if extractor and found and extractor.base_extract_rate:
                stated = float(found.group(1))
                got = extractor.base_extract_rate * PURITY_MULT["normal"]
                if abs(stated - got) > 0.5:
                    warnings.append(
                        f"{extractor.cls}: normal rate {got}/min but description says {stated}"
                    )
    return warnings


def _warn_unjoined_buildings(data: GameData) -> list[str]:
    """A production building with no descriptor silently loses its build cost."""
    warnings: list[str] = []
    for building in data.buildings.values():
        producing = building.is_manufacturer or building.is_extractor or building.is_generator
        if producing and not building.descriptor:
            warnings.append(
                f"{building.cls}: no FGBuildingDescriptor match, build cost unavailable"
            )
        if (building.is_manufacturer or building.is_generator) and building.footprint is None:
            warnings.append(f"{building.cls}: no clearance data, cannot size a layout")
    return warnings


def _collect_drift_warnings(data: GameData, dump: DocsDump) -> None:
    data.warnings.extend(_warn_recipe_partition(data))
    data.warnings.extend(_warn_belt_rates(data, dump))
    data.warnings.extend(_warn_pipe_rates(data, dump))
    data.warnings.extend(_warn_pump_head_lift(data, dump))
    data.warnings.extend(_warn_extractor_rates(data, dump))
    data.warnings.extend(_warn_unjoined_buildings(data))


# ------------------------------------------------------------------------- wiring


def _recipe_unlockers(schematics: dict[str, Schematic]) -> dict[str, list[str]]:
    """Recipe class -> the schematics that unlock it, in schematic order."""
    unlockers: dict[str, list[str]] = {}
    for schematic in schematics.values():
        for recipe_cls in schematic.unlocks_recipes:
            unlockers.setdefault(recipe_cls, []).append(schematic.cls)
    return unlockers


def _building_recipes(recipes: dict[str, Recipe]) -> dict[str, list[Recipe]]:
    """Building descriptor -> the build-gun recipes that make it, in recipe order."""
    by_descriptor: dict[str, list[Recipe]] = {}
    for recipe in recipes.values():
        if recipe.kind == "building":
            for product in recipe.products:
                by_descriptor.setdefault(product.item, []).append(recipe)
    return by_descriptor


def _wire_recipe_unlocks(
    recipes: dict[str, Recipe],
    schematics: dict[str, Schematic],
    unlockers: dict[str, list[str]],
) -> None:
    # is_alternate comes from EST_Alternate reachability, never from "Alternate" in the
    # ClassName, which is wrong in both directions.
    alternates = {
        recipe_cls
        for schematic in schematics.values()
        if schematic.is_alternate
        for recipe_cls in schematic.unlocks_recipes
    }
    for recipe_cls, sources in unlockers.items():
        recipe = recipes.get(recipe_cls)
        if recipe is not None:
            recipes[recipe_cls] = replace(
                recipe,
                unlocked_by=tuple(dict.fromkeys(sources)),
                is_alternate=recipe_cls in alternates,
            )


def _wire_building_unlocks(
    buildings: dict[str, Building],
    schematics: dict[str, Schematic],
    unlockers: dict[str, list[str]],
    building_recipes: dict[str, list[Recipe]],
) -> None:
    """A building is unlocked by whatever unlocks a build-gun recipe for its descriptor."""
    schematic_order = {cls: position for position, cls in enumerate(schematics)}
    for building_cls, building in list(buildings.items()):
        sources = {
            schematic
            for recipe in building_recipes.get(building.descriptor or "", ())
            for schematic in unlockers.get(recipe.cls, ())
        }
        if sources:
            buildings[building_cls] = replace(
                building, unlocked_by=tuple(sorted(sources, key=schematic_order.__getitem__))
            )


def _attach_build_costs(
    buildings: dict[str, Building], building_recipes: dict[str, list[Recipe]]
) -> None:
    """A building costs the ingredients of the first build-gun recipe for its descriptor."""
    for building_cls, building in list(buildings.items()):
        candidates = building_recipes.get(building.descriptor or "")
        if candidates:
            buildings[building_cls] = replace(building, build_cost=candidates[0].ingredients)


def normalize(dump: DocsDump) -> GameData:
    items = _build_items(dump)
    buildings = _build_buildings(dump, items)
    schematics = _build_schematics(dump)
    recipes = _build_recipes(dump, items, buildings)

    unlockers = _recipe_unlockers(schematics)
    _wire_recipe_unlocks(recipes, schematics, unlockers)
    building_recipes = _building_recipes(recipes)
    _wire_building_unlocks(buildings, schematics, unlockers, building_recipes)
    _attach_build_costs(buildings, building_recipes)

    data = GameData(
        items=items,
        recipes=recipes,
        buildings=buildings,
        schematics=schematics,
        docs_sha256=dump.sha256,
    )
    _collect_drift_warnings(data, dump)
    return data

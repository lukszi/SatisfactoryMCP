"""Turn a DocsDump into the normalized GameData model.

Cold build is ~80 ms for the whole 10.6 MB dump, so this runs at server startup and
needs no disk cache.

Invariant drift is collected into ``GameData.warnings`` rather than raised: a game
patch should degrade the server, not kill it. The test suite asserts warnings is
empty, so drift is still caught loudly in CI.
"""

from __future__ import annotations

import re

from .constants import BELT_SPEED_TO_IPM, PURITY_MULT
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
from .uestruct import amount, as_list, obj_class, parse_struct

__all__ = ["normalize"]

_CLASS_RE = re.compile(r"[\w/\-.]+?\.(\w+_C)\b")
_FLUID_FORMS = ("RF_LIQUID", "RF_GAS")


def _f(raw: object, default: float = 0.0) -> float:
    """Float from a Docs field, tolerating absence and struct-valued fields.

    ``Desc_Locomotive_C.mPowerConsumption`` is ``(Min=25,Max=110)``, so a blanket
    float() over every class crashes.
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, str) and raw.startswith("("):
        parsed = parse_struct(raw)
        if isinstance(parsed, dict):
            return _f(parsed.get("Max") or parsed.get("Min"), default)
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _b(raw: object, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() == "true"


def _i(raw: object, default: int = 0) -> int:
    return int(_f(raw, default))


def _classes_in(raw: object) -> tuple[str, ...]:
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
                c = obj_class(entry)
                if c:
                    out.append(c)
            elif isinstance(entry, dict):
                for v in entry.values():
                    c = obj_class(v) if isinstance(v, str) else None
                    if c:
                        out.append(c)
    if not out and isinstance(raw, str):
        out = _CLASS_RE.findall(raw)
    seen: dict[str, None] = {}
    for c in out:
        seen.setdefault(c, None)
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
        c["ClassName"]: str(c["mDisplayName"])
        for classes in dump.by_native.values()
        for c in classes
        if c.get("ClassName") and c.get("mDisplayName")
    }
    for native, classes in dump.by_native.items():
        for c in classes:
            if "mForm" not in c or "ClassName" not in c:
                continue
            form = str(c.get("mForm") or "RF_INVALID")
            energy = _f(c.get("mEnergyValue"))
            if form in _FLUID_FORMS:
                energy *= 1000  # mEnergyValue is MJ per litre for fluids
            cls = c["ClassName"]
            built = "Build_" + cls[len("Desc_") :] if cls.startswith("Desc_") else ""
            items[cls] = Item(
                cls=cls,
                name=str(c.get("mDisplayName") or shown.get(built) or cls),
                native=native,
                form=form,
                energy_mj=energy,
                stack_size=str(c.get("mStackSize") or ""),
                sink_points=_i(c.get("mResourceSinkPoints")),
                can_be_discarded=_b(c.get("mCanBeDiscarded"), True),
                is_resource=native == "FGResourceDescriptor",
                # Absent on all but the two FGPowerShardDescriptor classes, so the
                # default 0.0 is the right answer everywhere else.
                extra_potential=_f(c.get("mExtraPotential")),
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


def _fuels(raw: object) -> tuple[Fuel, ...]:
    """Parse ``mFuel``, which arrives as real JSON (a list of dicts)."""
    out: list[Fuel] = []
    for entry in as_list(raw if isinstance(raw, (list, dict)) else parse_struct(raw)):
        if not isinstance(entry, dict):
            continue
        fc = obj_class(entry.get("mFuelClass"))
        if not fc:
            continue
        out.append(
            Fuel(
                fuel_class=fc,
                supplemental_class=obj_class(entry.get("mSupplementalResourceClass")),
                byproduct_class=obj_class(entry.get("mByproduct")),
                byproduct_amount=_f(entry.get("mByproductAmount")),
            )
        )
    return tuple(out)


#: "Head Lift: 10 m" as the dump actually writes it. The space before the unit is U+202F, a
#: NARROW NO-BREAK SPACE, so ``\s`` is required here and a literal " m" matches nothing.
_HEAD_LIFT_RE = re.compile(r"Head\s*Lift:\s*([\d.]+)\s*m", re.IGNORECASE)


def _stated_head_lift(desc: object) -> float:
    m = _HEAD_LIFT_RE.search(str(desc or ""))
    return float(m.group(1)) if m else 0.0


#: Natives outside the FGBuildable* family whose classes are still placed buildings.
_OTHER_BUILDABLES = ("FGCentralStorageContainer",)


def _build_buildings(dump: DocsDump, items: dict[str, Item]) -> dict[str, Building]:
    descriptors = {k: v for k, v in items.items() if v.native == "FGBuildingDescriptor"}
    out: dict[str, Building] = {}
    for native, classes in dump.by_native.items():
        if not native.startswith("FGBuildable") and native not in _OTHER_BUILDABLES:
            continue
        for c in classes:
            cls = c.get("ClassName")
            if not cls:
                continue

            # Somersloop slots: read the override flag, because Smelter carries a
            # stale 0/False pair that would otherwise zero its single slot.
            override = _b(c.get("mOverrideProductionShardSlotSize"))
            slots = _i(c.get("mProductionShardSlotSize"), 1) if override else 1
            mult = _f(c.get("mProductionShardBoostMultiplier"), 1.0) if override else 1.0

            items_per_cycle = _f(c.get("mItemsPerCycle"))
            cycle_s = _f(c.get("mExtractCycleTime"))
            forms = tuple(
                x for x in _split_enum(c.get("mAllowedResourceForms")) if x.startswith("RF_")
            )
            base_rate = 0.0
            if items_per_cycle and cycle_s:
                base_rate = items_per_cycle * 60 / cycle_s
                # The /1000 is a fluid-only unit conversion. Applying it to the solid
                # miners in the same native group is 1000x wrong (60 -> 0.06).
                if any(f in _FLUID_FORMS for f in forms):
                    base_rate /= 1000

            speed = _f(c.get("mSpeed"))
            out[cls] = Building(
                cls=cls,
                name=str(c.get("mDisplayName") or cls),
                native=native,
                power_mw=_f(c.get("mPowerConsumption")),
                power_exponent=_f(c.get("mPowerConsumptionExponent"), 1.0),
                boost_power_exponent=_f(c.get("mProductionBoostPowerConsumptionExponent"), 1.0),
                can_overclock=_b(c.get("mCanChangePotential")),
                min_clock=_f(c.get("mMinPotential"), 0.01),
                base_max_clock=_f(c.get("mMaxPotential"), 1.0),
                can_boost=_b(c.get("mCanChangeProductionBoost")),
                sloop_slots=slots,
                sloop_mult=mult,
                base_boost=_f(c.get("mBaseProductionBoost"), 1.0),
                mfg_speed=_f(c.get("mManufacturingSpeed"), 1.0),
                descriptor=_descriptor_for(cls, descriptors),
                items_per_cycle=items_per_cycle,
                extract_cycle_s=cycle_s,
                base_extract_rate=base_rate,
                allowed_resources=_classes_in(c.get("mAllowedResources")),
                allowed_forms=forms,
                power_production_mw=_f(c.get("mPowerProduction")),
                variable_power_factor=_f(c.get("mVariablePowerProductionFactor")),
                requires_supplemental=_b(c.get("mRequiresSupplementalResource")),
                supplemental_ratio=_f(c.get("mSupplementalToPowerRatio")),
                fuels=_fuels(c.get("mFuel")),
                items_per_min=speed * BELT_SPEED_TO_IPM if speed else 0.0,
                flow_m3_min=_f(c.get("mFlowLimit")) * 60,
                storage_capacity_m3=_f(c.get("mStorageCapacity")),
                head_lift_m=_f(c.get("mDesignPressure")),
                max_head_lift_m=_f(c.get("mMaxPressure")),
                machine_head_lift_m=_stated_head_lift(c.get("mDescription")),
                footprint=extract_footprint(c.get("mClearanceData")),
            )
    return out


def _split_enum(raw: object) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = parse_struct(raw)
    except Exception:
        return ()
    vals = as_list(parsed)
    out: list[str] = []
    for v in vals:
        if isinstance(v, str):
            out.append(v.strip())
        elif isinstance(v, dict):
            out.extend(str(x).strip() for x in v.values() if isinstance(x, str))
    return tuple(out)


# ---------------------------------------------------------------------- schematics


def _build_schematics(dump: DocsDump) -> dict[str, Schematic]:
    out: dict[str, Schematic] = {}
    for c in dump.classes("FGSchematic"):
        cls = c.get("ClassName")
        if not cls:
            continue
        recipes: list[str] = []
        schematics: list[str] = []
        slots = 0
        # mUnlocks arrives as real JSON: a list of dicts, each with a 'Class' key.
        for unlock in as_list(c.get("mUnlocks") or []):
            if not isinstance(unlock, dict):
                continue
            ucls = str(unlock.get("Class") or "")
            if ucls in ("BP_UnlockRecipe_C", "BP_UnlockBlueprints_C"):
                recipes.extend(_classes_in(unlock.get("mRecipes")))
            elif ucls == "BP_UnlockSchematic_C":
                # Recorded, never expanded here: recursive expansion pulls in 23
                # CBG_* customization schematics and inflates the derived recipe
                # set to 514, of which 113 are not actually unlocked.
                schematics.extend(_classes_in(unlock.get("mSchematics")))
            elif ucls == "BP_UnlockInventorySlot_C":
                slots += _i(unlock.get("mNumInventorySlotsToUnlock"))

        deps: list[str] = []
        for dep in as_list(c.get("mSchematicDependencies") or []):
            if isinstance(dep, dict) and dep.get("Class") == "BP_SchematicPurchasedDependency_C":
                deps.extend(_classes_in(dep.get("mSchematics")))

        cost = tuple(
            Flow(item=obj_class(e.get("ItemClass")) or "?", amount=amount(e), per_min=0.0)
            for e in as_list(parse_struct(c.get("mCost")))
            if isinstance(e, dict)
        )
        out[cls] = Schematic(
            cls=cls,
            name=str(c.get("mDisplayName") or cls),
            type=str(c.get("mType") or ""),
            tier=_i(c.get("mTechTier")),
            time_s=_f(c.get("mTimeToComplete")),
            cost=cost,
            unlocks_recipes=tuple(dict.fromkeys(recipes)),
            unlocks_schematics=tuple(dict.fromkeys(schematics)),
            dependencies=tuple(dict.fromkeys(deps)),
            events=str(c.get("mRelevantEvents") or ""),
            grants_inventory_slots=slots,
        )
    return out


# ------------------------------------------------------------------------- recipes


def _flows(raw: object, items: dict[str, Item], duration: float) -> tuple[Flow, ...]:
    out: list[Flow] = []
    for e in as_list(parse_struct(raw)):
        if not isinstance(e, dict):
            continue
        cls = obj_class(e.get("ItemClass"))
        if not cls:
            continue
        qty = amount(e)
        it = items.get(cls)
        if it is not None and it.is_fluid:
            qty = qty / 1000.0  # float: Recipe_Battery_C has SulfuricAcid=2500
        per_min = qty * 60 / duration if duration else 0.0
        out.append(Flow(item=cls, amount=qty, per_min=per_min))
    return tuple(out)


def _build_recipes(
    dump: DocsDump, items: dict[str, Item], buildings: dict[str, Building]
) -> dict[str, Recipe]:
    building_descs = {k for k, v in items.items() if v.native == "FGBuildingDescriptor"}
    manufacturers = {c for c, b in buildings.items() if b.native in MANUFACTURER_NATIVES}
    variable = {
        c for c, b in buildings.items() if b.native == "FGBuildableManufacturerVariablePower"
    }

    out: dict[str, Recipe] = {}
    for c in dump.classes("FGRecipe"):
        cls = c.get("ClassName")
        if not cls:
            continue
        duration = _f(c.get("mManufactoringDuration"))  # typo is in the game data
        products = _flows(c.get("mProduct"), items, duration)
        ingredients = _flows(c.get("mIngredients"), items, duration)
        produced_in = _classes_in(c.get("mProducedIn"))

        machines = [p for p in produced_in if p in manufacturers]
        if products and products[0].item in building_descs:
            kind, machine = "building", None
        elif machines:
            kind, machine = "part", machines[0]
        else:
            kind, machine = "manual", None

        pmin = pmax = 0.0
        if machine in variable:
            const = _f(c.get("mVariablePowerConsumptionConstant"))
            factor = _f(c.get("mVariablePowerConsumptionFactor"))
            # Factor is a RANGE, not a multiplier: building-level
            # mEstimatedMininum/MaximumPowerConsumption exactly bracket const..const+factor.
            pmin, pmax = const, const + factor

        out[cls] = Recipe(
            cls=cls,
            name=str(c.get("mDisplayName") or cls),
            kind=kind,
            machine=machine,
            duration_s=duration,
            ingredients=ingredients,
            products=products,
            manual_mult=_f(c.get("mManualManufacturingMultiplier"), 1.0),
            power_min_mw=pmin,
            power_max_mw=pmax,
            events=str(c.get("mRelevantEvents") or ""),
        )
    return out


# ---------------------------------------------------------------------- assertions


def _check(data: GameData, dump: DocsDump) -> None:
    w = data.warnings
    counts: dict[str, int] = {}
    for r in data.recipes.values():
        counts[r.kind] = counts.get(r.kind, 0) + 1
    total = len(data.recipes)
    if total != sum(counts.values()):
        w.append(f"recipe partition lost entries: {total} != {counts}")
    # Known-good shape for v1.2.2.1: 872 = 547 building + 291 part + 34 manual.
    if counts.get("part", 0) == 0:
        w.append("no part recipes found -- mProducedIn / manufacturer join is broken")

    # Belts state their own rate in prose, so BELT_SPEED_TO_IPM is self-checking.
    for c in dump.classes("FGBuildableConveyorBelt"):
        b = data.buildings.get(c.get("ClassName", ""))
        desc = str(c.get("mDescription") or "")
        m = re.search(r"(\d[\d\s,]*)\s*(?:items|resources)?\s*per minute", desc, re.IGNORECASE)
        if b and m:
            stated = float(m.group(1).replace(",", "").replace(" ", ""))
            if abs(stated - b.items_per_min) > 0.5:
                w.append(f"{b.cls}: computed {b.items_per_min}/min but description says {stated}")

    # Pipes likewise.
    for c in dump.classes("FGBuildablePipeline"):
        b = data.buildings.get(c.get("ClassName", ""))
        desc = str(c.get("mDescription") or "")
        m = re.search(r"(\d+)\s*m.{0,4}\s*of fluid per minute", desc, re.IGNORECASE)
        if b and m and abs(float(m.group(1)) - b.flow_m3_min) > 0.5:
            w.append(f"{b.cls}: computed {b.flow_m3_min} m3/min, description says {m.group(1)}")

    # A pump states its head lift in prose as well as in mDesignPressure, so the prose parse
    # that gives every other machine its rating is checked against a field on the two classes
    # that carry both.
    for c in dump.classes("FGBuildablePipelinePump"):
        b = data.buildings.get(c.get("ClassName", ""))
        if b and b.machine_head_lift_m and abs(b.machine_head_lift_m - b.head_lift_m) > 0.01:
            w.append(
                f"{b.cls}: mDesignPressure {b.head_lift_m} m but description says "
                f"{b.machine_head_lift_m} m"
            )

    # Extractor descriptions state the NORMAL-purity rate, which is what pins
    # PURITY_MULT. Check the ones that spell out a number.
    for native in ("FGBuildableResourceExtractor", "FGBuildableWaterPump"):
        for c in dump.classes(native):
            b = data.buildings.get(c.get("ClassName", ""))
            desc = str(c.get("mDescription") or "")
            m = re.search(r"(\d+)\s*(?:resources|m.{0,4} of \w+)\s*per minute", desc, re.IGNORECASE)
            if b and m and b.base_extract_rate:
                stated = float(m.group(1))
                got = b.base_extract_rate * PURITY_MULT["normal"]
                if abs(stated - got) > 0.5:
                    w.append(f"{b.cls}: normal rate {got}/min but description says {stated}")

    # Every production-relevant buildable must resolve to a descriptor, or build
    # costs silently vanish.
    for b in data.buildings.values():
        if (b.is_manufacturer or b.is_extractor or b.is_generator) and not b.descriptor:
            w.append(f"{b.cls}: no FGBuildingDescriptor match, build cost unavailable")
        if (b.is_manufacturer or b.is_generator) and b.footprint is None:
            w.append(f"{b.cls}: no clearance data, cannot size a layout")


def normalize(dump: DocsDump) -> GameData:
    items = _build_items(dump)
    buildings = _build_buildings(dump, items)
    schematics = _build_schematics(dump)
    recipes = _build_recipes(dump, items, buildings)

    # Wire unlocks. is_alternate comes from EST_Alternate reachability, never from
    # "Alternate" in ClassName -- that set is 110 and wrong in both directions.
    unlocked_by: dict[str, list[str]] = {}
    alternates: set[str] = set()
    for s in schematics.values():
        for rc in s.unlocks_recipes:
            unlocked_by.setdefault(rc, []).append(s.cls)
            if s.is_alternate:
                alternates.add(rc)
    for rc, srcs in unlocked_by.items():
        r = recipes.get(rc)
        if r is None:
            continue
        recipes[rc] = Recipe(
            **{
                **r.__dict__,
                "unlocked_by": tuple(dict.fromkeys(srcs)),
                "is_alternate": rc in alternates,
            }
        )

    b_unlocked: dict[str, list[str]] = {}
    for s in schematics.values():
        for rc in s.unlocks_recipes:
            r = recipes.get(rc)
            if r is None or r.kind != "building":
                continue
            for f in r.products:
                for bcls, b in buildings.items():
                    if b.descriptor == f.item:
                        b_unlocked.setdefault(bcls, []).append(s.cls)
    for bcls, srcs in b_unlocked.items():
        b = buildings[bcls]
        buildings[bcls] = Building(**{**b.__dict__, "unlocked_by": tuple(dict.fromkeys(srcs))})

    # Attach build costs.
    for bcls, b in list(buildings.items()):
        if not b.descriptor:
            continue
        for r in recipes.values():
            if r.kind == "building" and any(f.item == b.descriptor for f in r.products):
                buildings[bcls] = Building(**{**b.__dict__, "build_cost": r.ingredients})
                break

    data = GameData(
        items=items,
        recipes=recipes,
        buildings=buildings,
        schematics=schematics,
        docs_sha256=dump.sha256,
    )
    _check(data, dump)
    return data

"""What ``WorldState`` derives from a save: unlocks, offers, stock, power, building counts.

These run off the committed projection fixture, so they need no .sav and no parser --
only the Docs join needs the game install.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_unlock_gate_is_available_recipes_not_schematics(state):
    """Reconstructing unlocks from purchased schematics over-reports by 8."""
    assert len(state.available_recipe_ids) == 405
    assert len(state.unlocked_recipes("part")) == 126
    assert len(state.unlocked_alternates) == 30
    # Charcoal arrives via Compacted Coal, whose own alternate schematic is unowned.
    assert state.has_recipe("Recipe_Alternate_Coal_1_C")
    assert "Schematic_Alternate_Coal1_C" not in state.purchased_schematic_ids


@pytest.mark.integration
def test_unresolvable_recipes_are_filtered_not_surfaced(state):
    """31 available recipes have no FGRecipe in Docs.json (swatches, materials)."""
    assert len(state.unresolved_recipe_ids) == 31
    assert all(r not in state.game.recipes for r in state.unresolved_recipe_ids)


@pytest.mark.integration
def test_highest_complete_tier_is_fully_complete(state):
    """Tier 7 has milestones done but is NOT complete; reporting 7 would claim the
    player has finished it."""
    p = state.progression()
    assert p["highest_complete_tier"] == 6
    assert p["milestones_by_tier"][7] == "3/5"


@pytest.mark.integration
def test_unlocked_but_never_built_is_detected(state):
    """The Blender is unlocked with 0 built, which every Diluted Fuel plan must say."""
    unbuilt = set(state.unlocked_but_unbuilt())
    assert "Build_Blender_C" in unbuilt
    assert state.built("Build_Blender_C") == 0
    assert state.built("Build_OilRefinery_C") == 36


@pytest.mark.integration
def test_building_unlock_is_not_inferred_from_recipe_name(state):
    """Recipe_SmelterMk1_C builds the FOUNDRY, so naming cannot be trusted."""
    assert state.can_build("Build_FoundryMk1_C")
    assert state.can_build("Build_SmelterMk1_C")


@pytest.mark.integration
def test_resource_wells_are_locked(state):
    """1,080 m3/min of map crude sits on well satellites this world cannot reach."""
    assert not state.can_build("Build_FrackingSmasher_C")


@pytest.mark.integration
def test_hard_drive_offers_are_read_from_the_save(state):
    offers = state.hard_drive_offers
    assert len(offers) == 25
    assert all(len(o.options) == 2 for o in offers)
    by_id = {o.hard_drive_id: o for o in offers}
    names = {opt["name"] for opt in by_id[9].options}
    assert "Alternate: Turbo Heavy Fuel" in names
    # Drive 25 has spent its single reroll.
    assert by_id[25].rerolls_left == 0
    assert by_id[9].rerolls_left == 1


@pytest.mark.integration
def test_chained_schematic_grants_both_recipes(state):
    """Quartz Purification chains to Distilled Silica via BP_UnlockSchematic_C.
    Exactly one level -- deeper recursion drags in 23 customization schematics."""
    offers = {o.hard_drive_id: o for o in state.hard_drive_offers}
    opt = next(o for o in offers[23].options if "Quartz Purification" in o["name"])
    assert len(opt["recipes"]) == 2


@pytest.mark.integration
def test_inventory_slot_schematics_grant_no_recipe(state):
    """Two of the 50 offered schematics unlock inventory slots, not recipes."""
    slots = [opt for o in state.hard_drive_offers for opt in o.options if opt["slots"]]
    assert len(slots) == 2
    assert all(not opt["recipes"] for opt in slots)


@pytest.mark.integration
def test_dependency_gate_uses_dependencies_not_tier(state):
    """mTechTier is 0 for 71 of 109 alternates, so it cannot be the gate."""
    met, missing = state.dependencies_met("Schematic_Alternate_TurboHeavyFuel_C")
    assert met and missing == []
    blocked = [
        s.cls
        for s in state.game.schematics.values()
        if s.is_alternate and not state.dependencies_met(s.cls)[0]
    ]
    assert len(blocked) == 24


@pytest.mark.integration
def test_stock_excludes_machine_buffers(state):
    """Summing every stack in the world gives Water 5,556,375 and Fuel 1,048,762 --
    pipe and machine contents in litres. A build-cost check against that would tell
    the player they can afford anything."""
    stock = state.stock()
    buffers = state.machine_buffers()
    assert stock and buffers
    # Nothing spendable is in the millions.
    assert max(stock.values()) < 1_000_000
    # The fluids that dominated the old flat total are buffers, not stock.
    assert stock.get("Desc_Water_C", 0) < buffers.get("Desc_Water_C", 0)


@pytest.mark.integration
def test_fluid_stock_is_scaled_to_cubic_metres(state):
    """The sidecar reports raw litres; anything user-facing must be m3."""
    buffers = state.machine_buffers()
    water = buffers.get("Desc_Water_C", 0)
    assert 0 < water < 100_000  # ~5,558 m3, not 5,558,240 litres


@pytest.mark.integration
def test_hard_drives_counted_from_spendable_stock_only(state):
    """1 drive sits in the Dimensional Depot; none are carried."""
    assert state.spare_hard_drives() == 1


@pytest.mark.integration
def test_power_report_excludes_paused(state):
    pw = state.power_report()
    assert pw["generation_mw"] > 0
    assert pw["paused_count"] == 16
    # All 10 biomass generators are paused, so they contribute nothing.
    assert "Build_GeneratorBiomass_C" not in pw["by_generator"]


def test_a_building_the_save_names_differently_is_not_reported_unbuilt(game):
    """world_summary claimed "unlocked but never built: Biomass Burner" while eight of
    them were running. unlocked_building_ids comes from build recipes, which yield
    Build_GeneratorBiomass_Automated_C; the save stores the standing burners as
    Build_GeneratorBiomass_C. A never-built warning that fires on a building you can see
    erodes trust in every other warning."""
    from satisfactory_mcp.domain.world.state import WorldState

    projection = {
        "building_counts": {"Build_GeneratorBiomass_C": 8},
        "lightweight_counts": {},
        "progression": {"available_recipes": []},
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    assert st.built("Build_GeneratorBiomass_Automated_C") == 8
    assert st.built("Build_GeneratorBiomass_C") == 0, "folded onto the docs name"


def test_the_hub_integrated_burner_is_not_folded_in(game):
    """It has no build recipe and cannot be placed, so counting it would credit the
    player with generators they never built."""
    from satisfactory_mcp.core.gamedata.constants import BUILDING_CLASS_ALIASES

    assert "Build_GeneratorIntegratedBiomass_C" not in BUILDING_CLASS_ALIASES


def test_aliases_only_map_onto_classes_the_docs_actually_have(game):
    """An alias pointing at a class Docs.json does not define would move the count
    somewhere nothing can read it."""
    from satisfactory_mcp.core.gamedata.constants import BUILDING_CLASS_ALIASES

    for save_cls, docs_cls in BUILDING_CLASS_ALIASES.items():
        assert docs_cls in game.buildings, docs_cls
        assert save_cls not in game.buildings, f"{save_cls} needs no alias"

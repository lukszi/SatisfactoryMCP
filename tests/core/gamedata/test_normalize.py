"""Invariants over the normalized game data.

These are the tests that catch a game patch: every number here was measured from
v1.2.2.1 and is expected to break loudly rather than drift silently.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gamedata.constants import PURITY_MULT

pytestmark = pytest.mark.integration


def test_no_warnings(game):
    """Build-time invariant checks must all pass: belt/pipe/extractor rates match
    their own mDescription prose, and every production buildable maps to a
    descriptor."""
    assert game.warnings == []


def test_recipe_partition(game):
    counts: dict[str, int] = {}
    for r in game.recipes.values():
        counts[r.kind] = counts.get(r.kind, 0) + 1
    assert len(game.recipes) == 872
    assert counts == {"building": 547, "part": 291, "manual": 34}


def test_alternates_come_from_schematics_not_names(game):
    alts = game.alternates()
    assert len(alts) == 109
    # Turbofuel has "Alternate" in its ClassName but comes from the Sulfur MAM tree,
    # so the name heuristic would wrongly include it.
    turbo = game.recipes["Recipe_Alternate_Turbofuel_C"]
    assert not turbo.is_alternate
    # ...and this one IS a hard-drive alternate with no "Alternate" in its name.
    assert game.recipes["Recipe_PureAluminumIngot_C"].is_alternate


def test_fluids(game):
    fluids = [i for i in game.items.values() if i.is_fluid]
    assert len(fluids) == 15
    # Packaged forms and Fuel (the solid canister item) are NOT fluids.
    assert not game.items["Desc_Fuel_C"].is_fluid
    assert game.items["Desc_LiquidFuel_C"].is_fluid
    # x1000 scaling, as a float: Battery uses 2.5 m3 sulfuric acid, not 2.
    battery = game.recipes["Recipe_Battery_C"]
    acid = next(f for f in battery.ingredients if f.item == "Desc_SulfuricAcid_C")
    assert acid.amount == pytest.approx(2.5)


def test_energy_value_scaled_for_fluids(game):
    # mEnergyValue is MJ per LITRE for fluids, per item for solids.
    assert game.items["Desc_LiquidFuel_C"].energy_mj == pytest.approx(750.0)
    assert game.items["Desc_Coal_C"].energy_mj == pytest.approx(300.0)


@pytest.mark.parametrize(
    "cls,impure,normal,pure",
    [
        ("Build_MinerMk1_C", 30, 60, 120),
        ("Build_MinerMk2_C", 60, 120, 240),
        ("Build_MinerMk3_C", 120, 240, 480),
        ("Build_OilPump_C", 60, 120, 240),
        ("Build_WaterPump_C", 60, 120, 240),
        ("Build_FrackingExtractor_C", 30, 60, 120),
    ],
)
def test_extraction_rates(game, cls, impure, normal, pure):
    b = game.buildings[cls]
    assert b.extract_rate("impure") == pytest.approx(impure)
    assert b.extract_rate("normal") == pytest.approx(normal)
    assert b.extract_rate("pure") == pytest.approx(pure)


def test_purity_multipliers_are_the_documented_ones(game):
    assert PURITY_MULT == {"impure": 0.5, "normal": 1.0, "pure": 2.0}


def test_power_exponent_is_per_building_not_global(game):
    """1.321929 for manufacturers/extractors, 1.6 for generators. Deriving it from
    'is clockable' gives the wrong answer for Build_DroneStation_C."""
    assert game.buildings["Build_OilRefinery_C"].power_exponent == pytest.approx(1.321929)
    assert game.buildings["Build_OilPump_C"].power_exponent == pytest.approx(1.321929)
    assert game.buildings["Build_GeneratorFuel_C"].power_exponent == pytest.approx(1.6)
    assert game.buildings["Build_DroneStation_C"].power_exponent == pytest.approx(1.6)


def test_somersloop_slots_and_multiplier_are_per_building(game):
    for cls, slots, mult in [
        ("Build_ConstructorMk1_C", 1, 1.0),
        ("Build_SmelterMk1_C", 1, 1.0),  # carries a stale 0/False override pair
        ("Build_OilRefinery_C", 2, 0.5),
        ("Build_ManufacturerMk1_C", 4, 0.25),
        ("Build_Blender_C", 4, 0.25),
    ]:
        b = game.buildings[cls]
        assert (b.sloop_slots, b.sloop_mult) == (slots, mult), cls
        assert b.boost_for(slots) == pytest.approx(2.0), cls
    # The Packager is the one manufacturer that cannot take somersloops.
    assert not game.buildings["Build_Packager_C"].can_boost


def test_fuel_generator_needs_no_water_but_coal_does(game):
    fuel = game.buildings["Build_GeneratorFuel_C"]
    assert fuel.power_production_mw == pytest.approx(250.0)
    assert not fuel.requires_supplemental
    assert fuel.supplemental_m3_min() == pytest.approx(0.0)
    assert fuel.fuel_rate_per_min(game.items["Desc_LiquidFuel_C"]) == pytest.approx(20.0)
    assert fuel.fuel_rate_per_min(game.items["Desc_LiquidTurboFuel_C"]) == pytest.approx(7.5)

    coal = game.buildings["Build_GeneratorCoal_C"]
    assert coal.requires_supplemental
    assert coal.supplemental_m3_min() == pytest.approx(45.0)
    assert coal.fuel_rate_per_min(game.items["Desc_Coal_C"]) == pytest.approx(15.0)


def test_belt_and_pipe_throughput(game):
    speeds = {
        "Build_ConveyorBeltMk1_C": 60,
        "Build_ConveyorBeltMk2_C": 120,
        "Build_ConveyorBeltMk3_C": 270,
        "Build_ConveyorBeltMk4_C": 480,
        "Build_ConveyorBeltMk5_C": 780,
        "Build_ConveyorBeltMk6_C": 1200,
    }
    for cls, ipm in speeds.items():
        assert game.buildings[cls].items_per_min == pytest.approx(ipm), cls
    assert game.buildings["Build_Pipeline_C"].flow_m3_min == pytest.approx(300)
    assert game.buildings["Build_PipelineMK2_C"].flow_m3_min == pytest.approx(600)


def test_variable_power_factor_is_a_range_not_a_multiplier(game):
    """Proof: the building's own mEstimatedMinimum/Maximum bracket const..const+factor."""
    r = next(
        r for r in game.part_recipes() if r.machine == "Build_Converter_C" and r.is_variable_power
    )
    assert r.power_min_mw == pytest.approx(100.0)
    assert r.power_max_mw == pytest.approx(400.0)
    assert r.power_avg_mw == pytest.approx(250.0)


def test_fluids_cannot_be_sunk_even_though_docs_says_they_can(game):
    """The one place we deliberately contradict the game data."""
    hor = game.items["Desc_HeavyOilResidue_C"]
    assert hor.sink_points > 0 and hor.can_be_discarded  # what Docs.json claims
    assert not hor.sinkable  # what the game actually allows
    # Packaging it makes it disposable.
    assert game.items["Desc_PackagedOilResidue_C"].sinkable
    assert game.items["Desc_PolymerResin_C"].sinkable


def test_key_oil_recipe_rates(game):
    """The chain that makes the northern plant work: 60 crude -> 160 Fuel."""
    hor = game.recipes["Recipe_Alternate_HeavyOilResidue_C"]
    assert hor.rate_of("Desc_LiquidOil_C") == pytest.approx(-30)
    assert hor.rate_of("Desc_HeavyOilResidue_C") == pytest.approx(40)
    assert hor.rate_of("Desc_PolymerResin_C") == pytest.approx(20)

    diluted = game.recipes["Recipe_Alternate_DilutedFuel_C"]
    assert diluted.machine == "Build_Blender_C"
    assert diluted.rate_of("Desc_HeavyOilResidue_C") == pytest.approx(-50)
    assert diluted.rate_of("Desc_Water_C") == pytest.approx(-100)
    assert diluted.rate_of("Desc_LiquidFuel_C") == pytest.approx(100)


def test_build_costs_resolve_for_production_buildings(game):
    """Descriptor join must be total for anything a plan might tell you to build."""
    for cls in ("Build_OilRefinery_C", "Build_Blender_C", "Build_GeneratorFuel_C"):
        b = game.buildings[cls]
        assert b.descriptor, cls
        assert b.build_cost, cls


def test_one_class_gets_one_name_on_every_surface(game):
    """``GameData.building_name``: the dump's name where there is one, words where not.

    There were four spellings of the fallback before this existed -- the web API's spaced
    words, ``.replace("Build_", "").replace("_C", "")`` in two MCP tools, and one that left
    the ``_C`` on -- so the map page and the tool describing the same machine could
    disagree, and did, for 87 of the reference world's 90 built classes. No test caught it,
    because nothing pinned those columns. This pins them.
    """
    # Listed in the dump: the docs name wins, and it is not a rendering of the id.
    assert game.building_name("Build_AssemblerMk1_C") == "Assembler"
    assert game.building_name("Build_ConveyorAttachmentSplitterSmart_C") == "Smart Splitter"
    assert (
        game.building_name("Build_GeneratorFuel_C") == game.buildings["Build_GeneratorFuel_C"].name
    )

    # Not in the dump -- the HUB's burner and the map-placed actors, which is the whole reason
    # a fallback exists. Words, in the same language the docs names are in, or a pinned name.
    for cls in ("Build_GeneratorIntegratedBiomass_C", "Build_HubTerminal_C", "BP_ResourceNode_C"):
        assert cls not in game.buildings, f"{cls} is in the dump now -- re-pick this example"
    assert game.building_name("Build_GeneratorIntegratedBiomass_C") == "HUB Biomass Burner"
    assert game.building_name("Build_HubTerminal_C") == "Hub Terminal"
    assert game.building_name("BP_ResourceNode_C") == "Resource Node"

    # Never a raw engine id, for anything the reference world actually has built.
    assert not [c for c in game.buildings if "_C" in (game.building_name(c) or "")]

    # ``None`` in, ``None`` out: an absent occupant is not a building with an unknown name.
    assert game.building_name(None) is None
    assert game.building_name("") is None

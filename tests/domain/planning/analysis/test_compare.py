"""Route comparison: the pinning rule, the lexicographic tie-break, and the oil
finding it has to reproduce."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.compare import PROBE_RATE, compare_routes
from satisfactory_mcp.presenters.text.compare import render_comparison

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
FUEL = "Desc_LiquidFuel_C"
PLASTIC = "Desc_Plastic_C"
DILUTED = "Recipe_Alternate_DilutedFuel_C"
BASE_FUEL = "Recipe_LiquidFuel_C"
ALT_HOR = "Recipe_Alternate_HeavyOilResidue_C"


@pytest.fixture(scope="module")
def fuel(game, state):
    """The default call: byproducts may go to an AWESOME Sink."""
    return compare_routes(game, state, "Fuel")


@pytest.fixture(scope="module")
def fuel_with_products(game, state):
    """The design spec's own configuration for the oil plant: no sinks, and the
    resin has to terminate in Plastic or Rubber."""
    return compare_routes(game, state, "Fuel", allow_sinks=False, outlets=["Plastic", "Rubber"])


def _by_recipe(cmp, recipe_id):
    return next(r for r in cmp.routes if r.recipe == recipe_id)


# ------------------------------------------------------- the known answer


def test_reproduces_the_documented_oil_finding(fuel_with_products):
    """The reference save's best crude->Fuel route is Alt HOR + Diluted Fuel at
    60 crude -> 160 Fuel and 30.00 net MW per m3/min crude, against 40 Fuel and
    7.58 MW for the base recipe. If these drift, the tool is quietly recommending
    a different factory from the one the design spec measured."""
    best = _by_recipe(fuel_with_products, DILUTED)
    worst = _by_recipe(fuel_with_products, BASE_FUEL)

    assert best.yield_per_probe == pytest.approx(160.0, abs=1e-3)
    assert worst.yield_per_probe == pytest.approx(40.0, abs=1e-3)
    assert best.power_yield == pytest.approx(30.00, abs=0.01)
    assert worst.power_yield == pytest.approx(7.583, abs=0.01)
    assert best.power_yield / worst.power_yield == pytest.approx(3.96, abs=0.01)


def test_the_gap_is_stated_not_left_to_be_derived(fuel_with_products):
    """A reader who has to divide two table cells to notice a 4x difference has
    been shown the data and not told the answer."""
    text = render_comparison(fuel_with_products)
    header = text.splitlines()[1]
    assert header.startswith("# best vs worst")
    assert "3.96x" in header
    assert "4x" in header  # 0.375 vs 1.5 m3 crude per Fuel


def test_the_winning_route_ranks_first_by_default_too(fuel):
    """Letting the resin go to a sink changes the power figures but must not
    change the recommendation -- if it did, the answer would depend on a knob
    rather than on the recipes."""
    assert fuel.routes[0].recipe == DILUTED
    assert fuel.routes[0].per_unit == pytest.approx(0.375, abs=1e-4)
    assert fuel.routes[-1].recipe == BASE_FUEL
    assert fuel.routes[-1].per_unit == pytest.approx(1.5, abs=1e-4)


# ------------------------------------------------------- the pinning rule


def test_a_route_is_a_whole_subchain_not_one_recipe(fuel):
    """Diluted Fuel alone consumes Heavy Oil Residue, which no route buys as a
    raw. The comparison is only meaningful because the LP fills in the upstream,
    so an empty chain here means the tool has silently become a recipe lookup."""
    best = _by_recipe(fuel, DILUTED)
    assert ALT_HOR in best.upstream_ids
    assert best.raw_per_unit.get(CRUDE) == pytest.approx(0.375, abs=1e-4)


def test_rival_producers_are_deleted_so_routes_cannot_blend(fuel):
    """Without deleting the rivals the LP just mixes producers and every row
    collapses to the same optimum -- there would be nothing to compare."""
    rivals = {r.recipe for r in fuel.routes}
    for route in fuel.feasible:
        assert not (rivals - {route.recipe}) & set(route.upstream_ids), route.name


def test_locked_recipes_are_never_offered_as_a_route(fuel, state):
    """The question is what THIS save can build. A route through Turbo Blend Fuel
    would be advice the player cannot act on."""
    for route in fuel.routes:
        assert state.has_recipe(route.recipe)


# ------------------------------------- degeneracy and the lexicographic pin


def test_water_is_never_traded_against_the_scarce_resource(game, state):
    """A bare min_raw sums every resource with weight one, so it bought crude back
    with water: it returned Recycled Plastic at 0.9375 m3 crude per Plastic when
    0.3333 was reachable. Water is unlimited on this map and crude is not."""
    plastic = compare_routes(game, state, "Plastic")
    recycled = plastic.routes[0]
    assert recycled.recipe == "Recipe_Alternate_Plastic_1_C"
    assert recycled.per_unit == pytest.approx(1 / 3, abs=1e-3)
    assert recycled.raw_per_unit[CRUDE] == pytest.approx(1 / 3, abs=1e-3)


def test_the_ranking_resource_is_the_same_for_every_row(fuel):
    """A per-unit column whose denominator changes between rows is not a
    comparison. Crude, not water, even though the winning route drinks water."""
    assert fuel.primary == CRUDE
    assert all(r.per_unit > 0 for r in fuel.feasible)


def test_a_target_that_is_a_raw_resource_cannot_be_its_own_input(game, state):
    """Leave Water in the raw list and every route to Water scores a perfect
    "1 water per water" from zero machines, which is not a route at all."""
    water = compare_routes(game, state, "Water")
    assert water.routes
    assert not water.feasible


def test_machine_count_and_raw_cost_are_reported_as_two_decisions(game, state):
    """Recycled Plastic is 9 buildings when crude is what you are short of and 6
    when buildings are. Collapsing that to one number hides a real choice."""
    plastic = compare_routes(game, state, "Plastic")
    recycled = plastic.routes[0]
    assert recycled.machines == 9
    assert recycled.machines_floor == 6
    assert "9/6" in render_comparison(plastic)


# ------------------------------------------------ consequences of a route


def test_a_building_that_is_unlocked_but_never_built_is_surfaced(fuel):
    """The reference save has the Blender unlocked with 0 built, and the winning
    route needs one. That is the whole cost of the recommendation."""
    best = _by_recipe(fuel, DILUTED)
    assert best.build_first == ["Blender"]
    assert any("Blender" in n and "0 built" in n for n in fuel.notes)


def test_every_byproduct_that_leaves_the_plant_is_named_with_its_outlet(fuel):
    """Every crude->Fuel route this save owns emits Polymer Resin. A plan that
    does not say where it goes is a plan that stalls a pipe in game."""
    for route in fuel.feasible:
        resin = [b for b in route.byproducts if b.item == "Desc_PolymerResin_C"]
        assert resin, route.name
        assert resin[0].outlet == "sink"
    assert any("AWESOME Sink" in n for n in fuel.notes)


def test_outlets_change_the_byproduct_from_scrap_into_product(fuel_with_products):
    """Naming Plastic as an outlet is what turns 25/min of resin into something
    the player wanted, and it is priced: the base route goes from 3 machines to 5."""
    worst = _by_recipe(fuel_with_products, BASE_FUEL)
    assert [b.name for b in worst.byproducts] == ["Plastic"]
    assert worst.byproducts[0].outlet == "export"
    assert "Recipe_ResidualPlastic_C" in worst.upstream_ids


def test_power_yield_is_absent_when_the_target_is_not_a_fuel(game, state):
    """Plastic cannot be burnt, so pretending it has a MW value would be an
    invented number in a column that reads as measured."""
    plastic = compare_routes(game, state, "Plastic")
    assert plastic.generator_mw_per_unit == 0
    assert all(r.power_yield is None for r in plastic.routes)
    assert "MW/oil" not in render_comparison(plastic)


def test_machines_are_whole_buildings(fuel):
    """A fractional count is the ratio, not the build. Every row has to be
    something the player can place."""
    for route in fuel.feasible:
        assert isinstance(route.machines, int)
        assert route.machines >= 1


# --------------------------------------------------- infeasibility, errors


def test_an_infeasible_route_says_what_its_chain_could_not_supply(game, state):
    """Pinning Unpackage Heavy Oil Residue deletes every other HOR producer, so
    nothing can fill a canister. "infeasible" alone would read as a bug."""
    hor = compare_routes(game, state, "Heavy Oil Residue")
    unpack = _by_recipe(hor, "Recipe_UnpackageOilResidue_C")
    assert not unpack.ok
    assert "Packaged Heavy Oil Residue" in unpack.note


def test_unreachable_input_detection_survives_a_recipe_cycle(game, state):
    """Recycled Plastic and Recycled Rubber form a real 2-cycle. A depth-limited
    walk either loops or declares the pair reachable from nothing; the least
    fixpoint used here reports Plastic as reachable via crude, and only via crude."""
    plastic = compare_routes(game, state, "Plastic")
    recycled = _by_recipe(plastic, "Recipe_Alternate_Plastic_1_C")
    assert recycled.ok
    assert "Recipe_Alternate_RecycledRubber_C" in recycled.upstream_ids


def test_a_route_the_lp_can_build_is_never_reported_infeasible(game, state):
    """The buildable plan is solved with the scarce resource pinned to what the probe
    proved reachable -- but the probe's yield is rounded to 4dp, so a pin derived
    from it can sit just BELOW what the route needs. At 1e-6 of headroom that
    rounding said Heavy Modular Frame had no route at all, for either recipe, on a
    save that can build both. An "infeasible" verdict a player cannot check is the
    worst thing this tool can emit, so the pin must cover the rounding."""
    hmf = compare_routes(game, state, "Heavy Modular Frame")
    assert len(hmf.feasible) == len(hmf.routes) == 2
    alt = _by_recipe(hmf, "Recipe_Alternate_ModularFrameHeavy_C")
    base = _by_recipe(hmf, "Recipe_ModularFrameHeavy_C")
    assert base.per_unit / alt.per_unit == pytest.approx(1.81, abs=0.01)
    assert hmf.routes[0].recipe == alt.recipe

    for item in ("Motor", "Battery", "Reinforced Iron Plate"):
        cmp = compare_routes(game, state, item)
        assert cmp.feasible, item


def test_a_route_that_skips_the_ranking_resource_is_not_called_a_dead_chain(game, state):
    """Silica from Raw Quartz consumes no Bauxite, so this table has no denominator
    for it. That is a missing column, not a broken chain: explaining it with the
    stranded-byproduct note told the player a perfectly buildable route could not
    close."""
    silica = compare_routes(game, state, "Silica")
    quartz = _by_recipe(silica, "Recipe_Silica_C")
    assert quartz.status == "unbounded"
    assert any("buildable" in n and "per_resource" in n for n in silica.notes)
    assert not any("cannot close" in n for n in silica.notes)


def test_the_resources_the_per_unit_column_does_not_price_are_named(game, state):
    """One denominator prices one resource. "60 oil -> 30 Computer" is the whole
    story for Fuel and a lie for a Computer, which also eats copper, caterium,
    quartz and iron -- so the unpriced raws are named rather than left to be
    inferred from a column that never mentions them."""
    computer = compare_routes(game, state, "Computer")
    note = next(n for n in computer.notes if n.startswith("ranked on"))
    assert "Crude Oil alone" in note
    for missing in ("Caterium Ore", "Copper Ore", "Iron Ore", "Raw Quartz"):
        assert missing in note

    # Water is excluded by design, so an oil chain -- whose only other raw IS water
    # -- must not carry the caveat and pay its characters.
    assert not any(n.startswith("ranked on") for n in compare_routes(game, state, "Fuel").notes)


def test_unknown_item_and_bad_arguments_raise(game, state):
    """A typo'd item that silently compared something else would answer a
    different question with full confidence."""
    with pytest.raises(ValueError, match="no item matching"):
        compare_routes(game, state, "Unobtanium")
    with pytest.raises(ValueError, match="rate must be positive"):
        compare_routes(game, state, "Fuel", rate=0)
    with pytest.raises(ValueError, match="not a raw resource"):
        compare_routes(game, state, "Fuel", per_resource="Plastic")


# ------------------------------------------------------- context budget


def test_response_stays_inside_the_context_budget(fuel, game, state):
    """Context is the binding constraint of the whole server. A route table that
    grows a column per release is how that budget regresses."""
    text = render_comparison(fuel)
    assert len(text) < 1300, len(text)
    data_rows = [ln for ln in text.splitlines() if "\t" in ln]
    assert len(data_rows) == 1 + len(fuel.routes)  # header + one row per route

    hor = render_comparison(compare_routes(game, state, "Heavy Oil Residue"), limit=2)
    assert "showing 2" in hor


def test_the_probe_rate_is_stated_in_the_units_a_player_can_check(fuel):
    """ "60 crude -> 160 Fuel" is checkable in game; a bare ratio of 2.67 is not."""
    text = render_comparison(fuel)
    assert f"{PROBE_RATE:.0f}oil->" in text
    assert "\t160\t" in text

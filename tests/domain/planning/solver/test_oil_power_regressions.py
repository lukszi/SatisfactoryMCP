"""Three gaps a large oil-power planning session found, and what each cost.

Each test pins the thing that was wrong, not the thing that now works, because the
failures were all quiet: a ban that did nothing, a constraint that was never modelled,
and a build instruction for a machine at 2% clock.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.optimize import build_processes, solve
from satisfactory_mcp.domain.planning.scenario import build_scenario
from tests.support.reference_world import REFERENCE_SOURCES

pytestmark = pytest.mark.integration

# ------------------------------------------- 1. excluding a generator burn


def test_a_generator_burn_can_be_excluded_by_the_label_the_table_prints(game, state):
    """Generator burn is synthesised from building data, not from Docs.json -- there is
    no recipe object, so exclude_recipes could never match it. "Coal-Powered Generator
    on Coal" hit nothing, and the only recourse was deleting rows by hand and hoping
    the subgraph was isolated."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal-Powered Generator on Coal"],
    )
    assert not req.recipe_errors, req.recipe_errors
    labels = {p.label for p in build_processes(req.scenario)}
    assert "Coal-Powered Generator on Coal" not in labels
    assert any("Coal-Powered Generator" in x for x in labels), "only the coal burn goes"


def test_the_building_name_alone_bans_every_fuel_it_burns(game, state):
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal-Powered Generator"],
    )
    labels = {p.label for p in build_processes(req.scenario)}
    assert not any("Coal-Powered Generator" in x for x in labels)


def test_a_fuel_name_bans_burning_it_even_though_it_also_names_recipes(game, state):
    """Recipe-first precedence was wrong: "Coal" matches Biocoal/Charcoal/Compacted
    Coal, so under it the pattern never reached the generators and "do not burn coal
    here" quietly did the opposite. Every pattern is now offered to both."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal"],
    )
    labels = {p.label for p in build_processes(req.scenario)}
    assert not any(x.endswith("on Coal") for x in labels)
    assert any("Compacted Coal" in name for name in req.excluded), "recipes banned too"


def test_a_pattern_matching_neither_still_refuses(game, state):
    """The one behaviour the reporter asked to keep: it warned rather than silently
    ignoring the argument."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Nonsense Thing"],
    )
    assert any("nothing matches" in e for e in req.recipe_errors)


# ----------------------------------------------------- 2. water siting


def test_water_extractors_can_be_capped_to_what_a_site_holds(game, state):
    """Water was the binding constraint on the real build and the model had no opinion:
    a plan wanted 105 extractors and 12,400 m3/min, more than its Fuel.

    The cap exists so a player can state a measured limit. It is NOT a shoreline count --
    pumps sit on platforms built out over open water -- so what it stands in for is
    however much water the player is willing to floor over."""
    kw = dict(sources=REFERENCE_SOURCES, objective="max_mw", exports=["MW"])
    free = solve(build_scenario(game, state, **kw).scenario)
    capped = solve(build_scenario(game, state, water_extractors=5, **kw).scenario)
    assert free.ok and capped.ok

    def pumps(sol):
        return sum(
            p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
        )

    assert pumps(capped) <= 5
    assert pumps(capped) < pumps(free)
    assert capped.net_mw < free.net_mw, "a real constraint must cost something"


def test_the_default_water_cap_is_an_assumption_not_a_measurement(game):
    """It is the only number in the constants register with no data behind it, which makes
    reading it as capacity dangerous. It is NOT too high to bind either -- a whole-map
    max_mw takes all 200 -- so ``tests/domain/planning/readout/test_water_assumption`` pins that every plan says which
    of the two it is."""
    from satisfactory_mcp.core.gamedata.constants import WATER_EXTRACTOR_CAP_ASSUMED

    assert WATER_EXTRACTOR_CAP_ASSUMED >= 100


def test_a_water_pump_reports_its_resource_rather_than_a_question_mark(game, state):
    """A Water Extractor sits on an FGWaterVolume, which is not a node and has no
    purity, so both columns read "?" and a working pump looked broken."""
    from satisfactory_mcp.domain.factories.query import build_view

    pumps = [
        r["instance"].rsplit(".", 1)[-1]
        for r in state.projection.get("extractors", ())
        if "WaterPump" in r["cls"]
    ]
    if not pumps:
        pytest.skip("this save has no water extractors")
    view = build_view("probe", pumps, state.graph, game, state.projection)
    resources = {row[1] for row in view.nodes}
    assert "?" not in resources
    assert "Water" in resources


# --------------------------------------------- 3. degenerate clock rows


def test_clock_modes_of_one_node_set_collapse_to_a_single_row(game, state):
    """Offering a node set at several clocks makes one column per mode sharing a node
    cap, so the LP may split arbitrarily: 0.615 machine-equivalents at 100% plus 0.0201
    at 150%. That printed as two rows with an IDENTICAL label, the second a whole miner
    at 2% clock, which reads as a real build instruction."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
    )
    sol = solve(req.scenario)
    assert sol.ok
    built = [p for p in sol.processes if p["machines"]]
    labels = [p["label"] for p in built]
    assert len(labels) == len(set(labels)), f"duplicate rows: {labels}"


def test_folding_preserves_what_was_extracted(game, state):
    """Extraction is linear in clock, so pooling v*clock and re-emitting at one mode is
    exact. If it were not, the fold would quietly change the plan."""
    kw = dict(sources=REFERENCE_SOURCES, objective="max_mw", exports=["MW"])
    one = solve(build_scenario(game, state, **kw).scenario)
    many = solve(build_scenario(game, state, extractor_clocks=[1], **kw).scenario)
    assert one.ok and many.ok
    assert one.net_mw == pytest.approx(many.net_mw, rel=1e-6)


# ------------------------------------- follow-ups from the second report


@pytest.mark.parametrize("cap", [5, 27, 54, 96])
def test_the_water_cap_holds_at_every_value(game, state, cap):
    """water_extractors=54 came back as 64. The fold pooled NODE-UNITS, sum(v*clock),
    and re-expressed them at one mode's clock -- but the cap bounds MACHINE COUNT,
    sum(v). Once modes mix those differ, and 54 machines' worth of units re-read at a
    lower clock needs more machines. It only looked right at 27 because that solution
    happened to use a single mode."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=cap,
    )
    sol = solve(req.scenario)
    assert sol.ok
    built = sum(p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C")
    assert built <= cap, f"cap {cap} exceeded by {built - cap}"


def test_a_group_cap_is_reported_binding_even_when_split_across_modes(game, state):
    """Binding was tested per process against its own max_count, but grouped modes share
    ONE cap. A solve spreading extractors over two clocks left every column below the
    cap and reported nothing binding -- while the cap was fully consumed."""
    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=8,
    )
    sol = solve(req.scenario)
    assert sol.ok
    assert any("Water Extractor" in b for b in sol.binding), sol.binding


def test_a_negligible_process_is_unlisted_but_still_counted(game, state):
    """A degenerate basis can leave a recipe column at 0.0001 machine-equivalents making
    0.0017/min -- one item every ten hours. Unlike a clock-mode split there is nothing to
    fold it into. Dropping the ROW is right; dropping the MACHINE is not, and doing both
    silently turned a measured "9 buildings" into 8 in compare_recipe_options."""
    from satisfactory_mcp.domain.planning import optimize as opt

    req = build_scenario(
        game,
        state,
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW", "Plastic", "Rubber"],
        export_minimums={"Plastic": 2000, "Rubber": 300},
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=27,
        exclude_recipes=["Turbofuel", "Alternate: Compacted Coal", "Coal-Powered Generator"],
    )
    sol = solve(req.scenario)
    assert sol.ok
    listed = sum(p["machines"] for p in sol.processes)
    assert listed < sol.machines_total, "the omitted machine is still in the total"
    assert any("contribute under" in w for w in sol.warnings), sol.warnings
    assert all(p["clock"] >= opt.NEGLIGIBLE_IPM / 1000 for p in sol.processes), (
        "no vanishing rows survive in the table"
    )


# ------------------------------------------------- building footprints


def test_every_production_building_has_a_footprint(game):
    """They are extracted from mClearanceData and used by plan_layout, but were not
    exposed anywhere until asked for."""
    missing = [b.name for b in game.buildings.values() if b.is_manufacturer and not b.footprint]
    assert missing == []


def test_the_fuel_generator_footprint_survives_box_rotation(game):
    """The regression the footprint module exists for: its clearance is several thin
    boxes at 45-degree increments approximating a round machine. Taking the largest box
    naively gives 22x4m instead of ~20x20 -- about 1,000 foundations understated across
    a 176-generator plan."""
    fp = game.buildings["Build_GeneratorFuel_C"].footprint
    assert fp is not None
    assert min(fp.width_m, fp.depth_m) > 15, f"looks like an unrotated thin box: {fp}"
    assert fp.foundations == 9


def test_foundations_round_up_per_axis(game):
    """A 5x10m Smelter occupies two 8m foundations, not one: the axes round separately."""
    fp = game.buildings["Build_SmelterMk1_C"].footprint
    assert (fp.width_m, fp.depth_m) == (5.0, 10.0)
    assert fp.foundations == 2


def test_list_buildings_exposes_size_and_foundations(game):
    from satisfactory_mcp import server as srv

    out = srv.list_buildings("production")
    # "have" leads the header now: a row says whether the save can build the thing, which
    # is what stops a planner assuming a tier it has not unlocked.
    header = next(line for line in out.splitlines() if "\tbuilding\t" in line)
    assert header.startswith("have\t")
    assert "size" in header and "found" in header
    assert "18x20x11m" in out, "Manufacturer size should be listed"


def test_the_water_warning_quotes_real_geometry(game, state):
    """The whole point of surfacing footprints here: "siting is not modelled" is
    abstract, "96 of them cover 34,560 m2" is something you can check against a
    platform."""
    from satisfactory_mcp import server as srv

    fp = game.buildings["Build_WaterPump_C"].footprint
    assert fp is not None and fp.area_m2 > 0
    out = srv.plan_factory(
        sources=REFERENCE_SOURCES,
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
    )
    warn = next(line for line in out.splitlines() if "Water Extractor(s)" in line)
    # The platform and its concrete, which is what actually costs something, in BOTH
    # shapes: the block is the cheapest way to buy the area and the pier length is the
    # number you lay platform modules against.
    assert "Concrete)" in warn
    assert "pier" in warn
    assert "Shoreline is NOT the limit" in warn
    # The frontage figure this used to assert was answering a question nobody had.
    # Pumps do not line a shore, they sit on floors built out over open water, and
    # quoting metres-of-shoreline made ordinary large water plans look impossible.
    assert "of shoreline" not in warn
    # Packed, not n x footprint. The per-machine count ignores shared edges and
    # overstates the concrete by about a third, so the naive figure appears only as
    # the thing being corrected.
    assert "ignores shared edges" in warn


# ------------------------------------------------- packing machines onto foundations


def test_packing_beats_counting_machines_one_at_a_time(game):
    """`Footprint.foundations` says outright that it ignores shared edges, so
    `n x foundations` is an upper bound and not a build. Two Water Extractors side by side
    span 40 m and need 5 tiles, not 6, and across 77 pumps that gap is a third of the
    concrete -- 448 against 693."""
    fp = game.buildings["Build_WaterPump_C"].footprint
    packed = fp.pack(77)
    assert packed.foundations == 448
    assert packed.foundations < 77 * fp.foundations
    assert packed.count == 77


def test_a_pack_always_holds_every_machine(game):
    """A grid that quietly dropped the remainder would understate the platform."""
    fp = game.buildings["Build_WaterPump_C"].footprint
    for n in (1, 2, 7, 30, 64, 77, 105, 200):
        for columns in (0, 1, 3):
            packed = fp.pack(n, columns=columns)
            assert packed.columns * packed.rows >= n
            assert packed.width_m >= fp.width_m
            assert packed.depth_m >= fp.depth_m


def test_the_default_block_beats_a_pier_on_concrete(game):
    """Cheapest-buildable is the default, not squarest -- squarest was wrong, see pack().
    The pier is offered anyway because its LENGTH is the number you lay modules against."""
    fp = game.buildings["Build_WaterPump_C"].footprint
    block, pier = fp.pack(77), fp.pack(77, columns=1)
    assert block.foundations < pier.foundations
    assert pier.columns == 1 and pier.rows == 77
    assert pier.depth_m == pytest.approx(77 * fp.depth_m)


def test_one_machine_packs_to_its_own_footprint(game):
    fp = game.buildings["Build_WaterPump_C"].footprint
    one = fp.pack(1)
    assert one.foundations == fp.foundations
    assert (one.columns, one.rows) == (1, 1)


def test_packing_is_never_worse_than_the_arithmetic_it_replaced(game):
    """The guarantee that makes this strictly an improvement rather than one that is
    better on average and worse in places. It holds because the single row is always a
    candidate and ceil is subadditive -- an aspect cap alone broke it for 567 small cases,
    e.g. a 5-machine Lookout Tower block at 6 tiles against the old 5."""
    checked = 0
    for building in game.buildings.values():
        fp = building.footprint
        if fp is None:
            continue
        for n in (1, 2, 3, 5, 7, 12, 24, 64, 77, 105):
            packed = fp.pack(n)
            assert packed.foundations <= n * fp.foundations, (building.cls, n)
            assert packed.columns * packed.rows >= n
            checked += 1
    assert checked > 500


def test_the_default_block_is_a_shape_someone_would_build(game):
    """Unconstrained, the cheapest pack for 77 Water Extractors is a 40x702 m ribbon. It
    is correct arithmetic and not a build, so the default is capped at MAX_BLOCK_ASPECT."""
    from satisfactory_mcp.core.gamedata.footprint import MAX_BLOCK_ASPECT

    fp = game.buildings["Build_WaterPump_C"].footprint
    block = fp.pack(77)
    assert max(block.width_m, block.depth_m) <= MAX_BLOCK_ASPECT * min(block.width_m, block.depth_m)
    assert block.foundations == 448
    # The ribbon is still reachable when explicitly asked for.
    assert fp.pack(77, columns=2).foundations < block.foundations


# ----------------------------------------------------- water recycling and zero caps


def test_a_recycled_fluid_balances_without_being_asked_for(game, state):
    """Aluminium is the canonical loop: Alumina Solution drinks water and Aluminum Scrap
    gives some back. Nothing recycles it explicitly -- water is ONE balance row and the
    equality does the work, which is why a byproduct cannot pile up here."""
    from satisfactory_mcp.domain.planning.prepare import prepare

    plan = prepare(
        game,
        state,
        dict(
            objective="max_item",
            target_item="Aluminum Ingot",
            exports=["Aluminum Ingot"],
            sources=["region:Titan Forest"],
        ),
    )
    if not plan.ok or not plan.solution.exports:
        pytest.skip("no aluminium plan on this save")
    water = {
        r["label"]: r["rates"]["Desc_Water_C"]
        for r in plan.solution.processes
        if r["rates"].get("Desc_Water_C")
    }
    assert sum(water.values()) == pytest.approx(0.0, abs=1e-6)
    # Some of the supply is RECYCLED, not extracted -- that is the whole point.
    assert any(v > 0 and "Extractor" not in k for k, v in water.items())
    # And none of it is sunk, because a fluid cannot be.
    assert "Desc_Water_C" not in plan.solution.sunk
    assert game.items["Desc_Water_C"].sinkable is False


def test_zero_water_extractors_means_zero(game, state):
    """`int(x) if x else DEFAULT` turned an explicit 0 into the 200-pump assumption, so a
    plan told it had no water came back making 480 Aluminium Ingots on 480 m3/min of it.
    Zero is a meaningful answer -- it is what you ask of an inland site."""
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    key = ("Build_WaterPump_C", "Desc_Water_C", "normal")
    kw = dict(objective="max_item", target_item="Aluminum Ingot", exports=["Aluminum Ingot"])
    assert (
        build_scenario(game, state, water_extractors=0, **kw).scenario.extractor_nodes.get(key)
        is None
    )
    assert (
        build_scenario(game, state, water_extractors=27, **kw).scenario.extractor_nodes.get(key)
        == 27
    )
    # None still means "use the assumption", which is a different statement from zero.
    assert (
        build_scenario(game, state, water_extractors=None, **kw).scenario.extractor_nodes.get(key)
        == 200
    )


def test_an_empty_plan_says_why_it_is_empty(game, state):
    """An all-zero solve is OPTIMAL and reads as success: "buildings=0, exports:" with no
    complaint. Every recipe is present and unlocked, so `unmakeable` finds nothing to
    report -- the supply probe has to run for the same reason it runs on INFEASIBLE."""
    from satisfactory_mcp.domain.planning.prepare import prepare

    plan = prepare(
        game,
        state,
        dict(
            objective="max_item",
            target_item="Aluminum Ingot",
            exports=["Aluminum Ingot"],
            sources=["region:Titan Forest"],
            water_extractors=0,
        ),
    )
    assert plan.ok
    assert plan.solution.machines_total == 0
    assert any("EMPTY" in n for n in plan.notes)
    assert any("Water" in n for n in plan.notes)

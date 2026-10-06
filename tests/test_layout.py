"""Footprints and the layout schematic.

The layout is a schematic, not a blueprint, so these tests pin the things that are
genuinely derived -- footprints, block splitting, chain depth, floor stacking -- and
not the aesthetic choices.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp.core.gamedata.footprint import FOUNDATION_M, extract_footprint
from satisfactory_mcp.domain.planning.layout import LOGISTICS_FLOOR_M, build_layout
from satisfactory_mcp.domain.planning.solver.model import MW, Scenario
from satisfactory_mcp.domain.planning.solver.optimize import solve

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
WATER = "Desc_Water_C"


# ------------------------------------------------------------- footprints


@pytest.mark.parametrize(
    "cls,w,d",
    [
        ("Build_ConstructorMk1_C", 8, 10),
        ("Build_AssemblerMk1_C", 9, 16),
        ("Build_OilRefinery_C", 10, 22),
        ("Build_Blender_C", 18, 16),
        ("Build_ManufacturerMk1_C", 18, 20),
    ],
)
def test_footprints_match_known_dimensions(game, cls, w, d):
    fp = game.buildings[cls].footprint
    assert fp is not None
    assert (fp.width_m, fp.depth_m) == pytest.approx((w, d), abs=0.5)


def test_rotated_clearance_boxes_are_transformed(game):
    """The Fuel Generator's clearance is thin boxes at 45-degree increments around a
    round machine. Taking the largest box naively gives 22x4; the real footprint is
    ~20x20, a ~1000-foundation error across a 176-generator plan."""
    fp = game.buildings["Build_GeneratorFuel_C"].footprint
    assert fp.width_m == pytest.approx(20, abs=1)
    assert fp.depth_m == pytest.approx(20, abs=1)
    assert fp.foundations == 9  # 3x3 at 8 m


def test_every_production_building_has_a_footprint(game):
    """A missing footprint silently under-sizes a layout, so normalize warns and this
    keeps the warning honest."""
    for b in game.buildings.values():
        if b.is_manufacturer or b.is_generator:
            assert b.footprint is not None, b.cls


def test_foundations_round_up_to_the_grid(game):
    fp = game.buildings["Build_OilRefinery_C"].footprint
    # 10 x 22 m spans 2 x 3 eight-metre foundations.
    assert fp.foundations == 6
    assert FOUNDATION_M == 8.0


def test_no_clearance_data_returns_none():
    assert extract_footprint(None) is None
    assert extract_footprint("") is None


@pytest.mark.parametrize(
    "cls,w,d,h",
    [
        ("Build_Foundation_8x1_01_C", 8, 8, 1),
        ("Build_Foundation_8x4_01_C", 8, 8, 4),
        ("Build_Wall_8x4_01_C", 0.5, 8, 4),
        ("Build_PillarBase_C", 8, 8, 4),  # the Big Pillar Support the player measured
        ("Build_Ramp_8x4_01_C", 8, 8, 4),
        ("Build_Beam_C", 4, 0.8, 1),  # the rotated soft box: 4 m LONG, not 4 m tall
    ],
)
def test_architecture_pieces_have_sizes(game, cls, w, d, h):
    """Architecture carries only CT_Soft clearance boxes -- soft is how the game lets
    pieces clip together -- so the old skip-all-soft rule reported every foundation,
    wall, pillar, ramp and beam as sizeless and a player measured one in-game. With no
    hard box, the soft union IS the piece. Heights asserted too: a 1 m and a 4 m
    foundation differ in nothing else."""
    fp = game.buildings[cls].footprint
    assert fp is not None
    assert (fp.width_m, fp.depth_m, fp.height_m) == pytest.approx((w, d, h), abs=0.5)


def test_soft_boxes_still_do_not_inflate_machines(game):
    """The fallback must never mix soft into hard: on a machine the soft boxes are
    overlap allowances AROUND the hard volume, and counting them would regress the
    Fuel Generator's 3x3-foundation answer that test_rotated_clearance_boxes pins."""
    fp = game.buildings["Build_GeneratorFuel_C"].footprint
    assert fp.foundations == 9


# ----------------------------------------------------------------- layout


@pytest.fixture(scope="module")
def oil_layout(game):
    sol = solve(
        Scenario(
            game=game,
            recipes=[
                "Recipe_Alternate_HeavyOilResidue_C",
                "Recipe_Alternate_DilutedFuel_C",
                "Recipe_ResidualPlastic_C",
            ],
            objective="max_mw",
            exports=(MW, "Desc_Plastic_C"),
            raw_caps={CRUDE: 1200.0, WATER: 1e5},
        )
    )
    assert sol.ok
    return sol, build_layout(game, sol)


def test_blocks_are_split_by_throughput_not_taste(oil_layout, game):
    """Line count IS block count: a manifold cannot be fed by three pipes."""
    _sol, lay = oil_layout
    split = [b for b in lay.blocks if b.parts > 1]
    assert split, "expected at least one process to exceed a single line"
    for b in split:
        # Each sub-block's own flow must now fit within its carriers.
        for item, rate in {**b.inputs, **b.outputs}.items():
            capacity = 600.0 if game.items[item].is_fluid else 780.0
            assert rate <= capacity * 1.0001, (b.name, item, rate)


def test_split_blocks_conserve_machines(oil_layout):
    sol, lay = oil_layout
    by_label: dict[str, int] = {}
    for b in lay.blocks:
        by_label[b.label] = by_label.get(b.label, 0) + b.machines
    for proc in sol.processes:
        assert by_label[proc["label"]] == proc["machines"]


def test_stages_follow_the_chain(oil_layout, game):
    """Extractors at the bottom, generators at the top."""
    _sol, lay = oil_layout
    stages = {b.label: b.stage for b in lay.blocks}
    hor = next(s for lbl, s in stages.items() if "Heavy Oil Residue" in lbl)
    fuel = next(s for lbl, s in stages.items() if "Diluted Fuel" in lbl)
    gen = next(s for lbl, s in stages.items() if "Generator" in lbl)
    assert hor < fuel < gen


def test_stage_assignment_terminates_on_a_cyclic_recipe_graph(game):
    """Recycled Plastic and Recycled Rubber consume each other's output, so a
    topological sort would fail. Relaxation must settle instead of looping."""
    sol = solve(
        Scenario(
            game=game,
            recipes=[
                "Recipe_Alternate_Plastic_1_C",
                "Recipe_Alternate_RecycledRubber_C",
                "Recipe_Alternate_HeavyOilResidue_C",
                "Recipe_Alternate_DilutedFuel_C",
            ],
            objective="max_item",
            target_item="Desc_Plastic_C",
            exports=("Desc_Plastic_C",),
            raw_caps={CRUDE: 600.0, WATER: 1e5},
            grid_import_mw=1e6,
        )
    )
    assert sol.ok
    lay = build_layout(game, sol)
    assert lay.blocks
    assert all(b.stage < len(lay.blocks) + 1 for b in lay.blocks)


def test_floors_alternate_production_and_logistics(oil_layout):
    _sol, lay = oil_layout
    kinds = [f.kind for f in lay.floors]
    assert kinds[0] == "production"
    assert kinds[-1] == "production"
    for a, b in pairwise(kinds):
        assert a != b, kinds


def test_floor_height_clears_the_tallest_machine(oil_layout):
    _sol, lay = oil_layout
    for f in lay.floors:
        if f.kind == "production" and f.blocks:
            assert f.height_m >= max(b.height_m for b in f.blocks)
        else:
            assert f.height_m == LOGISTICS_FLOOR_M


def test_site_is_sized_by_the_largest_floor_not_the_sum(oil_layout):
    """Floors stack, so the site footprint is the peak floor, not the total."""
    _sol, lay = oil_layout
    assert lay.foundations == max(f.foundations for f in lay.floors)
    assert lay.foundations <= lay.total_foundations
    assert lay.site_side_m() > 0


def test_buses_never_flow_backwards_down_the_stack(oil_layout):
    """An item with no on-site consumer leaves at the level it is made. Defaulting
    its destination to stage 0 rendered as flowing back down."""
    _sol, lay = oil_layout
    for bus in lay.buses:
        assert bus.to_stage >= bus.from_stage, (bus.name, bus.from_stage, bus.to_stage)


def test_bus_rates_match_the_solve(oil_layout, game):
    sol, lay = oil_layout
    by_item = {b.item: b for b in lay.buses}
    for entry in sol.logistics:
        if entry["item"] in by_item and not by_item[entry["item"]].external:
            assert by_item[entry["item"]].rate == pytest.approx(entry["rate"], rel=1e-3)


def test_layout_excludes_power_from_buses(oil_layout):
    """MW is a pseudo-item for the balance; it does not ride a belt."""
    _sol, lay = oil_layout
    assert MW not in {b.item for b in lay.buses}
    for b in lay.blocks:
        assert MW not in b.inputs and MW not in b.outputs


def test_plan_layout_takes_the_same_solve_arguments_as_plan_factory(game, state):
    """It re-solved at defaults, so it schematised a DIFFERENT plan than the one it was
    asked to draw -- measured at 15,043 MW against an 83,737 MW plan, because base
    extraction is roughly a sixth of overclocked. Four arguments were missing."""
    import inspect

    from satisfactory_mcp import server as srv

    factory_args = set(inspect.signature(srv.plan_factory).parameters)
    layout_args = set(inspect.signature(srv.plan_layout).parameters)
    shaping = {"clocks", "extractor_clocks", "machine_cost_mw", "water_extractors"}
    assert shaping <= factory_args
    assert shaping <= layout_args, f"plan_layout still cannot express: {shaping - layout_args}"


def test_the_two_tools_agree_on_the_same_request(game, state):
    from satisfactory_mcp import server as srv

    kw = dict(
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=64,
        limit=1,
    )
    f = srv.plan_factory(**kw)
    lay = srv.plan_layout(**kw)

    def mw(text):
        line = next(x for x in text.splitlines() if x.startswith("net_MW"))
        return line.split()[0].split("=")[1]

    assert mw(f) == mw(lay), "the layout must schematise the plan it was given"


def test_fluid_head_names_what_the_floor_order_costs(game, state):
    """Floors follow chain depth, which is a correctness property, not a physics one.
    On a measured oil plan it made every fluid climb -- water four floors at 11,500
    m3/min. The model has no terrain, so the cost is reported rather than optimised."""
    from satisfactory_mcp.domain.planning.layout import build_layout, fluid_head
    from satisfactory_mcp.domain.planning.solver.optimize import solve
    from satisfactory_mcp.domain.planning.solver.scenario import build_scenario

    req = build_scenario(
        game,
        state,
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW", "Plastic", "Rubber"],
        export_minimums={"Plastic": 2000, "Rubber": 300},
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=64,
    )
    head = fluid_head(build_layout(game, solve(req.scenario)))
    assert head, "an oil plan moves fluids between floors"
    water = [d for d in head if d["item"] == "Water"]
    assert water and water[0]["direction"] == "climbs"
    assert head[0]["floors"] >= water[0]["floors"], "sorted by how far it is lifted"


def test_the_three_planning_tools_share_one_pipeline(game, state):
    """They ran the same seven-step prologue three times, and the copies drifted:
    plan_layout stopped accepting extractor_clocks and water_extractors and silently
    re-solved at defaults. One implementation cannot drift from itself."""
    import inspect

    from satisfactory_mcp.domain.planning import diff_service as diff_mod
    from satisfactory_mcp.domain.planning import layout_service as layout_mod
    from satisfactory_mcp.domain.planning.readout import report as report_mod
    from satisfactory_mcp.interfaces.mcp.tools import planning

    # All three reach the pipeline through a domain service -- prepare plus the world
    # lookups each of them implies. Still one implementation of the prologue, not a
    # fourth copy of it.
    assert "prepare(" in inspect.getsource(report_mod)
    assert "prepare(" in inspect.getsource(layout_mod)
    assert "prepare(" in inspect.getsource(diff_mod)
    for name in ("plan_factory", "plan_layout", "diff_vs_save"):
        src = inspect.getsource(getattr(planning, name))
        assert any(
            call in src
            for call in (
                "prepare(",
                "build_plan_report(",
                "build_layout_report(",
                "build_diff_report(",
            )
        ), f"{name} does not use the shared pipeline"
        assert "build_scenario(" not in src, f"{name} still builds its own scenario"
        assert "= solve(" not in src, f"{name} still solves for itself"


def test_prepare_renders_nothing(game, state):
    """The pipeline is reusable only if it is free of presentation. A failure comes back
    as a headline plus notes and the TOOL decides how to show it."""
    import inspect

    from satisfactory_mcp.domain.planning.solver import prepare as prepare_mod

    src = inspect.getsource(prepare_mod)
    assert "render." not in src
    assert "envelope" not in src


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (dict(sources=["region:Nowhere"], exports=["MW"]), "no sources selected"),
        (dict(exports=["Plastik"]), "unusable exports"),
    ],
)
def test_every_planning_tool_reports_a_bad_request_the_same_way(game, state, kwargs, expected):
    """Shared guards mean shared wording. Before, each tool spelled these out itself."""
    from satisfactory_mcp import server as srv

    for fn in (srv.plan_factory, srv.plan_layout, srv.diff_vs_save):
        out = fn(limit=1, **kwargs)
        assert expected in out.splitlines()[0], (fn.__name__, out.splitlines()[0])


def test_prepare_is_usable_without_the_mcp_layer(game, state):
    """The point of the extraction: a script or a batch planner can solve without going
    through a tool, and gets the same guards."""
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    good = prepare(
        game, state, {"objective": "max_mw", "sources": list(REFERENCE_FIELD), "exports": ["MW"]}
    )
    assert good.ok and good.solution.net_mw > 0

    bad = prepare(
        game, state, {"objective": "max_mw", "sources": ["region:Nowhere"], "exports": ["MW"]}
    )
    assert not bad.ok
    assert bad.failure.headline == "no sources selected"


# ------------------------------------------- capping a deck's footprint


@pytest.fixture(scope="module")
def oil_solution(game, state):
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    return prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW", "Plastic", "Rubber"],
            export_minimums={"Plastic": 2000, "Rubber": 300},
            extractor_clocks=[1, 1.5, 2, 2.5],
            water_extractors=64,
        ),
    ).solution


@pytest.mark.parametrize("cap", [1225, 900, 400])
def test_no_deck_exceeds_the_foundation_cap(game, oil_solution, cap):
    """The inverse of the default question. Uncapped, layout answers "how big a site
    does this need" -- 496x496 m. A player with a finished platform is asking the
    reverse: I have 30x30 foundations, how many decks?"""
    from satisfactory_mcp.domain.planning.layout import build_layout

    lay = build_layout(game, oil_solution, max_floor_foundations=cap)
    production = [f for f in lay.floors if f.kind == "production"]
    oversized = [f for f in production if f.foundations > cap and len(f.blocks) > 1]
    assert not oversized, [(f.index, f.foundations) for f in oversized]


def test_capping_adds_decks_without_changing_the_work(game, oil_solution):
    """Total foundations are conserved: the same machines, stacked differently. If the
    total moved, the cap would be silently dropping or duplicating blocks."""
    from satisfactory_mcp.domain.planning.layout import build_layout

    def totals(cap):
        lay = build_layout(game, oil_solution, max_floor_foundations=cap)
        production = [f for f in lay.floors if f.kind == "production"]
        return len(production), sum(f.foundations for f in production), lay.foundations

    open_decks, open_total, open_peak = totals(0)
    capped_decks, capped_total, capped_peak = totals(900)

    assert capped_total == open_total, "same machines, different stacking"
    assert capped_decks > open_decks
    assert capped_peak <= 900 < open_peak


def test_a_block_larger_than_the_cap_gets_its_own_deck(game):
    """It must not vanish, and it must not be split -- a block is one manifold. The
    honest answer is a deck of its own that exceeds the cap."""
    from satisfactory_mcp.domain.planning.layout import _decks_for

    class B:
        def __init__(self, f):
            self.foundations = f

    big, small = B(500), B(10)
    decks = _decks_for([small, big, small], cap=100)
    assert [len(d) for d in decks] == [1, 1, 1]
    assert decks[1][0] is big


def test_a_zero_cap_means_no_cap(game, oil_solution):
    from satisfactory_mcp.domain.planning.layout import build_layout

    assert [f.index for f in build_layout(game, oil_solution, max_floor_foundations=0).floors] == [
        f.index for f in build_layout(game, oil_solution).floors
    ]


# ------------------------------------------------- one sizing primitive


def test_a_block_is_packed_not_multiplied(game, state):
    """`Footprint.foundations` is per machine and says outright that it ignores shared
    edges, so `n x found` is an upper bound. plan_layout used to charge exactly that, and
    the water-siting note had independently grown its own copy of the same arithmetic --
    two places to be wrong instead of one."""
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )
    lay = build_layout(game, prepared.solution)
    checked = 0
    for block in lay.blocks:
        fp = game.buildings[block.building_id].footprint
        if fp is None:
            continue
        assert block.foundations == fp.pack(block.machines).foundations
        if block.machines > 1:
            assert block.foundations <= fp.foundations * block.machines
            checked += 1
    assert checked, "no multi-machine block to compare"


def test_packing_shrank_the_site_rather_than_the_machine_count(game, state):
    """The correction must move floor area only. A foundation change that also moved
    machines would mean it had eaten part of the plan."""
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )
    lay = build_layout(game, prepared.solution)
    naive = sum(
        game.buildings[b.building_id].footprint.foundations * b.machines
        for b in lay.blocks
        if game.buildings.get(b.building_id) and game.buildings[b.building_id].footprint
    )
    assert lay.total_foundations < naive
    assert lay.machines == sum(p["machines"] for p in prepared.solution.processes)


def test_a_building_with_no_clearance_data_is_not_free(game, state):
    """packed is None only when the dump has no clearance boxes at all. Reporting 0
    foundations there is honest; silently sizing it as a point would not be, so
    build_layout names those buildings in its warnings."""
    from satisfactory_mcp.domain.planning.layout import Block

    bare = Block(
        key="k",
        label="l",
        building_id="x",
        building="X",
        recipe=None,
        machines=4,
        clock=1.0,
        part=1,
        parts=1,
    )
    assert bare.packed is None
    assert bare.foundations == 0
    assert bare.block_width_m == 0.0

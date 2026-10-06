"""diff_vs_save: the matching, the action taxonomy, the staging and the guards.

Every test here pins one way this tool could be CONFIDENTLY WRONG. That is the whole
risk of a diff: an answer that is well-formed, plausible and tells the player to
dismantle a working factory reads exactly like a correct one.

The reference plan is max_mw over Spire Coast with Plastic and Rubber exportable --
the driving use case from DESIGN.md Appendix B, solved against the committed save
projection so the numbers do not drift with the live autosave.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.progress.diff import (
    NEIGHBOUR_RADIUS_M,
    RECLOCK_TOLERANCE,
    _reclock_note,
    build_diff,
)
from satisfactory_mcp.domain.planning.progress.jobs import BuildJob
from satisfactory_mcp.domain.planning.solver.optimize import solve
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.presenters.text import primitives as render

#: See test_surface.py for why ``game`` is requested module-wide: the three tests here that
#: call a tool rather than the domain reach the game through the lru_cache'd ``app.game()``,
#: which is not this suite's fixture and does not skip when the install is absent.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("game")]

#: A diff answers a strictly larger question than plan_factory (1,669 chars measured)
#: and replaces a plan_factory + world_summary + power_report + factory_sites sequence
#: of ~3,500. It must still never grow into a machine dump: the ids it prints are the
#: ones an ACTION applies to, three per row, and never the row's whole matched set.
DIFF_BUDGET = 2900

SPIRE = {
    "objective": "max_mw",
    "sources": list(REFERENCE_FIELD),
    "exports": ["MW", "Plastic", "Rubber"],
}


@pytest.fixture(scope="module")
def spire(game, state):
    req = build_scenario(game, state, **SPIRE)
    sol = solve(req.scenario)
    assert sol.ok
    return req, sol, build_diff(game, state, sol, req)


def _variant(game, state, **projection_changes):
    """A fresh WorldState, because WorldState caches its unlock sets: deep-copying a
    used one carries the cache along and the mutation has no effect."""
    projection = deepcopy(state.projection)
    projection.update(projection_changes)
    return WorldState(projection=projection, game=game)


def _row(report, needle):
    hits = [
        r
        for r in report.rows
        if needle == r.process or (needle in r.process and needle != "pure Crude Oil")
    ]
    assert len(hits) == 1, f"{needle} matched {[r.process for r in hits]}"
    return hits[0]


# ------------------------------------------------------------------- matching


def test_off_recipe_machines_never_count_toward_the_plan(spire, state):
    """This world has 36 Refineries; 5 run Alternate Heavy Oil Residue and 31 make
    copper, plastic and alumina. Matching on the building class alone would say "you
    have 36, build 10" -- arithmetically true, and it would tell the player to break
    their copper line. Identity is (building, recipe)."""
    _req, _sol, rep = spire
    row = _row(rep, "Alternate: Heavy Oil Residue")
    assert state.built("Build_OilRefinery_C") == 36
    assert row.have == 5
    assert row.build == row.need - 5
    # The reuse pool is named so the count cannot be mistaken for an oversight.
    assert "31 Refineries busy on other recipes" in row.note


def test_generators_match_on_building_because_fuel_is_piped_not_set(spire):
    """A generator has no recipe: mCurrentFuelClass is whatever is currently piped in.
    The plan runs 176 Fuel Generators on Fuel and 20 on Turbofuel, but that is 196
    identical buildings and one plumbing decision, not two machines to place. Keeping
    them apart would have counted the 20 already built against the Fuel row only and
    demanded 20 brand-new generators for the Turbofuel row."""
    _req, _sol, rep = spire
    rows = [r for r in rep.rows if r.building_id == "Build_GeneratorFuel_C"]
    assert len(rows) == 1
    assert rows[0].have == 20
    assert rows[0].need == 196
    assert rows[0].build == 176
    assert "176 on Fuel + 20 on Turbofuel" in rows[0].note


def test_extractors_match_through_the_node_they_occupy(spire):
    """The only exact machine-level match the save supports. mExtractableResource
    resolves 13/13 oil pumps, and the plan extractor columns were built from those very
    node rows, so the join needs no inference and no proximity guess.

    On this save every crude node the plan uses already carries a pump -- 7 impure, 4
    normal, 2 pure, the whole 13 -- so all three rows read OK with nothing to build. That
    is the join's strongest form: purity-by-purity equality, which a proximity guess would
    have to hit three times in a row by luck. ``have`` is by rate: the seven impure pumps
    are clocked to eight pumps' worth."""
    _req, _sol, rep = spire
    by_purity = {p: _row(rep, f"{p} Crude Oil") for p in ("impure", "normal", "pure")}
    assert [(r.have, r.need, r.build) for r in by_purity.values()] == [
        (8, 7, 0),
        (4, 4, 0),
        (2, 2, 0),
    ]
    assert sum(len(r.have_instances) for r in by_purity.values()) == 13
    assert all(r.verb == "OK" for r in by_purity.values())
    # A row that DOES ask for extractors names the nodes, and the ids must paste straight
    # back in as node: selectors.
    coal = _row(rep, "impure Coal")
    assert (coal.have, coal.build) == (0, 2)
    assert len(coal.targets) == 2
    assert all(t[0].startswith("BP_ResourceNode") for t in coal.targets)


def test_unmatchable_extractors_report_a_range_not_a_number(spire):
    """Water Extractors have no recipe and all 23 point at FGWaterVolume objects that
    are not node keys (OQ5), so they cannot be attributed to a plant at all. 4 stand at
    the oil plant, 13 at the main base and 6 two and a half kilometres away, almost
    certainly feeding the coal generators. Both 8 and 27 are defensible and both are
    wrong to assert, so the answer is the interval. Counted by rate, the 23 pumps' clocks
    add up to 24 pumps at the plan's 100%."""
    _req, _sol, rep = spire
    row = _row(rep, "normal Water")
    assert len(row.have_instances) == 23 and row.have == 24
    assert row.build_max is not None
    assert (row.build, row.build_max) == (7, 28)
    # Lower bound counts every pump in the world; the upper counts only those standing
    # among the plan machines.
    assert row.build == row.need - row.have
    assert render.where_bands(row.have_distances).count("@") == 3


def test_the_range_only_appears_where_identity_is_actually_missing(spire):
    """A range everywhere would be hedging. Every row that CAN be matched exactly must
    report an exact count."""
    _req, _sol, rep = spire
    ranged = [r for r in rep.rows if r.build_max is not None and r.build_max != r.build]
    assert [r.building_id for r in ranged] == ["Build_WaterPump_C"]


# --------------------------------------------------------------------- clocks


@pytest.mark.parametrize(
    "clock,expected",
    [
        (None, False),
        (1.0, False),
        (1.0 + RECLOCK_TOLERANCE / 2, False),
        (2.5, True),
        (0.375, True),
    ],
)
def test_reclock_compares_the_total_against_the_plans_total(clock, expected):
    """One machine planned at 100%: only a clock that changes the job's total is noted."""
    job = BuildJob(("recipe", "", ""), "recipe", "", "", "", "", "", 1, 1.0, 0.0, [], {})
    assert bool(_reclock_note([{"clock": clock}], 0, job)) is expected


def test_a_ratio_clock_plan_row_asks_nobody_to_reclock(spire):
    """The Fuel Generator row runs at 99.43% and its 20 existing generators are all at
    the default clock. A correct diff says nothing about clocks there."""
    _req, sol, rep = spire
    plan_clock = min(
        p["clock"] for p in sol.processes if p["building_id"] == "Build_GeneratorFuel_C"
    )
    assert plan_clock < 1.0
    assert "planned rate" not in _row(rep, "Fuel-Powered Generator").note


def test_an_overclocked_machine_is_noted_but_never_becomes_the_action(spire):
    """One oil pump runs at 250% where the plan budgets 100%, so the player already
    extracts more crude than the plan asks for. That is worth saying -- the plan is
    understating them -- but it is not a change the plan requires, and the verb must come
    from the count and never from the clock: this row is OK because 7 pumps stand on 7
    nodes, with the 250% mentioned in the note and nowhere else."""
    _req, _sol, rep = spire
    row = _row(rep, "impure Crude Oil")
    assert "of the planned rate" in row.note
    assert row.verb == "OK"
    assert not any(r.verb == "RECLOCK" for r in rep.rows)


# -------------------------------------------------------------------- actions


def test_free_actions_come_before_building(spire):
    """4 Assemblers stand 20 m from the plan Compacted Coal Assembler with no recipe
    set at all. They produce nothing, so re-recipeing them has zero opportunity cost
    and turns "build 4" into four dropdowns. Materials are the expensive resource, so
    anything free must be spent first."""
    _req, _sol, rep = spire
    row = _row(rep, "Alternate: Compacted Coal")
    assert row.verb == "SETRECIPE"
    assert row.reuse == 4
    assert row.build == 0
    assert "no output today" in row.note


def test_paused_machines_count_as_built_and_are_unpaused_not_rebuilt(spire):
    """3 of the 23 Water Extractors are paused by the player. They exist; they just do
    not run. Treating them as absent would have added 3 buildings to the bill."""
    _req, _sol, rep = spire
    row = _row(rep, "normal Water")
    assert row.verb == "UNPAUSE"
    assert row.count == 3
    assert len(row.have_instances) == 23  # paused machines are still built
    assert "then BUILD 7..28" in row.note


def test_an_action_names_the_machines_it_applies_to(spire, state):
    """The row said "UNPAUSE 3, have 23" and named none of them, and the three are not
    the first three matched -- rendering `have_instances[:3]` would have sent the player
    to three pumps that are already running. SETRECIPE is worse: the idle machines it
    takes are not in the matched set at all."""
    _req, _sol, rep = spire
    paused = {r["instance"].rsplit(".", 1)[-1] for r in state.all_records() if r.get("paused")}

    water = _row(rep, "normal Water")
    assert len(water.act_instances) == water.count == 3
    assert set(water.act_instances) <= paused
    assert set(water.act_instances) <= set(water.have_instances)
    assert water.act_instances != water.have_instances[:3], "the paused ones are not the first"

    coal = _row(rep, "Alternate: Compacted Coal")
    assert len(coal.act_instances) == coal.reuse == 4
    assert not set(coal.act_instances) & set(coal.have_instances)

    assert all(not r.act_instances for r in rep.rows if r.verb in ("OK", "BUILD"))


def test_the_ids_of_an_action_reach_the_reader(game, state):
    """The same reusable-footer pattern the build targets already use, per row: an id in
    a row would crowd every other column off it, and a pooled list would lose which verb
    it belongs to."""
    from satisfactory_mcp.domain.planning.progress.diff_service import build_diff_report
    from satisfactory_mcp.presenters.text.diff import render_diff

    report = build_diff_report(game, state, dict(SPIRE), objective="max_mw")
    out = render_diff(game, state, report, objective="max_mw", limit=20)
    water = _row(report.diff, "normal Water")
    assert "# machines to act on, reusable as machine: selectors" in out
    assert f"#   UNPAUSE normal Water: {' '.join(water.act_instances)}" in out
    # Four idle Assemblers, three named, and the fourth is counted rather than dropped.
    assert "(+1 more)" in out


def test_an_idle_machine_is_only_reused_once(game, state):
    """Idle machines are a shared pool. Allocating the same Assembler to two plan rows
    would under-count the build twice over."""
    req = build_scenario(game, state, **SPIRE)
    rep = build_diff(game, state, solve(req.scenario), req)
    assert sum(r.reuse for r in rep.rows) <= len(state.misconfigured)


# ------------------------------------------------------- the no-dismantle guard


def test_there_is_no_dismantle_action_anywhere(spire):
    """Saves are read-only and this is the player factory. Superseded machines are
    named; what to do about them is not the tool call to make."""
    _req, _sol, rep = spire
    assert {r.verb for r in rep.rows} <= {"BUILD", "UNPAUSE", "SETRECIPE", "OK"}


def test_neighbours_are_scoped_by_radius_and_by_shared_materials(spire):
    """Two guards, both needed. The 200 m radius excludes this world 32 Coal
    Generators, which sit 887-1060 m from the oil plant -- without it a max_mw plan
    would list every generator in the world as superseded. The shared-item test
    excludes the main base, which one matched Assembler standing in it would otherwise
    sweep in: an earlier run listed 22 Iron Ingot Smelters and 19 Iron Rod Constructors.

    What survives both is the right answer: the Diluted Packaged Fuel route, which the
    37 Blenders replace."""
    _req, _sol, rep = spire
    labels = dict(rep.neighbours)
    assert "Refinery Alternate: Diluted Packaged Fuel" in labels
    assert not any("Ingot" in label or "Iron Rod" in label for label in labels)
    assert not any("Generator" in label for label in labels)
    assert NEIGHBOUR_RADIUS_M == 200.0


# ------------------------------------------------------------ staging and power


def test_stages_run_extractors_first_and_generators_last(spire):
    """Build order is the plan own chain depth, condensed so the genuine Recycled
    Plastic / Recycled Rubber cycle shares a stage. Nothing is special-cased:
    extractors fall out at the bottom because they consume nothing."""
    _req, _sol, rep = spire
    stage = {r.process: r.stage for r in rep.rows}
    assert stage["normal Water"] == stage["impure Coal"] == 1
    # Alt HOR feeds the Blender, whose Fuel feeds the Turbofuel Refinery, whose fuel
    # feeds the generators -- so both Refinery rows must NOT share a stage.
    assert stage["Alternate: Heavy Oil Residue"] < stage["Alternate: Diluted Fuel"]
    assert stage["Alternate: Diluted Fuel"] < stage["Turbofuel"]
    assert stage["Fuel-Powered Generator"] == max(r.stage for r in rep.rows)


def test_the_power_arithmetic_is_incremental_not_the_plan_total(spire, state):
    """The plan draws ~9,800 MW across 339 buildings, but 20 Fuel Generators, 5
    Refineries and 10 Oil Extractors already exist and already draw. Charging the plan
    own total would double-count them and demand far more slices than the build needs."""
    _req, sol, rep = spire
    plan_draw = sum(-p["mw"] for p in sol.processes if p["mw"] < 0)
    assert 0 < rep.deficit_mw < plan_draw
    assert rep.headroom_mw == pytest.approx(state.power_report()["headroom_mw"])


def test_slices_come_from_the_deficit_against_real_headroom(spire):
    """An LP solution is a ray, so any fraction of it is itself feasible and
    self-powered. That turns "power first?" from folklore into a number: 4,655 MW of
    new draw against 831 MW of headroom is at least six proportional slices."""
    _req, _sol, rep = spire
    assert rep.slices == -(-rep.deficit_mw // rep.headroom_mw)
    assert rep.slices > 1


# ------------------------------------------------------------------------ cost


def test_cost_uses_spendable_stock_and_never_machine_buffers(spire, state):
    """Summing every stack in the world reported Water 5,556,375 and Fuel 1,048,762 --
    pipe and machine contents in litres -- so a build-cost check against that would
    have said the player can afford anything."""
    _req, _sol, rep = spire
    stock = state.stock()
    for line in rep.cost:
        assert line.stock == pytest.approx(stock.get(line.item, 0.0))
    assert all(line.shortfall > 0 for line in rep.cost)


def test_cost_names_the_missing_production_line_not_just_the_big_number(spire):
    """8,800 Rubber with zero machines making Rubber is a different problem from 8,800
    Quickwire with fourteen. The gate on this plan is the line that does not exist."""
    _req, _sol, rep = spire
    top = rep.cost[0]
    assert top.name == "Rubber"
    assert top.lines == 0
    assert "Heavy Modular Frame" in [c.name for c in rep.cost[:2]]


def test_hand_crafted_items_are_not_reported_as_a_missing_line(spire, game):
    """A Portable Miner has zero production lines by nature, not by neglect -- it has
    no automatable recipe at all. Ranking it beside Rubber would bury the real finding
    under a two-unit shortfall."""
    _req, _sol, rep = spire
    names = [c.name for c in rep.cost]
    if "Portable Miner" in names:
        assert names.index("Portable Miner") == len(names) - 1
        assert not game.producers_of("Desc_PortableMiner_C", "part")


# ---------------------------------------------------------------- plan identity


def test_the_plan_id_covers_the_save_derived_inputs_not_just_the_arguments(game, state):
    """The server keeps no state, so the diff re-solves rather than taking a handle.
    The id is what makes that safe: identical arguments must give an identical id, and
    a different question must give a different one. Hashing the arguments alone would
    let a newly unlocked recipe silently change the plan under a stable id."""
    a = build_scenario(game, state, **SPIRE)
    b = build_scenario(game, state, **SPIRE)
    assert a.plan_id == b.plan_id
    assert build_scenario(game, state, **{**SPIRE, "exports": ["MW"]}).plan_id != a.plan_id

    # A save-derived input, not an argument: losing a recipe changes what can be built.
    progression = deepcopy(state.projection["progression"])
    progression["available_recipes"] = [
        r for r in progression["available_recipes"] if r != "Recipe_Alternate_DilutedFuel_C"
    ]
    fewer = _variant(game, state, progression=progression)
    assert build_scenario(game, fewer, **SPIRE).plan_id != a.plan_id

    # But tapping a node must NOT move the plan: the same nodes are still in scope, so
    # the id keeps meaning "same plan" and the save id carries "you built something".
    assert build_scenario(game, _variant(game, state, extractors=[]), **SPIRE).plan_id == a.plan_id


def test_the_save_id_moves_when_the_factory_does(game, state, spire):
    """Same plan id with a different save id is the useful mid-build signal: the plan
    did not move, you did."""
    _req, sol, rep = spire
    changed = _variant(game, state, machines=state.projection["machines"][:-1])
    other = build_diff(game, changed, sol, build_scenario(game, changed, **SPIRE))
    assert other.save_id != rep.save_id


def test_one_scenario_path_serves_every_planning_tool(game, state):
    """plan_factory, plan_layout and diff_vs_save must describe the same factory for
    the same arguments. Three copies of the translation would drift invisibly: each
    tool would stay self-consistent about a slightly different plant."""
    req = build_scenario(game, state, **SPIRE)
    assert req.scenario.exports == ("__MW__", "Desc_Plastic_C", "Desc_Rubber_C")
    assert req.node_rows and all(r["reachable"] for r in req.node_rows)


def test_the_cost_table_says_when_it_hid_rows(game, state):
    """A five-row slice with no envelope reads as the whole bill. The slice is right --
    the list is ranked by shortfall, so the gate on the build is at its head -- but the
    reader has to be able to tell that a sixth shortfall exists. The fixture world has
    only three, so the rows are fabricated: this pins the envelope, not the bill."""
    from satisfactory_mcp.domain.planning.progress.diff import CostLine
    from satisfactory_mcp.domain.planning.progress.diff_service import build_diff_report
    from satisfactory_mcp.presenters.text.diff import COST_ROWS, render_diff

    report = build_diff_report(game, state, dict(SPIRE), objective="max_mw")
    assert len(report.diff.cost) <= COST_ROWS, "this world stopped being the short case"
    report.diff.cost = [
        CostLine(item=f"Desc_{i}_C", name=f"Item {i}", need=100.0, stock=1.0, lines=0)
        for i in range(COST_ROWS + 3)
    ]
    out = render_diff(game, state, report, objective="max_mw", limit=20)
    assert f"{COST_ROWS + 3} match(es), showing {COST_ROWS}" in out


def test_both_tools_print_the_id_they_tell_the_reader_to_compare():
    """diff_vs_save's own docstring says two responses carrying the same id are provably
    the same plan -- and plan_factory printed the id only when the plan was SAVED, so the
    cross-check it advertises could not be performed on an unsaved one."""
    plan = srv.plan_factory(**SPIRE)
    diff = srv.diff_vs_save(**SPIRE)
    plan_id = plan.split("[plan ", 1)[1].split("]")[0]
    assert f"[plan {plan_id}/save " in diff


# ----------------------------------------------------------------- the surface


def test_infeasible_plans_pass_the_reason_through_instead_of_an_empty_diff():
    """An empty table reads as "you already have it", which is the opposite of what
    infeasible means. max_mw without MW in exports is rejected by the solver itself."""
    out = srv.diff_vs_save(objective="max_mw", sources=["north"], exports=["Plastic"])
    assert "INFEASIBLE" in out
    assert "st\tact" not in out


def test_an_empty_plan_says_so_rather_than_reporting_nothing_to_do():
    """Every crude route this world has unlocked emits Polymer Resin, so with MW as the
    only export the LP abandons oil and builds nothing. Feasible, empty, and not the
    same thing as "your factory already matches"."""
    out = srv.diff_vs_save(
        objective="max_mw",
        sources=["region:Northern Forest", "resource:Crude Oil"],
        exports=["MW"],
    )
    assert "EMPTY PLAN" in out


def test_the_tool_is_registered_and_capped():
    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    assert "diff_vs_save" in tools
    assert "25" in str(tools["diff_vs_save"].inputSchema["properties"]["limit"])
    assert not tools["diff_vs_save"].outputSchema


def test_the_response_fits_its_budget():
    """The context budget is the binding design constraint, and a diff is the tool most
    tempted to dump 435 machine rows."""
    out = srv.diff_vs_save(**SPIRE)
    assert len(out) < DIFF_BUDGET, f"diff_vs_save returned {len(out)} chars"
    assert "DISMANTLE" not in out
    # Ids belong in a footer, not in rows: a node instance name runs to 51 characters.
    assert "# build targets" in out


# -------------------------------------------------------- the render primitives


def test_where_bands_keeps_the_split_a_mean_would_hide():
    """23 Water Extractors reads as one fleet until you see 4 at the plant, 13 at the
    main base and 6 at 2.4 km. Their mean, 1.1 km, describes no pump at all."""
    assert render.where_bands([15, 26, 35, 54]) == "4@0"
    assert render.where_bands([15, 900, 950, 2450]) == "1@0 2@0.9 1@2.5"
    assert render.where_bands([]) == ""


def test_where_bands_collapses_only_the_far_tail():
    """Near groups stay separate because that is where the reusable machines are; the
    tail only ever means "and some far away"."""
    out = render.where_bands([10, 500, 1500, 3000, 5000], max_bands=3)
    assert out.count("@") == 3
    assert out.startswith("1@0 1@0.5")


def test_plural_does_not_print_refinerys():
    """A visible typo next to a number makes the reader distrust the number."""
    assert render.plural("Refinery", 31) == "Refineries"
    assert render.plural("Refinery", 1) == "Refinery"
    assert render.plural("Assembler", 4) == "Assemblers"

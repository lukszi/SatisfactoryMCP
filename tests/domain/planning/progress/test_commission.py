"""Startup order, and why it is not a build order.

The re-frame that shrinks the whole problem: building costs materials, not power, and a
machine draws only when it runs. So the plant is constructed unpowered at leisure and the
question is what to switch on first. That removes the search over build partitions and
leaves a fixed machine set plus one hard constraint.

Hard, not advisory: exceeding available power in Satisfactory blows the fuse and stops the
WHOLE grid until it is reset by hand -- including the plant that was feeding it. Every
assertion about power below is an inequality that must never be violated, not a preference.

The second half of the file is the tracker: which stage the save says you are in. Its
tests all turn on ONE distinction, because getting it wrong makes the tracker lie. Built
and energised are separate states, and the save proves only one of them -- a machine that
produced inside the last 300 s window certainly had power, a machine that did not may be
unpowered, starved, blocked or idle, and nothing in the file separates those. Every test
below that touches power is really a test that silence is never read as "unpowered".
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.progress.diff import (
    DiffReport,
    DiffRow,
    build_diff,
)
from satisfactory_mcp.domain.planning.progress.jobs import group_key
from satisfactory_mcp.domain.planning.progress.stages import track
from satisfactory_mcp.domain.planning.progress.startup import commission
from satisfactory_mcp.domain.planning.solver.prepare import prepare
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools import planning
from tests.support.reference_world import REFERENCE_FIELD, REFERENCE_MAX_MW_ARGS

pytestmark = pytest.mark.integration

HEADROOM = 711.49

#: A real recipe id, so a fabricated machine record is assessed as a machine with a
#: recipe rather than falling into health.py's "no recipe" bucket and hiding the state
#: the test is actually setting.
A_RECIPE = "Recipe_Alternate_HeavyOilResidue_C"

#: The full 300 s productivity window. Fixed by the game on every carrier in the save.
WINDOW = 300.0


@pytest.fixture(autouse=True)
def _tools_read_the_reference_world(monkeypatch, projection, game):
    """The tool tests below quote this world's headroom, so they must not read the newest save.

    A new state per call, as the server builds one, so a plan saved by one call is seen by
    the next.
    """
    monkeypatch.setattr(
        planning,
        "_state",
        lambda save=None, world=None, as_of=None: WorldState(projection=projection, game=game),
    )


@pytest.fixture
def plan(game, state):
    return prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))


@pytest.fixture
def run(plan, game):
    return commission(plan, game, HEADROOM, "test")


def _record(name: str, uptime: dict | None) -> dict:
    record = {"instance": f"L:P.{name}", "cls": "Build_OilRefinery_C", "recipe": A_RECIPE}
    if uptime is not None:
        record["uptime"] = uptime
    return record


#: The three evidence states a built machine can be in, as the save records them.
#: "running" is the only one that proves power; the other two are silence with and
#: without a monitor behind it, and neither may be reported as "unpowered".
EVIDENCE = {
    "running": {"window_s": WINDOW, "produce_s": WINDOW, "cur_window_s": 0.0, "cur_produce_s": 0.0},
    "dark": {"window_s": WINDOW, "produce_s": 0.0, "cur_window_s": 0.0, "cur_produce_s": 0.0},
    "unmonitored": None,
}


def _built(plan, game, fraction: float, evidence: str = "running"):
    """A (DiffReport, WorldState) pair standing in for a save at a chosen completeness.

    Fabricated rather than measured because the two cases that matter most -- nothing
    built and everything built -- are the two the reference save is not, and a tracker
    that only works partway through is exactly the one that misreports the end of a build.
    """
    totals: dict[tuple, int] = {}
    shape: dict[tuple, dict] = {}
    for proc in plan.solution.processes:
        key = group_key(proc)
        totals[key] = totals.get(key, 0) + proc["machines"]
        shape.setdefault(key, proc)

    rows, records = [], []
    for n, (key, need) in enumerate(totals.items()):
        have = round(need * fraction)
        names = [f"Fake_{n}_{i}" for i in range(have)]
        records += [_record(name, EVIDENCE[evidence]) for name in names]
        rows.append(
            DiffRow(
                stage=1,
                key=key,
                have_instances=names,
                verb="OK" if have >= need else "BUILD",
                count=max(0, need - have),
                process=shape[key]["label"],
                building_id=shape[key]["building_id"],
                building=shape[key]["building"],
                need=need,
                have=have,
                build=max(0, need - have),
            )
        )
    report = DiffReport(
        rows=rows,
        cost=[],
        neighbours=[],
        notes=[],
        to_build=sum(r.build for r in rows),
        to_build_max=sum(r.build for r in rows),
        headroom_mw=HEADROOM,
        deficit_mw=0.0,
        slices=1,
        anchor=None,
        save_id="fake",
    )
    return report, WorldState(projection={"machines": records}, game=game)


# --------------------------------------------------------- the game rule


def test_generators_cost_nothing_to_energise(game):
    """The load-bearing fact, read from the dump rather than assumed. If generators drew
    power there would be no bootstrap at all."""
    gen = game.buildings["Build_GeneratorFuel_C"]
    assert gen.power_mw == 0.0
    assert gen.power_production_mw == 250.0
    assert game.buildings["Build_WaterPump_C"].power_mw == 20.0


# --------------------------------------------------------- the hard constraint


def test_no_wave_ever_draws_more_than_is_available(run):
    """The whole point. One megawatt over and the fuse blows on everything."""
    assert run.ok
    for w in run.waves:
        assert w.draw_mw <= w.available_before + 1e-6, (w.index, w.draw_mw, w.available_before)


def test_a_wave_never_pays_for_itself_with_its_own_generation(run):
    """Fill time. Between energising a wave's refineries and its generators actually
    burning fuel, the pipes are filling and nothing is coming back. Counting a wave's own
    generation against its own draw is what makes a sequence that trips halfway."""
    available = run.headroom_mw
    for w in run.waves:
        assert w.available_before == pytest.approx(available)
        assert w.draw_mw <= available + 1e-6
        available = available - w.draw_mw + w.generation_mw


def test_available_power_climbs_monotonically(run):
    """If it did not, the sequence would be walking backwards and could never finish."""
    seen = [w.available_after for w in run.waves]
    assert seen == sorted(seen)
    assert seen[-1] > run.headroom_mw


def test_it_converges_in_a_handful_of_waves(run):
    """Generation outruns draw by ~8x on this plan, so each wave pays for a much larger
    next one. Measured at four."""
    assert 1 <= len(run.waves) <= 6


# --------------------------------------------------------- partition


def test_every_machine_is_energised_exactly_once(run, plan):
    """A partition, not a re-solve. The union of the waves is the plan."""
    total = sum(p["machines"] for p in plan.solution.processes)
    assert run.machines == total
    for w in run.waves:
        for r in w.rows:
            assert r.machines > 0
    final = {}
    for w in run.waves:
        for r in w.rows:
            final[r.label] = (r.cumulative, r.total)
    assert all(cum == tot for cum, tot in final.values())


def test_cumulative_counts_never_exceed_the_plan(run):
    for w in run.waves:
        for r in w.rows:
            assert 0 < r.cumulative <= r.total


# --------------------------------------------------------- ordering


def test_upstream_comes_on_before_downstream_within_a_wave(run):
    """Rows are ordered by chain depth so the fluid is already moving when the next block
    lights. Extraction is depth 0."""
    for w in run.waves:
        assert [r.depth for r in w.rows] == sorted(r.depth for r in w.rows)
    assert any(r.depth == 0 and r.kind == "extractor" for r in run.waves[0].rows)


def test_power_is_kept_out_of_the_dependency_graph(game, state):
    """MW is modelled as an item so the power balance is just another row. Left in the
    dependency graph it would make every consumer depend on every generator and every
    generator on its fuel -- one component, and no order at all."""
    from satisfactory_mcp.domain.planning.progress.startup import _depths

    prepared = prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))
    depths = _depths(prepared.solution.processes)
    assert len(set(depths.values())) > 1
    extractors = [p["pid"] for p in prepared.solution.processes if p["kind"] == "extractor"]
    assert extractors and all(depths[pid] == 0 for pid in extractors)


def test_a_cycle_puts_its_members_on_the_SAME_stage(game, state):
    """Item flow is genuinely cyclic -- Recycled Plastic and Recycled Rubber consume each
    other's output -- and members of a cycle have to be energised together, so they must
    share a depth.

    This is the regression that motivated deleting a second depth implementation. Relaxing
    with a cap, which is what commission used to do, returned plastic=7 rubber=8 sink=8 on
    exactly this shape: the cycle split across stages and the consumer landed level with
    its own producer. graph.chain_depth condenses the cycle instead and returns 1, 1, 2.
    """
    from satisfactory_mcp.domain.planning.progress.startup import _depths

    procs = [
        {"pid": "ore", "rates": {"ore": 1.0}},
        {"pid": "plastic", "rates": {"plastic": 1.0, "rubber": -1.0, "ore": -1.0}},
        {"pid": "rubber", "rates": {"rubber": 1.0, "plastic": -1.0}},
        {"pid": "sink", "rates": {"plastic": -1.0}},
    ]
    depths = _depths(procs)
    assert depths["plastic"] == depths["rubber"], "cycle members must share a stage"
    assert depths["ore"] < depths["plastic"] < depths["sink"]


def test_commission_and_diff_order_a_plant_the_same_way(game, state):
    """`track` joins a commission wave against a diff row, so the two must agree on chain
    depth. They now agree by construction -- one function -- and this pins that they are
    not allowed to drift back apart."""
    from satisfactory_mcp.domain.planning.progress.startup import _depths
    from satisfactory_mcp.domain.planning.solver.graph import chain_depth

    prepared = prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))
    procs = prepared.solution.processes
    mine = _depths(procs)
    theirs = chain_depth(
        [
            (
                [i for i, r in p["rates"].items() if r < 0],
                [i for i, r in p["rates"].items() if r > 0],
            )
            for p in procs
        ]
    )
    assert [mine[p["pid"]] for p in procs] == theirs


# --------------------------------------------------------- the floor


def test_the_minimum_slice_is_reported(run):
    """One machine of every process: the cheapest thing that still feeds the whole chain,
    and the floor no startup order can go under. Measured at 631 MW against 711 free --
    which is close enough that a player deserves to see the number."""
    assert run.minimum_slice_mw > 0
    assert run.minimum_slice_mw <= run.headroom_mw
    assert run.waves[0].draw_mw >= run.minimum_slice_mw - 1e-6


def test_too_little_headroom_refuses_rather_than_inventing_an_order(plan, game):
    """There is genuinely no answer below the floor, and the useful response is to say so
    and name the number -- not to emit a sequence that blows the fuse on step one."""
    run = commission(plan, game, 10.0, "test")
    assert not run.ok
    assert not run.waves
    assert any("no startup order exists" in w for w in run.warnings)
    assert any("smaller sub-plant" in w for w in run.warnings)


def test_a_bigger_headroom_needs_no_more_waves(plan, game):
    small = commission(plan, game, HEADROOM, "test")
    large = commission(plan, game, 50_000.0, "test")
    assert large.ok
    assert len(large.waves) <= len(small.waves)


# --------------------------------------------------------- the tool


def test_the_tool_prints_the_sequence(game):
    out = srv.commission_plan(**REFERENCE_MAX_MW_ARGS)
    assert not out.startswith("! ")
    assert "wave\tchain\ton" in out
    assert "W1" in out


def test_headroom_is_printed_as_a_labelled_input(game):
    """So a sequence computed against a save that has since moved is visibly stale rather
    than quietly wrong."""
    out = srv.commission_plan(**REFERENCE_MAX_MW_ARGS)
    assert "headroom_MW=" in out
    assert "source: measured from the save" in out
    given = srv.commission_plan(headroom_mw=5000.0, **REFERENCE_MAX_MW_ARGS)
    assert "source: given by caller" in given


def test_the_tool_says_to_build_everything_first(game):
    """The re-frame, stated where it matters. A reader who takes these for build stages
    would sequence the construction for no reason."""
    out = srv.commission_plan(**REFERENCE_MAX_MW_ARGS)
    assert "build EVERYTHING first" in out
    assert "switch-ons" in out


def test_the_tool_recommends_a_power_switch_per_block(game):
    """It has to be built in from the start, so it belongs with the sequence rather than
    in a footnote after the plant is up."""
    out = srv.commission_plan(**REFERENCE_MAX_MW_ARGS)
    assert "Power Switch per block" in out
    assert "WHOLE grid" in out


def test_generator_rows_survive_truncation(game):
    """They sort last by chain depth, so a per-wave limit silently dropped exactly the
    rows that pay for the next wave."""
    out = srv.commission_plan(limit=12, **REFERENCE_MAX_MW_ARGS)
    assert "Generator" in out
    # And truncation is announced rather than silent, so a short table does not read as
    # the whole sequence.
    assert "more: call again with offset" in out


def test_the_offset_the_truncation_notice_names_is_a_real_parameter(game):
    """The envelope said "call again with offset=25" while the tool's schema had no
    offset at all -- an instruction the caller could not follow. Paging must continue
    the ONE sequence: same rows, later window, no repeats.

    headroom_mw is passed explicitly so the sequence exists whatever the live grid is
    doing right now -- the default reads headroom from the save, and a loaded grid
    answers "0 wave(s)", which would turn this into a test of whatever the grid was doing when the save was made."""
    header = "wave\tchain\ton\tcum\tprocess\tMW\tfree after"

    def data_rows(out: str) -> list[str]:
        lines = out.splitlines()
        body = lines[lines.index(header) + 1 :]
        return [line for line in body if "\t" in line]

    first = srv.commission_plan(limit=5, headroom_mw=5000.0, **REFERENCE_MAX_MW_ARGS)
    assert "more: call again with offset=5" in first
    paged = srv.commission_plan(limit=5, offset=5, headroom_mw=5000.0, **REFERENCE_MAX_MW_ARGS)
    assert "from offset 5" in paged
    overlap = set(data_rows(first)) & set(data_rows(paged))
    assert not overlap, overlap


# ------------------------------------------------- which stage am I in: the grouping


def test_a_stage_is_a_wave_and_the_two_never_disagree(plan, run, game, state):
    """Stages are the commissioning partition re-read from the save, not a second
    partition invented here. If the two ever diverged, the sequence a player was handed
    and the progress they are shown against it would be counting different machines."""
    report, world = _built(plan, game, 0.0)
    tracking = track(plan, run, report, game, world)
    assert [s.index for s in tracking.stages] == [w.index for w in run.waves]
    assert [s.machines for s in tracking.stages] == [w.machines for w in run.waves]
    assert tracking.machines == sum(p["machines"] for p in plan.solution.processes)


def test_the_join_is_the_diff_identity_key_not_the_label(plan, run, game, state):
    """A label is a display string and two build jobs can share one. The wave carries the
    process id and the diff carries group_key, and joining on anything else would let the
    tracker credit a Refinery on one recipe with a Refinery on another."""
    keys = {group_key(p) for p in plan.solution.processes}
    report = build_diff(game, state, plan.solution, plan.request)
    assert {r.key for r in report.rows} == keys
    assert all(r.key for r in report.rows)


def test_built_machines_fill_the_earliest_stage_first(plan, run, game):
    """Identical machines are indistinguishable in the save -- nothing records which
    Refinery was meant for wave 2 -- so progress is assumed to follow the order the
    sequence prescribes. Any other rule would need evidence the file does not have."""
    report, world = _built(plan, game, 0.5)
    tracking = track(plan, run, report, game, world)
    # One build job's share, stage by stage. It is split across waves precisely because
    # a wave can only afford part of it, which is what makes the ordering visible.
    split = [
        [r for r in s.rows if r.label == plan.solution.processes[0]["label"]]
        for s in tracking.stages
    ]
    shares = [(r.built, r.machines) for rows in split for r in rows]
    assert len(shares) > 1, "the reference plan should split at least one job across waves"
    starved = False
    for built, machines in shares:
        assert not (starved and built), "a later stage was credited before an earlier one"
        starved = starved or built < machines
    assert tracking.stages[0].rows[0].built == tracking.stages[0].rows[0].machines


# ------------------------------------------------- built and energised are different


def test_only_production_in_the_last_window_proves_power(plan, run, game):
    """THE distinction. A machine that produced inside the 300 s window certainly had
    power. That is the only positive evidence of energisation in the save, and it is the
    only thing `running` is ever allowed to count."""
    report, world = _built(plan, game, 1.0, evidence="running")
    lit = track(plan, run, report, game, world)
    assert lit.running == lit.built == lit.machines

    report, world = _built(plan, game, 1.0, evidence="dark")
    dark = track(plan, run, report, game, world)
    assert dark.built == dark.machines
    assert dark.running == 0


def test_a_fully_built_dark_plant_is_a_valid_state_not_an_anomaly(plan, run, game):
    """The whole point of the Q1 re-frame: you build the entire plant unpowered and then
    energise it block by block, so every stage complete and nothing running is the
    EXPECTED state on the day the build finishes. A tracker that flagged it, or that read
    it as "unpowered", would be wrong on the most important day of the plan."""
    report, world = _built(plan, game, 1.0, evidence="dark")
    tracking = track(plan, run, report, game, world)
    assert all(s.complete for s in tracking.stages)
    assert tracking.current == 0
    assert all(s.dark == s.built for s in tracking.stages)
    assert not tracking.warnings


def test_silence_with_no_monitor_is_reported_as_no_evidence_not_as_zero_running(plan, run, game):
    """A save that carries no productivity monitor says NOTHING about what is energised,
    which is a different report from "nothing is running" and must not be printed as one.
    Same precedent as sloop_budget reporting committed sloops as unknown rather than 0."""
    report, world = _built(plan, game, 1.0, evidence="unmonitored")
    tracking = track(plan, run, report, game, world)
    assert tracking.built == tracking.machines
    assert tracking.monitored == 0
    assert any("NO evidence either way" in w for w in tracking.warnings)


def test_the_save_carries_no_direct_answer_about_power(state):
    """Pinned because the gap is the finding, and a future projection change might close
    it. mHasPower and mCircuitID on UFGPowerInfoComponent carry no SaveGame specifier, so
    grid membership is rebuilt at load and never persisted; there is no property here to
    read "is this block switched on" from, whatever else the projection grows."""
    records = [
        *state.projection.get("machines", ()),
        *state.projection.get("extractors", ()),
        *state.projection.get("generators", ()),
    ]
    assert records
    assert not any("has_power" in r or "circuit" in r for r in records)


# ------------------------------------------------- the ends of the build


def test_nothing_built_puts_you_in_the_first_stage(plan, run, game):
    """A greenfield plan. Every stage 0%, and the answer is stage 1 rather than a
    division by zero or a cheerful "complete"."""
    report, world = _built(plan, game, 0.0)
    tracking = track(plan, run, report, game, world)
    assert tracking.built == 0
    assert tracking.current == 1
    assert all(not s.complete and s.fraction_built == 0.0 for s in tracking.stages)


def test_everything_built_reports_no_current_stage(plan, run, game):
    """Zero means "there is no stage you are partway through", not stage zero. At that
    point the build is over and the only remaining question is energisation, which the
    save cannot answer -- so claiming a stage there would be inventing one."""
    report, world = _built(plan, game, 1.0)
    tracking = track(plan, run, report, game, world)
    assert tracking.current == 0
    assert tracking.built == tracking.machines


def test_no_startup_order_means_no_stages_rather_than_an_exception(plan, game):
    """Below the minimum slice there is no sequence, so there is nothing to match the
    save against. The tracker says that instead of dividing an empty partition."""
    starved = commission(plan, game, 10.0, "test")
    report, world = _built(plan, game, 0.5)
    tracking = track(plan, starved, report, game, world)
    assert not tracking.ok
    assert not tracking.stages
    assert any("no stages" in w for w in tracking.warnings)


# ------------------------------------------------- the tool


def test_the_tool_groups_the_diff_by_stage(game):
    """stage=0 asks for the overview without a stored plan. The table is the point: one
    row per wave, with what is built and what is proven running side by side."""
    out = srv.diff_vs_save(stage=0, **REFERENCE_MAX_MW_ARGS)
    assert not out.startswith("! ")
    assert "# STAGES" in out
    assert "stage\ton\tbuilt\trunning" in out
    assert "S1" in out


def test_the_stage_overview_never_calls_a_dark_block_unpowered(game):
    """The one way this feature can be confidently wrong. Silence has four causes and the
    save separates none of them, so the word must not appear as a verdict."""
    out = srv.diff_vs_save(stage=0, **REFERENCE_MAX_MW_ARGS)
    assert "built and ENERGISED are different states" in out
    assert "may be unpowered, starved, blocked or simply idle" in out
    assert "not an anomaly" in out


def test_the_stage_filter_shows_one_stage_and_drops_the_cost_table(game):
    """A stage is a switch-on, not a build step: the whole plant is built first, so
    splitting the materials bill across stages would describe a build nobody does."""
    out = srv.diff_vs_save(stage=1, **REFERENCE_MAX_MW_ARGS)
    assert "# STAGE 1 of" in out
    assert "act\ton\tbuilt\trunning" in out
    assert "cost of the build counts" not in out
    assert "materials are NOT split by stage" in out


def test_an_unknown_stage_names_the_stages_that_exist(game):
    """Rather than an empty table, which would read as "stage 9 is done"."""
    out = srv.diff_vs_save(stage=99, **REFERENCE_MAX_MW_ARGS)
    assert "no stage 99" in out
    assert "it has stages 1" in out


def test_a_stage_number_without_a_stored_plan_says_it_will_move(game):
    """Stage numbers are only a milestone if the partition is stable, and it is stable
    only for a stored request. Derived from loose arguments they renumber the moment an
    argument or the world moves, so the tool says so rather than letting a player write
    "I am in stage 3" in their notes against nothing."""
    out = srv.diff_vs_save(stage=0, **REFERENCE_MAX_MW_ARGS)
    assert "not a stored plan" in out
    assert "save_as" in out


def test_an_unknown_plan_name_is_a_message_not_an_exception(game):
    out = srv.diff_vs_save(plan="no-such-plan", stage=1)
    assert out.startswith("! no saved plan named 'no-such-plan'")


def test_recalling_a_stored_plan_answers_which_stage_you_are_in(game):
    """The headline case: `diff_vs_save(plan=...)` with no stage argument at all. A
    stored plan is what makes a stage number worth writing down, so recalling one turns
    the grouping on without being asked, and the caveat about loose numbering drops."""
    saved = srv.plan_factory(save_as="stage-test", **REFERENCE_MAX_MW_ARGS)
    assert 'saved as "stage-test" v1' in saved

    out = srv.diff_vs_save(plan="stage-test")
    assert "# STAGES" in out
    assert "you are in STAGE" in out or "every stage is built" in out
    assert "not a stored plan" not in out


# --------------------------------------------------------- how long the wait is


def test_a_wave_says_how_long_the_wait_is(run):
    """ "Wait, then next wave" is the least actionable line in the sequence, and the wait
    is exactly where the risk lives -- the draw is already being paid and nothing is coming
    back."""
    assert run.ok
    for w in run.waves:
        assert w.fill_s() > 0


def test_the_wait_is_one_cycle_per_chain_depth(run, game):
    """Each stage must finish a full cycle before the next sees anything, so the sum of
    the slowest cycle at each depth is a hard floor. Anything less would be claiming
    material moved before it existed."""
    for w in run.waves:
        deepest: dict[int, float] = {}
        for row in w.rows:
            if row.cycle_s > 0:
                deepest[row.depth] = max(deepest.get(row.depth, 0.0), row.cycle_s)
        assert w.fill_s() == pytest.approx(sum(deepest.values()))
        # And it is a floor over the individual cycles, never a sum of everything.
        assert w.fill_s() >= max(deepest.values(), default=0.0)
        assert w.fill_s() <= sum(r.cycle_s for r in w.rows) + 1e-9


def test_a_generator_contributes_no_cycle(run):
    """It burns continuously; there is no cycle to wait through. Charging one would
    inflate every wave that lands generators, which is all of them."""
    generators = [r for r in run.waves[0].rows if r.generation_mw > 0]
    assert generators
    assert all(r.cycle_s == 0.0 for r in generators)


def test_overclocking_shortens_a_cycle(game):
    """A machine at 250% finishes in 40% of the base time, which is why an overclocked
    extractor is not what holds up a startup."""
    from satisfactory_mcp.domain.planning.progress.startup import _cycle_s

    base = {"recipe": A_RECIPE, "clock": 1.0, "building_id": "Build_OilRefinery_C"}
    fast = {**base, "clock": 2.5}
    assert _cycle_s(fast, game) == pytest.approx(_cycle_s(base, game) / 2.5)


def test_an_extractor_uses_its_extract_cycle(game):
    """It has no recipe at all, so a recipe-only lookup would silently call it instant."""
    from satisfactory_mcp.domain.planning.progress.startup import _cycle_s

    row = {"recipe": None, "clock": 1.0, "building_id": "Build_OilPump_C"}
    assert _cycle_s(row, game) == pytest.approx(game.buildings["Build_OilPump_C"].extract_cycle_s)


def test_the_tool_prints_the_wait_and_calls_it_a_floor(game):
    """Pipe transit is not in it and dominates on a long run, so presenting the number as
    an estimate would understate exactly the interval the player is exposed during."""
    out = srv.commission_plan(
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=14,
    )
    assert "wait >=" in out
    assert "LOWER bound" in out
    assert "does NOT include pipe transit" in out


# ------------------------------------------------------------------ shared words (P4)


def _stage(index, *rows):
    from collections import Counter

    from satisfactory_mcp.domain.planning.progress.stages import Stage, StageRow

    stage = Stage(index=index)
    for key, machines, built, built_max, states in rows:
        stage.rows.append(
            StageRow(
                stage=index,
                label=str(key),
                kind="recipe",
                building="B",
                machines=machines,
                total=machines,
                built=built,
                built_max=built_max,
                by_state=Counter(states),
                key=key,
            )
        )
    return stage


def test_a_stage_describes_itself_in_the_words_the_text_used():
    assert _stage(1, (("a",), 4, 0, 0, {})).describe() == "not built"
    assert _stage(1, (("a",), 4, 1, 1, {})).describe() == "25% built"
    assert _stage(1, (("a",), 4, 1, 3, {})).describe() == "25%..75% built"
    assert _stage(1, (("a",), 2, 2, 2, {"saturated": 2})).describe() == "built, all running"
    assert _stage(1, (("a",), 2, 2, 2, {"saturated": 1})).describe() == "built, 1 running"
    assert _stage(1, (("a",), 2, 2, 2, {"stalled": 2})).describe() == "built, none running"
    assert _stage(1, (("a",), 2, 2, 2, {"unmonitored": 2})).describe() == "built"


def test_the_headline_and_the_partition_come_from_the_tracking():
    from satisfactory_mcp.domain.planning.progress.stages import Tracking, partition_id

    tracking = Tracking(
        stages=[
            _stage(1, (("a",), 2, 2, 2, {"saturated": 2})),
            _stage(2, (("a",), 1, 0, 0, {}), (("b",), 3, 3, 3, {"saturated": 1})),
        ],
        current=2,
    )
    assert tracking.headline() == (
        "you are in STAGE 2 of 2: stage 1 complete, stage 2 is 75% built (3/4) and 1 "
        "machine(s) in it are proven running"
    )
    assert (
        tracking.headline(brief=True)
        == "you are in stage 2 of 2: 75% built (3/4), 1 proven running"
    )
    pid = partition_id(tracking)
    assert len(pid) == 10 and pid == partition_id(tracking)
    tracking.stages[1].rows[1].machines = 4
    assert partition_id(tracking) != pid
    assert partition_id(Tracking(ok=False)) == "" and Tracking(ok=False).headline() == ""


def test_the_presenter_speaks_the_domain_caveats():
    from satisfactory_mcp.domain.planning.progress import stages as domain
    from satisfactory_mcp.presenters.text import diff as text

    assert text.ENERGISED_CAVEAT is domain.ENERGISED_CAVEAT
    assert text.RANGE_CAVEAT is domain.RANGE_CAVEAT
    assert planning.ENERGISED_CAVEAT is domain.ENERGISED_CAVEAT

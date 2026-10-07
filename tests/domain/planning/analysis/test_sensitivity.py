"""What a locked recipe would be worth to a specific plan.

`advise_hard_drive` answered this for the two options of one pending drive. The question
underneath -- across every alternate NOT unlocked, which would change the factory being
built -- was answered by hand, by tracing the recipe tree.

The behaviour worth pinning is that a ZERO is a result. Most candidates change nothing,
and "you are not missing anything here" is the decision the hand-walk was producing.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.core.gamedata.unlocks import SOURCE_OF_TYPE
from satisfactory_mcp.domain.planning.analysis.sensitivity import sweep_unlocks
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.reference_world import FIXTURE_WORLD, REFERENCE_MAX_MW_ARGS, SPIRE_COAST_FULL
from tests.support.user_data import private_user_data

# The fixture world, not the newest save: the measured answer names the Blender it lacked.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("planned")]


@pytest.fixture(scope="module")
def sweep(game, projection, tmp_path_factory):
    """One sweep for the module, over the world ``planned`` builds, in a user data root of its
    own: no test's private root is in force yet while a module fixture runs."""
    with private_user_data(tmp_path_factory.mktemp("sweep") / "user"):
        PlanLog(FIXTURE_WORLD).create("spire-coast-full", SPIRE_COAST_FULL, actor=Actor("chat"))
        world = WorldState(projection=projection, game=game)
        return sweep_unlocks(build_scenario(game, world, **REFERENCE_MAX_MW_ARGS), world)


# ------------------------------------------------------------ the sweep


def test_every_locked_alternate_is_tried(sweep, planned):
    """No relevance filter. A recipe that opens a chain the plan cannot currently reach
    touches none of its items by definition, and is exactly the interesting case -- and at
    0.01 s a solve, skipping the cleverness costs about a second."""
    assert sweep.tried == len(planned.locked_alternates)
    assert sweep.tried > 50


def test_an_unlocked_recipe_is_never_a_candidate(sweep, planned):
    """It would report a delta of zero and pad the table with things you already own."""
    tried = {r.recipe for r in sweep.rows}
    assert not (tried & planned.available_recipe_ids)


def test_most_candidates_change_nothing_and_that_is_reported(sweep):
    """The zero is the answer. Reporting only winners would hide that 78 of 79 were
    checked and found irrelevant, which is the finding."""
    assert len(sweep.movers) < sweep.tried
    assert sweep.rows, "every candidate gets a row even when its gain is zero"


def test_the_baseline_matches_the_plan_it_is_measured_against(sweep, game, planned):
    """A delta against a different baseline is not a delta. This is the mistake advisor.py
    documents: feeding raw_caps instead of real extractors inflated a baseline by 86%."""
    from satisfactory_mcp.domain.planning.solver.prepare import prepare

    plan = prepare(game, planned, dict(REFERENCE_MAX_MW_ARGS))
    assert sweep.baseline == pytest.approx(plan.solution.objective_value)


def test_gain_is_positive_for_better_whichever_way_the_objective_points(game, planned):
    """`objective_value` is sign-normalised for max/min, so a min_* objective needs
    flipping -- otherwise a recipe that halves raw usage reports a large NEGATIVE gain and
    sorts last, which is exactly backwards."""
    from satisfactory_mcp.domain.planning.analysis.sensitivity import _as_gain

    assert _as_gain("max_mw", 100.0) == 100.0
    assert _as_gain("min_raw", 100.0) == -100.0
    # A min_raw plan that drops from 100 to 60 must read as a gain.
    assert _as_gain("min_raw", 60.0) - _as_gain("min_raw", 100.0) == pytest.approx(40.0)


def test_rows_are_ranked_best_first(sweep):
    gains = [r.gain for r in sweep.rows]
    assert gains == sorted(gains, reverse=True)


def test_noise_is_not_a_finding(sweep):
    """An LP on a 107,000 MW plan does not return the same digits twice at the bottom
    end, so a 0.4 MW 'improvement' listed above a genuine zero would be worse than
    silence."""
    assert sweep.tolerance > 0
    assert all(abs(r.gain) > sweep.tolerance for r in sweep.movers)


def test_a_search_narrows_the_candidate_pool(game, planned):
    narrow = sweep_unlocks(
        build_scenario(game, planned, **REFERENCE_MAX_MW_ARGS),
        planned,
        [r for r in planned.locked_alternates if "Turbo" in r.name],
    )
    assert 0 < narrow.tried < len(planned.locked_alternates)


# ------------------------------------------------------------ the finding


def test_turbo_blend_fuel_is_the_one_that_matters_here(sweep):
    """The measured answer on the reference plan: 1 of 79 moves it, by +13.6%. This is
    what an hour of hand-tracing produced, and it is the number that decides the build."""
    movers = sweep.movers
    assert len(movers) == 1
    top = movers[0]
    assert top.name == "Alternate: Turbo Blend Fuel"
    assert top.gain / sweep.baseline == pytest.approx(0.136, abs=0.01)


def test_a_candidate_names_the_machine_its_delta_assumes(sweep):
    """The delta is an UPPER bound wherever a machine is missing, so the condition has to
    travel with the number."""
    top = sweep.movers[0]
    assert "Blender" in top.needs


# ------------------------------------------------------------ the tool


def test_the_tool_reports_the_sweep(game):
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    assert not out.startswith("! ")
    assert "gain\tvs base\talternate" in out
    assert "Turbo Blend Fuel" in out
    assert "candidates=79" in out


def test_it_flags_what_is_claimable_from_a_pending_drive(game, planned):
    """The difference between "worth having" and "you can have it right now". Turbo Blend
    Fuel is worth +14,540 MW here AND sitting in drive 25."""
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    on_offer = {
        r.cls for o in planned.hard_drive_offers for opt in o.options for r in opt["recipes"]
    }
    if "Recipe_Alternate_TurboBlendFuel_C" not in on_offer:
        pytest.skip("that drive has been claimed")
    assert "claimable NOW" in out
    assert "drive 25" in out


def test_it_says_how_many_were_checked_and_found_irrelevant(game):
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    assert "changed this plan" in out
    assert "worth nothing HERE" in out


def test_an_unmatched_search_refuses_rather_than_sweeping_everything(game):
    assert srv.rank_unlocks(query="no such recipe", **REFERENCE_MAX_MW_ARGS).startswith(
        "! no LOCKED"
    )


def test_an_infeasible_plan_has_nothing_to_rank(game):
    out = srv.rank_unlocks(objective="max_mw", sources=["region:Nowhere"], exports=["MW"])
    assert "nothing to rank against" in out


# ------------------------------------------------------ what to research, and what failed


def test_every_candidate_names_the_schematic_that_grants_it(sweep):
    """The tool exists to decide what to research next, and the schematic is the thing
    you research. It is a separate identifier from the recipe -- the recipe id is what
    only_recipes takes -- and this game names the two the same, which is the answer to
    "what do I look for in the MAM", not an absence of one."""
    assert all(r.unlocked_by for r in sweep.rows)
    assert sweep.movers[0].unlocked_by == [sweep.movers[0].name]


def test_the_tool_prints_the_schematic_column(game):
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    assert "alternate\tgranted by" in out


def test_the_column_says_what_kind_of_work_the_unlock_is(game):
    """A hard drive and a milestone are different evenings, and the schematic name alone
    said neither -- it repeated the alternate column and was cut off at 30 characters. The
    words come from ``core.gamedata.unlocks``, so this column reads like search_recipes'."""
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    body = out.split("alternate\tgranted by", 1)[1].splitlines()[1:]
    cells = [line.split("\t")[3] for line in body if line.count("\t") >= 7]
    assert cells
    assert all(c.split(":")[0] in set(SOURCE_OF_TYPE.values()) for c in cells), cells


def test_an_unsolved_candidate_is_not_filed_with_the_worthless_ones():
    """Both report gain 0, and only one of them means "worth nothing here". `after`
    falls back to the baseline when the counterfactual does not solve, because there is
    no other number to fall back to -- so the flag, not the gain, has to separate them."""
    from satisfactory_mcp.domain.planning.analysis.sensitivity import UnlockDelta, UnlockSweep

    def delta(name: str, ok: bool) -> UnlockDelta:
        return UnlockDelta(
            recipe=name,
            name=name,
            before=100.0,
            after=100.0,
            machines_before=1.0,
            machines_after=1.0,
            ok=ok,
        )

    measured, failed = delta("measured", True), delta("failed", False)
    sweep = UnlockSweep(objective="max_mw", baseline=100.0, rows=[measured, failed], tried=2)
    assert measured.gain == failed.gain == 0.0
    assert sweep.movers == []
    assert sweep.unsolved == [failed]


def test_an_infeasible_candidate_is_marked_rather_than_left_at_zero(game, monkeypatch):
    """It printed nothing at all: gain 0 drops it out of the movers, so a candidate that
    could not be measured was indistinguishable from one measured and found irrelevant."""
    from satisfactory_mcp.domain.planning.analysis import sensitivity
    from satisfactory_mcp.interfaces.mcp.tools.planning import analysis as tool

    real = sensitivity.sweep_unlocks
    broke: list[str] = []

    def one_fails(request, state, candidates=None):
        out = real(request, state, candidates)
        out.rows[0].ok = False
        broke.append(out.rows[0].name)
        return out

    monkeypatch.setattr(tool, "sweep_unlocks", one_fails)
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    assert "INFEASIBLE: 1 candidate(s) did not solve" in out
    assert "UNKNOWN rather than zero" in out
    assert broke[0] in out


# ------------------------------------------------------------ what a gain depends on


def test_a_gain_names_what_it_switches_on(sweep):
    """The difference between "+13.6%" and "+13.6% if you reintroduce the chain you
    deleted on purpose". Turbo Blend Fuel drags in a coal generator and Petroleum Coke --
    a whole second supply chain, not a free win."""
    top = sweep.movers[0]
    assert top.activates
    assert any("Coal" in a for a in top.activates)


def test_activates_lists_only_what_the_baseline_was_not_already_running(sweep):
    """Otherwise every row would repeat the whole plan and say nothing."""
    for row in sweep.movers:
        assert row.name not in row.activates
        assert len(row.activates) < 20


def test_the_same_sweep_against_the_saved_plan_finds_nothing(game, planned):
    """The trap this tool set for its own author. Ad-hoc arguments measure a DIFFERENT
    plant: Turbo Blend Fuel is worth +13.6% against unconstrained Spire Coast and exactly
    zero against the saved plan, which bans Turbofuel and coal generators. Both answers
    are right; only one is about the factory being built."""
    stored = planned.plans.find("spire-coast-full")
    saved = sweep_unlocks(build_scenario(game, planned, **stored.kwargs()), planned)
    assert saved.tried == len(planned.locked_alternates)
    assert saved.movers == []


def test_ad_hoc_arguments_warn_that_a_saved_plan_exists(game, planned):
    """Because the ad-hoc number was once read and reported it as if it
    were about the saved architecture."""
    out = srv.rank_unlocks(**REFERENCE_MAX_MW_ARGS)
    assert "measured against the ARGUMENTS GIVEN" in out
    assert "pass plan=" in out
    # And recalling a plan drops the warning, because then it is not true.
    assert "measured against the ARGUMENTS GIVEN" not in srv.rank_unlocks(plan="spire-coast-full")

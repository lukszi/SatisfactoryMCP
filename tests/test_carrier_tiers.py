"""Which belt and pipe a plan is allowed to assume.

`belt_ipm=780` and `pipe_m3min=600` were hardcoded defaults -- Mk5 and Mk2. Correct on the
reference world and unverified everywhere else, and a whole design session ran on them
without anything confirming the tiers were unlocked. Had Pipeline Mk.2 been locked, every
pipe count in that session doubled: six crude trunks become twelve and the deck stops
fitting. Silent-wrong-by-default is the worst failure a planner has, so the tier now comes
from the save and the tool says which one it picked.
"""

from __future__ import annotations

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.solver.scenario import build_scenario

pytestmark = pytest.mark.integration


# ------------------------------------------------------- picking a tier


def test_the_best_tier_is_the_fastest_UNLOCKED_one(live, game):
    belt, pipe = live.best_belt(), live.best_pipe()
    assert belt and pipe
    for cls, rate in (belt, pipe):
        assert cls in live.unlocked_building_ids
    faster_locked = [
        b.name
        for c, b in game.buildings.items()
        if b.native == live.BELT_NATIVE
        and b.items_per_min > belt[1]
        and c not in live.unlocked_building_ids
    ]
    # Mk6 exists and is locked on this save; the pick must not reach for it.
    assert "Conveyor Belt Mk.6" in faster_locked


def test_a_lift_or_an_elevator_is_never_picked_as_a_belt(live, game):
    """`items_per_min` alone is not the test. A Personnel Elevator reports 400/min and
    carries people, and a Conveyor Lift duplicates a belt tier's rate -- so a naive
    "fastest thing with a rate" pick can name something that is not a belt."""
    cls, _ = live.best_belt()
    assert game.buildings[cls].native == "FGBuildableConveyorBelt"
    assert "Lift" not in game.buildings[cls].name
    assert game.buildings["Build_Elevator_C"].native == "FGBuildableElevator"


def test_a_scenario_defaults_to_what_the_save_can_build(game, live):
    req = build_scenario(game, live, objective="max_mw", exports=["MW"])
    assert req.scenario.belt_ipm == live.best_belt()[1]
    assert req.scenario.pipe_m3min == live.best_pipe()[1]


def test_an_explicit_tier_still_wins(game, live):
    """Planning for a tier you are about to unlock is legitimate."""
    req = build_scenario(game, live, objective="max_mw", exports=["MW"], belt_ipm=1200.0)
    assert req.scenario.belt_ipm == 1200.0


def test_the_tier_is_part_of_the_plan_id(game, live):
    """Two plans that differ only in carrier tier describe different factories -- the
    line counts and the block splits both move -- so they must not share an id."""
    a = build_scenario(game, live, objective="max_mw", exports=["MW"])
    b = build_scenario(game, live, objective="max_mw", exports=["MW"], pipe_m3min=300.0)
    assert a.plan_id != b.plan_id


# ------------------------------------------------------- saying so


def test_list_buildings_marks_locked_tiers(game):
    out = srv.list_buildings(building_kind="logistics")
    assert "have\tbuilding\tbuilt" in out
    assert "LOCKED\tConveyor Belt Mk.6" in out
    assert "HAVE\tPipeline Mk.2" in out


def test_list_buildings_names_the_planning_default(game):
    out = srv.list_buildings(building_kind="logistics")
    assert "fastest UNLOCKED tier" in out
    assert "Pipeline Mk.2" in out


def test_plan_layout_says_which_carriers_it_used(game):
    out = srv.plan_layout(
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=3,
    )
    line = next(x for x in out.splitlines() if "fastest you have UNLOCKED" in x)
    assert "Pipeline Mk.2" in line
    assert "Conveyor Belt Mk.5" in line


def test_halving_the_pipe_doubles_the_trunks(game):
    """The consequence the planner was worried about, made concrete: on a Mk1 pipe the
    Spire crude field needs twice the runs."""
    kw = dict(
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        show="trunks",
        limit=25,
    )
    mk2 = srv.plan_layout(pipe_tier="Mk2", **kw)
    mk1 = srv.plan_layout(pipe_tier="Mk1", **kw)

    def crude(out: str) -> int:
        return sum(1 for x in out.splitlines() if x.startswith("T") and "Crude Oil" in x)

    assert crude(mk1) > crude(mk2)

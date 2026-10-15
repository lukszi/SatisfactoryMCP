"""An infeasible plan names the limit it actually runs into, and nothing else.

Every failure used to end with the same two hints -- a free Nitrogen Gas supply does not
help, and a byproduct may have no consumer -- whatever the real cause was.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration

BOILERPLATE = ("Nitrogen Gas", "a byproduct has no consumer", "HiGHS")


@pytest.fixture(autouse=True)
def _reference_world(use_world, projection, game):
    use_world(lambda: WorldState(projection=projection, game=game))


def _notes(out: str) -> list[str]:
    assert out.startswith("# INFEASIBLE"), out
    return [line for line in out.splitlines() if line.startswith("! ")]


def _no_boilerplate(notes: list[str]) -> None:
    for line in notes:
        assert not any(b in line for b in BOILERPLATE), line


def test_an_objective_without_its_export_says_only_that():
    notes = _notes(
        srv.plan_factory(objective="max_mw", exports=["Computer"], export_minimums={"Computer": 2})
    )
    assert notes == [
        (
            "! objective max_mw maximises exported power, so it needs MW in exports: "
            "add MW to exports, or choose another objective"
        )
    ]


def test_a_locked_export_is_the_whole_answer():
    notes = _notes(
        srv.plan_factory(
            objective="min_machines", exports=["Turbo Motor"], export_minimums={"Turbo Motor": 1}
        )
    )
    assert len(notes) == 1 and "nothing in scope makes Turbo Motor" in notes[0]


def test_short_raw_is_named_with_what_the_scope_gives():
    notes = _notes(
        srv.plan_factory(
            objective="min_machines",
            exports=["Motor"],
            export_minimums={"Motor": 500},
            sources=list(REFERENCE_FIELD),
        )
    )
    _no_boilerplate(notes)
    assert notes[0].startswith("! short raw: ")
    assert "Coal at most 120/min" in notes[0]


def test_a_byproduct_is_named_only_when_letting_it_out_solves_the_plan():
    notes = _notes(
        srv.plan_factory(
            objective="min_machines",
            exports=["Fuel"],
            export_minimums={"Fuel": 100},
            allow_sinks=False,
            exclude_recipes=["Alternate: Diluted Fuel", "Alternate: Heavy Oil Residue"],
        )
    )
    _no_boilerplate(notes)
    assert notes == [
        (
            "! byproduct with no outlet: Polymer Resin -- letting it leave makes the plan solve: "
            "add it to exports, allow_sinks for a solid, or explain_byproducts for consumers"
        )
    ]


def test_the_page_cause_follows_the_named_limit(game, state):
    from satisfactory_mcp.domain.planning.readout.summary import solve_summary

    out = solve_summary(
        game,
        state,
        {
            "objective": "min_machines",
            "exports": ["Motor"],
            "export_minimums": {"Motor": 500},
            "sources": list(REFERENCE_FIELD),
        },
    )
    assert out["feasible"] is False
    assert out["cause"].startswith("these sources cannot supply enough Iron Ore")

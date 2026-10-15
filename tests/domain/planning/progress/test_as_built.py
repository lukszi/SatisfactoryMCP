"""A plan made from a built factory (docs/planning.md §8.5p): the mapping, the round trip and
the stored plan, over a hand-built line and the reference world's named factories."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.select import SelectorError
from satisfactory_mcp.domain.planning.progress import as_built
from satisfactory_mcp.domain.planning.readout import summary
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.projections import INSIDE, smelter_feeding_two_rods

PAGE = Actor("page")


def _line(game, **shape) -> WorldState:
    st = WorldState(projection=smelter_feeding_two_rods(**shape), game=game)
    st.labels.put("rod line", list(INSIDE))
    return st


def _solved(st: WorldState, name: str) -> tuple[as_built.AsBuilt, list[str]]:
    built = as_built.map_factory(st, name)
    solved = summary.solve_summary(st.game, st, built.args.kwargs())
    return built, as_built.compare(built, solved)


def test_a_line_maps_to_its_recipes_its_surplus_and_its_feed(game):
    built = as_built.map_factory(_line(game), "rod line")
    args = built.args
    assert built.factory == "rod line"
    assert args.required == ["Recipe_IngotIron_C", "Recipe_IronRod_C"]
    assert args.only_recipes == args.required
    assert args.exports == ["Desc_IronRod_C"]
    assert args.export_minimums == {"Desc_IronRod_C": 30.0}
    assert args.supplied == {"Desc_OreIron_C": 30.0}
    assert args.sources == ["none"], "no extractor taps a node, so the plan may tap none"
    assert (args.objective, args.water_extractors) == ("min_raw_power", 0)
    assert (args.payback_hours, args.overclock_last) == (0.0, False)
    assert set(built.rows) == {"Recipe_IngotIron_C", "Recipe_IronRod_C"}
    assert built.rows["Recipe_IronRod_C"].machines == 2


def test_the_plan_is_sited_on_the_machines(game):
    siting = as_built.map_factory(_line(game), "rod line").siting
    assert siting is not None
    x, y, _z = siting["origin_m"]
    assert (x, y) == (10.0, 0.0)
    assert siting["origin_label"] == "built “rod line”"


def test_a_paused_machine_makes_and_uses_nothing(game):
    built = as_built.map_factory(_line(game, paused=True), "rod line")
    assert built.args.export_minimums == {"Desc_IronRod_C": 15.0, "Desc_IronIngot_C": 15.0}
    assert built.rows["Recipe_IronRod_C"].machines == 1


def test_an_unknown_factory_is_refused_with_the_names_there_are(game):
    with pytest.raises(SelectorError, match="rod line"):
        as_built.map_factory(_line(game), "no such factory")


@pytest.mark.parametrize("name", ["aluminium setup", "biofuel setup", "tor factory"])
def test_a_factory_solves_back_to_itself(labelled, name):
    built, differences = _solved(labelled, name)
    assert differences == [], differences
    assert built.args.required


def test_water_extractors_are_counted_not_supplied(labelled):
    built = as_built.map_factory(labelled, "aluminium setup")
    assert built.args.water_extractors == 3
    assert "Desc_Water_C" not in built.args.supplied


def test_tapped_nodes_and_their_clocks_carry_over(labelled):
    args = as_built.map_factory(labelled, "steel factory").args
    assert args.sources and all(s.startswith("node:") for s in args.sources)
    assert args.extractor_clocks == [1.0, 2.0]


def test_generators_stay_out_and_what_they_burn_is_exported(labelled):
    built = as_built.map_factory(labelled, "coal powerplant")
    assert "Desc_Coal_C" in built.args.exports
    assert any("generators stay out" in note for note in built.notes)


def test_an_overclocked_row_is_named_as_a_difference(labelled):
    _built, differences = _solved(labelled, "tier 1&2")
    engine = next(line for line in differences if line.startswith("Modular Engine"))
    assert "built 1 Manufacturer at 250%" in engine and "3 Manufacturer at 83.33%" in engine


def test_a_plan_that_does_not_solve_says_why(labelled):
    _built, differences = _solved(labelled, "christmas factory")
    assert differences and differences[0].startswith("the plan does not solve")


def test_the_stored_plan_tracks_the_factory_and_keeps_the_differences(labelled):
    made = as_built.plan_from_factory(labelled, "tier 1&2", actor=PAGE)
    plan = PlanLog(labelled.world_id).state(made.pushed.key)
    assert (plan.name, plan.rev, plan.factory) == ("tier 1&2 as built", 1, "tier 1&2")
    assert plan.siting and plan.plan_id
    assert "Modular Engine" in plan.notes
    assert plan.args.required == made.built.args.required


def test_every_call_makes_a_new_plan_and_forget_takes_one_back(labelled):
    first = as_built.plan_from_factory(labelled, "biofuel setup", actor=PAGE).pushed
    second = as_built.plan_from_factory(labelled, "biofuel setup", actor=PAGE).pushed
    assert first.key != second.key
    assert second.state.name == "biofuel setup as built (2)"
    log = PlanLog(labelled.world_id)
    log.push(second.key, 1, [{"op": "forget"}], actor=PAGE)
    assert [s.name for s in log.heads()] == ["biofuel setup as built"]

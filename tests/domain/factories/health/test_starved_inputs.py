"""What feeds the input a starved machine lacks: the run, its far end, and what that makes."""

from __future__ import annotations

from collections import Counter

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.health import (
    FED,
    JOINED,
    NOTHING,
    OPEN,
    assess,
)
from satisfactory_mcp.domain.world.logistics import BASIS_UNKNOWN, build_physical_graph
from tests.support.factory_health import (
    ArrivingRuns,
    assess_records,
    link,
    machine,
    record_names,
    state_of,
    uptime_record,
)

pytestmark = pytest.mark.integration


def _starved(name):
    """An assembler on Black Powder holding Sulfur and no Coal."""
    return machine(
        name,
        "Recipe_Gunpowder_C",
        uptime=uptime_record(0.0),
        buffers={
            "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
            "out": {"items": {}, "slots": 2},
        },
    )


def test_a_starved_machine_names_what_feeds_the_input_it_lacks(game):
    name = "Build_AssemblerMk1_C_100"
    feeder = machine(
        "Build_ConstructorMk1_C_101",
        "Recipe_IronPlate_C",
        uptime=uptime_record(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = assess_records(
        game,
        machines=[_starved(name), feeder],
        physical=ArrivingRuns([link("Build_ConstructorMk1_C_101", name, pieces=7)]),
    )
    (feed,) = next(m for m in report.machines if m.instance == name).feeds
    assert (feed.item, feed.verdict) == ("Coal", FED)
    assert (feed.far, feed.pieces) == ("Build_ConstructorMk1_C_101", 7)
    assert feed.far_state == "starved", "the feeder's own health is the next thing to check"


def test_nothing_feeding_it_and_an_unjoined_run_are_different_findings(game):
    """The distinction the physical graph exists to keep. 24 runs on the reference world end
    at nothing, and reporting one of those as "no conduit feeds this" is a confident wrong
    claim rather than a finding."""
    empty = assess_records(
        game, machines=[_starved("Build_AssemblerMk1_C_102")], physical=ArrivingRuns([])
    )
    (missing,) = empty.machines[0].feeds
    assert (missing.verdict, missing.far) == (NOTHING, "")

    torn = assess_records(
        game,
        machines=[_starved("Build_AssemblerMk1_C_103")],
        physical=ArrivingRuns([link(None, "Build_AssemblerMk1_C_103")]),
    )
    (open_end,) = torn.machines[0].feeds
    assert open_end.verdict == OPEN
    assert open_end.pieces == 1, "the run is real and measured; only its far end is unknown"


def test_a_fluid_ingredient_looks_at_pipes_and_a_solid_at_belts(game):
    """A run carries whatever is put on it, so the medium is the only separation the save
    supports -- a conveyor arriving cannot be delivering the missing Water."""
    name = "Build_OilRefinery_C_104"
    wet = machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=uptime_record(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )
    report = assess_records(
        game, machines=[wet], physical=ArrivingRuns([link("Build_StorageContainerMk1_C_1", name)])
    )
    by_item = {f.item: f.verdict for f in report.machines[0].feeds}
    assert by_item["Water"] == NOTHING, "a conveyor cannot be delivering the water"
    assert by_item["Polymer Resin"] == FED


def test_an_undirected_run_is_not_reported_as_feeding(game):
    """A pipe between two fittings has no direction without the rates, so it JOINS the
    machine to the far end and the report must not claim which way it flows."""
    name = "Build_AssemblerMk1_C_105"
    report = assess_records(
        game,
        machines=[_starved(name)],
        physical=ArrivingRuns([link(name, "Build_PipelineJunction_C_9", basis=BASIS_UNKNOWN)]),
    )
    (feed,) = report.machines[0].feeds
    assert feed.verdict == JOINED
    assert feed.far == "Build_PipelineJunction_C_9", "walked from the end that is not us"


def test_without_a_physical_graph_nothing_is_called_unfed(game):
    """Absent evidence must not read as a finding, on `unwired`'s terms."""
    report = assess_records(game, machines=[_starved("Build_AssemblerMk1_C_106")])
    assert report.machines[0].state == "starved"
    assert report.machines[0].feeds == ()


def test_inputs_not_arriving_counts_only_what_is_missing(game):
    """The Sulfur is in the buffer, so only the Coal is an input not arriving."""
    report = assess_records(game, machines=[_starved("Build_AssemblerMk1_C_109")])
    assert report.starved_of == Counter({"Coal": 1})


def _generator(name, fuel, held):
    return machine(
        name,
        "",
        fuel=fuel,
        uptime=uptime_record(0.0),
        buffers={"fuel": {"items": held, "slots": 2}},
    )


def test_a_starved_generator_counts_and_a_hand_fed_burner_does_not(game):
    """A coal plant whose water stopped is a supply fault; an empty biomass burner is a
    player's armful not yet delivered (frontend_vision.md §8.7)."""
    burner = "Build_GeneratorBiomass_Automated_C_111"
    report = assess_records(
        game,
        generators=[
            _generator("Build_GeneratorCoal_C_110", "Desc_Coal_C", {"Desc_Coal_C": 50}),
            _generator(burner, "Desc_Leaves_C", {}),
        ],
    )
    assert state_of(report, "Build_GeneratorCoal_C_110") == "starved"
    assert state_of(report, burner) == "starved"
    assert report.starved_of == Counter({"Water": 1})


def test_a_never_fuelled_generator_is_not_an_input_not_arriving(game):
    """An empty hopper with no fuel class names no item: its ``(no fuel)`` cause is a state."""
    report = assess_records(
        game,
        generators=[
            _generator("Build_GeneratorCoal_C_112", "", {}),
            _generator("Build_GeneratorFuel_C_113", "", {}),
        ],
    )
    assert state_of(report, "Build_GeneratorCoal_C_112") == "starved"
    assert report.starved_of == Counter()


def test_a_feeder_that_provably_makes_the_item_is_marked(game):
    """Which of two arriving belts brings the ingots is not in the save; which far end can
    produce them at all is."""
    name = "Build_ConstructorMk1_C_107"
    hungry = machine(
        name,
        "Recipe_IronPlate_C",
        uptime=uptime_record(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 2}},
    )
    smelter = machine(
        "Build_SmelterMk1_C_108",
        "Recipe_IngotIron_C",
        uptime=uptime_record(1.0),
        buffers={"in": {"items": {"Desc_OreIron_C": 50}, "slots": 1}, "out": {"items": {}}},
    )
    report = assess_records(
        game,
        machines=[hungry, smelter],
        physical=ArrivingRuns(
            [link("Build_SmelterMk1_C_108", name), link("Build_StorageContainerMk1_C_2", name)]
        ),
    )
    marked = {f.far: f.makes for f in report.machines[0].feeds}
    assert marked == {"Build_SmelterMk1_C_108": True, "Build_StorageContainerMk1_C_2": False}, (
        "a container is not established as making anything, and false must not read as 'does not'"
    )


def test_every_starved_machine_that_has_a_feeder_names_the_run_to_go_and_look_at(projection, game):
    """What schema 20 was for, measured where it is actually spent.

    All 12 starved machines on the reference world are BELT-fed, so before the belt segment
    carried an actor index this section named a far-end actor for every real case and a run
    for none -- the feature was paid for and not collected. Now 16 of the 18 feed rows carry
    a ``chain:<n>`` a reader can hand straight to ``show_on_map`` or ``search_conduits``.

    The two rows without one are the assertion that matters most here, because they are the
    case that must NOT be papered over: their verdict is ``NOTHING``, no conveyor arrives at
    that machine at all, and there is therefore no run in existence to name. An id there
    would be an invention. So the rule pinned is not "every row has a run" but "every row
    that has a FEEDER has a run".
    """
    names = record_names(projection)
    graph = build_graph(projection)
    report = assess("all", names, game, projection, graph, build_physical_graph(projection, game))
    starved = [m for m in report.machines if m.state == "starved"]
    rows = [f for m in starved for f in m.feeds]
    assert (len(starved), len(rows)) == (12, 18)

    assert all(f.medium == ports.CONVEYOR for f in rows), "the world's starvation is all belts"
    assert [f.verdict for f in rows if not f.run] == [NOTHING, NOTHING]
    assert all(f.run.startswith("chain:") for f in rows if f.run)
    assert sum(1 for f in rows if f.run) == 16
    assert sum(1 for m in starved if any(f.run for f in m.feeds)) == 11

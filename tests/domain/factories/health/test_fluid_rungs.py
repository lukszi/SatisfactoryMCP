"""The plumbing manual's ladder (§24.5): the rung a starved fluid input stops at."""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.health import (
    CONNECTION,
    FLOW_RATE,
    HEAD_LIFT,
    UNDETERMINED,
    assess,
)
from satisfactory_mcp.domain.world import headlift as H
from satisfactory_mcp.domain.world.headlift import Crest, head_lift
from satisfactory_mcp.domain.world.logistics import build_physical_graph
from tests.support.factory_health import (
    ArrivingRuns,
    assess_records,
    head_lift_with,
    link,
    machine,
    record_names,
    rungs_of,
    uptime_record,
)

pytestmark = pytest.mark.integration


def _crest(consumers, fluid="Desc_Water_C"):
    return Crest(
        fluid=fluid,
        crest_m=20.0,
        head_m=17.0,
        consumers=tuple(consumers),
        marginal=False,
        assumed=True,
        pos=(0.0, 0.0, 20.0),
    )


def _refinery(name):
    """A refinery on Residual Plastic holding neither of its inputs.

    Its ingredients are one solid and one fluid, which is what makes it the machine to test
    the ladder on: only the Water may ever carry a rung.
    """
    return machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=uptime_record(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )


def test_a_fluid_no_pipe_reaches_stops_at_connection(game):
    name = "Build_OilRefinery_C_200"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_StorageContainerMk1_C_1", name)]),
        heads=head_lift_with(),
    )
    assert rungs_of(report, name)["Water"] == CONNECTION


def test_a_pipe_whose_far_end_is_nothing_stops_at_connection(game):
    """A torn line is rung (1) even though a pipe of the right medium does arrive."""
    name = "Build_OilRefinery_C_201"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link(None, name, medium=ports.PIPE)]),
        heads=head_lift_with(),
    )
    assert rungs_of(report, name)["Water"] == CONNECTION


def test_a_network_no_source_reaches_is_connection_and_not_head_lift(game):
    """The rung (1) fact only the head-lift model can see: the run arrives from a real
    fitting, so the conduit graph is satisfied, and yet nothing anywhere puts fluid in it.

    This is the ten refineries of the owner's newest saves, and calling them a head-lift
    fault would be ten wrong answers to one unfinished pipe.
    """
    name = "Build_OilRefinery_C_202"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=head_lift_with(unfed=[name]),
    )
    assert rungs_of(report, name)["Water"] == CONNECTION


def test_a_fed_fluid_behind_a_crest_stops_at_head_lift(game):
    name = "Build_OilRefinery_C_203"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=head_lift_with(crests=[_crest([name])]),
    )
    assert rungs_of(report, name)["Water"] == HEAD_LIFT


def test_a_crest_on_another_fluid_leaves_this_input_alone(game):
    """A machine takes several fluids; a crest names the one whose network it stands on."""
    name = "Build_OilRefinery_C_204"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=head_lift_with(crests=[_crest([name], fluid="Desc_LiquidOil_C")]),
    )
    assert rungs_of(report, name)["Water"] == FLOW_RATE


def test_connection_wins_over_a_crest_behind_the_same_machine(game):
    """THE rule of the ladder. A machine can be both unplumbed and, on the model's reading,
    below a hill; the manual says answer the first rung and stop, because a pump is the wrong
    thing to build when no pipe arrives at all.
    """
    name = "Build_OilRefinery_C_205"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([]),
        heads=head_lift_with(crests=[_crest([name])]),
    )
    assert rungs_of(report, name)["Water"] == CONNECTION


def test_a_connected_fluid_the_model_clears_reaches_flow_rate(game):
    name = "Build_OilRefinery_C_206"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=head_lift_with(),
    )
    assert rungs_of(report, name)["Water"] == FLOW_RATE


def test_without_the_head_lift_model_nothing_is_called_a_flow_rate_problem(game):
    """Rung (3) is EARNED by ruling rung (2) out, and the manual's red box says attempting
    it first is the usual mistake. No model, no verdict -- on `unwired`'s terms."""
    name = "Build_OilRefinery_C_207"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
    )
    assert rungs_of(report, name)["Water"] == UNDETERMINED


def test_a_solid_never_carries_a_rung_and_a_mixed_machine_reads_sensibly(game):
    """Head lift is not a thing that happens to Polymer Resin."""
    name = "Build_OilRefinery_C_208"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns([link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=head_lift_with(crests=[_crest([name])]),
    )
    assert rungs_of(report, name) == {"Polymer Resin": UNDETERMINED, "Water": HEAD_LIFT}
    (machine,) = report.machines
    assert machine.cause == ("Polymer Resin", "Water (head lift)")


def test_one_ingredient_reached_by_several_runs_gets_one_rung(game):
    """The rung belongs to the ingredient, not to a run: two pipes into one input is one
    diagnosis, and rows disagreeing about it would be two."""
    name = "Build_OilRefinery_C_209"
    report = assess_records(
        game,
        machines=[_refinery(name)],
        physical=ArrivingRuns(
            [
                link("Build_PipelineJunction_C_9", name, medium=ports.PIPE),
                link(None, name, medium=ports.PIPE),
            ]
        ),
        heads=head_lift_with(crests=[_crest([name])]),
    )
    water = [f for f in report.machines[0].feeds if f.item == "Water"]
    assert len(water) == 2
    assert {f.rung for f in water} == {HEAD_LIFT}


def test_the_reference_world_puts_no_fluid_on_the_ladder_at_all(projection, game):
    """The calibration record, and it is a fact about the world rather than about the code.

    Across every save on the machine these were measured on not one starved machine is short of a FLUID --
    a machine's fluid box is carried in its input inventory, so the ingredient is readable,
    and none of the 802 starved machines there is missing one. The ladder is correct and
    silent here, and this test fails the day that stops being true.
    """
    names = record_names(projection)
    graph = build_graph(projection)
    report = assess(
        "all",
        names,
        game,
        projection,
        graph,
        build_physical_graph(projection, game),
        head_lift(projection, game, graph),
    )
    assert report.by_state["starved"] == 12
    assert [f.rung for m in report.machines for f in m.feeds if f.rung] == []


def test_the_same_machines_flip_from_flow_rate_to_head_lift_when_the_pumps_go_dark(
    projection, game
):
    """THE acceptance test for the ladder, on real geometry and both ways round.

    Take every generator's supplemental water away and 32 of them read starved of Water. As
    the world is actually wired the plumbing reaches all of them, so the answer is rung (3),
    the rates -- and cutting power to every pump moves the SAME 32 to rung (2) without one
    of them still being told to check its supply. That is the manual's red box, and it is
    what the silence in the test above is a verdict about.
    """
    thirsty = [
        dict(
            record,
            buffers={"fuel": {"items": {record["fuel"]: 100}, "slots": 2}},
            uptime=uptime_record(0.0),
        )
        if record.get("fuel")
        else record
        for record in projection["generators"]
    ]
    patched = dict(projection, generators=thirsty)
    names = record_names(patched)
    graph = build_graph(projection)
    physical = build_physical_graph(projection, game)

    def rungs(heads):
        report = assess("all", names, game, patched, graph, physical, heads)
        assert report.by_state["starved"] == 44
        return Counter(
            rung for m in report.machines for rung in {f.rung for f in m.feeds if f.rung}
        )

    assert rungs(head_lift(projection, game, graph)) == {FLOW_RATE: 32}

    plumbing = H._build(projection, game, powered=set())
    fed = H._fed(plumbing)
    reach, whence, gated = H._spread(plumbing, fed, H.MACHINE_MAX_HEAD_LIFT_M, True)
    cut = {n for n, _a in plumbing.sinks if n in fed} - set(reach)
    dark = dataclasses.replace(
        head_lift(projection, game, graph),
        crests=tuple(H._crests(plumbing, reach, whence, cut, False, gated)),
    )
    assert len(dark.crests) == 5
    assert rungs(dark) == {HEAD_LIFT: 32}

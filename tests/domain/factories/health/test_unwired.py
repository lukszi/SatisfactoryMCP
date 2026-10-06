"""Machines no wire reaches, and circuits no generator stands on.

The save records no ``mHasPower`` and no ``mCircuitID``, so "unpowered" is never
claimed: a wire is the one positive electrical fact, and a circuit with no source on it
is the second (docs/save-projection.md §6.1a).
"""

from __future__ import annotations

import itertools

import pytest

from satisfactory_mcp.core.saveio.records import actor_class
from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph
from tests.support.factory_health import (
    SingleCircuit,
    assess_records,
    machine,
    state_of,
    uptime_record,
)

pytestmark = pytest.mark.integration


def _fed(name, uptime=0.0):
    """A machine with input, room in its output, and not producing -- a bare stall."""
    return machine(
        name,
        "Recipe_IronPlate_C",
        uptime=uptime_record(uptime),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 1}, "slots": 2},
        },
    )


def test_a_stalled_machine_no_wire_reaches_says_so(game):
    dark = _fed("Build_ConstructorMk1_C_90")
    lit = _fed("Build_ConstructorMk1_C_91")
    report = assess_records(
        game, machines=[dark, lit], graph=SingleCircuit({"Build_ConstructorMk1_C_91"})
    )
    assert state_of(report, "Build_ConstructorMk1_C_90") == "stalled"
    assert state_of(report, "Build_ConstructorMk1_C_91") == "stalled"
    causes = {m.instance: m.cause for m in report.machines}
    assert causes["Build_ConstructorMk1_C_90"] == ("no power connection",)
    assert causes["Build_ConstructorMk1_C_91"] == (), "a wired stall has no such evidence"


def test_without_a_graph_nothing_is_called_unwired(game):
    """Absent evidence must not read as a finding: every caller that passes no graph --
    the whole of this module before this section -- would otherwise report every machine."""
    report = assess_records(game, machines=[_fed("Build_ConstructorMk1_C_92")])
    assert report.unwired == []
    assert state_of(report, "Build_ConstructorMk1_C_92") == "stalled"
    assert report.machines[0].cause == ()


def test_being_wired_to_nothing_is_reported_whatever_the_state_is(game):
    """It is not a state, and that is the design: on the reference world all eight machines
    wired to nothing are `no recipe`, `paused` or `unmonitored`, and NONE is stalled."""
    idle = machine("Build_ConstructorMk1_C_93", "", uptime=uptime_record(0.0))
    paused = machine("Build_ConstructorMk1_C_94", "Recipe_IronPlate_C", paused=True)
    everything = {"Build_ConstructorMk1_C_93", "Build_ConstructorMk1_C_94"}
    report = assess_records(game, machines=[idle, paused], graph=SingleCircuit(set()))
    assert set(report.unwired) == everything
    assert state_of(report, "Build_ConstructorMk1_C_93") == "no recipe"
    assert state_of(report, "Build_ConstructorMk1_C_94") == "paused"


def test_a_fully_wired_factory_reports_none(game):
    report = assess_records(
        game,
        machines=[_fed("Build_ConstructorMk1_C_95")],
        graph=SingleCircuit({"Build_ConstructorMk1_C_95"}),
    )
    assert report.unwired == []


def _grid(*wires):
    """A real ``FactoryGraph`` carrying the given power edges and nothing else."""
    graph = FactoryGraph(cls={})
    for a, b in wires:
        graph.cls[a], graph.cls[b] = actor_class(a), actor_class(b)
        graph.power.append(Edge(a=a, b=b))
    return graph


def _burner(name):
    """A generator record, which is what makes a circuit a source of power."""
    return machine(name, "", buffers={"fuel": {"items": {"Desc_Leaves_C": 20}, "slots": 1}})


def test_a_stall_on_a_circuit_no_generator_stands_on_says_which(game):
    """The finding the degree-zero check cannot make: the wire is built, the source is not."""
    lit = _fed("Build_ConstructorMk1_C_96")
    stranded = _fed("Build_ConstructorMk1_C_97")
    graph = _grid(
        ("Build_GeneratorBiomass_C_98", "Build_PowerPoleMk1_C_1"),
        ("Build_PowerPoleMk1_C_1", "Build_ConstructorMk1_C_96"),
        ("Build_PowerPoleMk1_C_2", "Build_ConstructorMk1_C_97"),
    )
    report = assess_records(
        game,
        machines=[lit, stranded],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
        graph=graph,
    )
    causes = {m.instance: m.cause for m in report.machines}
    assert causes["Build_ConstructorMk1_C_97"] == ("no generator on its circuit",)
    assert causes["Build_ConstructorMk1_C_96"] == (), "a pole away from the burner is powered"
    assert report.no_generator == ["Build_ConstructorMk1_C_97"]
    assert report.unwired == [], "it is wired; that is the whole point of the second list"


def test_the_two_lists_are_disjoint_and_name_different_builds(game):
    """A machine on no wire is never also reported as being on a generator-less circuit:
    they
    are two answers to "what is unbuilt", and one machine has one of them."""
    graph = _grid(
        ("Build_GeneratorBiomass_C_98", "Build_SmelterMk1_C_99"),
        ("Build_PowerPoleMk1_C_2", "Build_ConstructorMk1_C_100"),
    )
    report = assess_records(
        game,
        machines=[
            _fed("Build_SmelterMk1_C_99"),
            _fed("Build_ConstructorMk1_C_100"),
            _fed("Build_ConstructorMk1_C_101"),
        ],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
        graph=graph,
    )
    assert report.unwired == ["Build_ConstructorMk1_C_101"]
    assert report.no_generator == ["Build_ConstructorMk1_C_100"]


def test_a_world_with_no_generator_anywhere_reports_no_dark_circuit(game):
    """The second-order absence. With no source in the projection every actor is unreachable,
    and "all 570 of your machines are dark" is a statement about the save, not a diagnosis --
    so the whole check stands down and only the wire itself is still reported."""
    graph = _grid(("Build_PowerPoleMk1_C_2", "Build_ConstructorMk1_C_102"))
    report = assess_records(game, machines=[_fed("Build_ConstructorMk1_C_102")], graph=graph)
    assert report.no_generator == []
    assert report.unwired == []
    assert report.machines[0].cause == (), "the stall keeps its honest silence"


def test_without_a_graph_no_circuit_is_called_generator_less(game):
    """`unwired`'s rule, applied to the wider claim: no graph, no finding."""
    report = assess_records(
        game,
        machines=[_fed("Build_ConstructorMk1_C_103")],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
    )
    assert report.no_generator == []
    assert report.machines[0].cause == ()


def test_a_generator_four_poles_away_still_counts_as_reaching(game):
    """Reachability, not adjacency: the whole point of the wires is that they carry."""
    hops = [f"Build_PowerPoleMk1_C_{i}" for i in range(3, 7)]
    chain = ["Build_GeneratorBiomass_C_98", *hops, "Build_ConstructorMk1_C_104"]
    graph = _grid(*itertools.pairwise(chain))
    report = assess_records(
        game,
        machines=[_fed("Build_ConstructorMk1_C_104")],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
        graph=graph,
    )
    assert report.no_generator == []
    assert report.machines[0].cause == ()


def test_being_on_a_generator_less_circuit_is_reported_whatever_the_state_is(game):
    """`unwired`'s design, and for the same reason: the ten refineries that motivated this
    are `unmonitored`, never having run, and a state would have hidden them behind that."""
    idle = machine("Build_OilRefinery_C_105", "Recipe_Alternate_HeavyOilResidue_C")
    graph = _grid(
        ("Build_GeneratorBiomass_C_98", "Build_PowerPoleMk1_C_1"),
        ("Build_PowerPoleMk1_C_2", "Build_OilRefinery_C_105"),
    )
    report = assess_records(
        game,
        machines=[idle],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
        graph=graph,
    )
    assert state_of(report, "Build_OilRefinery_C_105") == "unmonitored"
    assert report.no_generator == ["Build_OilRefinery_C_105"]


def _rewired(projection):
    """The reference world with one wired machine moved onto a pole of its own.

    A perturbation rather than a hand-built world, for the reason the fluid ladder's own
    fixtures give: the rendering under test reaches for the graph, the labels and the
    census, and a stub projection answers none of them. The fixture itself has no machine
    on a generator-less circuit -- asserted in ``tests/data/test_reference_counts`` -- so the case
    has to be made rather than found.
    """
    graph = projection["graph"]
    actors = [*graph["actors"], "Build_PowerPoleMk1_C_999999"]
    victim = next(
        a
        for i, a in enumerate(graph["actors"])
        if a.startswith("Build_OilRefinery_C_") and any(i in e[:2] for e in graph["power"])
    )
    index = graph["actors"].index(victim)
    power = [e for e in graph["power"] if index not in e[:2]]
    power.append([index, len(actors) - 1])
    return dict(projection, graph=dict(graph, actors=actors, power=power)), victim


def test_the_note_names_a_machine_on_a_circuit_with_no_source(monkeypatch, game, projection):
    """End to end, because the sentence is the deliverable: the tool used to answer a stall
    with "usually power" and now says which of the two unbuilt things it is."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as tool

    patched, victim = _rewired(projection)
    monkeypatch.setattr(
        tool, "_state", lambda save=None, world=None, as_of=None: WorldState(patched, game)
    )
    out = tool.factory_health(factory=f"machine:{victim}")
    assert "1 machine(s) are wired to a circuit NO GENERATOR stands on" in out
    assert victim in out
    assert "have no electrical connection at all" not in out, "it is wired; say the right one"

"""A machine that has never produced: when that silence is starvation and when not.

A never-run record carries no productivity window, permanently rather than until one
closes, and many of them have empty buffers and a recipe for innocent reasons -- a
fresh row of machines with belts already run -- so the buffers alone cannot decide.
Only a missing connection, or a fluid network no source reaches, promotes one.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.health import (
    CONNECTION,
    UNFED,
)
from tests.support.factory_health import (
    SingleCircuit,
    assess_records,
    head_lift_with,
    link,
    machine,
    rungs_of,
    state_of,
)

pytestmark = pytest.mark.integration


#: The two neighbours every case below is built beside, and they are load-bearing rather
#: than scenery: `assess` reads "no run of this medium arrives" as a fact only where the
#: conduit graph resolves that medium somewhere, so one belt AND one pipe have to resolve
#: for the refinery's dangling pipe to mean anything. `Starved.sav` resolves 2,275 runs.
ON_A_BELT = "Build_ConstructorMk1_C_2147441491"


ON_A_PIPE = "Build_OilRefinery_C_2146795155"


class _PerActor:
    """A ``PhysicalGraph`` that answers per actor, unlike ``ArrivingRuns``."""

    def __init__(self, arriving):
        self.arriving = dict(arriving)

    def feeds(self, actor):
        return list(self.arriving.get(actor, ()))


def _never_run(name, recipe="Recipe_Alternate_HeavyOilResidue_C", held=None, out=None):
    """The refinery's shape: a recipe, empty buffers, and no uptime key whatsoever."""
    return machine(
        name,
        recipe,
        buffers={
            "in": {"items": held or {}, "slots": 2},
            "out": {"items": out or {}, "slots": 3},
        },
    )


def _neighbours(exclude=""):
    """One machine on a belt that resolves and one refinery on a pipe that resolves."""
    return [
        record
        for record in (
            _never_run(ON_A_BELT, "Recipe_IronRod_C", held={"Desc_IronIngot_C": 100}),
            _never_run(ON_A_PIPE, held={"Desc_LiquidOil_C": 300}),
        )
        if record["instance"] != f"L:P.{exclude}"
    ]


def _world(**arriving):
    return _PerActor(
        {
            ON_A_BELT: [link("Build_ConveyorAttachmentSplitter_C_2147444586", ON_A_BELT)],
            ON_A_PIPE: [link("Build_Pipeline_C_2146795200", ON_A_PIPE, medium=ports.PIPE)],
            **arriving,
        }
    )


def _beside(game, record, arriving=(), **kw):
    """Assess one never-run machine in a world whose belts AND pipes demonstrably resolve."""
    name = record["instance"].rsplit(".", 1)[-1]
    return assess_records(
        game,
        machines=[record, *_neighbours(exclude=name)],
        graph=SingleCircuit({name, ON_A_BELT, ON_A_PIPE}),
        physical=_world(**{name: list(arriving)}),
        **kw,
    )


def test_a_never_run_machine_no_pipe_reaches_is_starved_on_the_connection_rung(game):
    """THE case. `uptime is None` used to answer before the buffers were read, so this
    machine was `unmonitored`, never entered `needs_attention`, and the fluid ladder --
    gated on `state == "starved"` -- was never called on it at all."""
    name = "Build_OilRefinery_C_2146786838"
    report = _beside(game, _never_run(name), heads=head_lift_with())
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.uptime is None, "no window, and it is not being invented"
    assert machine.state == "starved"
    assert machine.needs_attention
    assert machine.cause == ("Crude Oil (connection)",)
    assert rungs_of(report, name) == {"Crude Oil": CONNECTION}
    assert [m.instance for m in report.worst()] == [name]


def test_a_closed_window_on_the_same_record_still_gives_the_old_answer(game):
    """The counterfactual that proved this was only a gate: injecting a closed window into
    that refinery's record and running the UNCHANGED code already gave this."""
    name = "Build_OilRefinery_C_2146786838"
    record = _never_run(name)
    record["uptime"] = {"window_s": 300.0, "produce_s": 0.0}
    report = _beside(game, record, heads=head_lift_with())
    machine = next(m for m in report.machines if m.instance == name)
    assert (machine.state, machine.uptime) == ("starved", 0.0)
    assert machine.cause == ("Crude Oil (connection)",)


def test_a_never_run_machine_a_belt_does_reach_stays_quiet(game):
    """The eight Constructors that must NOT light up. Same empty buffers, same absent
    window, but a conveyor arrives from a real splitter: the build is finished and nothing
    has come down it yet, which is a base mid-construction and not a fault."""
    name = "Build_ConstructorMk1_C_2146956309"
    report = _beside(
        game,
        _never_run(name, "Recipe_IronPlate_C"),
        arriving=[link("Build_ConveyorAttachmentSplitter_C_2146887273", name)],
        heads=head_lift_with(),
    )
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.state == "unmonitored"
    assert not machine.needs_attention
    assert report.worst() == []


def test_a_medium_the_conduit_graph_never_resolves_promotes_nothing(game):
    """THE correction to this rule, and it is per MEDIUM rather than per world. Save versions
    25 to 36 on the machine these were measured on resolve 88 pipe runs between coal generators and NOT ONE
    conveyor run, in worlds of 6,266 material couplings. Reading that as "no belt feeds this"
    turned 39 smelters and constructors in a single save into findings, every one of them the
    projection's blind spot rather than the world's -- and a world-wide "does anything
    resolve" test passes there, because the pipes do."""
    name = "Build_SmelterMk1_C_310"
    pipes_only = _PerActor(
        {ON_A_PIPE: [link("Build_Pipeline_C_2146795200", ON_A_PIPE, medium=ports.PIPE)]}
    )
    report = assess_records(
        game,
        machines=[_never_run(name, "Recipe_IngotIron_C"), _never_run(ON_A_PIPE)],
        graph=SingleCircuit({name, ON_A_PIPE}),
        physical=pipes_only,
        heads=head_lift_with(),
    )
    assert state_of(report, name) == "unmonitored"


def test_a_never_run_machine_whose_run_reaches_nothing_stays_quiet(game):
    """OPEN is not NOTHING, and this module's own vocabulary says so: a run does arrive and
    the save joins its far end to no actor, which is a feeder unknown rather than a feeder
    absent. Ten FICSMAS Constructors on one save are exactly this and must stay quiet."""
    name = "Build_ConstructorMk1_C_2145270171"
    report = _beside(
        game,
        _never_run(name, "Recipe_CandyCane_C"),
        arriving=[link(None, name)],
        heads=head_lift_with(),
    )
    assert state_of(report, name) == "unmonitored"


def test_a_never_run_machine_holding_its_ingredients_stays_quiet(game):
    """Nothing is missing, so there is nothing an absent window is hiding."""
    report = _beside(
        game, _never_run(ON_A_BELT, "Recipe_IronRod_C", held={"Desc_IronIngot_C": 100})
    )
    assert state_of(report, ON_A_BELT) == "unmonitored"


def test_a_never_run_machine_with_no_recipe_stays_quiet(game):
    """Eight Smelters in that save. No recipe is the player's own answer to why it is
    stopped, and it already outranks this branch."""
    name = "Build_SmelterMk1_C_300"
    report = _beside(game, _never_run(name, ""))
    assert next(m for m in report.machines if m.instance == name).state == "no recipe"


def test_a_never_run_generator_out_of_fuel_stays_quiet(game):
    """Ten Biomass Burners in that save hold no fuel and never will unless the player walks
    over with an armful. A fuel inventory is not a recipe's ingredient list, so it is not
    evidence that a run was ever meant to arrive."""
    name = "Build_GeneratorBiomass_C_301"
    record = machine(name, "", buffers={"fuel": {"items": {}, "slots": 1}})
    report = assess_records(
        game,
        generators=[record],
        machines=_neighbours(),
        graph=SingleCircuit({name, ON_A_BELT, ON_A_PIPE}),
        physical=_world(),
    )
    assert next(m for m in report.machines if m.instance == name).state == "unmonitored"


def test_without_a_physical_graph_a_never_run_machine_is_never_promoted(game):
    """Absent evidence must not read as a finding, on `unwired`'s terms -- and /api/machines
    supplies no conduit graph, so this is the map's answer for these machines."""
    name = "Build_OilRefinery_C_302"
    report = assess_records(game, machines=[_never_run(name)], graph=SingleCircuit({name}))
    assert report.machines[0].state == "unmonitored"
    assert report.machines[0].feeds == ()


def test_a_never_run_machine_backed_up_at_the_output_stays_quiet(game):
    """Five Constructors in that save sit on 497-499 of a 500 stack with no window. A full
    output box is not actionable on its own -- the tool's own note says so -- and there is no
    second, structural fact behind it the way there is for an input no run reaches."""
    name = "Build_ConstructorMk1_C_2147447118"
    record = _never_run(
        name, "Recipe_Screw_C", held={"Desc_IronRod_C": 200}, out={"Desc_IronScrew_C": 498}
    )
    report = _beside(game, record)
    assert next(m for m in report.machines if m.instance == name).state == "unmonitored"


# ------------------- rung (1)'s second form: a pipe arrives, but no source reaches its network


def test_a_never_run_machine_on_a_network_no_source_reaches_is_starved(game):
    """The pipe is REAL, so `_cut_off`'s conduit form declines it and the head-lift form
    catches it. The cause has to say which fluid and that the network has no source, because
    that is the actionable difference: the fix is a source, not a pump and not a pipe."""
    name = "Build_OilRefinery_C_2145161411"
    report = _beside(
        game,
        _never_run(name),
        arriving=[link("Build_PipelineJunction_T_C_2144913486", name, medium=ports.PIPE)],
        heads=head_lift_with(unfed=[name]),
    )
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.uptime is None
    assert machine.state == "starved"
    assert machine.cause == ("Crude Oil (connection: no source on its network)",)
    assert [f.verdict for f in machine.feeds] == [UNFED]
    # Still rung (1), so the ladder's own counts do not gain a fourth rung.
    assert rungs_of(report, name) == {"Crude Oil": CONNECTION}


def test_a_never_run_machine_whose_network_has_a_source_stays_quiet(game):
    """The case that must NOT promote, and it is the same record: a pipe arrives from a real
    fitting and a source does reach it, so an empty buffer on a machine that has never run is
    a build mid-commissioning rather than a fault."""
    name = "Build_OilRefinery_C_2145162120"
    report = _beside(
        game,
        _never_run(name),
        arriving=[link("Build_PipelineJunction_T_C_2144913767", name, medium=ports.PIPE)],
        heads=head_lift_with(),
    )
    assert state_of(report, name) == "unmonitored"


def test_an_unfed_port_does_not_promote_a_missing_SOLID(game):
    """``unfed_ports`` is a fact about pipes. A machine can stand on a dead fluid network and
    lack a solid, and the belt bringing that solid is the head-lift model's blind spot."""
    name = "Build_ConstructorMk1_C_2146956309"
    report = _beside(
        game,
        _never_run(name, "Recipe_IronPlate_C"),
        arriving=[link("Build_ConveyorAttachmentSplitter_C_2146887273", name)],
        heads=head_lift_with(unfed=[name]),
    )
    assert state_of(report, name) == "unmonitored"


def test_without_a_head_lift_model_the_unfed_form_promotes_nothing(game):
    """Absent evidence is not a finding: `assess` takes ``heads`` optionally and /api/machines
    supplies none, so the promotion must need the model rather than merely tolerate it."""
    name = "Build_OilRefinery_C_2145162762"
    report = _beside(
        game,
        _never_run(name),
        arriving=[link("Build_PipelineJunction_T_C_2144914070", name, medium=ports.PIPE)],
    )
    assert state_of(report, name) == "unmonitored"


def test_an_unfed_machine_holding_its_fluid_stays_quiet(game):
    """A dead network is not a verdict on a machine that has what it needs -- which is what
    keeps this rule off a Refinery that is merely paused between runs."""
    name = "Build_OilRefinery_C_2145163705"
    report = _beside(
        game,
        _never_run(name, held={"Desc_LiquidOil_C": 300}),
        arriving=[link("Build_PipelineJunction_T_C_2144918347", name, medium=ports.PIPE)],
        heads=head_lift_with(unfed=[name]),
    )
    assert state_of(report, name) == "unmonitored"

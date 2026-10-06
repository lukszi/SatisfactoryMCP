"""The head-lift model: every rule once, and the reference world as the acceptance test.

The model exists to be SILENT on a working base, so the test that matters most is the last
section: the owner's world reports no FAULT, the one line it does name is pinned as the
disagreement it is, and the perturbations beside it prove the silence is a verdict rather
than a model with nothing to say.
"""

from __future__ import annotations

import dataclasses

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.world import headlift as H
from satisfactory_mcp.domain.world.headlift import head_lift

pytestmark = pytest.mark.integration

#: Role indices for the hand-built worlds below, in one place so a test reads as plumbing.
R = {
    "PipelineConnection0": 0,
    "PipelineConnection1": 1,
    "FGPipeConnectionFactory": 2,
    "PipeInputFactory": 3,
    "PipeOutputFactory": 4,
    "Connection0": 5,
    "Connection1": 6,
    "Connection2": 7,
    "Connection3": 8,
    "ConnectionAny0": 9,
    "ConnectionAny1": 10,
}
ROLES = [name for name, _ in sorted(R.items(), key=lambda kv: kv[1])]

WATER = "Desc_Water_C"
NITROGEN = "Desc_NitrogenGas_C"


def _couple(a, ra, b, rb):
    """Both directions, because the save writes both and the reader relies on it."""
    return [[a, b, R[ra], R[rb]], [b, a, R[rb], R[ra]]]


class _World:
    """A projection built one actor and one pipe at a time, in metres."""

    def __init__(self, fluid=WATER):
        self.actors: list[str] = []
        self.material: list = []
        self.segments: list = []
        self.power: list = []
        self.networks = [{"id": 0, "fluid": fluid}]
        self.extractors: list = []
        self.generators: list = []
        self.storage: list = []

    def produces(self, cls):
        self.extractors.append({"cls": cls, "instance": f"x.{cls}_0"})

    def consumes(self, cls):
        self.generators.append({"cls": cls, "instance": f"x.{cls}_0"})

    def actor(self, cls, index=None):
        name = f"{cls}_{index if index is not None else len(self.actors) + 1}"
        self.actors.append(name)
        return len(self.actors) - 1

    def pipe(self, a, ra, b, rb, points, network=0):
        """One pipe joining two ports, its polyline given as (x, z) pairs in metres."""
        piece = self.actor("Build_Pipeline_C")
        self.material += _couple(a, ra, piece, "PipelineConnection0")
        self.material += _couple(piece, "PipelineConnection1", b, rb)
        self.segments.append([network, 0, [[x * 100.0, 0.0, z * 100.0] for x, z in points], piece])
        return piece

    def wire(self, actor):
        pole = self.actor("Build_PowerPoleMk1_C")
        self.power.append([actor, pole])

    def out(self):
        return {
            "graph": {
                "actors": self.actors,
                "roles": ROLES,
                "material": self.material,
                "power": self.power,
            },
            "pipes": {
                "classes": ["Build_Pipeline_C"],
                "networks": self.networks,
                "segments": self.segments,
            },
            "extractors": self.extractors,
            "generators": self.generators,
            "storage": self.storage,
        }


def _straight(consumer_z: float, *, source_z: float = 0.0, crest_z: float | None = None):
    """A water extractor and one coal generator, joined by a single pipe."""
    w = _World()
    pump = w.actor("Build_WaterPump_C")
    burner = w.actor("Build_GeneratorCoal_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    middle = [(5.0, crest_z)] if crest_z is not None else []
    w.pipe(
        pump,
        "FGPipeConnectionFactory",
        burner,
        "FGPipeConnectionFactory",
        [(0.0, source_z), *middle, (10.0, consumer_z)],
    )
    return w


def _verdict(world, game):
    projection = world.out() if isinstance(world, _World) else world
    return head_lift(projection, game, build_graph(projection))


# --------------------------------------------------------------- the altitude rule


def test_a_consumer_below_its_source_plus_ten_metres_is_supplied(game):
    assert _verdict(_straight(5.0), game).crests == ()


def test_a_consumer_above_the_tolerance_ceiling_is_a_crest(game):
    """A fault is measured against the CEILING, so its shortfall is the understated one."""
    report = _verdict(_straight(15.0), game)
    (crest,) = report.crests
    assert not crest.marginal
    assert crest.crest_m == pytest.approx(15.0)
    assert crest.head_m == pytest.approx(11.020)
    assert crest.short_m == pytest.approx(3.98)
    assert crest.consumers == ("Build_GeneratorCoal_C_2",)
    assert crest.fluid == WATER


def test_a_consumer_between_the_rating_and_the_ceiling_is_marginal(game):
    (crest,) = _verdict(_straight(10.5), game).crests
    assert crest.marginal
    assert crest.consumers == ("Build_GeneratorCoal_C_2",)


def test_the_measured_ceiling_is_eleven_so_a_climb_past_it_is_a_fault_not_a_warning(game):
    """The correction that moves a verdict: 11.5 m was marginal-but-reachable at a 12 m
    ceiling, and the game does not deliver it. Measured at 11.020 +-0.26, so 11.5 is outside
    the bar rather than inside it."""
    (crest,) = _verdict(_straight(11.5), game).crests
    assert not crest.marginal


def test_a_verdict_says_whether_the_game_or_the_manual_is_behind_it(game):
    """A Water Extractor states "Head Lift: 10 m" in its own description, so a crest behind
    one rests on the game. A Converter states nothing and falls back to the pinned figure,
    which is the only case left that has to declare itself."""
    (stated,) = _verdict(_straight(15.0), game).crests
    assert not stated.assumed

    w = _straight(15.0)
    w.actors[0] = "Build_Converter_C_1"
    w.extractors = [{"cls": "Build_Converter_C", "instance": "x.Build_Converter_C_0"}]
    (pinned,) = _verdict(w, game).crests
    assert pinned.assumed
    assert pinned.head_m == pytest.approx(11.020)


def test_the_stated_rating_is_read_per_class_and_not_pinned_for_all_six(game):
    """All six that state it say 10 m, so nothing moves today -- what moves is the claim."""
    assert [
        game.buildings[c].machine_head_lift_m
        for c in (
            "Build_WaterPump_C",
            "Build_OilPump_C",
            "Build_OilRefinery_C",
            "Build_Packager_C",
            "Build_Blender_C",
            "Build_FrackingExtractor_C",
        )
    ] == [10.0] * 6
    # The prose parse is checked against a field on the only two classes carrying both.
    assert game.buildings["Build_PipelinePump_C"].machine_head_lift_m == 20.0
    assert game.buildings["Build_PipelinePumpMk2_C"].machine_head_lift_m == 50.0
    # And it is NOT head_lift_m, which is what makes a building a pump.
    assert game.buildings["Build_OilRefinery_C"].head_lift_m == 0.0


def test_a_hill_between_two_low_ends_stops_the_fluid(game):
    """The manual's own worked example: the highest CREST binds, not the far end.

    Both machines sit at 0 m, so the destination is trivially in reach; the 15 m hump the
    pipe crosses on the way is what the head has to clear.
    """
    report = _verdict(_straight(0.0, crest_z=15.0), game)
    (crest,) = report.crests
    assert crest.crest_m == pytest.approx(15.0)
    assert _verdict(_straight(0.0, crest_z=9.0), game).crests == ()


# --------------------------------------------------------------- pumps


def _pumped(consumer_z, count=1, cls="Build_PipelinePump_C", powered=True):
    """A water extractor at sea level, ``count`` pumps end to end, then one generator."""
    w = _World()
    node = w.actor("Build_WaterPump_C")
    w.produces("Build_WaterPump_C")
    port = "FGPipeConnectionFactory"
    for i in range(count):
        pump = w.actor(cls)
        if powered:
            w.wire(pump)
        w.pipe(node, port, pump, "Connection0", [(i * 2.0, 0.0), (i * 2.0 + 1.0, 0.0)])
        node, port = pump, "Connection1"
    burner = w.actor("Build_GeneratorCoal_C")
    w.consumes("Build_GeneratorCoal_C")
    w.pipe(node, port, burner, "FGPipeConnectionFactory", [(20.0, 0.0), (21.0, consumer_z)])
    return w


def test_three_pumps_in_a_row_lift_twenty_metres_and_not_sixty(game):
    """The rule the whole model turns on. A max cannot become a sum by repetition."""
    assert _verdict(_pumped(19.0, count=3), game).crests == ()
    (crest,) = _verdict(_pumped(25.0, count=3), game).crests
    assert crest.head_m == pytest.approx(22.801)  # the Mk1's MEASURED reach


def _staged(consumer_z):
    """A Mk2 at sea level lifting to a Mk1 that stands 40 m up, then on to a consumer."""
    w = _World()
    source = w.actor("Build_WaterPump_C")
    big = w.actor("Build_PipelinePumpMk2_C")
    small = w.actor("Build_PipelinePump_C")
    burner = w.actor("Build_GeneratorCoal_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    w.wire(big)
    w.wire(small)
    w.pipe(source, "FGPipeConnectionFactory", big, "Connection0", [(0.0, 0.0), (1.0, 0.0)])
    w.pipe(big, "Connection1", small, "Connection0", [(1.0, 0.0), (20.0, 40.0)])
    w.pipe(
        small, "Connection1", burner, "FGPipeConnectionFactory", [(20.0, 40.0), (30.0, consumer_z)]
    )
    return w


def test_a_pump_lifts_from_its_own_centre_so_the_height_under_it_stacks(game):
    """The Mk1 stands where the Mk2 put the fluid, so it reaches 60 m -- and not 70.

    ``50 + 20`` is the pump-to-pump sum the manual forbids and it would clear 65 m; ``40 +
    20`` is the rule, measured from the Mk1's own centre, and it does not.
    """
    assert _verdict(_staged(59.0), game).crests == ()
    (crest,) = _verdict(_staged(65.0), game).crests
    assert crest.head_m == pytest.approx(62.801)  # 40 m under it plus the Mk1's measured reach


def test_a_measured_pump_reaches_past_its_declared_ceiling_and_an_unmeasured_one_does_not(game):
    """The dump says what the game DECLARES and stays untouched; the register says what the
    game was seen to DO. A Mk1 stands 0.80 m above its own mMaxPressure, so a climb to 22.5 m
    is a warning where the declaration alone would have made it a fault. The Mk2 is
    unmeasured and is not given a factor borrowed from the Mk1: it stops at the 55 it
    declares."""
    assert game.buildings["Build_PipelinePump_C"].max_head_lift_m == 22.0
    (warned,) = _verdict(_pumped(22.5), game).crests
    assert warned.marginal  # inside the measured reach, outside the rating
    (crest,) = _verdict(_pumped(23.0), game).crests
    assert not crest.marginal
    assert crest.head_m == pytest.approx(22.801)

    assert game.buildings["Build_PipelinePumpMk2_C"].max_head_lift_m == 55.0
    (mk2,) = _verdict(_pumped(56.0, cls="Build_PipelinePumpMk2_C"), game).crests
    assert mk2.head_m == pytest.approx(55.0)


def test_a_pump_no_wire_reaches_sets_the_head_past_it_to_its_own_centre(game):
    """It still passes fluid, so nothing downstream looks broken -- which is the point."""
    assert _verdict(_pumped(8.0, powered=True), game).crests == ()
    (crest,) = _verdict(_pumped(8.0, powered=False), game).crests
    assert crest.head_m == pytest.approx(0.0)
    assert not crest.assumed


def _uphill_pump(consumer_z, pump_z=20.0, powered=True):
    """An extractor at sea level, a pump standing far above anything its head can reach."""
    w = _World()
    source = w.actor("Build_WaterPump_C")
    pump = w.actor("Build_PipelinePump_C")
    burner = w.actor("Build_GeneratorCoal_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    if powered:
        w.wire(pump)
    w.pipe(source, "FGPipeConnectionFactory", pump, "Connection0", [(0.0, 0.0), (5.0, pump_z)])
    w.pipe(
        pump, "Connection1", burner, "FGPipeConnectionFactory", [(5.0, pump_z), (10.0, consumer_z)]
    )
    return w


def test_a_powered_pump_draws_from_below_its_own_inlet(game):
    """The rule the owner's fuel line forced: a powered pump does not wait to be reached.

    The extractor's 11.02 m stops 9 m short of this pump's inlet and the pump lifts anyway,
    from its own centre, because fluid ARRIVING is what settles an inlet -- rung (1) -- and
    an altitude is what settles everything else. Measured on network 99, where the whole
    climbing pipe stands above the buffer's surface and is 18-65% full in 31 of 48 saves.
    """
    assert _verdict(_uphill_pump(39.0), game).crests == ()
    (crest,) = _verdict(_uphill_pump(43.0), game).crests
    assert crest.head_m == pytest.approx(42.801)  # 20 m centre plus the Mk1's measured reach


def test_an_unpowered_pump_draws_nothing_and_the_climb_to_it_still_binds(game):
    """Suction is the powered pump's, so cutting the wire puts the crest back on the pipe."""
    (crest,) = _verdict(_uphill_pump(39.0, powered=False), game).crests
    assert crest.crest_m == pytest.approx(20.0)
    assert crest.head_m == pytest.approx(11.020)


def test_a_valve_passes_head_lift_and_never_counts_as_unpowered(game):
    """A Valve is the same native class as a pump and lifts zero, so a wire is nothing to it."""
    world = _pumped(8.0, cls="Build_Valve_C", powered=False)
    assert _verdict(world, game).crests == ()


def test_the_mark_two_pump_is_read_from_game_data_and_not_from_its_name(game):
    assert _verdict(_pumped(49.0, cls="Build_PipelinePumpMk2_C"), game).crests == ()
    assert _verdict(_pumped(60.0, cls="Build_PipelinePumpMk2_C"), game).crests != ()


# --------------------------------------------------------------- bodies and buffers


def _tanked(stored_m3, consumer_z, cls="Build_IndustrialTank_C", connector_z=0.0):
    w = _World()
    tank = w.actor(cls)
    burner = w.actor("Build_GeneratorCoal_C")
    w.consumes("Build_GeneratorCoal_C")
    w.storage.append(
        {
            "cls": cls,
            "instance": f"Persistent_Level:PersistentLevel.{w.actors[tank]}",
            "pos": [0, 0, 0],
            "stored_m3": stored_m3,
            "fluid": WATER,
        }
    )
    w.pipe(
        tank,
        "ConnectionAny0",
        burner,
        "FGPipeConnectionFactory",
        [(0.0, connector_z), (5.0, consumer_z)],
    )
    return w


def _through_tank(stored_m3, consumer_z, cls="Build_IndustrialTank_C"):
    """An extractor 20 m up feeding a buffer at sea level, then a consumer.

    The source is deliberately high enough that its head (31.02 m) clears the buffer's own
    full column (12 m), so what arrives past the buffer tells the two rules apart.
    """
    w = _tanked(stored_m3, consumer_z, cls)
    source = w.actor("Build_WaterPump_C")
    w.produces("Build_WaterPump_C")
    w.pipe(source, "FGPipeConnectionFactory", 0, "ConnectionAny1", [(-5.0, 20.0), (0.0, 0.0)])
    return w


def test_a_buffers_head_lift_is_the_height_of_the_fluid_standing_in_it(game):
    """Full, an Industrial Buffer gives its own 12 m; a tenth full it gives a tenth of that."""
    assert _verdict(_tanked(2400.0, 11.0), game).crests == ()
    (crest,) = _verdict(_tanked(240.0, 11.0), game).crests
    assert crest.head_m == pytest.approx(1.2)


def test_a_part_full_buffer_passes_no_incoming_head_at_all(game):
    """The step, and the unsafe direction it closes: a buffer is a body for CONNECTION and a
    barrier for HEIGHT. Half full, the 31 m arriving is not what the line above it gets --
    the buffer's own 6 m is, so a consumer at 25 m is cut off rather than supplied."""
    (crest,) = _verdict(_through_tank(1200.0, 25.0), game).crests
    assert crest.head_m == pytest.approx(6.0)
    assert not crest.assumed


def test_a_full_buffer_passes_incoming_head_through_unchanged(game):
    """Measured at 94 mm of difference, inside the +-0.26 m bar: it neither attenuates nor
    stacks. Stacking would clear 43 m and was excluded by 7.1 m."""
    assert _verdict(_through_tank(2400.0, 25.0), game).crests == ()
    (crest,) = _verdict(_through_tank(2400.0, 40.0), game).crests
    assert crest.head_m == pytest.approx(31.020)


def test_a_buffer_inside_the_measured_bracket_is_counted_rather_than_decided_quietly(game):
    """99.96% is above the fill measured OFF and below the one measured ON, so the constant
    settles it and the verdict says how many buffers it settled.

    The band is 0.7 percentage points wide, so a buffer has to be within a couple of cubic
    metres of capacity to land in it: 95% is now DECIDED, by a measurement that says off.
    """
    report = _verdict(_through_tank(2399.0, 25.0), game)
    assert report.undecided_buffers == 1
    (crest,) = report.crests
    assert crest.head_m == pytest.approx(11.995)
    assert _verdict(_through_tank(2280.0, 25.0), game).undecided_buffers == 0


def test_a_buffer_delivers_at_its_connectors_however_little_it_holds(game):
    """A buffer's connectors stand 1.75 m up its side, so under 14.6% of an Industrial one
    its own surface does not reach them -- and it delivers anyway. ``BUF_OUT`` measured
    7.19 m3 crossing a flat pipe out of a buffer 4.3% full whose surface stood 1.40 m below
    that pipe, in a closed pair conserved to 0.00001 m3 over 299 s.

    So the surface is a FLOOR on what the buffer offers and never a cap below its own
    outlet. The barrier above it is unchanged: a tenth full it still cannot lift a consumer
    to 11 m, and it is still named rather than dropped.
    """
    (crest,) = _verdict(_tanked(240.0, 11.0, connector_z=1.75), game).crests
    assert crest.buffer_gated
    assert crest.head_m == pytest.approx(1.75)  # its connectors, above the 1.2 m it holds
    assert crest.crest_m == pytest.approx(11.0)
    assert crest.consumers == ("Build_GeneratorCoal_C_2",)


def test_a_consumer_on_the_flat_at_a_near_empty_buffers_connectors_is_fed(game):
    """The ``BUF_OUT`` rig in miniature, and the verdict the old rule got wrong.

    A tenth-full Industrial Buffer stood 1.2 m in its own tank and its outlet is 1.75 m up,
    so the head was 0.55 m short of a pipe leaving on the flat and everything past it was
    called cut off. The owner's fuel line is exactly this shape at 400 m3 scale.
    """
    assert _verdict(_tanked(240.0, 1.75, connector_z=1.75), game).crests == ()


def test_a_t_junction_is_a_body_and_not_a_machine_port(game):
    """The T and the cross are one NATIVE class, and matching on the build class missed it.

    A junction taken for a machine invents a source with 10 m of head at every one of its
    ports, which is worse than a missed finding: it hides real ones.
    """
    w = _World()
    pump = w.actor("Build_WaterPump_C")
    tee = w.actor("Build_PipelineJunction_T_C")
    burner = w.actor("Build_GeneratorCoal_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    w.pipe(pump, "FGPipeConnectionFactory", tee, "Connection0", [(0.0, 0.0), (5.0, 0.0)])
    w.pipe(tee, "Connection1", burner, "FGPipeConnectionFactory", [(5.0, 0.0), (10.0, 15.0)])
    report = _verdict(w, game)
    assert report.ambiguous_ports == 0
    assert report.consumers == 1
    (crest,) = report.crests
    assert crest.head_m == pytest.approx(11.020)


# --------------------------------------------------------------- what is refused


def test_a_gas_network_is_dropped_entirely_rather_than_modelled_with_a_zero(game):
    """Gas has no head lift at all, so a zero would report every gas consumer as failed."""
    w = _straight(50.0)
    w.networks = [{"id": 0, "fluid": NITROGEN}]
    report = _verdict(w, game)
    assert report.gas_networks == 1
    assert report.networks == 0
    assert report.crests == ()
    assert report.consumers == 0


def test_a_consumer_that_reaches_no_source_is_a_connection_fault_and_stays_silent(game):
    """Rung (1) of the manual's ladder, and the model is explicitly not on it."""
    w = _World()
    burner = w.actor("Build_GeneratorCoal_C")
    stub = w.actor("Build_PipelineJunction_Cross_C")
    w.consumes("Build_GeneratorCoal_C")
    w.pipe(stub, "Connection0", burner, "FGPipeConnectionFactory", [(0.0, 0.0), (5.0, 40.0)])
    report = _verdict(w, game)
    assert report.crests == ()
    assert report.unfed == 1


# --------------------------------------------------------------- the relaxation


def test_a_second_source_reached_the_long_way_round_still_counts(game):
    """One pass over a ring answers by visit order; a relaxation answers by the network.

    The low extractor is discovered first and its 10 m stops at the 30 m saddle. The high
    extractor's 40 m has to travel the whole ring and RAISE a node already settled, which is
    the whole reason this is a fixpoint and not a walk.
    """
    w = _World()
    low = w.actor("Build_WaterPump_C")
    high = w.actor("Build_WaterPump_C")
    far = w.actor("Build_PipelineJunction_Cross_C")
    burner = w.actor("Build_GeneratorCoal_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    w.pipe(low, "FGPipeConnectionFactory", far, "Connection0", [(0.0, 0.0), (10.0, 0.0)])
    w.pipe(far, "Connection1", high, "FGPipeConnectionFactory", [(10.0, 0.0), (20.0, 30.0)])
    w.pipe(far, "Connection2", burner, "FGPipeConnectionFactory", [(10.0, 0.0), (12.0, 35.0)])
    assert _verdict(w, game).crests == ()


def test_consumers_behind_one_hill_are_named_once_under_that_hill(game):
    """A crest is where a pump would go, so the answer is one crest and not five machines."""
    w = _World()
    source = w.actor("Build_WaterPump_C")
    ridge = w.actor("Build_PipelineJunction_Cross_C")
    w.produces("Build_WaterPump_C")
    w.consumes("Build_GeneratorCoal_C")
    w.pipe(source, "FGPipeConnectionFactory", ridge, "Connection0", [(0.0, 0.0), (5.0, 30.0)])
    for role in ("Connection1", "Connection2", "Connection3"):
        burner = w.actor("Build_GeneratorCoal_C")
        w.pipe(ridge, role, burner, "FGPipeConnectionFactory", [(5.0, 30.0), (8.0, 30.0)])
    (crest,) = _verdict(w, game).crests
    assert len(crest.consumers) == 3
    assert crest.crest_m == pytest.approx(30.0)


# --------------------------------------------------------------- the reference world


def test_the_owners_base_reports_no_head_lift_problem(projection, game):
    """The acceptance test. A model that cries wolf on a working factory is wrong.

    Swept over every save on the author's machine this holds for all 93 of them, 1,448 fluid
    networks and 6,380 consumer ports -- see `docs/fluids_model.md`.
    """
    report = head_lift(projection, game, build_graph(projection))
    assert report.faults == ()
    assert report.networks == 19
    assert report.consumers == 96
    assert report.unfed == 0
    assert report.ambiguous_ports == 0
    assert report.gas_networks == 0


def test_the_fuel_lines_twenty_generators_clear_the_buffer_they_sit_on(projection, game):
    """Both gates on the owner's fuel line are now cleared, by two separate measurements.

    A 400 m3 buffer sits in series between seven Packagers and the Mk2 pump, and the pump's
    inlet stands above its surface -- a powered pump draws, so that one does not bind. A
    SECOND 400 m3 buffer sits in series at +15.351 under the generators' flat manifold, and
    it is never more than 18.75% full, so its surface never reaches its own outlet 1.75 m
    up. ``BUF_OUT`` measured a buffer delivering at its connectors at 4.3% fill, so that one
    does not bind either, and the manifold is flat AT those connectors.

    The twenty generators read 100% uptime here and in 47 other saves, and the model finally
    agrees with them. The one crest left in this world is a Packager, and it is not short of
    fuel: it is output-blocked, holding 100 Packaged Fuel on a full input box.
    """
    report = head_lift(projection, game, build_graph(projection))
    assert report.faults == ()
    assert not [c for c in report.crests if len(c.consumers) == 20]
    (rest,) = report.crests
    assert rest.fluid == "Desc_LiquidFuel_C"
    assert rest.consumers == ("Build_Packager_C_2146899090",)
    assert rest.buffer_gated
    assert report.undecided_buffers == 0


def test_the_reference_worlds_silence_is_a_verdict_and_not_an_empty_model(projection, game):
    """Cut power to every pump and more than half of the world's consumers fall over.

    Without this, ``crests == ()`` above would also pass for a model that never speaks.
    """
    graph = build_graph(projection)
    powered = {n for n in graph.cls if graph.neighbours(n, "power")}
    plumbing = H._build(projection, game, powered)
    dark = dataclasses.replace(
        plumbing, devices=[(i, o, r, c, False) for i, o, r, c, _p in plumbing.devices]
    )
    fed = H._fed(dark)
    reach, whence, gated = H._spread(dark, fed, H.MACHINE_MAX_HEAD_LIFT_M, True)
    cut = {n for n, _a in dark.sinks if n in fed} - set(reach)
    crests = H._crests(dark, reach, whence, cut, False, gated)
    assert sum(len(c.consumers) for c in crests) == 33
    assert len(crests) == 5

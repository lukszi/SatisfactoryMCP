"""factory_health: measured uptime, and why a stopped machine is stopped.

Every rule here was wrong before it was measured against the real save, so each test
pins the counter-example that corrected it.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import Counter

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.build import build_graph, class_of
from satisfactory_mcp.domain.factories.health import (
    ACTIONABLE,
    CONNECTION,
    FED,
    FLOW_RATE,
    HEAD_LIFT,
    JOINED,
    NOTHING,
    OK,
    OPEN,
    STATES,
    UNDETERMINED,
    UNFED,
    assess,
)
from satisfactory_mcp.domain.factories.model import Edge, FactoryGraph
from satisfactory_mcp.domain.world import headlift as H
from satisfactory_mcp.domain.world.headlift import Crest, HeadLift, head_lift
from satisfactory_mcp.domain.world.logistics import BY_ROLE, UNKNOWN, Link, build_physical_graph

pytestmark = pytest.mark.integration

WINDOW = 300.0


def _uptime(fraction: float) -> dict:
    return {
        "window_s": WINDOW,
        "produce_s": WINDOW * fraction,
        "cur_window_s": 100.0,
        "cur_produce_s": 0.0,
        "producing": fraction > 0,
    }


def _machine(name, recipe, *, uptime=None, buffers=None, **extra):
    record = {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [0, 0, 0],
        **extra,
    }
    if uptime is not None:
        record["uptime"] = uptime
    if buffers is not None:
        record["buffers"] = buffers
    return record


#: The one pole every `_Wires` node hangs off. Named because the stub has to answer from
#: BOTH ends -- `assess` walks outwards from the generators, so a pole that led nowhere
#: would leave every other machine on the same circuit unreachable and so dark.
_POLE = "a pole"


class _Wires:
    """A ``FactoryGraph`` reduced to the one question `assess` asks it: one shared circuit."""

    def __init__(self, wired):
        self.wired = set(wired)

    def neighbours(self, node, layer="material"):
        assert layer == "power", "health asks about electricity and nothing else"
        if node == _POLE:
            return sorted(self.wired)
        return [_POLE] if node in self.wired else []


def _assess(game, machines=(), extractors=(), generators=(), graph=None, physical=None, heads=None):
    projection = {
        "machines": list(machines),
        "extractors": list(extractors),
        "generators": list(generators),
    }
    names = [r["instance"].rsplit(".", 1)[-1] for r in (*machines, *extractors, *generators)]
    return assess("test", names, game, projection, graph, physical, heads)


def _state_of(report, instance):
    return next(m.state for m in report.machines if m.instance == instance)


def test_starvation_is_a_missing_ingredient_not_an_empty_input(game):
    """THE correction. Black Powder takes Coal and Sulfur; the assembler that motivated
    this held 100 Sulfur and no Coal. An "is the input empty" test called it well-fed and
    left eight machines filed as unexplained stalls."""
    # The recipe CLASS is Gunpowder; only its display name is Black Powder.
    name = "Build_AssemblerMk1_C_1"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_Gunpowder_C",
                uptime=_uptime(0.0),
                buffers={
                    "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
                    "out": {"items": {}, "slots": 2},
                },
            )
        ],
    )
    assert _state_of(report, name) == "starved"
    entry = report.machines[0]
    assert entry.cause == ("Coal",), "the missing ingredient is the whole point"
    assert "Sulfur" not in entry.cause


def test_blocked_is_checked_before_starved(game):
    """A blocked machine's input backs up too. The sample reads input 100/100 Iron Ingot
    and output 199/200 Iron Plate -- testing the input first calls it well-fed and misses
    that nothing is taking its plates."""
    name = "Build_ConstructorMk1_C_2"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IronPlate_C",
                uptime=_uptime(0.0),
                buffers={
                    "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
                },
            )
        ],
    )
    assert _state_of(report, name) == "blocked"
    assert report.blocked_on["Iron Plate"] == 1


def test_a_nearly_full_stack_counts_as_backed_up(game):
    """199 of 200 is backed up. Demanding exactly 100% hides a bottleneck that has just
    ticked one item forward."""
    full = _machine(
        "Build_ConstructorMk1_C_3",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
        },
    )
    half = _machine(
        "Build_ConstructorMk1_C_4",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 100}, "slots": 2},
        },
    )
    report = _assess(game, machines=[full, half])
    assert _state_of(report, "Build_ConstructorMk1_C_3") == "blocked"
    assert _state_of(report, "Build_ConstructorMk1_C_4") == "stalled"


def test_an_extractor_with_no_node_is_a_dead_node_not_starved(game):
    """A miner draws from its node and has no InputInventory, so an empty-input test
    reported every idle miner as starved. When mExtractableResource is ABSENT the miner
    is bound to nothing at all -- on the real save, three left behind when a game update
    removed their resource node."""
    name = "Build_MinerMk1_C_5"
    report = _assess(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_MinerMk1_C",
                "pos": [0, 0, 0],
                "uptime": _uptime(0.0),
            }
        ],
    )
    assert _state_of(report, name) == "dead node"
    assert report.machines[0].cause == ("no resource node",)


def test_an_extractor_with_an_untabled_node_is_not_dead(game):
    """A water pump's node IS set; it points at an FGWaterVolume that is not a
    purity-table key. That is a gap in our table, not a fault in the factory."""
    name = "Build_WaterPump_C_6"
    report = _assess(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_WaterPump_C",
                "pos": [0, 0, 0],
                "node": "L:P.FGWaterVolume42",
                "uptime": _uptime(1.0),
            }
        ],
    )
    assert _state_of(report, name) == "saturated"


def test_missing_produce_duration_is_a_real_zero(game):
    """UE omits SaveGame properties equal to their default, so an absent ProduceDuration
    means the machine produced for zero seconds -- not that the data is missing."""
    name = "Build_SmelterMk1_C_7"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IngotIron_C",
                uptime={"window_s": WINDOW, "cur_window_s": 10.0, "producing": False},
                buffers={
                    "in": {"items": {"Desc_OreIron_C": 50}, "slots": 1},
                    "out": {"items": {}, "slots": 1},
                },
            )
        ],
    )
    assert report.machines[0].uptime == 0.0
    assert _state_of(report, name) == "stalled"


def test_a_machine_with_no_monitor_is_unknown_not_zero(game):
    name = "Build_ConstructorMk1_C_8"
    report = _assess(game, machines=[_machine(name, "Recipe_IronPlate_C")])
    assert _state_of(report, name) == "unmonitored"
    assert report.machines[0].uptime is None
    assert report.mean_uptime is None, "an unknown must not be averaged in as a zero"


def test_paused_outranks_every_other_diagnosis(game):
    name = "Build_ConstructorMk1_C_9"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IronPlate_C",
                uptime=_uptime(0.0),
                paused=True,
                buffers={
                    "in": {"items": {}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 200}, "slots": 1},
                },
            )
        ],
    )
    assert _state_of(report, name) == "paused"


def test_states_are_ordered_worst_first_and_ok_is_a_subset(game):
    assert OK <= set(STATES)
    assert STATES.index("blocked") < STATES.index("saturated")
    assert STATES.index("dead node") < STATES.index("starved")


def test_a_blocked_machine_needs_action_and_a_paused_one_does_not():
    """Lukas, 2026-09-26: a full output box is a problem, not a factory at rest."""
    assert "blocked" in ACTIONABLE
    assert not OK & set(ACTIONABLE)
    assert "paused" not in ACTIONABLE and "intermittent" not in ACTIONABLE
    assert list(ACTIONABLE) == [s for s in STATES if s in ACTIONABLE], "report order"


def test_worst_reports_only_what_needs_attention(game):
    good = _machine("Build_ConstructorMk1_C_10", "Recipe_IronPlate_C", uptime=_uptime(1.0))
    bad = _machine(
        "Build_ConstructorMk1_C_11",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = _assess(game, machines=[good, bad])
    assert [m.instance for m in report.worst()] == ["Build_ConstructorMk1_C_11"]
    assert report.mean_uptime == pytest.approx(0.5)


# ------------------------------------------------------------ power shards


def test_slug_yields_come_from_the_recipes_not_from_memory(game):
    """Blue 1, Yellow 2, Purple 5 are read off Power Shard (1)/(2)/(5). Hardcoding them
    would be exactly the game-knowledge guessing this project refuses elsewhere."""
    yields = {game.item_name(k): v for k, v in game.slug_yields().items()}
    assert yields == {"Blue Power Slug": 1.0, "Yellow Power Slug": 2.0, "Purple Power Slug": 5.0}


def test_the_synthetic_shard_recipe_is_not_treated_as_a_slug(game):
    """Synthetic Power Shard also makes shards, but from Time Crystal, Dark Matter
    Crystal, Quartz and Photonic Matter. That is a production chain, not something
    lying in a crate, so it must not inflate the craftable pool."""
    names = {game.item_name(k) for k in game.slug_yields()}
    assert "Quartz Crystal" not in names
    assert "Time Crystal" not in names
    assert all("Slug" in n for n in names)


def _budget(game, projection):
    from satisfactory_mcp.domain.world.state import WorldState

    return WorldState(projection=projection, game=game).shard_budget()


def _proj(depot=None, player=None, storage=None, machines=()):
    return {
        "depot": depot or {},
        "inventories": {"player": player or {}, "storage": storage or {}, "machine": {}},
        "machines": list(machines),
        "extractors": [],
        "generators": [],
    }


def test_slugs_in_the_depot_count_as_craftable_not_free(game):
    """The reference save holds 93 Blue, 58 Yellow and 39 Purple in the Dimensional
    Depot -- 404 shards against 22 already crafted. Reporting only the crafted pool
    understated what the player could overclock with by ~19x."""
    budget = _budget(
        game,
        _proj(
            depot={
                "Desc_Crystal_C": 93,
                "Desc_Crystal_mk2_C": 58,
                "Desc_Crystal_mk3_C": 39,
                "Desc_CrystalShard_C": 22,
            }
        ),
    )
    assert budget["free"] == 22
    assert budget["craftable"] == 404
    assert budget["potential"] == 426


def test_slugs_are_found_wherever_stock_looks(game):
    """Carried, in a storage container, or in the Depot -- all three are spendable, so
    all three count."""
    for place in ("player", "storage"):
        budget = _budget(game, _proj(**{place: {"Desc_Crystal_mk3_C": 4}}))
        assert budget["craftable"] == 20, place
        assert budget["by_place"], place


def test_shards_inside_machines_are_never_counted_as_free(game):
    """The trap this whole area exists to avoid: the 97 shards on the reference save are
    all in InventoryPotential components, so reading the machine bucket as stock
    overstates the free pool more than 4x."""
    projection = _proj()
    projection["inventories"]["machine"] = {"Desc_CrystalShard_C": 97}
    budget = _budget(game, projection)
    assert budget["free"] == 0
    assert budget["craftable"] == 0


def test_craftable_is_reported_apart_from_free(game):
    """Crafting is a manual step, so slugs are potential and must never be folded into
    a number the player reads as available now."""
    budget = _budget(game, _proj(depot={"Desc_Crystal_C": 10}))
    assert budget["free"] == 0
    assert budget["craftable"] == 10
    assert budget["potential"] == 10


# --------------------------------------------------------------- machines wired to nothing
#
# The save records no mHasPower and no mCircuitID -- 0 of 44,634 objects -- so "unpowered"
# is not knowable and is never claimed. A WIRE is: an actor either has a power edge or it
# does not, which makes "nothing at all reaches this machine" the one positive electrical
# fact in the file. `stalled` used to advise checking power because nothing here could.


def _fed(name, uptime=0.0):
    """A machine with input, room in its output, and not producing -- a bare stall."""
    return _machine(
        name,
        "Recipe_IronPlate_C",
        uptime=_uptime(uptime),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 1}, "slots": 2},
        },
    )


def test_a_stalled_machine_no_wire_reaches_says_so(game):
    dark = _fed("Build_ConstructorMk1_C_90")
    lit = _fed("Build_ConstructorMk1_C_91")
    report = _assess(game, machines=[dark, lit], graph=_Wires({"Build_ConstructorMk1_C_91"}))
    assert _state_of(report, "Build_ConstructorMk1_C_90") == "stalled"
    assert _state_of(report, "Build_ConstructorMk1_C_91") == "stalled"
    causes = {m.instance: m.cause for m in report.machines}
    assert causes["Build_ConstructorMk1_C_90"] == ("no power connection",)
    assert causes["Build_ConstructorMk1_C_91"] == (), "a wired stall has no such evidence"


def test_without_a_graph_nothing_is_called_unwired(game):
    """Absent evidence must not read as a finding: every caller that passes no graph --
    the whole of this module before this section -- would otherwise report every machine."""
    report = _assess(game, machines=[_fed("Build_ConstructorMk1_C_92")])
    assert report.unwired == []
    assert _state_of(report, "Build_ConstructorMk1_C_92") == "stalled"
    assert report.machines[0].cause == ()


def test_being_wired_to_nothing_is_reported_whatever_the_state_is(game):
    """It is not a state, and that is the design: on the reference world all eight machines
    wired to nothing are `no recipe`, `paused` or `unmonitored`, and NONE is stalled."""
    idle = _machine("Build_ConstructorMk1_C_93", "", uptime=_uptime(0.0))
    paused = _machine("Build_ConstructorMk1_C_94", "Recipe_IronPlate_C", paused=True)
    everything = {"Build_ConstructorMk1_C_93", "Build_ConstructorMk1_C_94"}
    report = _assess(game, machines=[idle, paused], graph=_Wires(set()))
    assert set(report.unwired) == everything
    assert _state_of(report, "Build_ConstructorMk1_C_93") == "no recipe"
    assert _state_of(report, "Build_ConstructorMk1_C_94") == "paused"


def test_a_fully_wired_factory_reports_none(game):
    report = _assess(
        game,
        machines=[_fed("Build_ConstructorMk1_C_95")],
        graph=_Wires({"Build_ConstructorMk1_C_95"}),
    )
    assert report.unwired == []


# ---- and the wire that leads to no generator ------------------------------
#
# A wire is a wire whether or not a source hangs off it. Ten Oil Refineries in two rows of
# five, on HL_BUFFER_A through D, are wired to each other and to one pole and to nothing
# else -- and the current save has none, so the answer there is "every wired machine is on
# a circuit a generator stands on", which is the sharper sentence. Measured: 34 of the 98
# saves on this machine carry at least one, across three worlds; save-projection.md §6.1a.


def _grid(*wires):
    """A real ``FactoryGraph`` carrying the given power edges and nothing else."""
    graph = FactoryGraph(cls={})
    for a, b in wires:
        graph.cls[a], graph.cls[b] = class_of(a), class_of(b)
        graph.power.append(Edge(a=a, b=b))
    return graph


def _burner(name):
    """A generator record, which is what makes a circuit a source of power."""
    return _machine(name, "", buffers={"fuel": {"items": {"Desc_Leaves_C": 20}, "slots": 1}})


def test_a_stall_on_a_circuit_no_generator_stands_on_says_which(game):
    """The finding the degree-zero check cannot make: the wire is built, the source is not."""
    lit = _fed("Build_ConstructorMk1_C_96")
    stranded = _fed("Build_ConstructorMk1_C_97")
    graph = _grid(
        ("Build_GeneratorBiomass_C_98", "Build_PowerPoleMk1_C_1"),
        ("Build_PowerPoleMk1_C_1", "Build_ConstructorMk1_C_96"),
        ("Build_PowerPoleMk1_C_2", "Build_ConstructorMk1_C_97"),
    )
    report = _assess(
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
    report = _assess(
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
    report = _assess(game, machines=[_fed("Build_ConstructorMk1_C_102")], graph=graph)
    assert report.no_generator == []
    assert report.unwired == []
    assert report.machines[0].cause == (), "the stall keeps its honest silence"


def test_without_a_graph_no_circuit_is_called_generator_less(game):
    """`unwired`'s rule, applied to the wider claim: no graph, no finding."""
    report = _assess(
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
    report = _assess(
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
    idle = _machine("Build_OilRefinery_C_105", "Recipe_Alternate_HeavyOilResidue_C")
    graph = _grid(
        ("Build_GeneratorBiomass_C_98", "Build_PowerPoleMk1_C_1"),
        ("Build_PowerPoleMk1_C_2", "Build_OilRefinery_C_105"),
    )
    report = _assess(
        game,
        machines=[idle],
        generators=[_burner("Build_GeneratorBiomass_C_98")],
        graph=graph,
    )
    assert _state_of(report, "Build_OilRefinery_C_105") == "unmonitored"
    assert report.no_generator == ["Build_OilRefinery_C_105"]


def _rewired(projection):
    """The reference world with one wired machine moved onto a pole of its own.

    A perturbation rather than a hand-built world, for the reason the fluid ladder's own
    fixtures give: the rendering under test reaches for the graph, the labels and the
    census, and a stub projection answers none of them. The fixture itself has no machine
    on a generator-less circuit -- asserted in ``test_reference_counts`` -- so the case
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


# ---- what feeds the input a starved machine lacks -------------------------


def _starved(name):
    """An assembler on Black Powder holding Sulfur and no Coal."""
    return _machine(
        name,
        "Recipe_Gunpowder_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
            "out": {"items": {}, "slots": 2},
        },
    )


class _Runs:
    """A ``PhysicalGraph`` reduced to the one question `assess` asks it."""

    def __init__(self, arriving):
        self.arriving = list(arriving)

    def feeds(self, actor):
        return list(self.arriving)


def _link(source, target, medium=ports.CONVEYOR, basis=BY_ROLE, pieces=1):
    return Link(source=source, target=target, medium=medium, basis=basis, pieces=pieces)


def test_a_starved_machine_names_what_feeds_the_input_it_lacks(game):
    name = "Build_AssemblerMk1_C_100"
    feeder = _machine(
        "Build_ConstructorMk1_C_101",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = _assess(
        game,
        machines=[_starved(name), feeder],
        physical=_Runs([_link("Build_ConstructorMk1_C_101", name, pieces=7)]),
    )
    (feed,) = next(m for m in report.machines if m.instance == name).feeds
    assert (feed.item, feed.verdict) == ("Coal", FED)
    assert (feed.far, feed.pieces) == ("Build_ConstructorMk1_C_101", 7)
    assert feed.far_state == "starved", "the feeder's own health is the next thing to check"


def test_nothing_feeding_it_and_an_unjoined_run_are_different_findings(game):
    """The distinction the physical graph exists to keep. 24 runs on the reference world end
    at nothing, and reporting one of those as "no conduit feeds this" is a confident wrong
    claim rather than a finding."""
    empty = _assess(game, machines=[_starved("Build_AssemblerMk1_C_102")], physical=_Runs([]))
    (missing,) = empty.machines[0].feeds
    assert (missing.verdict, missing.far) == (NOTHING, "")

    torn = _assess(
        game,
        machines=[_starved("Build_AssemblerMk1_C_103")],
        physical=_Runs([_link(None, "Build_AssemblerMk1_C_103")]),
    )
    (open_end,) = torn.machines[0].feeds
    assert open_end.verdict == OPEN
    assert open_end.pieces == 1, "the run is real and measured; only its far end is unknown"


def test_a_fluid_ingredient_looks_at_pipes_and_a_solid_at_belts(game):
    """A run carries whatever is put on it, so the medium is the only separation the save
    supports -- a conveyor arriving cannot be delivering the missing Water."""
    name = "Build_OilRefinery_C_104"
    wet = _machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )
    report = _assess(
        game, machines=[wet], physical=_Runs([_link("Build_StorageContainerMk1_C_1", name)])
    )
    by_item = {f.item: f.verdict for f in report.machines[0].feeds}
    assert by_item["Water"] == NOTHING, "a conveyor cannot be delivering the water"
    assert by_item["Polymer Resin"] == FED


def test_an_undirected_run_is_not_reported_as_feeding(game):
    """A pipe between two fittings has no direction without the rates, so it JOINS the
    machine to the far end and the report must not claim which way it flows."""
    name = "Build_AssemblerMk1_C_105"
    report = _assess(
        game,
        machines=[_starved(name)],
        physical=_Runs([_link(name, "Build_PipelineJunction_C_9", basis=UNKNOWN)]),
    )
    (feed,) = report.machines[0].feeds
    assert feed.verdict == JOINED
    assert feed.far == "Build_PipelineJunction_C_9", "walked from the end that is not us"


def test_without_a_physical_graph_nothing_is_called_unfed(game):
    """Absent evidence must not read as a finding, on `unwired`'s terms."""
    report = _assess(game, machines=[_starved("Build_AssemblerMk1_C_106")])
    assert report.machines[0].state == "starved"
    assert report.machines[0].feeds == ()


def test_a_feeder_that_provably_makes_the_item_is_marked(game):
    """Which of two arriving belts brings the ingots is not in the save; which far end can
    produce them at all is."""
    name = "Build_ConstructorMk1_C_107"
    hungry = _machine(
        name,
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 2}},
    )
    smelter = _machine(
        "Build_SmelterMk1_C_108",
        "Recipe_IngotIron_C",
        uptime=_uptime(1.0),
        buffers={"in": {"items": {"Desc_OreIron_C": 50}, "slots": 1}, "out": {"items": {}}},
    )
    report = _assess(
        game,
        machines=[hungry, smelter],
        physical=_Runs(
            [_link("Build_SmelterMk1_C_108", name), _link("Build_StorageContainerMk1_C_2", name)]
        ),
    )
    marked = {f.far: f.makes for f in report.machines[0].feeds}
    assert marked == {"Build_SmelterMk1_C_108": True, "Build_StorageContainerMk1_C_2": False}, (
        "a container is not established as making anything, and false must not read as 'does not'"
    )


# ------------------------------------------------- the plumbing manual's ladder, §24.5


def _heads(crests=(), unfed=()):
    return HeadLift(
        crests=tuple(crests),
        consumers=0,
        unfed_ports=tuple(unfed),
        networks=0,
        gas_networks=0,
        ambiguous_ports=0,
    )


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
    return _machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )


def _rungs(report, instance):
    found = next(m for m in report.machines if m.instance == instance)
    return {f.item: f.rung for f in found.feeds}


def test_a_fluid_no_pipe_reaches_stops_at_connection(game):
    name = "Build_OilRefinery_C_200"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_StorageContainerMk1_C_1", name)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_pipe_whose_far_end_is_nothing_stops_at_connection(game):
    """A torn line is rung (1) even though a pipe of the right medium does arrive."""
    name = "Build_OilRefinery_C_201"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link(None, name, medium=ports.PIPE)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_network_no_source_reaches_is_connection_and_not_head_lift(game):
    """The rung (1) fact only the head-lift model can see: the run arrives from a real
    fitting, so the conduit graph is satisfied, and yet nothing anywhere puts fluid in it.

    This is the ten refineries of the owner's newest saves, and calling them a head-lift
    fault would be ten wrong answers to one unfinished pipe.
    """
    name = "Build_OilRefinery_C_202"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(unfed=[name]),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_fed_fluid_behind_a_crest_stops_at_head_lift(game):
    name = "Build_OilRefinery_C_203"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name)["Water"] == HEAD_LIFT


def test_a_crest_on_another_fluid_leaves_this_input_alone(game):
    """A machine takes several fluids; a crest names the one whose network it stands on."""
    name = "Build_OilRefinery_C_204"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name], fluid="Desc_LiquidOil_C")]),
    )
    assert _rungs(report, name)["Water"] == FLOW_RATE


def test_connection_wins_over_a_crest_behind_the_same_machine(game):
    """THE rule of the ladder. A machine can be both unplumbed and, on the model's reading,
    below a hill; the manual says answer the first rung and stop, because a pump is the wrong
    thing to build when no pipe arrives at all.
    """
    name = "Build_OilRefinery_C_205"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_connected_fluid_the_model_clears_reaches_flow_rate(game):
    name = "Build_OilRefinery_C_206"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == FLOW_RATE


def test_without_the_head_lift_model_nothing_is_called_a_flow_rate_problem(game):
    """Rung (3) is EARNED by ruling rung (2) out, and the manual's red box says attempting
    it first is the usual mistake. No model, no verdict -- on `unwired`'s terms."""
    name = "Build_OilRefinery_C_207"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
    )
    assert _rungs(report, name)["Water"] == UNDETERMINED


def test_a_solid_never_carries_a_rung_and_a_mixed_machine_reads_sensibly(game):
    """Head lift is not a thing that happens to Polymer Resin."""
    name = "Build_OilRefinery_C_208"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name) == {"Polymer Resin": UNDETERMINED, "Water": HEAD_LIFT}
    (machine,) = report.machines
    assert machine.cause == ("Polymer Resin", "Water (head lift)")


def test_one_ingredient_reached_by_several_runs_gets_one_rung(game):
    """The rung belongs to the ingredient, not to a run: two pipes into one input is one
    diagnosis, and rows disagreeing about it would be two."""
    name = "Build_OilRefinery_C_209"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs(
            [
                _link("Build_PipelineJunction_C_9", name, medium=ports.PIPE),
                _link(None, name, medium=ports.PIPE),
            ]
        ),
        heads=_heads(crests=[_crest([name])]),
    )
    water = [f for f in report.machines[0].feeds if f.item == "Water"]
    assert len(water) == 2
    assert {f.rung for f in water} == {HEAD_LIFT}


# ------------------------------------------- a machine that has never produced at all
#
# `Starved.sav` holds the case these were written from: an Oil Refinery on Alternate: Heavy
# Oil Residue, input pipe connector with no mConnectedComponent at all, both buffers empty,
# wired and idle at 0.1 MW. It carries NO mLastProductivityMeasurementDuration, and that is
# permanent rather than a window yet to close -- still absent 19 minutes and four
# window-lengths after the machine was built. 48 of that save's 584 machine-like records are
# in the same position and 19 of them have a recipe set, so the buffers alone cannot decide
# this: eight of those nineteen are a fresh row of Constructors with belts already run.


#: The two neighbours every case below is built beside, and they are load-bearing rather
#: than scenery: `assess` reads "no run of this medium arrives" as a fact only where the
#: conduit graph resolves that medium somewhere, so one belt AND one pipe have to resolve
#: for the refinery's dangling pipe to mean anything. `Starved.sav` resolves 2,275 runs.
ON_A_BELT = "Build_ConstructorMk1_C_2147441491"
ON_A_PIPE = "Build_OilRefinery_C_2146795155"


class _PerActor:
    """A ``PhysicalGraph`` that answers per actor, unlike `_Runs`."""

    def __init__(self, arriving):
        self.arriving = dict(arriving)

    def feeds(self, actor):
        return list(self.arriving.get(actor, ()))


def _never_run(name, recipe="Recipe_Alternate_HeavyOilResidue_C", held=None, out=None):
    """The refinery's shape: a recipe, empty buffers, and no uptime key whatsoever."""
    return _machine(
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
            ON_A_BELT: [_link("Build_ConveyorAttachmentSplitter_C_2147444586", ON_A_BELT)],
            ON_A_PIPE: [_link("Build_Pipeline_C_2146795200", ON_A_PIPE, medium=ports.PIPE)],
            **arriving,
        }
    )


def _beside(game, record, arriving=(), **kw):
    """Assess one never-run machine in a world whose belts AND pipes demonstrably resolve."""
    name = record["instance"].rsplit(".", 1)[-1]
    return _assess(
        game,
        machines=[record, *_neighbours(exclude=name)],
        graph=_Wires({name, ON_A_BELT, ON_A_PIPE}),
        physical=_world(**{name: list(arriving)}),
        **kw,
    )


def test_a_never_run_machine_no_pipe_reaches_is_starved_on_the_connection_rung(game):
    """THE case. `uptime is None` used to answer before the buffers were read, so this
    machine was `unmonitored`, never entered `needs_attention`, and the fluid ladder --
    gated on `state == "starved"` -- was never called on it at all."""
    name = "Build_OilRefinery_C_2146786838"
    report = _beside(game, _never_run(name), heads=_heads())
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.uptime is None, "no window, and it is not being invented"
    assert machine.state == "starved"
    assert machine.needs_attention
    assert machine.cause == ("Crude Oil (connection)",)
    assert _rungs(report, name) == {"Crude Oil": CONNECTION}
    assert [m.instance for m in report.worst()] == [name]


def test_a_closed_window_on_the_same_record_still_gives_the_old_answer(game):
    """The counterfactual that proved this was only a gate: injecting a closed window into
    that refinery's record and running the UNCHANGED code already gave this."""
    name = "Build_OilRefinery_C_2146786838"
    record = _never_run(name)
    record["uptime"] = {"window_s": 300.0, "produce_s": 0.0}
    report = _beside(game, record, heads=_heads())
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
        arriving=[_link("Build_ConveyorAttachmentSplitter_C_2146887273", name)],
        heads=_heads(),
    )
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.state == "unmonitored"
    assert not machine.needs_attention
    assert report.worst() == []


def test_a_medium_the_conduit_graph_never_resolves_promotes_nothing(game):
    """THE correction to this rule, and it is per MEDIUM rather than per world. Save versions
    25 to 36 on the author's machine resolve 88 pipe runs between coal generators and NOT ONE
    conveyor run, in worlds of 6,266 material couplings. Reading that as "no belt feeds this"
    turned 39 smelters and constructors in a single save into findings, every one of them the
    projection's blind spot rather than the world's -- and a world-wide "does anything
    resolve" test passes there, because the pipes do."""
    name = "Build_SmelterMk1_C_310"
    pipes_only = _PerActor(
        {ON_A_PIPE: [_link("Build_Pipeline_C_2146795200", ON_A_PIPE, medium=ports.PIPE)]}
    )
    report = _assess(
        game,
        machines=[_never_run(name, "Recipe_IngotIron_C"), _never_run(ON_A_PIPE)],
        graph=_Wires({name, ON_A_PIPE}),
        physical=pipes_only,
        heads=_heads(),
    )
    assert _state_of(report, name) == "unmonitored"


def test_a_never_run_machine_whose_run_reaches_nothing_stays_quiet(game):
    """OPEN is not NOTHING, and this module's own vocabulary says so: a run does arrive and
    the save joins its far end to no actor, which is a feeder unknown rather than a feeder
    absent. Ten FICSMAS Constructors on one save are exactly this and must stay quiet."""
    name = "Build_ConstructorMk1_C_2145270171"
    report = _beside(
        game,
        _never_run(name, "Recipe_CandyCane_C"),
        arriving=[_link(None, name)],
        heads=_heads(),
    )
    assert _state_of(report, name) == "unmonitored"


def test_a_never_run_machine_holding_its_ingredients_stays_quiet(game):
    """Nothing is missing, so there is nothing an absent window is hiding."""
    report = _beside(
        game, _never_run(ON_A_BELT, "Recipe_IronRod_C", held={"Desc_IronIngot_C": 100})
    )
    assert _state_of(report, ON_A_BELT) == "unmonitored"


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
    record = _machine(name, "", buffers={"fuel": {"items": {}, "slots": 1}})
    report = _assess(
        game,
        generators=[record],
        machines=_neighbours(),
        graph=_Wires({name, ON_A_BELT, ON_A_PIPE}),
        physical=_world(),
    )
    assert next(m for m in report.machines if m.instance == name).state == "unmonitored"


def test_without_a_physical_graph_a_never_run_machine_is_never_promoted(game):
    """Absent evidence must not read as a finding, on `unwired`'s terms -- and /api/machines
    supplies no conduit graph, so this is the map's answer for these machines."""
    name = "Build_OilRefinery_C_302"
    report = _assess(game, machines=[_never_run(name)], graph=_Wires({name}))
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


# ----------------------------------- rung (1)'s second form: a network no source reaches
#
# Ten Oil Refineries on Alternate: Heavy Oil Residue, in two rows of five at +13 m, are the
# whole population of this rule on the author's machine: the same ten actors in 8 of 98
# saves, and no other unfed port in any of the other 90. Every one is wired, holds an empty
# input AND an empty output, keeps no window, and has a pipe arriving from a real T junction
# -- so the conduit graph is satisfied and only the head-lift model can see that nothing
# anywhere puts crude into that network.


def test_a_never_run_machine_on_a_network_no_source_reaches_is_starved(game):
    """The pipe is REAL, so `_cut_off`'s conduit form declines it and the head-lift form
    catches it. The cause has to say which fluid and that the network has no source, because
    that is the actionable difference: the fix is a source, not a pump and not a pipe."""
    name = "Build_OilRefinery_C_2145161411"
    report = _beside(
        game,
        _never_run(name),
        arriving=[_link("Build_PipelineJunction_T_C_2144913486", name, medium=ports.PIPE)],
        heads=_heads(unfed=[name]),
    )
    machine = next(m for m in report.machines if m.instance == name)
    assert machine.uptime is None
    assert machine.state == "starved"
    assert machine.cause == ("Crude Oil (connection: no source on its network)",)
    assert [f.verdict for f in machine.feeds] == [UNFED]
    # Still rung (1), so the ladder's own counts do not gain a fourth rung.
    assert _rungs(report, name) == {"Crude Oil": CONNECTION}


def test_a_never_run_machine_whose_network_has_a_source_stays_quiet(game):
    """The case that must NOT promote, and it is the same record: a pipe arrives from a real
    fitting and a source does reach it, so an empty buffer on a machine that has never run is
    a build mid-commissioning rather than a fault."""
    name = "Build_OilRefinery_C_2145162120"
    report = _beside(
        game,
        _never_run(name),
        arriving=[_link("Build_PipelineJunction_T_C_2144913767", name, medium=ports.PIPE)],
        heads=_heads(),
    )
    assert _state_of(report, name) == "unmonitored"


def test_an_unfed_port_does_not_promote_a_missing_SOLID(game):
    """``unfed_ports`` is a fact about pipes. A machine can stand on a dead fluid network and
    lack a solid, and the belt bringing that solid is the head-lift model's blind spot."""
    name = "Build_ConstructorMk1_C_2146956309"
    report = _beside(
        game,
        _never_run(name, "Recipe_IronPlate_C"),
        arriving=[_link("Build_ConveyorAttachmentSplitter_C_2146887273", name)],
        heads=_heads(unfed=[name]),
    )
    assert _state_of(report, name) == "unmonitored"


def test_without_a_head_lift_model_the_unfed_form_promotes_nothing(game):
    """Absent evidence is not a finding: `assess` takes ``heads`` optionally and /api/machines
    supplies none, so the promotion must need the model rather than merely tolerate it."""
    name = "Build_OilRefinery_C_2145162762"
    report = _beside(
        game,
        _never_run(name),
        arriving=[_link("Build_PipelineJunction_T_C_2144914070", name, medium=ports.PIPE)],
    )
    assert _state_of(report, name) == "unmonitored"


def test_an_unfed_machine_holding_its_fluid_stays_quiet(game):
    """A dead network is not a verdict on a machine that has what it needs -- which is what
    keeps this rule off a Refinery that is merely paused between runs."""
    name = "Build_OilRefinery_C_2145163705"
    report = _beside(
        game,
        _never_run(name, held={"Desc_LiquidOil_C": 300}),
        arriving=[_link("Build_PipelineJunction_T_C_2144918347", name, medium=ports.PIPE)],
        heads=_heads(unfed=[name]),
    )
    assert _state_of(report, name) == "unmonitored"


def test_the_reference_world_puts_no_fluid_on_the_ladder_at_all(projection, game):
    """The calibration record, and it is a fact about the world rather than about the code.

    Across every save on the author's machine not one starved machine is short of a FLUID --
    a machine's fluid box is carried in its input inventory, so the ingredient is readable,
    and none of the 802 starved machines there is missing one. The ladder is correct and
    silent here, and this test fails the day that stops being true.
    """
    names = [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in projection.get(key, ())
    ]
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
    names = [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in projection.get(key, ())
    ]
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
            uptime=_uptime(0.0),
        )
        if record.get("fuel")
        else record
        for record in projection["generators"]
    ]
    patched = dict(projection, generators=thirsty)
    names = [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in patched.get(key, ())
    ]
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

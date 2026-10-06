"""factory_query: what a machine set makes, needs, draws and touches.

The balance table is the part that earns the tool. A per-machine listing tells you a
Foundry runs Solid Steel Ingot; only production minus consumption across the whole set
tells you the steel factory needs 975 Coal/min fed in and hands you 405 Steel Ingot/min.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.query import ASPECTS, build_view

pytestmark = pytest.mark.integration

SMELTER = "Build_SmelterMk1_C_1"
ROD_A = "Build_ConstructorMk1_C_2"
ROD_B = "Build_ConstructorMk1_C_4"
OUTSIDER = "Build_ConstructorMk1_C_3"
BELT = "Build_ConveyorBeltMk1_C_50"
INSIDE = (SMELTER, ROD_A, ROD_B)


def _machine(name, recipe, x, **extra):
    return {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [x, 0, 0],
        **extra,
    }


def _projection(clock: float = 1.0, paused: bool = False) -> dict:
    """One smelter feeding TWO rod constructors, plus a machine belted on from outside.

    Deliberately balanced: 30 Iron Ingot/min made, 2 x 15 consumed. That is what makes
    Iron Ingot an *internal* item rather than a surplus, which is the case worth testing.
    """
    actors = [SMELTER, ROD_A, ROD_B, OUTSIDER, BELT]
    index = {a: i for i, a in enumerate(actors)}
    return {
        "machines": [
            _machine(SMELTER, "Recipe_IngotIron_C", 0),
            _machine(ROD_A, "Recipe_IronRod_C", 1000, clock=clock, paused=paused),
            _machine(ROD_B, "Recipe_IronRod_C", 2000),
            _machine(OUTSIDER, "Recipe_IronPlate_C", 9000),
        ],
        "extractors": [],
        "generators": [],
        "graph": {
            "actors": actors,
            "roles": [],
            # The outsider is reached only THROUGH a belt, which is the normal shape.
            "material": [
                [index[SMELTER], index[ROD_A], 0, 0],
                [index[SMELTER], index[ROD_B], 0, 0],
                [index[ROD_A], index[BELT], 0, 0],
                [index[BELT], index[OUTSIDER], 0, 0],
            ],
            "power": [],
        },
    }


def _view(game, projection, machines=INSIDE, name="test"):
    return build_view(name, list(machines), build_graph(projection), game, projection)


def test_balance_splits_surplus_from_deficit_from_internal(game):
    """The three signs are the whole point. Iron Ore is consumed and never made here, the
    rods leave, and the ingots are made and eaten inside the set."""
    view = _view(game, _projection())
    assert dict(view.outputs())["Iron Rod"] == pytest.approx(30.0)
    assert dict(view.inputs())["Iron Ore"] == pytest.approx(30.0)
    internal = dict(view.internal())
    assert "Iron Ingot" in internal, "made and consumed in equal measure"
    assert view.net("Iron Ingot") == pytest.approx(0.0)


def test_rates_scale_with_the_saved_clock(game):
    """Nameplate AT THE SAVED CLOCK. A machine left at 150% is not producing its 100%
    rate, and reporting the recipe rate unscaled would understate the whole line."""
    base = _view(game, _projection(clock=1.0))
    fast = _view(game, _projection(clock=1.5))
    # Only ROD_A is overclocked, so the set gains half of one constructor's 15/min --
    # the clock is applied per machine, not to the factory total.
    assert dict(base.outputs())["Iron Rod"] == pytest.approx(30.0)
    assert dict(fast.outputs())["Iron Rod"] == pytest.approx(37.5)
    assert fast.draw_mw > base.draw_mw


def test_a_paused_machine_contributes_no_flow_but_is_reported(game):
    """Counting a paused machine's output would invent throughput that does not exist."""
    view = _view(game, _projection(paused=True))
    assert any("paused" in issue for issue in view.issues)
    # It is still a member: it exists, it just is not running.
    assert view.size == 3
    # The other constructor keeps running, so rods do not vanish entirely.
    assert dict(view.outputs())["Iron Rod"] == pytest.approx(15.0)


def test_the_boundary_walk_passes_through_belts(game):
    """A material edge runs machine -> belt -> machine, so a walk that stops at the first
    non-machine finds no links at all and every factory looks isolated."""
    view = _view(game, _projection())
    assert sum(view.links.values()) == 1, view.links
    assert "(unlabelled)" in view.links


def test_machines_outside_the_set_do_not_leak_into_the_balance(game):
    view = _view(game, _projection())
    assert not any(k == "Iron Plate" for k, _ in view.outputs())
    assert OUTSIDER not in {m.instance for m in view.machines}


def test_power_draw_accumulates_over_the_set(game):
    view = _view(game, _projection())
    assert view.draw_mw > 0
    assert view.generation_mw == 0.0


def test_measured_draw_weights_each_machine_by_its_own_window(game):
    """Every machine's rated draw is weighted by its own 300 s monitor and the whole
    per-machine figure used to be destroyed where it was computed, so "which factory is
    burning the grid" -- as against which one could -- had no answer anywhere."""
    projection = _projection()
    projection["machines"][0]["uptime"] = {"window_s": 300.0}  # monitored, never produced
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 150.0}
    # ROD_B carries no monitor at all, and is charged in full for it.
    view = _view(game, projection)
    rod = game.recipe_power_mw(game.recipes["Recipe_IronRod_C"], 1.0)
    assert view.unmonitored == 1
    assert view.measured_draw_mw == pytest.approx(rod * 1.5)
    assert view.measured_draw_mw < view.draw_mw


def test_measured_output_weights_each_machine_by_its_own_window(game):
    """A player could see what a factory COULD make and never what it IS making. Both rod
    constructors are nameplate 15/min; only what each spent its window doing separates the
    one that is fed from the one that is not."""
    projection = _projection()
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 300.0}
    projection["machines"][2]["uptime"] = {"window_s": 300.0, "produce_s": 60.0}
    projection["machines"][0]["uptime"] = {"window_s": 300.0, "produce_s": 150.0}
    view = _view(game, projection)
    assert dict(view.outputs())["Iron Rod"] == pytest.approx(30.0)
    rods = view.flows["Iron Rod"]
    assert rods["measured_produced"] == pytest.approx(15.0 + 3.0)
    assert rods["unmonitored_produced"] == 0.0
    # The smelter ran a fifth of its window, and the ingots the rods ate are measured too.
    assert view.measured_net("Iron Ingot") == pytest.approx(15.0 - (15.0 + 3.0))


def test_an_unmonitored_machine_is_left_out_of_measured_output(game):
    """The safe direction FLIPS between the two sides. Charging an unreadable machine in
    full keeps a POWER figure conservative and would make an OUTPUT figure optimistic, so
    the same record is counted in full on one side and not at all on the other."""
    projection = _projection()
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 300.0}
    # ROD_B and the smelter carry no monitor at all.
    view = _view(game, projection)
    rods = view.flows["Iron Rod"]
    assert rods["produced"] == pytest.approx(30.0)
    assert rods["measured_produced"] == pytest.approx(15.0), "only the readable one"
    assert rods["unmonitored_produced"] == pytest.approx(15.0), "the other is not lost"
    assert rods["measured_produced"] + rods["unmonitored_produced"] <= rods["produced"] + 1e-9
    assert (view.producers, view.unmonitored_producers) == (3, 2)
    # The draw side of the very same machines does the opposite, and must keep doing it.
    assert view.measured_draw_mw > 0


def test_an_item_no_monitor_can_see_is_unknown_rather_than_zero(game):
    """Nothing readable makes the ingots, so their measured rate is not 0 -- there is no
    measurement. Printing a zero there would report a factory the save cannot see as a
    factory that has stopped."""
    projection = _projection()
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 300.0}
    view = _view(game, projection)
    assert not view.measurable("Iron Ingot", "produced"), "only the unmonitored smelter"
    assert view.measurable("Iron Ingot", "consumed"), "one rod constructor is readable"
    assert view.measurable("Iron Rod", "produced")


def test_producing_now_is_counted_from_the_flag_not_from_the_window(game):
    """The window is a five-minute average and this is the snapshot at the moment of saving.
    A machine that produced for half its window and had stopped by the save is both, and it
    is the pair that explains a nameplate figure sitting beside a measured zero."""
    projection = _projection()
    projection["machines"][1]["uptime"] = {
        "window_s": 300.0,
        "produce_s": 150.0,
        "producing": False,
    }
    projection["machines"][2]["uptime"] = {"window_s": 300.0, "produce_s": 0.0, "producing": True}
    view = _view(game, projection)
    assert view.producing_now == 1
    assert view.producers == 3


def test_the_flow_aspects_print_both_rates_and_say_which_window(game, use_world):
    """A true answer that reads as a broken tool is not shipped. 2.50/min nameplate against
    0.00/min measured is only honest beside the sentence saying the measurement is of the
    window that ended when the save was written."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    projection = _projection()
    projection["header"] = {"save_identifier": "TEST-query-measured", "session_name": "t"}
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 60.0}
    st = WorldState(projection=projection, game=game)
    use_world(st)
    out = ftools.factory_query(f"machine:{','.join(INSIDE)}", show="outputs,balance,summary")
    assert "per min (measured)" in out
    assert "net (measured)" in out
    assert "no monitor" in out
    assert "window that ENDED when this save was written" in out
    assert "were mid-production at the instant the save was written" in out
    assert "measured is a FLOOR" in out
    # 30/min nameplate, of which one machine ran a fifth of its window and one is unreadable.
    assert "Iron Rod\t30\t3\t15" in out


def test_the_internal_aspect_names_what_never_crosses_the_boundary(game, use_world):
    """``FactoryView.internal`` was written, documented as "the mark of a self-contained
    line", and rendered by nothing. The fixture is balanced on purpose: 30 Iron Ingot
    made, 30 consumed, so the ingots appear in neither outputs nor inputs and the only
    place they can be seen at all is here."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    projection = _projection()
    projection["header"] = {"save_identifier": "TEST-query-internal", "session_name": "t"}
    st = WorldState(projection=projection, game=game)
    use_world(st)
    out = ftools.factory_query(f"machine:{','.join(INSIDE)}", show="internal,summary")
    assert "## internal" in out
    assert "Iron Ingot\t30" in out
    assert "keeps: Iron Ingot 30/min" in out


def test_the_machines_aspect_says_where_each_machine_stands(game, use_world):
    """MachineRow has carried a 3-D position since it was written and no aspect printed
    it, so the one table that names individual machines could not place any of them."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    projection = _projection()
    projection["header"] = {"save_identifier": "TEST-query-pos", "session_name": "t"}
    st = WorldState(projection=projection, game=game)
    use_world(st)
    out = ftools.factory_query(f"machine:{ROD_A}", show="machines")
    assert "x,y,z(m)" in out
    assert "10,0,0" in out, "1000 cm east of the origin, in metres, with its elevation"


def test_the_power_aspect_prints_both_figures(game, use_world):
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    projection = _projection()
    projection["header"] = {"save_identifier": "TEST-query-power", "session_name": "t"}
    projection["machines"][1]["uptime"] = {"window_s": 300.0, "produce_s": 150.0}
    st = WorldState(projection=projection, game=game)
    use_world(st)
    out = ftools.factory_query(f"machine:{','.join(INSIDE)}", show="power")
    assert "draw (nameplate)" in out
    assert "draw (measured)" in out


def test_a_machine_with_no_recipe_is_an_issue_not_a_silent_zero(game):
    projection = _projection()
    projection["machines"][1].pop("recipe")
    view = _view(game, projection)
    assert any("no recipe" in issue for issue in view.issues)


def test_an_unknown_building_is_an_issue_not_a_silent_zero(game):
    """Power that cannot be looked up must be reported, not quietly dropped -- otherwise
    a factory's draw is understated with nothing to show why."""
    projection = _projection()
    projection["machines"][0]["cls"] = "Build_SomethingModded_C"
    view = _view(game, projection)
    assert any("unknown building" in issue for issue in view.issues)


def test_every_advertised_aspect_is_handled(game):
    """ASPECTS is the tool's public menu; an entry with no branch would return an empty
    section instead of an error."""
    from satisfactory_mcp import server

    src = server.factory_query.__doc__ or ""
    for aspect in ASPECTS:
        assert aspect in src, f"{aspect} is offered but undocumented"


def test_links_count_machines_reached_not_edges(game):
    """Deliberately asymmetric. From a small set you reach many machines of a big
    neighbour; walking back, the first machine of the small set blocks the rest. Calling
    the column 'connections' invited reading it as an edge count, which it is not."""
    projection = _projection()
    view = _view(game, projection)
    back = _view(game, projection, machines=(OUTSIDER,), name="outsider")
    assert sum(view.links.values()) == 1
    assert sum(back.links.values()) >= 1

"""Walking the material graph the way material actually moves.

The question a cutover asks and nothing could answer: thirteen Oil Extractors sit on the
Spire nodes and twenty Fuel Generators are burning -- repiping the wrong one first drops
several GW. The measured answer is that exactly ONE of sixteen built Oil Extractors
carries all 5,000 MW, and the other fifteen carry nothing.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.factories.trace import orient, power_at_risk, trace

pytestmark = pytest.mark.integration


@pytest.fixture
def gens(live):
    out = [
        r["instance"].rsplit(".", 1)[-1]
        for r in live.projection["generators"]
        if r["cls"] == "Build_GeneratorFuel_C"
    ]
    if not out:
        pytest.skip("no fuel generators built")
    return out


# ------------------------------------------------------------ direction


def test_a_connector_name_states_direction_where_it_can():
    assert orient("Output1") == "out"
    assert orient("PipeOutputFactory") == "out"
    assert orient("Input0") == "in"
    assert orient("PipeInputFactory") == "in"
    # The bare pipe connector does not, which is the case the machine's own role settles.
    assert orient("FGPipeConnectionFactory") is None
    assert orient("ConveyorAny0") is None


def test_every_ambiguous_machine_connector_sits_on_an_extractor_or_generator(live, game):
    """Which is what makes the resolution exact rather than a heuristic: an extractor only
    produces and a generator only consumes, so its own nature orients the edge."""
    graph = live.projection["graph"]
    roles, actors = graph["roles"], graph["actors"]
    cls_of = {r["instance"].rsplit(".", 1)[-1]: r.get("cls", "") for r in live.all_records()}
    for edge in graph["material"]:
        for side in (0, 1):
            if not roles[edge[2 + side]].startswith("FGPipeConnectionFactory"):
                continue
            b = game.buildings.get(cls_of.get(actors[edge[side]], ""))
            if b is None:
                continue
            assert b.is_extractor or b.is_generator, b.name


# ------------------------------------------------------------ the walk


def test_only_one_oil_extractor_feeds_the_running_generators(live, game, gens):
    """The whole point. Fifteen of sixteen are safe to repipe; one is not."""
    upstream = trace(live, game, gens, "up")
    oil = [r for r in upstream.reached if r.cls == "Build_OilPump_C"]
    built = [r for r in live.projection["extractors"] if r["cls"] == "Build_OilPump_C"]
    assert len(oil) == 1
    assert len(built) > 10


def test_cutting_that_one_costs_the_whole_generator_hall(live, game, gens):
    upstream = trace(live, game, gens, "up")
    culprit = next(r for r in upstream.reached if r.cls == "Build_OilPump_C")
    mw, total, running = power_at_risk(live, game, [culprit.instance])
    assert total == len(gens)
    assert running == total
    assert mw == pytest.approx(len(gens) * 250.0, rel=0.01)


def test_an_extractor_that_feeds_nothing_risks_nothing(live, game, gens):
    upstream = trace(live, game, gens, "up")
    feeding = {r.instance for r in upstream.reached}
    idle = [
        r["instance"].rsplit(".", 1)[-1]
        for r in live.projection["extractors"]
        if r["cls"] == "Build_OilPump_C" and r["instance"].rsplit(".", 1)[-1] not in feeding
    ]
    assert idle
    mw, _, _ = power_at_risk(live, game, idle[:1])
    assert mw == 0.0


def test_logistics_is_walked_through_but_not_reported(live, game, gens):
    """331 nodes at depth 72, nearly all conveyor. A path through that is unreadable."""
    result = trace(live, game, gens, "up")
    assert result.visited > 200
    assert result.deepest > 20
    assert all(r.kind in ("extractor", "generator", "production") for r in result.reached)
    assert len(result.reached) < result.visited / 5


def test_the_walk_keeps_the_runs_it_crossed(live, game, gens):
    """The route, named without the table growing 300 rows: those hundreds of belt and pipe
    nodes contract to the runs a player would call them.

    Belts are named beside the pipes since schema 20, which is what makes this route
    followable rather than merely countable -- this walk is nearly all conveyor, so a list
    that could only name pipes named almost none of what it crossed.
    """
    result = trace(live, game, gens, "up")
    assert result.crossed
    assert len(result.crossed) < result.visited, "a run is many nodes, so this must contract"
    assert sum(link.pieces for link in result.crossed) <= result.visited
    named = [link.ident for link in result.crossed if link.ident]
    assert all(i.startswith(("chain:", "pipe:")) for i in named)
    assert any(i.startswith("chain:") for i in named), "a mostly-conveyor route named no belt"
    assert len(set(named)) == len(named), "one run listed twice is one run counted twice"


def test_the_traversal_itself_did_not_change(live, game, gens):
    """The runs are read off the nodes the walk already visited, so what it reaches and how
    deep it goes must be untouched by keeping them."""
    result = trace(live, game, gens, "up")
    assert (result.visited, result.deepest) == (
        trace(live, game, gens, "up").visited,
        trace(live, game, gens, "up").deepest,
    )
    assert {r.instance for r in result.reached} == {
        r.instance for r in trace(live, game, gens, "up").reached
    }


def test_the_route_note_summarises_rather_than_listing_every_run(live, game, gens):
    from satisfactory_mcp.interfaces.mcp.tools.factories.trace import VIA_NAMED, _via

    said = _via(trace(live, game, gens, "up").crossed)
    assert "run(s)" in said
    assert said.count("pipe:") + said.count("chain:") <= VIA_NAMED
    assert "carries no id" not in said, "the belt caveat outlived the gap it described"
    assert _via([]) == "", "no route is silence, not a sentence about nothing"


def test_up_and_down_are_inverses_for_a_reached_pair(live, game, gens):
    """If A is upstream of B then B must be downstream of A, or the orientation is being
    applied inconsistently in the two directions."""
    upstream = trace(live, game, gens, "up")
    culprit = next(r for r in upstream.reached if r.cls == "Build_OilPump_C")
    downstream = trace(live, game, [culprit.instance], "down")
    assert set(gens) <= {r.instance for r in downstream.reached}


# ------------------------------------------------------------ the tool


def test_the_tool_traces_from_a_building_name(game):
    out = srv.trace_upstream("Fuel-Powered Generator")
    assert not out.startswith("! ")
    assert "what feeds" in out
    assert "Oil Extractor" in out


def test_it_reports_power_at_risk_downstream(game, live, gens):
    upstream = trace(live, game, gens, "up")
    culprit = next(r for r in upstream.reached if r.cls == "Build_OilPump_C")
    out = srv.trace_upstream(culprit.instance, direction="down")
    assert "PROVEN running" in out
    assert "5,000 MW" in out


def test_an_unknown_seed_says_what_it_accepts(game):
    out = srv.trace_upstream("no such thing at all")
    assert out.startswith("! ")


def test_an_unknown_direction_lists_the_choices(game):
    assert "Choose from: up, down" in srv.trace_upstream(
        "Fuel-Powered Generator", direction="sideways"
    )


def test_commissioning_flags_the_cutover_risk(planned):
    """A wave that repipes a live feeder takes that power out at the moment the plan has
    least headroom to spare, so the warning belongs beside the sequence rather than in a
    tool you have to remember to call."""
    out = srv.commission_plan(plan="spire-coast-full", limit=4)
    assert "CUTOVER RISK" in out
    assert "Oil Extractor" in out
    assert "trace_upstream" in out

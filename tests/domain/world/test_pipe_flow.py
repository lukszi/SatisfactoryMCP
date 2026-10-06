"""Which way the fluid goes, and how far that claim is allowed to go.

The subject is an INFERENCE, so the tests that matter are not "does it produce a string" but
"does it ever produce a string it has no right to". Three kinds here:

* **Unit** -- hand-built projections, one rule each, including the ones that must REFUSE.
* **Reference world** -- coverage, and four checks that never consult the answer to judge the
  answer: the two models against each other, conservation, following the flow to its end, and
  water extractor to coal generator, which is known a priori.
* **Robustness** -- the schema-13 shape, a missing graph, a torn row. All must degrade to
  ``unknown`` rather than raise, because a map that loses its plumbing over a bad row is worse
  than a map with no arrows.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise

from satisfactory_mcp.domain.world.flow import (
    FORWARD,
    REVERSE,
    UNKNOWN,
    _build,
    _class_of,
    _solve,
    pipe_flow,
)

# --------------------------------------------------------------------------- unit


def _world(actors, roles, material, segments, **rest):
    """A projection with only the keys the inference reads."""
    return {
        "graph": {"actors": actors, "roles": roles, "material": material, "power": []},
        "pipes": {"classes": ["Build_Pipeline_C"], "networks": [], "segments": segments},
        **rest,
    }


#: Role indices used by the hand-built worlds below, in one place so a test reads as plumbing.
R = {
    "PipelineConnection0": 0,
    "PipelineConnection1": 1,
    "FGPipeConnectionFactory": 2,
    "PipeInputFactory": 3,
    "PipeOutputFactory": 4,
    "Connection0": 5,
    "Connection1": 6,
    "Connection2": 7,
    "ConnectionAny0": 8,
    "ConnectionAny1": 9,
}
ROLES = [name for name, _ in sorted(R.items(), key=lambda kv: kv[1])]


def _couple(a, ra, b, rb):
    """Both directions, because the save writes both and the reader relies on it."""
    return [[a, b, R[ra], R[rb]], [b, a, R[rb], R[ra]]]


def _line(points):
    return [0, 0, points]


def test_a_pipe_from_an_extractor_flows_away_from_it():
    """The simplest true statement in the whole subject, and the one the owner asked for.

    A water extractor cannot consume water. So a pipe with one end on its port and a consumer
    somewhere beyond the other end runs away from the extractor, and ``basis`` says the
    warrant is the port rather than any inference over the network.
    """
    world = _world(
        actors=["Build_WaterPump_C_1", "Build_Pipeline_C_2", "Build_GeneratorCoal_C_3"],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "FGPipeConnectionFactory"),
        ],
        segments=[[0, 0, [[0, 0, 0], [1000, 0, 0]], 1]],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    assert pipe_flow(world) == [{"direction": FORWARD, "basis": "machine port"}]


def test_the_answer_is_about_the_pipe_and_not_about_the_point_order():
    """The same plumbing with the pipe drawn the other way round comes out ``reverse``.

    This is the test that fails if the direction is quietly being read off the spline's own
    order -- which is the order the player dragged it and means nothing. Here the extractor is
    on ``PipelineConnection1``, i.e. the LAST point, so the fluid runs backwards along the
    drawn line and the row has to say so.
    """
    world = _world(
        actors=["Build_WaterPump_C_1", "Build_Pipeline_C_2", "Build_GeneratorCoal_C_3"],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection1"),
            *_couple(1, "PipelineConnection0", 2, "FGPipeConnectionFactory"),
        ],
        segments=[[0, 0, [[0, 0, 0], [1000, 0, 0]], 1]],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    assert pipe_flow(world) == [{"direction": REVERSE, "basis": "machine port"}]


def test_a_machines_port_name_types_it_without_any_help_from_the_docs():
    """``PipeInputFactory`` and ``PipeOutputFactory`` are the game's own Consumer and Producer.

    Neither building here appears in ``extractors`` or ``generators`` -- there is no game data
    in this projection at all -- and the direction still comes out, because the component NAME
    carried it. That is the finding this whole module rests on.
    """
    world = _world(
        actors=["Build_OilRefinery_C_1", "Build_Pipeline_C_2", "Build_Packager_C_3"],
        roles=ROLES,
        material=[
            *_couple(0, "PipeOutputFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "PipeInputFactory"),
        ],
        segments=[[0, 0, [[0, 0, 0], [1000, 0, 0]], 1]],
    )
    assert pipe_flow(world) == [{"direction": FORWARD, "basis": "machine port"}]


def test_a_pump_orients_the_pipes_on_either_side_of_it():
    """One-way by construction, ``Connection0`` in and ``Connection1`` out.

    No typed port anywhere in this world: two buffers with ``ConnectionAny`` ports, which the
    save is explicitly declining to type, and a pump between them. The pump is the only thing
    that knows, and both pipes come out oriented with ``pump`` as the warrant.
    """
    world = _world(
        actors=[
            "Build_PipeStorageTank_C_0",
            "Build_Pipeline_C_1",
            "Build_PipelinePump_C_2",
            "Build_Pipeline_C_3",
            "Build_PipeStorageTank_C_4",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "ConnectionAny0", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "Connection0"),
            *_couple(2, "Connection1", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "ConnectionAny0"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [1000, 0, 0]], 1],
            [0, 0, [[2000, 0, 0], [3000, 0, 0]], 3],
        ],
    )
    assert pipe_flow(world) == [
        {"direction": FORWARD, "basis": "pump"},
        {"direction": FORWARD, "basis": "pump"},
    ]


def test_direction_propagates_through_a_junction_to_a_pipe_touching_nothing():
    """A pipe with no port and no pump at either end still gets an answer, labelled as such.

    Extractor, pipe, junction, pipe, generator: the middle of that is a pipe whose own ends
    are two junction bodies, and the only reason it has a direction is the shape of the wider
    network. ``propagated`` is the warrant, and it is a weaker claim than the other two on
    purpose.
    """
    world = _world(
        actors=[
            "Build_WaterPump_C_0",
            "Build_Pipeline_C_1",
            "Build_PipelineJunction_Cross_C_2",
            "Build_Pipeline_C_3",
            "Build_PipelineJunction_Cross_C_4",
            "Build_Pipeline_C_5",
            "Build_GeneratorCoal_C_6",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "Connection0"),
            *_couple(2, "Connection1", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "Connection0"),
            *_couple(4, "Connection1", 5, "PipelineConnection0"),
            *_couple(5, "PipelineConnection1", 6, "FGPipeConnectionFactory"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [100, 0, 0]], 1],
            [0, 0, [[200, 0, 0], [300, 0, 0]], 3],
            [0, 0, [[400, 0, 0], [500, 0, 0]], 5],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    assert pipe_flow(world) == [
        {"direction": FORWARD, "basis": "machine port"},
        {"direction": FORWARD, "basis": "propagated"},
        {"direction": FORWARD, "basis": "machine port"},
    ]


def test_the_t_junction_is_a_body_exactly_as_the_cross_is():
    """A junction missing from ``_BODIES`` is a CUT, so the far half loses its warrant.

    The same plumbing as above with a T in the middle. Left out, the T's three ports are
    three unjoined nodes and the network stops there.
    """
    world = _world(
        actors=[
            "Build_WaterPump_C_0",
            "Build_Pipeline_C_1",
            "Build_PipelineJunction_T_C_2",
            "Build_Pipeline_C_3",
            "Build_GeneratorCoal_C_4",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "Connection0"),
            *_couple(2, "Connection2", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "FGPipeConnectionFactory"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [100, 0, 0]], 1],
            [0, 0, [[200, 0, 0], [300, 0, 0]], 3],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    assert pipe_flow(world) == [
        {"direction": FORWARD, "basis": "machine port"},
        {"direction": FORWARD, "basis": "machine port"},
    ]


def test_a_pipe_into_a_dead_end_is_refused_rather_than_guessed():
    """The guard, and the hold-out that put it there.

    Extractor, junction, and two branches: one reaching a generator, one reaching nothing.
    Conservation at the junction says two of the three are settled and the third is forced --
    and following that would draw fluid flowing into a stub that consumes nothing. Nothing
    flows down a dead branch, so the honest answer is ``unknown`` and this pins it.
    """
    world = _world(
        actors=[
            "Build_WaterPump_C_0",
            "Build_Pipeline_C_1",
            "Build_PipelineJunction_Cross_C_2",
            "Build_Pipeline_C_3",
            "Build_GeneratorCoal_C_4",
            "Build_Pipeline_C_5",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "Connection0"),
            *_couple(2, "Connection1", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "FGPipeConnectionFactory"),
            *_couple(2, "Connection2", 5, "PipelineConnection0"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [100, 0, 0]], 1],
            [0, 0, [[200, 0, 0], [300, 0, 0]], 3],
            [0, 0, [[200, 0, 0], [200, 100, 0]], 5],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    rows = pipe_flow(world)
    assert rows[0]["direction"] == FORWARD and rows[1]["direction"] == FORWARD
    assert rows[2] == {"direction": UNKNOWN, "basis": "unresolved"}


def test_a_pipe_in_a_loop_is_refused_because_both_ways_are_consistent():
    """Two routes from the same source to the same sink: the fluid may take either.

    Removing a pipe that lies in a cycle splits nothing, so the cut argument has no purchase
    and there is no ordering to prefer. Refused rather than settled by whichever pipe the loop
    happened to be walked from.
    """
    world = _world(
        actors=[
            "Build_WaterPump_C_0",
            "Build_PipelineJunction_Cross_C_1",
            "Build_Pipeline_C_2",
            "Build_Pipeline_C_3",
            "Build_PipelineJunction_Cross_C_4",
            "Build_GeneratorCoal_C_5",
            "Build_Pipeline_C_6",
            "Build_Pipeline_C_7",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 6, "PipelineConnection0"),
            *_couple(6, "PipelineConnection1", 1, "Connection0"),
            *_couple(1, "Connection1", 2, "PipelineConnection0"),
            *_couple(2, "PipelineConnection1", 4, "Connection0"),
            *_couple(1, "Connection2", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "Connection1"),
            *_couple(4, "Connection2", 7, "PipelineConnection0"),
            *_couple(7, "PipelineConnection1", 5, "FGPipeConnectionFactory"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [100, 0, 0]], 2],
            [0, 0, [[0, 0, 0], [100, 0, 0]], 3],
            [0, 0, [[0, 0, 0], [100, 0, 0]], 6],
            [0, 0, [[0, 0, 0], [100, 0, 0]], 7],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    rows = pipe_flow(world)
    assert rows[0]["direction"] == UNKNOWN, "one leg of the loop"
    assert rows[1]["direction"] == UNKNOWN, "and the other"
    assert rows[2]["direction"] != UNKNOWN, "the stem into the loop is still forced"
    assert rows[3]["direction"] != UNKNOWN, "and the stem out of it"


def test_a_buffers_ports_are_typed_as_neither_because_the_save_types_them_as_either():
    """``ConnectionAny`` is the game declining to say, and this declines with it.

    A tank between an extractor and a generator passes fluid through -- so the pipes on either
    side are settled by the machines beyond it -- but the tank itself contributes no producer
    and no consumer. If ``ConnectionAny`` were being read as a port, the tank would look like
    both and every pipe touching it would be refused.
    """
    world = _world(
        actors=[
            "Build_WaterPump_C_0",
            "Build_Pipeline_C_1",
            "Build_IndustrialTank_C_2",
            "Build_Pipeline_C_3",
            "Build_GeneratorCoal_C_4",
        ],
        roles=ROLES,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "ConnectionAny0"),
            *_couple(2, "ConnectionAny1", 3, "PipelineConnection0"),
            *_couple(3, "PipelineConnection1", 4, "FGPipeConnectionFactory"),
        ],
        segments=[
            [0, 0, [[0, 0, 0], [100, 0, 0]], 1],
            [0, 0, [[200, 0, 0], [300, 0, 0]], 3],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    assert [r["direction"] for r in pipe_flow(world)] == [FORWARD, FORWARD]


def test_a_hypertube_is_not_plumbing():
    """``PipeHyperConnection*`` moves a player and no fluid, and never joins a fluid node.

    The same trap ``PIPE_CLASSES`` exists for, one layer up: a substring match on ``Pipe``
    would splice the hypertube network into the plumbing graph and could orient a pipe from a
    walkway. Here a hypertube touches the pipe's far end and changes nothing.
    """
    roles = [*ROLES, "PipeHyperConnection0", "PipeHyperConnection1"]
    world = _world(
        actors=["Build_WaterPump_C_0", "Build_Pipeline_C_1", "Build_PipeHyper_C_2"],
        roles=roles,
        material=[
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            [1, 2, R["PipelineConnection1"], len(ROLES)],
            [2, 1, len(ROLES), R["PipelineConnection1"]],
        ],
        segments=[[0, 0, [[0, 0, 0], [1000, 0, 0]], 1]],
        extractors=[{"cls": "Build_WaterPump_C"}],
    )
    # No consumer anywhere, so nothing flows -- and the hypertube did not become one.
    assert pipe_flow(world) == [{"direction": UNKNOWN, "basis": "unresolved"}]


# ------------------------------------------------------------------- robustness


def test_a_projection_with_nothing_to_go_on_answers_unknown_rather_than_raising():
    """Every shape a projection can arrive in, including the schema-13 one.

    A three-column segment carries no join to the graph, which is precisely a projection
    written before this existed. It has to read as "no idea", never as an error and never as a
    silently dropped pipe -- the map still has plumbing to draw.
    """
    assert pipe_flow({}) == []
    assert pipe_flow({"pipes": {}}) == []
    assert pipe_flow({"pipes": {"segments": []}}) == []
    # Schema 13: three columns, no graph.
    old = {"pipes": {"segments": [_line([[0, 0, 0], [1, 1, 1]])]}}
    assert pipe_flow(old) == [{"direction": UNKNOWN, "basis": "unresolved"}]
    # A join with no graph behind it.
    assert pipe_flow({"pipes": {"segments": [[0, 0, [[0, 0, 0]], 7]]}}) == [
        {"direction": UNKNOWN, "basis": "unresolved"}
    ]


def test_a_torn_row_costs_that_row_and_not_the_plumbing():
    """The projection is read guarded field by field, the same rule the surface above uses."""
    world = _world(
        actors=["Build_WaterPump_C_0", "Build_Pipeline_C_1", "Build_GeneratorCoal_C_2"],
        roles=ROLES,
        material=[
            "not an edge",
            [0],  # too short
            *_couple(0, "FGPipeConnectionFactory", 1, "PipelineConnection0"),
            *_couple(1, "PipelineConnection1", 2, "FGPipeConnectionFactory"),
        ],
        segments=[
            "not a segment",
            [0, 0],
            [0, 0, [[0, 0, 0], [1000, 0, 0]], 1],
            [0, 0, [[0, 0, 0]], "not an index"],
        ],
        extractors=[{"cls": "Build_WaterPump_C"}],
        generators=[{"cls": "Build_GeneratorCoal_C"}],
    )
    rows = pipe_flow(world)
    assert len(rows) == 4, "one row out per row in, positionally, or the join to /api/pipes slips"
    assert rows[2] == {"direction": FORWARD, "basis": "machine port"}
    assert [r["direction"] for r in (rows[0], rows[1], rows[3])] == [UNKNOWN] * 3


# --------------------------------------------------------------- reference world


def test_the_reference_world_is_mostly_settled_and_says_so_where_it_is_not(projection):
    """Coverage, and the shape of what is left over."""
    rows = pipe_flow(projection)
    assert len(rows) == len(projection["pipes"]["segments"])
    settled = [r for r in rows if r["direction"] != UNKNOWN]
    assert len(rows) == 503
    assert len(settled) == 365, "the measured coverage; a change here is a change of claim"
    assert {r["basis"] for r in settled} == {"machine port", "pump", "propagated"}
    assert all(r["basis"] == "unresolved" for r in rows if r["direction"] == UNKNOWN)
    assert {r["direction"] for r in settled} == {FORWARD, REVERSE}


def test_the_two_models_never_disagree_where_both_of_them_speak(projection):
    """The strongest check available, because the two do not share a premise.

    One argues from conservation across a cut given the typed machine ports; the other from a
    pump being one-way. Where both settle the same pipe they are an independent check on each
    other, and a sign error in either shows up here as a disagreement.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    by_ports = _solve(pipes, devices, terminals, adjacency, stores, one_way=False)
    by_pumps = _solve(pipes, devices, terminals, adjacency, stores, cuts=False)
    overlap = [(a, b) for a, b in zip(by_ports, by_pumps) if a and b]
    assert len(overlap) == 118, "the measured overlap"
    assert all(a == b for a, b in overlap), "the two models disagree about a pipe"


def test_no_node_ends_up_making_fluid_or_swallowing_it(projection):
    """Conservation, audited over the finished answer rather than assumed by construction.

    A junction with no port of its own cannot be where fluid appears or vanishes. Any node all
    of whose settled pipes point the same way, with nothing unsettled to absorb the difference
    and no machine port to explain it, is a contradiction.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    settled = _solve(pipes, devices, terminals, adjacency, stores)
    ported = {node for node, _kind, _actor in terminals}
    tally: dict = defaultdict(lambda: [0, 0, 0])
    for i, (a, b) in enumerate(pipes):
        if a is None or b is None:
            continue
        if settled[i] == 0:
            tally[a][2] += 1
            tally[b][2] += 1
        else:
            head, tail = (a, b) if settled[i] == 1 else (b, a)
            tally[head][1] += 1
            tally[tail][0] += 1
    for a, b in devices:
        tally[a][1] += 1
        tally[b][0] += 1
    checked = 0
    for node, (into, out, open_) in tally.items():
        if open_ or node in ported or into + out < 2:
            continue
        checked += 1
        assert into and out, f"node {node}: {into} in, {out} out, and nothing to explain it"
    assert checked == 293, "the measured number of fully-settled junctions"


def test_following_the_flow_never_arrives_at_a_producer(projection):
    """The end-to-end check: walk the answer, and see where it takes you.

    Fluid ends at something that consumes it. If any single pipe's sign were flipped, a walk
    downstream from somewhere would arrive at a water extractor -- which cannot happen -- or a
    walk upstream would set off from a coal generator. Neither uses the pipe's own end to judge
    it, which is what makes this independent of the rule that settled it.
    """
    pipes, devices, terminals, adjacency, stores = _build(projection)
    settled = _solve(pipes, devices, terminals, adjacency, stores)
    kinds: dict = defaultdict(set)
    for node, kind, _actor in terminals:
        kinds[node].add(kind)

    downstream: dict = defaultdict(list)
    upstream: dict = defaultdict(list)
    for i, (a, b) in enumerate(pipes):
        if not settled[i] or a is None or b is None:
            continue
        head, tail = (a, b) if settled[i] == 1 else (b, a)
        downstream[head].append(tail)
        upstream[tail].append(head)
    for a, b in devices:
        downstream[a].append(b)
        upstream[b].append(a)

    def reached(start, edges):
        seen, stack = {start}, [start]
        while stack:
            node = stack.pop()
            for peer in edges.get(node, ()):
                if peer not in seen:
                    seen.add(peer)
                    stack.append(peer)
        return {k for node in seen for k in kinds.get(node, ())}

    walked = 0
    for i, (a, b) in enumerate(pipes):
        if not settled[i] or a is None or b is None:
            continue
        head, tail = (a, b) if settled[i] == 1 else (b, a)
        walked += 1
        assert reached(tail, downstream) != {"source"}, f"pipe {i} flows INTO a producer"
        assert reached(head, upstream) != {"sink"}, f"pipe {i} flows OUT of a consumer"
    assert walked == 365


def test_water_runs_from_the_extractors_to_the_coal_plants(projection):
    """The one direction on this world that is known before any of this ran.

    A water extractor makes water and a coal generator drinks it; nothing about the inference
    is needed to know which way that goes. So no water pipe may point into an extractor or out
    of a generator, and 39 of them touch one or the other.
    """
    pipes, _devices, terminals, _adjacency, _stores = _build(projection)
    rows = pipe_flow(projection)
    classes: dict = defaultdict(set)
    actors = projection["graph"]["actors"]
    for node, _kind, actor in terminals:
        classes[node].add(_class_of(actors[actor]))

    networks = projection["pipes"]["networks"]
    water = {i for i, n in enumerate(networks) if n.get("fluid") == "Desc_Water_C"}
    touching = 0
    for i, (a, b) in enumerate(pipes):
        if projection["pipes"]["segments"][i][0] not in water:
            continue
        if rows[i]["direction"] == UNKNOWN:
            continue
        head, tail = (a, b) if rows[i]["direction"] == FORWARD else (b, a)
        assert "Build_WaterPump_C" not in classes[tail], f"pipe {i} flows INTO a water extractor"
        assert "Build_GeneratorCoal_C" not in classes[head], f"pipe {i} flows OUT of a coal plant"
        if "Build_WaterPump_C" in classes[head] or "Build_GeneratorCoal_C" in classes[tail]:
            touching += 1
    assert touching == 39, "the measured number of pipes with a known end"


def test_the_oil_lift_climbs_which_is_the_only_reason_a_pump_is_there(projection):
    """Gravity as an oracle, and it knows nothing about any of the models above.

    This world holds an unfinished crude-oil trunk: 45 pipes and 9 Mk2 pumps over 696 m,
    climbing 240 m, with both ends not yet plumbed to anything. It has no typed port anywhere,
    so the pumps are the only reason any of it is settled -- and a pump pushes UP. If
    ``Connection0`` were the outlet rather than the inlet this same test would report the oil
    running 191 m downhill through nine pumps arranged to help it fall.
    """
    rows = pipe_flow(projection)
    networks = projection["pipes"]["networks"]
    lift = next(i for i, n in enumerate(networks) if n.get("id") == 28)
    climb = 0.0
    settled = 0
    for i, seg in enumerate(projection["pipes"]["segments"]):
        if seg[0] != lift or rows[i]["direction"] == UNKNOWN:
            continue
        settled += 1
        first, last = seg[2][0], seg[2][-1]
        climb += (last[2] - first[2]) if rows[i]["direction"] == FORWARD else (first[2] - last[2])
    assert settled == 31, "the measured coverage of that trunk"
    assert climb / 100.0 > 150.0, f"the inferred flow runs {climb / 100:.0f} m, and a pump lifts"
    # And the trunk really is the climb this claims: a flat line would make the test vacuous.
    zs = [p[2] for seg in projection["pipes"]["segments"] if seg[0] == lift for p in seg[2]]
    assert (max(zs) - min(zs)) / 100.0 > 200.0


def test_the_inference_is_cheap_enough_to_do_on_every_request(projection, state):
    """It walks the plumbing once per pipe, so its cost is worth stating rather than assuming.

    Cached on ``WorldState`` for the same reason the factory graph is, and this pins that the
    cache is real: a second read is the same object, not an equal one.
    """
    assert state.pipe_flow is state.pipe_flow
    assert len(state.pipe_flow) == len(projection["pipes"]["segments"])


def test_every_settled_pipe_has_two_ends_in_the_graph(projection):
    """A direction on a pipe the graph does not fully join would be an answer about nothing."""
    pipes, _devices, _terminals, _adjacency, _stores = _build(projection)
    rows = pipe_flow(projection)
    for i, (a, b) in enumerate(pipes):
        if rows[i]["direction"] != UNKNOWN:
            assert a is not None and b is not None, i
            assert a != b, f"pipe {i} has both ends at the same node"
    # And the world's pipes really are joined: 5 of the 503 have one loose end, no more.
    loose = sum(1 for a, b in pipes if a is None or b is None)
    assert loose == 5
    assert all(
        rows[i]["direction"] == UNKNOWN for i, (a, b) in enumerate(pipes) if a is None or b is None
    )


def test_a_chevron_would_have_something_to_sit_on(projection):
    """The client draws one mark per 24 m of settled pipe, with a 4 m floor.

    Kept here rather than left to the page, because the numbers in the map's comment are
    claims about this world's geometry and this is where that geometry lives.

    Measured the way the page measures, which took two corrections to get right and is the
    reason this test is worth having at all. In the PLAN, two dimensions, because the map is
    top-down and a pipe's climb is not length it has anywhere to put a mark -- counting in
    three says 412. And on the ROUNDED metres ``/api/pipes`` actually sends, not on the
    projection's centimetres, because a run summed after rounding is not the same number and
    two pipes here sit near enough a 24 m boundary to change their mark count over it.
    """
    rows = pipe_flow(projection)
    marks = 0
    too_short = 0
    for i, seg in enumerate(projection["pipes"]["segments"]):
        if rows[i]["direction"] == UNKNOWN:
            continue
        plan = [(round(p[0] / 100.0, 1), round(p[1] / 100.0, 1)) for p in seg[2]]
        length = sum(math.dist(a, b) for a, b in pairwise(plan))
        if length < 4.0:
            too_short += 1
            continue
        marks += max(1, int(length // 24.0))
    assert (marks, too_short) == (400, 28), "the page draws 400, measured in the browser"

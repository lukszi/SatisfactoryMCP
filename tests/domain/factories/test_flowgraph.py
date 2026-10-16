"""Where an output ends up, walked over a hand-built belt graph: box, machine, sink, export."""

from __future__ import annotations

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories import flowgraph as fg
from satisfactory_mcp.domain.world.logistics import BY_ROLE, Link, PhysicalGraph

BELT = ports.CONVEYOR

MAKER = "Build_ConstructorMk1_C_1"
USER = "Build_AssemblerMk1_C_2"
OUTSIDE = "Build_AssemblerMk1_C_9"
SPLIT = "Build_ConveyorAttachmentSplitter_C_3"
BOX = "Build_StorageContainerMk1_C_4"
BUFFER = "Build_StorageContainerMk1_C_5"
SINK = "Build_ResourceSink_C_6"


def _graph(*pairs: tuple[str, str | None]) -> PhysicalGraph:
    graph = PhysicalGraph()
    for source, target in pairs:
        link = Link(source=source, target=target, medium=BELT, basis=BY_ROLE, pieces=3)
        graph.links.append(link)
        graph.outbound[source].append(link)
        if target is not None:
            graph.inbound[target].append(link)
    return graph


def _ends(graph: PhysicalGraph) -> set:
    machines = {MAKER, USER, OUTSIDE}
    return fg.output_destinations(graph, MAKER, BELT, {MAKER, USER}, machines.__contains__)


def test_a_splitter_to_a_box_and_a_machine_reaches_both():
    graph = _graph((MAKER, SPLIT), (SPLIT, BOX), (SPLIT, USER))
    assert _ends(graph) == {(fg.STORAGE, BOX), (fg.INSIDE, USER)}


def test_a_sink_is_its_own_end_and_not_storage():
    assert _ends(_graph((MAKER, SINK))) == {(fg.SINK, SINK)}


def test_a_machine_outside_the_set_is_an_export():
    assert _ends(_graph((MAKER, SPLIT), (SPLIT, OUTSIDE))) == {(fg.EXPORT, OUTSIDE)}


def test_a_box_that_something_drains_is_walked_through_as_a_buffer():
    graph = _graph((MAKER, BUFFER), (BUFFER, USER))
    assert _ends(graph) == {(fg.BUFFER, BUFFER), (fg.INSIDE, USER)}


def test_a_belt_to_nothing_and_a_splitter_to_nothing_both_go_nowhere():
    assert _ends(_graph((MAKER, None))) == {(fg.NOWHERE, None)}
    assert _ends(_graph((MAKER, SPLIT))) == {(fg.NOWHERE, SPLIT)}


def test_node_kinds_are_read_off_the_class():
    assert fg.node_kind(SINK) == fg.SINK
    assert fg.node_kind(BOX) == fg.STORAGE
    assert fg.node_kind("Build_IndustrialTank_C_7") == fg.STORAGE
    assert fg.node_kind("Build_PipeStorageTank_C_8") == fg.STORAGE
    assert fg.node_kind(SPLIT) == "other"


def _group(key: str, makes: dict[str, float] | None = None, uses: dict[str, float] | None = None):
    group = fg.Group(key, key, key)
    group.makes.update(makes or {})
    group.uses.update(uses or {})
    return group


def _apportioned(groups: list[fg.Group], reach: fg.Reach, kinds: fg.Reach | None = None):
    out = fg.FlowGraph(groups={g.key: g for g in groups})
    for g in groups:
        for item, rate in g.uses.items():
            out.consumed[item] = out.consumed.get(item, 0.0) + rate
    fg._apportion(out, reach, kinds or {})
    return out.edges


def _into(edges, target: str) -> float:
    return sum(e.per_min or 0.0 for e in edges if e.target == target)


def _from(edges, source: str) -> float:
    return sum(e.per_min or 0.0 for e in edges if e.source == source)


def test_a_short_supplier_sends_what_it_makes_and_the_gap_comes_in_once():
    edges = _apportioned(
        [_group("s", makes={"Ore": 60.0}), _group("c", uses={"Ore": 90.0})],
        {("s", "Ore"): {"c"}},
    )
    assert sorted(edges, key=lambda e: e.source) == [
        fg.FlowEdge("in:Ore", "c", "Ore", 30.0),
        fg.FlowEdge("s", "c", "Ore", 60.0),
    ]


def test_supply_short_of_several_consumers_is_shared_and_never_overdrawn():
    groups = [
        _group("s1", makes={"Ore": 30.0}),
        _group("s2", makes={"Ore": 60.0}),
        _group("c1", uses={"Ore": 100.0}),
        _group("c2", uses={"Ore": 50.0}),
    ]
    edges = _apportioned(groups, {("s1", "Ore"): {"c1", "c2"}, ("s2", "Ore"): {"c1", "c2"}})
    assert abs(_from(edges, "s1") - 30.0) < 0.02
    assert abs(_from(edges, "s2") - 60.0) < 0.02
    assert abs(_into(edges, "c1") - 100.0) < 0.02
    assert abs(_into(edges, "c2") - 50.0) < 0.02
    assert abs(_from(edges, "in:Ore") - 60.0) < 0.02


def test_a_supplier_shared_with_a_short_consumer_hands_its_spare_on():
    groups = [
        _group("a", makes={"Ore": 60.0}),
        _group("b", makes={"Ore": 60.0}),
        _group("c1", uses={"Ore": 100.0}),
        _group("c2", uses={"Ore": 20.0}),
    ]
    edges = _apportioned(groups, {("a", "Ore"): {"c1", "c2"}, ("b", "Ore"): {"c2"}})
    assert _from(edges, "a") <= 60.0 + 0.01
    assert _from(edges, "b") <= 60.0 + 0.01
    assert abs(_into(edges, "c1") - 100.0) < 0.02
    assert abs(_into(edges, "c2") - 20.0) < 0.02
    assert not [e for e in edges if e.source.startswith("in:") and e.target == "c2"]


def test_ample_supply_keeps_the_proportional_split_and_the_surplus_goes_on():
    groups = [
        _group("s1", makes={"Ore": 30.0}),
        _group("s2", makes={"Ore": 90.0}),
        _group("c", uses={"Ore": 60.0}),
    ]
    reach = {("s1", "Ore"): {"c"}, ("s2", "Ore"): {"c"}}
    edges = _apportioned(groups, reach, {("s2", "Ore"): {fg.STORAGE}})
    assert fg.FlowEdge("s1", "c", "Ore", 15.0) in edges
    assert fg.FlowEdge("s2", "c", "Ore", 45.0) in edges
    assert fg.FlowEdge("s2", fg.STORAGE, "Ore", 45.0) in edges
    assert not [e for e in edges if e.source.startswith("in:")]

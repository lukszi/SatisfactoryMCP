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

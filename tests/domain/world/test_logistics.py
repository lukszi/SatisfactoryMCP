"""The physical logistics graph, and the role filter that keeps hypertubes out of it.

Two properties are measured on the committed projection rather than asserted in the
abstract: a hypertube is not a belt, and contracting the conduit runs out of
``graph["material"]`` leaves node-to-node links that the save STATES rather than links
geometry guesses. The counts here move whenever the fixture is re-cut, on
``tests/data/test_reference_counts``'s terms.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.core.saveio import rows as saverows
from satisfactory_mcp.core.saveio.records import actor_class
from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.world.conduits import build_runs
from satisfactory_mcp.domain.world.logistics import (
    BASIS_UNKNOWN,
    BY_NATURE,
    BY_ROLE,
    build_physical_graph,
)

#: Hypertube couplings in the reference projection. They are published in the same list as
#: the belts and are separated by role alone.
HYPERTUBE_EDGES = 126


def test_the_vocabulary_separates_the_three_media() -> None:
    assert ports.medium("ConveyorAny0") == ports.CONVEYOR
    assert ports.medium("Output1") == ports.CONVEYOR
    assert ports.medium("PipelineConnection0") == ports.PIPE
    assert ports.medium("Connection2") == ports.PIPE
    assert ports.medium("PipeHyperConnection0") == ports.HYPERTUBE
    assert ports.medium("PipeHyperStartConnection") == ports.HYPERTUBE
    # A role this table has never seen is unknown, never guessed into a medium.
    assert ports.medium("SomeFutureConnection") is None


def test_a_hypertube_role_is_not_matched_by_substring() -> None:
    """``PipeHyperConnection0`` contains ``Pipe``; the fluid classifier must not take it."""
    assert ports.medium("PipeHyperConnection0") != ports.PIPE
    assert ports.is_hypertube_edge("PipeHyperConnection0", "PipeHyperConnection1")
    assert not ports.is_hypertube_edge("PipelineConnection0", "PipelineConnection1")
    assert not ports.is_hypertube_edge("Output1", "ConveyorAny0")


def test_the_factory_graph_keeps_hypertubes_off_the_material_layer(projection) -> None:
    graph = build_graph(projection)
    assert len(graph.hyper) == HYPERTUBE_EDGES
    assert not any(ports.is_hypertube_edge(e.role_a, e.role_b) for e in graph.material), (
        "a hypertube on the material layer is a pedestrian route read as a belt"
    )


def test_every_material_edge_in_the_fixture_names_a_medium(projection) -> None:
    """A role the vocabulary cannot place would leave an edge silently unclassified."""
    roles = projection["graph"]["roles"]
    unplaced = sorted(r for r in roles if ports.medium(r) is None)
    assert not unplaced, f"connector roles this vocabulary does not know: {unplaced}"


def test_the_runs_contract_to_two_ended_links(projection, game) -> None:
    """The join is by actor identity, so almost every run names both of its ends."""
    graph = build_physical_graph(projection, game)
    assert len(graph.links) == 2198
    both_ended = [link for link in graph.links if link.target and link.source]
    assert len(both_ended) == 2174
    # What could not be joined, stated rather than guessed at.
    assert len(graph.dangling) == 24
    assert graph.orphan_runs == 2


def test_direction_is_declined_only_between_fittings(projection, game) -> None:
    """A pipe between two junctions has no direction without the rates; a belt always has
    one, because the machine end of it names itself an input or an output."""
    graph = build_physical_graph(projection, game)
    assert graph.undirected == 128
    undirected = [link for link in graph.links if link.basis == BASIS_UNKNOWN and link.target]
    assert {link.medium for link in undirected} == {ports.PIPE}
    conveyors = [link for link in graph.links if link.medium == ports.CONVEYOR]
    assert all(link.basis in (BY_ROLE, BY_NATURE) for link in conveyors)


def test_an_undirected_link_is_walked_from_either_end(projection, game) -> None:
    """``other`` is the only safe accessor on an undirected link: it is indexed from both
    ends, so reading ``source`` after ``feeds`` can hand the caller its own node back."""
    graph = build_physical_graph(projection, game)
    undirected = next(
        link for link in graph.links if link.basis == BASIS_UNKNOWN and link.source and link.target
    )
    assert link_in(graph.inbound.get(undirected.source), undirected)
    assert link_in(graph.inbound.get(undirected.target), undirected)
    assert undirected.other(undirected.source) == undirected.target
    assert undirected.other(undirected.target) == undirected.source


def link_in(links, wanted) -> bool:
    return any(link is wanted for link in links or ())


def test_no_hypertube_reaches_the_physical_graph(projection, game) -> None:
    graph = build_physical_graph(projection, game)
    assert {link.medium for link in graph.links} == {ports.CONVEYOR, ports.PIPE}


def test_a_machines_recipe_finds_its_feeders(projection, game) -> None:
    """The coverage measurement the graph is worth building for: of the machines with a
    recipe set, how many have a physical feeder for every ingredient."""
    graph = build_physical_graph(projection, game)
    with_recipe = fed = 0
    for record in projection["machines"]:
        recipe = game.recipes.get(record.get("recipe") or "")
        if recipe is None:
            continue
        with_recipe += 1
        short = record["instance"].rsplit(".", 1)[-1]
        arriving = {link.medium for link in graph.feeds(short)}
        wanted = {
            ports.PIPE
            if (game.items.get(flow.item) and game.items[flow.item].is_fluid)
            else ports.CONVEYOR
            for flow in recipe.ingredients
        }
        fed += wanted <= arriving
    assert (with_recipe, fed) == (426, 423)


def test_both_a_pipe_run_and_a_belt_run_are_followable(projection, game) -> None:
    """``ident`` is the id ``search_conduits`` prints and ``resolve_place`` takes.

    Both media carry one since schema 20 gave a belt segment the actor index a pipe has had
    since 14 -- so both join by the save's OWN identity for the piece. That is the whole
    point of the column: the geometric match this replaced was unique for 75% of belt runs,
    and a 75%-accurate id printed as a fact is a confident wrong claim about which belt to go
    and look at. There is no tolerance here to loosen, because no distance is measured.

    2,187 of the 2,198 contracted runs are named. The 11 that are not are named in the test
    below, which is where the residue is argued rather than merely tolerated.
    """
    graph = build_physical_graph(projection, game)
    belts = [link for link in graph.links if link.medium == ports.CONVEYOR]
    pipes = [link for link in graph.links if link.medium == ports.PIPE]
    assert (len(belts), len(pipes)) == (1916, 282)

    assert all(link.ident.startswith("pipe:") for link in pipes)
    assert sum(1 for link in pipes if link.ident) == 282, "every pipe run is named"
    assert all(link.ident.startswith("chain:") for link in belts if link.ident)
    assert sum(1 for link in belts if link.ident) == 1905

    # No two runs may answer to one id, across BOTH media at once: an ident is what a reader
    # hands back to ``show_on_map``, so a collision sends two different belts to one place.
    idents = [link.ident for link in graph.links if link.ident]
    assert len(set(idents)) == len(idents) == 2187


def test_the_only_unnamed_runs_are_the_belts_the_save_draws_no_line_for(projection, game) -> None:
    """The residue, named -- 11 runs, and not one of them is a join that FAILED.

    A belt is named by its chain, and these eleven pieces are in ``graph["actors"]`` with real
    couplings at both ends while appearing in no ``FGConveyorChainActor`` at all, so the
    projection holds no drawn line for them and there is nothing to name. All eleven hang off
    the FICSMAS gift trees, which §6.15 already found sitting outside the placement tables.

    The distinction this pins is the one the whole column was added for: an id that is absent
    because the save states nothing is honest, and an id guessed from the nearest chain is
    not.
    """
    graph = build_physical_graph(projection, game)
    unnamed = [link for link in graph.links if not link.ident]
    assert len(unnamed) == 11
    assert all(link.medium == ports.CONVEYOR for link in unnamed)
    assert all(link.pieces == 1 for link in unnamed)
    # Every one of them touches a gift tree, directly or through the mergers they feed.
    trees = {"Build_TreeGiftProducer_C", "Build_ConveyorAttachmentMerger_C"}
    for link in unnamed:
        ends = {actor_class(e) for e in (link.source, link.target) if e}
        assert ends <= trees, ends

    chains = {seg.actor_index for seg in saverows.iter_belt_segments(projection)}
    for link in unnamed:
        for actor, run in graph.run_of.items():
            if run is link:
                assert actor not in chains, "this piece has a drawn line and still went unnamed"


def test_every_ident_a_contracted_run_prints_is_one_the_drawn_view_answers_to(
    projection, game
) -> None:
    """The loop the ident exists to close, and it could not be tested before schema 20.

    ``logistics`` contracts runs out of the CONNECTION records and ``conduits`` builds them
    from the DRAWN LINE. They are two views of one world, and an ident is only useful if it
    means the same thing in both -- ``factory_health`` prints one from the first view and
    ``resolve_place`` and ``search_conduits`` look it up in the second. An id that resolved
    to nothing would be worse than no id: it reads as a fact and dead-ends.
    """
    graph = build_physical_graph(projection, game)
    drawn = {run.ident for run in build_runs(projection, game)}
    printed = {link.ident for link in graph.links if link.ident}
    assert len(printed) == 2187
    assert printed <= drawn, sorted(printed - drawn)[:5]
    # Both prefixes really are exercised, or the containment above is a statement about pipes.
    assert {ident.split(":")[0] for ident in printed} == {"chain", "pipe"}


def test_every_contracted_piece_points_back_at_its_run(projection, game) -> None:
    """``run_of`` is what turns a walk over the raw graph back into the runs it crossed."""
    graph = build_physical_graph(projection, game)
    assert len(graph.run_of) == 3590
    assert {id(link) for link in graph.run_of.values()} == {id(link) for link in graph.links}
    pieces = sum(link.pieces for link in graph.links)
    assert pieces == len(graph.run_of), "a piece counted into a run but not indexed by it"


@pytest.mark.integration
def test_the_live_world_contracts_too(live) -> None:
    """The reference fixture is one save; the shape has to hold on whatever is newest."""
    graph = build_physical_graph(live.projection, live.game)
    assert graph.links, "a world with belts in it contracts to no links at all"
    joined = sum(1 for link in graph.links if link.source and link.target)
    assert joined / len(graph.links) > 0.95

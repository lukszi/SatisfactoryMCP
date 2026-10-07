"""Machine selectors: terms, exclusions, expansion, labels and proposals by index.

On the synthetic two-site world of ``tests.support.synthetic_factories``.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.factories.select import SelectorError
from tests.support.synthetic_factories import (
    BASE_CONCRETE,
    IRON,
    ORPHAN,
    OUTPOST,
    STEEL,
    STEEL_CONCRETE,
    SelectorWorld,
    select_terms,
    slab_projection,
)

pytestmark = pytest.mark.integration


def test_terms_are_anded(synthetic_graph, game, synthetic_projection):
    picked = select_terms(
        ["product:Concrete", "near:-1000,-1210@100"], synthetic_graph, game, synthetic_projection
    )
    assert set(picked) == set(STEEL_CONCRETE)


def test_commas_inside_a_term_are_ored(synthetic_graph, game, synthetic_projection):
    picked = select_terms(
        ["product:Steel Ingot,Iron Ingot"], synthetic_graph, game, synthetic_projection
    )
    assert set(picked) == set(STEEL) | set(IRON) | set(OUTPOST)


def test_a_leading_minus_excludes(synthetic_graph, game, synthetic_projection):
    picked = select_terms(
        ["product:Concrete", "-near:-1000,-1210@100"], synthetic_graph, game, synthetic_projection
    )
    assert set(picked) == set(BASE_CONCRETE)


def test_split_keeps_only_the_largest_site(synthetic_graph, game, synthetic_projection):
    picked = select_terms(
        ["product:Concrete"], synthetic_graph, game, synthetic_projection, split=True
    )
    assert set(picked) == set(STEEL_CONCRETE)


def test_building_matches_the_display_name_not_only_the_class(
    synthetic_graph, game, synthetic_projection
):
    assert set(
        select_terms(["building:Foundry"], synthetic_graph, game, synthetic_projection)
    ) == set(STEEL)
    assert set(
        select_terms(["building:Build_FoundryMk1_C"], synthetic_graph, game, synthetic_projection)
    ) == set(STEEL)


def test_near_is_in_metres(synthetic_graph, game, synthetic_projection):
    """The save stores centimetres and every tool in this MCP quotes metres. Getting
    this wrong silently returns everything or nothing."""
    # The foundries sit at -1000,-1200 m and every 10 m eastward from there.
    assert set(
        select_terms(
            ["building:Foundry", "near:-1000,-1200@50"], synthetic_graph, game, synthetic_projection
        )
    ) == set(STEEL)
    assert select_terms(
        ["building:Foundry", "near:-1000,-1200@15"], synthetic_graph, game, synthetic_projection
    ) == sorted(STEEL[:2])
    # Read as centimetres this would still match everything; read as metres it does not.
    assert select_terms(
        ["building:Foundry", "near:-1000,-1200@5"], synthetic_graph, game, synthetic_projection
    ) == [STEEL[0]]


def test_selectors_explain_themselves_when_they_fail(synthetic_graph, game, synthetic_projection):
    with pytest.raises(SelectorError, match="nothing is making"):
        select_terms(["product:Turbo Motor"], synthetic_graph, game, synthetic_projection)
    with pytest.raises(SelectorError, match="not a selector"):
        select_terms(["steel factory"], synthetic_graph, game, synthetic_projection)
    with pytest.raises(SelectorError, match="out of range"):
        select_terms(["base:99"], synthetic_graph, game, synthetic_projection)
    with pytest.raises(SelectorError, match="add something to start from"):
        select_terms(["-product:Concrete"], synthetic_graph, game, synthetic_projection)


def test_a_machine_selector_takes_ids_and_refuses_the_ones_that_do_not_exist(
    synthetic_graph, game, synthetic_projection
):
    """It used to return whatever string it was handed, so a typo, a stale id and a
    machine standing right there all produced the same "0 machines" answer."""
    from satisfactory_mcp.domain.factories.select import SELECTOR_HELP

    picked = select_terms(
        [f"machine:{STEEL[0]},{STEEL[1]}"], synthetic_graph, game, synthetic_projection
    )
    assert set(picked) == set(STEEL[:2])
    with pytest.raises(SelectorError, match="no machine"):
        select_terms(
            ["machine:Build_FoundryMk1_C_9999"], synthetic_graph, game, synthetic_projection
        )
    assert "machine:" in SELECTOR_HELP, "a selector nothing documents is a selector nobody uses"


def test_label_and_near_label_resolve_through_the_store(
    synthetic_graph, game, synthetic_projection
):
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    assert set(
        select_terms(["label:steel factory"], synthetic_graph, game, synthetic_projection, store)
    ) == set(STEEL)
    near = select_terms(
        ["product:Concrete", "near:steel factory@100"],
        synthetic_graph,
        game,
        synthetic_projection,
        store,
    )
    assert set(near) == set(STEEL_CONCRETE)


def test_expand_pulls_in_the_whole_belt_component(synthetic_graph, game, synthetic_projection):
    """Some factories are defined by what feeds them. The player's concrete setup is a
    miner into storage into one constructor -- no product or radius term describes it,
    but the belt component delimits it exactly."""
    picked = select_terms(
        ["product:Concrete"], synthetic_graph, game, synthetic_projection, expand=True
    )
    assert set(STEEL) <= set(picked), "the belt web reaches the foundries"
    assert ORPHAN not in picked, "an unconnected machine has no component to expand into"


def test_exclusions_are_applied_after_expanding(synthetic_graph, game, synthetic_projection):
    """Otherwise expansion would silently undo the exclusion that was the whole point
    of writing '-label:...'."""
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    picked = select_terms(
        ["product:Concrete", "-label:steel factory"],
        synthetic_graph,
        game,
        synthetic_projection,
        store,
        expand=True,
    )
    assert not set(STEEL) & set(picked)


def test_proposal_selector_names_exactly_what_was_proposed():
    """The workflow is propose-then-name, so a proposal has to be selectable. Rebuilding
    it by hand from a centroid and a radius does not work: on the real save,
    near:-442,-1406@120 around a 15-machine proposal picked up 137 machines, 82 of them
    belonging to the factory next door."""
    from satisfactory_mcp.domain.factories.cohere import Proposal
    from satisfactory_mcp.domain.factories.select import select_machines

    proposals = [
        Proposal(machines=sorted(STEEL)),
        Proposal(machines=sorted(STEEL_CONCRETE)),
    ]
    graph = build_graph(slab_projection())
    world = SelectorWorld(graph, projection=slab_projection(), proposals=proposals)
    picked = select_machines(["proposal:1"], world)
    assert picked == sorted(STEEL_CONCRETE)


def test_proposal_selector_reports_a_bad_index():
    from satisfactory_mcp.domain.factories.cohere import Proposal
    from satisfactory_mcp.domain.factories.select import SelectorError, select_machines

    graph = build_graph(slab_projection())
    with pytest.raises(SelectorError, match="out of range"):
        select_machines(["proposal:9"], SelectorWorld(graph, proposals=[Proposal(machines=["a"])]))


def test_index_selectors_are_documented_as_volatile():
    """base:/line:/slab:/proposal: are positions in size-ordered lists rebuilt per call.
    A stale index once re-anchored the speedwire factory onto the aluminium site, so the
    tools that print indices must say so."""
    from satisfactory_mcp import server
    from satisfactory_mcp.domain.factories.select import INDEX_WARNING

    for token in ("base:", "line:", "slab:", "proposal:"):
        assert token in INDEX_WARNING
    assert server.GRAPH_INDEX_WARNING is INDEX_WARNING

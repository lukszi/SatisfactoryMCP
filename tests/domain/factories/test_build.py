"""The factory graph built from a projection's edges: nodes, poles and components.

On the synthetic two-site world of ``tests.support.synthetic_factories``, built by hand
because the committed save fixture predates the graph field.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio.records import actor_class
from satisfactory_mcp.domain.factories import candidates
from tests.support.synthetic_factories import (
    IRON,
    ORPHAN,
    OUTPOST,
    POLE,
    STEEL,
    TOWER,
)

pytestmark = pytest.mark.integration


def test_actor_class_strips_the_instance_id_but_not_the_class_suffix():
    assert actor_class("Build_ConstructorMk1_C_2147441119") == "Build_ConstructorMk1_C"
    # No trailing number: leave it alone rather than eating "_C".
    assert actor_class("Build_ConstructorMk1_C") == "Build_ConstructorMk1_C"


def test_a_machine_wired_to_nothing_is_still_a_node(synthetic_graph):
    """The interned actor list comes from EDGES, so an isolated machine would vanish
    -- and an isolated machine is precisely what a coverage report must surface."""
    assert ORPHAN in synthetic_graph.cls
    assert ORPHAN in synthetic_graph.machines()


def test_poles_and_towers_are_not_machines(synthetic_graph):
    assert synthetic_graph.kind(POLE) == "pole"
    assert synthetic_graph.kind(TOWER) == "tower"
    assert synthetic_graph.towers() == {TOWER}
    assert POLE not in synthetic_graph.machines()


def test_material_components_cannot_split_a_grown_together_base(synthetic_graph):
    """The premise of the whole design: one belt web means one component, however
    many distinct factories the player built inside it."""
    comps = synthetic_graph.machine_components("material")
    biggest = max(comps, key=len)
    assert set(STEEL) <= set(biggest)
    assert set(IRON) <= set(biggest)


def test_dropping_towers_separates_the_outpost(synthetic_graph):
    with_towers = candidates.bases.__wrapped__ if hasattr(candidates.bases, "__wrapped__") else None
    assert with_towers is None  # bases() is plain; guard against it growing a cache
    islands = candidates.bases(synthetic_graph)
    assert len(islands) == 2, islands
    assert set(OUTPOST) in [set(i) for i in islands]
    # Without the skip, the tower welds them into one.
    assert len(synthetic_graph.machine_components("power")) == 1

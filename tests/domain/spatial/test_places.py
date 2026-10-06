"""One place vocabulary, asserted on both sides of the ``near:`` grammar.

The two selector languages used to take disjoint sets of places behind identical
syntax -- a coordinate or ``me`` for nodes, a coordinate or a factory name for machines
-- so a term copied from one failed in the other for no reason a reader could see. Both
now resolve through ``spatial.places.resolve_place``, and this file is what keeps the
two sides from drifting apart again: every kind is exercised against both.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.factories.select import SelectorError, select_machines
from satisfactory_mcp.domain.planning.stored.store import Plan, PlanStore
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.nodes.selectors import select_nodes
from satisfactory_mcp.domain.spatial.places import resolve_place
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration

#: A label and a plan of this suite's own, because the fixture world's id matches a real
#: one: read off disk, these two facets would be whatever the reader has named today.
PROBE_LABEL = "probe factory"
PROBE_PLAN = "probe plan"

RADIUS_M = 600.0


class _Fixed(WorldState):
    """The fixture world with labels and plans this file controls."""

    def __init__(self, base: WorldState, labels: LabelStore, plans: PlanStore):
        super().__init__(projection=base.projection, game=base.game)
        self._labels, self._plans = labels, plans

    @property
    def labels(self):  # type: ignore[override]
        return self._labels

    @property
    def plans(self):  # type: ignore[override]
        return self._plans


def _machine_positions(projection: dict) -> dict[str, tuple[float, float]]:
    out = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            if record.get("pos"):
                out[record["instance"].rsplit(".", 1)[-1]] = tuple(record["pos"][:2])
    return out


@pytest.fixture(scope="module")
def world(state) -> _Fixed:
    labels = LabelStore(world_id="PLACES")
    # Anchored on machines that really stand in the fixture save, so the centroid this
    # label resolves to is a place and not an invented coordinate.
    labels.put(PROBE_LABEL, sorted(_machine_positions(state.projection))[:6])
    plans = PlanStore(
        world_id="PLACES",
        plans=[
            Plan(
                name=PROBE_PLAN,
                siting={"origin_m": [-470.0, -1480.0], "footprint_m": [96.0, 64.0]},
            )
        ],
    )
    return _Fixed(state, labels, plans)


@pytest.fixture(scope="module")
def a_node_id() -> str:
    """One resource node id, picked the way a reader picks one: off the printed table."""
    table = nodes_mod.load_nodes()
    return min(n["instance"].rsplit(".", 1)[-1] for n in table.nodes)


@pytest.fixture(scope="module")
def places(world, a_node_id) -> list[str]:
    return [
        "-470,-1480",
        "me",
        "player",
        "here",
        PROBE_LABEL,
        "slab:1",
        world.conduit_runs[0].ident,
        f"node:{a_node_id}",
        f"machine:{min(_machine_positions(world.projection))}",
        f"plan:{PROBE_PLAN}",
    ]


def test_every_place_kind_resolves_to_a_point(world, places):
    """The list below IS the vocabulary; a kind that stopped resolving fails here first."""
    for place in places:
        point, where = resolve_place(world, place)
        assert len(point) == 2 and where, place


def test_a_node_selector_takes_every_place(world, places):
    table = nodes_mod.load_nodes()
    for place in places:
        centre, _ = resolve_place(world, place)
        sel = select_nodes([f"near:{place}@{RADIUS_M:g}"], table.nodes, st=world)
        assert not sel.errors, (place, sel.errors)
        assert {n["instance"] for n in sel.nodes} == {
            n["instance"]
            for n in table.nodes
            if geo.distance_m((n["x"], n["y"]), centre) <= RADIUS_M
        }, place


def test_a_machine_selector_takes_every_place(world, places):
    pos = _machine_positions(world.projection)
    for place in places:
        centre, _ = resolve_place(world, place)
        picked = select_machines([f"near:{place}@{RADIUS_M:g}"], world)
        assert set(picked) == {
            m
            for m in world.graph.machines()
            if m in pos and geo.distance_m(pos[m], centre) <= RADIUS_M
        }, place


def test_at_least_one_place_of_each_kind_actually_selects_something(world, places):
    """Two empty sets compare equal, so the test above would pass on a resolver that
    always answered with the map's far corner. Somewhere in the list, both sides hit."""
    table = nodes_mod.load_nodes()
    assert any(
        select_nodes([f"near:{p}@{RADIUS_M:g}"], table.nodes, st=world).nodes for p in places
    )
    assert any(select_machines([f"near:{p}@{RADIUS_M:g}"], world) for p in places)


def test_an_unresolvable_place_names_the_kinds_that_would_have_worked(world):
    """Rule 6: nothing widens its scope to cover a term it could not read."""
    sel = select_nodes(["near:nowhere at all@100"], nodes_mod.load_nodes().nodes, st=world)
    assert sel.nodes == []
    assert any("node:<id>" in e and "plan:<name>" in e for e in sel.errors), sel.errors

    with pytest.raises(SelectorError, match="node:<id>"):
        select_machines(["near:nowhere at all@100"], world)


def test_the_place_kinds_that_need_no_save_still_resolve_without_one():
    """A coordinate and a node id are map facts; the rest say what they are missing."""
    table = nodes_mod.load_nodes()
    ident = min(n["instance"].rsplit(".", 1)[-1] for n in table.nodes)
    assert select_nodes([f"near:node:{ident}@200"], table.nodes).nodes
    assert not select_nodes(["near:-470,-1480@200"], table.nodes).errors

    for place, expected in (
        ("me", "player pawn"),
        ("slab:1", "readable save"),
        ("chain:0", "readable save"),
        (f"plan:{PROBE_PLAN}", "readable save"),
    ):
        sel = select_nodes([f"near:{place}@200"], table.nodes)
        assert any(expected in e for e in sel.errors), (place, sel.errors)


def test_a_machine_place_centres_on_that_machine(world):
    leaf, at = min(_machine_positions(world.projection).items())
    assert resolve_place(world, f"machine:{leaf}") == (at, f"machine:{leaf}")
    with pytest.raises(ValueError, match="no machine"):
        resolve_place(world, "machine:Build_Nothing_C_0")


def test_a_node_place_and_the_node_selector_name_the_same_node(world, a_node_id):
    """``node:`` means one node on both sides -- picked OUT of a field by the node
    selector, and centred ON by the place resolver."""
    table = nodes_mod.load_nodes()
    centre, _ = resolve_place(world, f"node:{a_node_id}")
    picked = select_nodes([f"node:{a_node_id}"], table.nodes)
    assert len(picked.nodes) == 1
    assert (picked.nodes[0]["x"], picked.nodes[0]["y"]) == centre

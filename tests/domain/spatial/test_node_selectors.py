"""The source-selector DSL: which resource nodes a list of location and filter terms picks."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.select import select_nodes

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def node_table():
    return nodes_mod.load_nodes()


def test_no_spec_means_whole_map(node_table):
    sel = select_nodes(None, node_table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == len(node_table.nodes)


def test_region_selector(node_table):
    sel = select_nodes(["region:Spire Coast", "resource:Desc_LiquidOil_C"], node_table.nodes)
    assert not sel.whole_map
    assert len(sel.nodes) == 6  # test_region_map's oil test says where the other seven went
    assert not sel.errors


def test_bare_region_name_works(node_table):
    sel = select_nodes(["Spire Coast"], node_table.nodes)
    assert len(sel.nodes) > 0
    assert not sel.errors


def test_node_id_selector_accepts_short_and_full(node_table):
    node = node_table.by_resource("Desc_LiquidOil_C")[0]
    short = node["instance"].rsplit(".", 1)[-1]
    for spec in (f"node:{short}", f"node:{node['instance']}", short):
        sel = select_nodes([spec], node_table.nodes)
        assert [n["instance"] for n in sel.nodes] == [node["instance"]], spec


def test_multiple_node_ids_union(node_table):
    oil = node_table.by_resource("Desc_LiquidOil_C")[:3]
    spec = [f"node:{n['instance'].rsplit('.', 1)[-1]}" for n in oil]
    sel = select_nodes(spec, node_table.nodes)
    assert len(sel.nodes) == 3


def test_a_pin_without_a_save_is_refused_not_read_as_a_region(node_table):
    sel = select_nodes(["pin:1"], node_table.nodes)
    assert sel.nodes == [] and not sel.whole_map
    assert sel.errors == ["pin:1 needs a readable save to resolve"]
    sel = select_nodes(["pin:x"], node_table.nodes)
    assert sel.errors and sel.errors[0].startswith("'pin:x' is not a pin")


def test_near_selector(node_table):
    sel = select_nodes(["near:0,0@1000"], node_table.nodes)
    for n in sel.nodes:
        assert geo.distance_m((n["x"], n["y"]), (0.0, 0.0)) <= 1000


def test_bbox_selector(node_table):
    sel = select_nodes(["bbox:-100,-100,100,100"], node_table.nodes)
    for n in sel.nodes:
        assert -10_000 <= n["x"] <= 10_000
        assert -10_000 <= n["y"] <= 10_000


def test_grid_selector(node_table):
    sel = select_nodes(["grid:X3Y4"], node_table.nodes)
    assert sel.nodes
    for n in sel.nodes:
        assert geo.grid_cell(n["x"], n["y"]) == "X3Y4"


def test_direction_selector(node_table):
    sel = select_nodes(["north"], node_table.nodes)
    assert sel.nodes
    assert all(n["y"] < 400_000 for n in sel.nodes)
    assert len(sel.nodes) < len(node_table.nodes)


def test_filters_intersect_locations(node_table):
    both = select_nodes(["north", "resource:Desc_LiquidOil_C", "purity:pure"], node_table.nodes)
    assert both.nodes
    assert all(n["purity"] == "pure" for n in both.nodes)
    assert all(n["resource"] == "Desc_LiquidOil_C" for n in both.nodes)


def test_locations_union(node_table):
    a = select_nodes(["grid:X3Y4"], node_table.nodes)
    b = select_nodes(["grid:X3Y5"], node_table.nodes)
    both = select_nodes(["grid:X3Y4", "grid:X3Y5"], node_table.nodes)
    assert len(both.nodes) == len(a.nodes) + len(b.nodes)


def test_failed_location_returns_nothing_not_the_whole_map(node_table):
    """The dangerous failure mode: a typo'd region must not quietly widen the scope
    to the entire map, which would answer a completely different question."""
    sel = select_nodes(["region:Northern Forrest"], node_table.nodes)
    assert sel.nodes == []
    assert not sel.whole_map
    assert any("unknown region" in e for e in sel.errors)


def test_filters_only_still_means_whole_map(node_table):
    """A filter with no location is a legitimate whole-map query."""
    sel = select_nodes(["resource:Desc_LiquidOil_C"], node_table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == 48


def test_unknown_node_is_reported(node_table):
    sel = select_nodes(["node:BP_DoesNotExist"], node_table.nodes)
    assert sel.nodes == []
    assert any("unknown node" in e for e in sel.errors)


def test_malformed_near_is_reported(node_table):
    sel = select_nodes(["near:1,2"], node_table.nodes)
    assert any("near:" in e for e in sel.errors)

"""The advisory region-name layer: which named region a point or node stands in.

Each defect section pins one of the four defects the shipped region dataset had, so a
regenerated dataset that reintroduces one fails loudly.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.regions import load_regions

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def region_map():
    return load_regions()


@pytest.fixture(scope="module")
def node_table():
    return nodes_mod.load_nodes()


# ------------------------------------------------------- defect 1: void class


def test_ocean_returns_no_name(region_map):
    """The original raster labelled every cell, so open ocean north-west of the map
    came back as 'Rocky Desert'."""
    label = region_map.label_for(-300_000, -350_000)
    assert label.name is None
    assert label.confidence == "void"
    assert label.describe() == "off-map or ocean"


def test_off_grid_returns_no_name(region_map):
    assert region_map.label_for(10_000_000, 10_000_000).name is None


def test_land_is_still_labelled(region_map):
    """The void mask must not eat real land."""
    label = region_map.label_for(23_900, -192_800)  # a tapped northern oil field
    assert label.name == "Spire Coast"
    assert label.certain


def test_void_cells_exist_but_are_a_minority(region_map):
    total = region_map.column_count * region_map.row_count
    void = sum(row.count(".") for row in region_map.grid)
    assert 0 < void < total * 0.5


# ----------------------------------------------- defect 2: bbox/raster agreement


def test_every_raster_cell_lies_inside_its_regions_bbox(region_map):
    """The shipped bboxes disagreed with the raster for 11 of 21 regions, so a
    bbox-AND-raster test returned False for points the raster itself assigned.
    Recomputing bboxes from the raster makes this hold by construction."""
    for name, info in region_map.regions.items():
        x1, y1, x2, y2 = info["bbox"]
        letter = info["letter"]
        for j in range(region_map.row_count):
            for i in range(region_map.column_count):
                if region_map.grid[j][i] != letter:
                    continue
                cx = region_map.x0_cm + (i + 0.5) * region_map.cell_cm
                cy = region_map.y0_cm + (j + 0.5) * region_map.cell_cm
                assert x1 <= cx <= x2 and y1 <= cy <= y2, f"{name} cell ({i},{j})"


# ------------------------------------------- defect 3: confidence, now measured


def test_boundary_cells_are_flagged_not_hidden(region_map):
    codes = {c for row in region_map.confidence for c in row}
    assert "b" in codes  # boundary cells are marked
    assert "l" in codes  # interior cells exist too


def test_a_label_anchor_lands_on_its_own_regions_ground(region_map):
    """A centroid is a mean and a mean can land in the neighbour: Titan Forest's sits in
    the Swamp, so a tool quoting it sends the player to a coordinate the map paints as
    somewhere else. Measured against the PUBLISHED grid, because that is the raster the
    map draws and the anchor has to agree with what the reader sees."""
    letters = {name: ch for ch, name in region_map.legend.items()}
    for name in region_map.names():
        anchor = region_map.label_anchor(name)
        assert anchor is not None, name
        at = region_map.cell_of(*anchor)
        assert at is not None, (name, anchor)
        assert region_map.grid[at[1]][at[0]] == letters[name], (name, anchor)


def test_a_concave_regions_anchor_leaves_its_centroid(region_map):
    """The case the anchor exists for. If a regenerated raster ever makes every centroid
    land on its own ground this test is free to be deleted -- but silently agreeing with
    the centroid everywhere would mean the correction stopped running."""
    moved = [
        name
        for name in region_map.names()
        if region_map.label_anchor(name) != tuple(region_map.regions[name]["centroid"])
    ]
    assert moved, "no region's centroid misses its own ground, so nothing is being corrected"


def test_an_unknown_region_has_no_anchor(region_map):
    assert region_map.label_anchor("Nowhere At All") is None


def test_a_node_is_named_by_where_it_stands_and_nothing_else(region_map, node_table):
    """The override table is gone, and this is what replaced it.

    It held 48 oil nodes whose region a human had read off the wiki's biome image and which
    were trusted over the raster, reported as ``verified``. Two of them are checked here:
    ``BP_ResourceNode86`` and ``BP_ResourceNode88``, which the override called Western
    Beaches while the trace's own raster said Jungle Spires -- two readings of the same wiki
    disagreeing about the same node.

    The game names neither. Its own map areas put those two on Rocky Desert and No Man's
    Land, and a position lookup is now the whole answer: ``label_for_node`` agrees with
    ``label_for`` at the same coordinates, by construction.
    """
    got = {}
    for short in ("BP_ResourceNode86", "BP_ResourceNode88"):
        node = next(n for n in node_table.nodes if n["instance"].endswith("." + short))
        raster = region_map.label_for(node["x"], node["y"])
        final = region_map.label_for_node(node)
        assert (final.name, final.confidence) == (raster.name, raster.confidence)
        got[short] = final.name
    assert got == {"BP_ResourceNode86": "Rocky Desert", "BP_ResourceNode88": "No Man's Land"}
    assert "verified" not in {region_map.label_for_node(n).confidence for n in node_table.nodes}


def test_the_wiki_only_region_names_are_gone(region_map):
    """Three names the retired trace carried that the game puts nowhere on the map.

    Western Beaches and Snaketree Forest are not the game's words at all. Eastern Dune
    Forest is: an ``Area_EasternDuneForest_1`` asset exists and states that display name,
    and the map-area raster never references it, so the game has the name and no ground
    under it. All three must fail to resolve, or a selector would match nothing and read as
    "no nodes there".
    """
    for name in ("Western Beaches", "Snaketree Forest", "Eastern Dune Forest"):
        assert region_map.resolve(name) is None, name
        assert region_map.nodes_in_region([], name) == []


def test_oil_bearing_regions(region_map, node_table):
    """13 crude nodes were Spire Coast under the wiki trace and 6 are under the game's.

    The trace drew one coastal ring across the whole north; the game draws a much smaller
    Spire Coast and gives the rest of that ring to Rocky Desert, Desert Canyons, Dune Desert
    and Swamp. Six of the thirteen stayed, six became Rocky Desert and one Desert Canyons --
    measured, and the reason this suite plans over a box rather than a region name.
    """
    oil = node_table.by_resource("Desc_LiquidOil_C")
    named = {region_map.label_for_node(n).name for n in oil}
    named.discard(None)
    assert "Spire Coast" in named
    assert len(region_map.nodes_in_region(oil, "Spire Coast")) == 6
    # And the crude that left it is accounted for rather than merely absent.
    assert len(region_map.nodes_in_region(oil, "Rocky Desert")) == 12
    assert len(region_map.nodes_in_region(oil, "Desert Canyons")) == 1


# --------------------------------------------------------- name resolution


def test_resolve_is_case_insensitive_and_accepts_prefixes(region_map):
    assert region_map.resolve("spire coast") == "Spire Coast"
    assert region_map.resolve("Northern For") == "Northern Forest"
    assert region_map.resolve("Nonexistent") is None

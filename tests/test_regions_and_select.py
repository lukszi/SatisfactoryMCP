"""The advisory region-name layer and the source-selector DSL.

Each test here pins one of the four defects the shipped region dataset had, so a
regenerated dataset that reintroduces one fails loudly.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.regions import load_regions
from satisfactory_mcp.domain.spatial.select import select_nodes
from tests.support.paths import REPO_ROOT

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rm():
    return load_regions()


@pytest.fixture(scope="module")
def table():
    return nodes_mod.load_nodes()


# ------------------------------------------------- node table completeness


def test_node_table_matches_the_save_in_both_directions(table, projection):
    """The join must be checked BOTH ways.

    The original check only asserted that every table entry appears in the save,
    which passed at 607/607 while the table was silently missing a node the save
    contained -- a pure Limestone node absent from the SCIM extract. Counting save
    actors against table rows is what caught it.
    """
    counts = projection["building_counts"]
    by_kind: dict[str, int] = {}
    for n in table.nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    assert by_kind["node"] == counts["BP_ResourceNode_C"]
    assert by_kind["well_sat"] == counts["BP_FrackingSatellite_C"]
    assert by_kind["geyser"] == counts["BP_ResourceNodeGeyser_C"]


#: Where the recovered pure Limestone node stands, centimetres. See the test below for
#: why this is the anchor and an instance name is not.
_RECOVERED_LIMESTONE_XY = (-279_020.0, -186_294.0)


def test_the_recovered_limestone_node_is_present(table):
    """A pure Limestone node that the third-party extract omitted is in this table.

    The node it protects is the one recovered from the game's own assets: the SCIM extract
    was missing it, both counts read 607, and only counting save actors against table rows
    caught it. That requirement is unchanged and is what is asserted here.

    What changed is the ANCHOR. This test used to name ``BP_ResourceNode11``, and a game
    update renamed that instance -- it exists in no current save, so the assertion had
    quietly become a claim about a build nobody runs. An instance name is exactly the wrong
    identity for a node that a map change can rename, which is the failure the skew gate
    exists to make loud. Position, resource and purity are what actually survive: the
    installed build confirms both attributes on every compared row, and the renamed row
    moved 150 cm, so a metres-wide tolerance around the recorded spot still names one node
    and only one. The nearest other pure Limestone node is 51 m away.
    """
    hits = [
        n
        for n in table.nodes
        if n["resource"] == "Desc_Stone_C"
        and n["purity"] == "pure"
        and geo.distance_m((n["x"], n["y"]), _RECOVERED_LIMESTONE_XY) <= 10.0
    ]
    assert len(hits) == 1, [n["instance"] for n in hits]
    assert hits[0]["kind"] == "node"


def test_the_recovered_limestone_node_is_joinable_or_disclosed(table, projection):
    """The other half of the old docstring: "and in the save".

    That clause is no longer true of the id the table ships, and it is the reason the
    count check above cannot see the problem -- 459 stays 459 across a rename. So the
    requirement becomes a disjunction, and either branch is acceptable: the row's instance
    name is one this save can join, OR the software says out loud that it cannot. What is
    NOT acceptable is a row whose name no save carries while every tool stays quiet,
    because that is the case that answers confidently and wrongly.
    """
    node = next(
        n
        for n in table.nodes
        if n["resource"] == "Desc_Stone_C"
        and n["purity"] == "pure"
        and geo.distance_m((n["x"], n["y"]), _RECOVERED_LIMESTONE_XY) <= 10.0
    )
    skew = nodes_mod.skew_for_save(projection["header"], table)
    disclosed = nodes_mod.identity_notes(skew, [node["instance"]])
    unjoinable = node["instance"] in (skew.unjoinable if skew else ())

    if unjoinable:
        assert disclosed, "the row this save cannot join is reported by nothing"
        assert node["instance"].rsplit(".", 1)[-1] in disclosed[0]
        # And the disclosure must lead somewhere: a name with no replacement is a dead
        # end for anyone trying to find the node in their own save.
        assert skew.renamed_to.get(node["instance"]), skew
    else:
        assert disclosed == [], disclosed


def test_deposits_and_cores_are_excluded_from_the_node_table(table):
    """A deposit is hand-mineable only and a fracking core produces nothing, so
    neither may advertise capacity."""
    kinds = {n["kind"] for n in table.nodes}
    assert kinds == {"node", "well_sat", "geyser"}


def test_every_well_satellite_keeps_its_core_link(table):
    """Well rate is summed per core, so a satellite without one is unplannable."""
    sats = [n for n in table.nodes if n["kind"] == "well_sat"]
    assert sats
    assert all(n["well_core"] for n in sats)


def test_sources_are_recorded_with_licences(table):
    """The source names its provenance and the game build it was read at.

    Two literals have died here, each for the right reason. ``"1.2.0.0" in ...`` described
    the GPL-3.0 SCIM table that supplied the satellite/core mapping; ``licence == "MIT"``
    described the vendored rockfactory node set that supplied everything else. Both are
    deleted: the node set, resource, purity, position AND the well link now come from
    ``data/world_resource_nodes.json``, this repository's own extraction from the installed
    game's map package, so the claims pinned are the new ones -- first-party, no external
    licence or attribution obligation, and still pinned to the build it was read at so a
    consumer can tell whether the table predates the build they are running. The
    requirement that provenance stays RECORDED is the part that has never changed.
    """
    sources = table.meta["sources"]
    primary = sources["primary"]
    assert primary["name"] == "data/world_resource_nodes.json"
    assert "first-party" in primary["licence"]
    assert "no external licence" in primary["licence"]
    assert "no attribution" in primary["licence"]
    assert "502094" in primary["game_version_pinned"]
    # And the two retired sources stay on the record rather than being tidied away.
    assert "retired_mit_table" in sources["retired"]
    assert "GPL" in sources["retired"]


def test_purity_and_resource_agree_with_the_installed_game(table):
    """Purity and resource are the installed game's own, via the projection this table is.

    These assertions have meant three things in their life. First "agrees with the SCIM
    table", then -- SCIM deleted -- "agrees with ``mPurity``/``mResourceClass`` in the
    installed build's ``Persistent_Level.umap``", recorded as mismatch lists because the
    node set itself was still third-party. Now the third party is gone too and the node set
    IS that package read (``data/world_resource_nodes.json``), so recorded mismatch lists
    against it would be the generator agreeing with itself. What is checked instead is the
    same claim with no generator in the loop: the served table re-joined against the
    committed world table, row for row, on identity, resource and purity. Geysers are the
    one mapped value -- the asset carries no ``mResourceClass``, and the synthetic label
    says so in ``_meta.geyser_note``.
    """
    world = json.loads(
        (REPO_ROOT / "data" / "world_resource_nodes.json").read_text(encoding="utf-8")
    )
    rows = {r["id"]: r for r in world["nodes"] if r["class"] != "BP_FrackingCore_C"}
    assert {n["instance"].rsplit(".", 1)[-1] for n in table.nodes} == set(rows)
    for n in table.nodes:
        src = rows[n["instance"].rsplit(".", 1)[-1]]
        assert n["purity"] == src["purity"], n["instance"]
        assert n["resource"] == (src["resource"] or "Desc_Geyser_C"), n["instance"]
        if n["kind"] == "well_sat":
            assert n["well_core"].rsplit(".", 1)[-1] == src["core"], n["instance"]
    assert "geyser" in table.meta["geyser_note"].casefold()


def test_position_deltas_are_recorded_against_both_builds(table):
    """One comparison now, because there is only one build in play -- and it must be exact.

    The two-block record this name still remembers ("against the build this table was cut
    from" AND "against the installed build") existed because the vendored node set was cut
    from an older build than the one installed, and a single delta could not be honest
    about that. The table is now cut FROM the installed build, so a second block would be
    manufacturing a build to lag. What survives is every property that made the record
    trustworthy: the one block declares its rounding floor, its measured worst delta sits
    inside that floor, no row is named past it (a summary must never outrun its rows),
    nothing is missing on either side, and the build, method and date stamps are present.
    These are exactly the preconditions under which the skew gate in
    ``domain/spatial/nodes.py`` finds a pin and no drift, and stays silent -- which
    ``test_node_table_skew`` asserts against the reference save.
    """
    positions = table.meta["cross_validation"]["positions"]
    assert set(positions) == {"against_the_installed_build"}
    block = positions["against_the_installed_build"]

    floor = block["rounding_floor_cm"]
    worst = block["max_position_delta_cm"]
    assert isinstance(worst, (int, float)), "no measured delta stated"
    assert 0 < floor < 1.0
    assert 0 <= worst <= floor
    assert block["rows_past_the_rounding_floor"] == []
    assert block["rows_only_in_this_table"] == []
    assert block["rows_only_in_the_installed_build"] == []
    assert block["rows_compared"] == len(table.nodes)
    assert block["measured"] and block["build"] and block["method"]
    assert "502094" in block["build"]


# ------------------------------------------------------- defect 1: void class


def test_ocean_returns_no_name(rm):
    """The original raster labelled every cell, so open ocean north-west of the map
    came back as 'Rocky Desert'."""
    label = rm.label_for(-300_000, -350_000)
    assert label.name is None
    assert label.confidence == "void"
    assert label.describe() == "off-map or ocean"


def test_off_grid_returns_no_name(rm):
    assert rm.label_for(10_000_000, 10_000_000).name is None


def test_land_is_still_labelled(rm):
    """The void mask must not eat real land."""
    label = rm.label_for(23_900, -192_800)  # a tapped northern oil field
    assert label.name == "Spire Coast"
    assert label.certain


def test_void_cells_exist_but_are_a_minority(rm):
    total = rm.nx * rm.ny
    void = sum(row.count(".") for row in rm.grid)
    assert 0 < void < total * 0.5


# ----------------------------------------------- defect 2: bbox/raster agreement


def test_every_raster_cell_lies_inside_its_regions_bbox(rm):
    """The shipped bboxes disagreed with the raster for 11 of 21 regions, so a
    bbox-AND-raster test returned False for points the raster itself assigned.
    Recomputing bboxes from the raster makes this hold by construction."""
    for name, info in rm.regions.items():
        x1, y1, x2, y2 = info["bbox"]
        letter = info["letter"]
        for j in range(rm.ny):
            for i in range(rm.nx):
                if rm.grid[j][i] != letter:
                    continue
                cx = rm.x0 + (i + 0.5) * rm.cell
                cy = rm.y0 + (j + 0.5) * rm.cell
                assert x1 <= cx <= x2 and y1 <= cy <= y2, f"{name} cell ({i},{j})"


# ------------------------------------------- defect 3: confidence, now measured


def test_boundary_cells_are_flagged_not_hidden(rm):
    codes = {c for row in rm.confidence for c in row}
    assert "b" in codes  # boundary cells are marked
    assert "l" in codes  # interior cells exist too


def test_a_label_anchor_lands_on_its_own_regions_ground(rm):
    """A centroid is a mean and a mean can land in the neighbour: Titan Forest's sits in
    the Swamp, so a tool quoting it sends the player to a coordinate the map paints as
    somewhere else. Measured against the PUBLISHED grid, because that is the raster the
    map draws and the anchor has to agree with what the reader sees."""
    letters = {name: ch for ch, name in rm.legend.items()}
    for name in rm.names():
        anchor = rm.label_anchor(name)
        assert anchor is not None, name
        at = rm.cell_of(*anchor)
        assert at is not None, (name, anchor)
        assert rm.grid[at[1]][at[0]] == letters[name], (name, anchor)


def test_a_concave_regions_anchor_leaves_its_centroid(rm):
    """The case the anchor exists for. If a regenerated raster ever makes every centroid
    land on its own ground this test is free to be deleted -- but silently agreeing with
    the centroid everywhere would mean the correction stopped running."""
    moved = [
        name for name in rm.names() if rm.label_anchor(name) != tuple(rm.regions[name]["centroid"])
    ]
    assert moved, "no region's centroid misses its own ground, so nothing is being corrected"


def test_an_unknown_region_has_no_anchor(rm):
    assert rm.label_anchor("Nowhere At All") is None


def test_a_node_is_named_by_where_it_stands_and_nothing_else(rm, table):
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
        node = next(n for n in table.nodes if n["instance"].endswith("." + short))
        raster = rm.label_for(node["x"], node["y"])
        final = rm.label_for_node(node)
        assert (final.name, final.confidence) == (raster.name, raster.confidence)
        got[short] = final.name
    assert got == {"BP_ResourceNode86": "Rocky Desert", "BP_ResourceNode88": "No Man's Land"}
    assert "verified" not in {rm.label_for_node(n).confidence for n in table.nodes}


def test_the_wiki_only_region_names_are_gone(rm):
    """Three names the retired trace carried that the game puts nowhere on the map.

    Western Beaches and Snaketree Forest are not the game's words at all. Eastern Dune
    Forest is: an ``Area_EasternDuneForest_1`` asset exists and states that display name,
    and the map-area raster never references it, so the game has the name and no ground
    under it. All three must fail to resolve, or a selector would match nothing and read as
    "no nodes there".
    """
    for name in ("Western Beaches", "Snaketree Forest", "Eastern Dune Forest"):
        assert rm.resolve(name) is None, name
        assert rm.filter_nodes([], name) == []


def test_oil_bearing_regions(rm, table):
    """13 crude nodes were Spire Coast under the wiki trace and 6 are under the game's.

    The trace drew one coastal ring across the whole north; the game draws a much smaller
    Spire Coast and gives the rest of that ring to Rocky Desert, Desert Canyons, Dune Desert
    and Swamp. Six of the thirteen stayed, six became Rocky Desert and one Desert Canyons --
    measured, and the reason this suite plans over a box rather than a region name.
    """
    oil = table.by_resource("Desc_LiquidOil_C")
    named = {rm.label_for_node(n).name for n in oil}
    named.discard(None)
    assert "Spire Coast" in named
    assert len(rm.filter_nodes(oil, "Spire Coast")) == 6
    # And the crude that left it is accounted for rather than merely absent.
    assert len(rm.filter_nodes(oil, "Rocky Desert")) == 12
    assert len(rm.filter_nodes(oil, "Desert Canyons")) == 1


# --------------------------------------------------------- name resolution


def test_resolve_is_case_insensitive_and_accepts_prefixes(rm):
    assert rm.resolve("spire coast") == "Spire Coast"
    assert rm.resolve("Northern For") == "Northern Forest"
    assert rm.resolve("Nonexistent") is None


# ------------------------------------------------------------- selectors


def test_no_spec_means_whole_map(table):
    sel = select_nodes(None, table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == len(table.nodes)


def test_region_selector(table):
    sel = select_nodes(["region:Spire Coast", "resource:Desc_LiquidOil_C"], table.nodes)
    assert not sel.whole_map
    assert len(sel.nodes) == 6  # see test_oil_bearing_regions for where the other seven went
    assert not sel.errors


def test_bare_region_name_works(table):
    sel = select_nodes(["Spire Coast"], table.nodes)
    assert len(sel.nodes) > 0
    assert not sel.errors


def test_node_id_selector_accepts_short_and_full(table):
    node = table.by_resource("Desc_LiquidOil_C")[0]
    short = node["instance"].rsplit(".", 1)[-1]
    for spec in (f"node:{short}", f"node:{node['instance']}", short):
        sel = select_nodes([spec], table.nodes)
        assert [n["instance"] for n in sel.nodes] == [node["instance"]], spec


def test_multiple_node_ids_union(table):
    oil = table.by_resource("Desc_LiquidOil_C")[:3]
    spec = [f"node:{n['instance'].rsplit('.', 1)[-1]}" for n in oil]
    sel = select_nodes(spec, table.nodes)
    assert len(sel.nodes) == 3


def test_a_pin_without_a_save_is_refused_not_read_as_a_region(table):
    sel = select_nodes(["pin:1"], table.nodes)
    assert sel.nodes == [] and not sel.whole_map
    assert sel.errors == ["pin:1 needs a readable save to resolve"]
    sel = select_nodes(["pin:x"], table.nodes)
    assert sel.errors and sel.errors[0].startswith("'pin:x' is not a pin")


def test_near_selector(table):
    sel = select_nodes(["near:0,0@1000"], table.nodes)
    for n in sel.nodes:
        assert geo.distance_m((n["x"], n["y"]), (0.0, 0.0)) <= 1000


def test_bbox_selector(table):
    sel = select_nodes(["bbox:-100,-100,100,100"], table.nodes)
    for n in sel.nodes:
        assert -10_000 <= n["x"] <= 10_000
        assert -10_000 <= n["y"] <= 10_000


def test_grid_selector(table):
    sel = select_nodes(["grid:X3Y4"], table.nodes)
    assert sel.nodes
    for n in sel.nodes:
        assert geo.grid_cell(n["x"], n["y"]) == "X3Y4"


def test_direction_selector(table):
    sel = select_nodes(["north"], table.nodes)
    assert sel.nodes
    assert all(n["y"] < 400_000 for n in sel.nodes)
    assert len(sel.nodes) < len(table.nodes)


def test_filters_intersect_locations(table):
    both = select_nodes(["north", "resource:Desc_LiquidOil_C", "purity:pure"], table.nodes)
    assert both.nodes
    assert all(n["purity"] == "pure" for n in both.nodes)
    assert all(n["resource"] == "Desc_LiquidOil_C" for n in both.nodes)


def test_locations_union(table):
    a = select_nodes(["grid:X3Y4"], table.nodes)
    b = select_nodes(["grid:X3Y5"], table.nodes)
    both = select_nodes(["grid:X3Y4", "grid:X3Y5"], table.nodes)
    assert len(both.nodes) == len(a.nodes) + len(b.nodes)


def test_failed_location_returns_nothing_not_the_whole_map(table):
    """The dangerous failure mode: a typo'd region must not quietly widen the scope
    to the entire map, which would answer a completely different question."""
    sel = select_nodes(["region:Northern Forrest"], table.nodes)
    assert sel.nodes == []
    assert not sel.whole_map
    assert any("unknown region" in e for e in sel.errors)


def test_filters_only_still_means_whole_map(table):
    """A filter with no location is a legitimate whole-map query."""
    sel = select_nodes(["resource:Desc_LiquidOil_C"], table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == 48


def test_unknown_node_is_reported(table):
    sel = select_nodes(["node:BP_DoesNotExist"], table.nodes)
    assert sel.nodes == []
    assert any("unknown node" in e for e in sel.errors)


def test_malformed_near_is_reported(table):
    sel = select_nodes(["near:1,2"], table.nodes)
    assert any("near:" in e for e in sel.errors)

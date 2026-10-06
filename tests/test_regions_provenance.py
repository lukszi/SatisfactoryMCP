"""``data/region_names.json`` says where it came from. This checks it, claim by claim.

The file it replaced was a raster of ``data/satisfactory_regions.json``, a hand trace of
satisfactory.wiki.gg's Biome Map image -- CC BY-SA 4.0, and the one share-alike obligation in
a repository whose licence posture is that no copyleft reaches it. The trace is retired and
that source file is deleted; the geometry and the names now come out of the game's own
``FGMapAreaTexture`` and the ``UFGMapArea`` assets beside it.

Three kinds of claim are pinned here, and they are pinned for three different reasons.

**The licence claim**, by string scan, because it is a claim about the whole file rather than
about one field: nothing in it may credit the wiki, name a CC licence, or describe its
geometry as traced -- except inside ``_meta.retired_wiki_trace``, which is history and has to
survive so the change is on the record rather than tidied away.

**The internal arithmetic**, because a table that contradicts itself answers questions anyway.
Every region's box holds its own cells, the coarse grid is the fine grid's majority, the cell
counts add up, and every ``u`` cell carries the No Man's Land letter.

**The derivation**, against the installed game where there is one. The committed grid must be
this build's raster downsampled -- exactly, not approximately -- and the calibration recorded
in ``_meta`` must still hold when it is measured again.

Almost everything is parametrised per area asset, per region and per grid row, because the
failures worth reading name the thing that moved.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

DATA = REPO_ROOT / "data"
TABLE = json.loads((DATA / "region_names.json").read_text(encoding="utf-8"))
META = TABLE["_meta"]
GRID_META = TABLE["grid_meta"]
LEGEND = TABLE["legend"]
NAME_MAP = META["name_map"]

#: The game's own name for ground it does not otherwise name, and the one label in the legend
#: that is not a biome.
UNNAMED = "No Man's Land"

#: Words that may appear only inside the block that records what was retired. A licence, a
#: source and a method: the three ways a share-alike obligation could come back in.
RETIRED_WORDS = ("cc by-sa", "by-sa", "share-alike", "satisfactory.wiki", "wiki.gg", "biome_map")

#: Where naming the retired trace is allowed, because the change has to stay on the record.
HISTORY_KEY = "retired_wiki_trace"


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


# --------------------------------------------------------------------------------------
# The licence claim.
# --------------------------------------------------------------------------------------


#: A mention outside the history block is allowed only in a sentence that also says the
#: thing is gone. Same shape as the rule the deleted GPL-3.0 parser was held to: the history
#: is worth keeping and the credit is not, and a bare mention is how one turns back into the
#: other.
RETIRED_QUALIFIERS = ("retired", "deleted", "no copyleft", "reaches this file")


@pytest.mark.parametrize("word", RETIRED_WORDS)
def test_the_wiki_trace_is_named_only_as_history(word):
    """It may be remembered and it may not be credited.

    A credit is an obligation. This file is derived from the game's own assets and carries
    none, and the way that stops being true is a stray line describing the geometry as
    traced -- so the scan covers the whole document, exempts one block outright, and
    everywhere else demands that naming it and disowning it happen in the same sentence.
    """
    for key, block in META.items():
        if key == HISTORY_KEY:
            continue
        for s in _strings(block):
            if word not in s.casefold():
                continue
            assert any(q in s.casefold() for q in RETIRED_QUALIFIERS), f"{key}: {s}"
    for key, block in TABLE.items():
        if key == "_meta":
            continue
        assert not [s for s in _strings(block) if word in s.casefold()], key


def test_the_retirement_is_on_the_record_rather_than_tidied_away():
    """The block that is allowed to name the wiki must actually name it, and say what moved.

    An empty exemption is worse than no exemption: it would let the history be deleted while
    the test above kept passing, and the reason this layer was re-derived would be gone.
    """
    history = " ".join(_strings(META[HISTORY_KEY])).casefold()
    assert "cc by-sa" in history
    assert "satisfactory.wiki.gg" in history
    assert "retired" in history or "deleted" in history
    assert META[HISTORY_KEY]["agreement_pct"] == 68.1
    assert META[HISTORY_KEY]["cells_agreeing"] == 273
    assert META[HISTORY_KEY]["cells_comparable_by_name"] == 401


def test_the_source_file_is_gone_rather_than_merely_unused():
    """``data/satisfactory_regions.json`` is deleted. A file nothing reads is still a file
    somebody will read, and this one carried the obligation."""
    assert not (DATA / "satisfactory_regions.json").exists()


def test_the_licence_line_states_first_party_derivation():
    licence = META["licence"].casefold()
    assert "first-party" in licence
    assert "no copyleft" in licence
    assert "no artwork" in licence or "no artwork" in " ".join(_strings(META["source"])).casefold()


def test_the_false_known_limitation_is_gone():
    """The retired file claimed "the game ships no biome geometry". It does; this is it.

    Asserted as an absence with the sentence spelled out, because that claim is exactly the
    kind that gets copied forward into a new file's limitations by habit.
    """
    limitations = " ".join(_strings(META["known_limitations"])).casefold()
    assert "ships no biome geometry" not in limitations
    assert "cannot be regenerated" not in limitations
    assert limitations, "a table with no stated limitations is claiming to have none"


# --------------------------------------------------------------------------------------
# Provenance and staleness.
# --------------------------------------------------------------------------------------


def test_the_build_is_pinned_so_a_consumer_can_tell_it_predates_theirs():
    assert "502094" in META["game_version_pinned"]
    assert "502094" in (META["game_build"] or "")
    assert META["generated"].startswith("20")


def test_the_source_asset_is_named_by_path_and_class():
    source = META["source"]
    assert source["asset"].endswith("MapareatexturePersistentLevel")
    assert source["class"] == "/Script/FactoryGame.FGMapAreaTexture"
    assert "core.gameassets.maparea" in source["read_by"]
    assert source["frame_m"] == {
        "x_min_m": -3247.0,
        "x_max_m": 4253.0,
        "y_min_m": -3750.0,
        "y_max_m": 3750.0,
    }


def test_the_shipped_palette_is_recorded_and_disowned():
    """37 RGBA entries of minimap legend, kept for the record and drawn by nothing.

    Recording it is what lets a reader see that the colours in the asset were looked at and
    rejected rather than never noticed -- and this file emits no colour at all, so there is
    nothing here that could have come from them.
    """
    source = META["source"]
    assert len(source["shipped_palette_rgba"]) == 37
    assert all(len(entry) == 4 for entry in source["shipped_palette_rgba"])
    assert "never drawn" in source["shipped_palette_role"]
    assert "#" not in json.dumps(TABLE), "a hex colour in a table that emits no colour"


def test_the_calibration_holds_and_says_what_it_measured():
    """The corners are measured, not stated in the asset, so the margin is the claim.

    1.97 at the pin against 1.33 for the best rival shift on build 495413. The generator
    refuses to write when this stops holding, which is why the recorded value can be
    asserted rather than merely printed.
    """
    cal = META["calibration"]
    assert cal["pin_holds"] is True
    assert cal["edge_ratio_at_the_pin"] > cal["edge_ratio_at_the_best_rival_shift"]
    assert cal["margin_over_the_best_rival"] >= cal["margin_required"]
    assert cal["metres_per_texel"] == pytest.approx(1.8311, abs=0.001)
    assert "edge strength" in cal["method"]


def test_the_grid_resolution_decision_carries_its_numbers():
    """Two grids is a choice, and a choice with no measurement behind it is a preference."""
    why = META["grids"]["why_64_m"]
    for figure in ("14.13%", "8.33%", "5.30%", "2.61%", "57,600"):
        assert figure in why, figure
    assert "not_exact" in META["grids"]
    assert "pairing" in META["grids"]


def test_the_land_mask_names_its_two_sources_and_claims_no_names():
    """Both mask sources are first-party now: the MIT node table is retired and deleted,
    replaced by ``data/world_resource_nodes.json`` (the game's own map package, read from
    the installed game -- its retirement record is that file's ``_meta.retired_mit_table``,
    pinned by ``test_nodes_provenance``)."""
    mask = META["land_mask"]
    assert set(mask["sources"]) == {
        "data/world_resource_nodes.json",
        "data/world_collectibles.json",
    }
    assert "first-party" in mask["sources"]["data/world_resource_nodes.json"]
    assert "installed game" in mask["sources"]["data/world_resource_nodes.json"]
    assert mask["reference_points"] == sum(mask["categories"].values())
    assert "supplies no name" in mask["role"]


def test_the_override_table_is_recorded_as_removed():
    assert "node_region_overrides" not in TABLE
    assert "gone" in META["node_region_overrides"]


# --------------------------------------------------------------------------------------
# The name map: one entry per area asset the raster reaches.
# --------------------------------------------------------------------------------------


def test_the_name_map_covers_every_asset_and_only_those():
    assert len(NAME_MAP) == 32
    assert set(META["area_display_names"]) == set(NAME_MAP)
    assert set(NAME_MAP.values().__iter__().__next__()) == {
        "package",
        "display_name_key",
        "string_table",
        "display_name",
        "palette_indices",
        "texels",
    }


@pytest.mark.parametrize("asset", sorted(NAME_MAP), ids=lambda a: a)
def test_each_area_asset_states_its_own_name_through_the_games_string_table(asset):
    """Every label is the game's, and the two shortcuts a reader might assume are wrong.

    The display name is NOT derivable from the asset name -- ``Area_Savanna_1`` is Rocky
    Desert -- and it is NOT one per package name, since ``Area_crater_1`` and
    ``Area_crater_2`` are two regions. Both are checked by the tests below; what is checked
    here is that each asset carries a key, that the key came from the one string table the
    game states these in, and that its label is a legend entry with ground under it.
    """
    entry = NAME_MAP[asset]
    assert entry["string_table"] == "World_Data"
    assert entry["display_name_key"].startswith("Locations/")
    assert entry["display_name"] in set(LEGEND.values())
    assert entry["palette_indices"], f"{asset} is in the map with no palette index"
    assert entry["texels"] > 0, f"{asset} is named and covers no texel"
    assert entry["package"].startswith("Area_")
    assert asset.startswith(entry["package"])


def test_the_display_name_is_not_the_asset_name_and_the_difference_is_measured():
    """Four assets whose label the file name would have got wrong.

    Two Savannas that are Rocky Desert, and the two pairs that share a package name and mean
    different places. These are the entire reason the reader resolves a palette index by
    public export hash instead of by package name.
    """
    assert NAME_MAP["Area_Savanna_1"]["display_name"] == "Rocky Desert"
    assert NAME_MAP["Area_Savanna_2"]["display_name"] == "Rocky Desert"
    assert NAME_MAP["Area_crater_1"]["display_name"] == "Blue Crater"
    assert NAME_MAP["Area_crater_2"]["display_name"] == "Crater Lakes"
    assert NAME_MAP["Area_RedJungle_1"]["display_name"] == "Red Jungle"
    assert NAME_MAP["Area_RedJungle_2"]["display_name"] == "Jungle Spires"


def test_two_labels_are_pluralised_and_the_deviation_is_declared():
    """The only place a familiar spelling wins over the game's key, and it is written down."""
    spellings = META["naming_rules"]["spellings"]
    assert set(spellings) == {"Locations/DesertCanyon", "Locations/MazeCanyon"}
    assert spellings["Locations/DesertCanyon"]["emitted"] == "Desert Canyons"
    assert spellings["Locations/MazeCanyon"]["emitted"] == "Maze Canyons"
    # ...and every OTHER label is the key's own words, so the exception list is complete.
    for entry in NAME_MAP.values():
        key = entry["display_name_key"].removeprefix("Locations/")
        if entry["display_name_key"] in spellings:
            continue
        assert key.casefold() == entry["display_name"].replace(" ", "").replace("'", "").casefold()


def test_a_name_the_game_has_and_puts_nowhere_is_recorded_rather_than_dropped():
    """Eastern Dune Forest, which the retired trace carried and the raster never reaches.

    "The game has no such name" and "the game has the name and no ground under it" are
    different answers, and only this distinguishes them.
    """
    unreferenced = META["naming_rules"]["unreferenced_area_assets"]
    assert "Area_EasternDuneForest_1" in unreferenced
    assert "Eastern Dune Forest" not in set(LEGEND.values())


def test_the_no_mans_land_rule_is_stated_and_applied():
    rule = META["naming_rules"]["no_mans_land"].casefold()
    assert "outer coast" in rule
    assert "rather than blanked" in rule
    assert UNNAMED in set(LEGEND.values())
    assert META["void_distance_m"] == 1000.0


# --------------------------------------------------------------------------------------
# The grids: shape, and agreement with each other.
# --------------------------------------------------------------------------------------

GRID = TABLE["region_grid"]
CONF = TABLE["confidence_grid"]
FINE = TABLE["fine_grid"]
FINE_CONF = TABLE["fine_confidence"]
RATIO = int(GRID_META["cell"] / GRID_META["fine_cell"])


def test_the_two_pairs_are_the_shapes_their_own_meta_says():
    assert (len(GRID), len(GRID[0])) == (GRID_META["ny"], GRID_META["nx"])
    assert [len(r) for r in CONF] == [len(r) for r in GRID]
    assert (len(FINE), len(FINE[0])) == (GRID_META["fine_ny"], GRID_META["fine_nx"])
    assert [len(r) for r in FINE_CONF] == [len(r) for r in FINE]
    # The fine cell divides the coarse one exactly, which is what makes one a downsample of
    # the other rather than two independent readings of the same raster.
    assert RATIO == 4
    assert GRID_META["fine_nx"] == GRID_META["nx"] * RATIO
    assert GRID_META["cell"] == RATIO * GRID_META["fine_cell"]


def test_the_legend_is_alphabetical_and_that_is_what_the_page_colours_by():
    """The frontend's ``REGION_COLOUR`` is keyed by these letters, so the rule that assigns
    them has to be one a person can check by eye."""
    assert list(LEGEND) == sorted(LEGEND)
    assert [LEGEND[k] for k in sorted(LEGEND)] == sorted(LEGEND.values())
    assert len(LEGEND) == 19
    assert GRID_META["void"] not in LEGEND


@pytest.mark.parametrize("row", range(30), ids=lambda j: f"row{j}")
def test_each_coarse_row_is_the_majority_of_the_fine_rows_under_it(row):
    """887 of 900 cells, and every one of the 13 exceptions is a near-tie.

    A majority of majorities is not a majority, so exact agreement is not available and
    asserting it would be wrong. What IS available is that a disagreement only ever happens
    where the two candidates are within one 64 m cell of each other -- which is the honest
    statement that these are two views of one boundary rather than two boundaries.
    """
    for i in range(GRID_META["nx"]):
        block = [FINE[row * RATIO + b][i * RATIO + a] for a in range(RATIO) for b in range(RATIO)]
        counts = Counter(block)
        top, best = counts.most_common(1)[0]
        got = GRID[row][i]
        if got == top:
            continue
        assert counts[got] >= best - 2, (row, i, got, counts)


@pytest.mark.parametrize("code", sorted(set("".join(CONF)) | set("".join(FINE_CONF))))
def test_every_confidence_letter_is_one_the_legend_explains(code):
    assert code in META["confidence_legend"]


def test_the_confidence_letters_mean_what_each_cell_is():
    """Void iff no name; ``u`` iff the label is No Man's Land. Both directions.

    One direction alone is the bug this catches: a grid where every void cell is unnamed but
    some unnamed cell is not void reads as correct and hides a region with no letter.
    """
    unnamed_letter = {name: ch for ch, name in LEGEND.items()}[UNNAMED]
    for grid, conf in ((GRID, CONF), (FINE, FINE_CONF)):
        for j, row in enumerate(grid):
            for i, letter in enumerate(row):
                code = conf[j][i]
                assert (letter == GRID_META["void"]) == (code == "."), (j, i)
                assert (code == "u") == (letter == unnamed_letter and code != "."), (j, i)


def test_the_cell_counts_are_the_grids_own_arithmetic():
    for key, grid, conf in (("256m", GRID, CONF), ("64m", FINE, FINE_CONF)):
        counts = META["cell_counts"][key]
        assert sum(counts.values()) == len(grid) * len(grid[0]), key
        letters = Counter("".join(conf))
        assert counts["void"] == letters["."], key
        assert counts["unnamed"] == letters["u"], key
        assert counts["boundary"] == letters["b"], key
        assert counts["interior"] == letters["l"], key


def test_no_mans_land_is_the_largest_thing_on_the_published_grid():
    """287 of the 768 painted cells, which is what makes its colour a decision.

    Recorded here because it is the number that surprises: the game leaves more than a third
    of the walkable map unnamed, and a table that blanked it would be answering "off-map or
    ocean" for a third of the coastline.
    """
    unnamed_letter = {name: ch for ch, name in LEGEND.items()}[UNNAMED]
    painted = Counter(c for row in GRID for c in row if c != GRID_META["void"])
    assert painted[unnamed_letter] == 287
    assert painted.most_common(1)[0][0] == unnamed_letter


# --------------------------------------------------------------------------------------
# The regions block.
# --------------------------------------------------------------------------------------

REGIONS = TABLE["regions"]


def test_every_legend_entry_has_a_region_and_the_other_way_round():
    assert set(REGIONS) == set(LEGEND.values())
    assert {entry["letter"] for entry in REGIONS.values()} == set(LEGEND)


@pytest.mark.parametrize("name", sorted(REGIONS), ids=lambda n: n)
def test_each_regions_extent_is_its_own_cells_arithmetic(name):
    """Box, centroid, cell count and area all recomputed from the published grid.

    The defect this replaces was real: the shipped boxes used to come from a coarser grid
    than the raster, so a bbox-AND-raster containment test answered False for points the
    raster itself assigned. Deriving the box from the grid makes containment hold by
    construction, and this is what says it still does.
    """
    entry = REGIONS[name]
    letter = entry["letter"]
    cell = GRID_META["cell"]
    cells = [(i, j) for j, row in enumerate(GRID) for i, ch in enumerate(row) if ch == letter]
    assert cells, name
    assert entry["cells"] == len(cells)
    assert entry["area_km2"] == pytest.approx(round(len(cells) * (cell / 100_000) ** 2, 2))
    x0, y0 = GRID_META["x0"], GRID_META["y0"]
    assert entry["bbox"][0] == min(x0 + i * cell for i, _ in cells)
    assert entry["bbox"][1] == min(y0 + j * cell for _, j in cells)
    assert entry["bbox"][2] == max(x0 + (i + 1) * cell for i, _ in cells)
    assert entry["bbox"][3] == max(y0 + (j + 1) * cell for _, j in cells)
    for i, j in cells:
        cx, cy = x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell
        assert entry["bbox"][0] <= cx <= entry["bbox"][2], (name, i, j)
        assert entry["bbox"][1] <= cy <= entry["bbox"][3], (name, i, j)
    assert entry["grid_cells"] == sorted(set(entry["grid_cells"]))
    assert entry["areas"], f"{name} names no area asset"
    for asset in entry["areas"]:
        assert NAME_MAP[asset]["display_name"] == name


def test_the_regions_that_are_two_assets_say_so():
    """Ten of the nineteen are more than one area asset, and one is five.

    Kept as an explicit claim because it is the shape of the game's data that a
    one-asset-one-region assumption would quietly flatten -- Rocky Desert is two RockyDesert
    assets and two Savannas, and a reader who assumed otherwise would lose half of it.
    """
    counts = {name: len(entry["areas"]) for name, entry in REGIONS.items()}
    assert counts["Rocky Desert"] == 4
    assert set(REGIONS["Rocky Desert"]["areas"]) == {
        "Area_RockyDesert_1",
        "Area_RockyDesert_2",
        "Area_Savanna_1",
        "Area_Savanna_2",
    }
    assert counts["Spire Coast"] == 1
    assert sum(1 for n in counts.values() if n > 1) == 10


# --------------------------------------------------------------------------------------
# The derivation, against the installed game.
# --------------------------------------------------------------------------------------

GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")
PAKS = GAME / "FactoryGame" / "Content" / "Paks"


@pytest.fixture(scope="module")
def raster():
    """The installed build's map-area raster, as ``(texels, width, index -> display name)``.

    Read through the same ``core.gameassets.maparea`` the generator uses, because what is
    under test is the committed TABLE rather than a second decoder's opinion of the asset.
    """
    if not (PAKS / "FactoryGame-Windows.utoc").exists():
        pytest.skip(f"needs the installed game at {GAME}")
    from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
    from satisfactory_mcp.core.gameassets.maparea import read_map_areas
    from satisfactory_mcp.core.gameassets.packages import ScriptObjects

    store = IoStore(PAKS, "FactoryGame-Windows", oodle_decompress)
    areas = read_map_areas(store, ScriptObjects(PAKS, oodle_decompress))
    names = [None if a is None else META["area_display_names"][a.asset] for a in areas.areas]
    return areas, areas.texels, areas.width, names


def _majority(texels, width, names, i, j, cell):
    """The majority display name over one grid cell of the committed frame."""
    frame = META["source"]["frame_m"]
    x0, x1 = frame["x_min_m"] * 100, frame["x_max_m"] * 100
    y0, y1 = frame["y_min_m"] * 100, frame["y_max_m"] * 100
    gx0, gy0 = GRID_META["x0"], GRID_META["y0"]
    us = [
        max(0, min(width, round((gx0 + (i + k) * cell - x0) / (x1 - x0) * width))) for k in (0, 1)
    ]
    vs = [
        max(0, min(width, round((gy0 + (j + k) * cell - y0) / (y1 - y0) * width))) for k in (0, 1)
    ]
    if us[1] <= us[0] or vs[1] <= vs[0]:
        return None
    counts: Counter[int] = Counter()
    for v in range(vs[0], vs[1]):
        counts.update(texels[v * width + us[0] : v * width + us[1]])
    return names[counts.most_common(1)[0][0]]


@pytest.mark.integration
def test_the_name_map_is_what_the_installed_build_states(raster):
    """Every asset, key and texel count in ``_meta.name_map``, re-read from the container.

    The whole table stands on this mapping, and it is the part a reader cannot check by
    looking at the grid: nothing in a 30x30 character raster says that ``Area_Savanna_1`` is
    called Rocky Desert.
    """
    areas, texels, _width, _names = raster
    assert {a.asset for a in areas.areas if a is not None} == set(NAME_MAP)
    for area in areas.areas:
        if area is None:
            continue
        entry = NAME_MAP[area.asset]
        assert entry["display_name_key"] == area.key
        assert entry["package"] == area.stem
        assert entry["string_table"] == area.string_table
    for asset, entry in NAME_MAP.items():
        indices = [i for i, a in enumerate(areas.areas) if a is not None and a.asset == asset]
        assert entry["palette_indices"] == indices, asset
        assert entry["texels"] == sum(texels.count(i) for i in indices), asset


@pytest.mark.integration
@pytest.mark.parametrize("row", range(30), ids=lambda j: f"row{j}")
def test_the_published_grid_is_this_builds_raster_downsampled(raster, row):
    """Cell for cell against the installed build, at 256 m. Staleness, not accuracy.

    A committed table that no longer matches the asset it was cut from is the failure this
    project answers with "announce drift rather than answer silently wrong", and a region
    layer is exactly the kind of table that would answer wrong in silence. Void cells are
    skipped: the land mask decides those and it is not in the raster.
    """
    _areas, texels, width, names = raster
    for i, letter in enumerate(GRID[row]):
        if letter == GRID_META["void"]:
            continue
        got = _majority(texels, width, names, i, row, GRID_META["cell"])
        assert LEGEND[letter] == (got or UNNAMED), (row, i)


@pytest.mark.integration
def test_the_fine_grid_is_the_same_raster_at_its_own_cell(raster):
    """And the finer pair, which is what every lookup actually reads.

    Sampled rather than exhaustive -- every fourth row and column is 900 cells, the same
    count the check above makes at 256 m, against a claim the two share. Walking all 14,400
    in Python buys nothing the sample does not.
    """
    _areas, texels, width, names = raster
    cell = GRID_META["fine_cell"]
    for j in range(0, GRID_META["fine_ny"], 4):
        for i in range(0, GRID_META["fine_nx"], 4):
            letter = FINE[j][i]
            if letter == GRID_META["void"]:
                continue
            got = _majority(texels, width, names, i, j, cell)
            assert LEGEND[letter] == (got or UNNAMED), (j, i)

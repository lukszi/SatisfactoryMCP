"""Cutting a tile pyramid, and the rename that means a reader never meets half of one.

Three layers are cut with this now -- the game's own artwork and both of
``tools/gen_map_renders.py``'s renders -- so the level arithmetic, the file layout and the
staged install are asserted here on their own rather than through whichever generator
happened to be running.

Pillow is the ``gen`` extra and this suite runs without it, so the sheet is a stand-in with
``width``, ``resize`` and ``crop`` and nothing else. That is not a workaround: what is
under test is the tree that comes out -- the levels, the names, the count, what survives a
failure -- and not anybody's Lanczos filter.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest import mock

import pytest

from satisfactory_mcp.core.gameassets.provenance import RETIRED_SUFFIX, STAGING_SUFFIX
from satisfactory_mcp.core.gameassets.pyramid import (
    DEFAULT_LEVEL_SOURCE,
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    TILES_RETIRED,
    TILES_STAGING,
    PyramidError,
    cut_pyramid,
    cut_square,
    enhanced_top_z,
    install_pyramid,
    merge_enhanced,
    pyramid_top_z,
    swap_into_place,
    tile_relpath,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"not really a PNG, but a real file with a real size"

IMAGING = types.SimpleNamespace(LANCZOS="the filter, which the stand-in ignores")


class _Sheet:
    """The three things the cutter asks of a Pillow image, and nothing else."""

    def __init__(self, width: int) -> None:
        self.width = width

    def resize(self, size, _filter):
        return _Sheet(size[0])

    def crop(self, box):
        return _Tile(box)


class _Tile:
    def __init__(self, box) -> None:
        self.box = box

    def save(self, path, **_kwargs):
        path.write_bytes(_PNG)


def test_the_top_level_is_derived_from_the_sheet_rather_than_assumed():
    """8192 px over 256 px tiles is z5, and a sheet that does not divide is refused.

    ``--size`` can halve the sheet, so a top level typed in rather than derived is a level
    of tiles upscaled from nothing the first time somebody uses that flag.
    """
    assert pyramid_top_z(8192) == 5
    assert pyramid_top_z(2048) == 3
    assert pyramid_top_z(256) == 0
    assert pyramid_top_z(1024, 512) == 1

    with pytest.raises(PyramidError, match="power-of-two multiple"):
        pyramid_top_z(5000)
    with pytest.raises(PyramidError):
        pyramid_top_z(128)


def test_an_upscale_is_worth_exactly_its_own_log2_in_levels():
    """4x adds two levels, 1x adds none, and a scale that is not a power of two adds none.

    Derived from ``pyramid_top_z`` so the two cannot hold different opinions about how deep
    an enhanced tree goes -- which is the number the serving side reads back out of the
    sidecar and configures the page's tile grid from.
    """
    assert enhanced_top_z(8192) == pyramid_top_z(8192) + 2 == 7
    assert enhanced_top_z(8192, 1) == 5
    assert enhanced_top_z(2048, 4) == 5
    # z7 is 128 tiles a side of the 4x sheet, which is the arithmetic stated twice.
    assert (1 << enhanced_top_z(8192)) * PYRAMID_TILE_PX == 8192 * 4

    with pytest.raises(PyramidError, match="not a power of two"):
        enhanced_top_z(8192, 3)


def test_the_layout_is_written_down_once():
    """``{z}/{x}_{y}.png``, and the staging names come off provenance's own suffixes."""
    assert tile_relpath(3, 5, 6) == "3/5_6.png"
    assert tile_relpath(7, 127, 127) == "7/127_127.png"

    assert (TILES_DIR_NAME, PYRAMID_TILE_PX) == ("tiles", 256)
    assert TILES_STAGING == TILES_DIR_NAME + STAGING_SUFFIX == "tiles.incoming"
    assert TILES_RETIRED == TILES_DIR_NAME + RETIRED_SUFFIX == "tiles.retired"


def test_a_square_becomes_files_at_the_offset_it_was_given(tmp_path):
    """``cut_square`` is the only place pixels become files, for whole levels and cores alike.

    The offset is what makes it serve both: an enhanced core is one square of sixty-four and
    lands at its own corner of the level, so the same function that writes a whole downscale
    starting at (0, 0) writes that one starting wherever it belongs.
    """
    written = cut_square(_Sheet(512), tmp_path, 4, 6, 8, 256)

    assert written == 4 * len(_PNG)
    assert sorted(p.name for p in (tmp_path / "4").iterdir()) == [
        "6_8.png",
        "6_9.png",
        "7_8.png",
        "7_9.png",
    ]
    assert (tmp_path / tile_relpath(4, 7, 9)).read_bytes() == _PNG


def test_every_level_is_cut_from_the_sheet_and_says_what_it_was_cut_from(tmp_path):
    """z0..z_top, each one downscale of the whole sheet, and the record a reader checks.

    Downscaling the sheet once per level rather than each tile from its four children is
    what keeps every level a resampling of the original pixels; the stand-in cannot see the
    filter, but it can see that every level was resized from the 1024 px sheet and not from
    the level above it.
    """
    stats = cut_pyramid(_Sheet(1024), IMAGING, tmp_path)

    assert (stats["max_z"], stats["tile_px"], stats["enhanced"]) == (2, 256, False)
    assert stats["count"] == 1 + 4 + 16 == sum(level["tiles"] for level in stats["levels"])
    assert stats["bytes"] == stats["count"] * len(_PNG)
    assert [level["sheet_px"] for level in stats["levels"]] == [256, 512, 1024]
    assert stats["layout"] == "tiles/{z}/{x}_{y}.png"
    assert TILES_STAGING in stats["completeness"]
    assert len(list(tmp_path.rglob("*.png"))) == stats["count"]

    # The default is the artwork tool's answer; a caller drawing something else says so,
    # or the level record would name the artwork under a hillshade.
    assert {level["from"] for level in stats["levels"]} == {DEFAULT_LEVEL_SOURCE}
    other = cut_pyramid(_Sheet(256), IMAGING, tmp_path / "other", source="a hillshade, Lanczos")
    assert {level["from"] for level in other["levels"]} == {"a hillshade, Lanczos"}


def test_the_merged_record_is_re_summed_rather_than_added_to():
    """``count`` and ``bytes`` come off the levels, because ``count`` is what is checked.

    The installer compares that number against the PNGs really on disk, so it has to be
    derived from the same list a doubter would count themselves -- and the plain record
    stays untouched, since a caller may still be holding it.
    """
    plain = {
        "max_z": 5,
        "enhanced": False,
        "count": 1365,
        "bytes": 100,
        "levels": [{"z": z, "tiles": 4**z, "bytes": 10} for z in range(6)],
    }

    merged = merge_enhanced(
        plain,
        {
            "levels": [{"z": 6, "tiles": 4096, "bytes": 40}, {"z": 7, "tiles": 16384, "bytes": 50}],
            "enhancement": {"model": "an upscaler"},
        },
    )

    assert (merged["max_z"], merged["enhanced"], merged["count"]) == (7, True, 21845)
    assert merged["bytes"] == 60 + 90
    assert merged["enhancement"] == {"model": "an upscaler"}
    assert (plain["max_z"], plain["count"], plain["enhanced"]) == (5, 1365, False)


def test_the_pyramid_is_renamed_into_place_so_a_reader_never_meets_half_of_one(tmp_path):
    """An interrupted run leaves no tree at all rather than a tree missing its deep levels.

    So the cut goes to staging and is renamed over the old one, the count is checked against
    what is really on disk before the swap, and both leftovers of a run that died mid-swap
    are cleared rather than merged into. A level the new pyramid does not have cannot
    survive from the old one either -- the old tree is moved aside whole, not written over.
    """
    tiles = tmp_path / TILES_DIR_NAME
    (tiles / "9").mkdir(parents=True)
    (tiles / "9" / "0_0.png").write_bytes(b"a level the new cut does not have")
    (tmp_path / TILES_STAGING / "3").mkdir(parents=True)
    (tmp_path / TILES_STAGING / "3" / "0_0.png").write_bytes(b"half of a dead run")
    (tmp_path / TILES_RETIRED).mkdir()

    stats = install_pyramid(_Sheet(1024), IMAGING, tmp_path)

    assert (stats["max_z"], stats["count"]) == (2, 21)
    assert not (tmp_path / TILES_STAGING).exists(), "staging is not left behind"
    assert not (tmp_path / TILES_RETIRED).exists(), "nor is the tree it replaced"
    assert sorted(p.name for p in tiles.iterdir()) == ["0", "1", "2"]
    assert len(list(tiles.rglob("*.png"))) == stats["count"]


def test_a_tree_that_disagrees_with_its_own_count_is_not_installed(tmp_path):
    """The count is checked against the PNGs on disk, and the old pyramid survives a failure.

    ``enhance`` here claims two levels it never wrote, which is the shape of every way this
    can go wrong: a stage that reports what it meant to do rather than what it did. The
    refusal has to come BEFORE the rename or the reader is handed the gap.
    """
    tiles = tmp_path / TILES_DIR_NAME
    (tiles / "0").mkdir(parents=True)
    (tiles / "0" / "0_0.png").write_bytes(b"the pyramid that is already installed")

    def enhance(_staging):
        return {
            "levels": [{"z": 3, "tiles": 64, "bytes": 0}],
            "enhancement": {"model": "a stage that wrote nothing"},
        }

    with pytest.raises(PyramidError, match="does not match its own count"):
        install_pyramid(_Sheet(512), IMAGING, tmp_path, enhance=enhance)

    assert (tiles / "0" / "0_0.png").read_bytes() == b"the pyramid that is already installed"
    assert sorted(p.name for p in tiles.iterdir()) == ["0"]


def test_the_denser_grid_is_the_same_pyramid_with_its_own_names(tmp_path):
    """``tiles@2x/`` is one call with two arguments changed, and it cannot disturb ``tiles/``.

    The @2x tree is the identical tile GRID at twice the pixels -- level z is still 2**z
    tiles a side over the identical squares of the world -- which is why it is the same
    cutter rather than a second one. Two things follow and both are asserted: it is exactly
    one level shallower, because 512 * 2**z runs out of sheet before 256 * 2**z does; and it
    stages and retires under ITS own names, so cutting it while a reader is being served the
    1x tree touches nothing the reader can see.
    """
    plain = install_pyramid(_Sheet(1024), IMAGING, tmp_path)
    dense = install_pyramid(
        _Sheet(1024), IMAGING, tmp_path, tile_px=PYRAMID_TILE_2X_PX, dir_name=TILES_2X_DIR_NAME
    )

    assert (plain["max_z"], dense["max_z"]) == (2, 1)
    assert dense["tile_px"] == PYRAMID_TILE_2X_PX == 512
    assert dense["layout"] == "tiles@2x/{z}/{x}_{y}.png"
    # Level z of each covers the same 2**z squares of the world; only the pixels differ.
    assert [level["tiles"] for level in dense["levels"]] == [1, 4]
    assert [level["sheet_px"] for level in dense["levels"]] == [512, 1024]

    assert sorted(p.name for p in tmp_path.iterdir()) == [TILES_DIR_NAME, TILES_2X_DIR_NAME]
    assert len(list((tmp_path / TILES_DIR_NAME).rglob("*.png"))) == plain["count"] == 21
    assert len(list((tmp_path / TILES_2X_DIR_NAME).rglob("*.png"))) == dense["count"] == 5
    assert TILES_2X_DIR_NAME + STAGING_SUFFIX in dense["completeness"]


def test_a_tree_something_holds_open_is_swapped_level_by_level_rather_than_abandoned(tmp_path):
    """Windows will not rename a directory anything has open, and that is the ordinary state.

    An Explorer window sitting in ``tiles/``, the search indexer walking it, a backup agent:
    any of them makes the whole-tree rename fail with ``Access is denied`` after ten minutes
    of drawing, and it was doing exactly that on the machine this was written on. The
    fallback is second-best rather than equal and says so: each level is renamed over its
    predecessor, so a reader who catches the middle sees every level present with some of
    them still the old cut, rather than a level missing.

    Asserted through a refusal injected into ``Path.rename`` for the one call that is
    supposed to fail, because the real cause cannot be arranged from inside a test.
    """
    final = tmp_path / TILES_DIR_NAME
    staging = tmp_path / TILES_STAGING
    for level, name in ((0, "0_0.png"), (1, "0_0.png"), (9, "0_0.png")):
        (final / str(level)).mkdir(parents=True)
        (final / str(level) / name).write_bytes(b"the tree that is being served")
    for level in (0, 1):
        (staging / str(level)).mkdir(parents=True)
        (staging / str(level) / "0_0.png").write_bytes(b"the tree that was just cut")

    real = Path.rename
    refused = []

    def refuse(self, target):
        if self == final and not refused:
            refused.append(self)
            raise PermissionError(5, "Access is denied")
        return real(self, target)

    with mock.patch.object(Path, "rename", refuse):
        how = swap_into_place(staging, final, tmp_path / TILES_RETIRED)

    assert refused, "the whole-tree rename has to be TRIED before the fallback is taken"
    assert "level by level" in how
    assert not staging.exists()
    # Every level is the new cut, and the level only the old tree had is gone rather than
    # left behind pretending to belong to the new one.
    assert sorted(p.name for p in final.iterdir()) == ["0", "1"]
    for level in (0, 1):
        assert (final / str(level) / "0_0.png").read_bytes() == b"the tree that was just cut"


def test_the_enhance_stage_runs_inside_the_staging_window(tmp_path):
    """It is handed the staging directory, and its failure leaves the installed tree alone.

    The GPU stage is the part most likely to fail, and it must fail against a tree nothing
    serves. What it is given is asserted too: an ``enhance`` that wrote into the final
    directory would be writing into what a reader is reading.
    """
    tiles = tmp_path / TILES_DIR_NAME
    (tiles / "0").mkdir(parents=True)
    (tiles / "0" / "0_0.png").write_bytes(b"the pyramid that is already installed")
    seen = []

    def enhance(staging):
        seen.append(staging)
        raise RuntimeError("no Vulkan device")

    with pytest.raises(RuntimeError, match="no Vulkan device"):
        install_pyramid(_Sheet(256), IMAGING, tmp_path, enhance=enhance)

    assert seen == [tmp_path / TILES_STAGING]
    assert (tiles / "0" / "0_0.png").read_bytes() == b"the pyramid that is already installed"

"""The light stage's scratch: one run's, its crowns written once, deleted however it ends;
and the drawn sheets' beside it.

docs/spatial-and-map.md sections 29, "Scratch", and 40. Synthetic fixtures throughout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from mapgen.common import Refusal
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting import stage
from mapgen.lighting.occluders import CrownGrid
from mapgen.lighting.stage import Surface, bake_light, occluder_planes
from mapgen.render.light import (
    LIGHT_CACHE_DIR_NAME,
    SCRATCH_IN_USE,
    CrownTops,
    add_light_flags,
    claim_scratch,
    light_run,
)
from mapgen.render.sheets import SHEETS_DIR_NAME, claim_sheets


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path)
    add_light_flags(parser)
    return parser.parse_args(list(argv))


def _crowns() -> CrownTops:
    grid: CrownGrid = {"x0_cm": BOUNDS_M["x_min_m"] * 100.0,
                       "y0_cm": BOUNDS_M["y_min_m"] * 100.0, "spacing_cm": 100.0}  # fmt: skip
    return CrownTops(np.full((8, 8), 120, np.int16), grid)


def test_the_scratch_goes_under_scratch_dir_else_the_cache_dir_else_the_renders(tmp_path):
    renders, cache, scratch = tmp_path / "r", tmp_path / "c", tmp_path / "s"
    assert claim_scratch(_args(), renders) == renders
    assert claim_scratch(_args("--cache-dir", str(cache)), renders) == cache
    both = _args("--cache-dir", str(cache), "--scratch-dir", str(scratch))
    assert claim_scratch(both, renders) == scratch
    assert claim_scratch(_args("--no-light", "--scratch-dir", str(scratch)), renders) is None


def test_a_lit_run_starts_by_emptying_what_a_killed_run_left(tmp_path):
    left = tmp_path / LIGHT_CACHE_DIR_NAME
    left.mkdir()
    for name in ("z.npy", "occluder.npy", "slab_lo.npy", "terms.npy"):
        np.save(left / name, np.zeros(4, np.float32))
    assert claim_scratch(_args("--no-light"), tmp_path) is None
    assert (left / "occluder.npy").is_file(), "a run without the light leaves it alone"
    assert claim_scratch(_args(), tmp_path) == tmp_path
    assert not left.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename an open file")
def test_the_scratch_of_a_render_still_running_is_refused_and_left_as_it_is(tmp_path):
    live = Surface(tmp_path / LIGHT_CACHE_DIR_NAME, 16)
    np.save(live.path("terms"), np.zeros((16, 16, 3), np.uint8))
    with pytest.raises(Refusal, match="still running") as refused:
        claim_scratch(_args(), tmp_path)
    assert refused.value.code == SCRATCH_IN_USE
    assert live.path("terms").is_file() and live.path("z").is_file()
    live.close()


def test_the_sheets_are_files_beside_the_light_s_scratch_made_when_drawn(tmp_path):
    scratch = tmp_path / "s"
    sheets = claim_sheets(_args("--no-light", "--scratch-dir", str(scratch)), tmp_path / "r")
    directory = scratch / SHEETS_DIR_NAME
    assert not directory.exists(), "nothing is written before a sheet is drawn"
    sheet = sheets("terrain", (16, 32, 3))
    sheet[:] = 9
    sheet.flush()
    assert (np.load(directory / "terrain.npy") == 9).all()
    del sheet
    sheets.release("terrain")
    assert not (directory / "terrain.npy").exists()
    sheets.close()
    assert not directory.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename an open file")
def test_the_sheets_of_a_render_still_running_are_refused_and_left_as_they_are(tmp_path):
    live = claim_sheets(_args(), tmp_path)
    sheet = live("painted", (8, 8, 3))
    with pytest.raises(Refusal, match="still running") as refused:
        claim_sheets(_args(), tmp_path)
    assert refused.value.code == SCRATCH_IN_USE
    assert (tmp_path / SHEETS_DIR_NAME / "painted.npy").is_file()
    del sheet
    assert claim_sheets(_args(), tmp_path).directory == tmp_path / SHEETS_DIR_NAME
    assert not (tmp_path / SHEETS_DIR_NAME).exists(), "a dead run's sheets are emptied"


def test_a_run_that_fails_still_deletes_its_scratch(tmp_path):
    with pytest.raises(RuntimeError), light_run(tmp_path, 16, _crowns()) as run:
        assert sorted(p.name for p in (tmp_path / LIGHT_CACHE_DIR_NAME).glob("occluder*")) == [
            "occluder.npy",
            "occluder_cover.npy",
        ]
        run.surface.put(0, np.zeros((16, 16), np.float32), np.ones((16, 16), np.float32))
        raise RuntimeError("a layer failed part way")
    assert not (tmp_path / LIGHT_CACHE_DIR_NAME).exists()
    with light_run(None, 16, _crowns()) as run:
        assert run is None


def test_crowns_that_fail_to_write_leave_no_scratch_behind(tmp_path):
    broken = CrownTops(np.full((8, 8), 120, np.int16), cast(CrownGrid, {}))
    with pytest.raises(KeyError), light_run(tmp_path, 16, broken):
        pass
    assert not (tmp_path / LIGHT_CACHE_DIR_NAME).exists()


def _surface(work: Path, size: int) -> Surface:
    surface = Surface(work, size)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    z = (30 * np.exp(-((xx - 200) ** 2 + (yy - 260) ** 2) / 3000.0)).astype(np.float32)
    for top in range(0, size, 128):
        surface.put(top, z[top : top + 128], np.ones((128, size), np.float32))
    return surface


def _webp(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.webp"))}


def test_an_occluder_read_in_place_bakes_the_bytes_a_copied_one_does(tmp_path, monkeypatch):
    size = 512
    top = np.full((size, size), np.nan, np.float32)
    top[250:262, 240:270] = 60.0
    cover = np.where(np.isfinite(top), 255, 0).astype(np.uint8)
    copied = _surface(tmp_path / "copied", size)
    bake_light(copied, tmp_path / "a", 1, (top, cover), progress=False)
    copied.close()

    work = tmp_path / "in_place"
    planes = occluder_planes(work, size)
    planes[0][:], planes[1][:] = top, cover
    saved: list[str] = []
    monkeypatch.setattr(stage.np, "save", lambda path, *_a, **_k: saved.append(Path(path).name))
    in_place = _surface(work, size)
    bake_light(in_place, tmp_path / "b", 1, planes, progress=False)
    in_place.close()

    del planes
    assert saved == [], "the crowns are not copied"
    tiles = _webp(tmp_path / "a" / "light" / "tiles")
    assert tiles and tiles == _webp(tmp_path / "b" / "light" / "tiles")

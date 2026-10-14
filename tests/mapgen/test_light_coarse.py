"""The light's coarser levels built strip by strip as the block rows come in.

docs/map/renders.md section 42, "The light, a row of blocks at a time". Synthetic sources.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from mapgen.lighting import light_tiles, model
from mapgen.lighting.bake import LightBake
from mapgen.lighting.coarse import CoarseLevels
from mapgen.lighting.stage import Surface

#: The first coarser level of a 2048 bake (z2), and native tiles with a folded band.
N = 1024
FOLDED = frozenset({(0, 0), (5, 3), (7, 7)})


def _sources(work: Path) -> None:
    work.mkdir(parents=True)
    rng = np.random.default_rng(5)
    z = (rng.standard_normal((N, N)) * 30).astype(np.float32)
    z[600:610, 100:400] = np.nan
    np.save(work / "zh.npy", z)
    np.save(work / "landh.npy", rng.choice(np.array([0, 255], np.uint8), (N, N)))
    np.save(work / "svfh.npy", rng.integers(0, 256, (N, N), dtype=np.uint8))
    np.save(work / "hzq.npy", rng.integers(0, 253, (N // 2, N // 2, model.HZ_CELLS), np.uint8))


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_levels_built_as_the_rows_come_in_are_the_bytes_of_levels_built_whole(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(light_tiles, "LEVEL_PIECE_TILES", 1)
    _sources(tmp_path / "whole")
    shutil.copytree(tmp_path / "whole", tmp_path / "rows")
    with ThreadPoolExecutor(2) as pool:
        folded = FOLDED
        for level in (2, 1, 0):
            folded = frozenset((x // 2, y // 2) for x, y in folded)
            spacing = 2.0 * 2 ** (2 - level)
            light_tiles.level_strips(
                tmp_path / "whole", tmp_path / "d-whole", level, spacing, pool, level == 0, folded
            )
        coarse = CoarseLevels(tmp_path / "rows", tmp_path / "d-rows", 3, 1.0, pool, 1)
        coarse.advance(300, FOLDED)
        assert [level.r0 for level in coarse.levels] == [256, 0], "z2's first strip, no z1 yet"
        for rows in (520, 777, N):
            coarse.advance(rows, FOLDED)
        assert coarse.finish() == 16 + 4 + 1
    assert _tree(tmp_path / "d-rows") == _tree(tmp_path / "d-whole")
    assert not list((tmp_path / "rows").glob("*.z*.npy")), "the levels' files are removed"


def test_a_bake_closed_before_its_finish_stops_and_leaves_no_level_files(tmp_path):
    surface = Surface(tmp_path / "work", N)
    z = np.zeros((N, N), np.float32)
    surface.put(0, z, np.ones((N, N), np.float32))
    bake = LightBake(surface, tmp_path / "out", 1)
    bake.queue(0).result()
    bake.close()
    surface.close()
    assert bake._builder is None
    assert not list((tmp_path / "work").glob("*.z*.npy"))

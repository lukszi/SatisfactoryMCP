"""The arches' and overhangs' bands in the light: their shade in cells of their own, folded at
the sun path's elevation and read back there whatever the sun's, and refolded on the coarser
levels.

docs/map/light-and-crowns.md section 29, "Bands off the sun's path". Synthetic fixtures.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.lighting import horizon as hz
from mapgen.lighting import light_tiles, model, refold
from mapgen.lighting.spans.march import path_elevation

DIRS = hz.HORIZON_DIRS
SOFT = np.float32(model.SHADOW_SOFT_DEG)


def _cells(shade: float, ground: float = 0.0, k: int | None = None, k_shade: float = 0.0):
    """Every ground cell ``ground`` degrees, every band cell ``shade`` folded at its own path
    elevation, direction ``k``'s ``k_shade``."""
    cells = np.zeros((model.HZ_CELLS, 4, 4), np.float32)
    cells[:DIRS] = ground
    for d in range(DIRS):
        s = k_shade if d == k else shade
        cells[model.BAND_CELL + d] = path_elevation(d * 360.0 / DIRS) + SOFT * (s - 0.5)
    return cells


def _shade(cells: np.ndarray, sun: tuple[float, float]) -> float:
    return float(model.soft_shade(model.sun_horizon(cells, sun), sun[1])[0, 0])


@pytest.mark.parametrize("el", [5.0, 10.0, 30.0, 62.25, 80.0])
def test_a_sun_off_the_path_keeps_the_path_s_band_shade(el):
    cells = _cells(0.4)
    assert model.band_shade(cells, 225.0)[0, 0] == pytest.approx(0.4, abs=1e-5)
    assert _shade(cells, (225.0, el)) == pytest.approx(0.4, abs=1e-5), "never all or none"


def test_a_band_folded_into_the_ground_s_cell_is_read_as_before():
    cells = _cells(0.4)
    old = cells[: model.BAND_CELL].copy()  # light version 7: no band cells
    old[:DIRS] = cells[model.BAND_CELL : model.AO_CELL]
    assert _shade(old, (225.0, path_elevation(225.0))) == pytest.approx(0.4, abs=1e-5)
    assert _shade(old, (225.0, 30.0)) == 1.0, "all or none off the path, as it was"


def test_between_two_directions_the_band_shades_blend_whatever_their_path_elevations():
    k = 27  # 303.75 degrees, on the path at 16.7; the next, 315, past its end at 45
    cells = _cells(0.0, k=k, k_shade=0.6)
    az = (k + 0.25) * 360.0 / DIRS
    assert model.band_shade(cells, az)[0, 0] == pytest.approx(0.75 * 0.6, abs=1e-5)
    assert _shade(cells, (az, 20.0)) == pytest.approx(0.75 * 0.6, abs=1e-5)


def test_the_terrain_is_read_at_the_sun_s_elevation_and_the_larger_shade_shades():
    open_bands = _cells(0.0, ground=40.0)
    assert _shade(open_bands, (225.0, 30.0)) == 1.0 and _shade(open_bands, (225.0, 50.0)) == 0.0
    plain = model.sun_horizon(open_bands[:DIRS], (225.0, 30.0))
    assert model.sun_horizon(open_bands, (225.0, 30.0)).tobytes() == plain.tobytes()
    assert _shade(_cells(0.6, ground=30.0 + 0.3 * 6 - 3), (225.0, 30.0)) == pytest.approx(0.6)
    assert _shade(_cells(0.6, ground=30.0 + 0.8 * 6 - 3), (225.0, 30.0)) == pytest.approx(0.8)


def test_the_trees_are_lit_under_a_band_only_where_no_canopy_holds_them():
    cells = _cells(1.0)
    sun = (225.0, 30.0)
    assert model.soft_shade(model.canopy_horizon(cells, sun), 30.0)[0, 0] == pytest.approx(1.0)
    cells[model.CANOPY_CELL : model.BAND_CELL] = model.CANOPY_FLOOR_DEG
    assert model.soft_shade(model.canopy_horizon(cells, sun), 30.0)[0, 0] == 0.0


def _folded(rng: np.random.Generator, shape: tuple[int, ...], el: np.ndarray) -> np.ndarray:
    """Shade folded at ``el``, a third of it open."""
    shade = np.where(rng.random(shape) < 1 / 3, 0.0, rng.random(shape)).astype(np.float32)
    return (el + SOFT * (shade - np.float32(0.5))).astype(np.float32)


def _page(deg: np.ndarray, el: np.ndarray) -> np.ndarray:
    return np.clip((deg - el) / SOFT + np.float32(0.5), 0, 1)


def _four_groups(seed: int, shape: tuple[int, int]) -> np.ndarray:
    els = refold.path_elevations()
    rng = np.random.default_rng(seed)
    ground = (rng.random((*shape, DIRS), dtype=np.float32) * 80).astype(np.float32)
    near = _folded(rng, ground.shape, els)
    ground = np.where(rng.random(ground.shape) < 0.4, near, ground).astype(np.float32)
    trees = _folded(rng, ground.shape, els)
    canopy = np.zeros_like(ground)
    return np.concatenate([ground, trees, canopy, _folded(rng, ground.shape, els)], -1)


def test_a_coarser_band_cell_keeps_the_page_s_larger_shade_on_average():
    els = refold.path_elevations()
    fine = _four_groups(5, (64, 96))
    coarse = light_tiles.decode_linear(light_tiles.encode_linear(refold.refold(fine, els)))
    ground, trees, bands = fine[..., :DIRS], fine[..., DIRS : 2 * DIRS], fine[..., 3 * DIRS :]
    half_step = np.float32(0.5 / light_tiles.HZ_LINEAR_SCALE) / SOFT + np.float32(1e-5)
    terrain = np.maximum(ground, bands)
    want = light_tiles.downsample(_page(terrain, els))
    page = _page(np.maximum(coarse[..., :DIRS], coarse[..., 3 * DIRS :]), els)
    assert np.abs(page - want).max() <= half_step, "the page's max of ground and band, averaged"
    want = light_tiles.downsample(_page(np.maximum(terrain, trees), els))
    page = np.maximum(page, _page(coarse[..., DIRS : 2 * DIRS], els))
    assert np.abs(page - want).max() <= 2 * half_step, "and of the trees over both"
    own = light_tiles.downsample(_page(bands, els))
    raised = light_tiles.downsample(_page(terrain, els)) > light_tiles.downsample(
        _page(ground, els)
    )
    alone = np.abs(_page(coarse[..., 3 * DIRS :], els) - own) <= half_step
    assert raised.any() and alone[~raised].all(), "the band's own mean where it raises nothing"


def test_a_coarser_level_without_bands_keeps_its_band_cells_empty():
    els = refold.path_elevations()
    fine = _four_groups(6, (8, 8))
    fine[..., 3 * DIRS :] = 0.0
    assert not refold.refold(fine, els)[..., 3 * DIRS :].any()
    fine[..., DIRS : 2 * DIRS] = 0.0
    assert not refold.refold(fine, els)[..., DIRS:].any(), "nor tree cells from the bands"

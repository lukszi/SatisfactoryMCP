"""Height bands: per-class breaks, the rules that veto a break, and the traced bands."""

from __future__ import annotations

import numpy as np
import pytest

from tools.building_shapes.bands import (
    Band,
    BandOptions,
    band_floors,
    band_surface,
    bands_from_tops,
    height_bands,
)
from tools.building_shapes.breaks import natural_breaks

OPTIONS = BandOptions(
    budgets=(32, 28, 24, 20),
    min_floor=200.0,
    step=25.0,
    min_visible=0.06,
    min_width=60.0,
    min_rise=100.0,
    opening=30.0,
    min_area=20_000.0,
)


def _area(ring: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )


def _tops(*blocks: tuple[int, int, int, int, float], shape: tuple[int, int] = (100, 100)):
    """A top-height grid (10 cm cells) built from ``(row0, col0, row1, col1, height)``
    rectangles, later ones over earlier ones; uncovered cells are -inf."""
    top = np.full(shape, -np.inf)
    for r0, c0, r1, c1, z in blocks:
        top[r0:r1, c0:c1] = z
    return top


def _floors(top: np.ndarray, options: BandOptions = OPTIONS) -> list[float]:
    return band_floors(band_surface(top, 4, 3), options, 10.0)


def _quad3(x0: float, y0: float, x1: float, y1: float, z: float) -> np.ndarray:
    """A flat rectangle at height ``z`` as two triangles, cm."""
    corners = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]
    a, b, c, d = corners
    return np.array([[a, b, c], [a, c, d]], float)


def test_breaks_find_three_clusters():
    values = np.array([0, 1, 2, 10, 11, 12, 30, 31], float)
    assert natural_breaks(values, np.ones(8), 3) == [3, 6]


def test_breaks_fall_back_to_fewer_groups_when_a_split_is_vetoed():
    values = np.array([0, 1, 10, 11, 30, 31], float)
    allowed = np.ones((7, 7), dtype=bool)
    assert natural_breaks(values, np.ones(6), 3, allowed) == [2, 4]
    allowed[:, [1, 2, 3, 5]] = False  # a group may only end where the top cluster starts
    assert natural_breaks(values, np.ones(6), 3, allowed) == [4]
    allowed[:, 4] = False
    assert natural_breaks(values, np.ones(6), 3, allowed) == []


def test_a_stepped_building_gets_a_floor_at_each_step():
    """Base plate 0.5 m, body 3 m, upper body 8 m, a tower 15 m: three bands, one per step."""
    top = _tops(
        (0, 0, 100, 100, 50.0),
        (10, 10, 90, 90, 300.0),
        (20, 20, 70, 70, 800.0),
        (30, 30, 50, 50, 1500.0),
    )
    floors = _floors(top)
    assert len(floors) == 3
    assert 50 < floors[0] <= 300 and 300 < floors[1] <= 800 and 800 < floors[2] <= 1500


def test_breaks_follow_the_class_own_heights_not_fixed_metres():
    """The same massing at a quarter of the height breaks at a quarter of the heights."""
    blocks = [
        (0, 0, 100, 100, 0.0),
        (10, 10, 90, 90, 1.0),
        (20, 20, 70, 70, 2.0),
        (30, 30, 50, 50, 4.0),
    ]
    tall = _floors(_tops(*[(*b[:4], b[4] * 1000.0) for b in blocks]))
    flat = _floors(_tops(*[(*b[:4], b[4] * 250.0) for b in blocks]))
    assert len(tall) == len(flat) == 3
    for high, low in zip(tall, flat, strict=True):
        assert low == pytest.approx(high / 4, abs=OPTIONS.step)


def test_no_floor_under_the_minimum():
    top = _tops((0, 0, 100, 100, 0.0), (10, 10, 90, 90, 120.0), (30, 30, 70, 70, 600.0))
    assert all(floor >= OPTIONS.min_floor for floor in _floors(top))


def test_a_step_seen_only_as_a_thin_rim_is_no_break():
    """A 40 cm ledge round a block is narrower than ``min_width``: one band, not two."""
    top = _tops((0, 0, 100, 100, 0.0), (20, 20, 80, 80, 500.0), (24, 24, 76, 76, 900.0))
    assert len(_floors(top)) == 1


def test_steps_closer_than_the_rise_merge():
    top = _tops((0, 0, 100, 100, 0.0), (10, 10, 90, 90, 500.0), (25, 25, 75, 75, 560.0))
    assert len(_floors(top)) == 1


def test_a_box_has_no_bands():
    assert _floors(_tops((0, 0, 100, 100, 450.0))) == []


def test_bands_are_traced_lowest_first_with_their_own_budgets():
    tris = np.concatenate(
        [
            _quad3(0, 0, 1000, 600, 50),
            _quad3(100, 100, 700, 500, 300),
            _quad3(450, 150, 650, 350, 1200),
        ]
    )
    bands = height_bands(tris, 10, 40, OPTIONS)
    assert [band.tier for band in bands] == [1, 2]
    body, tower = bands
    assert abs(_area(body.rings[0])) == pytest.approx(600 * 400, rel=0.1)
    assert abs(_area(tower.rings[0])) == pytest.approx(200 * 200, rel=0.25)
    assert all(len(ring) <= OPTIONS.budgets[band.tier - 1] for band in bands for ring in band.rings)


def test_a_band_keeps_its_tier_when_the_one_below_is_dropped():
    """The body is nearly the whole footprint, so it is dropped; the tower stays tier 2."""
    top = _tops((0, 0, 100, 100, 50.0), (2, 2, 98, 98, 300.0), (40, 40, 60, 60, 1200.0))
    options = OPTIONS._replace(min_visible=0.0)
    bands = bands_from_tops(top, (0.0, 0.0), 10.0, 40.0, options)
    assert [band.tier for band in bands] == [2]


def test_a_band_keeps_at_most_its_ring_cap():
    blocks = [(0, 0, 200, 200, 0.0), (5, 5, 195, 195, 300.0)]
    blocks += [
        (10 + 25 * i, 10 + 25 * j, 25 + 25 * i, 25 + 25 * j, 900.0)
        for i in range(7)
        for j in range(7)
    ]
    bands = bands_from_tops(_tops(*blocks, shape=(200, 200)), (0.0, 0.0), 10.0, 40.0, OPTIONS)
    assert bands and all(len(band.rings) <= OPTIONS.max_rings for band in bands)
    assert isinstance(bands[0], Band)

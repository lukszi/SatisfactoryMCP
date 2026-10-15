"""A mesh's top-down silhouette: raster, trace, simplification."""

from __future__ import annotations

import numpy as np
import pytest

from tools.building_shapes.silhouette import (
    silhouette,
    simplify_ring,
    simplify_to_budget,
    trace_outer,
)


def _quad(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """A rectangle as two ground-plane triangles, cm."""
    return np.array([[[x0, y0], [x1, y0], [x1, y1]], [[x0, y0], [x1, y1], [x0, y1]]], float)


def _area(ring: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )


def test_a_box_traces_to_its_four_corners_within_a_cell():
    rings = silhouette(_quad(-200, -100, 200, 100), cell=10, close=20, max_vertices=48)
    assert len(rings) == 1
    ring = rings[0]
    assert len(ring) == 4
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    assert min(xs) == pytest.approx(-200, abs=10) and max(xs) == pytest.approx(200, abs=10)
    assert min(ys) == pytest.approx(-100, abs=10) and max(ys) == pytest.approx(100, abs=10)
    assert _area(ring) > 0, "wound counter-clockwise in (x, y)"


def test_an_l_shape_keeps_its_notch():
    """The closing bevels the inside corner by about its radius, and no more."""
    tris = np.concatenate([_quad(0, 0, 400, 100), _quad(0, 0, 100, 400)])
    ring = silhouette(tris, cell=10, close=20, max_vertices=48)[0]
    assert 6 <= len(ring) <= 8
    assert abs(_area(ring)) == pytest.approx(400 * 100 + 100 * 300, rel=0.08)
    assert max(x for x, _y in ring) == pytest.approx(400, abs=6)


def test_a_wall_seen_edge_on_still_marks_its_line():
    """Zero area from above; sampled along its edges it closes with the floor next to it."""
    wall = np.array([[[0, 0], [300, 0], [300, 0]]], float)
    floor = _quad(0, 10, 300, 200)
    ring = silhouette(np.concatenate([wall, floor]), cell=10, close=20, max_vertices=48)[0]
    assert min(p[1] for p in ring) == pytest.approx(0, abs=10)


def test_parts_further_apart_than_the_closing_stay_separate_and_strays_drop():
    tris = np.concatenate([_quad(0, 0, 200, 200), _quad(500, 0, 700, 200), _quad(900, 0, 905, 5)])
    rings = silhouette(tris, cell=10, close=20, max_vertices=48, min_fraction=0.05)
    assert len(rings) == 2


def test_the_trace_follows_cell_edges_counter_clockwise():
    mask = np.zeros((3, 3), bool)
    mask[1, 1] = True
    assert trace_outer(mask) == [(1, 1), (2, 1), (2, 2), (1, 2)]


def test_simplification_keeps_a_square_and_drops_a_bump_under_tolerance():
    ring = [(0.0, 0.0), (5.0, 0.0), (5.0, 0.2), (5.2, 0.2), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    simple = simplify_ring(ring, tolerance=0.5)
    assert sorted(simple) == sorted([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])


def test_the_budget_raises_the_tolerance_until_the_ring_fits():
    circle = [(np.cos(t) * 100, np.sin(t) * 100) for t in np.linspace(0, 2 * np.pi, 400)[:-1]]
    fitted = simplify_to_budget([(float(x), float(y)) for x, y in circle], 12, tolerance=0.1)
    assert 3 < len(fitted) <= 12

"""Regular outlines: straight walls, round parts, mirror symmetry, and where they give way."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tools.building_shapes.bands import BandOptions, bands_from_tops
from tools.building_shapes.polygons import nearest_on, pulled_inside
from tools.building_shapes.regular import Regulariser, RegularOptions
from tools.building_shapes.rounds import fit_circle
from tools.building_shapes.silhouette import silhouette, trace_outer

CELL, CLOSE = 10.0, 40.0


def _quad(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[[x0, y0], [x1, y0], [x1, y1]], [[x0, y0], [x1, y1], [x0, y1]]], float)


def _poly(points: list[tuple[float, float]]) -> np.ndarray:
    """A convex polygon as a triangle fan."""
    return np.array([[points[0], points[i], points[i + 1]] for i in range(1, len(points) - 1)])


def _disc(cx: float, cy: float, r: float, sides: int = 90) -> np.ndarray:
    ring = [
        (cx + r * math.cos(t), cy + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, sides)
    ]
    return _poly(ring[:-1])


def _area(ring: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )


def _regular(ports: list[tuple[float, float]] | None = None) -> Regulariser:
    return Regulariser(RegularOptions(), ports)


def _edge_angles(ring: list[tuple[float, float]]) -> list[float]:
    return [
        math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    ]


def test_a_box_a_little_off_square_comes_out_square():
    """A wall two degrees off the building's axes traces as a staircase the plain
    simplification draws slanted; the regular one squares it."""
    turn = math.radians(2)
    corners = [(-300, -200), (300, -200), (300, 200), (-300, 200)]
    box = [
        (x * math.cos(turn) - y * math.sin(turn), x * math.sin(turn) + y * math.cos(turn))
        for x, y in corners
    ]
    plain = silhouette(_poly(box), CELL, CLOSE, 48)[0]
    ring = silhouette(_poly(box), CELL, CLOSE, 48, regular=_regular())[0]
    assert not all(angle % 90 == pytest.approx(0, abs=1e-6) for angle in _edge_angles(plain))
    assert len(ring) == 4
    assert all(angle % 90 == pytest.approx(0, abs=1e-6) for angle in _edge_angles(ring))
    assert _area(ring) > 0


def test_a_disc_becomes_a_regular_polygon():
    regular = _regular()
    ring = silhouette(_disc(50, -30, 180), CELL, CLOSE, 48, regular=regular)[0]
    assert regular.log[-1].kind == "round"
    assert len(ring) == 24
    cx, cy = sum(x for x, _y in ring) / 24, sum(y for _x, y in ring) / 24
    assert math.hypot(cx - 50, cy + 30) < CELL
    radii = [math.hypot(x - cx, y - cy) for x, y in ring]
    assert max(radii) - min(radii) < 1e-6
    plain = silhouette(_disc(50, -30, 180), CELL, CLOSE, 48)[0]
    assert _area(ring) == pytest.approx(_area(plain), rel=0.01)


def test_a_round_end_is_drawn_as_an_arc_and_the_flat_sides_stay_straight():
    """A pill: two straight walls and two half circles."""
    tris = np.concatenate([_quad(-300, -150, 300, 150), _disc(-300, 0, 150), _disc(300, 0, 150)])
    regular = _regular()
    ring = silhouette(tris, CELL, CLOSE, 48, regular=regular)[0]
    assert regular.log[-1].kind == "straight"
    radii = sorted(min(math.hypot(x + 300, y), math.hypot(x - 300, y)) for x, y in ring)
    assert radii[len(radii) // 2] == pytest.approx(150, abs=10)
    flat = [a for a in _edge_angles(ring) if a == pytest.approx(0, abs=1e-6)]
    assert len(flat) == 2


def test_a_genuine_slant_is_kept_when_squaring_it_would_lose_too_much():
    """A wedge whose long side runs at 30 degrees: snapping that side to 0 would cut away a
    third of the shape, so it stays slanted."""
    tris = _poly([(0.0, 0.0), (600.0, 0.0), (600.0, 346.0)])
    ring = silhouette(tris, CELL, CLOSE, 48, regular=_regular())[0]
    assert any(25 < angle < 35 for angle in _edge_angles(ring))


def test_a_near_symmetric_outline_is_made_exactly_symmetric():
    """A T whose stem is a cell off centre comes out mirrored about the bar's middle."""
    tris = np.concatenate([_quad(-400, 100, 400, 300), _quad(-110, -300, 90, 100)])
    regular = _regular()
    ring = silhouette(tris, CELL, CLOSE, 48, regular=regular)[0]
    assert [axis.dim for axis in regular.axes] == [0]
    at = regular.axes[0].at
    mirrored = sorted((round(2 * at - x, 6), round(y, 6)) for x, y in ring)
    assert mirrored == sorted((round(x, 6), round(y, 6)) for x, y in ring)


def test_a_port_in_a_notch_keeps_its_notch():
    """Squaring would fill the notch the pipe plugs into; the port says it is real."""
    notch = np.concatenate(
        [_quad(-300, -300, -40, 300), _quad(40, -300, 300, 300), _quad(-40, -300, 40, 240)]
    )
    port = (0.0, 240.0)
    plain = silhouette(notch, CELL, CLOSE, 48)[0]
    ring = silhouette(notch, CELL, CLOSE, 48, regular=_regular([port]))[0]
    assert _gap(port, ring) <= _gap(port, plain) + RegularOptions().port_drift


def _gap(point: tuple[float, float], ring: list[tuple[float, float]]) -> float:
    return math.dist(point, nearest_on(point, ring))


def test_a_band_stays_inside_its_footprint():
    top = np.full((100, 100), -np.inf)
    top[10:90, 10:90] = 100.0
    top[20:80, 12:88] = 400.0  # a housing flush with the base's sides, a cell in
    options = BandOptions(budgets=(32,), min_floor=200.0, step=25.0, min_area=20_000.0)
    regular = _regular()
    footprint = silhouette(_quad(100, 100, 900, 900), CELL, CLOSE, 48, regular=regular)
    regular.parent = footprint
    bands = bands_from_tops(top, (0.0, 0.0), CELL, CLOSE, options, regular)
    assert bands
    xs = [x for ring in bands[0].rings for x, _y in ring]
    fx = [x for ring in footprint for x, _y in ring]
    assert min(xs) >= min(fx) - 1e-6 and max(xs) <= max(fx) + 1e-6


def test_a_vertex_outside_its_parent_is_pulled_onto_the_parent_edge():
    parent = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    ring = [(10.0, 10.0), (104.0, 10.0), (104.0, 90.0), (10.0, 90.0)]
    assert pulled_inside(ring, [parent]) == [
        (10.0, 10.0),
        (100.0, 10.0),
        (100.0, 90.0),
        (10.0, 90.0),
    ]


def test_a_staircase_circle_fits_closely_and_a_bevelled_corner_does_not():
    """In cells: a traced disc strays well under the arc tolerance from its circle, a wall,
    bevel and wall turning the same way well over it."""
    yy, xx = np.mgrid[:50, :50]
    corners = trace_outer((xx - 25) ** 2 + (yy - 25) ** 2 <= 20 * 20)
    circle = fit_circle([(float(c), float(r)) for c, r in corners], 0.5)
    assert circle is not None and circle.r == pytest.approx(20, abs=0.5)
    assert circle.rms < 0.25
    bevel = fit_circle([(0.0, 0.0), (10.0, 0.0), (14.0, 4.0), (14.0, 14.0)], 0.5, closed=False)
    assert bevel is not None and bevel.rms > 0.25

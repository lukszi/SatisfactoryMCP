"""Where the ground lattice under the rocks stops, the rock's height and share blend into the
field's fold instead of stepping at its last texel.

docs/map/renders.md section 25, "Where the lattice stops". Synthetic fixtures: no install.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.cache import DirectPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.render.draw import compose
from mapgen.render.ground import lift
from mapgen.render.ground.holes import hole_rim
from mapgen.render.ground.lift import lattice_edge
from mapgen.render.ground.surface import Window, band_grid, band_surfaces, span
from mapgen.terrain.sample import taps_linear, taps_pchip
from satisfactory_mcp.domain.spatial import heightfield as hf

#: One texel of the field to an output pixel.
N = 160
#: The lattice stops at this column; the field goes on to ``VOID``.
EDGE, VOID = 80, 140


def test_the_edge_falls_from_where_the_fold_stands_in_and_leaves_the_void_alone():
    lattice = np.zeros((20, N), np.float32)
    lattice[:, EDGE:] = hf.NODATA
    heights = np.zeros((20, N), np.float32)
    heights[:, VOID:] = hf.NODATA
    edge = lattice_edge(lattice, heights, 1.0)
    assert (edge[:, EDGE:VOID] == 255).all(), "the fold stands in wherever the lattice is empty"
    assert (edge[:, : EDGE - 8] == 0).all() and (edge[:, VOID + 8 :] == 0).all()
    assert np.all(np.diff(edge[0, EDGE - 8 : EDGE + 1].astype(int)) >= 0)
    assert 0 < edge[0, EDGE - 2] < 255, "a soft edge"


def _scene():
    """Ground at 30 m, its lattice west of ``EDGE``; east of it a cliff province, where the
    field's fold is the same 30 m; a rock a metre under that surface over all of it."""
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    height = np.full((N, N), 300, np.int16)
    height[:, VOID:] = hf.NODATA
    field = SimpleNamespace(
        height_dm=height,
        provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: None,
        water_quality_raster=lambda: None,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2,
        spacing_cm=step_cm,
        width=N,
        height=N,
    )
    ground = height.astype(np.float32)
    ground[:, EDGE:] = hf.NODATA
    rock = np.where(height != hf.NODATA, 1, 0).astype(np.uint8)
    direct = DirectPlanes(np.full((N, N), 2900.0, np.float32), rock, ground, 1)
    return field, height.astype(np.float32), direct, step_cm / 100.0


def _rock_seen(field, heights, direct):
    """How much of each pixel the painted layer draws as rock, over the whole sheet."""
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    inputs = compose.GroundInputs(height_dm=heights, direct=direct)
    sources = compose._ground_sources(field, Window(0, N, 0, N), N, borrow, inputs)
    grid = band_grid(sources, span(0, N, (0, N), 0), span(0, N, (0, N), 0))
    surfaces, _owed = band_surfaces(sources, grid, {False})
    seen = surfaces[False].rock_seen
    assert seen is not None
    return np.array(seen)


def test_the_rock_s_share_blends_into_the_fold_s_where_the_lattice_stops(monkeypatch):
    """East of the edge the rock is the whole answer, west of it a rock under the surface
    hardly shows: the share they draw went from one to the other in a pixel."""
    field, heights, direct, spacing_m = _scene()
    monkeypatch.setattr(lift, "LATTICE_EDGE_BLUR_M", 3 * spacing_m)
    soft = _rock_seen(field, heights, direct)
    monkeypatch.setattr(compose, "lattice_edge", lambda ground, *_: np.zeros_like(ground, np.uint8))
    hard = _rock_seen(field, heights, direct)
    row = N // 2
    assert hard[row, EDGE - 12] < 0.1 and hard[row, EDGE + 2] == 1.0
    assert np.abs(np.diff(hard[row, EDGE - 12 : EDGE + 4])).max() > 0.8, "a step before"
    assert np.abs(np.diff(soft[row, EDGE - 12 : EDGE + 4])).max() < 0.3, "a ramp now"
    assert np.array_equal(soft[:, : EDGE - 12], hard[:, : EDGE - 12]), "the same bits away"
    assert np.array_equal(soft[:, EDGE:VOID], hard[:, EDGE:VOID])


def test_a_lattice_buried_under_its_rock_draws_no_crease_where_it_stops(monkeypatch):
    """A landscape 80 m under a rock that covers it, the fold beside it just over the rock:
    the heights ramp from the rock to the fold's lift instead of turning a corner in a pixel."""
    field, heights, direct, spacing_m = _scene()
    ground = direct.ground.copy()
    ground[:, :EDGE] = -500.0
    direct = DirectPlanes(np.full((N, N), 2990.0, np.float32), direct.coverage, ground, 1)
    monkeypatch.setattr(lift, "LATTICE_EDGE_BLUR_M", 3 * spacing_m)
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    inputs = compose.GroundInputs(height_dm=heights, direct=direct)
    sources = compose._ground_sources(field, Window(0, N, 0, N), N, borrow, inputs)
    grid = band_grid(sources, span(0, N, (0, N), 0), span(0, N, (0, N), 0))
    surfaces, _owed = band_surfaces(sources, grid, {False})
    row = np.array(surfaces[False].z_m)[N // 2, EDGE - 14 : EDGE + 4]
    assert abs(row[0] - 29.9) < 1e-3 and row[-1] > 30.05, "the rock, then the fold's lift"
    assert np.abs(np.diff(row, 2)).max() < 0.04, "a ramp, not a corner"


def _rim_scene(drop_dm: float):
    """A lattice at 0 dm west of a diagonal staircase of texels and the fold ``drop_dm`` under
    it east of the staircase, read at a quarter of a texel."""
    side = 40
    rows, cols = np.mgrid[0:side, 0:side]
    east = cols > rows
    ground = np.where(east, hf.NODATA, 0.0).astype(np.float32)
    heights = np.where(east, -drop_dm, 0.0).astype(np.float32)
    position = np.arange(10, 30, 0.25)
    taps = (taps_pchip(position, side), taps_pchip(position, side))
    linear = (taps_linear(position, side), taps_linear(position, side))
    return heights, ground, taps, linear, position


def test_a_hole_s_rim_follows_the_smoothed_outline_and_a_small_drop_is_left_alone():
    heights, ground, taps, linear, position = _rim_scene(500.0)
    rim = hole_rim(heights, ground, taps, linear, 1.0)
    assert rim is not None and rim.inside.any()
    # Where the drop is drawn, column by column: the staircase put it on the texel grid.
    low = rim.inside & (rim.z_dm < -250.0)
    first = np.argmax(low, axis=1)
    drop_at = position[first] - position
    assert np.ptp(drop_at[8:-8]) < 0.3, "the drop runs along the diagonal, not in steps"
    assert (rim.fold == low)[rim.inside].mean() > 0.95
    small, ground, taps, linear, _ = _rim_scene(30.0)
    assert hole_rim(small, ground, taps, linear, 1.0) is None, "a 3 m drop keeps its ramp"

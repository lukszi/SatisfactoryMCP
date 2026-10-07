"""Where the ground lattice under the rocks stops, the rock's height and share blend into the
field's fold instead of stepping at its last texel.

docs/map/renders.md section 25, "Where the lattice stops". Synthetic fixtures: no install.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.cache import DirectPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.render import compose, lift
from mapgen.render.lift import lattice_edge
from mapgen.render.surface import Window, band_grid, band_surfaces, span
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
    sources = compose._ground_sources(
        field, Window(0, N, 0, N), N, borrow, height_dm=heights, direct=direct, seam=None,
        regimes=None, measured_plane_u8=None, overlay=None, kernel=None, meshes=None,
        reach=None, rivers=None, surface=None, water_level=None, sea=None,
    )  # fmt: skip
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

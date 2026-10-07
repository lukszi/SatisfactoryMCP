"""The band and piece halos against the stencils a band's draw reads through
(``render/ground/stencils.py``), each measured on the code across rows and along them, and a
full-size window drawn in bands and in pieces against one band.

docs/map/renders.md section 40. Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M, RENDER_PX
from mapgen.lighting.hillshade import slope_degrees, sun_dot
from mapgen.lighting.model import surface_direct
from mapgen.palette import relief
from mapgen.palette.painted.surfaces import sunk_specks, top_cover
from mapgen.palette.scene import BandGrid
from mapgen.palette.water.shore import shore_terms
from mapgen.palette.water.surface import WATER_EDGE_BLUR_M, water_alpha
from mapgen.render.draw import compose
from mapgen.render.ground.stencils import STENCILS, band_halo, band_reach, piece_halo, piece_reach
from mapgen.terrain.render_meshes import MESH_CORAL
from mapgen.terrain.sample import frame_coordinates
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.draw import render_layer

#: Every size ``mapgen renders --size`` takes.
SIZES = [RENDER_PX >> shift for shift in range(6)]

#: A probe's plane: wider than any reach either side of the row or column it moves.
ROWS = COLS = 41
MOVED = ROWS // 2

#: Which way a probe moves its plane: a row, read across rows, or a column, read along them.
ACROSS, ALONG = 0, 1

#: Three bands of the full-size sheet, 64 columns wide: ``(r0, r1, c0, c1)``.
WINDOW = (40 * compose.BAND_ROWS, 43 * compose.BAND_ROWS, 4096, 4160)


def _spacing(size: int) -> float:
    """A pixel's edge in metres, as ``render.draw.compose`` works it out."""
    return (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size


def _plane(seed: int, scale: float, lo: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (lo + scale * rng.random((ROWS, COLS))).astype(np.float32)


def _reached(
    draw: Callable[[np.ndarray], np.ndarray], plane: np.ndarray, step: float, axis: int
) -> int:
    """How far from row (``axis`` 0) or column (1) ``MOVED`` the output of ``draw`` changes
    when that row or column of ``plane`` does."""
    moved = plane.copy()
    line = _plane(99, step)[0]
    if axis == ACROSS:
        moved[MOVED] += line
    else:
        moved[:, MOVED] += line
    diff = draw(moved) != draw(plane)
    changed = diff.reshape(ROWS, COLS, -1).any(axis=(1 - axis, 2))
    return int(np.abs(np.flatnonzero(changed) - MOVED).max())


def _gradients(size: int, axis: int) -> list[int]:
    sp = _spacing(size)

    def shore(heights: np.ndarray) -> np.ndarray:
        return np.stack(list(shore_terms(heights, sp, 0.0).values()), axis=-1)

    shade = {"suns": [(315.0, 45.0, 1.0)], "mode": "add", "k": 0.0, "dechroma": 0.0, "l_max": 1.0}
    flat = np.zeros(2, np.float32)
    ground = SimpleNamespace(palette={"shade": shade}, cool=flat, warm=flat)

    def relief_sun(heights: np.ndarray) -> np.ndarray:
        return relief._shade(np.zeros((ROWS, COLS, 3), np.float32), heights, sp, ground)[1]

    draws = [
        lambda heights: sun_dot(heights, sp),
        lambda heights: slope_degrees(heights, sp),
        lambda heights: surface_direct(heights, sp),
        relief_sun,
        shore,
    ]
    return [_reached(draw, _plane(1, 0.05 * sp), 0.01 * sp, axis) for draw in draws]


def _rock_top(size: int, axis: int) -> list[int]:
    """Faces about 45 degrees steep, inside the up-facing ramp; no patches."""
    sp = _spacing(size)
    z = np.arange(COLS, dtype=np.float32) * np.float32(sp) + _plane(2, 0.05 * sp)
    ground = SimpleNamespace(
        palette={"rock_top": {"up": (0.6, 0.85)}}, family_has_top=np.ones(1, np.float32)
    )
    code = np.zeros((ROWS, COLS), np.uint8)
    whole = (slice(0, ROWS), slice(0, COLS))

    def draw(heights: np.ndarray) -> np.ndarray:
        scene = {"z_m": heights, "grid": BandGrid(whole, 0, ROWS, 0, COLS, sp)}
        return top_cover(scene, ground, code)

    return [_reached(draw, z, 0.05 * sp, axis)]


def _water_blur(size: int, axis: int) -> list[int]:
    """Measured share 0, so the alpha is the wet share, blurred."""
    zero = np.zeros((ROWS, COLS), np.float32)
    blur_px = WATER_EDGE_BLUR_M / _spacing(size)

    def draw(wet: np.ndarray) -> np.ndarray:
        return water_alpha(zero, zero + 1.0, wet, zero, blur_px)

    return [_reached(draw, _plane(3, 0.2, 0.2), 0.5, axis)]


def _specks_own(axis: int) -> int:
    """Coral everywhere under water mostly covering it: every pixel is a speck."""
    depth = _plane(4, 5.0, 1.0)
    coral = np.full((ROWS, COLS), MESH_CORAL, np.uint8)
    ones = np.ones((ROWS, COLS), np.float32)

    def draw(cover: np.ndarray) -> np.ndarray:
        water = {"cover": cover, "depth_m": depth}
        sunk = sunk_specks({"water": water, "mesh_weight": ones, "mesh_class": coral})
        return np.stack([sunk["cover"], sunk["depth_m"]], axis=-1)

    return _reached(draw, _plane(5, 0.1, 0.8), -0.05, axis)


def _sunk_specks(size: int, axis: int) -> list[int]:
    """The 3 x 3 mean over the cover the blur and the shore crossing draw."""
    return [max(*_water_blur(size, axis), *_gradients(size, axis)) + _specks_own(axis)]


#: Each stencil a piece draws, measured on the functions at its sites, across rows or along.
PROBES: dict[str, Callable[[int, int], list[int]]] = {
    "gradient": _gradients,
    "rock top": _rock_top,
    "water edge blur": _water_blur,
    "sunk specks": _sunk_specks,
}


def _resolve(site: str) -> object:
    parts = site.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            found = importlib.import_module(".".join(["mapgen", *parts[:cut]]))
        except ModuleNotFoundError:
            continue
        for name in parts[cut:]:
            found = getattr(found, name)
        return found
    raise LookupError(site)


def test_every_site_names_a_function_of_the_draw():
    assert all(callable(_resolve(site)) for stencil in STENCILS for site in stencil.sites)
    assert len({stencil.name for stencil in STENCILS}) == len(STENCILS)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("stencil", [s for s in STENCILS if s.rows], ids=lambda s: s.name)
def test_each_stencil_reads_as_far_as_its_entry_says(stencil, size):
    assert set(PROBES[stencil.name](size, ACROSS)) == {stencil.reach(size)}


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("stencil", [s for s in STENCILS if s.pieces], ids=lambda s: s.name)
def test_each_stencil_a_piece_draws_reads_as_far_along_its_rows(stencil, size):
    assert set(PROBES[stencil.name](size, ALONG)) == {stencil.reach(size)}


def test_only_the_seam_trace_reads_the_band_whole():
    assert [s.name for s in STENCILS if not s.pieces] == ["seam trace"]


def test_the_widest_reach_is_the_specks_over_the_water_edge_blur():
    assert [band_reach(size) for size in SIZES] == [14, 7, 4, 3, 2, 2]
    assert [piece_reach(size) for size in SIZES] == [14, 7, 4, 3, 2, 2]
    assert band_halo() == piece_halo() == 16


@pytest.mark.parametrize("size", SIZES)
def test_the_band_halo_holds_every_stencil_at_every_size(size):
    assert compose.BAND_HALO >= band_reach(size)
    assert compose.PIECE_HALO >= piece_reach(size)


def _lake_field() -> SimpleNamespace:
    """Hills under a lake on 0.25 m texels over the window and 8 m round it: shores that
    cross the band edges at a slant."""
    x_cm, y_cm = frame_coordinates(RENDER_PX)
    r0, r1, c0, c1 = WINDOW
    step_cm, margin_cm = 25.0, 800.0
    x0, y0 = x_cm[c0] - margin_cm, y_cm[r0] - margin_cm
    rows = int((y_cm[r1 - 1] + margin_cm - y0) / step_cm) + 1
    cols = int((x_cm[c1 - 1] + margin_cm - x0) / step_cm) + 1
    y_m, x_m = np.mgrid[0:rows, 0:cols] * step_cm / 100.0
    height_m = 2.0 * np.sin(y_m / 3.1) * np.cos(x_m / 2.3) + 0.4 * np.sin(y_m / 0.9 + x_m / 1.3)
    height = np.round(height_m * hf.DM_PER_M).astype(np.int16)
    level = np.full(height.shape, 3, np.int16)
    grades = np.where(height < level, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    return SimpleNamespace(
        height_dm=height, provenance_plane=np.ones(height.shape, np.uint8),
        water_raster=lambda: level, water_quality_raster=lambda: grades,
        x0_cm=x0, y0_cm=y0, spacing_cm=step_cm, width=cols, height=rows,
    )  # fmt: skip


class _Capture:
    """The light's surface over the window."""

    def __init__(self):
        r0, r1, c0, c1 = WINDOW
        self.z = np.full((r1 - r0, c1 - c0), np.nan, np.float32)
        self.land = self.z.copy()

    def put(self, row, z_m, land, columns=slice(None)):
        r0, _r1, c0, _c1 = WINDOW
        rows = slice(row - r0, row - r0 + len(z_m))
        cols = slice(columns.start - c0, columns.stop - c0)
        self.z[rows, cols], self.land[rows, cols] = z_m, land


def _drawn(
    field: SimpleNamespace, layer: str, columns: int = WINDOW[3] - WINDOW[2]
) -> tuple[np.ndarray, _Capture]:
    capture = _Capture()
    borrow = (np.broadcast_to(np.int8(0), (SHEET_PX, SHEET_PX)), np.zeros_like(field.height_dm))
    rgb = render_layer(
        layer, field, np.full((1, 1, 3), 90.0, np.float32), 1, borrow, RENDER_PX, False,
        window=WINDOW, surface=capture, columns=columns,
    )  # fmt: skip
    return rgb, capture


@pytest.mark.parametrize("layer", ["terrain", "satellite"])
def test_a_full_size_window_drawn_in_bands_is_the_window_drawn_whole(monkeypatch, layer):
    field = _lake_field()
    rgb, capture = _drawn(field, layer)
    with monkeypatch.context() as patch:
        patch.setattr(compose, "BAND_ROWS", WINDOW[1] - WINDOW[0])
        whole_rgb, whole = _drawn(field, layer)
    assert rgb.tobytes() == whole_rgb.tobytes()
    assert capture.z.tobytes() == whole.z.tobytes()
    assert capture.land.tobytes() == whole.land.tobytes()

    monkeypatch.setattr(compose, "BAND_HALO", 8)
    _rgb, narrow = _drawn(field, layer)
    moved = np.flatnonzero((narrow.land != whole.land).any(axis=1))
    in_band = moved % compose.BAND_ROWS
    edge = np.minimum(in_band, compose.BAND_ROWS - 1 - in_band)
    assert moved.size, "the halo before the registry drew a seam at full size"
    assert edge.max() < band_reach(RENDER_PX) - 8, "only within the reach of a band edge"


#: Pieces a quarter of the window wide: three piece edges across each band.
PIECE = (WINDOW[3] - WINDOW[2]) // 4


@pytest.mark.parametrize("layer", ["terrain", "satellite"])
def test_a_full_size_window_drawn_in_pieces_is_the_window_drawn_whole(monkeypatch, layer):
    field = _lake_field()
    rgb, capture = _drawn(field, layer, PIECE)
    whole_rgb, whole = _drawn(field, layer)
    assert rgb.tobytes() == whole_rgb.tobytes()
    assert capture.z.tobytes() == whole.z.tobytes()
    assert capture.land.tobytes() == whole.land.tobytes()

    monkeypatch.setattr(compose, "PIECE_HALO", 8)
    _rgb, narrow = _drawn(field, layer, PIECE)
    moved = np.flatnonzero((narrow.land != whole.land).any(axis=0))
    in_piece = moved % PIECE
    edge = np.minimum(in_piece, PIECE - 1 - in_piece)
    assert moved.size, "a halo short of the reach draws a seam at full size"
    assert edge.max() < piece_reach(RENDER_PX) - 8, "only within the reach of a piece edge"

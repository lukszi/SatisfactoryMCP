"""The stencils a band's draw reads through (``render/stencils.py``), each measured on the code.

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
from mapgen.render.stencils import STENCILS, band_halo, band_reach
from mapgen.terrain.render_meshes import MESH_CORAL

#: Every size ``mapgen renders --size`` takes.
SIZES = [RENDER_PX >> shift for shift in range(6)]

#: A probe's plane: rows wider than any reach either side of the one it moves.
ROWS, COLS = 81, 24
MOVED = ROWS // 2


def _spacing(size: int) -> float:
    """A pixel's edge in metres, as ``render.compose`` works it out."""
    return (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size


def _plane(seed: int, scale: float, lo: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (lo + scale * rng.random((ROWS, COLS))).astype(np.float32)


def _rows_reached(draw: Callable[[np.ndarray], np.ndarray], plane: np.ndarray, step: float) -> int:
    """How far from row ``MOVED`` the output of ``draw`` changes when that row of ``plane`` does."""
    moved = plane.copy()
    moved[MOVED] += _plane(99, step)[0]
    changed = (draw(moved) != draw(plane)).reshape(ROWS, -1).any(axis=1)
    return int(np.abs(np.flatnonzero(changed) - MOVED).max())


def _gradients(size: int) -> list[int]:
    sp = _spacing(size)

    def shore(heights: np.ndarray) -> np.ndarray:
        return np.concatenate(list(shore_terms(heights, sp, 0.0).values()), axis=1)

    draws = [
        lambda heights: sun_dot(heights, sp),
        lambda heights: slope_degrees(heights, sp),
        lambda heights: surface_direct(heights, sp),
        lambda heights: relief._lambert(heights, sp, 315.0, 45.0),
        shore,
    ]
    return [_rows_reached(draw, _plane(1, 0.05 * sp), 0.01 * sp) for draw in draws]


def _rock_top(size: int) -> list[int]:
    """Faces about 45 degrees steep, inside the up-facing ramp; no patches."""
    sp = _spacing(size)
    z = np.arange(COLS, dtype=np.float32) * np.float32(sp) + _plane(2, 0.05 * sp)
    ground = SimpleNamespace(
        palette={"rock_top": {"up": (0.6, 0.85)}}, family_has_top=np.ones(1, np.float32)
    )
    code = np.zeros((ROWS, COLS), np.uint8)

    def draw(heights: np.ndarray) -> np.ndarray:
        scene = {"z_m": heights, "grid": BandGrid(slice(0, ROWS), 0, ROWS, 0, COLS, sp)}
        return top_cover(scene, ground, code)

    return [_rows_reached(draw, z, 0.05 * sp)]


def _water_blur(size: int) -> list[int]:
    """Measured share 0, so the alpha is the wet share, blurred."""
    zero = np.zeros((ROWS, COLS), np.float32)
    blur_px = WATER_EDGE_BLUR_M / _spacing(size)

    def draw(wet: np.ndarray) -> np.ndarray:
        return water_alpha(zero, zero + 1.0, wet, zero, blur_px)

    return [_rows_reached(draw, _plane(3, 0.2, 0.2), 0.5)]


def _specks_own() -> int:
    """Coral everywhere under water mostly covering it: every pixel is a speck."""
    depth = _plane(4, 5.0, 1.0)
    coral = np.full((ROWS, COLS), MESH_CORAL, np.uint8)
    ones = np.ones((ROWS, COLS), np.float32)

    def draw(cover: np.ndarray) -> np.ndarray:
        water = {"cover": cover, "depth_m": depth}
        sunk = sunk_specks({"water": water, "mesh_weight": ones, "mesh_class": coral})
        return np.concatenate([sunk["cover"], sunk["depth_m"]], axis=1)

    return _rows_reached(draw, _plane(5, 0.1, 0.8), -0.05)


def _sunk_specks(size: int) -> list[int]:
    """The 3 x 3 mean over the cover the blur and the shore crossing draw."""
    return [max(*_water_blur(size), *_gradients(size)) + _specks_own()]


#: Each stencil that reads across rows, measured on the functions at its sites.
PROBES: dict[str, Callable[[int], list[int]]] = {
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
    assert set(PROBES[stencil.name](size)) == {stencil.reach(size)}


def test_the_widest_reach_is_the_specks_over_the_water_edge_blur():
    assert [band_reach(size) for size in SIZES] == [14, 7, 4, 3, 2, 2]
    assert band_halo() == 16

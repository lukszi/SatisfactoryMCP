"""The light is one capture whatever layers a run draws, and in whatever order.

Every layer hands the light the surface the seabed rule draws. docs/spatial-and-map.md
section 29. Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.palette.relief import ReliefGround
from mapgen.palette.styles import RELIEF_PALETTES
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.render import compose
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK
from satisfactory_mcp.domain.spatial import heightfield as hf

#: One band of output, one texel of the field a pixel.
N = 96

#: The layers in the order a full run draws them.
LAYERS = ("terrain", "satellite", "painted", "relief", "relief-dark")


class _Surface:
    """What ``render_layer`` hands the light: the drawn heights and land weight."""

    def __init__(self) -> None:
        self.z = np.full((N, N), np.nan, np.float32)
        self.land = np.full((N, N), np.nan, np.float32)

    def put(self, row, z_m, land, columns=slice(None)):
        self.z[row : row + len(z_m), columns] = z_m
        self.land[row : row + len(z_m), columns] = land


def _scene() -> SimpleNamespace:
    """Land to the west and a measured sea 3 m deep, with render-only meshes: coral a hand
    under the sea's surface, a rock standing out of it, and coral on the land."""
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    rows, cols = np.mgrid[0:N, 0:N]
    land = cols < N // 3
    height = np.where(land, 300 + 2 * rows, -200).astype(np.int16)
    ocean_dm = round(OCEAN_LEVEL_M * hf.DM_PER_M)
    water = np.where(land, hf.NODATA, ocean_dm).astype(np.int16)
    grades = np.where(land, hf.WATER_DRY, hf.WATER_MEASURED).astype(np.uint8)
    field = SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: water, water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2, spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip
    mesh_z = np.zeros((N, N), np.float32)
    cls = np.zeros((N, N), np.uint8)
    for (r0, r1, c0, c1), top_m, kind in (
        ((10, 30, 50, 70), OCEAN_LEVEL_M - 0.3, MESH_CORAL),
        ((50, 60, 50, 60), OCEAN_LEVEL_M + 7.0, MESH_ROCK),
        ((60, 80, 5, 20), 45.0, MESH_CORAL),
    ):
        mesh_z[r0:r1, c0:c1], cls[r0:r1, c0:c1] = top_m * 100.0, kind
    return SimpleNamespace(field=field, heights=height.astype(np.float32),
                           meshes=MeshPlanes(mesh_z, cls))  # fmt: skip


def _painted_ground() -> SimpleNamespace:
    """The parts of the painted ground ``render_layer`` reads before the painter."""
    rock = [np.zeros((N // 4, N // 4), np.float32)]
    return SimpleNamespace(rock=rock, crowns=None, water_optics=lambda taps, river=None: None)


def _draw(scene, layer: str) -> _Surface:
    surface = _Surface()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    relief = None
    if layer in RELIEF_PALETTES:
        relief = ReliefGround(RELIEF_PALETTES[layer][0], scene.field, None, [])
    compose.render_layer(
        layer, scene.field, np.full((1, 1, 3), 90.0, np.float32), 1, borrow, N, False,
        scene.heights, meshes=scene.meshes, unlit=True, surface=surface,
        painted=_painted_ground() if layer == "painted" else None, relief=relief,
    )  # fmt: skip
    return surface


def test_every_layer_hands_the_light_the_same_surface(monkeypatch):
    seen: dict = {}

    def painter(scene, ground, sample, sample_rock):
        seen["z_m"], seen["ndl"] = scene["z_m"].copy(), scene["ndl"].copy()
        return np.zeros(scene["z_m"].shape + (3,), np.float32)

    monkeypatch.setattr(compose, "painted_colours", painter)
    scene = _scene()
    captured = {layer: _draw(scene, layer) for layer in LAYERS}
    first = captured["terrain"]
    assert np.isfinite(first.z).all() and np.isfinite(first.land).all()
    for layer in LAYERS[1:]:
        assert captured[layer].z.tobytes() == first.z.tobytes(), layer
        assert captured[layer].land.tobytes() == first.land.tobytes(), layer

    coral, rock = (slice(12, 28), slice(52, 68)), (slice(52, 58), slice(52, 58))
    assert (seen["z_m"][coral] > first.z[coral] + 2.0).all(), "the painted layer draws the coral"
    assert (first.land[coral] < 0.01).all(), "and the light has the sea there"
    assert (first.land[rock] > 0.99).all(), "a rock out of the sea is land in it"
    lit = seen["ndl"] != np.float32(np.sin(np.deg2rad(45.0)))
    assert lit.any() and lit[10:30, 50:70].sum() == lit.sum(), "the coral keeps the default sun"

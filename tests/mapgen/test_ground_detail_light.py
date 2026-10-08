"""The ground's detail where it is drawn: the painted layer reads it, the light keeps its
normal beside the surface, hashes it, and tilts the normal tiles by it.

docs/map/painted.md section 30, "The layers' own textures". Synthetic fixtures: no install,
no field on disk.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.light_tiles import DETAIL_SCALE, detail_window, with_detail
from mapgen.lighting.stage import Surface, discard
from mapgen.render.draw import painting
from mapgen.render.ground.detail import detail_bytes, drawn_share
from mapgen.terrain.ground_detail.reference import GroundDetail
from mapgen.terrain.ground_detail.textures import load_detail_textures
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.draw import draw_layers
from tests.support.ground_textures import write_textured_store

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

#: One band of output, one texel of the field a pixel.
N = 64


class _Capture:
    """What the pass hands the light, the detail normal's bytes included."""

    def __init__(self) -> None:
        self.detail = np.full((N, N, 2), -128, np.int16)
        self.calls = 0

    def put(self, row, z_m, land, columns=slice(None), slabs=None, *, detail=None):
        self.calls += 1
        if detail is not None:
            self.detail[row : row + len(z_m), columns] = detail


def _field() -> SimpleNamespace:
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    rows, cols = np.mgrid[0:N, 0:N]
    height = (300 + 3 * rows + 2 * cols).astype(np.int16)
    return SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: None, water_quality_raster=lambda: None,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2, spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip


@pytest.fixture(scope="module")
def textures(tmp_path_factory):
    cols = np.arange(N)[None, :].repeat(N, 0)
    weights = {
        "Gravel_WeightLayerInfo": np.where(cols < N // 2, 255, 0).astype(np.uint8),
        "Sand_LayerInfo": np.where(cols >= N // 2, 255, 0).astype(np.uint8),
    }
    weights["Sand_LayerInfo"][:, N - 8 :] = 0
    found = load_detail_textures(write_textured_store(tmp_path_factory.mktemp("p"), weights), 0.2)
    assert found is not None
    return found


def _painted_ground() -> SimpleNamespace:
    rock = [np.zeros((N // 4, N // 4), np.float32)]
    return SimpleNamespace(rock=rock, rock_family=None, titan=None, crowns=None,
                           water_optics=lambda taps, river=None: None)  # fmt: skip


def test_the_painted_layer_and_the_light_read_the_detail_in_any_pieces(monkeypatch, textures):
    seen: list[GroundDetail | None] = []

    def painter(scene, ground, sample, sample_rock):
        seen.append(scene.get("detail"))
        return np.zeros(scene["z_m"].shape + (3,), np.float32)

    monkeypatch.setattr(painting, "painted_colours", painter)
    field = _field()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    captured = []
    for columns in (N, 13):
        capture = _Capture()
        draw_layers(
            ("painted",), field, 1, borrow, N, False, field.height_dm.astype(np.float32),
            unlit=True, surface=capture, painted=_painted_ground(), textures=textures,
            columns=columns,
        )  # fmt: skip
        captured.append(capture.detail)
    assert captured[0].tobytes() == captured[1].tobytes(), "pieces capture what rows do"
    detail = captured[0]
    assert (detail >= -127).all(), "every pixel's detail is captured"
    assert detail[:, : N // 2 - 1].any(), "the gravel's bumps"
    assert not detail[:, N - 6 :].any(), "nothing painted, no bumps"
    assert all(d is not None for d in seen) and any(d.ratio.std() > 0 for d in seen if d)


def test_rock_and_meshes_hide_the_detail_from_the_light():
    normal = np.full((2, 3, 2), 0.5, np.float32)
    detail = GroundDetail(np.ones((2, 3, 3), np.float32), normal)
    rock = np.array([[0.0, 1.0, 0.5], [0.0, 0.0, 0.0]], np.float32)
    mesh = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.5]], np.float32)
    drawn = drawn_share((2, 3), rock, None, mesh)
    got = detail_bytes(detail, drawn)[..., 0]
    assert got.tolist() == [[64, 0, 32], [64, 0, 32]]
    assert not detail_bytes(None, drawn).any()


def test_the_surface_keeps_the_detail_and_its_digest_holds_it(tmp_path):
    z, land = np.zeros((4, 16), np.float32), np.ones((4, 16), np.float32)
    bumps = np.zeros((4, 16, 2), np.int8)
    bumps[1, 3] = (90, -20)
    plain, bumped = Surface(tmp_path / "a", 16), Surface(tmp_path / "b", 16)
    plain.put(0, z, land)
    bumped.put(0, z, land, detail=bumps)
    assert plain.digest() != bumped.digest()
    assert plain.detail is None and not plain.path("detail").exists()
    assert bumped.detail is not None and bumped.detail[1, 3].tolist() == [90, -20]
    for surface in (plain, bumped):
        surface.close()
    window = detail_window(tmp_path / "b", 0, 0, 4)
    assert window is not None and window[1, 3].tolist() == [90, -20]
    assert detail_window(tmp_path / "a", 0, 0, 4) is None
    del window
    discard(bumped)
    assert not bumped.path("detail").exists()


def test_the_detail_tilts_the_normal_and_none_leaves_it():
    east, south = np.zeros((2, 2), np.float32), np.zeros((2, 2), np.float32)
    assert with_detail(east, south, None) == (east, south)
    bumps = np.zeros((2, 2, 2), np.int8)
    bumps[0, 0] = (int(DETAIL_SCALE), 0)
    tilted_e, tilted_s = with_detail(east, south, bumps)
    assert tilted_e[0, 0] == pytest.approx(np.sqrt(0.5)) and tilted_s[0, 0] == 0
    assert tilted_e[1, 1] == 0 and tilted_s[1, 1] == 0
    slope_e = np.full((2, 2), 0.6, np.float32)
    up = with_detail(slope_e, south, np.zeros((2, 2, 2), np.int8))
    assert np.allclose(up[0], 0.6), "no bumps, the same normal"


def test_a_field_without_textures_hands_the_light_no_detail():
    field = _field()
    capture = _Capture()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    draw_layers(("terrain",), field, 1, borrow, N, False, field.height_dm.astype(np.float32),
                unlit=True, surface=capture)  # fmt: skip
    assert capture.calls and (capture.detail == -128).all()
    assert hf.NODATA not in field.height_dm

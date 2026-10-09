"""The ground's detail under the bake: the layers' textures at the shaders' repeats, the
leading layers per texel, the height blend, the cells, the overlay, and the CUDA kernel's
bits.

docs/map/painted.md section 30, "The layers' own textures". A synthetic store
(``tests/support/ground_textures.py``); the kernel tests skip, saying why, on a machine
without CuPy or a CUDA device.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from mapgen import jit
from mapgen.gamedata.ground.layer_textures import (
    CELLS_NONE,
    CELLS_OFFSET,
    CELLS_ROTATED,
    LAYERS,
    NOISE_TILE_M,
    OVERLAYS,
    TEXTURES,
    decode_ground_textures,
    encode_ground_textures,
)
from mapgen.terrain.ground_detail.reference import detail_piece, ground_detail
from mapgen.terrain.ground_detail.textures import (
    DETAIL_MAX_SPACING_M,
    NO_LAYER,
    TOP_LAYERS,
    load_detail_textures,
)
from mapgen.terrain.sample import grid_position, taps_linear
from tests.support.ground_textures import texture_rgba, write_textured_store

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

#: The paint grid's texels a side, and the run's pixel in metres.
GRID = 48
SPACING_M = 0.229


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _weights() -> dict[str, np.ndarray]:
    """Grass west, sand east, gravel in a soft band between them, puddles over a corner, red
    grass in a corner, nothing painted in the south rows."""
    cols = np.arange(GRID)[None, :].repeat(GRID, 0)
    grass = np.clip((28 - cols) * 32, 0, 255)
    sand = np.clip((cols - 20) * 32, 0, 255)
    gravel = np.clip(255 - np.abs(cols - 24) * 40, 0, 255)
    red = np.zeros((GRID, GRID))
    red[:10, :10] = 200
    puddles = np.zeros((GRID, GRID))
    puddles[5:20, 30:44] = 180
    planes = {"Grass_LayerInfo": grass, "Sand_LayerInfo": sand, "Gravel_WeightLayerInfo": gravel,
              "GrassRed_LayerInfo": red, "Puddles_LayerInfo": puddles}  # fmt: skip
    for plane in planes.values():
        plane[GRID - 6 :] = 0
    return {name: plane.astype(np.uint8) for name, plane in planes.items()}


@pytest.fixture(scope="module")
def textures(tmp_path_factory):
    store = write_textured_store(tmp_path_factory.mktemp("paint"), _weights())
    found = load_detail_textures(store, SPACING_M)
    assert found is not None
    return found


def _piece(textures, rows: tuple[float, float, int], cols: tuple[float, float, int]):
    """The piece over metres ``[start, stop)`` at ``count`` pixels along each axis."""
    centres = [np.linspace(a, b, n, endpoint=False) + (b - a) / n / 2 for a, b, n in (rows, cols)]
    taps = tuple(taps_linear(grid_position(c * 100.0, 0.0, 100.0, GRID), GRID) for c in centres)
    v, u = (c.astype(np.float32) for c in centres)
    return detail_piece(textures, taps, u, v)


# ----------------------------------------------------------------------------- the recipe


def test_every_read_is_a_texture_the_store_keeps_and_every_kept_texture_is_read():
    reads = {r.texture for layer in (*LAYERS.values(), *OVERLAYS.values()) for r in layer if r}
    assert reads == set(TEXTURES)


def test_the_repeats_are_the_shaders_far_scales():
    grass, forest, sand = (LAYERS[f"{n}_LayerInfo"] for n in ("Grass", "Forest", "Sand"))
    assert grass.albedo.tile_m == 50.0 and grass.normal.tile_m == 4.0
    assert forest.albedo.tile_m == pytest.approx(14.2857, abs=1e-4) and forest.normal is None
    assert sand.albedo.tile_m == 20.0 and sand.height.tile_m == 4.0
    assert sand.albedo.strength == 0.5 and sand.normal.strength == 0.2
    assert LAYERS["SandRock_LayerInfo"].albedo.tile_m == pytest.approx(4.5454545)
    assert LAYERS["Gravel_WeightLayerInfo"].albedo.cells == CELLS_ROTATED
    assert LAYERS["SandRipples_LayerInfo"].height.cells == CELLS_OFFSET
    assert LAYERS["Grass_LayerInfo"].albedo.cells == CELLS_NONE and NOISE_TILE_M == 100.0


def test_the_store_blob_reads_back_as_it_was_written(tmp_path):
    blob, index = encode_ground_textures(texture_rgba)
    (tmp_path / "t.z").write_bytes(blob)
    found = decode_ground_textures(tmp_path / "t.z", index)
    for name, (asset, kept) in TEXTURES.items():
        want = texture_rgba(asset, 512)[..., list(kept)]
        assert found[name].tobytes() == want.tobytes(), name


def test_a_pixel_as_wide_as_the_bake_s_metre_draws_no_detail(tmp_path):
    store = write_textured_store(tmp_path, _weights())
    assert load_detail_textures(store, DETAIL_MAX_SPACING_M) is None
    assert load_detail_textures(tmp_path / "none", SPACING_M) is None


# ----------------------------------------------------------------------- the leading layers


def test_each_texel_keeps_its_heaviest_layers_heaviest_first(textures):
    weights = _weights()
    names = [n for n in textures.layers if n in LAYERS]
    stack = np.stack([weights.get(n, np.zeros((GRID, GRID), np.uint8)) for n in names])
    for r, c in [(0, 0), (12, 22), (30, 24), (40, 47), (GRID - 1, 3)]:
        held = [(int(textures.weights[k, r, c]), int(textures.ids[k, r, c])) for k in range(4)]
        want = sorted(((int(w), i) for i, w in enumerate(stack[:, r, c]) if w), key=lambda t: -t[0])
        want = want[:TOP_LAYERS] + [(0, NO_LAYER)] * (TOP_LAYERS - len(want[:TOP_LAYERS]))
        assert held == want, (r, c)


# ---------------------------------------------------------------------------- the detail


def test_unpainted_ground_has_no_detail(textures):
    assert _piece(textures, (43.0, 47.0, 8), (2.0, 40.0, 30)) is None


def test_the_detail_keeps_the_ground_s_mean(textures):
    piece = _piece(textures, (2.0, 40.0, 160), (2.0, 46.0, 190))
    detail = ground_detail(textures, piece)
    assert abs(float(detail.ratio.mean()) - 1.0) < 0.05
    assert detail.ratio.std() > 0.02, "the textures show"
    assert np.isfinite(detail.ratio).all() and np.isfinite(detail.normal).all()


def test_a_flat_texture_adds_nothing(textures):
    alone = ground_detail(textures, _piece(textures, (20.0, 24.0, 16), (2.0, 6.0, 16)))
    assert alone.ratio.std() > 0.01
    flat = replace(textures, colour=textures.colour._replace(texels=textures.colour.texels * 0 + 1))
    level = ground_detail(flat, _piece(flat, (20.0, 24.0, 16), (2.0, 6.0, 16)))
    assert level.ratio.tobytes() == np.ones_like(level.ratio).tobytes()


def test_the_layer_standing_higher_wins_an_even_blend(tmp_path):
    """Grass and soil half and half: grass's height texture at 1 and soil's at 0 leave grass
    alone showing, as the engine's ``clamp(2 w - 1 + h, 1e-4, 1)`` does."""
    half = np.full((GRID, GRID), 127, np.uint8)
    stores = (
        ({"Grass_LayerInfo": half, "Soil_LayerInfo": half}, (1.0, 0.0)),
        ({"Grass_LayerInfo": half * 0 + 255}, (1.0,)),
    )
    made = []
    for k, (weights, heights) in enumerate(stores):
        found = load_detail_textures(write_textured_store(tmp_path / str(k), weights), SPACING_M)
        assert found is not None
        texels = found.surface.texels.copy()
        for layer, height in enumerate(heights):
            x, y, w, h = found.surface.tiles[found.table.tiles[layer, 2]]
            texels[y : y + h, x : x + w] = height
        made.append(replace(found, surface=found.surface._replace(texels=texels)))
    span = ((10.0, 14.0, 12), (10.0, 14.0, 12))
    mixed, own = (ground_detail(t, _piece(t, *span)) for t in made)
    assert np.allclose(mixed.ratio, own.ratio, atol=1e-3)
    assert np.allclose(mixed.normal, own.normal, atol=1e-3)


def test_cells_with_no_mask_read_as_the_plain_tiling(textures):
    piece = _piece(textures, (2.0, 30.0, 64), (20.0, 30.0, 40))
    masked = textures.noise.copy()
    masked[..., 5] = 0.25  # 2A - 0.5 is 0: the cell's read mixes in at nothing
    plain_table = textures.table._replace(cells=np.zeros_like(textures.table.cells))
    no_mask = ground_detail(replace(textures, noise=masked), piece)
    no_cells = ground_detail(replace(textures, table=plain_table), piece)
    assert no_mask.ratio.tobytes() == no_cells.ratio.tobytes()
    assert no_mask.normal.tobytes() == no_cells.normal.tobytes()
    turned = ground_detail(textures, piece)
    assert not np.array_equal(turned.ratio, no_cells.ratio), "the cells turn the read"


def test_the_overlay_lerps_over_the_blend_by_its_weight(textures):
    piece = _piece(textures, (8.0, 16.0, 24), (32.0, 42.0, 30))
    wet = ground_detail(textures, piece)
    dry = ground_detail(replace(textures, overlay=-1), piece)
    assert not np.array_equal(wet.ratio, dry.ratio)
    none = replace(textures, overlay_weight=np.zeros_like(textures.overlay_weight))
    unsoaked = _piece(none, (8.0, 16.0, 24), (32.0, 42.0, 30))
    assert ground_detail(none, unsoaked).ratio.tobytes() == dry.ratio.tobytes()


# ----------------------------------------------------------------------------- on the GPU


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize(
    ("rows", "cols"),
    [((2.0, 40.0, 97), (2.0, 46.0, 131)), ((8.0, 16.0, 24), (32.0, 42.0, 30)),
     ((40.0, 47.0, 33), (0.0, 47.9, 77)), ((20.0, 20.5, 1), (5.0, 5.2, 1))],
)  # fmt: skip
def test_the_detail_on_the_device_is_the_reference_s(textures, rows, cols):
    from mapgen.render.gpu.ground import ground_detail as on_device

    piece = _piece(textures, rows, cols)
    assert piece is not None
    want = ground_detail(textures, piece)
    got = on_device(textures, piece)
    assert got is not None
    assert got.ratio.tobytes() == want.ratio.tobytes()
    assert got.normal.tobytes() == want.normal.tobytes()

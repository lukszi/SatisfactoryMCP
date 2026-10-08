"""The crown sprite raster: a mesh from straight above with its textures, and its GPU twin.

docs/map/light-and-crowns.md section 36, "Crown sprites". Synthetic card meshes throughout:
no install. The twin's test skips, saying why, on a machine without numba, CuPy or a device.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen import jit
from mapgen.gamedata.vegetation.tree_surface import (
    SlotShading,
    SlotTexture,
    SurfaceMesh,
    is_mask,
    texture_side,
    uv_scale_cm,
)
from mapgen.sprites import fill, raster, shade

#: pytest puts its own filters before ``mapgen.jit``'s, which quiets this one in a render.
pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

LEAF = (0.10, 0.30, 0.05)
BARK = (0.20, 0.12, 0.08)


def _card(x0, y0, x1, y1, z, slot, down=False, tiles=1.0):
    """A flat card, mesh cm, UVs 0 to ``tiles`` across it, its normal up (or down)."""
    verts = np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], np.float32)
    uvs = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32) * np.float32(tiles)
    normals = np.tile(np.array([0, 0, -1.0 if down else 1.0], np.float32), (4, 1))
    tris = np.array([[0, 1, 2], [0, 2, 3]], np.int64)
    return verts, uvs, normals, tris, np.full(2, slot, np.int64)


def _mesh(*cards):
    parts = list(zip(*cards))
    offsets = np.cumsum([0] + [len(v) for v in parts[0][:-1]])
    tris = np.concatenate([t + o for t, o in zip(parts[3], offsets)])
    return SurfaceMesh(
        verts=np.concatenate(parts[0]),
        uvs=np.concatenate(parts[1]),
        normals=np.concatenate(parts[2]),
        tris=tris,
        slots=np.concatenate(parts[4]),
        materials=["leaf", "bark"],
        tangents=np.tile(np.array([1, 0, 0], np.float32), (len(tris) * 2, 1)),
        signs=np.ones(len(tris) * 2, np.float32),
    )


def _texture(colour, alpha=None, kind="leaf", shading=None):
    """A 16 x 16 slot texture of one colour; ``alpha`` the mask, unmasked when None."""
    albedo = np.tile(np.array(colour, np.float32), (16, 16, 1))
    shading = shading or SlotShading()
    if alpha is None:
        return SlotTexture(kind, albedo, np.full((16, 16), 255, np.uint8), False, shading)
    return SlotTexture(kind, albedo, alpha.astype(np.uint8), True, shading)


def _normal_map(x, y):
    """A 4 x 4 normal map of one tangent-space normal."""
    return np.tile(np.array([x, y, np.sqrt(1 - x * x - y * y)], np.float32), (4, 4, 1))


def _left_half():
    alpha = np.zeros((16, 16), np.uint8)
    alpha[:, :8] = 255
    return alpha


def test_the_grid_is_the_paint_store_s_rows_along_y_and_one_texel_of_room():
    mesh = _mesh(_card(0, 0, 100, 50, 300, 0))
    planes = raster.rasterise(mesh, [_texture(LEAF), _texture(BARK, kind="bark")])
    assert planes is not None
    assert (planes.x0_cm, planes.y0_cm) == (-raster.SPRITE_CM, -raster.SPRITE_CM)
    assert planes.alpha.shape == (4 + 2, 8 + 2), "rows run along +Y, columns along +X"
    inside = planes.alpha[1:-1, 1:-1]
    assert inside.min() == 1.0 and planes.alpha.sum() == inside.sum()
    assert np.allclose(planes.top_cm[1:-1, 1:-1], 300.0)
    assert np.allclose(planes.colour[1:-1, 1:-1], LEAF)


def test_a_masked_card_covers_its_mask_and_a_card_below_shows_through_the_holes():
    upper = _card(0, 0, 200, 200, 800, 0)
    lower = _card(0, 0, 200, 200, 500, 1)
    textures = [_texture(LEAF, _left_half()), _texture(BARK, kind="bark")]
    alone = raster.rasterise(_mesh(upper), textures)
    assert alone is not None
    card = alone.alpha[1:-1, 1:-1]
    assert card.shape == (16, 16)
    assert card[:, :7].min() == 1.0 and card[:, -7:].max() == 0.0, "the mask's left half"
    assert card.sum() == pytest.approx(16 * 8, rel=0.1)
    both = raster.rasterise(_mesh(upper, lower), textures)
    assert both is not None
    body = (slice(1, -1), slice(1, -1))
    assert both.alpha[body].min() == 1.0
    left, right = both.top_cm[body][:, :6], both.top_cm[body][:, -6:]
    assert np.all(left == 800.0) and np.all(right == 500.0)
    assert np.allclose(both.colour[body][:, :6], LEAF) and np.allclose(
        both.colour[body][:, -6:], BARK
    )


def test_a_card_seen_from_its_back_still_faces_up_and_an_edge_on_one_draws_nothing():
    down = raster.rasterise(_mesh(_card(0, 0, 100, 100, 300, 0, down=True)), [_texture(LEAF)])
    assert down is not None and np.allclose(down.normal[down.alpha > 0], (0.0, 0.0, 1.0))
    wall = SurfaceMesh(
        verts=np.array([[0, 0, 0], [100, 0, 0], [100, 0, 300]], np.float32),
        uvs=np.zeros((3, 2), np.float32),
        normals=np.tile(np.array([0, 1, 0], np.float32), (3, 1)),
        tris=np.array([[0, 1, 2]], np.int64),
        slots=np.zeros(1, np.int64),
        materials=["leaf"],
    )
    planes = raster.rasterise(wall, [_texture(LEAF)])
    assert planes is not None and planes.alpha.sum() == 0


def test_skipped_slots_are_not_drawn_and_nothing_drawn_is_no_sprite():
    mesh = _mesh(_card(0, 0, 100, 100, 300, 0))
    skip = SlotTexture("skip", np.zeros((1, 1, 3), np.float32), np.zeros((1, 1), np.uint8), False)
    assert raster.rasterise(mesh, [skip]) is None


def test_ties_keep_the_first_triangle_and_only_a_tiled_card_wraps_its_mask():
    mesh = _mesh(_card(0, 0, 100, 100, 300, 0), _card(0, 0, 100, 100, 300, 1))
    textures = [_texture(LEAF), _texture(BARK, kind="bark")]
    planes = raster.rasterise(mesh, textures)
    assert planes is not None and np.allclose(planes.colour[planes.alpha > 0], LEAF)
    texels = np.zeros(16, np.uint8)
    texels[:4] = 255  # a 4 x 4 mask, its first row opaque
    row = np.array([0, 4, 4, 1], np.int32)
    u = np.array([0.5, 1.5, -0.5], np.float32)
    v = np.array([0.125, 1.125, -0.875], np.float32)
    assert np.all(fill.mask_at(texels, row, u, v, True) == 255.0)
    last = np.zeros(16, np.uint8)
    last[3::4] = 255  # only the last column opaque
    edge = np.array([0.999], np.float32), np.array([0.5], np.float32)
    assert fill.mask_at(last, row, *edge, False)[0] == 255.0, "a card holds its edge"
    assert fill.mask_at(last, row, *edge, True)[0] < 255.0, "a tiled one reads round"


def test_the_mask_is_a_cut_out_and_textures_are_read_at_the_sample_s_size():
    assert is_mask(np.where(np.arange(256) < 100, 0, 255).astype(np.uint8))
    assert not is_mask(np.linspace(0, 255, 256).astype(np.uint8)), "a gradient is subsurface"
    assert not is_mask(np.full(256, 255, np.uint8)), "all opaque masks nothing"
    assert texture_side(400.0, raster.SAMPLE_CM) == 128
    assert texture_side(None, raster.SAMPLE_CM) == 16
    mesh = _mesh(_card(0, 0, 200, 200, 300, 0))
    assert uv_scale_cm(mesh, 0) == pytest.approx(200.0)
    assert uv_scale_cm(mesh, 1) is None


# ---------------------------------------------------------------------- the shading


def test_a_normal_map_turns_the_card_through_its_tangent_basis():
    tilted = SlotShading(normal_map=_normal_map(0.6, 0.0))
    up = raster.rasterise(_mesh(_card(0, 0, 100, 100, 300, 0)), [_texture(LEAF, shading=tilted)])
    assert up is not None and np.allclose(up.normal[up.alpha == 1], (0.6, 0.0, 0.8), atol=1e-5)
    across = SlotShading(normal_map=_normal_map(0.0, 0.6))
    side = raster.rasterise(_mesh(_card(0, 0, 100, 100, 300, 0)), [_texture(LEAF, shading=across)])
    assert side is not None and np.allclose(side.normal[side.alpha == 1], (0, 0.6, 0.8), atol=1e-5)
    back = raster.rasterise(
        _mesh(_card(0, 0, 100, 100, 300, 0, down=True)), [_texture(LEAF, shading=across)]
    )
    assert back is not None
    assert np.allclose(back.normal[back.alpha == 1], (0, -0.6, 0.8), atol=1e-5), (
        "a back face keeps the front's bitangent, as the engine's two-sided foliage does"
    )


def test_spherical_normals_bend_toward_the_pivot_and_moss_covers_a_bark_turned_up():
    sphere = SlotShading(sphere=1.0, pivot_cm=(0.0, 0.0, 0.0))
    planes = raster.rasterise(
        _mesh(_card(0, 0, 100, 100, 300, 0)), [_texture(LEAF, shading=sphere)]
    )
    assert planes is not None
    centre = np.array([-12.5 + 8.5 * raster.SPRITE_CM, -12.5 + 4.5 * raster.SPRITE_CM, 300.0])
    assert np.allclose(planes.normal[4, 8], centre / np.linalg.norm(centre), atol=2e-3)
    moss = SlotShading(moss=(0.0, 1.0, 0.0), moss_low=0.5, moss_gain=2.0)
    flat = raster.rasterise(
        _mesh(_card(0, 0, 100, 100, 300, 0)), [_texture(BARK, kind="bark", shading=moss)]
    )
    assert flat is not None and np.allclose(flat.colour[flat.alpha == 1], (0, 1, 0)), "full moss"
    leaning = SlotShading(
        normal_map=_normal_map(0.8, 0.0), moss=(0.0, 1.0, 0.0), moss_low=0.5, moss_gain=2.0
    )
    steep = raster.rasterise(
        _mesh(_card(0, 0, 100, 100, 300, 0)), [_texture(BARK, kind="bark", shading=leaning)]
    )
    assert steep is not None
    expected = np.array(BARK) + (np.array([0, 1, 0]) - np.array(BARK)) * 0.2
    assert np.allclose(steep.colour[steep.alpha == 1], expected, atol=1e-5), "z 0.6: a fifth"


# ------------------------------------------------------------------------- the twin


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


@pytest.mark.usefixtures("device")
def test_the_gpu_fill_gives_the_reference_s_bits(monkeypatch):
    rng = np.random.default_rng(7)
    cards = []
    for k in range(40):
        x, y = rng.uniform(-400, 400, 2)
        w, h = rng.uniform(30, 300, 2)
        cards.append(_card(x, y, x + w, y + h, rng.uniform(0, 900), k % 2, tiles=1 + k % 3))
    mesh = _mesh(*cards)
    noise = (rng.uniform(0, 1, (16, 16)) > 0.4) * 255
    textures = [_texture(LEAF, noise), _texture(BARK, kind="bark")]
    drawn = np.ones(len(mesh.tris), bool)
    grid = raster.sprite_grid(mesh.verts)
    tris = raster.triangle_setup(mesh, drawn, grid)
    alpha = raster.pack_alpha(textures)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = raster.top_hits(tris, alpha, grid)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    on_gpu = raster.top_hits(tris, alpha, grid)
    for field in ("z", "tri", "b1", "b2"):
        a, b = getattr(reference, field), getattr(on_gpu, field)
        assert (a.dtype, a.shape) == (b.dtype, b.shape)
        assert a.tobytes() == b.tobytes(), field
    assert (reference.tri >= 0).mean() > 0.3


@pytest.mark.usefixtures("device")
def test_the_gpu_shading_gives_the_reference_s_bits(monkeypatch):
    rng = np.random.default_rng(11)
    cards = []
    for k in range(40):
        x, y = rng.uniform(-400, 400, 2)
        w, h = rng.uniform(30, 300, 2)
        down = bool(k % 5 == 0)
        cards.append(_card(x, y, x + w, y + h, rng.uniform(0, 900), k % 2, down, 1 + k % 3))
    mesh = _mesh(*cards)
    maps = rng.normal(0, 0.4, (8, 8, 3)).astype(np.float32)
    maps[..., 2] = np.abs(maps[..., 2]) + 0.5
    maps /= np.linalg.norm(maps, axis=-1, keepdims=True)
    leaf = SlotShading(normal_map=maps, sphere=0.7, pivot_cm=(10.0, -20.0, 400.0))
    bark = SlotShading(moss=(0.1, 0.4, 0.05), moss_low=0.3, moss_gain=3.0)
    albedo = rng.uniform(0, 1, (16, 16, 3)).astype(np.float32)
    textures = [
        SlotTexture("leaf", albedo, np.full((16, 16), 255, np.uint8), False, leaf),
        _texture(BARK, kind="bark", shading=bark),
    ]
    grid = raster.sprite_grid(mesh.verts)
    tris = raster.triangle_setup(mesh, np.ones(len(mesh.tris), bool), grid)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    hits = raster.top_hits(tris, raster.pack_alpha(textures), grid)
    tables = shade.shading_tables(mesh, tris, textures)
    reference = raster.shade_samples(hits, tris, tables, grid)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    on_gpu = raster.shade_samples(hits, tris, tables, grid)
    for field in ("colour", "normal"):
        a, b = getattr(reference, field), getattr(on_gpu, field)
        assert (a.dtype, a.shape) == (b.dtype, b.shape)
        assert a.tobytes() == b.tobytes(), field
    assert (np.abs(reference.normal[..., 0]) > 0.05).mean() > 0.2, "the map and sphere bend it"

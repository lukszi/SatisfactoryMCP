"""Textures read onto a band and sprites stamped over it: the reference reads what it should,
and the CUDA kernels give its bytes.

docs/map/renders.md section 41, "The draw on the GPU". Synthetic textures throughout. The
kernel tests skip, saying why, on a machine without numba, CuPy or a CUDA device.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen import jit
from mapgen.terrain.texels import (
    Atlas,
    Sprites,
    sample_atlas,
    sample_texture,
    sprite_boxes,
    stamp_sprites,
)

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _texture(h: int, w: int, channels: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random((h, w, channels), dtype=np.float32)


def _coords(shape: tuple[int, int], span: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Positions inside, on and well past a texture's edges, negative ones included."""
    rng = np.random.default_rng(seed)
    u = (rng.random(shape, dtype=np.float32) * 3 - 1) * np.float32(span)
    v = (rng.random(shape, dtype=np.float32) * 3 - 1) * np.float32(span)
    u[0, :4] = [0.0, 0.5, span - 0.5, span]
    return u.astype(np.float32), v.astype(np.float32)


def _atlas() -> Atlas:
    """Three tiles of different sizes side by side, RGBA, the alpha soft at the edges."""
    texels = np.zeros((24, 60, 4), np.float32)
    texels[..., :3] = _texture(24, 60, 3, 1)
    yy, xx = np.mgrid[0:24, 0:60].astype(np.float32)
    texels[..., 3] = np.clip(1.2 - np.hypot((xx % 20) - 10, yy % 24 - 12) / 10, 0, 1)
    tiles = np.array([[0, 0, 20, 24], [20, 0, 20, 20], [40, 4, 16, 16]], np.int32)
    return Atlas(texels, tiles)


def _sprites(count: int, rows: int, cols: int, seed: int) -> Sprites:
    """Turned sprites over a band and past its edges, overlapping, of every tile."""
    rng = np.random.default_rng(seed)
    turn = rng.random(count) * 2 * np.pi
    return Sprites(
        x=(rng.random(count) * (cols + 40) - 20).astype(np.float32),
        y=(rng.random(count) * (rows + 40) - 20).astype(np.float32),
        half=(2 + rng.random(count) * 14).astype(np.float32),
        cos=np.cos(turn).astype(np.float32),
        sin=np.sin(turn).astype(np.float32),
        tile=rng.integers(0, 3, count).astype(np.int32),
        opacity=(0.3 + 0.7 * rng.random(count)).astype(np.float32),
    )


# ------------------------------------------------------------------------- the reference


def test_a_texture_is_read_at_its_texel_centres():
    texture = _texture(4, 5, 2, 3)
    u = np.array([[0.5, 4.5, 2.5]], np.float32)
    v = np.array([[0.5, 3.5, 1.5]], np.float32)
    got = sample_texture(texture, u, v)
    want = np.stack([texture[0, 0], texture[3, 4], texture[1, 2]])[None]
    assert got.tobytes() == want.tobytes()


def test_a_texture_repeats_or_clamps_past_its_edge():
    texture = _texture(4, 6, 1, 4)
    u, v = np.array([[6.5, -0.5]], np.float32), np.array([[1.5, 1.5]], np.float32)
    repeated = sample_texture(texture, u, v, wrap=True)
    assert repeated[0, :, 0].tolist() == [texture[1, 0, 0], texture[1, 5, 0]]
    clamped = sample_texture(texture, u, v, wrap=False)
    assert clamped[0, :, 0].tolist() == [texture[1, 5, 0], texture[1, 0, 0]]


def test_an_atlas_tile_never_reads_its_neighbours():
    atlas = _atlas()
    u, v = _coords((30, 30), 20.0, 5)
    tile = np.ones(u.shape, np.int32)
    got = sample_atlas(atlas, tile, u, v)
    alone = sample_texture(atlas.texels[0:20, 20:40], u, v, wrap=False)
    assert got.tobytes() == alone.tobytes()


def test_a_sprite_box_holds_every_pixel_its_tile_reaches():
    sprites = _sprites(40, 64, 80, 6)
    boxes = sprite_boxes(sprites, (64, 80))
    colour, cover = np.zeros((64, 80, 3), np.float32), np.zeros((64, 80), np.float32)
    for k in range(40):
        one = Sprites(*(np.asarray(part)[k : k + 1] for part in sprites))
        cover[:] = 0
        stamp_sprites(colour, cover, one._replace(opacity=np.ones(1, np.float32)), _atlas())
        rows, cols = np.nonzero(cover)
        r0, r1, c0, c1 = boxes[k]
        assert np.all((rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)), k


def test_sprites_are_laid_over_in_their_order():
    atlas = Atlas(np.ones((2, 4, 4), np.float32), np.array([[0, 0, 2, 2], [2, 0, 2, 2]], np.int32))
    atlas.texels[:, 2:, :3] = 0.25
    one = np.ones(2, np.float32)
    sprites = Sprites(np.array([5.0, 5.0], np.float32), np.array([5.0, 5.0], np.float32),
                      one * 3, one, one * 0, np.array([0, 1], np.int32), one * 0.5)  # fmt: skip
    colour, cover = np.zeros((10, 10, 3), np.float32), np.zeros((10, 10), np.float32)
    stamp_sprites(colour, cover, sprites, atlas)
    assert colour[5, 5].tolist() == [0.375] * 3, "half white, then half a quarter over it"
    assert cover[5, 5] == 0.75 and cover[0, 0] == 0


# --------------------------------------------------------------------------- on the GPU


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("wrap", [True, False])
def test_a_texture_read_on_the_device_is_the_reference_s(wrap):
    import cupy as cp

    from mapgen.render.gpu import texels

    texture = _texture(17, 23, 3, 7)
    u, v = _coords((41, 37), 23.0, 8)
    want = sample_texture(texture, u, v, wrap)
    got = texels.sample_texture(cp.asarray(texture), cp.asarray(u), cp.asarray(v), wrap).get()
    assert got.tobytes() == want.tobytes()


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("wrap", [True, False])
def test_an_atlas_read_on_the_device_is_the_reference_s(wrap):
    import cupy as cp

    from mapgen.render.gpu import texels

    atlas = _atlas()
    u, v = _coords((33, 29), 20.0, 9)
    tile = np.random.default_rng(10).integers(0, 3, u.shape).astype(np.int32)
    want = sample_atlas(atlas, tile, u, v, wrap)
    on_atlas = texels.upload_atlas(atlas)
    got = texels.sample_atlas(on_atlas, cp.asarray(tile), cp.asarray(u), cp.asarray(v), wrap)
    assert got.get().tobytes() == want.tobytes()


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize(("count", "rows", "cols"), [(300, 96, 130), (1, 5, 7), (0, 8, 8)])
def test_sprites_stamped_on_the_device_are_the_reference_s(count, rows, cols):
    import cupy as cp

    from mapgen.render.gpu import texels

    sprites = _sprites(count, rows, cols, count + rows)
    colour = _texture(rows, cols, 3, 11)
    cover = np.random.default_rng(12).random((rows, cols), dtype=np.float32) * 0.5
    on_colour, on_cover = cp.asarray(colour), cp.asarray(cover)
    stamp_sprites(colour, cover, sprites, _atlas())
    texels.stamp_sprites(on_colour, on_cover, sprites, texels.upload_atlas(_atlas()))
    assert on_colour.get().tobytes() == colour.tobytes()
    assert on_cover.get().tobytes() == cover.tobytes()

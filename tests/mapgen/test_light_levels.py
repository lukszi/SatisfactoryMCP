"""The light pyramid's horizons: stored lossless, and averaged as shade on the coarser levels.

docs/map/light-and-crowns.md section 29, "Coarser levels" and "Encoding". Synthetic fixtures
throughout. The CUDA twin's tests skip, saying why, without numba, CuPy or a device.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from PIL import Image

from mapgen import jit
from mapgen.lighting import horizon as hz
from mapgen.lighting import light_tiles, model, refold
from tests.support.paths import REPO_ROOT

#: pytest puts its own filters before ``mapgen.jit``'s, which quiets this one in a render.
pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

DIRS = hz.HORIZON_DIRS
SOFT = np.float32(model.SHADOW_SOFT_DEG)


def _page_shade(deg: np.ndarray, el: np.ndarray) -> np.ndarray:
    """litlayer.ts' shadow of a horizon at ``el``: ``clamp((h - el) / soft + 0.5, 0, 1)``."""
    return np.clip((deg - el) / SOFT + np.float32(0.5), 0, 1)


def _folded(rng: np.random.Generator, shape: tuple[int, ...], el: np.ndarray) -> np.ndarray:
    """Horizons under 90 degrees, a third of them inside the soft edge at ``el``, as a folded
    band is stored."""
    deg = rng.random(shape, dtype=np.float32) * np.float32(80.0)
    near = el + (rng.random(shape, dtype=np.float32) - np.float32(0.5)) * SOFT
    return np.where(rng.random(shape) < 1 / 3, near, deg).astype(np.float32)


def _crowns(rng: np.random.Generator, ground: np.ndarray, el: np.ndarray) -> np.ndarray:
    """Crown cells as the atlas stores them: a horizon where it stands above the ground's,
    else 0."""
    over = _folded(rng, ground.shape, el)
    return np.where((over > ground) & (rng.random(ground.shape) < 0.5), over, 0).astype(np.float32)


# ------------------------------------------------------------------------------ the atlas


def _written(tmp_path, atlas: np.ndarray, exact: bool) -> np.ndarray:
    """``atlas`` through ``encode_tiles`` and read back, as the page's texture reads it."""
    job = light_tiles.TileJob(str(tmp_path / "t"), np.zeros((256, 256, 4), np.uint8), atlas, exact)
    light_tiles.encode_tiles([job])
    with Image.open(tmp_path / f"t{light_tiles.HZ_SUFFIX}") as image:
        rgb = np.asarray(image.convert("RGB"))
    assert not exact or (rgb == rgb[..., :1]).all(), "grey"
    return rgb[..., 0]  # the channel the shader reads


def test_a_horizon_atlas_round_trips_within_the_8_bit_code(tmp_path):
    rng = np.random.default_rng(1)
    els = refold.path_elevations()[np.arange(model.HZ_CELLS) % DIRS, None, None]
    deg = _folded(rng, (model.HZ_CELLS, 128, 128), els)
    deg[0, 0] = hz.decode_horizon(np.arange(128))
    deg[0, 1] = hz.decode_horizon(np.arange(128, 256))
    hz_u8 = hz.encode_horizon(deg)
    assert set(np.unique(hz_u8)) == set(range(256)), "every byte the atlas can hold"
    atlas = light_tiles.hz_atlas(hz_u8)
    read = _written(tmp_path, atlas, exact=True)
    assert read.tobytes() == atlas.tobytes(), "lossless: the bytes encoded"
    code = np.sqrt(np.clip(deg / 90.0, 0, 1)) * 255.0
    back = np.sqrt(hz.decode_horizon(light_tiles.atlas_cells(read)) / 90.0) * 255.0
    assert float(np.abs(back - code).max()) <= 0.5 + 1e-3, "within half a step of the code"


def test_each_atlas_cell_sits_in_a_border_of_its_own_edge():
    rng = np.random.default_rng(8)
    hz_u8 = rng.integers(0, 256, (model.HZ_CELLS, 128, 128), dtype=np.uint8)
    atlas = light_tiles.hz_atlas(hz_u8)
    g, stride = model.HZ_GUTTER_PX, 128 + 2 * model.HZ_GUTTER_PX
    assert atlas.shape == (8 * stride, 8 * stride) and g == 16
    assert light_tiles.atlas_cells(atlas).tobytes() == hz_u8.tobytes()
    k, r, c = 19, 2, 3  # cell 19 is the third row's fourth
    framed = atlas[r * stride : (r + 1) * stride, c * stride : (c + 1) * stride]
    assert framed.tobytes() == np.pad(hz_u8[k], g, mode="edge").tobytes()


def _edge_error(tmp_path, monkeypatch, gutter: int) -> int:
    """The largest error a lossy atlas leaves on its cells' edge texels, each cell smooth on
    its own and unlike its neighbours, as a tile's east edge is unlike its west."""
    monkeypatch.setattr(light_tiles, "HZ_GUTTER_PX", gutter)
    yy, xx = np.mgrid[0:128, 0:128].astype(np.float32)
    cells = np.stack([128 + 100 * np.sin(xx / (9 + k % 7)) * np.cos(yy / (7 + k % 5))
                      for k in range(model.HZ_CELLS)]).astype(np.uint8)  # fmt: skip
    lossy = light_tiles.atlas_cells(_written(tmp_path, light_tiles.hz_atlas(cells), exact=False))
    edges = (slice(None), slice(None), [0, 1, -2, -1])
    return int(np.abs(lossy[edges].astype(int) - cells[edges]).max())


def test_a_lossy_atlas_keeps_its_neighbours_off_each_cell_s_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(light_tiles, "HZ_QUALITY", 75)  # where the codec's blur is widest
    bordered = _edge_error(tmp_path / "g", monkeypatch, model.HZ_GUTTER_PX)
    bare = _edge_error(tmp_path / "bare", monkeypatch, 0)
    assert bordered * 2 <= bare, (bordered, bare)


def test_a_tile_is_stored_lossless_where_a_band_is_folded_into_it():
    folded = np.zeros((256, 384), bool)
    folded[130, 5] = folded[3, 300] = True
    assert light_tiles.exact_tiles(folded, 10, 20) == {(10, 21), (12, 20)}
    assert light_tiles.exact_tiles(np.zeros((128, 128), bool), 0, 0) == frozenset()


# ------------------------------------------------------------------------ coarser levels


def test_a_thin_shadow_survives_a_coarser_level():
    els = refold.path_elevations()
    fine = np.broadcast_to(els - 20, (8, 8, DIRS)).astype(np.float32)
    fine[:, 3] = els + SOFT / 2  # a shadow one pixel wide, the sun disc wholly hidden
    coarse = refold.refold(np.concatenate([fine, np.zeros_like(fine)], -1), els)
    shade = _page_shade(coarse[..., :DIRS], els)
    assert np.allclose(shade[:, 1], 0.5) and not shade[:, [0, 2, 3]].any()
    assert not _page_shade(light_tiles.downsample(fine), els).any(), "the mean of degrees lost it"
    assert not coarse[..., DIRS:].any(), "no crown, no crown cell"


def test_a_coarser_level_averages_the_shade_the_page_reads():
    els = refold.path_elevations()
    rng = np.random.default_rng(2)
    ground = _folded(rng, (64, 96, DIRS), els)
    crowns = _crowns(rng, ground, els)
    stored = light_tiles.encode_linear(refold.refold(np.concatenate([ground, crowns], -1), els))
    coarse = light_tiles.decode_linear(stored)
    half_step = np.float32(0.5 / light_tiles.HZ_LINEAR_SCALE) / SOFT + np.float32(1e-5)
    want = light_tiles.downsample(_page_shade(ground, els))
    assert np.abs(_page_shade(coarse[..., :DIRS], els) - want).max() <= half_step
    crowned = light_tiles.downsample(_page_shade(np.maximum(ground, crowns), els))
    page = _page_shade(np.maximum(coarse[..., :DIRS], coarse[..., DIRS:]), els)
    assert np.abs(page - crowned).max() <= half_step, "the page's max of the two, averaged"
    assert np.abs(_page_shade(light_tiles.downsample(ground), els) - want).max() > 0.3
    bare = light_tiles.downsample((crowns > 0).astype(np.float32)) == 0
    assert not stored[..., DIRS:][bare].any(), "a crown cell stays empty where no crown stands"
    lit = light_tiles.downsample(np.where(_page_shade(ground, els) > 0, 1.0, 0.0)) == 0
    mean = light_tiles.encode_linear(light_tiles.downsample(ground))
    assert np.array_equal(stored[..., :DIRS][lit], mean[lit]), "all lit: the mean of degrees"


def _level(tmp_path) -> np.ndarray:
    """A coarser level of 2 x 2 tiles from random horizons, tile (1, 0) named lossless: the
    horizons it read."""
    work, n = tmp_path / "work", 512
    work.mkdir()
    rng = np.random.default_rng(3)
    np.save(work / "zh.npy", np.zeros((n, n), np.float32))
    np.save(work / "landh.npy", np.full((n, n), 255, np.uint8))
    np.save(work / "svfh.npy", np.full((n, n), 255, np.uint8))
    hzq = rng.integers(0, 253, (n // 2, n // 2, model.HZ_CELLS), np.uint8)
    np.save(work / "hzq.npy", hzq)
    with ThreadPoolExecutor(1) as pool:
        light_tiles.level_strips(work, tmp_path / "dest", 1, 2.0, pool, False, frozenset({(1, 0)}))
    return hzq


def test_the_coarser_levels_stay_off_the_device_in_the_run_s_own_process(tmp_path, monkeypatch):
    def on_gpu(*_args: object) -> np.ndarray:
        raise AssertionError("the run's own process opened the device")

    monkeypatch.setattr(jit, "_numba", lambda: object())
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    monkeypatch.setattr(refold, "_on_gpu", on_gpu)
    assert jit.gpu_on()
    _level(tmp_path)


def test_the_coarser_levels_refold_what_they_read(tmp_path):
    work = tmp_path / "work"
    hzq = _level(tmp_path)
    els = refold.path_elevations()
    want = light_tiles.encode_linear(refold.refold(light_tiles.decode_linear(hzq), els))
    assert np.load(work / "hzq.npy").tobytes() == want.tobytes()
    tiles = tmp_path / "dest" / "1"
    with Image.open(tiles / f"1_0{light_tiles.HZ_SUFFIX}") as image:
        atlas = np.asarray(image.convert("L"))
    cells = np.moveaxis(light_tiles.LINEAR_TO_HZ[hzq[:128, 128:]], -1, 0)
    assert atlas.tobytes() == light_tiles.hz_atlas(cells).tobytes(), "named lossless: exact"
    kinds = {p.name: p.read_bytes()[12:16] for p in tiles.glob(f"*{light_tiles.HZ_SUFFIX}")}
    assert kinds == {"0_0.hz.webp": b"VP8 ", "1_0.hz.webp": b"VP8L", "0_1.hz.webp": b"VP8 ",
                     "1_1.hz.webp": b"VP8 "}  # fmt: skip


def test_refold_takes_float32_texels_of_its_directions():
    els = refold.path_elevations()
    with pytest.raises(TypeError):
        refold.refold(np.zeros((4, 4, DIRS)), els)
    with pytest.raises(ValueError):
        refold.refold(np.zeros((4, 5, DIRS), np.float32), els)
    with pytest.raises(ValueError):
        refold.refold(np.zeros((4, 4, DIRS + 1), np.float32), els)


# ------------------------------------------------------------------------------ the GPU


@pytest.fixture(scope="module")
def device() -> None:
    """The CUDA kernels' preconditions, checked once a worker: numba, CuPy, a device."""
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _same(monkeypatch: pytest.MonkeyPatch, call: Callable[[], np.ndarray]) -> None:
    """``call`` under the reference and on the GPU: the same dtype, shape and bytes."""
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert jit.gpu_on()
    on_gpu = call()
    assert (on_gpu.dtype, on_gpu.shape) == (reference.dtype, reference.shape)
    assert on_gpu.tobytes() == reference.tobytes()


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("crowned", [False, True])
def test_the_cuda_refold_is_the_reference_bit_for_bit(monkeypatch, crowned):
    els = refold.path_elevations()
    rng = np.random.default_rng(4 + crowned)
    ground = _folded(rng, (2 * 9, 2 * 5, DIRS), els)  # 160 threads a row: one block idles
    ground[0, 0] = ground[0, 1] = ground[1, 0] = ground[1, 1] = els + SOFT
    fine = np.concatenate([ground, _crowns(rng, ground, els)], -1) if crowned else ground
    _same(monkeypatch, lambda: refold.refold(fine, els))
    _same(monkeypatch, lambda: refold.refold(fine[2:-2, 2:], els))  # a strided view


@pytest.mark.usefixtures("device")
def test_the_cuda_refold_of_one_direction_is_the_reference_bit_for_bit(monkeypatch):
    """A light block refolds a direction at a time: its ground cell and its crown cell."""
    els = refold.path_elevations()[7:8]
    rng = np.random.default_rng(6)
    ground = _folded(rng, (2 * 130, 2 * 70, 1), els)
    pair = np.concatenate([ground, _crowns(rng, ground, els)], -1)
    _same(monkeypatch, lambda: refold.refold(pair, els))


@pytest.mark.usefixtures("device")
def test_a_refold_the_device_has_no_memory_for_runs_the_reference(monkeypatch):
    def out_of_memory(*_args: object) -> np.ndarray:
        raise MemoryError("out of device memory")

    monkeypatch.setattr(refold, "_on_gpu", out_of_memory)
    els = refold.path_elevations()
    fine = _folded(np.random.default_rng(7), (6, 6, DIRS), els)
    _same(monkeypatch, lambda: refold.refold(fine, els))


def test_only_the_gpu_switch_loads_cupy_for_a_refold():
    code = (
        "import os, sys\n"
        "import numpy as np\n"
        "from mapgen.lighting import refold\n"
        "for value in ('numpy', ''):\n"
        "    os.environ['MAPGEN_KERNELS'] = value\n"
        "    refold.refold(np.zeros((4, 4, 64), np.float32), refold.path_elevations())\n"
        "print('cupy' in sys.modules)\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "False"

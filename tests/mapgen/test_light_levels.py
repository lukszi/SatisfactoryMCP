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
    """Tree cells as the atlas stores them: the trees' own horizon, above or under the
    ground's, and 0 where no tree is in reach."""
    over = _folded(rng, ground.shape, el)
    return np.where(rng.random(ground.shape) < 0.5, over, 0).astype(np.float32)


# ------------------------------------------------------------------------------ the atlas


def _written(tmp_path, atlas: np.ndarray, folded: bool) -> np.ndarray:
    """``atlas`` through ``encode_tiles`` and read back, as the page's texture reads it."""
    job = light_tiles.TileJob(str(tmp_path / "t"), np.zeros((256, 256, 4), np.uint8), atlas, folded)
    light_tiles.encode_tiles([job])
    path = tmp_path / f"t{light_tiles.HZ_SUFFIX}"
    assert path.read_bytes() == light_tiles.hz_webp(atlas, folded), "the tile is hz_webp's bytes"
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))[..., 0]  # the channel the shader reads


def _strips() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Smooth horizons with strips of folded bands across every cell, as a tile under arches
    or crowns holds: the bytes, their cells' path elevations, and where the strips are."""
    els = refold.path_elevations()[np.arange(model.HZ_CELLS) % DIRS, None, None]
    yy, xx = np.mgrid[0:128, 0:128].astype(np.float32)
    k = np.arange(model.HZ_CELLS)[:, None, None]
    ground = 20 + 15 * np.sin(xx / (11 + k % 7)) * np.cos(yy / (9 + k % 5))
    cover = 0.5 + 0.5 * np.sin((xx + yy) / 13 + k)
    strip = np.abs(np.sin(xx / 17 + k)) < 0.35
    deg = np.where(strip, els + SOFT * (cover - 0.5), ground).astype(np.float32)
    return hz.encode_horizon(deg), els, strip


def test_a_folded_atlas_reads_back_within_a_few_steps_of_its_code(tmp_path):
    codes, els, strip = _strips()
    exact = _page_shade(hz.decode_horizon(codes), els)
    errors = {}
    for folded in (False, True):
        read = light_tiles.atlas_cells(_written(tmp_path, light_tiles.hz_atlas(codes), folded))
        steps = np.abs(read.astype(int) - codes)
        shade = np.abs(_page_shade(hz.decode_horizon(read), els) - exact)[strip]
        errors[folded] = (float(np.percentile(steps, 99)), float(np.percentile(shade, 95)))
    assert errors[True][0] <= 3, "the folded quality: within a few steps of the 8-bit code"
    assert errors[True][1] <= 0.15 and errors[True][1] < errors[False][1], errors


def test_each_atlas_cell_sits_in_a_border_of_its_own_edge():
    rng = np.random.default_rng(8)
    hz_u8 = rng.integers(0, 256, (model.HZ_CELLS, 128, 128), dtype=np.uint8)
    atlas = light_tiles.hz_atlas(hz_u8)
    g, stride = model.HZ_GUTTER_PX, 128 + 2 * model.HZ_GUTTER_PX
    assert atlas.shape == (13 * stride, 8 * stride) and g == 16, "97 cells, 8 a row"
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
    lossy = light_tiles.atlas_cells(_written(tmp_path, light_tiles.hz_atlas(cells), folded=False))
    edges = (slice(None), slice(None), [0, 1, -2, -1])
    return int(np.abs(lossy[edges].astype(int) - cells[edges]).max())


def test_a_lossy_atlas_keeps_its_neighbours_off_each_cell_s_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(light_tiles, "HZ_QUALITY", 75)  # where the codec's blur is widest
    bordered = _edge_error(tmp_path / "g", monkeypatch, model.HZ_GUTTER_PX)
    bare = _edge_error(tmp_path / "bare", monkeypatch, 0)
    assert bordered * 2 <= bare, (bordered, bare)


def test_a_tile_holds_a_fold_where_any_of_its_pixels_does():
    folded = np.zeros((256, 384), bool)
    folded[130, 5] = folded[3, 300] = True
    assert light_tiles.folded_tiles(folded, 10, 20) == {(10, 21), (12, 20)}
    assert light_tiles.folded_tiles(np.zeros((128, 128), bool), 0, 0) == frozenset()


# ------------------------------------------------------------------------ coarser levels


def test_a_thin_shadow_survives_a_coarser_level():
    els = refold.path_elevations()
    fine = np.broadcast_to(els - 20, (8, 8, DIRS)).astype(np.float32)
    fine[:, 3] = els + SOFT / 2  # a shadow one pixel wide, the sun disc wholly hidden
    coarse = refold.refold(np.concatenate([fine, np.zeros_like(fine)], -1), els)
    shade = _page_shade(coarse[..., :DIRS], els)
    assert np.allclose(shade[:, 1], 0.5) and not shade[:, [0, 2, 3]].any()
    assert not _page_shade(light_tiles.downsample(fine), els).any(), "the mean of degrees lost it"
    assert not coarse[..., DIRS:].any(), "no tree, no tree cell"


@pytest.mark.parametrize("groups", [1, 2])
def test_a_coarser_level_averages_the_shade_the_page_reads(groups):
    els = refold.path_elevations()
    rng = np.random.default_rng(2)
    ground = _folded(rng, (64, 96, DIRS), els)
    trees = [_crowns(rng, ground, els) for _ in range(groups)]
    stored = light_tiles.encode_linear(refold.refold(np.concatenate([ground, *trees], -1), els))
    coarse = light_tiles.decode_linear(stored)
    half_step = np.float32(0.5 / light_tiles.HZ_LINEAR_SCALE) / SOFT + np.float32(1e-5)
    want = light_tiles.downsample(_page_shade(ground, els))
    assert np.abs(_page_shade(coarse[..., :DIRS], els) - want).max() <= half_step
    assert np.abs(_page_shade(light_tiles.downsample(ground), els) - want).max() > 0.3
    for g, crowns in enumerate(trees, 1):
        cell = coarse[..., g * DIRS : (g + 1) * DIRS]
        crowned = light_tiles.downsample(_page_shade(np.maximum(ground, crowns), els))
        page = _page_shade(np.maximum(coarse[..., :DIRS], cell), els)
        assert np.abs(page - crowned).max() <= half_step, "the page's max of the two, averaged"
        own = light_tiles.downsample(_page_shade(crowns, els))
        alone = np.abs(_page_shade(cell, els) - own) <= half_step
        raised = crowned > want
        assert alone[~raised].all(), "the trees alone, where they raise nothing: their own mean"
        assert raised.any() and (~raised).any()
        bare = light_tiles.downsample((crowns > 0).astype(np.float32)) == 0
        assert not stored[..., g * DIRS : (g + 1) * DIRS][bare].any(), "empty where no tree"
    lit = light_tiles.downsample(np.where(_page_shade(ground, els) > 0, 1.0, 0.0)) == 0
    mean = light_tiles.encode_linear(light_tiles.downsample(ground))
    assert np.array_equal(stored[..., :DIRS][lit], mean[lit]), "all lit: the mean of degrees"


def _level(tmp_path) -> np.ndarray:
    """A coarser level of 2 x 2 tiles from random horizons, tile (1, 0) named as holding a
    fold: the horizons it read."""
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
    horizons, ambient = hzq[..., : model.HORIZON_CELLS], hzq[..., model.HORIZON_CELLS :]
    want = np.concatenate(
        [
            light_tiles.encode_linear(refold.refold(light_tiles.decode_linear(horizons), els)),
            np.round(light_tiles.downsample(ambient.astype(np.float32))).astype(np.uint8),
        ],
        -1,
    )
    assert np.load(work / "hzq.npy").tobytes() == want.tobytes(), "the ambient cell averaged"
    stored = np.concatenate([light_tiles.LINEAR_TO_HZ[horizons], ambient], -1)
    tiles = tmp_path / "dest" / "1"
    for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
        rows, cols = slice(128 * y, 128 * (y + 1)), slice(128 * x, 128 * (x + 1))
        atlas = light_tiles.hz_atlas(np.moveaxis(stored[rows, cols], -1, 0))
        written = (tiles / f"{x}_{y}{light_tiles.HZ_SUFFIX}").read_bytes()
        assert written == light_tiles.hz_webp(atlas, (x, y) == (1, 0)), "the named tile at q95"


def test_refold_takes_float32_texels_of_its_directions():
    els = refold.path_elevations()
    with pytest.raises(TypeError):
        refold.refold(np.zeros((4, 4, DIRS)), els)
    with pytest.raises(ValueError):
        refold.refold(np.zeros((4, 5, DIRS), np.float32), els)
    with pytest.raises(ValueError):
        refold.refold(np.zeros((4, 4, DIRS + 1), np.float32), els)
    with pytest.raises(ValueError):
        refold.refold(np.zeros((4, 4, 4 * DIRS), np.float32), els)
    assert refold.refold(np.zeros((4, 4, 3 * DIRS), np.float32), els).shape == (2, 2, 3 * DIRS)


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
@pytest.mark.parametrize("groups", [0, 1, 2])
def test_the_cuda_refold_is_the_reference_bit_for_bit(monkeypatch, groups):
    els = refold.path_elevations()
    rng = np.random.default_rng(4 + groups)
    ground = _folded(rng, (2 * 9, 2 * 5, DIRS), els)  # 160 threads a row: one block idles
    ground[0, 0] = ground[0, 1] = ground[1, 0] = ground[1, 1] = els + SOFT
    fine = np.concatenate([ground, *(_crowns(rng, ground, els) for _ in range(groups))], -1)
    _same(monkeypatch, lambda: refold.refold(fine, els))
    _same(monkeypatch, lambda: refold.refold(fine[2:-2, 2:], els))  # a strided view


@pytest.mark.usefixtures("device")
def test_the_cuda_refold_of_one_direction_is_the_reference_bit_for_bit(monkeypatch):
    """A light block refolds a direction at a time: its ground cell and its trees' cells."""
    els = refold.path_elevations()[7:8]
    rng = np.random.default_rng(6)
    ground = _folded(rng, (2 * 130, 2 * 70, 1), els)
    trio = np.concatenate([ground, _crowns(rng, ground, els), _crowns(rng, ground, els)], -1)
    _same(monkeypatch, lambda: refold.refold(trio, els))


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

"""The light bake in strips: the same bytes as whole-array passes, less memory, a fed pool.

docs/spatial-and-map.md section 29, "The stage". Synthetic fixtures throughout; each
reference below is the whole-array arithmetic the strips replaced, kept as the oracle.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import Future, ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from mapgen import pools
from mapgen.lighting import horizon as hz
from mapgen.lighting import light_tiles, model, stage
from mapgen.lighting.stage import Surface, bake_light
from mapgen.lighting.sun import DEFAULT_SUN


def _terrain(side: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    z = np.zeros((side, side), np.float32)
    for octave, amp in ((4, 40.0), (16, 8.0), (64, 2.0)):
        small = rng.standard_normal((octave + 3, octave + 3)).astype(np.float32)
        z += amp * ndimage.zoom(small, side / octave, order=3)[:side, :side]
    return z


def _march_whole(solid, zc, halo, az_deg, spacing_m, fade, best, slabs=None):
    r0, r1, c0, c1 = halo, solid.shape[0] - halo, halo, solid.shape[1] - halo
    az = np.deg2rad(az_deg)
    dr, dc = -np.cos(az), np.sin(az)
    rise = np.empty(zc.shape, np.float32)
    for t in hz._steps(fade[1] / spacing_m, hz.FINE_M / spacing_m):
        d = t * spacing_m
        w = hz.fade_weight(d, fade)
        if w <= 0:
            break
        scale = np.float32(w / d)
        sample = hz._bilinear if t < hz.BILINEAR_PX else hz._nearest
        np.subtract(sample(solid, r0, r1, c0, c1, dr * t, dc * t), zc, out=rise)
        rise *= scale
        np.maximum(best, rise, out=best)
        if slabs is not None:
            lo = sample(slabs[1], r0, r1, c0, c1, dr * t, dc * t)
            hi = sample(slabs[2], r0, r1, c0, c1, dr * t, dc * t)
            hz._raise_by_slab(best, (lo - zc) * scale, (hi - zc) * scale)


def _sky_whole(z, halo, spacing_m, radius_m=hz.SKY_RADIUS_M):
    r0, r1, c0, c1 = halo, z.shape[0] - halo, halo, z.shape[1] - halo
    zc = z[r0:r1, c0:c1]
    acc = np.zeros(zc.shape, np.float32)
    reach = radius_m / spacing_m
    for k in range(hz.SKY_DIRS):
        theta = 2 * np.pi * k / hz.SKY_DIRS
        best = np.zeros(zc.shape, np.float32)
        for t in np.geomspace(1.0, reach, hz.SKY_STEPS):
            zz = hz._bilinear(z, r0, r1, c0, c1, np.sin(theta) * t, np.cos(theta) * t)
            np.maximum(best, (zz - zc) * np.float32(1.0 / (t * spacing_m)), out=best)
        acc += best / np.sqrt(1.0 + best * best)
    return (1.0 - acc / hz.SKY_DIRS).astype(np.float32)


@pytest.mark.parametrize("az", [0.0, 33.75, 90.0, 202.5, 315.0])
def test_the_march_in_strips_is_the_whole_array_march_bit_for_bit(az):
    sp, halo = 1.0, hz.horizon_reach_px(1.0)
    z = _terrain(2 * halo + hz.STRIP_ROWS * 2 + 23)  # a ragged last strip
    core = (slice(halo, z.shape[0] - halo),) * 2
    lo = np.where(z > 20, z - 4, np.nan).astype(np.float32)
    cases = ((hz.FADE_M, None), (hz.OCCLUDER_FADE_M, None), (hz.FADE_M, (z, lo, lo + 9)))
    for fade, slabs in cases:
        want = np.zeros(z[core].shape, np.float32)
        _march_whole(z, z[core], halo, az, sp, fade, want, slabs)
        assert hz._march(z, z, halo, az, sp, fade, slabs).tobytes() == want.tobytes()


def test_the_sky_view_in_strips_is_the_whole_array_one_bit_for_bit():
    sp = 0.5
    halo = int(np.ceil(hz.SKY_RADIUS_M / sp)) + 2
    z = _terrain(2 * halo + hz.STRIP_ROWS * 3 + 5, seed=2)
    got = hz.sky_view(z, halo, sp)
    assert got.dtype == np.float32 and got.tobytes() == _sky_whole(z, halo, sp).tobytes()
    assert hz.sky_view(z, halo, 100.0).tobytes() == np.ones(got.shape, np.float32).tobytes()


def test_a_direction_s_2x2_mean_sums_as_the_stacked_one_did():
    rng = np.random.default_rng(5)
    stack = (rng.random((model.HZ_CELLS, 66, 66), dtype=np.float32) * 90).astype(np.float32)
    stack *= np.where(rng.random(stack.shape) < 0.3, 1e-3, 1.0).astype(np.float32)
    whole = light_tiles.downsample(np.moveaxis(stack, 0, -1))
    each = np.stack([light_tiles.downsample(cell) for cell in stack], -1)
    assert each.tobytes() == whole.tobytes()


def test_the_coarser_levels_lookup_is_the_decode_and_encode_it_replaced():
    q = np.random.default_rng(6).integers(0, 256, (37, 41, model.HZ_CELLS), dtype=np.uint8)
    want = hz.encode_horizon(np.moveaxis(light_tiles.decode_linear(q), -1, 0))
    assert np.moveaxis(light_tiles.LINEAR_TO_HZ[q], -1, 0).tobytes() == want.tobytes()


SP = 5.0


def _block_inputs(side=96):
    halo = hz.horizon_reach_px(SP)
    z = _terrain(side + 2 * halo, seed=3)
    crowns = (z + np.where(z > 5, 6.0, 0.0).astype(np.float32), z)
    lo = np.where(z > 15, z - 3, np.nan).astype(np.float32)
    return z, halo, (z, lo, lo + 7), crowns


def test_a_block_s_horizons_a_direction_at_a_time_are_the_stacked_ones():
    zh, halo, slabs, crowns = _block_inputs()
    m = zh.shape[0] - 2 * halo
    ground = hz.faded_horizons(zh, halo, SP, None, slabs)
    over = hz.crown_horizons(crowns[0], halo, SP, crowns[1])
    stack = np.concatenate([ground, np.where(over > ground, over, np.float32(0.0))])
    hz_u8, hq, sun = stage._bake_horizons(zh, halo, SP, crowns, slabs, m, True)
    assert hz_u8.tobytes() == hz.encode_horizon(stack).tobytes()
    scale = light_tiles.HZ_LINEAR_SCALE
    want_hq = np.round(np.clip(light_tiles.downsample(np.moveaxis(stack, 0, -1)), 0, 90) * scale)
    assert hq.tobytes() == want_hq.astype(np.uint8).tobytes()
    nrm = np.random.default_rng(7).integers(0, 256, (2 * m, 2 * m, 4), dtype=np.uint8)
    for crowned in (False, True):
        want = model.direct_term(nrm, stack, DEFAULT_SUN, crowns=crowned)
        assert model.direct_term(nrm, sun, DEFAULT_SUN, crowns=crowned).tobytes() == want.tobytes()


def test_the_default_sun_reads_only_its_four_cells_and_water_bakes_zero():
    zh, halo, _slabs, _crowns = _block_inputs()
    m = zh.shape[0] - 2 * halo
    keep = model.sun_cells(DEFAULT_SUN[0])
    assert len(set(keep)) == 4 and all(k >= model.HORIZON_DIRS for k in keep[2:])
    sparse = [None] * model.HZ_CELLS
    for k in keep:
        sparse[k] = np.full((m, m), 10.0 + k, np.float32)
    nrm = np.full((m, m, 4), 200, np.uint8)
    assert model.direct_term(nrm, sparse, DEFAULT_SUN, crowns=True).shape == (m, m)
    hz_u8, hq, sun = stage._bake_horizons(zh, halo, SP, None, None, m, False)
    assert not hz_u8.any() and not hq.any() and not any(plane.any() for plane in sun)


def test_without_crowns_the_crown_cells_stay_zero():
    zh, halo, slabs, _crowns = _block_inputs()
    m = zh.shape[0] - 2 * halo
    hz_u8, hq, _sun = stage._bake_horizons(zh, halo, SP, None, slabs, m, True)
    assert hz_u8[: model.HORIZON_DIRS].any() and not hz_u8[model.HORIZON_DIRS :].any()
    assert not hq[..., model.HORIZON_DIRS :].any()


class _LazyPool:
    """Futures that finish only when waited on, so the test sees how far the parent runs ahead."""

    def __init__(self):
        self.open: set[int] = set()
        self.submitted = self.most = self.tiles = 0

    def submit(self, _fn, jobs):
        self.submitted += 1
        key, future = self.submitted, Future()
        self.open.add(key)
        self.most = max(self.most, len(self.open))

        def result(timeout=None):
            self.open.discard(key)
            self.tiles += len(jobs)
            return 0

        future.result = result
        return future


def _level_sources(work: Path, n: int) -> None:
    work.mkdir(parents=True)
    rng = np.random.default_rng(8)
    np.save(work / "zh.npy", _terrain(n, seed=9))
    np.save(work / "landh.npy", rng.integers(0, 256, (n, n), dtype=np.uint8))
    np.save(work / "svfh.npy", rng.integers(0, 256, (n, n), dtype=np.uint8))
    np.save(work / "hzq.npy", rng.integers(0, 253, (n // 2, n // 2, model.HZ_CELLS), np.uint8))


def test_a_coarser_level_runs_at_most_level_ahead_strips_before_the_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(light_tiles, "LEVEL_AHEAD", 1)
    monkeypatch.setattr(light_tiles, "LEVEL_TASK_TILES", 2)
    _level_sources(tmp_path / "work", 1024)
    pool = _LazyPool()
    count = light_tiles.level_strips(tmp_path / "work", tmp_path / "dest", 2, 1.0, pool, False)
    assert count == pool.tiles == 16, "every strip's tiles were submitted and waited for"
    assert not pool.open and pool.submitted == 8, "4 strips of 4 tiles, 2 tiles to a task"
    assert pool.most == (1 + 1) * 2, "the strip being computed and one ahead, 2 tasks each"
    assert np.load(tmp_path / "work" / "hzq.npy").shape == (256, 256, model.HZ_CELLS)
    assert not list(tmp_path.rglob("*.next.npy"))


def test_a_level_is_the_same_bytes_however_far_ahead_it_runs(tmp_path, monkeypatch):
    trees = {}
    for ahead in (0, 3):
        monkeypatch.setattr(light_tiles, "LEVEL_AHEAD", ahead)
        _level_sources(tmp_path / f"w{ahead}", 512)
        with ProcessPoolExecutor(2) as pool:
            light_tiles.level_strips(
                tmp_path / f"w{ahead}", tmp_path / f"d{ahead}", 1, 2.0, pool, False
            )
        root = tmp_path / f"d{ahead}"
        trees[ahead] = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                        if p.is_file()}  # fmt: skip
        trees[ahead]["next"] = (tmp_path / f"w{ahead}" / "hzq.npy").read_bytes()
    assert len(trees[0]) == 2 * 4 + 1 and trees[0] == trees[3]


def test_a_bake_is_the_same_bytes_on_one_worker_and_on_the_default_count(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: 3 * stage.LIGHT_WORKER_BYTES)
    entered = []

    @contextmanager
    def counted():
        with pools.one_blas_thread():
            entered.append(os.environ["OPENBLAS_NUM_THREADS"])
            yield

    monkeypatch.setattr(stage, "one_blas_thread", counted)
    trees, workers = {}, {}
    for name, asked in (("one", 1), ("default", None)):
        surface = Surface(tmp_path / name / "work", 512)
        z = _terrain(512, seed=4)
        surface.put(0, z, np.where(z > -20, 1.0, 0.0).astype(np.float32))
        meta = bake_light(surface, tmp_path / name, asked, progress=False)
        surface.close()
        root = tmp_path / name / "light" / "tiles"
        trees[name] = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.webp")}
        workers[name] = meta["render"]["workers"]
    assert workers == {"one": 1, "default": min(3, os.cpu_count() or 1)}
    assert len(trees["one"]) == 2 * (4 + 1) and trees["one"] == trees["default"]
    assert entered == ["1", "1"], "each bake's pool starts its workers with one BLAS thread"


def test_light_workers_take_the_cores_capped_by_the_free_ram(monkeypatch):
    gb = stage.LIGHT_WORKER_BYTES
    monkeypatch.setattr(stage.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: 40 * gb)
    assert stage.light_workers() == stage.LIGHT_WORKER_CAP == 16
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: 5.5 * gb)
    assert stage.light_workers() == 5
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: 0.2 * gb)
    assert stage.light_workers() == 1
    assert stage.light_workers(24) == 24, "asked for, it is what is asked for"
    monkeypatch.setattr(stage, "free_ram_bytes", lambda: None)
    monkeypatch.setattr(stage.os, "cpu_count", lambda: 6)
    assert stage.light_workers() == 6
    monkeypatch.undo()
    free = pools.free_ram_bytes()
    assert free is None or free > 0


def test_one_blas_thread_reaches_the_processes_started_inside_and_then_unwinds(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "7")
    with pools.one_blas_thread(), ProcessPoolExecutor(1) as pool:
        assert pool.submit(os.getenv, "OPENBLAS_NUM_THREADS").result() == "1"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "7", "the value before comes back"
    monkeypatch.delenv("OPENBLAS_NUM_THREADS")
    with pools.one_blas_thread():
        assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    assert "OPENBLAS_NUM_THREADS" not in os.environ


#: A child that imports what a bake worker does and prints the bytes it has committed.
_COMMIT_PROBE = """
import ctypes, ctypes.wintypes as w
import numpy, scipy.ndimage
class C(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [(f"f{i}", ctypes.c_size_t) for i in range(9)]
k = ctypes.windll.kernel32
k.GetCurrentProcess.restype = w.HANDLE
k.K32GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(C), w.DWORD]
c = C(cb=ctypes.sizeof(C))
assert k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
print(c.f8)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="the commit charge is a Windows count")
def test_a_worker_with_one_blas_thread_commits_no_blas_buffers():
    """numpy and scipy each load an OpenBLAS: about 0.8 GB of commit apiece on 32 threads."""
    with pools.one_blas_thread():
        out = subprocess.run([sys.executable, "-c", _COMMIT_PROBE], capture_output=True,
                             text=True, check=True)  # fmt: skip
    assert int(out.stdout) < 200_000_000

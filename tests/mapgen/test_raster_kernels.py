"""The raster passes' kernels give numpy's bits: the max-Z scan and fold, a placement's faces,
whole bands of the direct, top and mesh rasters, and the caches on any number of threads.

docs/map/renders.md sections 20 and 41. Synthetic geometry: random rocks, ties, wide and
degenerate triangles, and folds forced every few hundred candidates.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from mapgen import jit
from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_COVERAGE_NAME,
    DIRECT_UNDER_NAME,
    raster_cache_stamp,
)
from mapgen.gamedata import maxz_raster
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.gamedata.placements import rotation_matrix
from mapgen.terrain.maxz import faces, raster
from mapgen.terrain.overhangs import rasterise_direct_planes
from mapgen.terrain.rasters import PreparedPlacement, TopItems
from mapgen.terrain.rasters_banded import in_band_order, write_banded_raster
from mapgen.terrain.render_meshes import (
    InstanceSpans,
    MeshGroup,
    PreparedMeshes,
    rasterise_mesh_band,
)
from mapgen.terrain.top_raster import rasterise_top_planes
from tests.support.paths import REPO_ROOT

needs_numba = pytest.mark.skipif(jit._numba() is None, reason="numba is not installed")

W, H = 300, 200


def _triangles(rng, n, spread, size, quantum=None, dtype=np.float32):
    tri = rng.uniform(-20, spread, (n, 1, 3)) + rng.normal(0, size, (n, 3, 3))
    tri[:, :, 2] = rng.uniform(0, 50, (n, 3))
    if quantum:
        tri = np.round(tri * quantum) / quantum
    return tri.astype(dtype)


def _batches(seed):
    """Small quantised triangles that tie, medium ones, wide ones, flat ones, degenerate ones."""
    rng = np.random.default_rng(seed)
    out = []
    for source in range(1, 21):
        kind = source % 5
        if kind == 0:
            tri = _triangles(rng, 300, 700, 3.0, quantum=1)
        elif kind == 1:
            tri = _triangles(rng, 40, 700, 60.0)
        elif kind == 2:
            tri = _triangles(rng, 3, 700, 500.0)
        elif kind == 3:
            tri = _triangles(rng, 80, 700, 5.0)
            tri[::3, :, 2] = 10.0
        else:
            tri = _triangles(rng, 30, 700, 5.0)
            tri[::2, 1:] = tri[::2, :1]
        out.append((tri, source))
    return out


def _planes(kind, batches, ceiling=None, row0=0, sample=0.5):
    r = kind(W, H, 3.0, -7.0, 2.5, sample=sample, ceiling=ceiling, row0=row0)
    for tri, source in batches:
        r.add(tri, source)
    return r.result()


def _bits(planes):
    return [(p.dtype, p.shape, p.tobytes()) for p in planes]


def _small_folds(monkeypatch, flush):
    monkeypatch.setattr(maxz_raster, "RASTER_FLUSH", flush)
    monkeypatch.setattr(raster, "RASTER_FLUSH", flush)


@needs_numba
@pytest.mark.parametrize("flush", [6_000_000, 4000, 37])
@pytest.mark.parametrize("lid", [False, True])
@pytest.mark.parametrize(("row0", "sample"), [(0, 0.5), (-13, 0.5), (0, 0.0)])
def test_the_kernel_raster_is_numpy_s_bit_for_bit(monkeypatch, flush, lid, row0, sample):
    _small_folds(monkeypatch, flush)
    ceiling = None
    if lid:
        rng = np.random.default_rng(3)
        ceiling = rng.uniform(0, 60, (H, W)).astype(np.float32)
        ceiling[rng.random((H, W)) < 0.2] = -np.inf
    batches = _batches(flush + row0)
    reference = _planes(MaxZRaster, batches, ceiling, row0, sample)
    assert np.isfinite(reference[0]).sum() > 5000
    assert _bits(_planes(raster.KernelRaster, batches, ceiling, row0, sample)) == _bits(reference)


@needs_numba
def test_a_tie_goes_to_the_later_add_in_a_fold_and_to_the_earlier_across_folds(monkeypatch):
    tri = np.array([[[0, 0, 5], [400, 0, 5], [0, 400, 5]]], np.float32)
    for flush, winner in ((6_000_000, 2), (3, 1)):
        _small_folds(monkeypatch, flush)
        for kind in (MaxZRaster, raster.KernelRaster):
            source = _planes(kind, [(tri, 1), (tri, 2)])[1].reshape(H, W)
            assert source[10, 10] == winner, (kind.__name__, flush)


@needs_numba
def test_float64_vertices_and_nan_heights_fold_as_numpy_does():
    tri = _triangles(np.random.default_rng(4), 300, 700, 8.0, dtype=np.float64)
    tri[5, 1, 2] = np.nan
    batches = [(tri, 3), (_triangles(np.random.default_rng(5), 300, 700, 8.0), 4)]
    assert _bits(_planes(raster.KernelRaster, batches)) == _bits(_planes(MaxZRaster, batches))


@needs_numba
def test_instances_and_rows_index_the_triangles_add_would_take():
    rng = np.random.default_rng(6)
    world = rng.uniform(0, 600, (7, 40, 3)).astype(np.float32)
    tris = rng.integers(0, 40, (25, 3))
    rows = np.array([3, 0, 17, 24, 9], np.int32)
    for kind in (MaxZRaster, raster.KernelRaster):
        indexed = kind(W, H, 0.0, 0.0, 2.5, sample=0.5)
        indexed.add_indexed(world, tris, 5)
        indexed.add_indexed(world[2], tris, 6, rows)
        plain = kind(W, H, 0.0, 0.0, 2.5, sample=0.5)
        plain.add(world[:, tris].reshape(-1, 3, 3), 5)
        plain.add(world[2][tris[rows]], 6)
        assert _bits(indexed.result()) == _bits(plain.result()), kind.__name__


@needs_numba
@pytest.mark.parametrize("den", [3.7, -0.004, 1e-12, -5e7, 1e-9, -1e-12, 2.5e5])
def test_the_numerator_threshold_passes_every_numerator_the_division_does(den):
    from mapgen.terrain.maxz import kernels

    one, tol = np.float32(1.0), np.float32(-1e-6)
    den = np.float32(den)
    sign, least = kernels._threshold(den, tol, one)
    near = least + np.arange(-300, 300, dtype=np.float32) * np.spacing(least)
    numerators = np.concatenate([near, np.random.default_rng(7).normal(0, 1e3, 2000)])
    numerators = numerators.astype(np.float32)
    passes = numerators / den >= tol
    assert np.array_equal(passes, sign * numerators >= least), "the test is the division's"
    assert passes.any() and not passes.all()


def test_a_scaled_mesh_s_largest_coordinate_is_its_largest_coordinate_scaled():
    """``direct_placements``' oversize cull reads each mesh's largest coordinate once."""
    rng = np.random.default_rng(13)
    for _ in range(200):
        verts = (rng.normal(0, 1, (500, 3)) * 10.0 ** rng.uniform(-3, 6)).astype(np.float32)
        scale = (rng.uniform(-3, 3, 3) * 10.0 ** rng.uniform(-2, 2)).astype(np.float32)
        whole = np.abs(verts * scale).max()
        assert whole == (np.abs(verts).max(0) * np.abs(scale)).max()


def _mesh(rng, verts=60, tris=90):
    return rng.normal(0, 300, (verts, 3)).astype(np.float32), rng.integers(0, verts, (tris, 3))


def _same(monkeypatch, call):
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.delenv(jit.KERNEL_SWITCH)
    assert jit.kernels_on()
    got = call()
    flat = lambda value: [np.asarray(v) for v in (value if isinstance(value, tuple) else (value,))]
    assert _bits(flat(got)) == _bits(flat(reference))


@needs_numba
@pytest.mark.parametrize("facing", [1, -1, 0])
@pytest.mark.parametrize("rise", [None, 100.0, 1e9])
def test_a_placement_s_faces_and_extent_are_the_reference_bit_for_bit(monkeypatch, facing, rise):
    rng = np.random.default_rng(8)
    world, tris = _mesh(rng)
    world[4, 1] = np.nan
    world[9, 2] = np.nan if facing < 0 else world[9, 2]
    for bounds in ((-200.0, 150.0), (-1e9, 1e9), (900.0, 1000.0)):
        _same(monkeypatch, lambda b=bounds: faces.band_faces(world, tris, facing, b, rise))
    rows = np.arange(0, 90, 3, dtype=np.int32)
    _same(monkeypatch, lambda: faces.extent(world, tris, rows, (-31.0, 17.5, 2.29)))


def _rocks(seed, count=40):
    """Placements of random rocks over a 12 m square, turned, scaled and some mirrored."""
    rng = np.random.default_rng(seed)
    geometry, prepared = {}, []
    for k in range(count):
        mesh = f"Rock_{k % 9}"
        if mesh not in geometry:
            verts, tris = _mesh(rng, 30 + 10 * (k % 9), 50 + 15 * (k % 9))
            geometry[mesh] = (verts, np.ascontiguousarray(tris))
        verts = geometry[mesh][0]
        matrix = rotation_matrix(*rng.uniform(-180, 180, 3)).astype(np.float32)
        scale = (rng.uniform(0.5, 2.0, 3) * rng.choice([1, -1], 3)).astype(np.float32)
        offset = np.array([*rng.uniform(0, 1200, 2), rng.uniform(0, 500)], np.float32)
        world_y = ((verts * scale) @ matrix + offset)[:, 1]
        prepared.append(
            PreparedPlacement(
                mesh,
                k % 9,
                matrix,
                scale,
                offset,
                int(rng.choice([1, -1, 0])),
                float(world_y.min()),
                float(world_y.max()),
                None if k % 4 else 1 + k % 3,
            )
        )
    return prepared, geometry


@needs_numba
@pytest.mark.parametrize("subsamples", [1, 2])
def test_a_direct_band_is_the_reference_bit_for_bit(monkeypatch, subsamples):
    _small_folds(monkeypatch, 500)
    prepared, geometry = _rocks(9)
    for y0 in (-100.0, 400.0):
        band = rasterise_direct_planes(prepared, geometry, -50.0, y0, 9.0, 40, 150, subsamples)
        assert band[DIRECT_COVERAGE_NAME].any() and np.isfinite(band[DIRECT_UNDER_NAME]).any()
        _same(
            monkeypatch,
            lambda y=y0: tuple(
                rasterise_direct_planes(
                    prepared, geometry, -50.0, y, 9.0, 40, 150, subsamples
                ).values()
            ),
        )


@needs_numba
def test_the_top_and_mesh_bands_are_the_reference_bit_for_bit(monkeypatch):
    _small_folds(monkeypatch, 700)
    prepared, geometry = _rocks(10, 12)
    arches = [entry._replace(facing=0) for entry in prepared]
    rng = np.random.default_rng(11)
    mats = np.tile(np.eye(4, dtype=np.float32), (30, 1, 1))
    mats[:, 3, :3] = rng.uniform(0, 1200, (30, 3))
    reach = np.full(30, 900.0)
    codes = np.arange(30, dtype=np.uint16) % 3 + 1
    boulders = {"Rock_1": MeshGroup(codes, mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)}
    items = TopItems(arches, {"Rock_1": InstanceSpans(*boulders["Rock_1"][1:])}, geometry)
    meshes = PreparedMeshes(boulders, geometry, families=True)
    _same(
        monkeypatch,
        lambda: tuple(rasterise_top_planes(items, -50.0, 200.0, 9.0, 40, 150, 1).values()),
    )
    _same(monkeypatch, lambda: rasterise_mesh_band(meshes, -50.0, 200.0, 9.0, 40, 150))


def _cache(tmp_path, threads, name):
    prepared, geometry = _rocks(12, 60)
    size = 384
    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    shift = np.array([BOUNDS_M["x_min_m"] * 100, BOUNDS_M["y_min_m"] * 100, 0], np.float32)
    spread = np.array([size * step / 1200, size * step / 1200, 1], np.float32)
    placed = [
        e._replace(offset=e.offset * spread + shift, y_min_cm=-1e12, y_max_cm=1e12)
        for e in prepared
    ]
    directory = tmp_path / name

    def band(x0, y0, scale, rows, cols, subsamples):
        return rasterise_direct_planes(placed, geometry, x0, y0, scale, rows, cols, subsamples)

    stamp = raster_cache_stamp(size, 1, None)
    write_banded_raster(band, directory, size, 1, stamp, False, threads=threads)
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.name != CACHE_SIDECAR_NAME}


@needs_numba
def test_the_cache_is_the_same_bytes_on_any_number_of_threads(tmp_path):
    one = _cache(tmp_path, 1, "one")
    assert len(one) == 5 and one == _cache(tmp_path, 4, "four")


def test_the_bands_come_back_in_order_with_a_bounded_number_ahead():
    started: list[int] = []
    lock = threading.Lock()

    def band(k):
        with lock:
            started.append(k)
        time.sleep(0.002 * ((7 * k) % 5))
        return k, threading.get_ident()

    seen = []
    for k, _thread in in_band_order(band, range(40), 4):
        with lock:
            assert len(started) <= k + 1 + 4, "no more than the threads past the one waited on"
        seen.append(k)
    assert seen == list(range(40))
    assert {t for _k, t in in_band_order(band, range(3), 1)} == {threading.get_ident()}


@needs_numba
def test_the_raster_kernels_release_the_gil_and_cache_on_disk():
    from numba.core.caching import NullCache

    from mapgen.terrain.maxz import kernels

    for kernel in (kernels.scan, kernels.commit, kernels.band_faces, kernels.extent):
        assert kernel.targetoptions["nogil"] is True
        assert not isinstance(kernel._cache, NullCache)


def test_the_reference_rasters_never_load_numba():
    code = (
        "import os, sys\n"
        "os.environ['MAPGEN_KERNELS'] = 'numpy'\n"
        "import numpy as np\n"
        "from mapgen.terrain.maxz import faces, raster\n"
        "r = raster.max_z_raster(9, 9, 0.0, 0.0, 10.0, sample=0.5)\n"
        "w = np.random.default_rng(1).uniform(0, 90, (12, 3)).astype(np.float32)\n"
        "t = np.arange(12).reshape(4, 3)\n"
        "f = faces.band_faces(w, t, 1, (0.0, 90.0), 100.0)\n"
        "r.add_indexed(w, t, 1, f.up); r.result()\n"
        "print(type(r).__name__, sorted(m for m in sys.modules if m == 'numba' or m.endswith('.kernels')))\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "MaxZRaster []"

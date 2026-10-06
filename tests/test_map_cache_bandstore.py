"""The raster caches' zstd band store: round trips, both storages hitting, a corrupt band.

docs/spatial-and-map.md section 39. Synthetic planes throughout: no install, no field.
"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("zstandard")

from mapgen.bandstore import BandArray, BandStoreError, BandWriter  # noqa: E402
from mapgen.cache import (  # noqa: E402
    BANDS_SUFFIX,
    DIRECT_CACHE_DIR_NAME,
    DIRECT_CACHE_SIDECAR,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    MESH_CACHE_DIR_NAME,
    MESH_CACHE_SIDECAR,
    MESH_CLASS_NAME,
    MESH_Z_NAME,
    STORAGE_BANDS,
    STORAGE_RAW,
    cached_direct,
    cached_family,
    cached_meshes,
    direct_cache_stamp,
    mesh_stamp,
    missing_caches,
)
from mapgen.gamedata.frame import BOUNDS_M  # noqa: E402
from mapgen.terrain import rasters  # noqa: E402
from mapgen.terrain.rasters import (  # noqa: E402
    rasterise_direct,
    rasterise_meshes,
    reduce_direct,
    reduce_source,
)

#: Three bands of 256 rows, the last one short.
SIZE = 600
DIRECT_PLANES = (DIRECT_Z_NAME, DIRECT_COVERAGE_NAME, DIRECT_FAMILY_NAME)


def _plane(dtype, shape, seed=0):
    rng = np.random.default_rng(seed)
    dtype = np.dtype(dtype)
    if dtype.kind == "f":
        out = (rng.normal(size=shape) * 1000).astype(dtype)
        out[rng.random(shape) < 0.7] = 0
        out[3, 4] = np.nan
        return out
    info = np.iinfo(dtype)
    return rng.integers(info.min, info.max, size=shape, endpoint=True).astype(dtype)


def _write(path, plane, band_rows=256):
    with BandWriter(path, plane.shape, plane.dtype, band_rows) as out:
        for top in range(0, plane.shape[0], band_rows):
            out.write(top, plane[top : top + band_rows])
    return BandArray(path, plane.shape, plane.dtype)


def _same(a, b) -> bool:
    """Equal to the bit, NaN included."""
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and a.dtype == b.dtype and a.tobytes() == b.tobytes()


def _corrupt_band(path, shape, dtype, k):
    offsets = BandArray(path, shape, dtype)._offsets
    data = bytearray(path.read_bytes())
    data[(int(offsets[k]) + int(offsets[k + 1])) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


# ------------------------------------------------------------------------- the format


@pytest.mark.parametrize("dtype", [np.float32, np.uint8, np.int16, np.float64])
def test_a_plane_round_trips_across_band_edges_and_a_short_last_band(tmp_path, dtype):
    plane = _plane(dtype, (1000, 37))
    store = _write(tmp_path / "p.bands", plane)
    assert (store.band_rows, len(store), store.shape) == (256, 1000, (1000, 37))
    keys = [
        slice(None), slice(250, 270), slice(255, 257), slice(0, 1), slice(999, 1000),
        slice(768, None), slice(-5, None), slice(300, 300), slice(100, 900, 7),
        slice(900, 100, -3), 10, -1, (slice(250, 520), slice(3, 20)), (slice(None), 5),
        (slice(10, 800), [0, 36, 4]), (511, slice(2, 9)), (0, 0),
    ]  # fmt: skip
    for key in keys:
        assert _same(store[key], plane[key]), key
    rows = np.array([999, 0, 256, 255, 0, 767, 768, -1])
    assert _same(store[rows], plane[rows])
    assert _same(store[rows, 5], plane[rows, 5])
    assert _same(store[rows][:, [1, 30]], plane[rows][:, [1, 30]])
    assert _same(store[[2, 3]], plane[[2, 3]])
    assert _same(np.asarray(store), plane)
    for got in (store[0:300], store[10:20], store[rows], store[4]):
        assert not got.flags.writeable
    with pytest.raises(IndexError):
        store[rows, [1, 2]]
    with pytest.raises(IndexError):
        store[[1000]]


def test_the_render_s_halo_reads_decode_each_band_once_and_hold_no_file(tmp_path):
    plane = _plane(np.float32, (1300, 16))
    store = _write(tmp_path / "p.bands", plane)
    decoded = []
    real = store._zd

    class Counting:
        def decompress(self, blob):
            decoded.append(len(blob))
            return real.decompress(blob)

    store._zd = Counting()
    for top in range(0, 1300, 256):
        lo, hi = max(top - 8, 0), min(top + 264, 1300)
        assert _same(store[lo:hi], plane[lo:hi])
        assert len(store._cache) <= 3
    assert len(decoded) == 6, "five whole bands and a short one, each decoded once"
    store.path.rename(tmp_path / "moved.bands")


def test_the_writer_refuses_bands_out_of_order_or_missing_and_leaves_nothing(tmp_path):
    path = tmp_path / "p.bands"
    plane = _plane(np.uint8, (600, 8))
    for bands in ([(256, plane[256:512])], [(0, plane[:100])], [(0, plane[:256])]):
        with pytest.raises(ValueError), BandWriter(path, plane.shape, plane.dtype) as out:
            for top, band in bands:
                out.write(top, band)
        assert not path.exists()


def test_a_corrupt_frame_raises_and_a_damaged_file_does_not_open(tmp_path):
    plane = _plane(np.float32, (600, 64))
    path = tmp_path / "p.bands"
    _write(path, plane)
    _corrupt_band(path, plane.shape, plane.dtype, 1)
    store = BandArray(path, plane.shape, plane.dtype)
    assert _same(store[:256], plane[:256]), "band 0 is untouched"
    with pytest.raises(BandStoreError, match="band 1"):
        store[300]
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(BandStoreError, match="not a band file"):
        BandArray(path, plane.shape, plane.dtype)
    sound = _write(tmp_path / "q.bands", plane).path
    with pytest.raises(BandStoreError, match="not 600x65"):
        BandArray(sound, (600, 65), np.float32)
    with pytest.raises(BandStoreError):
        BandArray(sound, plane.shape, np.int32)
    (tmp_path / "raw").write_bytes(plane.tobytes())
    with pytest.raises(BandStoreError, match="not a band file"):
        BandArray(tmp_path / "raw", plane.shape, plane.dtype)


# --------------------------------------------------------------------- the cache layer


def _band_raster(x0_cm, y0_cm, step_cm, rows, cols, subsamples):
    """``rasterise_direct_band``'s shape: max-Z with ``nan`` where nothing fell, and a source."""
    top = round((y0_cm - BOUNDS_M["y_min_m"] * 100) / step_cm)
    r, c = (top + np.arange(rows))[:, None], np.arange(cols)[None, :]
    z = np.where((r * 7 + c * 3) % 11 < 3, r * 0.5 + c * 0.25, np.nan).astype(np.float32)
    return z, (r + c) % 5 + 1


def _direct_want():
    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SIZE
    z, source = _band_raster(0, BOUNDS_M["y_min_m"] * 100, step, SIZE, SIZE, 1)
    return (*reduce_direct(z, SIZE, SIZE, 1), reduce_source(z, source, SIZE, SIZE, 1))


def _mesh_band(prepared, x0_cm, y0_cm, scale_cm, rows, cols):
    top = round((y0_cm - BOUNDS_M["y_min_m"] * 100) / scale_cm)
    r, c = (top + np.arange(rows))[:, None], np.arange(cols)[None, :]
    cls = ((r * 5 + c) % 9 // 2).astype(np.uint8)
    return (r * 10.0 - c).astype(np.float32), cls


@pytest.mark.parametrize("storage", [STORAGE_RAW, STORAGE_BANDS])
def test_both_storages_hit_and_hold_the_same_planes(tmp_path, monkeypatch, storage):
    folder, stamp = tmp_path / DIRECT_CACHE_DIR_NAME, direct_cache_stamp(SIZE, 1, "b1")
    stats = rasterise_direct(_band_raster, folder, SIZE, 1, stamp, False, storage)
    assert stats["storage"] == storage
    z, cov = cached_direct(folder, stamp)
    family = cached_family(folder, stamp)
    kind = np.memmap if storage == STORAGE_RAW else BandArray
    assert all(isinstance(p, kind) for p in (z, cov, family))
    for got, want in zip((z, cov, family), _direct_want(), strict=True):
        assert _same(np.asarray(got), want)
    suffix = "" if storage == STORAGE_RAW else BANDS_SUFFIX
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [DIRECT_CACHE_SIDECAR] + [name + suffix for name in DIRECT_PLANES]
    )

    monkeypatch.setattr(rasters, "rasterise_mesh_band", _mesh_band)
    meshes, key = tmp_path / MESH_CACHE_DIR_NAME, mesh_stamp(SIZE, "b1", 2)
    rasterise_meshes({}, meshes, key, BOUNDS_M, 256, False, storage)
    mesh_z, mesh_class = cached_meshes(meshes, key)
    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SIZE
    want_z, want_class = _mesh_band({}, 0, BOUNDS_M["y_min_m"] * 100, step, SIZE, SIZE)
    assert _same(mesh_class[:], want_class)
    assert _same(mesh_z[:], np.where(want_class > 0, want_z, 0.0).astype(np.float32))
    assert missing_caches(tmp_path, stamp, key, False, True) == []
    del z, cov, family, mesh_z, mesh_class


def test_a_legacy_cache_without_a_storage_field_still_hits(tmp_path):
    stamp = mesh_stamp(64, "b1", 2)
    (tmp_path / MESH_CACHE_SIDECAR).write_text(json.dumps(stamp), encoding="utf-8")
    np.full((64, 64), 7.0, np.float32).tofile(tmp_path / MESH_Z_NAME)
    np.full((64, 64), 3, np.uint8).tofile(tmp_path / MESH_CLASS_NAME)
    z, cls = cached_meshes(tmp_path, stamp)
    assert isinstance(z, np.memmap) and float(z[5, 5]) == 7.0 and int(cls[63, 0]) == 3
    del z, cls
    (tmp_path / MESH_CACHE_SIDECAR).write_text(
        json.dumps({**stamp, "storage": "zstd-bands-v9"}), encoding="utf-8"
    )
    assert cached_meshes(tmp_path, stamp) is None, "a storage this reader does not know"


def test_a_rewrite_in_the_other_storage_removes_the_first(tmp_path):
    stamp = direct_cache_stamp(SIZE, 1, "b1")
    rasterise_direct(_band_raster, tmp_path, SIZE, 1, stamp, False, STORAGE_RAW)
    rasterise_direct(_band_raster, tmp_path, SIZE, 1, stamp, False, STORAGE_BANDS)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [DIRECT_CACHE_SIDECAR] + [name + BANDS_SUFFIX for name in DIRECT_PLANES]
    )
    rasterise_direct(_band_raster, tmp_path, SIZE, 1, stamp, False, STORAGE_RAW)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [DIRECT_CACHE_SIDECAR, *DIRECT_PLANES]
    )


def test_a_corrupt_band_raises_and_unstamps_its_cache_so_the_next_run_misses(tmp_path):
    stamp = direct_cache_stamp(SIZE, 1, "b1")
    rasterise_direct(_band_raster, tmp_path, SIZE, 1, stamp, False, STORAGE_BANDS)
    _corrupt_band(tmp_path / (DIRECT_Z_NAME + BANDS_SUFFIX), (SIZE, SIZE), np.float32, 1)
    z, _cov = cached_direct(tmp_path, stamp)
    with pytest.raises(BandStoreError, match="band 1"):
        z[250:300]
    assert not (tmp_path / DIRECT_CACHE_SIDECAR).exists()
    assert cached_direct(tmp_path, stamp) is None


def test_a_truncated_band_file_is_a_miss(tmp_path):
    stamp = direct_cache_stamp(SIZE, 1, "b1")
    rasterise_direct(_band_raster, tmp_path, SIZE, 1, stamp, False, STORAGE_BANDS)
    plane = tmp_path / (DIRECT_COVERAGE_NAME + BANDS_SUFFIX)
    plane.write_bytes(plane.read_bytes()[:-100])
    assert cached_direct(tmp_path, stamp) is None
    assert cached_family(tmp_path, stamp) is None

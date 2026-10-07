"""A layer's bands drawn on threads: the same bytes, the accumulators in band order, the pool,
the band store shared between threads, and how many threads a run takes.

docs/spatial-and-map.md section 40. Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.bandstore import BandArray, BandWriter
from mapgen.cache import DirectPlanes, TopPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.palette.water.open_sea import open_sea
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.pools import free_ram_bytes
from mapgen.render import compose, drawpool
from mapgen.render.compose import BAND_ROWS, render_layer
from mapgen.render.drawpool import AHEAD, add_draw_flags, bands_held, draw_threads, in_order
from mapgen.terrain.measure import RegimeCoverage, SeamTrace
from satisfactory_mcp.domain.spatial import heightfield as hf

#: Six bands, the last one short, one 1 m-scaled texel per output pixel.
N = 5 * BAND_ROWS + 100
OCEAN_DM = round(OCEAN_LEVEL_M * hf.DM_PER_M)


def _stored(tmp_path, name, plane):
    path = tmp_path / f"{name}.bands"
    with BandWriter(path, plane.shape, plane.dtype) as out:
        for top in range(0, plane.shape[0], BAND_ROWS):
            out.write(top, plane[top : top + BAND_ROWS])
    return BandArray(path, plane.shape, plane.dtype)


def _scene(tmp_path):
    """Hills, a measured sea and a void, rocks and an overlay in band stores, every band
    holding a regime seam."""
    rng = np.random.default_rng(7)
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    r, c = np.mgrid[0:N, 0:N].astype(np.float32)
    height = (300 + 400 * np.sin(r / 37.0) * np.cos(c / 53.0)).astype(np.int16)
    height[:, : N // 6] = -1000
    height[:, -N // 8 :] = hf.NODATA
    water = np.where(height == -1000, OCEAN_DM, hf.NODATA).astype(np.int16)
    grades = np.where(height == -1000, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    field = SimpleNamespace(
        height_dm=height,
        provenance_plane=(r // 97 % 3 + 1).astype(np.uint8),
        water_raster=lambda: water,
        water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2,
        spacing_cm=step_cm,
        width=N,
        height=N,
    )
    heights = height.astype(np.float32)
    sea = open_sea(field, (heights, heights.copy()), None, np.zeros((N, N), bool), OCEAN_LEVEL_M)
    rock = ((r // 23 + c // 31) % 4 == 0) & (height != hf.NODATA)
    rock_z = np.where(rock, heights * 10.0 + 800.0 + rng.normal(0, 50, (N, N)), 0.0)
    top = (r % 200 < 6) & (c % 150 < 40)
    planes = {
        "rock_z": rock_z.astype(np.float32),
        "rock_cov": rock.astype(np.uint8),
        "top_z": np.where(top, heights * 10.0 + 2000.0, 0.0).astype(np.float32),
        "top_cov": top.astype(np.uint8),
    }
    stored = {name: _stored(tmp_path, name, plane) for name, plane in planes.items()}
    return SimpleNamespace(
        field=field,
        heights=heights,
        sea=sea,
        direct=DirectPlanes(stored["rock_z"], stored["rock_cov"], heights.copy(), 1),
        overlay=TopPlanes(stored["top_z"], stored["top_cov"], 1),
        measured=(rng.random((N, N)) < 0.5).astype(np.uint8) * 255,
        stores=list(stored.values()),
    )


class _Surface:
    def __init__(self):
        self.z, self.land = np.zeros((N, N), np.float32), np.zeros((N, N), np.float32)

    def put(self, row, z_m, land, columns=slice(None)):
        self.z[row : row + len(z_m), columns] = z_m
        self.land[row : row + len(z_m), columns] = land


def _draw(scene, layer, threads):
    seam, regimes, surface = SeamTrace(), RegimeCoverage(), _Surface()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    rgb = render_layer(
        layer, scene.field, np.full((1, 1, 3), 90.0, np.float32), 1, borrow, N, False,
        scene.heights, direct=scene.direct, seam=seam, regimes=regimes,
        measured_plane_u8=scene.measured, overlay=scene.overlay, sea=scene.sea, unlit=True,
        surface=surface, threads=threads,
    )  # fmt: skip
    measured = json.dumps({"seam": seam.result(), "regimes": regimes.result()}, sort_keys=True)
    return rgb, measured, surface


@pytest.mark.parametrize("layer", ["terrain", "satellite"])
def test_a_layer_draws_the_same_bytes_and_measures_on_any_number_of_threads(tmp_path, layer):
    scene = _scene(tmp_path)
    rgb, measured, surface = _draw(scene, layer, 1)
    assert json.loads(measured)["seam"]["measured"], "the fixture has a seam to measure"
    for threads in (3, 64):
        got, got_measured, got_surface = _draw(scene, layer, threads)
        assert got.tobytes() == rgb.tobytes(), threads
        assert got_measured == measured, threads
        assert got_surface.z.tobytes() == surface.z.tobytes(), threads
        assert got_surface.land.tobytes() == surface.land.tobytes(), threads
    assert all(store.keep == 3 for store in scene.stores), "the band stores keep three again"


def test_the_accumulators_take_the_bands_in_order_whatever_order_they_finish(tmp_path, monkeypatch):
    merged, finished = [], []
    real = compose._draw_band

    def last_first(job, out, top):
        time.sleep(0.05 * (6 - top // BAND_ROWS))
        owed = real(job, out, top)
        finished.append(top)
        return [*owed, (merged.append, top)]

    scene = _scene(tmp_path)
    rgb, measured, _surface = _draw(scene, "terrain", 1)
    monkeypatch.setattr(compose, "_draw_band", last_first)
    merged.clear()
    got, got_measured, _surface = _draw(scene, "terrain", 6)
    tops = list(range(0, N, BAND_ROWS))
    assert finished != tops, "the bands finished out of order"
    assert merged == tops
    assert (got.tobytes(), got_measured) == (rgb.tobytes(), measured)


def test_the_pool_yields_in_order_and_keeps_a_bounded_number_ahead():
    lock, running, submitted = threading.Lock(), [0], []
    peak = [0]

    def work(k):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.001 * (k % 5))
        with lock:
            running[0] -= 1
        return k * k

    def items():
        for k in range(40):
            submitted.append(k)
            yield k

    got = []
    for value in in_order(work, items(), 4):
        got.append(value)
        assert len(submitted) - len(got) <= AHEAD * 4
    assert got == [k * k for k in range(40)]
    assert 1 < peak[0] <= 4


def test_one_thread_is_a_plain_loop_on_the_caller_s_thread():
    caller = threading.get_ident()
    assert list(in_order(lambda k: (k, threading.get_ident()), range(3), 1)) == [
        (0, caller),
        (1, caller),
        (2, caller),
    ]


def test_a_failing_band_raises_in_its_turn_and_cancels_the_rest():
    done = []

    def work(k):
        if k == 3:
            raise ValueError("band 3")
        time.sleep(0.01)
        done.append(k)
        return k

    got = []
    with pytest.raises(ValueError, match="band 3"):
        got.extend(in_order(work, range(100), 2))
    assert got == [0, 1, 2]
    assert len(done) < 20, "the items not yet started were cancelled"


def test_threads_share_a_band_store_and_decode_each_band_once(tmp_path):
    rng = np.random.default_rng(1)
    plane = rng.normal(size=(10 * BAND_ROWS - 40, 64)).astype(np.float32)
    store = _stored(tmp_path, "p", plane)
    decoded = []
    real = store._zd

    class Counting:
        def decompress(self, blob):
            decoded.append(len(blob))
            time.sleep(0.002)
            return real.decompress(blob)

    store._zd = Counting()
    tops = list(range(0, plane.shape[0], BAND_ROWS))

    def read(top):
        lo, hi = max(top - 8, 0), min(top + BAND_ROWS + 8, plane.shape[0])
        return store[lo:hi].tobytes() == plane[lo:hi].tobytes()

    with store.holding(len(tops)):
        assert store.keep == len(tops)
        assert all(in_order(read, tops * 4, 8))
    assert len(decoded) == len(tops), "each band decoded once, however many threads ask"
    assert store.keep == 3 and len(store._cache) <= 3


def test_the_band_stores_hold_two_bands_a_thread_and_a_halo():
    assert bands_held(1) == 4
    assert bands_held(8) == 18


def test_the_thread_count_is_the_request_or_the_default_capped_by_memory(monkeypatch):
    monkeypatch.setattr(drawpool.os, "cpu_count", lambda: 32)
    gb = 1e9
    assert draw_threads(None, "terrain", 32768, free=60 * gb) == drawpool.DRAW_THREADS
    assert draw_threads(16, "terrain", 32768, free=60 * gb) == 16
    assert draw_threads(32, "painted", 32768, free=60 * gb) == 16
    assert draw_threads(None, "painted", 32768, free=20 * gb) == 4
    assert draw_threads(None, "relief", 32768, free=20 * gb) == 7
    assert draw_threads(None, "painted", 32768, free=4 * gb) == 1
    assert draw_threads(1, "terrain", 2048, free=60 * gb) == 1
    assert draw_threads(0, "terrain", 2048, free=60 * gb) == 1
    assert draw_threads(None, "painted", 2048, free=4 * gb) == drawpool.DRAW_THREADS
    monkeypatch.setattr(drawpool.os, "cpu_count", lambda: 4)
    assert draw_threads(None, "terrain", 2048, free=60 * gb) == 4
    monkeypatch.setattr(drawpool, "free_ram_bytes", lambda: None)
    assert draw_threads(None, "painted", 32768) == 4


def test_free_memory_reads_on_this_machine_and_the_flag_parses():
    free = free_ram_bytes()
    assert free is None or free > 0
    parser = argparse.ArgumentParser()
    add_draw_flags(parser)
    assert parser.parse_args([]).draw_threads is None
    assert parser.parse_args(["--draw-threads", "1"]).draw_threads == 1

"""The draw's CUDA kernels give the CPU's bytes: the relight, the arches' FXAA, the terrain's
pieces, and where each call ran.

docs/map/renders.md section 41, "The draw on the GPU". Synthetic rows throughout. The kernel
tests skip, saying why, on a machine without numba, CuPy or a CUDA device; the log line runs
everywhere.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen import jit
from mapgen.colour import linear_to_srgb_unit
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting.hillshade import flat_shade, hillshade
from mapgen.lighting.stage import TERM_CROWNED_DIRECT, TERM_CROWNED_SKY, TERM_DIRECT, TERM_SKY
from mapgen.palette.lightparams import shader_light
from mapgen.palette.styles import terrain_colours
from mapgen.palette.water.open_sea import open_sea
from mapgen.palette.water.shore import OCEAN_LEVEL_M, blend_water, shore_terms
from mapgen.render.draw import archaa, light, painting
from mapgen.render.draw.compose import BAND_ROWS
from mapgen.render.draw.stream import RenderStream
from mapgen.render.ground.void import DrawnVoid
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.draw import render_layer

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _rows(rows: int, cols: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unlit colour, terms and land weight: random bytes, with every extreme in some row."""
    rng = np.random.default_rng(seed)
    rgb = rng.integers(0, 256, (rows, cols, 3), dtype=np.uint8)
    terms = rng.integers(0, 256, (rows, cols, 4), dtype=np.uint8)
    land = rng.integers(0, 256, (rows, cols), dtype=np.uint8)
    rgb[0:1], terms[1:2], land[2:3] = 255, 255, 0
    rgb[3:4], terms[4:5], land[5:6] = 0, 0, 255
    return rgb, terms, land


def _channels(layer: str) -> tuple[int, int]:
    crowned = shader_light(layer).get("crowns")
    return (TERM_CROWNED_DIRECT, TERM_CROWNED_SKY) if crowned else (TERM_DIRECT, TERM_SKY)


# ------------------------------------------------------------------------------ relight


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("layer", ["terrain", "relief-dark", "painted"])
@pytest.mark.parametrize("shape", [(7, 300), (64, 257), (1, 1)])
def test_the_relight_is_the_cpu_s_bit_for_bit(monkeypatch, layer, shape):
    """Both light spaces, sRGB and linear under the tone curve, at sizes off the block size."""
    from mapgen.render.gpu import relight

    rgb, terms, land = _rows(*shape, seed=shape[1])
    params = shader_light(layer)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    want = light.relight_rows(rgb, terms, land, params)
    got = relight.relight_rows(rgb, terms, land, params, _channels(layer))
    assert got is not None and got.dtype == np.uint8
    assert got.tobytes() == want.tobytes()


@pytest.mark.usefixtures("device")
def test_the_switch_relights_on_the_device(monkeypatch):
    from mapgen.render.gpu import device as gpu_device

    rgb, terms, land = _rows(40, 90, seed=3)
    params = shader_light("painted")
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    want = light.relight_rows(rgb, terms, land, params)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    gpu_device.ran()
    assert light.relight_rows(rgb, terms, land, params).tobytes() == want.tobytes()
    assert gpu_device.ran() == {gpu_device.device_name(): 1}


def test_the_srgb_steps_are_numpy_s_own_bytes():
    """Each step is the first float32 the CPU rounds to its byte; the one before rounds lower."""
    from mapgen.render.gpu.relight import DARK_KNEE, srgb_steps

    steps = srgb_steps()
    assert steps is not None, "numpy's pow rises with c here"
    start = np.nextafter(DARK_KNEE, np.float32(1))
    assert np.all(np.diff(steps) >= 0) and steps[0] == start and np.all(np.isfinite(steps))

    def byte(c: np.ndarray) -> np.ndarray:
        return np.round(linear_to_srgb_unit(c) * 255).astype(np.int64)

    k = np.arange(256)
    assert np.all(byte(steps) >= k)
    later = steps > start
    below = np.nextafter(steps[later], np.float32(0))
    assert np.all(byte(below) < k[later]), "no float32 before a step reaches its byte"


@pytest.mark.usefixtures("device")
def test_a_relight_the_device_has_no_memory_for_runs_on_the_cpu(monkeypatch):
    from mapgen.render.gpu import device as gpu_device
    from mapgen.render.gpu import relight

    rgb, terms, land = _rows(20, 50, seed=4)
    params = shader_light("terrain")
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    want = light.relight_rows(rgb, terms, land, params)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    monkeypatch.setattr(relight, "_relight", _out_of_memory)
    gpu_device.ran()
    assert light.relight_rows(rgb, terms, land, params).tobytes() == want.tobytes()
    assert gpu_device.ran() == {gpu_device.ON_CPU: 1}


def _out_of_memory(*_args: object) -> None:
    raise MemoryError("out of device memory")


# --------------------------------------------------------------------------------- FXAA


def _stairs(rows: int, cols: int, seed: int) -> np.ndarray:
    """Stair-stepped diagonals over noise: edges FXAA finds."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols]
    lit = ((xx + 2 * yy) // 7 % 2 == 0)[..., None]
    rgb = np.where(lit, [200, 190, 170], [60, 70, 50]).astype(int)
    return np.clip(rgb + rng.integers(-3, 4, rgb.shape), 0, 255).astype(np.uint8)


def _arches(rows: int, cols: int) -> np.ndarray:
    """Arches at both sheet sides, at the top row, mid-band and two near enough to merge."""
    cover = np.zeros((rows, cols), np.uint8)
    cover[0:4, 0:30] = 1
    cover[20:70, cols - 25 : cols] = 1
    cover[30:50, 100:130] = 1
    cover[35:60, 160:170] = 1
    return cover


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("seed", [1, 2])
@pytest.mark.parametrize("core", [slice(0, 96), slice(14, 82), slice(40, 41)])
def test_the_arches_fxaa_is_the_cpu_s_bit_for_bit(seed, core):
    from mapgen.render.gpu import fxaa

    noise = np.random.default_rng(seed).integers(0, 256, (96, 300, 3), dtype=np.uint8)
    for rgb in (_stairs(96, 300, seed), noise):
        cover = _arches(96, 300)
        want = archaa.arch_fxaa(rgb, cover, core)
        got = fxaa.arch_fxaa(rgb, cover, core)
        assert got is not None and got.tobytes() == want.tobytes()
        assert (want != rgb[core]).any(), "the arches moved"


@pytest.mark.usefixtures("device")
def test_rows_without_an_arch_come_back_as_they_were():
    from mapgen.render.gpu import fxaa

    rgb = _stairs(40, 70, 3)
    cover = np.zeros((40, 70), np.uint8)
    cover[0, 0] = 1  # within the halo only: no pixel of the core is kept
    got = fxaa.arch_fxaa(rgb, cover, slice(20, 30))
    assert got is not None and got.tobytes() == rgb[20:30].tobytes()


class _Cutter:
    """``TileStream``'s stand-in: each sheet the rows it was handed, transformed."""

    def __init__(self):
        self.sheets: dict[int, list[np.ndarray]] = {}

    def sheet(self, out_dir, px, trees, dense=None):
        self.sheets[len(self.sheets)] = []
        return len(self.sheets) - 1

    def put(self, sheet, rows, transform=None):
        self.sheets[sheet].append(rows if transform is None else transform(rows))


@pytest.mark.usefixtures("device")
def test_the_stream_antialiases_on_the_device_and_logs_it(monkeypatch, tmp_path: Path, capsys):
    from mapgen.render.gpu import device as gpu_device

    rgb, cover = _stairs(128, 128, 4), np.zeros((128, 128), np.uint8)
    cover[30:90, 40:70] = 1
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    gpu_device.ran()
    cutter = _Cutter()
    stream = RenderStream(cutter, ("terrain",), (tmp_path, "r"), 128, 7, None, cover)
    for top in range(0, 128, 32):
        stream.put(top, {"terrain": rgb[top : top + 32]})
    stream.finish()
    (lit,) = [rows for rows in cutter.sheets.values() if rows]
    want = archaa.arch_fxaa(rgb, cover, slice(0, 128))
    assert np.concatenate(lit).tobytes() == want.tobytes()
    calls = gpu_device.ran()
    assert calls == {gpu_device.device_name(): 4}, "every band of 32 rows is near the arch"


# ------------------------------------------------------------------------- the terrain

SP = 0.915


def _ground(rows: int, cols: int, seed: int, void: bool):
    """Hills under a sea and rivers, borrowed shading, holes without data, and a void."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float32)
    z = np.sin(xx / 17) * 30 + np.cos(yy / 11) * 25 - 10 + rng.normal(0, 1, (rows, cols))
    z = z.astype(np.float32)
    reach = (xx > cols / 3).astype(np.float32)
    old_cover = np.clip(rng.random((rows, cols), dtype=np.float32) * 2 - 1, 0, 1)
    water = blend_water(reach, old_cover, rng.random((rows, cols), dtype=np.float32),
                        shore_terms(z, SP), 4.0)  # fmt: skip
    water["river"] = np.clip(rng.random((rows, cols), dtype=np.float32) - 0.7, 0, 1)
    water["river_below_m"] = rng.random((rows, cols), dtype=np.float32) * 5
    borrow = (0.8 + 0.4 * rng.random((rows, cols), dtype=np.float32)).astype(np.float32)
    missing = rng.random((rows, cols)) < 0.05
    planes = None
    if void:
        unit = [rng.random((rows, cols), dtype=np.float32) * 3 - 2 for _ in range(4)]
        planes = DrawnVoid(*(np.clip(p, 0, 1).astype(np.float32) for p in unit))
    return z, water, borrow, missing, planes


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("shape", [(288, 544), (19, 33)])
@pytest.mark.parametrize("unlit", [True, False])
@pytest.mark.parametrize("void", ["sea", "open sea", "void"])
def test_the_terrain_piece_is_the_cpu_s_bit_for_bit(shape, unlit, void):
    """The ramp, flat or under the hillshade, the shore's water, each way of the void."""
    from mapgen.render.gpu.terrain import TerrainPiece, terrain_bytes

    rows, cols = shape
    z, water, borrow, missing, planes = _ground(rows, cols, rows + cols, void == "void")
    open_sea = object() if void != "sea" else None
    shade = flat_shade(z.shape) if unlit else hillshade(z, SP)
    ramp = (-30.0, 45.0)
    scene = {"z_m": z, "borrow": borrow, "ramp_lo": ramp[0], "ramp_hi": ramp[1],
             "water": water, "shade": shade}  # fmt: skip
    rgb = painting._void(terrain_colours(scene), missing, open_sea, planes)
    kept = (slice(3, rows - 3), slice(2, cols - 5))
    want = np.clip(rgb[kept], 0, 255).astype(np.uint8)
    piece = TerrainPiece(z, None if unlit else SP, borrow, water, missing, planes,
                         open_sea is not None, ramp, kept)  # fmt: skip
    got = terrain_bytes(piece)
    assert got is not None and got.tobytes() == want.tobytes()


@pytest.mark.usefixtures("device")
def test_a_terrain_piece_off_float32_is_left_to_the_cpu():
    from mapgen.render.gpu.terrain import TerrainPiece, terrain_bytes

    z, water, borrow, missing, _ = _ground(20, 30, 1, False)
    kept = (slice(0, 20), slice(0, 30))
    piece = TerrainPiece(z.astype(np.float64), None, borrow, water, missing, None, False,
                         (0.0, 1.0), kept)  # fmt: skip
    assert terrain_bytes(piece) is None


N = 2 * BAND_ROWS + 100
OCEAN_DM = round(OCEAN_LEVEL_M * hf.DM_PER_M)


def _field():
    """Hills, a measured sea along one side and no data along the other."""
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    r, c = np.mgrid[0:N, 0:N].astype(np.float32)
    height = (300 + 400 * np.sin(r / 37.0) * np.cos(c / 53.0)).astype(np.int16)
    height[:, : N // 6] = -1000
    height[:, -N // 8 :] = hf.NODATA
    water = np.where(height == -1000, OCEAN_DM, hf.NODATA).astype(np.int16)
    grades = np.where(height == -1000, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    field = SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: water, water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2,
        spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip
    heights = height.astype(np.float32)
    sea = open_sea(field, (heights, heights.copy()), None, np.zeros((N, N), bool), OCEAN_LEVEL_M)
    return field, heights, sea


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("unlit", [True, False])
def test_the_terrain_layer_draws_the_cpu_s_bytes_on_the_device(monkeypatch, unlit):
    field, heights, sea = _field()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))

    def draw(threads, columns):
        return render_layer(
            "terrain", field, 1, borrow, N, False,
            heights, sea=sea, unlit=unlit, threads=threads, columns=columns,
        )  # fmt: skip

    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    want = draw(1, N)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    for threads, columns in ((1, N), (4, 97)):
        assert draw(threads, columns).tobytes() == want.tobytes(), (threads, columns)


def test_a_gpu_draw_logs_where_its_calls_ran():
    pytest.importorskip("cupy")
    from mapgen.render.gpu.device import ON_CPU, calls_line

    assert calls_line({"RTX": 5, ON_CPU: 1}) == (
        "draw: relight, FXAA and terrain calls 5 on RTX; 1 ran on the CPU, the device out of memory"
    )
    assert "calls none on CUDA; 0 ran" in calls_line({})

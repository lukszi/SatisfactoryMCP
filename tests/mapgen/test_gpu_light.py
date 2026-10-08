"""The light's spans and atlas cells on the GPU give the reference's bits, the device's lanes
bound how many light processes hold it, and a block's tiles encode alike on threads.

docs/map/renders.md section 41, "On the GPU". Synthetic fixtures: no install, no field. Each
comparison runs one call under ``MAPGEN_KERNELS=numpy`` and again under ``cuda`` and compares
the bytes; the kernel tests skip, saying why, without numba, CuPy or a CUDA device.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np
import pytest

from mapgen import jit
from mapgen.lighting import bake, encoding, lanes, light_tiles, stage
from mapgen.lighting import horizon as hz
from mapgen.lighting.spans import bake as span_bake
from mapgen.lighting.spans import march as spans
from mapgen.lighting.spans.holes import find_holes, opened
from tests.support.relief import octave_terrain

SP = 1.0
HALO = hz.horizon_reach_px(SP)

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _bits(value: object) -> object:
    if isinstance(value, tuple | list):
        return tuple(_bits(part) for part in value)
    if value is None or isinstance(value, int):
        return value
    assert isinstance(value, np.ndarray)
    return value.dtype, value.shape, value.tobytes()


def _same(monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]) -> object:
    """``call`` under the reference and on the GPU: the same dtypes, shapes and bytes."""
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert jit.gpu_on()
    on_gpu = call()
    assert _bits(on_gpu) == _bits(reference)
    return on_gpu


def _spans(z: np.ndarray) -> spans.SpanSurface:
    """A span over a third of ``z``: an arch deck 6 to 9 m over it, and thin posts."""
    lo = np.where(z > 15, z + 6, np.nan).astype(np.float32)
    lo[::23, :] = z[::23, :] + 0.2
    return spans.span_surface(np.fmax(lo + 3, z), z, lo, lo + 3)


def _crowns(z: np.ndarray, seed: int) -> spans.SpanSurface:
    """Crowns 4 to 12 m tall on a share of ``z``, each underside its own share of the top."""
    rng = np.random.default_rng(seed)
    lift = np.where(rng.random(z.shape) < 0.35, rng.uniform(4, 12, z.shape), 0)
    under = rng.uniform(0.2, 0.7, z.shape)
    top = np.where(lift > 0, z + lift, np.nan).astype(np.float32)
    lo = np.where(lift > 0, z + under * lift, np.nan).astype(np.float32)
    return spans.span_surface((z + lift).astype(np.float32), z, lo, top)


# --------------------------------------------------------------------------- the kernels


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("az", [0.0, 33.75, 90.0, 191.25, 270.0, 303.75])
def test_the_span_march_is_the_reference_bit_for_bit(monkeypatch, az):
    surface = _spans(octave_terrain(2 * HALO + 150, seed=8))
    for fade in (hz.FADE_M, hz.OCCLUDER_FADE_M):
        _same(monkeypatch, lambda f=fade: tuple(spans.march_spans(surface, HALO, az, SP, f)))
    crowns = _crowns(octave_terrain(2 * HALO + 70, seed=2), seed=int(az))
    _same(monkeypatch, lambda: tuple(spans.march_spans(crowns, HALO, az, SP, hz.OCCLUDER_FADE_M)))


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("spacing", [0.5, 2.0])
def test_the_sky_view_with_spans_is_the_reference_bit_for_bit(monkeypatch, spacing):
    sky = int(np.ceil(hz.SKY_RADIUS_M / spacing)) + 2
    for surface in (_spans(octave_terrain(2 * sky + 140, seed=4)),
                    _crowns(octave_terrain(2 * sky + 90, seed=5), seed=6)):  # fmt: skip
        _same(monkeypatch, lambda s=surface: spans.sky_view_spans(s, sky, spacing))


@pytest.mark.usefixtures("device")
def test_the_kernels_keep_subnormals_as_numpy_does(tmp_path):
    """CuPy's own compile adds ``-ftz=true`` after the options; ``jit`` compiles without it."""
    import cupy as cp

    code = (
        'extern "C" __global__ void mul(const float* a, const float* b, float* out) {\n'
        "    out[threadIdx.x] = a[threadIdx.x] * b[threadIdx.x];\n}\n"
    )
    a = np.array([1e-20, 3e-39, 1.0, 2.0], np.float32)
    b = np.array([1e-20, 1.0, 2e-39, 0.5], np.float32)
    out = cp.empty(4, np.float32)
    kernel = cp.RawModule(path=str(jit._cubin(code))).get_function("mul")
    kernel((1,), (4,), (cp.asarray(a), cp.asarray(b), out))
    assert out.get().tobytes() == (a * b).tobytes()


# ------------------------------------------------------------------------ the atlas cells


def _block(seed: int, nan: str | None) -> tuple[np.ndarray, int, span_bake.BlockSpans, object]:
    """A block's heights with an arch deck and crowns, holes as ``bake_block`` opens them:
    none, a patch (``"patch"``) or all (``"all"``)."""
    z = octave_terrain(2 * HALO + 90, seed=seed)
    if nan == "patch":
        z[HALO + 10 : HALO + 30, HALO + 40 : HALO + 75] = np.nan
    elif nan == "all":
        z[...] = np.nan
    ground, crowns = _spans(z), _crowns(z, seed)
    holes = find_holes(z[HALO - 1 : 1 - HALO, HALO - 1 : 1 - HALO])
    block = span_bake.BlockSpans(ground, crowns)
    if holes is not None:
        z, block = opened(z), stage._opened_spans(block)
    return z, HALO - 1, block, holes


def _cells(z, halo, block, holes, wanted=None) -> list[object]:
    found = span_bake.horizon_cells(z, halo, SP, block, holes, wanted)
    return [
        (cell.k, cell.deg, cell.bands and tuple(cell.bands), cell.whole, cell.band_in)
        for cell in found
    ]


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("nan", [None, "patch", "all"])
def test_a_block_s_cells_on_the_device_are_the_reference_s(monkeypatch, nan):
    z, halo, block, holes = _block(seed=3, nan=nan)
    found = _same(monkeypatch, lambda: _cells(z, halo, block, holes))
    assert isinstance(found, list) and len(found) == 2 * hz.HORIZON_DIRS
    plain = span_bake.BlockSpans(None, block.crowns)
    _same(monkeypatch, lambda: _cells(z, halo, plain, holes))


@pytest.mark.usefixtures("device")
def test_a_cell_not_wanted_comes_without_its_bands_on_the_device(monkeypatch):
    z, halo, block, holes = _block(seed=4, nan=None)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    wanted = {20, 52}
    found = list(span_bake.horizon_cells(z, halo, SP, block, holes, wanted))
    assert {cell.k for cell in found if cell.bands is not None} == wanted


def _out_of_memory(*_args: object) -> None:
    raise MemoryError("out of device memory")


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize("room_for_a_call", [True, False])
def test_a_block_the_device_runs_out_of_memory_for_finishes_on_the_host(
    monkeypatch, room_for_a_call
):
    """Out of memory at the fifth direction's crowns: the directions left march a call at a
    time on the device, or with no room for one either, on numba."""
    from mapgen.lighting import gpu
    from mapgen.lighting.spans import device as on_device
    from mapgen.lighting.spans import gpu as span_gpu

    z, halo, block, holes = _block(seed=5, nan="patch")
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = _bits(_cells(z, halo, block, holes))
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    marched = on_device.DeviceCells.bands
    calls = [0]

    def fails_late(self, *args):
        calls[0] += 1
        if calls[0] > 9:
            raise MemoryError("out of device memory")
        return marched(self, *args)

    monkeypatch.setattr(on_device.DeviceCells, "bands", fails_late)
    if not room_for_a_call:
        monkeypatch.setattr(span_gpu, "_march_spans", _out_of_memory)
    gpu.ran()
    assert _bits(_cells(z, halo, block, holes)) == reference
    counted = gpu.ran()
    left = 2 * (hz.HORIZON_DIRS - 4)
    assert counted.pop(jit.ON_NUMBA, 0) == (0 if room_for_a_call else left)
    assert list(counted.values()) == [8 + left if room_for_a_call else 8]


# ------------------------------------------------------------------------------ the lanes


def test_a_process_that_joined_no_lanes_holds_none():
    assert lanes._lanes is None
    with lanes.device_lane():
        pass


def test_a_block_waits_while_every_lane_is_taken(monkeypatch):
    monkeypatch.setattr(lanes, "_lanes", threading.Semaphore(1))
    entered = threading.Event()

    def second_block() -> None:
        with lanes.device_lane():
            entered.set()

    with lanes.device_lane():
        waiter = threading.Thread(target=second_block)
        waiter.start()
        assert not entered.wait(0.2), "the second block waits for the lane"
    assert entered.wait(5), "and takes it once the first lets go"
    waiter.join()


def test_only_a_gpu_bake_hands_its_processes_the_lanes(monkeypatch):
    made: list[dict[str, object]] = []
    monkeypatch.setattr(bake, "ProcessPoolExecutor", lambda **kw: made.append(kw))
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    bake._light_pool(3)
    monkeypatch.setattr(jit, "_numba", lambda: object())
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    bake._light_pool(3)
    assert made[0] == {"max_workers": 3}
    assert made[1]["initializer"] is lanes.join and len(made[1]["initargs"]) == 1


# --------------------------------------------------------------------------- the encode


def test_a_block_s_tiles_encode_to_the_same_bytes_on_threads(tmp_path):
    rng = np.random.default_rng(9)
    nrm = rng.integers(0, 256, (512, 512, 4), dtype=np.uint8)
    atlas = rng.integers(0, 256, (2 * hz.HORIZON_DIRS, 256, 256), dtype=np.uint8)
    written = {}
    for threads in (1, 3):
        dest = tmp_path / str(threads)
        jobs = light_tiles.tile_jobs(dest, 5, 0, 0, nrm, atlas, frozenset({(1, 0)}))
        hz_bytes = encoding.encoded(jobs, threads)
        tiles = {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob("*.webp")}
        written[threads] = (hz_bytes, tiles)
    assert written[1] == written[3] and len(written[1][1]) == 8


def test_only_a_gpu_bake_encodes_a_block_on_threads(monkeypatch):
    monkeypatch.setattr(encoding.os, "cpu_count", lambda: 32)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    assert encoding.encode_threads(2) == 1
    monkeypatch.setattr(jit, "_numba", lambda: object())
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert [encoding.encode_threads(n) for n in (1, 4, 8, 16, 64)] == [8, 8, 4, 2, 1]

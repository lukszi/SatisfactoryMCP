"""The numba kernels give the bits of the numpy reference they replace, and the switch holds.

docs/map/renders.md section 41. Synthetic fixtures: no install, no field. Each comparison runs
one call twice, ``MAPGEN_KERNELS=numpy`` then the kernels, and compares the bytes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

import numpy as np
import pytest

from mapgen import jit
from mapgen.lighting import horizon as hz
from mapgen.lighting.spans import march as spans
from mapgen.terrain import sample as sm
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.paths import REPO_ROOT
from tests.support.relief import octave_terrain

needs_numba = pytest.mark.skipif(jit._numba() is None, reason="numba is not installed")


def _both(monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]) -> tuple[object, object]:
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.delenv(jit.KERNEL_SWITCH)
    assert jit.kernels_on()
    return reference, call()


def _bits(value: object) -> object:
    if isinstance(value, tuple):
        return tuple(_bits(part) for part in value)
    assert isinstance(value, np.ndarray)
    return value.dtype, value.shape, value.tobytes()


def _same(monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]) -> None:
    reference, compiled = _both(monkeypatch, call)
    assert _bits(compiled) == _bits(reference)


# ------------------------------------------------------------------------------- switch


def test_the_switch_names_the_reference(monkeypatch):
    monkeypatch.setenv(jit.KERNEL_SWITCH, " NumPy ")
    assert not jit.kernels_on()
    monkeypatch.setattr(jit, "_numba", lambda: None)
    monkeypatch.delenv(jit.KERNEL_SWITCH)
    assert not jit.kernels_on(), "without numba the reference runs"


def test_the_reference_never_loads_numba():
    code = (
        "import os, sys\n"
        "os.environ['MAPGEN_KERNELS'] = 'numpy'\n"
        "import numpy as np\n"
        "from mapgen.lighting import horizon as hz\n"
        "from mapgen.terrain import sample as sm\n"
        "z = np.random.default_rng(1).random((90, 90), dtype=np.float32)\n"
        "hz.march_horizon(z, 40, 30.0, 4.0); hz.sky_view(z, 12, 1.0)\n"
        "taps = (sm.taps_linear(np.arange(5.0), 90), sm.taps_linear(np.arange(9.0), 90))\n"
        "sm.sample_plain(z, taps); sm.resample(z, *taps, None)\n"
        "print(sorted(m for m in sys.modules if m == 'numba' or m.endswith('.kernels')))\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "[]"


@needs_numba
def test_every_kernel_releases_the_gil_and_caches_on_disk():
    from numba.core.caching import NullCache

    from mapgen.lighting import kernels as light
    from mapgen.lighting.spans import kernels as span_kernels
    from mapgen.terrain import kernels as gathers

    every = (light.march, light.sky_view, span_kernels.march_spans, span_kernels.sky_view_spans,
             gathers.separable, gathers.pchip)  # fmt: skip
    for kernel in every:
        assert kernel.targetoptions["nogil"] is True
        assert not isinstance(kernel._cache, NullCache)
        assert isinstance(kernel._cache._cache_file, jit.keyed_cache_files())


@needs_numba
def test_two_processes_compiling_at_once_keep_each_signature_s_own_code(tmp_path):
    """Both read the index before either writes it; the second's index lands last and the
    first's code after it. numba's numbered files would hand ``b`` the code of ``a``."""
    from numba.core.caching import IndexDataCacheFile

    files = jit.keyed_cache_files()
    first, second = (files(str(tmp_path), "kernels.loop-1.py313", "stamp") for _ in range(2))
    held: list[tuple[str, object]] = []
    for process in (first, second):
        process._save_data = lambda name, data: held.append((name, data))
    first.save(("a",), "code a")
    second._load_index = dict
    second.save(("b",), "code b")
    for name, data in reversed(held):
        IndexDataCacheFile._save_data(first, name, data)
    reader = files(str(tmp_path), "kernels.loop-1.py313", "stamp")
    assert reader.load(("b",)) == "code b"
    assert reader.load(("a",)) is None, "the lost entry is compiled again, never mistaken"


# ---------------------------------------------------------------------------- the light

SP = 1.0
HALO = hz.horizon_reach_px(SP)


@needs_numba
@pytest.mark.parametrize("az", [0.0, 33.75, 90.0, 191.25, 270.0])
def test_the_march_is_the_reference_bit_for_bit(monkeypatch, az):
    z = octave_terrain(2 * HALO + 70, seed=1)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, az, SP))
    _same(monkeypatch, lambda: hz.crown_horizon(z + 3, HALO, az, SP, z))


def _spans(z: np.ndarray) -> spans.SpanSurface:
    """A span over a third of ``z``: an arch deck 6 to 9 m over it, and thin posts."""
    lo = np.where(z > 15, z + 6, np.nan).astype(np.float32)
    lo[::23, :] = z[::23, :] + 0.2
    return spans.span_surface(np.fmax(lo + 3, z), z, lo, lo + 3)


@needs_numba
@pytest.mark.parametrize("az", [0.0, 33.75, 90.0, 191.25, 270.0])
def test_the_span_march_and_sky_view_are_the_reference_bit_for_bit(monkeypatch, az):
    surface = _spans(octave_terrain(2 * HALO + 70, seed=8))
    _same(monkeypatch, lambda: tuple(spans.march_spans(surface, HALO, az, SP)))
    _same(monkeypatch, lambda: tuple(spans.march_spans(surface, HALO, az, SP, hz.OCCLUDER_FADE_M)))
    sky = int(np.ceil(hz.SKY_RADIUS_M / SP)) + 2
    _same(monkeypatch, lambda: spans.sky_view_spans(surface, sky, SP))


@needs_numba
def test_the_march_with_crowns_is_the_reference_bit_for_bit(monkeypatch):
    z = octave_terrain(2 * HALO + 50, seed=3)
    occluder = np.where(z > 12, z + 9, np.nan).astype(np.float32)
    _same(monkeypatch, lambda: hz.march_horizon(z, HALO, 45.0, SP, occluder=occluder))


@needs_numba
def test_a_halo_short_of_the_march_is_refused(monkeypatch):
    monkeypatch.delenv(jit.KERNEL_SWITCH, raising=False)
    z = octave_terrain(2 * HALO + 10, seed=4)
    with pytest.raises(ValueError, match="halo"):
        hz.march_horizon(z, HALO - 3, 90.0, SP)


@needs_numba
@pytest.mark.parametrize("spacing", [0.5, 2.0, 100.0])
def test_the_sky_view_is_the_reference_bit_for_bit(monkeypatch, spacing):
    halo = int(np.ceil(hz.SKY_RADIUS_M / spacing)) + 2
    z = octave_terrain(2 * halo + 90, seed=5)
    _same(monkeypatch, lambda: hz.sky_view(z, halo, spacing))
    _same(monkeypatch, lambda: hz.sky_view(z[3:-1, 2:-5], halo, spacing))  # a strided view


# -------------------------------------------------------------------------- the sampler

SOURCE = (160, 700)


def _rasters() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(9)
    heights = rng.integers(-20000, 30000, SOURCE).astype(np.int16)
    heights[rng.random(SOURCE) < 0.08] = hf.NODATA
    cover = rng.integers(0, 256, SOURCE).astype(np.uint8)
    cover[rng.random(SOURCE) < 0.5] = 0
    signed = rng.normal(0.0, 40.0, SOURCE).astype(np.float32)
    return {"i16": heights, "u8": cover, "f32": signed, "f16": signed.astype(np.float16)}


def _taps(kind: str, scale: float):
    rows = np.clip(20.3 + (np.arange(48) + 0.5) * scale, 0.0, SOURCE[0] - 1.0)
    cols = np.clip(1.6 + (np.arange(500) + 0.5) * scale, 0.0, SOURCE[1] - 1.0)
    if kind == "pchip":
        return sm.taps_pchip(rows, SOURCE[0]), sm.taps_pchip(cols, SOURCE[1])
    if kind == "cubic":
        return sm.taps_cubic(rows, SOURCE[0]), sm.taps_cubic(cols, SOURCE[1])
    if kind == "footprint":
        return sm.taps_linear(rows, SOURCE[0]), sm.taps_footprint(cols, scale, SOURCE[1])
    return sm.taps_linear(rows, SOURCE[0]), sm.taps_linear(cols, SOURCE[1])


@needs_numba
@pytest.mark.parametrize("scale", [0.29, 1.3])
@pytest.mark.parametrize("kind", ["linear", "cubic", "footprint"])
@pytest.mark.parametrize("dtype", ["i16", "u8", "f32", "f16"])
def test_the_separable_gathers_are_the_reference_bit_for_bit(monkeypatch, dtype, kind, scale):
    raster, taps = _rasters()[dtype], _taps(kind, scale)
    nodata = hf.NODATA if dtype == "i16" else None
    _same(monkeypatch, lambda: sm.resample(raster, *taps, nodata))
    _same(monkeypatch, lambda: sm.sample_plain(raster, taps))


@needs_numba
@pytest.mark.parametrize("scale", [0.29, 1.3])
@pytest.mark.parametrize("dtype", ["i16", "f32"])
def test_pchip_is_the_reference_bit_for_bit(monkeypatch, dtype, scale):
    raster = _rasters()[dtype]
    smooth, linear = _taps("pchip", scale), _taps("linear", scale)
    _same(monkeypatch, lambda: sm.resample_pchip(raster, *smooth, hf.NODATA))
    _same(monkeypatch, lambda: sm.sample_surface(raster, smooth, linear, hf.NODATA))


def _piece(taps, columns: slice):
    parts = tuple(part[..., columns] for part in taps)
    return sm.PchipTaps(*parts) if isinstance(taps, sm.PchipTaps) else parts


@needs_numba
@pytest.mark.parametrize("kind", ["pchip", "cubic"])
def test_a_column_piece_is_the_whole_rows_columns_with_the_kernels(monkeypatch, kind):
    """A piece's gathers read its block cut to its columns (section 40) and index into it."""
    raster, columns = _rasters()["i16"], slice(300, 428)
    (rows, cols), (linear_rows, linear_cols) = _taps(kind, 1.3), _taps("linear", 1.3)

    def draw(smooth_cols, flat_cols):
        return sm.sample_surface(raster, (rows, smooth_cols), (linear_rows, flat_cols), hf.NODATA)

    def piece():
        return draw(_piece(cols, columns), _piece(linear_cols, columns))

    _same(monkeypatch, piece)
    whole = draw(cols, linear_cols)
    assert _bits(piece()) == _bits(tuple(part[:, columns] for part in whole))


@needs_numba
def test_float64_weights_round_after_every_tap_as_numpy_does(monkeypatch):
    raster = _rasters()["f32"]
    taps = tuple((index, weight.astype(np.float64)) for index, weight in _taps("cubic", 0.7))
    _same(monkeypatch, lambda: sm.sample_plain(raster, taps))

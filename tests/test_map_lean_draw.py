"""The band loop's lean paths give the same bits as the full arithmetic they replace.

docs/spatial-and-map.md section 26, "Drawing less". Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.palette.shore import wet_mix  # noqa: E402
from mapgen.palette.styles import (  # noqa: E402
    PIT_EDGE_RGB,
    PIT_RGB,
    SEA_RGB,
    VOID_EDGE_RGB,
    VOID_RIM_RGB,
    with_void,
)
from mapgen.palette.water import VoidPlanes  # noqa: E402
from mapgen.terrain.sample import (  # noqa: E402
    reads_nothing,
    resample,
    sample_plain,
    taps_cubic,
    taps_footprint,
    taps_linear,
    taps_pchip,
)
from mapgen.tiles import compose  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402

SOURCE = (90, 120)


def _positions(count, first, step, limit):
    return np.clip(first + (np.arange(count) + 0.5) * step, 0.0, limit - 1.0)


def _taps(kind, rows=(40, 30.0), cols=(400, 0.0)):
    """A band of output rows and a run of columns on ``SOURCE``, as ``kind`` taps."""
    made = []
    for (count, first), limit in zip((rows, cols), SOURCE, strict=True):
        position = _positions(count, first, 0.29, limit)
        if kind == "linear":
            made.append(taps_linear(position, limit))
        elif kind == "footprint":
            made.append(taps_footprint(position, 2.6, limit))
        else:
            made.append(taps_cubic(position, limit))
    return tuple(made)


def _rasters():
    rng = np.random.default_rng(7)
    sparse = rng.integers(0, 256, SOURCE).astype(np.uint8)
    sparse[rng.random(SOURCE) < 0.7] = 0
    signed = rng.normal(0.0, 40.0, SOURCE).astype(np.float32)
    signed[::3, ::2] = -0.0
    return {
        "u8": sparse,
        "f16": rng.normal(0.0, 2.0, SOURCE).astype(np.float16),
        "f32": signed,
    }


def _bits(array):
    return array.dtype, array.shape, array.tobytes()


@pytest.mark.parametrize("kind", ["linear", "footprint", "cubic"])
@pytest.mark.parametrize("dtype", ["u8", "f16", "f32"])
def test_sample_plain_is_resample_s_sum_to_the_bit(kind, dtype):
    raster, taps = _rasters()[dtype], _taps(kind)
    assert _bits(sample_plain(raster, taps)) == _bits(resample(raster, *taps, None)[0])


@pytest.mark.parametrize("kind", ["linear", "footprint", "cubic"])
def test_a_band_over_nothing_but_zeros_reads_nothing(kind):
    taps = _taps(kind)
    rows = taps[0][0]
    raster = np.zeros(SOURCE, np.float32)
    raster[: rows.min()] = raster[rows.max() + 1 :] = 5.0
    raster[rows.min() : rows.max() + 1, ::4] = -0.0
    assert reads_nothing(raster, taps)
    got = sample_plain(raster, taps)
    assert _bits(got) == _bits(resample(raster, *taps, None)[0])
    assert not np.signbit(got).any()
    raster[rows.max(), -1] = 1.0
    assert not reads_nothing(raster, taps)


def _with_void_full(rgb, cover, falloff, pit, rim):
    """The blend over every pixel, as it was before it was masked."""
    weight, deep, hole, line = (p[..., None] for p in (cover, falloff, pit, rim))
    edge = VOID_EDGE_RGB * (1.0 - hole) + PIT_EDGE_RGB * hole
    colour = edge * (1.0 - deep) + (SEA_RGB * (1.0 - hole) + PIT_RGB * hole) * deep
    return (rgb * (1.0 - weight) + colour * weight) * (1.0 - line) + VOID_RIM_RGB * line


def _void_planes(shape, rng, empty=0.9):
    """Cover, falloff, pit and rim; cover and rim mostly zero, as off the void they are."""
    planes = [rng.random(shape).astype(np.float32) for _ in range(4)]
    for index in (0, 3):
        planes[index][rng.random(shape) < empty] = 0.0
    return planes


@pytest.mark.parametrize("empty", [0.95, 0.6, 0.0])
@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_the_masked_void_is_the_full_blend_to_the_bit(dtype, empty):
    """Masked while the void covers at most ``VOID_MOST`` of the band, whole past it."""
    rng = np.random.default_rng(3)
    shape = (30, 50)
    rgb = rng.uniform(-5.0, 260.0, (*shape, 3)).astype(dtype)
    rgb[0, 0] = np.nan
    planes = _void_planes(shape, rng, empty)
    got, want = with_void(rgb, *planes), _with_void_full(rgb, *planes)
    assert _bits(got) == _bits(want)
    nothing = [np.zeros(shape, np.float32), *planes[1:3], np.zeros(shape, np.float32)]
    assert _bits(with_void(rgb, *nothing)) == _bits(_with_void_full(rgb, *nothing))


@pytest.mark.parametrize("dry", [0.9, 0.2])
@pytest.mark.parametrize("under_dtype", [np.float32, np.float64])
def test_the_wet_mix_is_the_full_blend_to_the_bit(under_dtype, dry):
    """Masked while at most ``WET_MIX_MOST`` of the band is wet, whole past it; a land
    plane that is a strided view is read in place."""
    rng = np.random.default_rng(5)
    land = rng.uniform(0.0, 255.0, (30, 20, 3)).astype(np.float32).transpose(1, 0, 2)
    under = rng.uniform(-1.0, 255.0, (20, 30, 3)).astype(under_dtype)
    cover = rng.random((20, 30, 1)).astype(np.float32)
    cover[rng.random((20, 30, 1)) < dry] = 0.0
    want = land * (1.0 - cover) + under * cover
    assert _bits(wet_mix(land, under, cover)) == _bits(want)


def _sea(rows, rng):
    """An ``OpenSea`` stand-in whose void lies only in ``rows`` of the field."""
    planes = []
    for _ in range(4):
        plane = np.zeros(SOURCE, np.uint8)
        plane[rows] = rng.integers(0, 256, plane[rows].shape)
        planes.append(plane)
    return SimpleNamespace(void=VoidPlanes(*planes))


def _always_read(*_args):
    return False


@pytest.mark.parametrize("void_rows", [slice(0, 5), slice(30, 60)])
@pytest.mark.parametrize("missing_at", [None, (3, 7)])
def test_the_void_helpers_skip_only_what_reads_as_zero(monkeypatch, void_rows, missing_at):
    """Each helper with its skips against the same helper forced down the full path."""
    rng = np.random.default_rng(11)
    linear = _taps("linear")
    smooth = tuple(
        taps_pchip(_positions(count, first, 0.29, limit), limit)
        for (count, first), limit in zip(((40, 30.0), (400, 0.0)), SOURCE, strict=True)
    )
    sea = _sea(void_rows, rng)
    shape = (40, 400)
    missing = np.zeros(shape, bool)
    if missing_at:
        missing[missing_at] = True
    rgb = rng.uniform(0.0, 255.0, (*shape, 3)).astype(np.float32)
    rock = rng.random(shape).astype(np.float32)
    z_m = rng.uniform(-40.0, 20.0, shape).astype(np.float32)
    wet_plane = (rng.random(SOURCE) < 0.5).astype(np.uint8)
    water = np.where(wet_plane > 0, -170, hf.NODATA).astype(np.int16)
    planes = (water, wet_plane, wet_plane, sea)

    def run():
        return (
            compose._void(rgb, missing, sea, linear, rock, z_m),
            compose._rock_kept(z_m * 100.0, missing, (wet_plane, sea), linear),
            compose._band_water(z_m, planes, smooth, linear),
        )

    lean = run()
    monkeypatch.setattr(compose, "reads_nothing", _always_read)
    full = run()
    for got, want in zip(lean[:2] + lean[2], full[:2] + full[2], strict=True):
        assert _bits(got) == _bits(want)
    assert reads_nothing(sea.void.cover, linear) == (void_rows.stop <= 30)

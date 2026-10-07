"""The colour under the water, painted on the wet pixels only, is the whole band's to the bit.

docs/map/renders.md section 26, "Painting only the wet pixels". Synthetic fixtures: no
install, no field.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.palette.painted import band as painted_band
from mapgen.palette.painted.optics import mix_underwater, underwater
from mapgen.palette.relief import relief_colours
from mapgen.palette.water import wet
from mapgen.palette.water.shore import wet_mix
from mapgen.palette.water.wet import WetPixels
from tests.support.map_scenes import painted_ground_stub, relief_ground
from tests.support.wet_bands import band_water
from tests.support.wet_bands import painted_band as wet_band

SHAPE = (9, 23)
#: Never the whole band, and always: the two ways a band's water is painted.
PATHS = {"wet pixels": 1.0, "whole band": 0.0}


def _bits(array):
    return array.dtype, array.shape, array.tobytes()


def _same(plane):
    return plane


def _water(rng, dry):
    return band_water(rng, SHAPE, dry)


def _painted(rng, dry, **extras):
    return wet_band(rng, SHAPE, dry, **extras)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("dry", [0.0, 0.6, 1.0])
@pytest.mark.parametrize("extras", ["none", "optics", "crowns", "carpet", "opaque", "all"])
def test_painted_water_on_the_wet_pixels_is_the_whole_band_s(monkeypatch, path, dry, extras):
    monkeypatch.setattr(wet, "WET_MOST", PATHS[path])
    rng = np.random.default_rng(len(extras) * 10 + int(dry * 10))
    on = {k: extras in (k, "all") for k in ("optics", "crowns", "carpet", "opaque")}
    scene, ground, crowns = _painted(rng, dry, **on)
    g = rng.random((*SHAPE, 3)).astype(np.float32)
    lit = rng.random((*SHAPE, 3)).astype(np.float32)
    exposure = np.float32(1.3)
    whole = underwater(g, scene, ground, _same, _same, exposure, crowns)
    want = wet_mix(lit, whole, scene["water"]["cover"][..., None])
    got = mix_underwater(lit, g, scene, ground, _same, _same, exposure, crowns)
    assert _bits(got) == _bits(want)


def test_the_painted_band_draws_the_same_bits_either_way(monkeypatch):
    """End to end through ``painted_colours``, with its stand-in ground, one row deep."""
    ground = painted_ground_stub(40)
    rng = np.random.default_rng(3)
    n = (1, 40)
    zero = np.zeros(n, np.float32)
    cover = np.where(rng.random(n) < 0.5, 0.0, rng.random(n)).astype(np.float32)
    water = {"depth_m": rng.uniform(0.0, 4.0, n).astype(np.float32), "cover": cover,
             "edge": zero, "ocean": (cover > 0.5).astype(np.float32), "above_m": zero + 1.0,
             "below_m": zero + 0.5}  # fmt: skip
    scene = {"z_m": zero - 17.5, "borrow": zero + 1.0, "ndl": zero + 0.8,
             "ndl_flat": np.float32(1.0), "rock_weight": zero, "mesh_weight": None,
             "water": water, "water_optics": None}  # fmt: skip
    drawn = {}
    for path, most in PATHS.items():
        monkeypatch.setattr(wet, "WET_MOST", most)
        drawn[path] = painted_band.painted_colours(scene, ground, _same, _same)
    assert _bits(drawn["wet pixels"]) == _bits(drawn["whole band"])


@pytest.mark.parametrize("layer", ["relief", "relief-dark"])
@pytest.mark.parametrize("dry", [0.0, 0.6, 1.0])
@pytest.mark.parametrize("tinted", [True, False])
def test_relief_water_on_the_wet_pixels_is_the_whole_band_s(monkeypatch, layer, dry, tinted):
    rng = np.random.default_rng(int(dry * 10) + 3 * tinted)
    tint = rng.integers(0, 256, SHAPE).astype(np.uint8)
    ground = relief_ground(layer)
    ground.water = tint if tinted else None
    scene = {
        "z_m": rng.uniform(0.0, 100.0, SHAPE).astype(np.float32),
        "spacing_m": 1.0,
        "borrow": rng.uniform(0.8, 1.2, SHAPE).astype(np.float32),
        "water": _water(rng, dry),
        "unlit": False,
    }

    def sample(plane):
        return plane.astype(np.float32)

    drawn = {}
    for path, most in PATHS.items():
        monkeypatch.setattr(wet, "WET_MOST", most)
        drawn[path] = relief_colours(scene, ground, sample, _same)
    assert _bits(drawn["wet pixels"]) == _bits(drawn["whole band"])


def test_the_wet_pixels_read_any_plane_of_the_band_and_leave_the_dry_ones():
    rng = np.random.default_rng(9)
    cover = np.where(rng.random(SHAPE) < 0.5, 0.0, rng.random(SHAPE)).astype(np.float32)
    pixels = WetPixels(cover)
    strided = rng.random((SHAPE[1], SHAPE[0], 3)).astype(np.float32).transpose(1, 0, 2)
    np.testing.assert_array_equal(pixels.take(strided), strided[cover != 0])
    colour = np.ones(3, np.float32)
    assert pixels.take_planes({"plane": cover, "colour": colour})["colour"] is colour
    land = rng.random((*SHAPE, 3)).astype(np.float32)
    under = np.full((pixels.index.size, 3), np.nan, np.float32)
    mixed = pixels.mix(land, pixels.take(land), under, pixels.take(cover)[:, None])
    np.testing.assert_array_equal(mixed[cover == 0], land[cover == 0])
    assert np.isnan(mixed[cover != 0]).all() and not pixels.whole
    assert WetPixels(np.ones(SHAPE, np.float32)).whole

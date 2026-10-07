"""The colour under the water, painted on the wet pixels only, is the whole band's to the bit.

docs/map/renders.md section 26, "Painting only the wet pixels". Synthetic fixtures: no
install, no field.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.gamedata.water.bodies import OCEAN, SWAMP
from mapgen.palette.painted import band as painted_band
from mapgen.palette.painted.optics import class_optics, mix_underwater, underwater, water_table
from mapgen.palette.relief import relief_colours
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.palette.water import wet
from mapgen.palette.water.shore import wet_mix
from mapgen.palette.water.wet import WetPixels
from mapgen.terrain.sample import taps_linear
from tests.support.map_scenes import painted_ground_stub, relief_ground

SHAPE = (9, 23)
#: Never the whole band, and always: the two ways a band's water is painted.
PATHS = {"wet pixels": 1.0, "whole band": 0.0}


def _bits(array):
    return array.dtype, array.shape, array.tobytes()


def _same(plane):
    return plane


def _water(rng, dry):
    """A band's water terms: ``dry`` of it without water, the rest partly or wholly covered."""
    cover = rng.random(SHAPE).astype(np.float32)
    cover[rng.random(SHAPE) < 0.3] = 1.0
    cover[rng.random(SHAPE) < dry] = 0.0
    river = np.where(rng.random(SHAPE) < 0.3, rng.random(SHAPE), 0.0).astype(np.float32)
    planes = {name: rng.uniform(0.0, 6.0, SHAPE).astype(np.float32)
              for name in ("depth_m", "above_m", "below_m", "river_below_m")}  # fmt: skip
    planes["river_below_m"][river == 0] = np.inf
    ocean = (rng.random(SHAPE) < 0.5).astype(np.float32)
    return {**planes, "cover": cover, "depth": rng.random(SHAPE).astype(np.float32),
            "ocean": ocean, "banks": ocean, "edge": rng.random(SHAPE).astype(np.float32),
            "river": river}  # fmt: skip


def _optics(rng, ground):
    """Class optics over a class plane of ocean, swamp and lake, as a band reads them."""
    plane = rng.choice(np.array([OCEAN, SWAMP, 3], np.uint8), (SHAPE[0] + 1, SHAPE[1] + 1))
    taps = tuple(taps_linear(np.linspace(0.0, n - 0.01, n), n + 1) for n in SHAPE)
    rows = water_table(PAINTED_PALETTE)
    river = (rng.random(SHAPE) < 0.2).astype(np.float32)
    return class_optics(plane, rows, ground.water, taps, river, shares=(SWAMP,))


def _painted(rng, dry, *, optics, crowns, carpet, opaque):
    """A painted band's scene, ground and crowns, every plane the band's shape."""
    ground = painted_ground_stub(SHAPE[1])
    ground.water["opaque_tau_m"] = np.float32(0.3)
    if carpet:
        ground.carpet = (rng.integers(0, 256, SHAPE).astype(np.uint8),
                         rng.uniform(-20.0, -14.0, SHAPE).astype(np.float16))  # fmt: skip
    if opaque:
        mud = np.array([0.4, 0.2, 0.2], np.float32)
        ground.opaque_water = [(rng.random(SHAPE).astype(np.float32), mud, SWAMP)]
    if optics:
        ground.water_class = np.zeros((1, 1), np.uint8)
    scene = {
        "z_m": rng.uniform(-20.0, -15.0, SHAPE).astype(np.float32),
        "water": _water(rng, dry),
        "water_optics": _optics(rng, ground) if optics else None,
    }
    layer = None
    if crowns:
        layer = {"alpha": rng.random(SHAPE).astype(np.float32),
                 "colour": rng.random((*SHAPE, 3)).astype(np.float32),
                 "top_m": rng.uniform(-20.0, -12.0, SHAPE).astype(np.float32),
                 "sunk": rng.random(SHAPE).astype(np.float32)}  # fmt: skip
    return scene, ground, layer


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

"""The river's physics row: the game's river absorption over its bed, the sky term at depth,
and no inland opacity floor under the ribbons.

docs/map/water.md section 33, "The river's physics row".
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.colour import linear_to_srgb, oklab, srgb_to_linear
from mapgen.gamedata.water.river_material import RiverAbsorption
from mapgen.palette.painted.optics import WATER_TABLE_COLUMNS, base_water, underwater, water_table
from mapgen.palette.painted.sea import surface_sky
from mapgen.palette.painted.shapes import UnderwaterScene
from mapgen.palette.styles import PAINTED_PALETTE
from tests.support.map_scenes import CLASS_ID, painted_ground_stub, water_scene

WATER = PAINTED_PALETTE["water"]
ROW = PAINTED_PALETTE["water_classes"]["river"]
#: ``MI_SLW_River_Base_01`` on build 502094: ``WaterAbsorptionColour`` and ``WaterMurkiness``.
COLOUR, MURKINESS = (1.0, 0.479167, 0.678819), 0.5
GAME = RiverAbsorption(tuple(c * MURKINESS for c in COLOUR), COLOUR, MURKINESS)
WET_SAND = np.array([0.36, 0.3, 0.22], np.float32)
#: River readings of docs/map/water.md, "The river's physics row", by the depth band each
#: shot shows, matched over the beds below.
SHALLOW, DEEP = (0.2, 1.0), (1.5, 5.0)
READINGS = [
    ("#628164", SHALLOW),
    ("#6a7b61", SHALLOW),
    ("#667d4b", SHALLOW),
    ("#417983", DEEP),
    ("#3f5860", DEEP),
    ("#3e5156", DEEP),
]
BEDS = ("#a68c72", "#a4947f", "#7d7560")


def _linear(hex_colour: str) -> np.ndarray:
    return srgb_to_linear([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)])


def _hue(linear: np.ndarray) -> float:
    lab = oklab(linear)
    return float(np.degrees(np.arctan2(lab[2], lab[1])) % 360)


def _drawn(depths: list[float], bed: np.ndarray = WET_SAND, ribbon: float = 1.0) -> np.ndarray:
    """River-class water over a bed at each depth, linear, by the numpy reference; ``ribbon``
    is the ribbon's share of the water, the rest field water of the river class."""
    n = len(depths)
    ground = painted_ground_stub(n)
    row = water_table(PAINTED_PALETTE, river=GAME)[CLASS_ID["river"]]
    k, body, deep, tau, turbidity, tint = np.split(
        np.tile(row, (1, n, 1)), np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1
    )
    optics = {**ground.water, "k": k, "body": body, "deep": deep, "deep_tau_m": tau,
              "turbidity": turbidity, "tint": tint}  # fmt: skip
    scene = water_scene(n, optics)
    scene["water"]["depth_m"] = np.asarray(depths, np.float32)[None, :]
    scene["water"]["river"] = np.full((1, n), ribbon, np.float32)
    scene["water"]["river_below_m"] = np.zeros((1, n), np.float32)
    beds = np.broadcast_to(bed, (1, n, 3)).astype(np.float32)
    whole = UnderwaterScene(scene["z_m"], scene["water"], optics)
    return underwater(beds, whole, ground, lambda p: p, lambda p: p, np.float32(1.0))[0]


def test_the_river_absorbs_the_game_s_absorption_times_the_sea_s_scale():
    row = water_table(PAINTED_PALETTE, river=GAME)[CLASS_ID["river"]]
    want = [WATER["absorption_scale"] * a for a in GAME.per_m]
    np.testing.assert_allclose(row[:3], want, rtol=1e-6)


def test_the_palette_k_is_what_the_scale_gives_on_the_1_0_material():
    """``k_per_m`` is drawn only where the install's material does not read."""
    fallback = water_table(PAINTED_PALETTE)[CLASS_ID["river"], :3]
    np.testing.assert_allclose(
        fallback, water_table(PAINTED_PALETTE, river=GAME)[CLASS_ID["river"], :3], atol=1e-4
    )


def test_body_and_deep_are_the_sea_s_sky_term():
    """As the sea's: the body with the surface's reflection is the sky term, which is also
    the deep colour, within the palette's sRGB rounding."""
    sky_term = srgb_to_linear(WATER["sky_term"])
    np.testing.assert_allclose(srgb_to_linear(ROW["deep"]), sky_term)
    np.testing.assert_allclose(
        srgb_to_linear(ROW["body"]) + surface_sky(WATER), sky_term, atol=2e-3
    )
    assert ROW["turbidity"] == 0.0


def test_at_no_depth_the_ribbon_shows_its_bed():
    """No inland floor under a ribbon: at its waterline the river is its wet bed and the
    surface's reflection, nothing of its body."""
    got = _drawn([0.0])[0]
    want = WET_SAND * np.float32(WATER["bed_wet"]) + surface_sky(WATER)
    np.testing.assert_allclose(got, want, rtol=1e-6)


def test_field_water_of_the_river_class_keeps_the_inland_floor():
    """Off the ribbon, river-class field water keeps the inland floor and depth as before."""
    row = water_table(PAINTED_PALETTE, river=GAME)[CLASS_ID["river"]]
    k, body, deep, tau = row[:3], row[3:6], row[6:9], row[9]
    keep = np.exp(-k * 1.0) * (1.0 - np.float32(WATER["inland_floor"]))
    want = WET_SAND * np.float32(WATER["bed_wet"]) * keep + body * (1.0 - keep)
    blend = 1.0 - np.exp(-1.0 / tau)
    want = (want + surface_sky(WATER)) * (1.0 - blend) + deep * blend
    np.testing.assert_allclose(_drawn([0.2], ribbon=0.0)[0], want, rtol=1e-5)


def test_shallow_water_keeps_its_bed_and_deep_water_turns_blue():
    shallow, mid, deep = _drawn([0.6, 3.0, 8.0])
    assert _hue(shallow) < 220, "a river 0.6 m deep still reads its bed"
    assert _hue(mid) > 235 and _hue(deep) > 245
    assert np.abs(linear_to_srgb(deep) - np.asarray(WATER["sky_term"])).max() < 1.5


def test_each_channel_runs_monotonically_from_the_bed_to_the_sky_term():
    """Up to the body's sRGB rounding, which the deep blend takes back at a few 1e-7 a step."""
    got = _drawn(list(np.linspace(0.0, 40.0, 401)))
    steps = np.diff(got, axis=0)
    for c in range(3):
        sign = np.sign(got[-1, c] - got[0, c])
        assert (steps[:, c] * sign >= -1e-6).all(), f"channel {c} turns back"


def test_the_screenshot_readings_are_met_by_depth_band():
    """Each reading against the nearest colour the row draws in its band: mean Delta E 4.77
    and at most 7.0 (OKLab x100), where the teal row before reached 4.84 and 8.7."""
    misses = []
    for reading, (lo, hi) in READINGS:
        target = oklab(_linear(reading))
        depths = list(np.linspace(lo, hi, 120))
        misses.append(
            min(
                float(np.min(np.linalg.norm(oklab(_drawn(depths, _linear(bed))) - target, axis=1)))
                for bed in BEDS
            )
            * 100
        )
    assert np.mean(misses) < 4.8
    assert max(misses) < 7.1


def test_only_the_river_row_takes_the_river_material():
    with_game = water_table(PAINTED_PALETTE, river=GAME)
    without = water_table(PAINTED_PALETTE)
    others = [i for i in range(len(without)) if i != CLASS_ID["river"]]
    np.testing.assert_array_equal(with_game[others], without[others])
    np.testing.assert_array_equal(with_game[CLASS_ID["river"], 3:], without[CLASS_ID["river"], 3:])
    assert base_water(PAINTED_PALETTE)["inland_floor"] == WATER["inland_floor"]


@pytest.mark.integration
def test_the_river_absorption_read_from_the_installed_game():
    from mapgen.common import DEFAULT_GAME
    from mapgen.gamedata.install import missing_container, open_game
    from mapgen.gamedata.water.river_material import read_river_absorption

    if missing_container(DEFAULT_GAME):
        pytest.skip(f"needs the installed game at {DEFAULT_GAME}")
    game = open_game(DEFAULT_GAME)
    found = read_river_absorption(game.store, game.scripts, game.index)
    np.testing.assert_allclose(found.colour, COLOUR, rtol=1e-5)
    assert found.murkiness == MURKINESS
    np.testing.assert_allclose(found.per_m, GAME.per_m, rtol=1e-5)

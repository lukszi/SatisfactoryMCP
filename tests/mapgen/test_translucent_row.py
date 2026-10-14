"""Translucent water by depth: the Blue Crater's target on its floor, darker and more teal
deeper, the lake's deep teal at depth.

docs/map/water.md section 33, "Translucent water".
"""

from __future__ import annotations

import numpy as np

from mapgen.colour import oklab, srgb_to_linear
from mapgen.gamedata.water.bodies import WATER_CLASSES
from mapgen.palette.painted.optics import underwater
from mapgen.palette.painted.shapes import UnderwaterScene
from mapgen.palette.styles import PAINTED_PALETTE
from tests.support.map_scenes import class_optics, painted_ground_stub, water_scene

#: The pooled Blue Crater reading, and the depth of the crater's floor (p50).
CRATER_TARGET, CRATER_DEPTH_M = "#718a75", 3.9
#: WetSand's calibrated target, the crater floor's bed.
WET_SAND = "#a4947f"


def _linear(hex_colour: str) -> np.ndarray:
    return srgb_to_linear([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)])


def _drawn(cls: str, depths: list[float], bed: str = WET_SAND) -> np.ndarray:
    """Class ``cls`` over ``bed`` at each depth, inland, linear, by the numpy reference."""
    n = len(depths)
    ground = painted_ground_stub(n)
    scene = water_scene(n, class_optics(ground, cls, n))
    scene["water"]["depth_m"] = np.asarray(depths, np.float32)[None, :]
    g = np.broadcast_to(_linear(bed), (1, n, 3)).astype(np.float32)
    whole = UnderwaterScene(scene["z_m"], scene["water"], scene["water_optics"])
    return underwater(g, whole, ground, lambda p: p, lambda p: p, np.float32(1.0))[0]


def _delta_e(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(oklab(a) - oklab(b))) * 100


def test_the_crater_floor_draws_the_pooled_target():
    got = _drawn("translucent", [CRATER_DEPTH_M])[0]
    assert _delta_e(got, _linear(CRATER_TARGET)) < 0.6


def test_translucent_water_darkens_and_turns_teal_with_depth():
    depths = [2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 20.0, 40.0]
    lab = oklab(_drawn("translucent", depths))
    assert (np.diff(lab[:, 0]) < 0).all(), "deeper is darker"
    hue = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360
    assert (np.diff(hue) > 0).all(), "deeper is more teal"
    assert lab[0, 0] - lab[3, 0] > 0.05, "a 2 to 6 m basin reads its depth"


def test_deep_translucent_water_draws_the_lake_s_deep_colour():
    rows = PAINTED_PALETTE["water_classes"]
    deep = rows["lake"]["deep"]
    assert list(rows["translucent"]["deep"]) == list(deep)
    assert rows["translucent"]["deep_tau_m"] == rows["lake"]["deep_tau_m"]
    got = _drawn("translucent", [200.0])[0]
    assert _delta_e(got, srgb_to_linear(deep)) < 0.3


def test_only_the_opaque_swamp_draws_one_colour_at_every_depth():
    for cls in WATER_CLASSES[2:]:
        mid, deep = _drawn(cls, [4.0, 12.0])
        if cls == "swamp":
            assert _delta_e(mid, deep) < 2.0
        else:
            assert _delta_e(mid, deep) > 3.0, f"{cls} draws one colour past 4 m"

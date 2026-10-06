"""Tree crowns and the water in the game-painted style: a crown that stands out of the water is
drawn over it whole, as over dry ground; one under the surface is drawn in the bed, seen
through the water above its top.

docs/spatial-and-map.md sections 36 and 37. Synthetic fixtures: no install, no field.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.palette.painted import painted_colours  # noqa: E402
from tests.test_map_water_classes import _ground, _optics, _scene  # noqa: E402

N = 4
LEAF = (0.06, 0.12, 0.035)
BED_M = -17.3


def _same(plane):
    return plane


def _crowns(top_m, cover=1.0):
    shape = (1, N)
    cover = np.broadcast_to(np.asarray(cover, np.float32), shape).copy()
    return {
        "cover": cover,
        "rgb": cover[..., None] * np.array(LEAF, np.float32),
        "top_cm": np.broadcast_to(np.asarray(top_m, np.float32) * 100, shape).copy(),
        "ndl": np.ones(shape, np.float32),
        "dome_m": np.zeros(shape, np.float32),
    }


def _draw(ground, depth_m, crowns=None, optics=None, cover=1.0):
    scene = _scene(N, optics)
    scene["z_m"] = np.full((1, N), BED_M, np.float32)
    scene["water"]["depth_m"] = np.full((1, N), depth_m, np.float32)
    scene["water"]["cover"] = np.full((1, N), cover, np.float32)
    if crowns is not None:
        scene["crowns"] = crowns
    return painted_colours(scene, ground, _same, _same).astype(np.float32)


def _gap(a, b):
    return np.abs(a - b).sum(-1)


def test_a_palm_on_awash_sand_is_drawn_whole_as_on_dry_sand():
    """The sand under the palms stands 8 to 27 cm under the drawn sea; the palms do not."""
    ground = _ground(N)
    awash = _draw(ground, 0.27, _crowns(BED_M + 9.0))
    dry = _draw(ground, 0.0, _crowns(BED_M + 9.0), cover=0.0)
    np.testing.assert_array_equal(awash, dry)
    assert (_gap(awash, _draw(ground, 0.27)) > 10).all(), "the crown, not the water"


def test_a_crown_under_the_surface_is_seen_through_the_water_above_its_top():
    ground = _ground(N)
    surface = BED_M + 4.0
    bare = _draw(ground, 4.0)
    whole = _draw(ground, 4.0, _crowns(surface + 5.0))
    shallow = _draw(ground, 4.0, _crowns(surface - 0.3))
    deep = _draw(ground, 4.0, _crowns(surface - 3.0))
    assert (_gap(shallow, bare) > _gap(deep, bare)).all(), "deeper water hides more of it"
    assert (_gap(deep, bare) > 0).all() and (_gap(shallow, whole) > 10).all()


def test_murky_water_hides_a_sunk_crown_the_sea_still_shows():
    ground = _ground(N)
    top = BED_M + 2.0 - 0.3
    seen = {}
    for cls in ("ocean", "swamp"):
        optics = _optics(ground, cls, N)
        seen[cls] = _gap(_draw(ground, 2.0, _crowns(top), optics), _draw(ground, 2.0, None, optics))
    assert (seen["swamp"] < seen["ocean"]).all()


def test_pixels_without_a_crown_keep_their_bed_carpet_and_water():
    ground = _ground(N)
    carpet = ground.palette["carpet"]
    assert carpet["strength"] > 0
    ground.carpet = (np.full((1, N), 255, np.uint8), np.full((1, N), BED_M + 0.5, np.float16))
    tops = [BED_M + 9.0, BED_M + 1.0, BED_M + 9.0, BED_M + 1.0]
    crowns = _crowns(tops, cover=[1.0, 1.0, 0.0, 0.0])
    drawn, bare = _draw(ground, 2.0, crowns), _draw(ground, 2.0)
    np.testing.assert_array_equal(drawn[0, 2:], bare[0, 2:])
    assert _gap(drawn, bare)[0, 0] > _gap(drawn, bare)[0, 1] > 0, "out of the water, then under"

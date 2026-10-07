"""``mapgen calibrate``'s rules and the palette that wears their colours, on a synthetic store.

docs/map/calibration.md section 31. No install and no data/local: ``tests.support.paint_store``
writes a 64 x 64 store under the test's own directory.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from mapgen.colour import srgb_to_linear
from mapgen.gamedata.level.curves import CurveKey, RichCurve, evaluate
from mapgen.gamedata.level.lighting import convex_hull_xy
from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.palette.painted.derive.palette import calibrated_palette
from mapgen.palette.painted.derive.rules import DESERT_ROCK_LIGHT, entries, untargeted_layers
from mapgen.palette.painted.derive.scene import AreaGrid, scene_from_store
from mapgen.palette.painted.derive.targets import (
    GLOBAL,
    TARGETS_NAME,
    derive,
    stamp_of,
    targets_json,
    vote,
    with_targets,
)
from mapgen.palette.styles import PAINTED_PALETTE, palette_digest
from tests.support.paint_store import (
    CALIBRATION,
    NAMES,
    SAND_RGB,
    VOLUMES,
    area_cells,
    write_store,
)


@pytest.fixture
def store(tmp_path):
    return write_store(tmp_path / "paint")


@pytest.fixture
def scene(store):
    areas = AreaGrid(area_cells(), list(NAMES), list(NAMES))
    return scene_from_store(store, load_paint_meta(store), areas)


def _rows(scene) -> dict:
    return {row.key: row for row in entries(scene, CALIBRATION)}


def _texels(row) -> int:
    return int(row.samples.split()[0])


def test_only_texels_with_seven_tenths_of_the_blend_are_a_layers_own(scene):
    sand = _rows(scene)["layers.Sand_LayerInfo"]
    # West of the mixed band, outside the desert: 7 sample columns by 8 rows.
    assert _texels(sand) == 56
    np.testing.assert_allclose(sand.albedo, srgb_to_linear(SAND_RGB), atol=1e-4)


def test_the_global_target_is_measured_outside_the_area_entries(scene):
    rows = _rows(scene)
    inside = rows["areas[Desert].layers.Sand_LayerInfo"]
    outside = rows["layers.Sand_LayerInfo"]
    assert _texels(inside) == _texels(outside) == 56
    assert np.all(inside.where[1] < 32) and np.all(outside.where[1] > 32)
    assert _texels(rows["layers.Grass_LayerInfo"]) == 7 * 15, "grass has no area entry"


def test_the_highest_priority_volume_wins_and_the_plurality_lights_the_key(scene):
    x = np.array([2.0, 4.5, 12.0, 16.5, 30.0])
    y = np.full(5, 10.0)
    shares = vote((x, y), VOLUMES)
    assert shares == {"Atmosphere_Oasis": 0.4, "Atmosphere_DuneDesert": 0.4, GLOBAL: 0.2}
    found = {t.key: t for t in derive(scene, CALIBRATION).targets}
    sand = found["layers.Sand_LayerInfo"]
    assert sand.light == "Atmosphere_DuneDesert"
    assert sand.shares == {"Atmosphere_DuneDesert": 0.429, "Atmosphere_Oasis": 0.286,
                           GLOBAL: 0.286}  # fmt: skip


def test_the_canopy_leaves_out_keyed_species_and_the_blue_palms(scene):
    canopy = _rows(scene)["canopy"]
    assert canopy.samples == "30 trees; SM_GreenTree_01 100%"
    palms = _rows(scene)["crowns.blue_palm"]
    assert palms.samples.startswith("30 trees; BluePalm_01")


def test_the_desert_family_is_the_sand_rock_under_the_dune_light(scene):
    rows = _rows(scene)
    for key in ("families.desert", "areas[Desert].rock"):
        assert rows[key].kind == "sand-rock texture x vector"
        assert rows[key].light == DESERT_ROCK_LIGHT
        np.testing.assert_allclose(rows[key].albedo, np.multiply([0.491, 0.392, 0.287],
                                                                 [0.594, 0.339, 0.328]))  # fmt: skip
    found = {t.key: t for t in derive(scene, CALIBRATION).targets}
    assert found["families.desert"].light == DESERT_ROCK_LIGHT


def test_water_comes_back_underivable_with_its_reason(scene):
    rows = _rows(scene)
    for key in ("meshes.coral_seabed", "areas[Grass].water"):
        assert rows[key].albedo is None
        assert "water" in (rows[key].error or "")


def test_a_key_no_rule_knows_is_answered_not_raised(scene):
    assert _rows(scene)["meshes.kelp"].error == "no rule for mesh 'kelp'"


def test_an_untargeted_layer_falls_back_to_its_paint_table(scene):
    (soil,) = untargeted_layers(scene, CALIBRATION)
    assert soil.key == "layers.Soil_LayerInfo"
    assert soil.kind == "paint table"
    np.testing.assert_allclose(soil.albedo, [0.12, 0.09, 0.07])


def test_derived_colours_land_on_their_keys_and_a_wrong_key_is_refused():
    palette = copy.deepcopy(PAINTED_PALETTE)
    cal = palette["calibration"]
    cal["derived_keys"] = ["layers.Sand_LayerInfo", "derived.WetSand_LayerInfo", "canopy",
                           "families.desert", "areas[RedJungle,RedBambooFields].rock",
                           "layers.Soil_LayerInfo"]  # fmt: skip
    hexes = {key: "#123456" for key in cal["derived_keys"][:-1]}
    merged, applied = with_targets(palette, hexes)
    out = merged["calibration"]
    assert applied == cal["derived_keys"][:-1]
    assert out["layers"]["Sand_LayerInfo"] == out["layers"]["WetSand_LayerInfo"] == "#123456"
    assert out["canopy"] == out["families"]["desert"] == "#123456"
    assert "Soil_LayerInfo" not in out["layers"], "no colour, no target"
    jungle = next(e for e in out["areas"] if e["areas"][0] == "Area_RedJungle")
    assert jungle["rock"] == "#123456" and jungle["layers"]["Cliff_LayerInfo"] == "#877e6e"
    assert PAINTED_PALETTE["calibration"]["layers"]["Sand_LayerInfo"] == "#d5cbb6"
    cal["derived_keys"] = ["areas[Nowhere].rock"]
    with pytest.raises(ValueError, match="Nowhere"):
        with_targets(palette, {})


def test_every_derived_key_of_the_shipped_palette_is_a_calibration_key():
    keys = PAINTED_PALETTE["calibration"]["derived_keys"]
    merged, applied = with_targets(PAINTED_PALETTE, {key: "#000000" for key in keys})
    assert applied == keys
    assert merged["calibration"]["rock"] == "#000000"


def _biome() -> tuple[dict, list[str]]:
    return {"width": 16, "area": area_cells(), "assets_by_index": list(NAMES)}, list(NAMES)


def _palette() -> dict:
    palette = copy.deepcopy(PAINTED_PALETTE)
    palette["calibration"] = copy.deepcopy(CALIBRATION)
    palette["calibration"]["derived_keys"] = ["layers.Grass_LayerInfo", "canopy"]
    return palette


def test_a_store_without_daylight_keeps_every_screenshot_target(tmp_path):
    store = write_store(tmp_path / "old", daylight=False)
    palette = _palette()
    out, digest, block = calibrated_palette(palette, "d", store, _biome(), None)
    assert out is palette and digest == "d" and block["source"] == "screenshot"


def test_the_file_is_read_when_its_stamp_holds_and_derived_again_when_not(store):
    palette = _palette()
    derived, digest, block = calibrated_palette(palette, "d", store, _biome(), None)
    assert block["source"] == "derived in this run" and digest == palette_digest(derived)
    assert set(block["applied"]) == {"layers.Grass_LayerInfo", "canopy"}
    assert derived["calibration"]["canopy"] != CALIBRATION["canopy"]
    areas = AreaGrid(area_cells(), list(NAMES), list(NAMES))
    meta = load_paint_meta(store)
    stamp = stamp_of(meta["digest"], areas.digest(), palette["calibration"])
    body = targets_json(derive(scene_from_store(store, meta, areas), palette["calibration"]),
                        stamp, 1)  # fmt: skip
    body["targets"]["canopy"]["hex"] = "#010203"
    (store / TARGETS_NAME).write_text(json.dumps(body), encoding="utf-8")
    from_file, _, block = calibrated_palette(palette, "d", store, _biome(), None)
    assert block["source"] == TARGETS_NAME and from_file["calibration"]["canopy"] == "#010203"
    body["stamp"]["paint_digest"] = "another store"
    (store / TARGETS_NAME).write_text(json.dumps(body), encoding="utf-8")
    again, _, block = calibrated_palette(palette, "d", store, _biome(), None)
    assert block["source"] == "derived in this run" and again == derived


def test_the_stamp_ignores_which_keys_wear_the_derived_colours():
    cal = copy.deepcopy(CALIBRATION)
    before = stamp_of("s", "a", cal)
    cal["derived_keys"] = ["canopy"]
    assert stamp_of("s", "a", cal) == before
    cal["layers"] = {}
    assert stamp_of("s", "a", cal) != before


def test_a_curve_holds_its_ends_and_steps_lines_and_bends_between_keys():
    flat = RichCurve((), 3.0)
    assert evaluate(flat, 5.0) == 3.0
    keys = (CurveKey(1, 0.0, 1.0, 0.0, 0.0), CurveKey(0, 6.0, 2.0, 0.0, 0.0),
            CurveKey(2, 12.0, 4.0, 0.0, 0.0), CurveKey(2, 18.0, 0.0, 0.0, 0.0))  # fmt: skip
    curve = RichCurve(keys, None)
    assert evaluate(curve, -1.0) == 1.0 and evaluate(curve, 30.0) == 0.0
    assert evaluate(curve, 3.0) == 1.0, "constant"
    assert evaluate(curve, 9.0) == pytest.approx(3.0), "linear"
    assert evaluate(curve, 15.0) == pytest.approx(2.0), "flat tangents bend through the middle"


def test_the_brush_hull_runs_counter_clockwise():
    hull = convex_hull_xy([(0.0, 0.0), (2.0, 0.0), (1.0, 1.0), (2.0, 2.0), (0.0, 2.0)])
    assert hull == [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]


def test_the_command_refuses_a_missing_store_and_one_without_daylight(
    tmp_path, monkeypatch, capsys
):
    from mapgen.commands import calibrate

    monkeypatch.setattr("sys.argv", ["calibrate", "--paint-dir", str(tmp_path / "none")])
    assert calibrate.main() == calibrate.NO_STORE
    old = write_store(tmp_path / "old", daylight=False)
    monkeypatch.setattr("sys.argv", ["calibrate", "--paint-dir", str(old)])
    assert calibrate.main() == calibrate.NO_DAYLIGHT
    assert "re-run" in capsys.readouterr().out
    assert not (old / TARGETS_NAME).exists()

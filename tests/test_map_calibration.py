"""Colour calibration of the game-painted style: tone, targets, layer transfer, the bake input.

docs/spatial-and-map.md section 28. Synthetic fixtures: no install, no paint store.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.gamedata.paint import LAYERS  # noqa: E402
from mapgen.palette.painted import (  # noqa: E402
    GroundBake,
    display_to_ground,
    ground_albedo,
    layer_transfer,
    linear_from_oklab,
    linear_to_srgb,
    oklab,
    srgb_to_linear,
    tone,
    transfer_op,
)
from mapgen.palette.styles import PAINTED_PALETTE  # noqa: E402
from satisfactory_mcp.core.gameassets import versions  # noqa: E402

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _hex(colour: str) -> np.ndarray:
    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], np.float32)


def _flat_forward(p: dict, lab: np.ndarray) -> np.ndarray:
    """``painted_colours`` on flat dry ground at mid altitude: OKLab ground to display sRGB."""
    lab = np.array(lab, np.float32)
    lab[1:] *= p["chroma_gain"]
    lab[0] += p["altitude_lift"] * 0.5
    unit = lambda c: np.asarray(c, np.float32) / (np.asarray(c, np.float32) @ LUMA)  # noqa: E731
    light = p["ambient"] * unit(p["sky"]) + (1 - p["ambient"]) * unit(p["sun"])
    out = linear_from_oklab(lab) * light * p["exposure"] * p["tone"]["gain"]
    y = float(out @ LUMA)
    out = out * tone(y, p["tone"]["knee"], p["tone"]["white"]) / y
    return linear_to_srgb(out)


def test_the_tone_is_identity_below_the_knee_and_takes_white_to_one():
    y = np.linspace(0.0, 1.6, 321, dtype=np.float32)
    out = tone(y, 0.6, 1.6)
    below = y <= 0.6
    np.testing.assert_allclose(out[below], y[below])
    assert out[-1] == pytest.approx(1.0, abs=1e-5)
    assert np.all(np.diff(out) > 0), "monotonic, so it has an inverse"
    assert float(tone(np.float32(0.6001), 0.6, 1.6)) == pytest.approx(0.6001, abs=1e-4)


@pytest.mark.parametrize("target", ["#d5cbb6", "#d07756", "#558653", "#ae8271", "#99868e"])
def test_a_display_target_survives_the_trip_back_through_light_and_tone(target):
    lab = display_to_ground(PAINTED_PALETTE, target)
    np.testing.assert_allclose(_flat_forward(PAINTED_PALETTE, lab), _hex(target), atol=1.5)


def test_the_transfer_puts_a_pure_layer_on_its_target_and_mixes_by_weight():
    source = np.array([0.4, 0.3, 0.2], np.float32)
    target_lab = oklab(np.array([0.2, 0.35, 0.25], np.float32))
    albedo = np.tile(source, (1, 4, 1))
    weights = {
        "Sand": np.array([[255, 128, 0, 0]], np.uint8),
        "Grass": np.array([[0, 127, 255, 0]], np.uint8),
    }
    ops = {"Sand": transfer_op(oklab(source), target_lab)}
    out = layer_transfer(albedo, weights, ops)
    np.testing.assert_allclose(oklab(out[0, 0]), target_lab, atol=2e-3)
    halfway = (oklab(source) + target_lab) / 2
    assert np.abs(oklab(out[0, 1]) - halfway).max() < 0.01
    np.testing.assert_allclose(out[0, 2], source, atol=1e-5)
    np.testing.assert_allclose(out[0, 3], source, atol=1e-5)


def test_a_transfer_op_turns_hue_and_scales_chroma():
    d_l, m = transfer_op([0.5, 0.1, 0.0], [0.6, 0.0, 0.05])
    assert d_l == pytest.approx(0.1)
    np.testing.assert_allclose(m @ np.array([0.1, 0.0], np.float32), [0.0, 0.05], atol=1e-6)


def test_the_bake_replaces_the_paint_inside_and_feathers_at_its_edge():
    paint = np.full((40, 40, 3), 0.1, np.float32)
    have = np.zeros((40, 40), bool)
    have[:, :20] = True
    bake = GroundBake(np.full((40, 40, 3), 0.5, np.float32), have)
    out, covered, weight = ground_albedo(paint, np.zeros((40, 40), bool), bake, 2.0)
    assert out[20, 2, 0] == pytest.approx(0.5) and out[20, 35, 0] == pytest.approx(0.1)
    assert 0.0 <= weight[20, 19] < 0.5 and np.all(weight[:, 20:] == 0)
    assert np.array_equal(covered, have)


def test_without_a_bake_the_paint_mix_is_the_source():
    paint = np.full((4, 4, 3), 0.1, np.float32)
    have = np.ones((4, 4), bool)
    out, covered, weight = ground_albedo(paint, have, None, 2.0)
    assert out is paint and covered is have and weight is None


def test_a_bake_from_srgb_is_linear_and_drops_black_holes():
    rgb = np.array([[[255, 255, 255], [0, 0, 0], [0, 0, 2]]], np.uint8)
    bake = GroundBake.from_srgb(rgb, np.ones((1, 3), bool))
    np.testing.assert_allclose(bake.linear[0, 0], 1.0)
    assert bake.have.tolist() == [[True, False, False]]


def test_the_calibration_names_real_layers_and_the_style_was_bumped():
    cal = PAINTED_PALETTE["calibration"]
    assert set(cal["layers"]) <= set(LAYERS)
    assert {"coral", "shell", "coral_seabed"} <= set(cal["meshes"])
    assert "shoulder" not in PAINTED_PALETTE, "the tone replaced it"
    assert versions.STYLES["satellite-painted"]["version"] >= 2
    seabed = srgb_to_linear(_hex(cal["meshes"]["coral_seabed"]))
    assert seabed[2] > seabed[0], "the seabed coral stays blue"

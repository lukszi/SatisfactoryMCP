"""The derive gate: lightness, chroma and hue each have to agree. docs/map/calibration.md
section 31, "The derive gate"."""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.palette.painted.derive.camera import lab_of_hex
from mapgen.palette.painted.derive.gate import (
    GATE_CHROMA_FLOOR,
    GATE_CHROMATIC_SHARE,
    GATE_DELTA_E,
    gate,
    gate_hex,
)


@pytest.mark.parametrize(
    "target", ["#85816c", "#7e7868", "#877e6e"], ids=["default", "grass fields", "red jungle"]
)
def test_the_tan_v8_rock_fails_every_rock_target_though_it_lies_within_de_5(target):
    verdict = gate_hex("#897758", target)
    assert verdict.delta_e < GATE_DELTA_E
    assert not verdict.passed and verdict.failures[0].startswith("chroma and hue")


def test_a_lightness_step_alone_passes_and_past_de_5_fails_on_de_only():
    target = lab_of_hex("#85816c")
    near = gate(target - [0.04, 0.0, 0.0], target)
    assert near.passed and near.delta_l == pytest.approx(-4.0)
    far = gate(target - [0.06, 0.0, 0.0], target)
    assert far.failures == ("dE 6.00 > 5",)


def test_a_hue_turn_at_the_same_lightness_and_chroma_fails_within_de_5():
    target = lab_of_hex("#877e6e")
    chroma = float(np.hypot(target[1], target[2]))
    angle = np.arctan2(target[2], target[1]) + np.radians(60.0)
    turned = np.array([target[0], chroma * np.cos(angle), chroma * np.sin(angle)])
    verdict = gate(turned, target)
    assert verdict.delta_e < GATE_DELTA_E
    assert abs(verdict.delta_c) < 1e-9 and verdict.delta_h == pytest.approx(100 * chroma)
    assert not verdict.passed


def test_a_grey_target_keeps_the_floor_s_allowance():
    grey = np.array([0.6, 0.0, 0.0])
    allowance = 100 * GATE_CHROMATIC_SHARE * GATE_CHROMA_FLOOR
    assert gate(grey + [0.0, 0.0, 0.009], grey).passed
    tinted = gate(grey + [0.0, 0.0, 0.02], grey)
    assert tinted.allowance == pytest.approx(allowance) and not tinted.passed

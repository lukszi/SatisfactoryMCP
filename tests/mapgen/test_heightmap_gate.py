"""The heightmap's node-table gate fails a field outside it, also one no node measured."""

from __future__ import annotations

from mapgen.terrain.heightfield.validate import VALIDATION_TRIM_RMS_MAX_M, field_gate_failure


def test_a_field_no_node_measured_fails_rather_than_raising_a_key_error():
    assert field_gate_failure({"n": 0, "coverage": 0.0})


def test_a_field_past_the_trimmed_rms_gate_fails():
    whole = {"n": 9, "coverage": 1.0, "trim90_rms_m": VALIDATION_TRIM_RMS_MAX_M + 1}
    assert "trimmed RMS" in (field_gate_failure(whole) or "")


def test_a_field_inside_the_gate_passes():
    whole = {"n": 9, "coverage": 1.0, "trim90_rms_m": VALIDATION_TRIM_RMS_MAX_M / 2}
    assert field_gate_failure(whole) is None

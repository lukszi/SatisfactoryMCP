"""The heightmap's node-table gate refuses with its exit code, also when no node was measured."""

from __future__ import annotations

import pytest

from mapgen.commands.heightmap import field_gate
from mapgen.common import Refusal
from mapgen.terrain.heightfield.validate import VALIDATION_TRIM_RMS_MAX_M


def test_a_field_no_node_measured_is_refused_not_a_key_error():
    with pytest.raises(Refusal) as refused:
        field_gate({"n": 0, "coverage": 0.0})
    assert refused.value.code == 6


def test_a_field_past_the_trimmed_rms_gate_is_refused():
    with pytest.raises(Refusal) as refused:
        field_gate({"n": 9, "coverage": 1.0, "trim90_rms_m": VALIDATION_TRIM_RMS_MAX_M + 1})
    assert refused.value.code == 6


def test_a_field_inside_the_gate_passes():
    field_gate({"n": 9, "coverage": 1.0, "trim90_rms_m": VALIDATION_TRIM_RMS_MAX_M / 2})

"""Schema 25: power switches carry their state and Power Storage its charge (§6.12a)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from satisfactory_mcp.core.saveio.extract.walk import _Object, _ProjectionWalk


class _Header:
    position = (100.0, 200.0, 300.0)
    rotation = (0.0, 0.0, 0.0, 1.0)


def _filed(cls: str, properties: dict[str, Any]) -> dict[str, Any]:
    """The record the walk files for one actor, and the list it lands in."""
    walk = _ProjectionWalk(out=cast("Any", {"facilities": []}))
    seen = _Object(cls, f"Persistent_Level:PersistentLevel.{cls}_1", _Header(), None, properties)  # type: ignore[arg-type]
    record = walk._buildable_record(seen)
    walk._file_record(seen, record, held=False)
    (filed,) = walk.out["facilities"]
    return cast("dict[str, Any]", filed)


@pytest.mark.parametrize("cls", ["Build_PowerSwitch_C", "Build_PriorityPowerSwitch_C"])
def test_a_switch_is_on_only_while_the_save_says_so(cls):
    assert _filed(cls, {"mIsSwitchOn": 1})["switch_on"] is True
    assert _filed(cls, {})["switch_on"] is False, "the property is written only while on"


def test_a_switch_lists_who_is_wired_to_each_of_its_connections():
    walk = _ProjectionWalk(out=cast("Any", {}))
    switch: dict[str, Any] = {"cls": "Build_PowerSwitch_C", "switch_on": False}
    walk.switches["Build_PowerSwitch_C_1"] = cast("Any", switch)
    walk.wire_ends = {
        "w1": [
            ("Build_PowerPoleMk1_C_1", "PowerConnection"),
            ("Build_PowerSwitch_C_1", "PowerConnection1"),
        ],
        "w2": [
            ("Build_PowerSwitch_C_1", "PowerConnection2"),
            ("Build_PowerStorageMk1_C_2", "PowerConnection"),
        ],
        "w3": [
            ("Build_PowerStorageMk1_C_1", "PowerConnection"),
            ("Build_PowerSwitch_C_1", "PowerConnection2"),
        ],
        "w4": [("Build_PowerPoleMk1_C_1", "PowerConnection")],
    }
    walk._attach_switch_sides()
    assert switch["switch_sides"] == [
        ["Build_PowerPoleMk1_C_1"],
        ["Build_PowerStorageMk1_C_1", "Build_PowerStorageMk1_C_2"],
    ]


def test_a_battery_carries_its_charge_and_an_empty_one_reads_zero():
    assert _filed("Build_PowerStorageMk1_C", {"mPowerStore": 87.25})["stored_mwh"] == 87.25
    assert _filed("Build_PowerStorageMk1_C", {})["stored_mwh"] == 0.0


def test_other_facilities_carry_neither_field():
    record = _filed("Build_RadarTower_C", {"mIsSwitchOn": 1, "mPowerStore": 5.0})
    assert "switch_on" not in record and "stored_mwh" not in record

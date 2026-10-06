"""The page-focus file: validated, heartbeat-stamped, and read as None when it is not there.

docs/planner_slice_contract.md §9 is what these pin. No game data is needed.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.planning import focus


@pytest.fixture
def ui(user_data):
    return user_data / "ui"


def test_a_written_focus_reads_back_with_every_field_and_a_heartbeat(ui):
    written = focus.write("W", {"view": "planner", "plan": "a1b2c3d4", "rev": 14})
    assert written == focus.read("W")
    assert set(written) == {
        "schema",
        "heartbeat",
        "view",
        "dash",
        "plan",
        "rev",
        "tab",
        "selection",
        "follow",
        "sav",
    }
    assert written["follow"] == "follow" and written["selection"] is None
    assert written["heartbeat"] > 0
    assert (ui / "W.json").is_file()


def test_the_world_id_is_sanitised_into_a_filename(ui):
    focus.write("../evil world", {"view": "map"})
    assert [p.name for p in ui.iterdir()] == ["evilworld.json"]


@pytest.mark.parametrize(
    "bad",
    [
        {"view": "kitchen"},
        {"view": "map", "follow": "sometimes"},
        {"view": "map", "rev": 0},
        {"view": "map", "rev": True},
        {"view": "map", "selection": "Blender"},
        {"view": "map", "selection": {"kind": 1}},
        {"view": "map", "tab": 3},
    ],
)
def test_a_malformed_focus_is_refused_and_nothing_is_written(ui, bad):
    with pytest.raises(focus.InvalidFocus):
        focus.write("W", bad)
    assert focus.read("W") is None


def test_an_absent_or_unreadable_file_reads_as_none(ui):
    assert focus.read("W") is None
    ui.mkdir(exist_ok=True)
    (ui / "W.json").write_text("{torn", encoding="utf-8")
    assert focus.read("W") is None
    (ui / "W.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert focus.read("W") is None


def test_open_means_a_heartbeat_inside_the_window():
    assert focus.is_open({"heartbeat": 1000.0}, now=1000.0 + focus.OPEN_WITHIN_S)
    assert not focus.is_open({"heartbeat": 1000.0}, now=1001.0 + focus.OPEN_WITHIN_S)
    assert not focus.is_open(None)
    assert not focus.is_open({"heartbeat": "soon"})

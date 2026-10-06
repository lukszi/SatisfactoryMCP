"""The two normalisation guards reach a reader.

``GameData.warnings`` and ``projection["warnings"]`` both existed so that a game patch which
breaks normalisation is visible instead of silent, and neither had a consumer: the first
reached a bare ``len()`` and the second reached nothing at all. A guard nobody reads publishes
a smaller world and calls it the world.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gamedata.model import GameData
from satisfactory_mcp.core.saveio.extract import DISMISSED_FACTORY_CLASSES, _unfiled_notes
from satisfactory_mcp.interfaces.mcp.app import integrity_notes


def _data(warnings: list[str]) -> GameData:
    g = GameData.__new__(GameData)
    g.warnings = warnings
    return g


# ----------------------------------------------------------- the two channels reach a reader


def test_a_healthy_boot_says_nothing_at_all():
    assert integrity_notes({"warnings": []}, _data([])) == []


def test_a_projection_with_no_warnings_key_is_not_a_problem():
    assert integrity_notes({}, _data([])) == []


def test_a_save_warning_is_quoted_and_says_the_world_may_be_short():
    notes = integrity_notes({"warnings": ["402 pipe(s) dropped"]}, _data([]))
    assert len(notes) == 1
    assert "402 pipe(s) dropped" in notes[0]
    assert "this save" in notes[0]
    assert "less than is really there" in notes[0]


def test_a_game_data_warning_is_quoted_too_and_is_named_apart():
    notes = integrity_notes({"warnings": []}, _data(["no clearance data"]))
    assert len(notes) == 1
    assert "no clearance data" in notes[0]
    assert "the game's own data" in notes[0]


def test_both_channels_speak_at_once_and_stay_separate():
    notes = integrity_notes({"warnings": ["a"]}, _data(["b"]))
    assert len(notes) == 2


def test_a_flood_is_summarised_rather_than_pasted():
    found = [f"problem {i}" for i in range(30)]
    (note,) = integrity_notes({"warnings": found}, _data([]))
    assert "30 problem(s)" in note
    assert "and 26 more" in note
    assert "problem 29" not in note


# ------------------------------------------------------------------ the unread-class census


def test_a_world_of_walls_and_foundations_is_silent():
    """The census's whole risk: without the evidence test this reports 4,300 berry bushes."""
    unfiled = {f"w{i}": "Build_Wall_8x4_01_C" for i in range(4300)}
    unfiled.update({f"b{i}": "BP_BerryBush_C" for i in range(4300)})
    assert _unfiled_notes(unfiled, set()) == []


def test_a_building_that_runs_something_and_is_filed_nowhere_is_named():
    (note,) = _unfiled_notes({"x.Build_NewThing_C_1": "Build_NewThing_C"}, {"x.Build_NewThing_C_1"})
    assert "1x Build_NewThing_C" in note
    assert "missing from machines, extractors and generators" in note
    assert "DISMISSED_FACTORY_CLASSES" in note


def test_the_same_class_twice_is_one_line_with_a_count():
    (note,) = _unfiled_notes({"a": "Build_NewThing_C", "b": "Build_NewThing_C"}, {"a", "b"})
    assert "2x Build_NewThing_C" in note
    assert note.startswith("1 building class(es)")


@pytest.mark.parametrize("cls", sorted(DISMISSED_FACTORY_CLASSES))
def test_every_dismissed_class_stays_quiet(cls):
    """Each is a real building carrying the mark, kept out by hand. Editing the list is how
    a future one is dismissed, and is why the list is the census's only maintenance."""
    assert _unfiled_notes({"a": cls}, {"a"}) == []


def test_a_dismissed_class_does_not_silence_its_neighbours():
    (note,) = _unfiled_notes({"a": "Build_TradingPost_C", "b": "Build_NewThing_C"}, {"a", "b"})
    assert "Build_TradingPost_C" not in note
    assert "Build_NewThing_C" in note


def test_a_building_with_no_evidence_on_it_is_not_a_finding():
    """Filed nowhere is ordinary -- it is every wall in the world. The mark is the claim."""
    assert _unfiled_notes({"a": "Build_NewThing_C"}, set()) == []


# ------------------------------------------------------------------- and quiet on this world


@pytest.mark.integration
def test_the_real_world_boots_quiet(live):
    """Both channels silent on the save this machine actually has.

    The whole design rests on it: a note that appears on a healthy world is a note nobody
    reads by the third time they see it, and then the guard is decorative again.
    """
    assert integrity_notes(live.projection, live.game) == []

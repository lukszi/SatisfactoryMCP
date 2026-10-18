"""The game mode as the server reads it, and the parts rounding (save-projection.md §6.18)."""

from __future__ import annotations

from typing import Any, cast

from satisfactory_mcp.core.saveio.extract import gamemode as extracted
from satisfactory_mcp.domain.world import gamemode

TEST_SAVE: Any = {
    "node_randomization": "strict",
    "node_purity": "all_normal",
    "seed": 160943561,
    "energy_cost": 0.75,
    "parts_cost": 0.75,
    "space_parts_cost": 0.75,
}


def test_the_server_default_is_the_extractors():
    assert gamemode.DEFAULT == extracted.DEFAULT_GAME_MODE


def test_a_projection_without_the_record_reads_as_the_default():
    assert gamemode.mode_of(None) == gamemode.DEFAULT
    assert gamemode.mode_of(cast("Any", {})) == gamemode.DEFAULT
    assert gamemode.energy_cost(cast("Any", {"game_mode": TEST_SAVE})) == 0.75


def test_a_default_game_says_nothing_and_a_changed_one_names_what_changed():
    assert gamemode.summary(gamemode.DEFAULT) is None
    assert gamemode.summary(TEST_SAVE) == (
        "game mode: nodes random, purity average · power x0.75 · parts x0.75"
        " · space elevator parts x0.75"
    )
    assert gamemode.summary({**gamemode.DEFAULT, "parts_cost": 2.0}) == "game mode: parts x2"


def test_a_part_rounds_to_a_whole_one_and_never_to_none():
    assert gamemode.scaled_part(8, 0.75) == 6
    assert gamemode.scaled_part(2, 0.75) == 2, "1.5 rounds up"
    assert gamemode.scaled_part(1, 0.25) == 1
    assert gamemode.scaled_part(5, 1.0) == 5

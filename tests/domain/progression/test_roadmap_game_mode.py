"""The roadmap's craft checks read the world's recipes: a game mode's parts multiplier included."""

from __future__ import annotations

from typing import Any

import pytest

from satisfactory_mcp.domain.progression.roadmap.bill import BillPart, Makers
from satisfactory_mcp.domain.progression.roadmap.gate import _inputs
from satisfactory_mcp.domain.progression.roadmap.steps import _inputs_held
from satisfactory_mcp.domain.world.state import WorldState

PLATE = "Desc_IronPlate_C"
INGOT = "Desc_IronIngot_C"
CHEAP: Any = {
    "node_randomization": "none",
    "node_purity": "no_change",
    "seed": 0,
    "energy_cost": 1.0,
    "parts_cost": 0.75,
    "space_parts_cost": 1.0,
}


@pytest.fixture
def cheap(projection, game) -> WorldState:
    return WorldState(projection={**projection, "game_mode": CHEAP}, game=game)


def _ingot_per_plate(st: WorldState) -> float:
    rows = _inputs(st, Makers(by_factory={}, world={}), BillPart(PLATE, owed=100, held=0), {})
    return next(row["per_unit"] for row in rows if row["item"] == INGOT)


def test_a_parts_multiplier_reaches_the_gates_inputs(state, cheap):
    assert _ingot_per_plate(state) == pytest.approx(1.5)
    assert _ingot_per_plate(cheap) == pytest.approx(1.0), "2 ingots per 2 plates under x0.75"


def test_a_parts_multiplier_reaches_the_inputs_held_check(state, cheap):
    stock = {INGOT: 120.0}
    assert _inputs_held(state, PLATE, 100, stock) is False, "100 plates take 150 ingots"
    assert _inputs_held(cheap, PLATE, 100, stock) is True, "and 100 under x0.75"

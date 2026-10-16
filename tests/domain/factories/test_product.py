"""main_product: the item a factory's icon shows when nobody chose one."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.product import main_product
from satisfactory_mcp.domain.factories.query import FactoryView, MachineRow, build_view
from tests.support.projections import INSIDE, smelter_feeding_two_rods

pytestmark = pytest.mark.integration


def _row(recipe_id: str, makes: dict[str, float]) -> MachineRow:
    return MachineRow(
        "m", "Build_X_C", "", 1.0, False, (0.0, 0.0, 0.0), makes=makes, recipe_id=recipe_id
    )


def _view(rows: list[MachineRow], nets: dict[str, float]) -> FactoryView:
    view = FactoryView(name="t", machines=rows)
    view.flows = {k: {"produced": max(v, 0.0), "consumed": max(-v, 0.0)} for k, v in nets.items()}
    return view


def test_the_surplus_a_recipe_makes_is_the_product(game):
    projection = smelter_feeding_two_rods()
    view = build_view("t", list(INSIDE), build_graph(projection), game, projection)
    assert main_product(view, game) == "Desc_IronRod_C"


def test_the_most_processed_surplus_wins_over_the_largest(game):
    rows = [
        _row("Recipe_IronPlate_C", {"Iron Plate": 60.0}),
        _row("Recipe_Screw_C", {"Screws": 5.0}),
    ]
    view = _view(rows, {"Iron Plate": 60.0, "Screws": 5.0})
    plate, screw = game.items["Desc_IronPlate_C"], game.items["Desc_IronScrew_C"]
    expected = "Desc_IronPlate_C" if plate.sink_points > screw.sink_points else "Desc_IronScrew_C"
    assert main_product(view, game) == expected


def test_an_extraction_only_set_shows_its_resource(game):
    view = _view(
        [_row("", {"Iron Ore": 120.0}), _row("", {"Copper Ore": 60.0})],
        {"Iron Ore": 120.0, "Copper Ore": 60.0},
    )
    assert main_product(view, game) == "Desc_OreIron_C"


def test_a_tie_or_nothing_made_is_no_product(game):
    assert main_product(_view([], {}), game) is None
    tie = _view(
        [_row("", {"Iron Ore": 60.0}), _row("", {"Copper Ore": 60.0})],
        {"Iron Ore": 60.0, "Copper Ore": 60.0},
    )
    assert main_product(tie, game) is None

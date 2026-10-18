"""A game mode's multipliers in the planner: draw scaled, generation not, build parts rounded.

What the game scales is in docs/save-projection.md §6.18.
"""

from __future__ import annotations

from typing import Any

import pytest

from satisfactory_mcp.domain.planning.layout.materials import cost_of
from satisfactory_mcp.domain.planning.solver.model import Scenario
from satisfactory_mcp.domain.planning.solver.processes import (
    extractor_processes,
    generator_processes,
    recipe_processes,
)


def _mw(processes, pid):
    return next(p for p in processes if p.pid == pid).mw


def test_the_energy_factor_scales_every_draw(game):
    plain = Scenario(game=game, recipes=["Recipe_IronPlate_C"])
    cheap = Scenario(game=game, recipes=["Recipe_IronPlate_C"], energy_cost=0.75)
    pid = "r:Recipe_IronPlate_C"
    assert _mw(recipe_processes(cheap), pid) == pytest.approx(
        _mw(recipe_processes(plain), pid) * 0.75
    )

    nodes = {("Build_MinerMk1_C", "Desc_OreIron_C", "normal"): 1}
    miners = extractor_processes(Scenario(game=game, recipes=[], extractor_nodes=nodes))
    scaled = extractor_processes(
        Scenario(game=game, recipes=[], extractor_nodes=nodes, energy_cost=0.75)
    )
    assert scaled[0].mw == pytest.approx(miners[0].mw * 0.75)
    assert scaled[0].mw_at_full == pytest.approx(miners[0].mw_at_full * 0.75)


def test_generation_is_never_scaled(game):
    plain = {p.pid: p.mw for p in generator_processes(Scenario(game=game, recipes=[]))}
    cheap = {
        p.pid: p.mw for p in generator_processes(Scenario(game=game, recipes=[], energy_cost=0.5))
    }
    assert plain and plain == cheap


def test_build_parts_round_per_building_then_multiply(game):
    assert cost_of(game, "Build_ConstructorMk1_C", 3).parts == {
        "Desc_IronPlateReinforced_C": 6.0,
        "Desc_Cable_C": 24.0,
    }
    # 2 x 0.75 = 1.5 rounds to 2 and 8 x 0.75 = 6, each per constructor, before the count.
    assert cost_of(game, "Build_ConstructorMk1_C", 3, 0.75).parts == {
        "Desc_IronPlateReinforced_C": 6.0,
        "Desc_Cable_C": 18.0,
    }


def test_the_parts_factor_scales_what_a_machine_consumes(game):
    pid = "r:Recipe_IronPlate_C"
    plain = Scenario(game=game, recipes=["Recipe_IronPlate_C"])
    cheap = Scenario(game=game, recipes=["Recipe_IronPlate_C"], parts_cost=0.75)
    rates = next(p for p in recipe_processes(plain) if p.pid == pid).rates
    scaled = next(p for p in recipe_processes(cheap) if p.pid == pid).rates
    assert rates == {"Desc_IronIngot_C": -30.0, "Desc_IronPlate_C": 20.0}
    assert scaled == {"Desc_IronIngot_C": -20.0, "Desc_IronPlate_C": 20.0}
    assert plain.game is game


def test_the_power_ledger_scales_draw_and_never_generation(game):
    from satisfactory_mcp.domain.power.report import PowerLedger

    groups: Any = {
        "generators": [{"instance": "Build_GeneratorCoal_C_1", "cls": "Build_GeneratorCoal_C"}],
        "machines": [
            {
                "instance": "Build_ConstructorMk1_C_1",
                "cls": "Build_ConstructorMk1_C",
                "recipe": "Recipe_IronPlate_C",
                "uptime": {"window_s": 100.0, "produce_s": 50.0},
            }
        ],
        "extractors": [{"instance": "Build_MinerMk1_C_1", "cls": "Build_MinerMk1_C"}],
    }
    plain = PowerLedger(projection=groups, game=game).power_report()
    cheap = PowerLedger(projection=groups, game=game, energy_cost=0.75).power_report()
    assert plain["draw_mw"] == pytest.approx(4.0 + 5.0)
    assert cheap["draw_mw"] == pytest.approx(plain["draw_mw"] * 0.75)
    assert cheap["measured_draw_mw"] == pytest.approx(plain["measured_draw_mw"] * 0.75)
    assert cheap["generation_mw"] == plain["generation_mw"] == pytest.approx(75.0)

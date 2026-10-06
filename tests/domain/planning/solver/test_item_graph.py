"""The item-flow graph helpers in ``planning.solver.graph``."""

from __future__ import annotations

from satisfactory_mcp.domain.planning.solver.graph import (
    chain_depth,
    chain_depth_of_rates,
    item_cycles,
    strongly_connected,
)
from satisfactory_mcp.domain.planning.solver.model import MW

RECYCLED = [
    {"rates": {"Rubber": -30, "Fuel": -30, "Plastic": 60}},
    {"rates": {"Plastic": -30, "Fuel": -30, "Rubber": 60}},
]


def test_strongly_connected_groups_a_two_cycle_and_leaves_the_rest_alone():
    components = strongly_connected(4, {0: {1}, 1: {0, 2}, 2: {3}})
    assert sorted(sorted(c) for c in components) == [[0, 1], [2], [3]]


def test_chain_depth_of_rates_matches_chain_depth_without_power():
    rate_maps = [
        {"Ore": 60, MW: -5},
        {"Ore": -30, "Ingot": 30, MW: -4},
        {"Ingot": -30, "Plate": 20, MW: -4},
        {MW: 75},
    ]
    nodes = [([], ["Ore"]), (["Ore"], ["Ingot"]), (["Ingot"], ["Plate"]), ([], [])]
    assert chain_depth_of_rates(rate_maps) == chain_depth(nodes) == [0, 1, 2, 0]


def test_item_cycles_names_the_recycled_loop_once():
    assert item_cycles(RECYCLED) == [["Plastic", "Rubber"]]


def test_item_cycles_ignores_a_plain_chain():
    chain = [{"rates": {"Ore": -30, "Ingot": 30}}, {"rates": {"Ingot": -30, "Plate": 20}}]
    assert item_cycles(chain) == []

"""A built factory's flow graph in the plan graph's shape: ids, ranks, ends and measured share."""

from __future__ import annotations

from collections import Counter

import pytest

from satisfactory_mcp.domain.factories import built_graph as bg
from satisfactory_mcp.domain.factories import flowgraph as fg
from satisfactory_mcp.domain.factories.query import FactoryView, MachineRow

SMELT = "Build_SmelterMk1_C"
MAKE = "Build_ConstructorMk1_C"


def _row(instance, building, recipe_id, recipe, clock, makes, uses, share, mw):
    return MachineRow(
        instance=instance,
        building=building,
        recipe=recipe,
        clock=clock,
        paused=False,
        pos=(0.0, 0.0, 0.0),
        makes=makes,
        uses=uses,
        recipe_id=recipe_id,
        share=share,
        mw=mw,
    )


def _group(key, building, recipe, rows, health):
    group = fg.Group(key, building, recipe)
    for row in rows:
        group.machines.append(row.instance)
        group.clocks.append(row.clock)
        for item, rate in row.makes.items():
            group.makes[item] += rate
        for item, rate in row.uses.items():
            group.uses[item] += rate
    group.health = Counter(health)
    return group


@pytest.fixture
def line():
    smelters = [
        _row(
            f"s{i}",
            SMELT,
            "Recipe_IngotIron_C",
            "Iron Ingot",
            1.0,
            {"Iron Ingot": 30.0},
            {"Iron Ore": 30.0},
            share,
            -4.0,
        )
        for i, share in enumerate((1.0, 0.5))
    ]
    plates = [
        _row(
            "c0",
            MAKE,
            "Recipe_IronPlate_C",
            "Iron Plate",
            0.5,
            {"Iron Plate": 10.0},
            {"Iron Ingot": 15.0},
            None,
            -1.5,
        )
    ]
    view = FactoryView(name="t", machines=smelters + plates)
    flow = fg.FlowGraph()
    flow.groups = {
        "s|Iron Ingot": _group(
            "s|Iron Ingot", "Smelter", "Iron Ingot", smelters, {"saturated": 1, "starved": 1}
        ),
        "c|Iron Plate": _group(
            "c|Iron Plate", "Constructor", "Iron Plate", plates, {"unmonitored": 1}
        ),
    }
    flow.edges = [
        fg.FlowEdge("in:Iron Ore", "s|Iron Ingot", "Iron Ore", 60.0),
        fg.FlowEdge("s|Iron Ingot", "c|Iron Plate", "Iron Ingot", 15.0),
        fg.FlowEdge("s|Iron Ingot", fg.STORAGE, "Iron Ingot", 45.0),
        fg.FlowEdge("c|Iron Plate", fg.EXPORT, "Iron Plate", 10.0),
    ]
    return flow, view


def _by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def test_nodes_take_plan_ids_kinds_and_ranks(line, game):
    graph = bg.built_graph(*line, game, "factory")
    nodes = _by_id(graph)
    assert set(nodes) == {
        "in:Desc_OreIron_C",
        "p:Recipe_IngotIron_C",
        "p:Recipe_IronPlate_C",
        "box:Desc_IronIngot_C",
        "ex:Desc_IronPlate_C",
    }
    assert [nodes[i]["rank"] for i in ("in:Desc_OreIron_C", "p:Recipe_IngotIron_C")] == [0, 1]
    assert nodes["p:Recipe_IronPlate_C"]["rank"] == 2
    assert nodes["ex:Desc_IronPlate_C"]["rank"] == nodes["box:Desc_IronIngot_C"]["rank"] == 3
    assert nodes["ex:Desc_IronPlate_C"]["kind"] == "export"
    assert nodes["ex:Desc_IronPlate_C"]["note"] == "leaves the factory"
    assert nodes["box:Desc_IronIngot_C"]["note"] == "to storage"
    assert nodes["in:Desc_OreIron_C"]["per_min"] == 60.0


def test_a_process_carries_clock_power_and_the_measured_share(line, game):
    nodes = _by_id(bg.built_graph(*line, game, "factory"))
    smelt = nodes["p:Recipe_IngotIron_C"]
    assert (smelt["built"], smelt["machines"], smelt["clock"], smelt["mw"]) == (2, 2.0, 100.0, -8.0)
    assert (smelt["productivity"], smelt["monitored"]) == (0.75, 2)
    assert smelt["states"] == {"saturated": 1, "starved": 1}
    plate = nodes["p:Recipe_IronPlate_C"]
    assert (plate["productivity"], plate["monitored"], plate["utilisation"]) == (None, 0, 0.5)
    assert plate["building_icon"] and plate["building_icon"].startswith("Desc_")


def test_edges_carry_item_groups_and_the_totals_add_up(line, game):
    graph = bg.built_graph(*line, game, "cluster")
    edges = {e["id"]: e for e in graph["edges"]}
    edge = edges["p:Recipe_IngotIron_C>p:Recipe_IronPlate_C:Desc_IronIngot_C"]
    assert (edge["item_group"], edge["per_min"], edge["unit"]) == ("metal", 15.0, "items")
    assert graph["mw_draw"] == 9.5 and graph["mw_generated"] == 0.0
    assert {b["building"]: b["count"] for b in graph["buildings"]} == {
        "Smelter": 2,
        "Constructor": 1,
    }


def test_output_no_edge_carries_backs_up_into_a_leftover(line, game):
    flow, view = line
    flow.edges = [e for e in flow.edges if e.target != fg.EXPORT]
    nodes = _by_id(bg.built_graph(flow, view, game, "factory"))
    left = nodes["left:Desc_IronPlate_C"]
    assert (left["kind"], left["per_min"], left["note"]) == ("leftover", 10.0, "backs up")


def test_a_loop_shares_one_column():
    ranks = bg._ranks(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d")])
    assert ranks == {"a": 1, "b": 2, "c": 2, "d": 3}

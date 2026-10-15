"""The production graph: flows that add up, ids that hold across re-solves, the item groups.

docs/planning.md §8.5o and docs/planner_vision.md §4.2 are what these pin.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from satisfactory_mcp.domain.planning.readout.flows import decompose
from satisfactory_mcp.domain.planning.readout.item_groups import GROUPS, ITEM_GROUPS, item_group
from satisfactory_mcp.domain.planning.readout.summary import solve_summary
from satisfactory_mcp.domain.planning.solver.model import MW
from tests.support.reference_world import FIVE_RIP_ARGS, RIP

#: A power plant whose Polymer Resin byproduct is sunk.
SUNK = {"objective": "max_mw", "exports": ["MW", "Plastic"], "export_minimums": {"Plastic": 20}}
#: The same plant asked for only 10 resin a minute: the rest is left over.
LEFT = {
    "objective": "max_mw",
    "exports": ["MW", "Plastic", "Polymer Resin"],
    "export_minimums": {"Plastic": 20, "Polymer Resin": 10},
}


def _graph(game, state, args):
    body = solve_summary(game, state, dict(args))
    assert body["feasible"], body["cause"]
    return body, body["graph"]


def _sums(edges, end):
    out: dict[tuple[str, str], float] = defaultdict(float)
    for e in edges:
        out[(e[end], e["item_id"])] += e["per_min"]
    return out


# ------------------------------------------------------------------ decomposition


def test_the_decomposition_uses_up_both_sides_in_order():
    supply = [("a", 30.0), ("b", 10.0)]
    demand = [("x", 5.0), ("y", 25.0), ("z", 10.0)]
    flows = decompose(supply, demand)
    assert flows == [("a", "x", 5.0), ("a", "y", 25.0), ("b", "z", 10.0)]
    assert len(flows) <= len(supply) + len(demand) - 1
    assert decompose(supply, demand) == flows


def test_the_decomposition_splits_a_supplier_over_users_and_skips_empty_ends():
    flows = decompose([("a", 10.0), ("b", 10.0)], [("x", 0.0), ("y", 15.0), ("z", 5.0)])
    assert flows == [("a", "y", 10.0), ("b", "y", 5.0), ("b", "z", 5.0)]


# ------------------------------------------------------------------ item groups


def test_every_item_in_the_group_table_is_a_game_item(game):
    assert set(ITEM_GROUPS.values()) <= set(GROUPS)
    assert [i for i in ITEM_GROUPS if i != MW and i not in game.items] == []


def test_every_raw_resource_has_a_group_and_an_unknown_item_is_other(game):
    raw = [i for i, item in game.items.items() if item.is_resource]
    assert raw and [i for i in raw if item_group(i) == "other"] == []
    assert item_group("Desc_LiquidOil_C") == "oil" and item_group("Desc_Water_C") == "water"
    assert item_group("Desc_NitrogenGas_C") == "gas" and item_group("Desc_OreIron_C") == "ore"
    assert item_group("Desc_IronPlate_C") == "metal" and item_group("Desc_Motor_C") == "component"
    assert item_group("Desc_NuclearFuelRod_C") == "nuclear" and item_group(MW) == "power"
    assert item_group("Desc_NoSuchThing_C") == "other" and item_group(None) == "other"


# ------------------------------------------------------------------ flows add up


def test_every_process_sends_and_takes_exactly_its_rates(game, state):
    body, graph = _graph(game, state, FIVE_RIP_ARGS)
    out, into = _sums(graph["edges"], "source"), _sums(graph["edges"], "target")
    rows = {r["id"]: r for r in body["rows"]}
    by_name = {item.name: cls for cls, item in game.items.items()}
    for node in (n for n in graph["nodes"] if n["kind"] == "process"):
        made: dict[str, float] = defaultdict(float)
        for rid in node["rows"]:
            for side, sign in (("outputs", 1), ("inputs", -1)):
                for rate in rows[rid][side]:
                    if rate["item"] != "MW":
                        made[by_name[rate["item"]]] += sign * rate["per_min"]
        for item, rate in made.items():
            got = out[(node["id"], item)] if rate > 0 else into[(node["id"], item)]
            assert got == pytest.approx(abs(rate), abs=1e-3), (node["id"], item)
    [export] = [n for n in graph["nodes"] if n["kind"] == "export"]
    assert into[(export["id"], export["item_id"])] == pytest.approx(5.0, abs=1e-4)


def test_edges_carry_stable_ids_groups_and_units(game, state):
    _, graph = _graph(game, state, FIVE_RIP_ARGS)
    ids = {n["id"] for n in graph["nodes"]}
    for e in graph["edges"]:
        assert e["id"] == f"{e['source']}>{e['target']}:{e['item_id']}"
        assert e["source"] in ids and e["target"] in ids and e["per_min"] > 0
        assert e["item_group"] == item_group(e["item_id"])
    water = [e for e in graph["edges"] if e["item_id"] == "Desc_Water_C"]
    assert water and all(e["unit"] == "m3" and e["item_group"] == "water" for e in water)
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"])


def test_a_solve_is_the_same_graph_every_time(game, state):
    assert _graph(game, state, FIVE_RIP_ARGS)[1] == _graph(game, state, FIVE_RIP_ARGS)[1]


def test_node_ids_hold_when_the_rate_changes(game, state):
    _, five = _graph(game, state, FIVE_RIP_ARGS)
    _, fifteen = _graph(game, state, {**FIVE_RIP_ARGS, "export_minimums": {RIP: 15}})
    assert {n["id"] for n in five["nodes"]} == {n["id"] for n in fifteen["nodes"]}
    assert {e["id"] for e in five["edges"]} == {e["id"] for e in fifteen["edges"]}
    [export] = [n for n in fifteen["nodes"] if n["kind"] == "export"]
    assert export["per_min"] == 15.0


def test_a_process_node_counts_machines_clock_and_its_last_machine(game, state):
    _, graph = _graph(game, state, FIVE_RIP_ARGS)
    for node in (n for n in graph["nodes"] if n["kind"] == "process"):
        assert node["id"] == "p:" + (node["recipe_id"] or node["id"][2:])
        assert node["built"] >= 1 and 0 < node["utilisation"] <= 1
        assert node["clock"] == pytest.approx(100 * node["machines"] / node["built"], abs=0.01)
        whole = node["machines"] - int(node["machines"])
        assert node["utilisation"] == (1.0 if whole < 1e-6 else pytest.approx(whole, abs=1e-3))
    total = sum(n["built"] for n in graph["nodes"] if n["kind"] == "process")
    assert sum(b["count"] for b in graph["buildings"]) == total
    mws = [abs(b["mw"]) for b in graph["buildings"]]
    assert mws == sorted(mws, reverse=True)


def test_process_nodes_name_their_building_picture_and_item_nodes_none(game, state):
    _, graph = _graph(game, state, SUNK)
    processes = [n for n in graph["nodes"] if n["kind"] == "process"]
    assert processes
    for node in processes:
        assert node["building_icon"].startswith("Desc_") and node["building_icon"].endswith("_C")
    ends = [n for n in graph["nodes"] if n["kind"] in ("input", "export", "leftover")]
    assert ends and all(n["building_icon"] is None for n in ends)


def test_a_power_plant_draws_its_generators_and_their_power_export(game, state):
    _, graph = _graph(game, state, SUNK)
    nodes = {n["id"]: n for n in graph["nodes"]}
    generators = [n for n in nodes.values() if n["kind"] == "process" and n["mw"] > 0]
    assert generators and nodes["ex:power"]["kind"] == "export"
    assert all(n["building_icon"].startswith("Desc_Generator") for n in generators)
    fed = {e["target"] for e in graph["edges"] if e["item_id"] != "power"}
    powering = {e["source"] for e in graph["edges"] if e["target"] == "ex:power"}
    assert {n["id"] for n in generators} <= fed & powering


# ------------------------------------------------------------------ leftovers and sinks


def test_a_sunk_byproduct_is_a_sink_node_with_its_sinks_and_power(game, state):
    _, graph = _graph(game, state, SUNK)
    [sink] = [n for n in graph["nodes"] if n["kind"] == "sink"]
    assert sink["id"] == "sink:Desc_PolymerResin_C" and sink["building"] == "AWESOME Sink"
    assert sink["built"] >= 1 and sink["mw"] is not None and sink["mw"] < 0
    assert sink["building_icon"] == "Desc_ResourceSink_C"
    into = _sums(graph["edges"], "target")[(sink["id"], "Desc_PolymerResin_C")]
    assert into == pytest.approx(sink["per_min"], abs=1e-3)
    [sinks] = [b for b in graph["buildings"] if b["building"] == "AWESOME Sink"]
    assert sinks["count"] == sink["built"]
    power = _sums(graph["edges"], "target")[("ex:power", "power")]
    assert power == pytest.approx(
        next(n for n in graph["nodes"] if n["id"] == "ex:power")["per_min"]
    )


def test_a_leftover_shows_only_the_excess_over_the_asked_rate(game, state):
    _, graph = _graph(game, state, LEFT)
    nodes = {n["id"]: n for n in graph["nodes"]}
    export, left = nodes["ex:Desc_PolymerResin_C"], nodes["left:Desc_PolymerResin_C"]
    assert export["per_min"] == 10.0 and left["kind"] == "leftover" and left["per_min"] > 0
    into = _sums(graph["edges"], "target")
    assert into[(left["id"], "Desc_PolymerResin_C")] == pytest.approx(left["per_min"], abs=1e-3)
    assert into[(export["id"], "Desc_PolymerResin_C")] == pytest.approx(10.0, abs=1e-4)
    _, sunk = _graph(game, state, SUNK)
    [sink] = [n for n in sunk["nodes"] if n["kind"] == "sink"]
    assert left["per_min"] == pytest.approx(sink["per_min"] - 10.0, abs=1e-3)
    assert "left over" in left["detail"]


def test_positions_are_echoed_only_for_nodes_the_graph_has(game, state):
    _, plain = _graph(game, state, FIVE_RIP_ARGS)
    node = plain["nodes"][0]["id"]
    body = solve_summary(
        game, state, dict(FIVE_RIP_ARGS), positions={node: [1.5, -2.0], "p:gone": [0.0, 0.0]}
    )
    assert body["graph"]["positions"] == {node: [1.5, -2.0]}
    assert {**body["graph"], "positions": {}} == plain

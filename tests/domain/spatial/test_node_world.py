"""A randomized world's node table: the map's positions with the save's resources and purities.

The overrides are the shape the randomized test save writes (save-projection.md §6.18).
"""

from __future__ import annotations

from typing import Any, cast

from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.nodes import table as node_table
from satisfactory_mcp.domain.spatial.nodes import world as node_world

P = "Persistent_Level:PersistentLevel."


def _node(name: str, resource: str, purity: str, kind: str = "node", core: str | None = None):
    return {
        "instance": P + name,
        "resource": resource,
        "purity": purity,
        "kind": kind,
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "well_core": core,
    }


def _table() -> nodes_mod.NodeTable:
    nodes = [
        _node("BP_ResourceNode1", "Desc_Coal_C", "impure"),
        _node("BP_ResourceNode2", "Desc_OreIron_C", "pure"),
        _node("BP_FrackingSatellite1", "Desc_Water_C", "pure", "well_sat", P + "BP_FrackingCore1"),
        _node(
            "BP_FrackingSatellite2", "Desc_Water_C", "normal", "well_sat", P + "BP_FrackingCore1"
        ),
        _node("BP_ResourceNodeGeyser1", "Desc_Geyser_C", "pure", "geyser"),
    ]
    core = {
        "instance": P + "BP_FrackingCore1",
        "resource": "Desc_Water_C",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "satellites": [P + "BP_FrackingSatellite1", P + "BP_FrackingSatellite2"],
    }
    return nodes_mod.NodeTable(cast("Any", nodes), {"m": 1}, cast("Any", [core]))


RANDOMIZED: dict[str, Any] = {
    P + "BP_ResourceNode1": {"resource": "Desc_OreGold_C", "purity": "normal"},
    P + "BP_ResourceNode2": {"resource": "Desc_OreIron_C", "purity": "normal", "resources_left": 9},
    P + "BP_FrackingSatellite1": {"resource": "Desc_NitrogenGas_C", "purity": "normal"},
    P + "BP_FrackingSatellite2": {"resource": "Desc_NitrogenGas_C", "purity": "normal"},
    P + "BP_FrackingCore1": {"resource": "Desc_NitrogenGas_C"},
}


def test_the_save_decides_resource_and_purity_and_the_map_keeps_position():
    world = node_world.overlaid(_table(), RANDOMIZED)
    by = world.by_instance()
    assert by[P + "BP_ResourceNode1"]["resource"] == "Desc_OreGold_C"
    assert by[P + "BP_ResourceNode1"]["purity"] == "normal"
    assert (by[P + "BP_ResourceNode1"]["x"], by[P + "BP_ResourceNode1"]["y"]) == (1.0, 2.0)
    assert by[P + "BP_ResourceNodeGeyser1"]["purity"] == "pure", "geysers are never overridden"
    assert world.randomized and world.meta == {"m": 1}


def test_a_core_takes_its_own_override_and_else_what_its_satellites_agree_on():
    assert node_world.overlaid(_table(), RANDOMIZED).well_cores[0]["resource"] == (
        "Desc_NitrogenGas_C"
    )
    without = {k: v for k, v in RANDOMIZED.items() if "Core" not in k}
    assert node_world.overlaid(_table(), without).well_cores[0]["resource"] == (
        "Desc_NitrogenGas_C"
    )
    split = {**without, P + "BP_FrackingSatellite2": {"resource": "Desc_Water_C"}}
    assert node_world.overlaid(_table(), split).well_cores[0]["resource"] == "Desc_Water_C"


def test_a_vanilla_save_gets_the_very_same_table():
    base = _table()
    assert node_world.overlaid(base, {P + "BP_ResourceNode1": {"resources_left": 70}}) is base
    assert node_world.overlaid(base, {}) is base


def test_no_save_is_the_maps_table_and_one_save_is_built_once(monkeypatch):
    base = _table()
    monkeypatch.setattr(node_table, "load_nodes", lambda: base)
    assert node_world.nodes_for(None) is base
    projection = cast("Any", {"node_state": RANDOMIZED})
    first = node_world.nodes_for(projection)
    assert first is not base and first.randomized
    assert node_world.nodes_for(projection) is first

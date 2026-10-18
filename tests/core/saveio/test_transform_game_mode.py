"""Schema 27: the game-mode options and a randomized world's per-node overrides (§6.18).

The property shapes are the ones a 1.2 randomized save writes, read off one with the parser.
"""

from __future__ import annotations

from typing import Any, cast

from pioneersav import ObjectReference
from satisfactory_mcp.core.saveio.extract import gamemode
from satisfactory_mcp.core.saveio.extract.walk import _ProjectionWalk

_GOLD = "/Game/FactoryGame/Resource/RawResources/OreGold/Desc_OreGold.Desc_OreGold_C"
_NODE = "Persistent_Level:PersistentLevel.BP_ResourceNode620"


def _ref(path: str) -> ObjectReference:
    return ObjectReference(level_name="", path_name=path)


def _walked(cls: str, properties: dict[str, Any]) -> dict[str, Any]:
    walk = _ProjectionWalk(out=cast("Any", {"node_state": {}, "game_mode": {}}))
    walk.visit(
        f"/Game/X.{cls}",
        cast("Any", type("H", (), {"instanceName": _NODE})()),
        cast("Any", _Obj(properties)),
    )
    return cast("dict[str, Any]", walk.out)


class _Obj:
    def __init__(self, properties: dict[str, Any]) -> None:
        self.properties = [[k, v] for k, v in properties.items()]


def test_the_options_read_as_plain_factors_and_names():
    mode = gamemode.game_mode(
        {
            "mEnergyCostMultiplier": 0.75,
            "mPartsCostMultiplier": 0.75,
            "mSpacePartsCostMultiplier": 0.75,
            "mNodeRandomization": ["ENodeRandomizationMode", "ENodeRandomizationMode::NRM_Strict"],
            "mNodePuritySettings": ["ENodePuritySettings", "ENodePuritySettings::NPS_AllNormal"],
            "mNodeRandomizationSeed": 160943561,
        }
    )
    assert mode == {
        "node_randomization": "strict",
        "node_purity": "all_normal",
        "seed": 160943561,
        "energy_cost": 0.75,
        "parts_cost": 0.75,
        "space_parts_cost": 0.75,
    }


def test_a_game_state_without_the_options_reads_as_the_defaults():
    assert gamemode.game_mode({}) == gamemode.DEFAULT_GAME_MODE


def test_a_randomized_node_carries_its_resource_and_purity():
    out = _walked(
        "BP_ResourceNode_C",
        {
            "mPurityOverride": ["EResourcePurity", "RP_Inpure"],
            "mResourcesLeft": -1,
            "mResourceClassOverride": _ref(_GOLD),
        },
    )
    assert out["node_state"] == {_NODE: {"resource": "Desc_OreGold_C", "purity": "impure"}}


def test_a_fracking_core_carries_a_resource_and_no_purity():
    assert gamemode.node_overrides({"mResourceClassOverride": _ref(_GOLD)}) == {
        "resource": "Desc_OreGold_C"
    }


def test_a_vanilla_node_writes_nothing_and_a_depleting_one_only_what_is_left():
    assert _walked("BP_ResourceNode_C", {"mResourcesLeft": -1})["node_state"] == {}
    assert _walked("BP_ResourceNode_C", {"mResourcesLeft": 70})["node_state"] == {
        _NODE: {"resources_left": 70}
    }


def test_the_game_state_actor_fills_game_mode():
    out = _walked("BP_GameState_C", {"mPartsCostMultiplier": 2.0})
    assert out["game_mode"]["parts_cost"] == 2.0
    assert out["game_mode"]["energy_cost"] == 1.0

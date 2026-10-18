"""The new-game options and the per-node overrides a randomized world writes (§6.18).

``AFGGameState`` holds the options; every resource node, satellite and fracking core of a
randomized world holds its own ``mResourceClassOverride`` and ``mPurityOverride``. The enum
spellings are ``FGGameState.h`` and ``FGResourceNode.h``.
"""

from __future__ import annotations

from typing import Literal

from ..schema import GameMode, NodeState
from .parser import SaveValue
from .readers import enum_value, optional_int, ref_class, to_float

__all__ = ["DEFAULT_GAME_MODE", "game_mode", "node_overrides"]

DEFAULT_GAME_MODE: GameMode = {
    "node_randomization": "none",
    "node_purity": "no_change",
    "seed": 0,
    "energy_cost": 1.0,
    "parts_cost": 1.0,
    "space_parts_cost": 1.0,
}

#: ``ENodeRandomizationMode``. ``NRM_BasicReach`` is the header's own spelling.
_RANDOMIZATION = {
    "NRM_None": "none",
    "NRM_Strict": "strict",
    "NRM_BasicReach": "basic_rich",
    "NRM_AdvancedRich": "advanced_rich",
    "NRM_FossilFuelRich": "fossil_fuel_rich",
}

#: ``ENodePuritySettings``.
_PURITY_SETTING = {
    "NPS_NoChange": "no_change",
    "NPS_AllPure": "all_pure",
    "NPS_AllNormal": "all_normal",
    "NPS_AllImpure": "all_impure",
    "NPS_AllRandom": "all_random",
    "NPS_Increase": "increase",
    "NPS_Decrease": "decrease",
}

#: ``EResourcePurity``; ``RP_MAX`` is the "no override" default and never reaches here.
_PURITY: dict[str, Literal["pure", "normal", "impure"]] = {
    "RP_Inpure": "impure",
    "RP_Normal": "normal",
    "RP_Pure": "pure",
}


def game_mode(properties: dict[str, SaveValue]) -> GameMode:
    """The options off ``BP_GameState_C``; a property the save leaves out is its default."""
    out = DEFAULT_GAME_MODE.copy()
    mode = enum_value(properties.get("mNodeRandomization"))
    if mode is not None:
        out["node_randomization"] = _RANDOMIZATION.get(mode, mode)
    purity = enum_value(properties.get("mNodePuritySettings"))
    if purity is not None:
        out["node_purity"] = _PURITY_SETTING.get(purity, purity)
    out["seed"] = optional_int(properties.get("mNodeRandomizationSeed")) or 0
    out["energy_cost"] = _multiplier(properties.get("mEnergyCostMultiplier"))
    out["parts_cost"] = _multiplier(properties.get("mPartsCostMultiplier"))
    out["space_parts_cost"] = _multiplier(properties.get("mSpacePartsCostMultiplier"))
    return out


def _multiplier(raw: SaveValue | None) -> float:
    """A float32 factor, rounded so that 0.3 reads back as written; absent is 1.0."""
    return 1.0 if raw is None else round(to_float(raw), 4)


def node_overrides(properties: dict[str, SaveValue]) -> NodeState:
    """The resource and purity a randomized world gave this node; empty on a vanilla one."""
    out: NodeState = {}
    resource = ref_class(properties.get("mResourceClassOverride"))
    if resource:
        out["resource"] = resource
    purity = _PURITY.get(enum_value(properties.get("mPurityOverride")) or "")
    if purity is not None:
        out["purity"] = purity
    return out

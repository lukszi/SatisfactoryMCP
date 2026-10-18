"""Which generator type to build out a circuit with: the biggest one the world can run.

A type is runnable when it is unlocked and unlocked recipes can make one of its fuels, raw
resources free. Hand-fed burners and geothermal (a free geyser, not a build) never count.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from ...core.gamedata.model import Building, GameData
from ..planning.analysis.supply import unmakeable
from ..planning.solver.optimize import solve
from ..planning.solver.scenario import chain_scenario
from ..power.report import BIOMASS_BURNERS

if TYPE_CHECKING:
    from ..world.state import WorldState

__all__ = ["GeneratorCandidate", "generator_candidates"]

#: Per minute asked of a fuel chain: any positive rate proves the chain exists.
_PROBE_RATE = 1.0

#: Geothermal needs a free geyser, so it is never a build-out answer.
_NEVER = BIOMASS_BURNERS | {"Build_GeneratorGeoThermal_C"}


class GeneratorCandidate(TypedDict):
    """One generator type, biggest first: ``mw`` its rated output at 100% (game data),
    ``runnable`` whether it is unlocked and a fuel of it makeable, ``verdict`` why in a few
    words and ``why`` the planner's reason a fuel is not makeable ("" otherwise), ``fuel``
    the fuel that made it runnable (null when none did)."""

    name: str
    icon: str | None
    mw: float
    runnable: bool
    verdict: str
    why: str
    fuel: str | None


def _candidates(game: GameData) -> list[Building]:
    found = [
        b
        for cls, b in game.buildings.items()
        if cls not in _NEVER and b.power_production_mw > 0 and b.fuels
    ]
    return sorted(found, key=lambda b: (-b.power_production_mw, b.name))


def _unlock_name(game: GameData, building: Building) -> str:
    names = [game.schematics[s].name for s in building.unlocked_by if s in game.schematics]
    return names[0] if names else "unknown"


def _fuel_chain(st: WorldState, fuel: str) -> str:
    """Empty when unlocked recipes make ``fuel`` from free raw resources, else why not."""
    game = st.game
    item = game.items.get(fuel)
    if item is not None and item.is_resource:
        return ""
    chain = chain_scenario(game, st, fuel)
    scenario = replace(chain.scenario, export_minimums={fuel: _PROBE_RATE})
    if solve(scenario).ok:
        return ""
    reasons = unmakeable(replace(chain.request, scenario=scenario), game)
    return reasons[0] if reasons else f"no unlocked chain makes {game.item_name(fuel)}"


def _candidate(st: WorldState, building: Building) -> GeneratorCandidate:
    game = st.game
    row: GeneratorCandidate = {
        "name": building.name,
        "icon": building.descriptor,
        "mw": building.power_production_mw,
        "runnable": False,
        "verdict": "",
        "why": "",
        "fuel": None,
    }
    if building.cls not in st.unlocked_building_ids:
        row["verdict"] = "not unlocked: " + _unlock_name(game, building)
        return row
    first_reason = ""
    for fuel in building.fuels:
        reason = _fuel_chain(st, fuel.fuel_class)
        if not reason:
            row.update(runnable=True, verdict="unlocked, fuel makeable")
            row["fuel"] = game.item_name(fuel.fuel_class)
            return row
        first_reason = first_reason or reason
    row.update(verdict="unlocked, no fuel makeable", why=first_reason)
    return row


def generator_candidates(st: WorldState) -> list[GeneratorCandidate]:
    """Every fuelled generator type, biggest first, each with its runnable verdict. The
    first runnable one is the build-out answer."""
    return [_candidate(st, b) for b in _candidates(st.game)]

"""The fastest belt and pipe the player can actually build."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from ...core.gamedata.model import GameData

__all__ = ["CarrierSet"]


@dataclass
class CarrierSet:
    """Carrier tiers, gated on what is unlocked rather than on what exists."""

    game: GameData
    unlocked_building_ids: set[str] = field(default_factory=set[str])

    #: Native classes that actually carry ITEMS between machines. Deliberately narrow.
    #: `items_per_min` alone is not the test: a Personnel Elevator reports 400/min and
    #: moves people, and a Conveyor Lift duplicates a belt tier's rate, so a naive
    #: "fastest thing with a rate" pick can name something that is not a belt at all.
    BELT_NATIVE: ClassVar[str] = "FGBuildableConveyorBelt"
    PIPE_NATIVE: ClassVar[str] = "FGBuildablePipeline"

    def best_carrier(self, native: str, rate: str) -> tuple[str, float] | None:
        """The fastest tier of one carrier the player can actually build.

        Planning against a tier you have not unlocked is silent-wrong-by-default, which is
        the worst failure a planner has: every pipe count halves or doubles and nothing
        says so. The belt and pipe rates were hardcoded at Mk5/Mk2 -- correct on this save
        and unverified for a year.
        """
        best: tuple[str, float] | None = None
        for cls, b in self.game.buildings.items():
            if b.native != native or cls not in self.unlocked_building_ids:
                continue
            value: float = getattr(b, rate, 0.0)
            if value and (best is None or value > best[1]):
                best = (cls, value)
        return best

    def best_belt(self) -> tuple[str, float] | None:
        return self.best_carrier(self.BELT_NATIVE, "items_per_min")

    def best_pipe(self) -> tuple[str, float] | None:
        return self.best_carrier(self.PIPE_NATIVE, "flow_m3_min")

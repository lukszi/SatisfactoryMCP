"""Which carrier moves an item, and how many parallel lines it takes.

The one home for that arithmetic, shared by ``layout`` and ``optimize._logistics``, plus
``resolve_tiers`` for where the capacities come from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ...world.state import WorldState

__all__ = ["Carrier", "TierChoice", "carrier_for", "resolve_tiers"]


@dataclass(frozen=True)
class Carrier:
    """A belt or a pipe, at a chosen tier."""

    kind: str  # belt | pipe
    unit: str
    capacity: float

    @property
    def fluid(self) -> bool:
        return self.kind == "pipe"

    def lines_for(self, rate: float) -> int:
        """Parallel lines needed to move ``rate``, never fewer than one. The ``- 1e-9`` is
        load-bearing: 1,560 items/min over a 780/min belt is exactly two lines, and without
        it binary rounding turns that into three.
        """
        if self.capacity <= 0:
            return 1
        return max(1, math.ceil(rate / self.capacity - 1e-9))


def carrier_for(game: GameData, item: str, belt_ipm: float, pipe_m3min: float) -> Carrier:
    """The carrier for one item. Fluids go by pipe, everything else by belt."""
    it = game.items.get(item)
    if it is not None and it.is_fluid:
        return Carrier("pipe", it.unit, pipe_m3min)
    # An item the dump does not know still has to be carried, and a belt never silently
    # promotes something to a pipe.
    return Carrier("belt", it.unit if it is not None else "/min", belt_ipm)


@dataclass
class TierChoice:
    """Which belt and pipe a schematic is drawn against, and whether the caller said so.

    A tier the CALLER named has to reach the scenario as well as the schematic, while a
    resolved default must not, or every recalled plan reports an override nobody made.
    """

    belt_ipm: float = 0.0
    pipe_m3min: float = 0.0
    #: Display names, resolved to the fastest unlocked tier when the caller named none.
    belt_tier: str = ""
    pipe_tier: str = ""
    asked_belt: bool = False
    asked_pipe: bool = False
    #: Populated only when a named tier does not exist; everything above is then unset.
    errors: list[str] = field(default_factory=list)


def resolve_tiers(game: GameData, st: WorldState, belt_tier: str, pipe_tier: str) -> TierChoice:
    """The belt and pipe a layout should be planned against. A blank tier means "the
    fastest this save can actually build", which is a world question and not a default."""

    # Both sides of every lookup go through this: the dump spells a tier "Mk.5" and callers
    # write "Mk5", and an unnormalised key misses silently, planning at the default tier
    # while reporting the one that was asked for.
    def _tier(name: str, prefix: str) -> str:
        return name.replace(prefix, "").replace(".", "").strip().casefold()

    belts = {
        _tier(b.name, "Conveyor Belt "): b.items_per_min
        for b in game.buildings.values()
        if b.native == st.BELT_NATIVE
    }
    pipes = {
        _tier(b.name, "Pipeline "): b.flow_m3_min
        for b in game.buildings.values()
        if b.native == st.PIPE_NATIVE and "Clean" not in b.name
    }
    tier_errors = [
        f"unknown {what}_tier {given!r}; known: {', '.join(sorted(table))}"
        for what, given, table in (("belt", belt_tier, belts), ("pipe", pipe_tier, pipes))
        if given and _tier(given, "") not in table
    ]
    if tier_errors:
        return TierChoice(errors=tier_errors)
    asked_belt, asked_pipe = bool(belt_tier), bool(pipe_tier)
    belt_tier, pipe_tier = _tier(belt_tier, ""), _tier(pipe_tier, "")
    # The Mk5/Mk2 figures are for a caller with no save to read, never a default here.
    best_belt, best_pipe = st.best_belt(), st.best_pipe()
    return TierChoice(
        belt_ipm=belts.get(belt_tier) or (best_belt[1] if best_belt else 780.0),
        pipe_m3min=pipes.get(pipe_tier) or (best_pipe[1] if best_pipe else 600.0),
        belt_tier=belt_tier or (game.buildings[best_belt[0]].name if best_belt else "Mk5"),
        pipe_tier=pipe_tier or (game.buildings[best_pipe[0]].name if best_pipe else "Mk2"),
        asked_belt=asked_belt,
        asked_pipe=asked_pipe,
    )

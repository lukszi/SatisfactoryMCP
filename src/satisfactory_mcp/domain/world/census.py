"""What is physically built, and how much of it is not doing its job."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from ..progression.unlocks import UnlockSet

__all__ = ["BuildCensus"]


@dataclass
class BuildCensus:
    """The build records of one save, counted and filtered.

    ``all_records`` is the join of machines, extractors and generators, and it is
    public because half the package wants exactly that list: overclocking, power,
    elevation and the site clustering all iterate it.
    """

    projection: dict
    game: GameData
    unlocks: UnlockSet

    @cached_property
    def built_counts(self) -> dict[str, int]:
        """Header census plus the lightweight subsystem.

        FGLightweightBuildableSubsystem holds Build_* classes that appear in no actor
        header, so a header-only count understates what exists.
        """
        from ...core.gamedata.constants import BUILDING_CLASS_ALIASES

        out: dict[str, int] = {}
        for source in (
            self.projection.get("building_counts", {}),
            self.projection.get("lightweight_counts") or {},
        ):
            for cls, n in source.items():
                # The save and the dump disagree on a few names. Folding the save's name
                # onto the dump's is what stops "unlocked but never built: Biomass
                # Burner" appearing while eight of them are running.
                key = BUILDING_CLASS_ALIASES.get(cls, cls)
                out[key] = out.get(key, 0) + n
        return out

    def built(self, building_id: str) -> int:
        return self.built_counts.get(building_id, 0)

    def unlocked_but_unbuilt(self) -> list[str]:
        """Capability the player has and is not using -- often the actionable gap."""
        interesting = {
            cls
            for cls in self.unlocks.unlocked_building_ids
            if (b := self.game.buildings.get(cls))
            and (b.is_manufacturer or b.is_generator or b.is_extractor)
        }
        return sorted(cls for cls in interesting if self.built(cls) == 0)

    @cached_property
    def paused(self) -> list[dict]:
        return [r for r in self.all_records() if r.get("paused")]

    @cached_property
    def misconfigured(self) -> list[dict]:
        """Manufacturers with no recipe selected -- they produce nothing."""
        return [m for m in self.projection.get("machines", ()) if not m.get("recipe")]

    @cached_property
    def overclocked(self) -> list[dict]:
        return [
            r
            for r in self.all_records()
            if r.get("clock") is not None and abs(r["clock"] - 1.0) > 1e-6
        ]

    def all_records(self) -> list[dict]:
        p = self.projection
        return [*p.get("machines", ()), *p.get("extractors", ()), *p.get("generators", ())]

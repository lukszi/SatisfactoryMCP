"""Milestones by tier, and what the Space Elevator is still owed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from .unlocks import UnlockSet

__all__ = ["PhaseLedger", "opened_tier", "opening_phase", "phase_number"]

#: The highest HUB tier open once Project Assembly phase N is delivered (game 1.0).
TIERS_OPENED: dict[int, int] = {0: 2, 1: 4, 2: 6, 3: 8, 4: 9}


def phase_number(phase: str | None) -> int | None:
    """GP_Project_Assembly_Phase_3 -> 3; None for a save with no phase."""
    match = re.search(r"_(\d+)$", phase or "")
    return int(match.group(1)) if match else None


def opened_tier(current_phase: str | None) -> int | None:
    """The highest HUB tier the delivered phases open; None when the save has no phase."""
    n = phase_number(current_phase)
    if n is None:
        return None
    return TIERS_OPENED[min(max(n, 0), max(TIERS_OPENED))]


def opening_phase(tier: int) -> int:
    """The Project Assembly phase whose delivery opens this HUB tier."""
    return min((n for n, top in TIERS_OPENED.items() if top >= tier), default=max(TIERS_OPENED))


@dataclass
class PhaseLedger:
    """Where the run stands: tiers completed and phase deliveries outstanding."""

    projection: dict
    game: GameData
    unlocks: UnlockSet

    def progression(self) -> dict:
        p = self.projection.get("progression", {})
        tiers: dict[int, list[str]] = {}
        for sid in self.unlocks.purchased_schematic_ids:
            s = self.game.schematics.get(sid)
            if s and s.type == "EST_Milestone":
                tiers.setdefault(s.tier, []).append(sid)
        all_tiers: dict[int, int] = {}
        for s in self.game.schematics.values():
            if s.type == "EST_Milestone":
                all_tiers[s.tier] = all_tiers.get(s.tier, 0) + 1
        # Highest FULLY complete tier. The highest tier with ANY milestone done is a
        # different and misleading number.
        complete = [t for t, total in sorted(all_tiers.items()) if len(tiers.get(t, ())) == total]
        return {
            "game_phase": p.get("game_phase"),
            "target_phase": p.get("target_phase"),
            "phase_costs_remaining": {
                k: {i: a for i, a in v.items() if a}
                for k, v in (p.get("phase_costs_remaining") or {}).items()
                if any(v.values())
            },
            "milestones_by_tier": {
                t: f"{len(tiers.get(t, ()))}/{all_tiers[t]}" for t in sorted(all_tiers)
            },
            "highest_complete_tier": max(complete) if complete else None,
            "purchased_schematics": len(self.unlocks.purchased_schematic_ids),
            "available_recipes": len(self.unlocks.available_recipe_ids),
        }

    #: EGamePhase -> GP_Project_Assembly_Phase_N, and it is MEASURED, not read.
    #:
    #: ``mGamePhaseCosts`` is keyed by the deprecated EGamePhase enum while
    #: ``mCurrentGamePhase``/``mTargetGamePhase`` point at UFGGamePhase assets, and nothing
    #: joins the two: those assets do not ship in Docs.json, the field that would join them
    #: lives on them, and the manager's own legacy scalar is absent from the save. The anchor
    #: is EGP_EndGame -> Phase_3, from a save where the target phase had exactly one item
    #: paid off and the EGP_EndGame entry showed the same three items with the same one
    #: settled; the rest follow by the enum order declared in the shipped header, the four
    #: stored keys being contiguous in it.
    #:
    #: ClassVar and not a field: a bare dict annotation on a dataclass is a mutable default
    #: and raises at class-creation time.
    EGP_TO_PHASE: ClassVar[dict[str, str]] = {
        "EGP_MidGame": "GP_Project_Assembly_Phase_1",
        "EGP_LateGame": "GP_Project_Assembly_Phase_2",
        "EGP_EndGame": "GP_Project_Assembly_Phase_3",
        "EGP_FoodCourt": "GP_Project_Assembly_Phase_4",
    }

    @staticmethod
    def _subtractable(snapshot: dict, complete: list[str], paid: dict) -> bool:
        """Whether the live paid-off record may be taken off this frozen row.

        Sound only where the row froze before ANY delivery, since a snapshot taken after one
        has the delivery in it already and would charge it twice. Two things prove a row
        froze late and both are checked: an item sitting at zero in it, and an item the live
        record says more has been paid into than the row still bills for. Neither can happen
        to a full cost. A partial payment smaller than the remainder is undetectable, which
        is why the surviving case is labelled and why what it yields is a LOWER bound on what
        is still owed -- the true cost is at least the frozen figure.
        """
        return not complete and all(v <= snapshot.get(i, 0.0) for i, v in paid.items())

    def phase_requirements(self) -> dict:
        """Space Elevator deliveries, live record first and deprecated record labelled.

        Two sources disagree and only one is alive. ``mCurrentGamePhase`` /
        ``mTargetGamePhase`` / ``mTargetGamePhasePaidOffCosts`` are live: deliveries go to
        the TARGET phase, so "what do I owe" is its cost minus what is paid off.
        ``mGamePhaseCosts`` is deprecated and **frozen** -- byte-identical across 29 saves of
        the reference world spanning the session that finished Phase 3, which it still bills
        for -- but it is the only source of per-phase item lists, since the UFGGamePhase
        assets holding ``mCosts`` do not ship in Docs.json. It is trustworthy for the TARGET
        row alone: untouched it is still that phase's full cost, and once deliveries start
        the live record is subtracted from it where ``_subtractable`` allows rather than the
        row being written off. Every row carries a ``stale`` flag rather than being filtered.
        """
        p = self.projection.get("progression", {}) or {}
        current = p.get("game_phase") or ""
        target = p.get("target_phase") or ""
        # Absent means empty, not missing: UE omits empty SaveGame TArrays. Empty is
        # the informative answer here -- nothing has been delivered to the target yet.
        paid = {k: v for k, v in (p.get("paid_off_target") or {}).items() if v}

        rows = []
        for egp, costs in (p.get("phase_costs_remaining") or {}).items():
            phase = self.EGP_TO_PHASE.get(egp)
            snapshot = {i: a for i, a in costs.items() if a}
            outstanding = snapshot
            applied: dict[str, float] = {}
            done = sorted(i for i, a in costs.items() if not a)
            if phase is None:
                stale = "unmapped"
            elif phase == target and not paid:
                # Never delivered into, so the frozen snapshot is still the true cost.
                stale = "usable"
            elif phase == target and self._subtractable(snapshot, done, paid):
                applied = {i: v for i, v in paid.items() if i in snapshot}
                outstanding = {
                    i: a - applied.get(i, 0.0)
                    for i, a in snapshot.items()
                    if a - applied.get(i, 0.0) > 0
                }
                done = sorted(set(done) | (set(snapshot) - set(outstanding)))
                stale = "derived"
            elif not outstanding and current and phase <= current:
                # All zeros, and the live pointer agrees for a phase at or below it. With no
                # pointer, zeros are all an old save's frozen record holds, not deliveries.
                stale = "complete"
            else:
                stale = "stale"
            rows.append(
                {
                    "egp": egp,
                    "phase": phase,
                    "outstanding": outstanding,
                    "snapshot": snapshot,
                    "paid_applied": applied,
                    "complete": done,
                    "stale": stale,
                }
            )
        rows.sort(key=lambda r: r["phase"] or "~")
        return {
            "current_phase": current,
            "target_phase": target,
            "paid_off_target": paid,
            "phases": rows,
        }

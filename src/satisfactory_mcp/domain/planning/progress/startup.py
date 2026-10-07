"""Bringing a plant online without blowing the fuse.

**This is a startup order, not a build order.** Building costs materials and never power --
a machine draws only when it runs -- so the whole plant may be constructed at leisure and
then energised block by block, under one constraint: at every step, the energised consumer
draw must stay within the headroom plus the generation from generators already receiving
fuel. Overshooting does not degrade gracefully in Satisfactory; the fuse blows and the
whole grid stops until it is reset by hand, so every step here is checked rather than
merely reported. ``track`` then matches the partition back against the save.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from ....core.gamedata.model import GameData
from ....core.text import plural
from ..solver.graph import chain_depth_of_rates

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..solver.model import ProcessRow
    from ..solver.prepare import PreparedPlan

__all__ = ["Commissioning", "Wave", "WaveRow", "commission"]


#: How many waves to attempt before giving up. A plant whose generation exceeds its draw
#: converges geometrically, so a run that reaches this many is not converging.
MAX_WAVES = 24
#: How often, and by how much, a wave's share of the plant shrinks to fit whole machines.
_MAX_SHRINK_STEPS = 60
_SHRINK_FACTOR = 0.75


@dataclass
class WaveRow:
    """One process, and how much of it comes on in this wave."""

    label: str
    kind: str
    building: str
    #: Machines switched on in THIS wave, and the running total against the plan.
    machines: int
    cumulative: int
    total: int
    draw_mw: float
    generation_mw: float
    #: Distance from raw extraction along the item chain. Decides switch-on order within a
    #: wave: upstream first, so the fluid is already moving when the next block lights.
    depth: int = 0
    #: Solution process id, so a wave row joins back to the build job the diff matched
    #: against the save without re-deriving it from labels, which are display strings.
    pid: str = ""
    #: One cycle of this process at its own clock, in seconds. 0 for a generator, which
    #: burns continuously and has no cycle to wait through.
    cycle_s: float = 0.0


@dataclass
class Wave:
    index: int
    rows: list[WaveRow] = field(default_factory=list[WaveRow])
    available_before: float = 0.0

    def fill_s(self) -> float:
        """Lower bound, in seconds, on the wait before this wave's generators produce.

        The CYCLE chain: every stage must finish one full cycle before the next stage sees
        anything, so the sum of the slowest cycle at each chain depth is a hard floor.

        It is a floor and not an estimate because **pipe transit is not in it**. A pipe's
        fluid volume is not in Docs.json -- the only dimension there is ``mRadius``, which
        is collision geometry -- and route lengths are unknown, so on a long run the
        transit dominates this number. Machine input buffers are out for a related reason:
        their capacity is per-BUILT-machine and these machines do not exist yet.
        """
        deepest: dict[int, float] = {}
        for row in self.rows:
            if row.cycle_s <= 0:
                continue
            deepest[row.depth] = max(deepest.get(row.depth, 0.0), row.cycle_s)
        return sum(deepest.values())

    @property
    def draw_mw(self) -> float:
        return sum(r.draw_mw for r in self.rows)

    @property
    def generation_mw(self) -> float:
        return sum(r.generation_mw for r in self.rows)

    @property
    def available_after(self) -> float:
        return self.available_before - self.draw_mw + self.generation_mw

    @property
    def machines(self) -> int:
        return sum(r.machines for r in self.rows)

    @property
    def waits_for_fill(self) -> bool:
        """True when this wave energises both consumers and the generators they feed."""
        return any(r.generation_mw > 0 for r in self.rows) and any(r.draw_mw > 0 for r in self.rows)


@dataclass
class Commissioning:
    headroom_mw: float = 0.0
    #: Where the headroom figure came from, printed as a labelled input so a sequence
    #: computed against a stale save is visibly stale.
    headroom_source: str = ""
    waves: list[Wave] = field(default_factory=list[Wave])
    plant_draw_mw: float = 0.0
    plant_generation_mw: float = 0.0
    #: Cheapest slice that keeps every stage of the chain fed: one machine of every
    #: process. If this does not fit the headroom, no startup order exists at this scope.
    minimum_slice_mw: float = 0.0
    ok: bool = True
    warnings: list[str] = field(default_factory=list[str])

    @property
    def machines(self) -> int:
        return sum(w.machines for w in self.waves)


def _cycle_s(proc: ProcessRow, game: GameData) -> float:
    """How long one cycle of this process takes at the clock the plan runs it at.

    Clock divides: a machine at 250% finishes its cycle in 40% of the base time.
    """
    clock = proc.get("clock") or 1.0
    recipe = game.recipes.get(proc.get("recipe") or "")
    if recipe is not None and recipe.duration_s:
        return recipe.duration_s / clock
    building = game.buildings.get(proc.get("building_id") or "")
    if building is not None and building.extract_cycle_s:
        return building.extract_cycle_s / clock
    # Generators burn continuously; there is no cycle to wait through.
    return 0.0


def _depths(processes: list[ProcessRow]) -> dict[str, int]:
    """Chain depth per process id, from the same ``graph.chain_depth_of_rates`` the diff
    orders its build with, so the two halves of "which stage am I in" order one plant alike."""
    depths = chain_depth_of_rates([p["rates"] for p in processes])
    return {p["pid"]: d for p, d in zip(processes, depths, strict=True)}


class _MachinePower(NamedTuple):
    """One machine's share of its row: what it draws, or what it generates."""

    draw_mw: float
    generation_mw: float


def _fit_wave(
    fraction: float,
    totals: dict[str, int],
    started: dict[str, int],
    per_machine: dict[str, _MachinePower],
    available: float,
) -> dict[str, int] | None:
    """Machines per process for the next wave: ``fraction`` of the plant, shrunk until the
    whole-machine draw fits ``available``; None when no shrink fits.

    Ceil keeps the chain fed: flooring 0.64 of an extractor to zero would light six
    refineries with nothing to refine. Exact ratios are unreachable at the bottom of the
    ramp, so early waves run starved -- which errs safe, since an idle machine draws well
    under its modelled figure.
    """
    for _ in range(_MAX_SHRINK_STEPS):
        starting_now = {
            pid: min(
                totals[pid] - started[pid],
                max(1, math.ceil(fraction * totals[pid])) if totals[pid] > started[pid] else 0,
            )
            for pid in totals
        }
        cost = sum(per_machine[pid].draw_mw * n for pid, n in starting_now.items())
        if cost <= available + 1e-6:
            return starting_now
        fraction *= _SHRINK_FACTOR
    return None


def commission(
    prepared: PreparedPlan, game: GameData, headroom_mw: float, headroom_source: str = ""
) -> Commissioning:
    """Order the plan's machines into waves that can each be switched on safely."""
    out = Commissioning(headroom_mw=headroom_mw, headroom_source=headroom_source)
    if prepared.solution is None:
        out.ok = False
        return out

    procs = [p for p in prepared.solution.processes if p["machines"] > 0]
    if not procs:
        out.ok = False
        out.warnings.append("plan has no machines to energise")
        return out

    depth = _depths(procs)
    # Per MACHINE, because a wave energises whole machines. p["mw"] is exact for the whole
    # row at its derived clock, so dividing is right and rounding is not.
    per_machine: dict[str, _MachinePower] = {}
    for p in procs:
        each = p["mw"] / p["machines"]
        per_machine[p["pid"]] = _MachinePower(max(0.0, -each), max(0.0, each))
        out.plant_draw_mw += max(0.0, -p["mw"])
        out.plant_generation_mw += max(0.0, p["mw"])

    # One machine of every consuming process: the cheapest slice that still feeds the whole
    # chain. Generators draw nothing, so they are not part of the floor.
    out.minimum_slice_mw = sum(power.draw_mw for power in per_machine.values())
    if out.minimum_slice_mw > headroom_mw + 1e-6:
        out.ok = False
        out.warnings.append(
            f"no startup order exists at this scope: one machine of every process draws "
            f"{out.minimum_slice_mw:,.0f} MW and only {headroom_mw:,.0f} MW is free. "
            "Plan a smaller sub-plant (fewer nodes) and commission that first, or add "
            "generation before starting"
        )
        return out

    totals = {p["pid"]: p["machines"] for p in procs}
    started = {p["pid"]: 0 for p in procs}
    by_pid = {p["pid"]: p for p in procs}
    available = headroom_mw

    while sum(started.values()) < sum(totals.values()):
        if len(out.waves) >= MAX_WAVES:
            out.ok = False
            left = sum(totals.values()) - sum(started.values())
            out.warnings.append(
                f"gave up after {MAX_WAVES} waves with "
                f"{left} {plural('machine', left)} unstarted -- "
                "the sequence is not converging, which means generation is not "
                "outrunning draw"
            )
            return out

        wave = Wave(index=len(out.waves) + 1, available_before=available)
        # The largest fraction of the remaining plant this wave's power can carry.
        fraction = 1.0 if out.plant_draw_mw <= 0 else min(1.0, available / out.plant_draw_mw)
        starting_now = _fit_wave(fraction, totals, started, per_machine, available)
        if starting_now is None:
            out.ok = False
            out.warnings.append("could not fit a whole-machine wave inside the headroom")
            return out

        for pid, n in sorted(starting_now.items(), key=lambda kv: (depth[kv[0]], kv[0])):
            if n <= 0:
                continue
            started[pid] += n
            p = by_pid[pid]
            wave.rows.append(
                WaveRow(
                    pid=pid,
                    label=p["label"],
                    kind=p["kind"],
                    cycle_s=_cycle_s(p, game),
                    building=p["building"],
                    machines=n,
                    cumulative=started[pid],
                    total=totals[pid],
                    draw_mw=per_machine[pid].draw_mw * n,
                    generation_mw=per_machine[pid].generation_mw * n,
                    depth=depth[pid],
                )
            )
        if not wave.rows:
            out.ok = False
            out.warnings.append("a wave came out empty; nothing further can be energised")
            return out
        out.waves.append(wave)
        # Only NOW does this wave's generation count. It is not available DURING the wave:
        # the pipes are still filling and the generators are not burning yet, so a sequence
        # that spends it early looks fine on paper and trips halfway through.
        available = wave.available_after

    return out

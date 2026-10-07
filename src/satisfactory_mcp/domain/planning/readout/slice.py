"""What a subset of a solved plan costs and produces: power, flows, shards and sloops.

Power is the exact figure for whole machines at their derived clocks, never the LP's linear
one, and the sink's draw belongs to the whole plan only (docs/planning.md §8.2e).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....core.gamedata.constants import AWESOME_SINK_MW, shards_for_clock
from ....core.gamedata.model import GameData
from ..solver.model import ProcessRow, Solution

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..solver.prepare import PreparedPlan

__all__ = ["PlanSlice", "ShardRow", "SloopRow", "grid_import_mw", "linear_gap_note", "slice_of"]


@dataclass
class ShardRow:
    label: str
    machines: int
    clock: float
    each: int

    @property
    def total(self) -> int:
        return self.each * self.machines


@dataclass
class SloopRow:
    label: str
    machines: int
    slots_each: int
    #: Output multiplier if every slot were filled. 2.0 everywhere, but read from the
    #: building rather than assumed, since the cap is a game constant we do not own.
    boost: float

    @property
    def total(self) -> int:
        return self.slots_each * self.machines


@dataclass
class PlanSlice:
    """Totals over some -- or all -- of a solved plan's processes."""

    label: str = ""
    processes: list[ProcessRow] = field(default_factory=list[ProcessRow])
    machines: int = 0
    #: Exact power, split. draw is a POSITIVE number.
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: What the LP itself optimised, kept so the solution's own total can be reproduced.
    net_mw_linear: float = 0.0
    #: Charged to the plan as a whole; 0 on a partial slice, which cannot attribute it.
    sink_mw: float = 0.0
    #: Net per-minute rate per item across these processes.
    flows: dict[str, float] = field(default_factory=dict[str, float])
    shard_rows: list[ShardRow] = field(default_factory=list[ShardRow])
    #: EMPTY boostable slots -- capacity the plan did not use.
    sloop_rows: list[SloopRow] = field(default_factory=list[SloopRow])
    #: Slots the plan actually fills: a bill, kept apart from the suggestion above (§8.2f).
    sloop_used_rows: list[SloopRow] = field(default_factory=list[SloopRow])
    #: Slots on buildings this model cannot production-boost (generators, extractors).
    #: Counted separately so they are never advertised as a doubling.
    unboostable_slots: int = 0

    @property
    def net_mw(self) -> float:
        """Exact net power, sink included. The figure to check headroom against."""
        return self.generation_mw - self.draw_mw - self.sink_mw

    @property
    def shards(self) -> int:
        return sum(r.total for r in self.shard_rows)

    @property
    def sloop_slots(self) -> int:
        """Empty Somersloop slots across the slice -- capacity, not a commitment."""
        return sum(r.total for r in self.sloop_rows)

    @property
    def sloops_used(self) -> int:
        """Somersloops this plan spends, counted against WHOLE machines, so it can exceed
        the budget the LP solved under; the caller checks (docs/planning.md §8.2f)."""
        return sum(r.total for r in self.sloop_used_rows)

    def outputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        return sorted([(k, v) for k, v in self.flows.items() if v > tol], key=lambda kv: -kv[1])

    def inputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        return sorted([(k, -v) for k, v in self.flows.items() if v < -tol], key=lambda kv: -kv[1])


def slice_of(
    prepared: PreparedPlan,
    game: GameData,
    keep: Callable[[ProcessRow], bool] | None = None,
    label: str = "",
) -> PlanSlice:
    """Total a solved plan, or the part of it ``keep`` accepts.

    ``keep`` receives a process row exactly as ``plan_factory`` prints it, so a caller
    filters on whatever it already reads -- ``kind``, ``building_id``, ``label``.
    """
    solution = prepared.solution
    if solution is None:
        return PlanSlice(label=label)

    rows = [p for p in solution.processes if keep is None or keep(p)]
    out = PlanSlice(label=label, processes=rows)
    per_shard = max(game.clock_shards().values(), default=0.0)

    for row in rows:
        out.machines += row["machines"]
        power = row["mw"]
        if power >= 0:
            out.generation_mw += power
        else:
            out.draw_mw += -power
        out.net_mw_linear += row["mw_linear"]

        for item, rate in row["rates"].items():
            out.flows[item] = out.flows.get(item, 0.0) + rate

        last = row.get("last_clock")
        if last is not None and per_shard:
            # Overclock-last: only the one machine carries shards.
            each = shards_for_clock(last, per_shard)
            if each:
                out.shard_rows.append(ShardRow(row["label"] + " (last machine)", 1, last, each))
        elif row["clock"] > 1.0 + 1e-9 and per_shard:
            each = shards_for_clock(row["clock"], per_shard)
            if each:
                out.shard_rows.append(ShardRow(row["label"], row["machines"], row["clock"], each))

        building = game.buildings.get(row["building_id"] or "")
        if building is not None and building.sloop_slots:
            boost = building.boost_for(building.sloop_slots)
            if row["sloops"]:
                # A bill line, at the boost this row runs at rather than the building's top.
                out.sloop_used_rows.append(
                    SloopRow(
                        row["label"],
                        row["machines"],
                        row["sloops"],
                        building.boost_for(row["sloops"]),
                    )
                )
            elif building.can_boost and boost > 1.0:
                out.sloop_rows.append(
                    SloopRow(row["label"], row["machines"], building.sloop_slots, boost)
                )
            elif not building.can_boost:
                # Generator and extractor slots are never capacity (docs/planning.md §8.2e).
                out.unboostable_slots += building.sloop_slots * row["machines"]

    if keep is None:
        # Only the whole plan can own the sink: it is charged per belt line of sunk
        # material, and no single column is responsible for it.
        scenario = prepared.request.scenario
        out.sink_mw = sum(
            v * AWESOME_SINK_MW / max(scenario.belt_ipm, 1.0) for v in solution.sunk.values()
        )

    out.shard_rows.sort(key=lambda r: -r.total)
    out.sloop_rows.sort(key=lambda r: -r.total)
    out.sloop_used_rows.sort(key=lambda r: -r.total)
    return out


def grid_import_mw(solution: Solution, bill: PlanSlice) -> float:
    """What a plan takes from the existing grid at its exact draw: 0 unless the solve
    imports at all, since a power plant exporting MW may not import."""
    return max(0.0, -bill.net_mw) if solution.grid_import_mw > 0 else 0.0


def linear_gap_note(solution: Solution, bill: PlanSlice) -> str:
    """The solver's linear power figure beside the exact one, where they differ by 0.1 MW."""
    gap = bill.net_mw - solution.net_mw
    if abs(gap) < 0.1:
        return ""
    return (
        f"net_MW is exact for whole machines at their derived clocks; the solver priced "
        f"power linearly at {solution.net_mw:,.2f} MW, {abs(gap):,.2f} MW "
        f"{'below' if gap > 0 else 'above'} it"
    )

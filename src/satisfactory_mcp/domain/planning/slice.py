"""What a subset of a solved plan costs and produces.

Every plan-level question that is not "solve it" turns out to be this: take some of the
processes, and total up their power, their item flows, and the Power Shards and
Somersloops they need. The shard bill is one call. Commissioning is this in a loop.

Two power figures, and the difference is not rounding
----------------------------------------------------
``mw_linear`` is what the LP optimised: power proportional to machine-equivalents.
``mw`` is the exact figure after whole machines are placed at a derived clock. Because
``clock**exponent`` is convex, running N whole machines below 100% draws LESS than the
linear estimate -- on a measured Spire Coast plan, 43,101 MW exact against 43,092
promised, 9.4 MW to the good.

So the identity that holds is::

    solution.net_mw == sum(mw_linear) - sink_mw          # exactly

and ``net_mw`` here (exact) is always at least as good. Headroom checks should use the
exact figure: it is the power the machines actually draw, and erring the other way would
reject a slice that fits.

``sink_mw`` belongs to the PLAN, not to any process -- an AWESOME Sink is charged per
belt line of sunk material and no column owns it. A subset therefore cannot attribute it,
and a partial slice reports 0 rather than a share invented by proration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.gamedata.constants import AWESOME_SINK_MW, shards_for_clock
from ...core.gamedata.model import GameData

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
    processes: list[dict] = field(default_factory=list)
    machines: int = 0
    #: Exact power, split. draw is a POSITIVE number.
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: What the LP itself optimised, kept so the solution's own total can be reproduced.
    net_mw_linear: float = 0.0
    #: Charged to the plan as a whole; 0 on a partial slice, which cannot attribute it.
    sink_mw: float = 0.0
    #: Net per-minute rate per item across these processes.
    flows: dict[str, float] = field(default_factory=dict)
    shard_rows: list[ShardRow] = field(default_factory=list)
    #: EMPTY boostable slots -- capacity the plan did not use.
    sloop_rows: list[SloopRow] = field(default_factory=list)
    #: Slots the plan actually fills. Distinct from `sloop_rows` because one is a
    #: suggestion and the other is a bill: conflating them would report 856 somersloops
    #: needed for a plan that spends none.
    sloop_used_rows: list[SloopRow] = field(default_factory=list)
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
        """Somersloops this plan spends, counted against WHOLE machines.

        The LP spends them against machine-equivalents and the readout rounds those up,
        so this can exceed the budget the solver was given -- the same overshoot that
        turned a 54-extractor cap into 64 machines. The caller checks it against the
        budget rather than being told a number that is quietly too small.
        """
        return sum(r.total for r in self.sloop_used_rows)

    def outputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        return sorted([(k, v) for k, v in self.flows.items() if v > tol], key=lambda kv: -kv[1])

    def inputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        return sorted([(k, -v) for k, v in self.flows.items() if v < -tol], key=lambda kv: -kv[1])


def slice_of(
    prepared,
    game: GameData,
    keep: Callable[[dict], bool] | None = None,
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

        if row["clock"] > 1.0 + 1e-9 and per_shard:
            each = shards_for_clock(row["clock"], per_shard)
            if each:
                out.shard_rows.append(ShardRow(row["label"], row["machines"], row["clock"], each))

        building = game.buildings.get(row["building_id"] or "")
        if building is not None and building.sloop_slots:
            boost = building.boost_for(building.sloop_slots)
            if row["sloops"]:
                # Spent, so it is a bill line and never a suggestion. The boost is the
                # one this row actually runs at, not the building's maximum: a Refinery
                # with 1 of its 2 slots filled makes 1.5x, and quoting the 2x it could
                # reach would overstate the plan's own output.
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
                # Generators and extractors carry slots with can_boost False, so
                # boost_for returns 1.0. Whatever a somersloop does in a Fuel Generator,
                # this model does not represent it -- counting those slots as capacity
                # would promise an output gain the solver cannot deliver.
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


def grid_import_mw(solution, bill: PlanSlice) -> float:
    """What a plan takes from the existing grid at its exact draw: 0 unless the solve
    imports at all, since a power plant exporting MW may not import."""
    return max(0.0, -bill.net_mw) if solution.grid_import_mw > 0 else 0.0


def linear_gap_note(solution, bill: PlanSlice) -> str:
    """The solver's linear power figure beside the exact one, where they differ by 0.1 MW."""
    gap = bill.net_mw - solution.net_mw
    if abs(gap) < 0.1:
        return ""
    return (
        f"net_MW is exact for whole machines at their derived clocks; the solver priced "
        f"power linearly at {solution.net_mw:,.2f} MW, {abs(gap):,.2f} MW "
        f"{'below' if gap > 0 else 'above'} it"
    )

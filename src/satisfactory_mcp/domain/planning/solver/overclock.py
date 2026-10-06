"""What a payback horizon makes of each row: its clock, its machine price, overclock-last.

docs/planner-payback-horizon_contract.md is the specification (§2 prices, §4-§5 overclock).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.constants import shards_for_clock
from ....core.gamedata.model import Building
from .model import PAYBACK_STOPS, Process, Scenario

#: A row this close to whole machines has no fraction for the last machine to carry.
_WHOLE = 1e-4


def best_clock(sc: Scenario, building: Building, draw_full: float) -> float:
    """The clock where one more machine of ``building`` costs what the power it saves is worth
    over the horizon: ``(X / ((e - 1) P)) ** (1 / e)`` with ``X = K / (H r)``."""
    if sc.payback_hours <= 0 or sc.power_price <= 0:
        return 1.0
    return _clock_at(
        sc.build_points.get(building.cls, 0.0),
        sc.payback_hours * sc.power_price,
        draw_full,
        building.power_exponent,
        building.min_clock,
    )


#: Shortest horizon at which a power goal prices its machines in build points (ruling 3b).
#: Below it a dismantle refunds the materials before the price could be earned back.
#: The value is measured, docs/planner-payback-horizon_contract.md §2.
POWER_GOAL_BUILD_COST_FROM_H = 5.0


def machine_price_mw(sc: Scenario, building: str | None) -> float:
    """What one machine costs in MW when the goal is power: ``K / (H r)`` from
    ``POWER_GOAL_BUILD_COST_FROM_H`` up (F1a, 3b), else the flat ``machine_cost_mw``."""
    per_mw = sc.payback_hours * sc.power_price
    points = sc.build_points.get(building or "")
    if per_mw > 0 and points is not None and sc.payback_hours >= POWER_GOAL_BUILD_COST_FROM_H:
        return max(points, 1.0) / per_mw
    return max(0.0, sc.machine_cost_mw)


def _clock_at(points: float, per_mw: float, draw: float, exponent: float, floor: float) -> float:
    if points <= 0 or per_mw <= 0 or draw <= 0 or exponent <= 1:
        return 1.0
    return min(1.0, max(floor, (points / per_mw / ((exponent - 1) * draw)) ** (1 / exponent)))


def _spreadable(process: Process) -> bool:
    """A row the horizon may spread: production at or below 100% with no sloops, on a building
    whose power is convex in clock. Extractors are bound by their nodes."""
    return (
        process.kind == "recipe"
        and not process.sloops
        and process.clock <= 1.0
        and process.power_exponent > 1.0
    )


@dataclass
class _SpreadableRow:
    """One recipe row the horizon may spread: its throughput in machine-units at 100%."""

    process: Process
    units: float
    building: Building | None

    @property
    def draw(self) -> float:
        return max(0.0, -self.process.mw_at_full)

    def power(self, machines: int) -> float:
        return machines * self.draw * (self.units / machines) ** self.process.power_exponent

    def spread(self, clock: float) -> int:
        floor = self.building.min_clock if self.building is not None else 0.01
        low = max(1, math.ceil(self.units - 1e-9))
        high = max(low, math.floor(self.units / floor + 1e-9))
        return max(low, min(high, math.ceil(self.units / clock - 1e-9)))

    def overclock_last_option(self, per_shard: float) -> tuple[int, float, int] | None:
        """``(machines, last clock, shards)`` with every machine but the last at 100%."""
        building = self.building
        machines = math.floor(self.units + _WHOLE)
        if building is None or machines < 1 or not building.can_overclock or not per_shard:
            return None
        fraction = self.units - machines
        if fraction < _WHOLE or 1.0 + fraction > building.max_clock + 1e-9:
            return None
        return machines, 1.0 + fraction, shards_for_clock(1.0 + fraction, per_shard)

    def last_power(self, machines: int, clock: float) -> float:
        return self.draw * ((machines - 1) + clock**self.process.power_exponent)


@dataclass
class _HorizonReadout:
    """Every spreadable row read out at one horizon, by row index."""

    #: ``(machines, draw)`` of the cheapest spread, one per row.
    spread: list[tuple[int, float]] = field(default_factory=list)
    #: ``(machines, last clock, shards)`` for the rows built overclock-last.
    picks: dict[int, tuple[int, float, int]] = field(default_factory=dict)
    #: Rows whose pick the shard budget could not cover.
    without: list[int] = field(default_factory=list)
    #: Rows where overclocking the last machine costs more than it saves.
    unused: list[int] = field(default_factory=list)
    #: Every row's overclock-last candidate, picked or not.
    options: dict[int, tuple[int, float, int]] = field(default_factory=dict)


def _pinned(sc: Scenario, row: _SpreadableRow) -> str | None:
    """The row's own choice, ``"last"`` or ``"spread"``, or None to follow the plan."""
    return sc.row_overclock.get(row.process.recipe or "")


def _applies(sc: Scenario, row: _SpreadableRow, on: bool | None) -> bool:
    """Whether a row's overclock pick is built: its own choice wins over the plan's switch;
    ``on`` None is the plain build, which never overclocks (contract §4)."""
    if on is None:
        return False
    pinned = _pinned(sc, row)
    return pinned == "last" or (on and pinned is None)


def _horizon_readout(
    sc: Scenario, rows: list[_SpreadableRow], hours: float, per_shard: float
) -> _HorizonReadout:
    """Machines per row at ``hours``: the cheapest spread, or one fewer with the last machine
    overclocked where that is cheaper still and shards last (contract §4-§5). A row set to
    ``last`` takes its shards first whatever it costs; one set to ``spread`` is never picked."""
    per_mw = hours * sc.power_price
    readout = _HorizonReadout()
    forced, candidates = [], []
    for i, row in enumerate(rows):
        points = sc.build_points.get(row.process.building or "", 0.0)
        clock = 1.0
        if row.building is not None:
            clock = _clock_at(
                points, per_mw, row.draw, row.process.power_exponent, row.building.min_clock
            )
        spread = row.spread(clock)
        readout.spread.append((spread, row.power(spread)))
        option = row.overclock_last_option(per_shard)
        if option is None:
            continue
        readout.options[i] = option
        pinned = _pinned(sc, row)
        if pinned == "spread":
            continue
        machines, top, shards = option
        price = max(points, 1.0)
        saving = price * (spread - machines) - per_mw * (
            row.last_power(machines, top) - row.power(spread)
        )
        if pinned == "last":
            forced.append((math.inf, i, machines, top, shards))
        elif saving > 1e-9:
            candidates.append((saving / max(shards, 1), i, machines, top, shards))
        else:
            readout.unused.append(i)
    left = math.inf if sc.overclock_shards is None else sc.overclock_shards
    ranked = forced + sorted(candidates, key=lambda c: (-c[0], c[1]))
    for _, i, machines, top, shards in ranked:
        if shards > left + 1e-9:
            readout.without.append(i)
            continue
        left -= shards
        readout.picks[i] = (machines, top, shards)
    return readout


def _overclock_option(sc: Scenario, row: _SpreadableRow, i: int, readout: _HorizonReadout) -> dict:
    """A row's two builds, for the per-row choice: one fewer with the last overclocked, or
    the spread; ``applied`` says which is built."""
    machines, top, shards = readout.options[i]
    spread, power = readout.spread[i]
    return {
        "pinned": _pinned(sc, row),
        "applied": i in readout.picks and _applies(sc, row, sc.overclock_last),
        "machines": machines,
        "last_clock": round(top, 6),
        "shards": shards,
        "extra_mw": round(row.last_power(machines, top) - power, 4),
        "spread_machines": spread,
        "spread_clock": round(row.units / spread, 6),
        "without": i in readout.without,
        "unused": i in readout.unused,
    }


def _row_names(rows: list[_SpreadableRow], indexes: list[int]) -> list[dict]:
    return [
        {
            "label": rows[i].process.label,
            "building": rows[i].building.name if rows[i].building is not None else "",
        }
        for i in indexes
    ]


def _overclock_view(sc: Scenario, rows: list[_SpreadableRow], readout: _HorizonReadout) -> dict:
    """``Solution.overclock``: the rows picked at this horizon, and the ones left out."""
    picked = []
    for i, (machines, top, shards) in sorted(readout.picks.items()):
        row = rows[i]
        spread, power = readout.spread[i]
        picked.append(
            {
                "label": row.process.label,
                "building": row.building.name if row.building is not None else "",
                "machines": machines,
                "instead": spread,
                "last_clock": round(top, 6),
                "shards": shards,
                "extra_mw": round(row.last_power(machines, top) - power, 4),
                "pinned": _pinned(sc, row),
                "applied": _applies(sc, row, sc.overclock_last),
            }
        )
    pinned = [_pinned(sc, rows[i]) for i in readout.options]
    return {
        "on": sc.overclock_last,
        "rows": picked,
        "shards": sum(r["shards"] for r in picked),
        "machines_saved": sum(r["instead"] - r["machines"] for r in picked),
        "extra_mw": round(sum(r["extra_mw"] for r in picked), 4),
        "without": _row_names(rows, readout.without),
        "unused": _row_names(rows, readout.unused),
        "pinned_last": pinned.count("last"),
        "pinned_spread": pinned.count("spread"),
    }


def _payback_curve(
    sc: Scenario, fixed: tuple[float, float], rows: list[_SpreadableRow], per_shard: float
) -> list[dict]:
    """One readout per stop, plus ``plain``: 0 h with no overclock, what stops compare to."""
    machines_fixed, draw_fixed = fixed
    out = []
    stops = [(h, sc.overclock_last) for h in sorted({*PAYBACK_STOPS, float(sc.payback_hours)})]
    for hours, overclock_last_on in [*stops, (0.0, None)]:
        readout = _horizon_readout(sc, rows, hours, per_shard)
        machines, draw, buildings = machines_fixed, draw_fixed, {}
        shards = 0
        for i, row in enumerate(rows):
            count, power = readout.spread[i]
            if i in readout.picks and _applies(sc, row, overclock_last_on):
                count, top, used = readout.picks[i]
                power = row.last_power(count, top)
                shards += used
            machines += count
            draw += power
            buildings[row.process.building] = buildings.get(row.process.building, 0) + count
        out.append(
            {
                "hours": hours,
                "machines": machines,
                "draw_mw": round(draw, 4),
                "buildings": buildings,
                "shards": shards,
            }
        )
    out[-1]["plain"] = True
    return out

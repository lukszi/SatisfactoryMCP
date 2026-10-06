"""What a payback horizon makes of each row: its clock, its machine price, overclock-last."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ....core.gamedata.constants import shards_for_clock
from .model import PAYBACK_STOPS, Process, Scenario

#: A row this close to whole machines has no fraction for the last machine to carry.
_WHOLE = 1e-4


def best_clock(sc: Scenario, b, draw_full: float) -> float:
    """The clock where one more machine of ``b`` costs what the power it saves is worth over
    the horizon: ``(X / ((e - 1) P)) ** (1 / e)`` with ``X = K / (H r)``."""
    if sc.payback_hours <= 0 or sc.power_price <= 0:
        return 1.0
    return _clock_at(
        sc.build_points.get(b.cls, 0.0),
        sc.payback_hours * sc.power_price,
        draw_full,
        b.power_exponent,
        b.min_clock,
    )


#: Shortest horizon at which a power goal prices its machines in build points (ruling 3b).
#: Below it a dismantle refunds the materials before the price could be earned back.
#: The value is measured, docs/planner-payback-horizon_contract.md §2.
POWER_GOAL_BUILD_COST_FROM_H = 5.0


def machine_mw(sc: Scenario, building: str | None) -> float:
    """What one machine costs in MW when the goal is power: ``K / (H r)`` from
    ``POWER_GOAL_BUILD_COST_FROM_H`` up (F1a, 3b), else the flat ``machine_cost_mw``."""
    per_mw = sc.payback_hours * sc.power_price
    points = sc.build_points.get(building or "")
    if per_mw > 0 and points is not None and sc.payback_hours >= POWER_GOAL_BUILD_COST_FROM_H:
        return max(points, 1.0) / per_mw
    return max(0.0, sc.machine_cost_mw)


def _clock_at(points: float, per_mw: float, draw: float, e: float, floor: float) -> float:
    if points <= 0 or per_mw <= 0 or draw <= 0 or e <= 1:
        return 1.0
    return min(1.0, max(floor, (points / per_mw / ((e - 1) * draw)) ** (1 / e)))


def _spreadable(p: Process) -> bool:
    """A row the horizon may spread: production at or below 100% with no sloops, on a building
    whose power is convex in clock. Extractors are bound by their nodes."""
    return p.kind == "recipe" and not p.sloops and p.clock <= 1.0 and p.power_exponent > 1.0


@dataclass
class _Row:
    p: Process
    units: float
    building: object | None

    @property
    def draw(self) -> float:
        return max(0.0, -self.p.mw_at_full)

    def power(self, n: int) -> float:
        return n * self.draw * (self.units / n) ** self.p.power_exponent

    def spread(self, clock: float) -> int:
        floor = self.building.min_clock if self.building is not None else 0.01
        low = max(1, math.ceil(self.units - 1e-9))
        high = max(low, math.floor(self.units / floor + 1e-9))
        return max(low, min(high, math.ceil(self.units / clock - 1e-9)))

    def last(self, per_shard: float) -> tuple[int, float, int] | None:
        """``(machines, last clock, shards)`` with every machine but the last at 100%."""
        b = self.building
        n = math.floor(self.units + _WHOLE)
        if b is None or n < 1 or not b.can_overclock or not per_shard:
            return None
        frac = self.units - n
        if frac < _WHOLE or 1.0 + frac > b.max_clock + 1e-9:
            return None
        return n, 1.0 + frac, shards_for_clock(1.0 + frac, per_shard)

    def last_power(self, n: int, clock: float) -> float:
        return self.draw * ((n - 1) + clock**self.p.power_exponent)


def _pinned(sc: Scenario, row: _Row) -> str | None:
    """The row's own choice, ``"last"`` or ``"spread"``, or None to follow the plan."""
    return sc.row_overclock.get(row.p.recipe or "")


def _applies(sc: Scenario, row: _Row, on: bool | None) -> bool:
    """Whether a row's overclock pick is built: its own choice wins over the plan's switch;
    ``on`` None is the plain build, which never overclocks (contract §4)."""
    if on is None:
        return False
    pinned = _pinned(sc, row)
    return pinned == "last" or (on and pinned is None)


def _readout(sc: Scenario, rows: list[_Row], hours: float, per_shard: float) -> dict:
    """Machines per row at ``hours``: the cheapest spread, or one fewer with the last machine
    overclocked where that is cheaper still and shards last (contract §4-§5). A row set to
    ``last`` takes its shards first whatever it costs; one set to ``spread`` is never picked."""
    per_mw = hours * sc.power_price
    spread, picks, without, unused, options = [], {}, [], [], {}
    forced, candidates = [], []
    for i, row in enumerate(rows):
        b = row.building
        points = sc.build_points.get(row.p.building or "", 0.0)
        clock = 1.0
        if b is not None:
            clock = _clock_at(points, per_mw, row.draw, row.p.power_exponent, b.min_clock)
        n = row.spread(clock)
        spread.append((n, row.power(n)))
        last = row.last(per_shard)
        if last is None:
            continue
        options[i] = last
        pinned = _pinned(sc, row)
        if pinned == "spread":
            continue
        machines, top, shards = last
        price = max(points, 1.0)
        saving = price * (n - machines) - per_mw * (row.last_power(machines, top) - row.power(n))
        if pinned == "last":
            forced.append((math.inf, i, machines, top, shards))
        elif saving > 1e-9:
            candidates.append((saving / max(shards, 1), i, machines, top, shards))
        else:
            unused.append(i)
    left = math.inf if sc.overclock_shards is None else sc.overclock_shards
    ranked = forced + sorted(candidates, key=lambda c: (-c[0], c[1]))
    for _, i, machines, top, shards in ranked:
        if shards > left + 1e-9:
            without.append(i)
            continue
        left -= shards
        picks[i] = (machines, top, shards)
    return {
        "spread": spread,
        "picks": picks,
        "without": without,
        "unused": unused,
        "options": options,
    }


def _option(sc: Scenario, row: _Row, i: int, read: dict) -> dict:
    """A row's two builds, for the per-row choice: one fewer with the last overclocked, or
    the spread; ``applied`` says which is built."""
    machines, top, shards = read["options"][i]
    n, power = read["spread"][i]
    return {
        "pinned": _pinned(sc, row),
        "applied": i in read["picks"] and _applies(sc, row, sc.overclock_last),
        "machines": machines,
        "last_clock": round(top, 6),
        "shards": shards,
        "extra_mw": round(row.last_power(machines, top) - power, 4),
        "spread_machines": n,
        "spread_clock": round(row.units / n, 6),
        "without": i in read["without"],
        "unused": i in read["unused"],
    }


def _overclock_view(sc: Scenario, rows: list[_Row], out: dict) -> dict:
    picked = []
    for i, (machines, top, shards) in sorted(out["picks"].items()):
        row = rows[i]
        n, power = out["spread"][i]
        picked.append(
            {
                "label": row.p.label,
                "building": row.building.name if row.building is not None else "",
                "machines": machines,
                "instead": n,
                "last_clock": round(top, 6),
                "shards": shards,
                "extra_mw": round(row.last_power(machines, top) - power, 4),
                "pinned": _pinned(sc, row),
                "applied": _applies(sc, row, sc.overclock_last),
            }
        )

    def names(idx: list[int]) -> list[dict]:
        return [
            {
                "label": rows[i].p.label,
                "building": rows[i].building.name if rows[i].building is not None else "",
            }
            for i in idx
        ]

    pinned = [_pinned(sc, rows[i]) for i in out["options"]]
    return {
        "on": sc.overclock_last,
        "rows": picked,
        "shards": sum(r["shards"] for r in picked),
        "machines_saved": sum(r["instead"] - r["machines"] for r in picked),
        "extra_mw": round(sum(r["extra_mw"] for r in picked), 4),
        "without": names(out["without"]),
        "unused": names(out["unused"]),
        "pinned_last": pinned.count("last"),
        "pinned_spread": pinned.count("spread"),
    }


def _payback_curve(
    sc: Scenario, fixed: tuple[float, float], rows: list[_Row], per_shard: float
) -> list[dict]:
    """One readout per stop, plus ``plain``: 0 h with no overclock, what stops compare to."""
    machines_fixed, draw_fixed = fixed
    out = []
    stops = [(h, sc.overclock_last) for h in sorted({*PAYBACK_STOPS, float(sc.payback_hours)})]
    for hours, on in [*stops, (0.0, None)]:
        read = _readout(sc, rows, hours, per_shard)
        machines, draw, buildings = machines_fixed, draw_fixed, {}
        shards = 0
        for i, row in enumerate(rows):
            n, power = read["spread"][i]
            if i in read["picks"] and _applies(sc, row, on):
                n, top, used = read["picks"][i]
                power = row.last_power(n, top)
                shards += used
            machines += n
            draw += power
            buildings[row.p.building] = buildings.get(row.p.building, 0) + n
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

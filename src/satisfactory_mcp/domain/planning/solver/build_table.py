"""The build table: an LP solution read out as whole machines, and what it binds and warns.

docs/planning.md §8.2c (folded clock modes, omitted rows) and §8.4 (derived clocks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.arrays import F64Grid
from ....core.gamedata.model import GameData
from .carrier import carrier_for
from .lp import Columns
from .model import NEGLIGIBLE_IPM, LogisticsRow, PaybackPoint, Process, ProcessRow, Scenario
from .overclock import (
    SpreadableRow,
    horizon_readout,
    overclock_option,
    overclock_view,
    payback_curve,
    spreadable,
)
from .views import OverclockOption, OverclockPick

__all__ = ["BuildRows", "binding_constraints", "build_rows", "logistics", "solution_warnings"]

_EPS = 1e-7


@dataclass
class BuildRows:
    """The rows a solve prints, and the totals and readouts that come with them."""

    rows: list[ProcessRow] = field(default_factory=list[ProcessRow])
    machines_total: float = 0.0
    #: ``(label, largest rate)`` of rows left out as negligible; still counted in the total.
    dropped: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])
    payback_curve: list[PaybackPoint] = field(default_factory=list[PaybackPoint])
    overclock: OverclockPick | None = None


class _RowWriter:
    """Writes build rows; every row counts toward ``machines_total``, printed or not (§8.2c)."""

    def __init__(self, game: GameData) -> None:
        self.game = game
        self.rows: list[ProcessRow] = []
        self.machines_total = 0.0

    def write(
        self,
        process: Process,
        built: int,
        effective_clock: float,
        equivalents: float,
        rate_scale: float,
        listed: bool = True,
        last_clock: float | None = None,
        option: OverclockOption | None = None,
    ) -> None:
        """One row: ``equivalents`` is the machine count, ``rate_scale`` what scales the rates."""
        exact_mw = built * process.mw_at_full * (effective_clock**process.power_exponent)
        if last_clock is not None:
            exact_mw = process.mw_at_full * ((built - 1) + last_clock**process.power_exponent)
        self.machines_total += built
        if not listed:
            return
        buildings = self.game.buildings
        row: ProcessRow = {
            "pid": process.pid,
            "kind": process.kind,
            "label": process.label,
            "building": buildings[process.building].name
            if process.building in buildings
            else process.building or "",
            "building_id": process.building,
            "recipe": process.recipe,
            "purity": process.purity,
            "machines": built,
            "machine_equivalents": round(equivalents, 4),
            "clock": round(effective_clock, 6),
            "sloops": process.sloops,
            "mw": round(exact_mw, 2),
            "mw_linear": round(rate_scale * process.mw, 2),
            # Already include clock and somersloop boost: never re-derive them from the recipe.
            "rates": {
                item: round(rate * rate_scale, 4)
                for item, rate in process.rates.items()
                if abs(rate * rate_scale) > _EPS
            },
        }
        if last_clock is not None:
            row["last_clock"] = round(last_clock, 6)
        if option is not None:
            row["overclock_option"] = option
        self.rows.append(row)


def pool_extractor_modes(
    processes: list[Process], x: F64Grid, columns: Columns
) -> dict[str, tuple[float, float]]:
    """Per extractor group, ``(machines, node-units)`` summed over its clock modes (§8.2c)."""
    pooled: dict[str, tuple[float, float]] = {}
    for i, process in enumerate(processes):
        count = float(x[columns.process(i)])
        if count > _EPS and process.kind == "extractor" and process.group:
            machines, units = pooled.get(process.group, (0.0, 0.0))
            pooled[process.group] = (machines + count, units + count * process.clock)
    return pooled


def _fixed_draw(process: Process, built: int, units: float) -> float:
    return max(0.0, -built * process.mw_at_full * ((units / built) ** process.power_exponent))


def build_rows(
    scenario: Scenario, processes: list[Process], x: F64Grid, columns: Columns
) -> BuildRows:
    """Whole machines at a derived clock per column, extractor modes folded, spreadable rows
    read out at the scenario's horizon."""
    game = scenario.game
    pooled = pool_extractor_modes(processes, x, columns)
    writer = _RowWriter(game)
    out = BuildRows()
    folded: set[str] = set()
    spread: list[SpreadableRow] = []
    deferred: list[float] = []
    fixed_machines, fixed_draw = 0, 0.0
    for i, process in enumerate(processes):
        count = float(x[columns.process(i)])
        if count <= _EPS:
            continue

        if process.kind == "extractor" and process.group:
            if process.group in folded:
                continue
            folded.add(process.group)
            machines, units = pooled[process.group]
            # The count is sum(v) and the clock absorbs the rate, so the cap holds (§8.2c).
            built = max(1, math.ceil(machines - 1e-9))
            fixed_machines += built
            fixed_draw += _fixed_draw(process, built, units)
            writer.write(process, built, units / built, machines, units / process.clock)
            continue

        negligible = all(abs(rate * count) < NEGLIGIBLE_IPM for rate in process.rates.values())
        if negligible:
            out.dropped.append(
                (
                    process.label,
                    max((abs(rate * count) for rate in process.rates.values()), default=0.0),
                )
            )

        # ceil(v) machines all at v/ceil(v): exact and power-optimal (§8.4).
        built = max(1, math.ceil(count - 1e-9))
        units = process.clock * count
        if spreadable(process) and not negligible:
            spread.append(SpreadableRow(process, units, game.buildings.get(process.building or "")))
            deferred.append(count)
            continue
        fixed_machines += built
        fixed_draw += _fixed_draw(process, built, units)
        writer.write(process, built, units / built, count, count, listed=not negligible)

    # The LP column already runs at the horizon's best clock; overclock-last is the one
    # readout choice the LP never sees.
    per_shard = max(game.clock_shards().values(), default=0.0)
    readout = horizon_readout(scenario, spread, scenario.payback_hours, per_shard)
    for i, (row, count) in enumerate(zip(spread, deferred, strict=True)):
        option = overclock_option(scenario, row, i, readout) if i in readout.options else None
        if option is not None and option["applied"]:
            built, top, _ = readout.picks[i]
            writer.write(
                row.process, built, row.units / built, count, count, last_clock=top, option=option
            )
        else:
            built = max(1, math.ceil(count - 1e-9))
            writer.write(row.process, built, row.units / built, count, count, option=option)
    writer.rows.sort(key=lambda d: -abs(d["mw"]))

    out.rows = writer.rows
    out.machines_total = writer.machines_total
    out.payback_curve = payback_curve(scenario, (fixed_machines, fixed_draw), spread, per_shard)
    out.overclock = overclock_view(scenario, spread, readout)
    return out


def binding_constraints(
    scenario: Scenario, processes: list[Process], x: F64Grid, columns: Columns
) -> list[str]:
    """Node caps and raw caps the solve used up, a clock-mode group tested as one (§8.2c)."""
    binding: list[str] = []
    group_used: dict[str, float] = {}
    group_cap: dict[str, float] = {}
    for i, process in enumerate(processes):
        if process.max_count is None or process.max_count <= 0:
            continue
        if process.group:
            group_used[process.group] = group_used.get(process.group, 0.0) + x[columns.process(i)]
            group_cap[process.group] = process.max_count
        elif x[columns.process(i)] >= process.max_count - 1e-6:
            binding.append(f"{process.label}: all {process.max_count:g} available")
    labelled = {process.group: process.label for process in processes if process.group}
    for key, used in group_used.items():
        if used >= group_cap[key] - 1e-6:
            binding.append(f"{labelled[key]}: all {group_cap[key]:g} available")
    for j, item in enumerate(columns.raw_items):
        # The same relative slack build_scenario adds to a supplied rate.
        cap = scenario.raw_caps[item]
        if x[columns.raw(j)] >= cap - max(1e-6, abs(cap) * 1e-6):
            binding.append(f"{scenario.game.item_name(item)} capped at {cap:g}")
    return binding


def logistics(
    scenario: Scenario,
    processes: list[Process],
    x: F64Grid,
    columns: Columns,
    raw_used: dict[str, float] | None = None,
) -> list[LogisticsRow]:
    """Each item's flow and the belt or pipe lines it needs; reported, never constrained (§8.4)."""
    moved: dict[str, float] = dict(raw_used or {})
    for i, process in enumerate(processes):
        count = float(x[columns.process(i)])
        if count <= _EPS:
            continue
        for item, rate in process.rates.items():
            if rate > 0:
                moved[item] = moved.get(item, 0.0) + rate * count

    out: list[LogisticsRow] = []
    for item, rate in sorted(moved.items(), key=lambda kv: -kv[1]):
        if rate <= _EPS:
            continue
        known = scenario.game.items.get(item)
        line = carrier_for(scenario.game, item, scenario.belt_ipm, scenario.pipe_m3min)
        out.append(
            {
                "item": item,
                "name": known.name if known else item,
                "rate": round(rate, 2),
                "carrier": line.kind,
                "unit": line.unit,
                "capacity_per_line": line.capacity,
                "lines": line.lines_for(rate),
            }
        )
    return out


def solution_warnings(
    scenario: Scenario,
    processes: list[Process],
    x: F64Grid,
    columns: Columns,
    dropped: list[tuple[str, float]],
    flows: list[LogisticsRow],
    sunk: dict[str, float],
    grid_draw: float,
) -> list[str]:
    """What the build table leaves out, and what the plan needs beyond its own machines."""
    game = scenario.game
    warnings: list[str] = []
    if dropped:
        worst = max(rate for _, rate in dropped)
        warnings.append(
            f"{len(dropped)} process(es) contribute under {NEGLIGIBLE_IPM}/min "
            f"({', '.join(sorted({name for name, _ in dropped}))}) and are left out of "
            f"the build table -- a whole machine at 0.01% clock reads as an instruction. "
            f"They ARE counted in the building total; the largest makes "
            f"{worst:.4f}/min"
        )

    heavy = [entry for entry in flows if (entry["lines"] or 0) > 1]
    if heavy:
        # Say how many are cut, or the shown four read as all of them.
        more = "" if len(heavy) <= 4 else f", and {len(heavy) - 4} more"
        warnings.append(
            "multi-line logistics: "
            + ", ".join(
                f"{e['name']} {e['rate']:g}{e['unit']} needs {e['lines']} {e['carrier']}s"
                for e in heavy[:4]
            )
            + more
        )

    if sunk:
        warnings.append(
            "plan sinks "
            + ", ".join(f"{v:g} {game.item_name(k)}/min" for k, v in sunk.items())
            + " -- needs a belt to an AWESOME Sink or the line stalls"
        )
    if grid_draw > _EPS:
        warnings.append(
            "plan draws from the existing grid (it is not self-powered); grid_import_MW "
            "says how much"
        )
    # Only a sub-100% MODE is spreading; a derived ratio clock is the normal build (§8.4).
    if min(scenario.clocks) < 1.0:
        used_low = [
            process
            for i, process in enumerate(processes)
            if process.clock < 1.0 and x[columns.process(i)] > _EPS
        ]
        if used_low:
            price = (
                f"the {scenario.payback_hours:g} h payback horizon"
                if scenario.payback_hours * scenario.power_price > 0
                else f"{scenario.machine_cost_mw:g} MW/machine"
            )
            warnings.append(
                f"{len(used_low)} process(es) use a sub-100% clock MODE: this spreads "
                f"throughput over more machines to save power, priced at {price}"
            )
    return warnings

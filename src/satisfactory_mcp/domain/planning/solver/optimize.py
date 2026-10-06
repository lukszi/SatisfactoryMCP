"""LP/MILP factory optimizer.

Formulation
-----------
One variable per *process* -- a recipe at a fixed (clock, sloops) mode, an extractor
on a given resource+purity, a generator on a given fuel, or a sink. Plus one
variable per raw input, export and sink flow.

Power is modelled as a pseudo-item ``__MW__`` so the power balance is just another
mass-balance row. That keeps every objective linear.

**Every item's balance is an equality.** ``net >= 0`` is wrong: a byproduct with no
consumer does not vanish, it fills a pipe and stalls the line. Exports and sinks are
explicit whitelists, so anything not exportable or sinkable must be consumed exactly.

Two-phase lexicographic solve is mandatory: with machine counts only bounded below,
any larger count is equally optimal, and an unguarded solve returns counts of 1e12.
Phase 1 optimises the goal; phase 2 pins it and minimises machines.

Clocks are discrete modes, never a continuous variable: power is ``c**1.32``, which
is non-convex, and for fixed throughput power strictly decreases in machine count,
so a min-power objective would drive machines to infinity.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import LinearConstraint, milp

from ....core import solverlane
from ....core.gamedata.constants import AWESOME_SINK_MW
from .carrier import carrier_for
from .model import MW, NEGLIGIBLE_IPM, Process, Scenario, Solution
from .overclock import (
    _option,
    _overclock_view,
    _payback_curve,
    _readout,
    _Row,
    _spreadable,
    machine_mw,
)
from .processes import build_processes

__all__ = ["free_lunch_audit", "solve"]

_EPS = 1e-7


def _logistics(
    sc: Scenario, procs: list[Process], x, col_p, raw_used: dict[str, float] | None = None
) -> list[dict]:
    """How much of each item moves, and how many belts or pipes that needs.

    Reported rather than constrained. A throughput CAP would be wrong here: a plan
    can legitimately run several parallel lines, and the game has no global limit --
    what a planner actually needs to know is how many lines, so the logistics burden
    is visible instead of hidden inside a ratio.
    """
    moved: dict[str, float] = dict(raw_used or {})
    for i, p in enumerate(procs):
        v = float(x[col_p(i)])
        if v <= _EPS:
            continue
        for item, rate in p.rates.items():
            if rate > 0:
                moved[item] = moved.get(item, 0.0) + rate * v

    out: list[dict] = []
    for item, rate in sorted(moved.items(), key=lambda kv: -kv[1]):
        if rate <= _EPS:
            continue
        it = sc.game.items.get(item)
        line = carrier_for(sc.game, item, sc.belt_ipm, sc.pipe_m3min)
        out.append(
            {
                "item": item,
                "name": it.name if it else item,
                "rate": round(rate, 2),
                "carrier": line.kind,
                "unit": line.unit,
                "capacity_per_line": line.capacity,
                "lines": line.lines_for(rate),
            }
        )
    return out


def _sinkable(sc: Scenario, item_id: str) -> bool:
    if not sc.allow_sinks:
        return False
    it = sc.game.items.get(item_id)
    return bool(it and it.sinkable)


def solve(sc: Scenario) -> Solution:
    g = sc.game
    procs = build_processes(sc)
    if not procs:
        # `warnings=` by name, not by position. The tenth positional field is
        # machine_penalty_mw, so every early return here used to file its reason
        # under a float and hand the caller an INFEASIBLE with no reason attached --
        # which is precisely the bare INFEASIBLE that made diagnosing one expensive.
        return Solution(
            "infeasible", 0.0, 0.0, [], {}, {}, {}, 0.0, 0.0, warnings=["no processes available"]
        )

    items = sorted({i for p in procs for i in p.rates})
    raw_items = sorted(sc.raw_caps)
    export_items = sorted(set(sc.exports) | set(sc.export_minimums))
    sink_items = sorted(i for i in items if _sinkable(sc, i))

    exporting_power = MW in export_items
    # A power plant must be self-contained; anything else may draw from the grid.
    grid_cap = (
        0.0 if exporting_power else (np.inf if sc.grid_import_mw is None else sc.grid_import_mw)
    )

    nP, nR, nE, nS = len(procs), len(raw_items), len(export_items), len(sink_items)
    n = nP + nR + nE + nS + 1  # trailing column: grid import

    def col_p(i: int) -> int:
        return i

    def col_r(i: int) -> int:
        return nP + i

    def col_e(i: int) -> int:
        return nP + nR + i

    def col_s(i: int) -> int:
        return nP + nR + nE + i

    col_grid = n - 1

    rows: list[np.ndarray] = []
    rhs: list[float] = []

    # ---- per-item equality balance (the crux) --------------------------
    # Every EXPORT gets a row, including one that no process touches. Without it the
    # export column exists with nothing tying it to production, and an unconstrained
    # column is not merely useless -- it is wrong in two different ways. `max_item` on
    # an unmakeable target pushes it up forever and HiGHS reports UNBOUNDED, which
    # surfaces as a bare INFEASIBLE. Worse, an `export_minimums` floor is then satisfied
    # out of thin air: min_power with a 100/min floor on Nitrogen Gas SUCCEEDED and
    # reported `exports: Nitrogen Gas=100` on a world with no recipe and no node for it.
    # With the row, such an export is pinned to 0 and a floor above 0 is honestly
    # infeasible.
    #
    # MW is excluded on purpose: it balances on the power row below, and a second row
    # here would constrain generation to zero.
    balanced = items + [i for i in raw_items if i not in items]
    balanced += [i for i in export_items if i != MW and i not in items and i not in raw_items]
    for item in balanced:
        row = np.zeros(n)
        for i, p in enumerate(procs):
            row[col_p(i)] = p.net(item)
        if item in raw_items:
            row[col_r(raw_items.index(item))] = 1.0
        if item in export_items:
            row[col_e(export_items.index(item))] = -1.0
        if item in sink_items:
            row[col_s(sink_items.index(item))] = -1.0
        rows.append(row)
        rhs.append(0.0)

    # ---- power balance, as just another item ---------------------------
    power_row = np.zeros(n)
    for i, p in enumerate(procs):
        power_row[col_p(i)] = p.mw
    # Sinking costs power: 30 MW per AWESOME Sink, one sink per belt line.
    for j, item in enumerate(sink_items):
        power_row[col_s(j)] = -AWESOME_SINK_MW / max(sc.belt_ipm, 1.0)
    power_row[col_grid] = 1.0  # grid import supplies MW
    if exporting_power:
        power_row[col_e(export_items.index(MW))] = -1.0
    rows.append(power_row)
    rhs.append(0.0)

    A_eq = np.vstack(rows)
    constraints = [LinearConstraint(A_eq, np.array(rhs), np.array(rhs))]

    lb = np.zeros(n)
    ub = np.full(n, np.inf)
    grouped: dict[str, list[int]] = {}
    for i, p in enumerate(procs):
        if p.max_count is None:
            continue
        if p.group is None:
            ub[col_p(i)] = p.max_count
        else:
            grouped.setdefault(p.group, []).append(i)
    # A group is one set of physical machines offered at several clocks. Capping each
    # mode separately would let the solver run the same nodes once per mode.
    for members in grouped.values():
        cap = min(procs[i].max_count for i in members)
        if len(members) == 1:
            ub[col_p(members[0])] = cap
            continue
        row = np.zeros(n)
        for i in members:
            row[col_p(i)] = 1.0
            ub[col_p(i)] = cap
        constraints.append(LinearConstraint(row, -np.inf, cap))
    for j, item in enumerate(raw_items):
        ub[col_r(j)] = sc.raw_caps[item]
    for j, item in enumerate(export_items):
        if item in sc.export_minimums:
            lb[col_e(j)] = sc.export_minimums[item]
    ub[col_grid] = grid_cap

    # ---- recipe cycles ------------------------------------------------
    if sc.recycle_once:
        named = {i for i, p in enumerate(procs) if p.pid in sc.recycle_once}
        # Items the named set both makes and eats -- the loop the caller pointed at.
        made = {i for x in named for i, rate in procs[x].rates.items() if rate > 0 and i != MW}
        eaten = {i for x in named for i, rate in procs[x].rates.items() if rate < 0 and i != MW}
        for item in sorted(made & eaten):
            row = np.zeros(n)
            for i, p in enumerate(procs):
                rate = p.rates.get(item, 0.0)
                if i in named and rate < 0:
                    row[col_p(i)] += -rate  # consumed inside the loop
                elif i not in named and rate > 0:
                    row[col_p(i)] -= rate  # the single pass of outside feedstock
            # consumed_inside <= produced_outside. The loop may run on material the rest
            # of the plant made, and may not run on its own output -- which is "once"
            # exactly, with no pass counting anywhere.
            constraints.append(LinearConstraint(row, -np.inf, 0.0))

    # ---- somersloop budget --------------------------------------------
    if sc.sloop_budget:
        row = np.zeros(n)
        used = False
        for i, p in enumerate(procs):
            if p.sloops:
                row[col_p(i)] = p.sloops
                used = True
        if used:
            constraints.append(LinearConstraint(row, -np.inf, sc.sloop_budget))

    if sc.max_machines is not None:
        row = np.zeros(n)
        for i in range(nP):
            row[col_p(i)] = 1.0
        constraints.append(LinearConstraint(row, -np.inf, sc.max_machines))

    integrality = np.zeros(n)
    if sc.integral:
        for i in range(nP):
            integrality[col_p(i)] = 1

    # ---- phase 1: the goal --------------------------------------------
    c = np.zeros(n)
    if sc.objective == "max_mw":
        if MW not in export_items:
            return Solution(
                "infeasible",
                0.0,
                0.0,
                [],
                {},
                {},
                {},
                0.0,
                0.0,
                warnings=["objective max_mw requires __MW__ in exports"],
            )
        c[col_e(export_items.index(MW))] = -1.0
    elif sc.objective == "max_item":
        if not sc.target_item or sc.target_item not in export_items:
            return Solution(
                "infeasible",
                0.0,
                0.0,
                [],
                {},
                {},
                {},
                0.0,
                0.0,
                warnings=[f"objective max_item requires {sc.target_item!r} in exports"],
            )
        c[col_e(export_items.index(sc.target_item))] = -1.0
    elif sc.objective == "min_raw":
        # Raw material arrives two ways and both must be priced. `raw_caps` gives
        # free-standing raw variables, but the normal path is extractor PROCESSES --
        # and build_scenario only ever populates the latter. Counting raw_ alone made
        # min_raw minimise an empty row, so it silently returned 0 for every input.
        for j in range(nR):
            c[col_r(j)] = sc.raw_weights.get(raw_items[j], 1.0)
        for i, p in enumerate(procs):
            if p.kind == "extractor":
                c[col_p(i)] = sum(
                    rate * sc.raw_weights.get(item, 1.0)
                    for item, rate in p.rates.items()
                    if rate > 0
                )
    elif sc.objective == "min_machines":
        for i in range(nP):
            c[col_p(i)] = 1.0
    elif sc.objective == "min_power":
        for i, p in enumerate(procs):
            if p.mw < 0:
                c[col_p(i)] = -p.mw
    else:
        return Solution(
            "infeasible",
            0.0,
            0.0,
            [],
            {},
            {},
            {},
            0.0,
            0.0,
            warnings=[f"unknown objective {sc.objective!r}"],
        )

    # ---- price machines when the objective is power --------------------
    #
    # Underclocking is allowed, not banned -- but it is never free. Its only benefit
    # is power efficiency and its only cost is buildings, so without a price a
    # max-power solve is ill-posed and drives machine count upward for ever.
    #
    # Applied only to the power objectives: for max_item / min_raw, underclocking
    # gives no benefit at all (throughput is linear in machines x clock), so it is
    # never selected and needs no penalty.
    # Keep the pure goal so the reported objective_value is not contaminated by the
    # penalty. Reading the penalised value as the objective understated max_mw by
    # ~5000 MW and made a per-unit cost derived from it wrong -- two separate tools
    # tripped on exactly this.
    c_goal = c.copy()
    if sc.objective in ("max_mw", "min_power"):
        for i, p in enumerate(procs):
            c[col_p(i)] += machine_mw(sc, p.building)

    res = solverlane.run(
        lambda: milp(c=c, constraints=constraints, integrality=integrality, bounds=(lb, ub))
    )
    if not res.success or res.x is None:
        return Solution(
            "infeasible",
            0.0,
            0.0,
            [],
            {},
            {},
            {},
            0.0,
            0.0,
            warnings=[f"phase 1 infeasible: {res.message}"],
        )
    goal = float(c @ res.x)

    # ---- phase 2: pin the goal, minimise machines ----------------------
    warnings: list[str] = []
    x = res.x
    if sc.objective not in ("min_machines",):
        # A MILP optimum is not exact to 1e-6; too tight a pin makes phase 2
        # infeasible and silently loses the machine minimisation.
        tol = max(1e-6, abs(goal) * 1e-7) if not sc.integral else max(1e-4, abs(goal) * 1e-6)
        pin = LinearConstraint(c.reshape(1, -1), goal - tol, goal + tol)
        c2 = np.zeros(n)
        per_mw = sc.payback_hours * sc.power_price
        for i, p in enumerate(procs):
            # At a horizon the machines are priced in points, their power as running cost.
            c2[col_p(i)] = (
                max(sc.build_points.get(p.building or "", 0.0), 1.0) + per_mw * max(0.0, -p.mw)
                if per_mw > 0
                else 1.0
            )
        res2 = solverlane.run(
            lambda: milp(
                c=c2,
                constraints=[*constraints, pin],
                integrality=integrality,
                bounds=(lb, ub),
            )
        )
        if res2.success and res2.x is not None:
            x = res2.x
        else:
            warnings.append("phase 2 (minimise machines) failed; counts are not minimal")

    # ---- read out -----------------------------------------------------
    # Offering a node set at several clocks creates one column per mode, and the modes
    # share ONE node cap, so the LP may split a solve across them arbitrarily: 0.615
    # machine-equivalents at 100% plus 0.0201 at 150% is the same extraction as 0.645
    # at 100%. Left alone that prints two rows with an IDENTICAL label, the second a
    # whole miner at 2% clock, which reads as a real build instruction and is not one.
    #
    # TWO quantities have to be carried, and conflating them broke the cap. The node cap
    # constrains MACHINE COUNT -- sum(v) -- while extraction is NODE-UNITS, sum(v*clock).
    # Re-expressing pooled node-units at one mode's clock preserved the rate and silently
    # inflated the count: water capped at 54 came back as 64 machines at 149.7%, because
    # 54 machines' worth of units re-read at a lower clock needs more machines. It only
    # looked right at 27 because that solution happened to use a single mode.
    #
    # So: keep sum(v) as the count and let the clock absorb the rate.
    pooled: dict[str, tuple[float, float]] = {}
    for i, p in enumerate(procs):
        v = float(x[col_p(i)])
        if v > _EPS and p.kind == "extractor" and p.group:
            count, units = pooled.get(p.group, (0.0, 0.0))
            pooled[p.group] = (count + v, units + v * p.clock)

    out_procs = []
    machines_total = 0.0
    exact_mw_total = 0.0
    folded: set[str] = set()
    dropped: list[tuple[str, float]] = []
    spread: list[_Row] = []
    deferred: list[float] = []
    fixed_machines, fixed_draw = 0, 0.0

    def emit(
        p,
        built: float,
        effective_clock: float,
        equivalents: float,
        rate_scale: float,
        listed: bool = True,
        last_clock: float | None = None,
        option: dict | None = None,
    ):
        """One build row.

        ``rate_scale`` is separate from ``equivalents`` because they are different
        quantities once clock modes are pooled: p.rates already includes p.clock, so a
        rate needs units/p.clock, while the machine count needs sum(v). Using one for
        both is exactly the bug that let a 54-extractor cap report 64 machines.
        """
        nonlocal machines_total, exact_mw_total
        exact_mw = built * p.mw_at_full * (effective_clock**p.power_exponent)
        if last_clock is not None:
            exact_mw = p.mw_at_full * ((built - 1) + last_clock**p.power_exponent)
        # Counted whether or not it is printed. Omitting a row is a PRESENTATION
        # decision; letting it change machines_total would have silently moved a
        # headline number compare_recipe_options ranks routes by -- it turned a
        # measured "9 buildings" into 8.
        machines_total += built
        exact_mw_total += exact_mw
        if not listed:
            return
        out_procs.append(
            {
                "pid": p.pid,
                "kind": p.kind,
                "label": p.label,
                "building": g.buildings[p.building].name
                if p.building in g.buildings
                else p.building,
                "building_id": p.building,
                "recipe": p.recipe,
                "purity": p.purity,
                "machines": built,
                "machine_equivalents": round(equivalents, 4),
                "clock": round(effective_clock, 6),
                "sloops": p.sloops,
                # Linear power (v * p.mw) is what the LP optimised; below 100% clock
                # it is a conservative OVER-estimate, so the exact figure is never
                # worse than what the solve promised.
                "mw": round(exact_mw, 2),
                "mw_linear": round(rate_scale * p.mw, 2),
                # Net per-minute item rates for the whole process. Already include
                # clock and somersloop boost, so downstream consumers must not
                # re-derive them from the recipe.
                "rates": {
                    k: round(r * rate_scale, 4)
                    for k, r in p.rates.items()
                    if abs(r * rate_scale) > _EPS
                },
            }
        )
        if last_clock is not None:
            out_procs[-1]["last_clock"] = round(last_clock, 6)
        if option is not None:
            out_procs[-1]["overclock_option"] = option

    for i, p in enumerate(procs):
        v = float(x[col_p(i)])
        if v <= _EPS:
            continue

        if p.kind == "extractor" and p.group:
            if p.group in folded:
                continue
            folded.add(p.group)
            count, units = pooled[p.group]
            # ceil(sum(v)) can never exceed the cap, because the cap bounds sum(v)
            # itself. The clock carries the extraction: built * clock == units, so the
            # rate is unchanged, and clock <= the highest mode because units <= count
            # times that mode.
            built = max(1, math.ceil(count - 1e-9))
            fixed_machines += built
            fixed_draw += max(0.0, -built * p.mw_at_full * ((units / built) ** p.power_exponent))
            emit(p, built, units / built, count, units / p.clock)
            continue

        # A degenerate basis can leave a recipe column at a hair -- 0.0001
        # machine-equivalents of Residual Rubber, making 0.0017/min, one item every ten
        # hours. Unlike the extractor case above this is NOT a clock-mode split and
        # cannot be folded into anything; there is one mode. It is dropped, but never
        # silently: the omitted flow is reported, because a plan whose printed rows do
        # not quite balance is only acceptable if it says by how much.
        negligible = all(abs(r * v) < NEGLIGIBLE_IPM for r in p.rates.values())
        if negligible:
            dropped.append((p.label, max((abs(r * v) for r in p.rates.values()), default=0.0)))

        # v is throughput in machine-equivalents. The build is ceil(v) whole machines
        # all clocked to v/ceil(v) -- exact, always a clean ratio, and power-optimal
        # for that throughput because c**k is convex, so a uniform clock beats any
        # mix. This is why ratio underclocking needs no solver mode.
        built = max(1, math.ceil(v - 1e-9))
        units = p.clock * v
        if _spreadable(p) and not negligible:
            spread.append(_Row(p, units, g.buildings.get(p.building or "")))
            deferred.append(v)
            continue
        fixed_machines += built
        fixed_draw += max(0.0, -built * p.mw_at_full * ((units / built) ** p.power_exponent))
        emit(p, built, units / built, v, v, listed=not negligible)

    # The LP column already runs at the horizon's best clock, so its ceil(v) is the spread;
    # the overclock-last candidate is the one readout choice the LP never sees.
    per_shard = max(g.clock_shards().values(), default=0.0)
    read = _readout(sc, spread, sc.payback_hours, per_shard)
    for i, (row, v) in enumerate(zip(spread, deferred, strict=True)):
        option = _option(sc, row, i, read) if i in read["options"] else None
        if option is not None and option["applied"]:
            built, top, _ = read["picks"][i]
            emit(row.p, built, row.units / built, v, v, last_clock=top, option=option)
        else:
            built = max(1, math.ceil(v - 1e-9))
            emit(row.p, built, row.units / built, v, v, option=option)
    out_procs.sort(key=lambda d: -abs(d["mw"]))

    raw_used = {raw_items[j]: round(float(x[col_r(j)]), 4) for j in range(nR) if x[col_r(j)] > _EPS}
    exports = {
        export_items[j]: round(float(x[col_e(j)]), 4) for j in range(nE) if x[col_e(j)] > _EPS
    }
    sunk = {sink_items[j]: round(float(x[col_s(j)]), 4) for j in range(nS) if x[col_s(j)] > _EPS}
    pure_goal = float(c_goal @ x)
    machine_penalty = round(float((c - c_goal) @ x), 4)
    grid_draw = round(float(x[col_grid]), 2)
    net_mw = exports.get(MW, 0.0) - grid_draw

    binding = []
    # Grouped processes share ONE cap across their clock modes, so the test has to be on
    # the group total. Checked per mode, a solve that spreads 54 extractors over two
    # clocks leaves every column below the cap and reports nothing binding -- while the
    # cap is in fact fully consumed. That is how a hard constraint went unmentioned.
    group_used: dict[str, float] = {}
    group_cap: dict[str, float] = {}
    for i, p in enumerate(procs):
        if p.max_count is None or p.max_count <= 0:
            continue
        if p.group:
            group_used[p.group] = group_used.get(p.group, 0.0) + x[col_p(i)]
            group_cap[p.group] = p.max_count
        elif x[col_p(i)] >= p.max_count - 1e-6:
            binding.append(f"{p.label}: all {p.max_count:g} available")
    labelled = {p.group: p.label for p in procs if p.group}
    for key, used in group_used.items():
        if used >= group_cap[key] - 1e-6:
            binding.append(f"{labelled[key]}: all {group_cap[key]:g} available")
    for j, item in enumerate(raw_items):
        # Tolerance matched to the slack `build_scenario` adds to a supplied rate. A cap
        # nudged up by 1e-6 relative, compared with a fixed 1e-6 absolute, meant an input
        # consumed to the last drop stopped reporting as binding -- the constraint still
        # bit, and the response stopped saying so.
        cap = sc.raw_caps[item]
        if x[col_r(j)] >= cap - max(1e-6, abs(cap) * 1e-6):
            binding.append(f"{g.item_name(item)} capped at {cap:g}")

    if dropped:
        worst = max(rate for _, rate in dropped)
        warnings.append(
            f"{len(dropped)} process(es) contribute under {NEGLIGIBLE_IPM}/min "
            f"({', '.join(sorted({name for name, _ in dropped}))}) and are left out of "
            f"the build table -- a whole machine at 0.01% clock reads as an instruction. "
            f"They ARE counted in the building total; the largest makes "
            f"{worst:.4f}/min"
        )

    logistics = _logistics(sc, procs, x, col_p, raw_used)
    heavy = [entry for entry in logistics if (entry["lines"] or 0) > 1]
    if heavy:
        # Truncated because a warning is one line, but say so: an unmarked cut here
        # reads as "these four are all of them", and the fifth pipe is still real.
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
            + ", ".join(f"{v:g} {g.item_name(k)}/min" for k, v in sunk.items())
            + " -- needs a belt to an AWESOME Sink or the line stalls"
        )
    if grid_draw > _EPS:
        warnings.append(
            "plan draws from the existing grid (it is not self-powered); grid_import_MW "
            "says how much"
        )
    # Only warn about SPREADING, never about ratio clocks. A derived clock of 99.4%
    # just means 176 machines carry 175 machines' worth of throughput -- that is the
    # normal, exact way to build, not a tradeoff the caller should second-guess.
    if min(sc.clocks) < 1.0:
        used_low = [p for i, p in enumerate(procs) if p.clock < 1.0 and x[col_p(i)] > _EPS]
        if used_low:
            price = (
                f"the {sc.payback_hours:g} h payback horizon"
                if sc.payback_hours * sc.power_price > 0
                else f"{sc.machine_cost_mw:g} MW/machine"
            )
            warnings.append(
                f"{len(used_low)} process(es) use a sub-100% clock MODE: this spreads "
                f"throughput over more machines to save power, priced at {price}"
            )

    return Solution(
        status="optimal",
        objective_value=round(-pure_goal if sc.objective.startswith("max") else pure_goal, 4),
        net_mw=net_mw,
        processes=out_procs,
        raw_used=raw_used,
        exports=exports,
        sunk=sunk,
        machines_total=round(machines_total, 3),
        grid_import_mw=grid_draw,
        machine_penalty_mw=machine_penalty,
        logistics=logistics,
        warnings=warnings,
        binding=binding,
        payback_curve=_payback_curve(sc, (fixed_machines, fixed_draw), spread, per_shard),
        overclock=_overclock_view(sc, spread, read),
    )


def free_lunch_audit(sc: Scenario) -> tuple[bool, float]:
    """Strip every matter source and maximise MW. Must return exactly 0.

    A non-zero result means some recipe cycle creates matter from nothing -- which a
    mass-balanced model can still do if a column is malformed. Run on every solve.
    """
    probe = Scenario(
        game=sc.game,
        recipes=sc.recipes,
        objective="max_mw",
        exports=(MW,),
        raw_caps={},
        extractor_nodes={},
        allow_sinks=False,
        clocks=sc.clocks,
        buildings_available=sc.buildings_available,
    )
    sol = solve(probe)
    value = sol.net_mw if sol.ok else 0.0
    return (abs(value) < 1e-6), value

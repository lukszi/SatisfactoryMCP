"""Which limit makes a plan INFEASIBLE: lift one family of limits at a time and re-solve.

Only a limit whose lifting makes the plan solve is named. docs/planning.md §8.2a.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from ....core.gamedata.model import GameData
from ..solver.model import MW, Scenario, Solution
from ..solver.optimize import solve
from ..solver.processes import build_processes
from ..solver.scenario import PlanRequest
from . import supply

__all__ = ["Diagnosis", "explain"]

_EPS = 1e-6

#: Lifted caps: big enough that no plan in scope reaches them, finite so the LP stays bounded.
_LIFTED = 1e6

#: Single-item confirmations per family, each one full solve.
_CONFIRM_BUDGET = 4


@dataclass
class Diagnosis:
    """Report lines for an infeasible plan, and how many extra solves they cost."""

    lines: list[str] = field(default_factory=list[str])
    solves: int = 0


@dataclass
class _Probe:
    """One way of loosening the scenario, and what to name when it rescues the plan."""

    family: str
    loosen: Callable[[Scenario], Scenario]
    suspects: Callable[[Solution], list[str]]
    single: Callable[[Scenario, str], Scenario]


def _capacity(sc: Scenario) -> dict[str, float]:
    """Most of each resource the scope can extract or import per minute, at its best clock."""
    best: dict[str, dict[str, float]] = {}
    for p in build_processes(sc):
        if p.kind != "extractor" or p.max_count is None:
            continue
        for item, rate in p.rates.items():
            if rate > _EPS:
                group = best.setdefault(item, {})
                key = p.group or p.pid
                group[key] = max(group.get(key, 0.0), rate * p.max_count)
    out = {item: sum(groups.values()) for item, groups in best.items()}
    for item, cap in sc.raw_caps.items():
        out[item] = out.get(item, 0.0) + cap
    return out


def _more_raw(sc: Scenario, only: str | None = None) -> Scenario:
    nodes = {
        key: (int(_LIFTED) if only in (None, key[1]) else count)
        for key, count in sc.extractor_nodes.items()
    }
    caps = {k: (_LIFTED if only in (None, k) else v) for k, v in sc.raw_caps.items()}
    if only is not None and only not in caps and not any(k[1] == only for k in nodes):
        caps[only] = _LIFTED
    return replace(sc, extractor_nodes=nodes, raw_caps=caps)


def _raw_probe(sc: Scenario, missing: tuple[str, ...]) -> _Probe:
    capacity = _capacity(sc)

    def loosen(s: Scenario) -> Scenario:
        lifted = _more_raw(s)
        return replace(lifted, raw_caps={**lifted.raw_caps, **dict.fromkeys(missing, _LIFTED)})

    def suspects(sol: Solution) -> list[str]:
        drawn = sol.drawn_raw()
        return [
            item
            for item, used in sorted(drawn.items(), key=lambda kv: -kv[1])
            if used > capacity.get(item, 0.0) * (1 + 1e-6) + 1e-4
        ]

    return _Probe("raw", loosen, suspects, lambda s, item: _more_raw(s, item))


def _outlet_probe(sc: Scenario) -> _Probe:
    produced = sorted(
        {i for p in build_processes(sc) for i, r in p.rates.items() if r > _EPS and i != MW}
    )
    openable = tuple(i for i in produced if i not in sc.exports)
    leaves = set(sc.exports) | set(sc.export_minimums)
    if sc.allow_sinks:
        leaves |= {cls for cls, it in sc.game.items.items() if it.sinkable}

    def suspects(sol: Solution) -> list[str]:
        out = [(i, r) for i, r in sol.exports.items() if i not in leaves and r > _EPS]
        return [i for i, _ in sorted(out, key=lambda kv: -kv[1])]

    return _Probe(
        "byproduct",
        lambda s: replace(s, exports=(*s.exports, *openable)),
        suspects,
        lambda s, item: replace(s, exports=(*s.exports, item)),
    )


def _power_probe() -> _Probe:
    def loosen(s: Scenario) -> Scenario:
        objective = "min_power" if s.objective == "max_mw" else s.objective
        exports = tuple(i for i in s.exports if i != MW)
        return replace(s, exports=exports, objective=objective, grid_import_mw=None)

    return _Probe("power", loosen, lambda _sol: [], lambda s, _item: loosen(s))


def _confirm(sc: Scenario, probe: _Probe, suspects: list[str], diag: Diagnosis) -> list[str]:
    """The suspects whose limit alone, lifted, makes the plan solve."""
    confirmed: list[str] = []
    for item in suspects[:_CONFIRM_BUDGET]:
        diag.solves += 1
        if solve(probe.single(sc, item)).ok:
            confirmed.append(item)
    return confirmed


def _outlet_fix(sc: Scenario, items: list[str]) -> str:
    """How to let ``items`` leave; sinking is offered only where it is off and would take one."""
    if not sc.allow_sinks and any(
        (known := sc.game.items.get(i)) is not None and known.sinkable for i in items
    ):
        return "add it to exports, allow_sinks for a solid, or explain_byproducts for consumers"
    return (
        "add it to exports, or explain_byproducts for consumers; "
        "it cannot go to an AWESOME Sink (fluids never can)"
    )


def _line(sc: Scenario, family: str, items: list[str], capacity: dict[str, float]) -> str:
    game = sc.game
    names = ", ".join(game.item_name(i) for i in items)
    if family == "raw":
        caps = "; ".join(
            f"{game.item_name(i)} at most {capacity[i]:,.0f}/min"
            if capacity.get(i, 0.0) > _EPS
            else f"no {game.item_name(i)} in scope"
            for i in items
        )
        return (
            f"short raw: {names} -- the scope cannot supply enough ({caps}); "
            "add sources, or lower a rate"
        )
    if family == "byproduct":
        return (
            f"byproduct with no outlet: {names} -- letting it leave makes the plan solve: "
            + _outlet_fix(sc, items)
        )
    return (
        "power: with MW in exports the plant must power itself, and it cannot here; "
        "without the MW export it solves by drawing from the grid"
    )


def _joint(sc: Scenario, found: list[tuple[str, list[str]]], cap: dict[str, float]) -> str:
    parts = [
        _line(sc, family, items, cap).split(" -- ")[0].split("; without")[0]
        for family, items in found
        if items or family == "power"
    ]
    return "no single limit is the cause; lifting these together makes it solve: " + " / ".join(
        parts
    )


def explain(
    req: PlanRequest, game: GameData, unlocked_buildings: set[str] | None = None
) -> Diagnosis:
    """Name the limits an infeasible plan runs into, each proven by a re-solve.

    An export nothing can make, and a resource the scope has no extractor for, keep their
    existing reports (``supply``). Otherwise more raw, open byproduct outlets and, for a
    self-powered plant, grid power are each lifted alone; a family that rescues the plan is
    named, down to the items that alone rescue it where a few re-solves can show that.
    """
    diag = Diagnosis()
    dead = supply.unmakeable(req, game)
    if dead:
        diag.lines = dead
        return diag
    report = supply.diagnose(req, game, unlocked_buildings)
    diag.solves += report.solves
    if report.rescued and report.missing:
        diag.lines = supply.describe(report, game)
        return diag

    sc = req.scenario
    capacity = _capacity(sc)
    probes = [_raw_probe(sc, report.candidates), _outlet_probe(sc)]
    if MW in sc.exports:
        probes.append(_power_probe())
    found: list[tuple[str, list[str]]] = []
    for probe in probes:
        diag.solves += 1
        sol = solve(probe.loosen(sc))
        if not sol.ok:
            continue
        suspects = probe.suspects(sol)
        confirmed = _confirm(sc, probe, suspects, diag) if suspects else []
        found.append((probe.family, confirmed or suspects))
        diag.lines.append(_line(sc, probe.family, confirmed or suspects, capacity))
        if suspects and not confirmed:
            diag.lines.append(f"{probe.family}: none of these alone is enough; they bind together")
    if found:
        return diag

    together = sc
    for probe in probes:
        together = probe.loosen(together)
    diag.solves += 1
    sol = solve(together)
    if sol.ok:
        joint = [(p.family, p.suspects(sol)) for p in probes]
        diag.lines.append(_joint(sc, joint, capacity))
    else:
        diag.lines.append(
            "cause not found: more raw, open byproduct outlets and grid power together still "
            "leave this infeasible -- check the export minimums against the recipes in scope"
        )
    return diag

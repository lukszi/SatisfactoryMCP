"""Which raw input a plan cannot get, and why -- the other half of INFEASIBLE.

One probe re-solves with a free supply of every resource the scope cannot extract, and
``raw_used`` names the ones the plan wanted; still infeasible means supply is not the cause.
The why beside each is a factual read of the node table, quoted as counts. docs/planning.md §8.2a.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ....core.gamedata.constants import WATER, WATER_PUMP
from ....core.gamedata.model import GameData
from ...spatial import nodes as nodes_mod
from ..solver.model import MW
from ..solver.optimize import solve
from ..solver.processes import build_processes
from ..solver.scenario import PlanRequest

__all__ = ["MissingRaw", "SupplyReport", "describe", "diagnose", "unmakeable"]

_EPS = 1e-6

#: Free supply handed to a candidate resource during the probe. Big enough that no
#: plan in scope can exhaust it, finite so that an unbounded objective still ends.
_FREE_SUPPLY = 1e6


@dataclass
class MissingRaw:
    """One raw resource the plan wanted and the scope cannot supply."""

    item: str
    name: str
    #: How much the probe drew once the resource was free, /min. Evidence of demand,
    #: not a requirement figure: the probe plan is not the plan.
    used: float
    in_scope: int
    on_map: int
    reason: str
    #: Buildings that would unblock it, where the node table can prove one exists.
    needs: tuple[str, ...] = ()


@dataclass
class SupplyReport:
    #: Resources tested, i.e. everything with a node somewhere that this scope has no
    #: extractor for. Reported so a reader can see what the probe did NOT rule out.
    candidates: tuple[str, ...] = ()
    missing: list[MissingRaw] = field(default_factory=list[MissingRaw])
    #: Whether free supply of all candidates made the solve feasible at all.
    rescued: bool = False
    solves: int = 0
    #: Export or target items nothing in scope can produce at all. Needs no probe:
    #: an export with no producing column is a demonstrable dead end on its own.
    unmakeable: list[str] = field(default_factory=list[str])


def _supplied(req: PlanRequest) -> set[str]:
    """Resources the scenario can actually extract, straight off its own columns."""
    return {resource for _building, resource, _purity in req.scenario.extractor_nodes}


def _explain(
    item: str,
    used: float,
    req: PlanRequest,
    game: GameData,
    table: nodes_mod.NodeTable,
    unlocked_buildings: set[str] | None,
) -> MissingRaw:
    """Why one resource is unavailable, from node facts only."""
    scoped = [n for n in req.scoped_nodes if n["resource"] == item]
    on_map = len(table.by_resource(item))
    out = MissingRaw(
        item=item,
        name=game.item_name(item),
        used=round(used, 2),
        in_scope=len(scoped),
        on_map=on_map,
        reason="",
    )

    if item == WATER:
        # Water comes from water volumes, never the node table; its well rows name the
        # wrong building.
        out.reason = "water comes from water volumes, and no Water Extractor is unlocked"
        out.needs = (
            (game.buildings[WATER_PUMP].name,) if WATER_PUMP in game.buildings else (WATER_PUMP,)
        )
        return out

    if not scoped:
        out.reason = (
            f"no node in scope ({on_map} elsewhere on the map -- widen sources)"
            if on_map
            else "no resource node anywhere in the world"
        )
        return out

    blocked = [n for n in scoped if not n["reachable"]]
    needs = sorted(
        {c for n in scoped for c in nodes_mod.blocking_buildings(n, game, unlocked_buildings)}
    )
    out.needs = tuple(game.buildings[c].name if c in game.buildings else c for c in needs)

    if len(blocked) == len(scoped):
        out.reason = f"{len(scoped)} node(s) in scope, none reachable"
        return out

    usable = [n for n in scoped if n["reachable"]]
    free = [n for n in usable if not n["tapped"]]
    if req.only_free_nodes and not free:
        out.reason = (
            f"{len(usable)} reachable node(s) in scope, all already tapped "
            "(only_free_nodes excluded them)"
        )
        return out

    # Reachable, free, and still no extractor column. The remaining causes are all
    # build_scenario's: it only turns kind="node" rows into extractors, so well
    # satellites and geysers never become supply even once their buildings exist.
    kinds = sorted({n["kind"] for n in (free or usable)} - {"node"})
    if kinds:
        out.reason = (
            f"{len(free or usable)} reachable node(s) in scope, all {'/'.join(kinds)} "
            "-- plans model no extractor for those"
        )
    elif out.needs:
        out.reason = (
            f"{len(free or usable)} node(s) in scope, but no unlocked extractor can tap {out.name}"
        )
    else:
        # Say so rather than invent a cause. Every branch above is demonstrable; this
        # one means the node data does not explain it and something else does.
        out.reason = f"{len(scoped)} node(s) in scope, cause not established from node data"
    return out


def unmakeable(req: PlanRequest, game: GameData) -> list[str]:
    """Exports and targets that no column in this scope produces, and why: no recipe, a
    locked recipe, or a locked machine. Needs no probe (docs/planning.md §8.2a, §8.2b)."""
    sc = req.scenario
    made = {i for p in build_processes(sc) for i, rate in p.rates.items() if rate > 0}
    pool = set(sc.recipes)
    available = sc.buildings_available
    out: list[str] = []
    for item in dict.fromkeys([*sc.exports, sc.target_item]):
        if not item or item == MW or item in made:
            continue
        name = game.item_name(item)
        recipes = game.producers_of(item, "part")
        if not recipes:
            out.append(f"nothing produces {name}: no part recipe makes it, and no node supplies it")
            continue
        in_pool = [r for r in recipes if r.cls in pool]
        if not in_pool:
            out.append(
                f"nothing in scope makes {name}: {len(recipes)} recipe(s) do "
                f"({', '.join(r.name for r in recipes[:3])}), none of them unlocked or allowed here"
            )
            continue
        # In the recipe pool but still no column: build_processes drops a recipe whose
        # machine the world has not unlocked.
        machines = [game.machine(r) for r in in_pool]
        blocked = sorted(
            {
                m.name
                for m in machines
                if m is not None and available is not None and m.cls not in available
            }
        )
        out.append(
            f"nothing in scope makes {name}: its recipe(s) need "
            + (", ".join(blocked) if blocked else "a machine this scope does not offer")
            + ", not unlocked"
        )
    return out


def diagnose(
    req: PlanRequest,
    game: GameData,
    unlocked_buildings: set[str] | None = None,
    table: nodes_mod.NodeTable | None = None,
) -> SupplyReport:
    """Name the raw resources whose absence a probe shows to be part of an infeasibility.

    Costs exactly one extra solve, and only on the infeasible path.
    """
    table = table or nodes_mod.load_nodes()
    sc = req.scenario
    unmakeable_now = unmakeable(req, game)
    supplied = _supplied(req)
    # Desc_Geyser_C is a placement target, not an item -- it is in no recipe and so
    # can be no plan's missing input.
    candidates = sorted(
        r for r in {n["resource"] for n in table.nodes} - supplied if r in game.items
    )
    if not candidates:
        return SupplyReport(unmakeable=unmakeable_now)

    probe = replace(sc, raw_caps={**sc.raw_caps, **{c: _FREE_SUPPLY for c in candidates}})
    sol = solve(probe)
    report = SupplyReport(
        candidates=tuple(candidates), solves=1, rescued=sol.ok, unmakeable=unmakeable_now
    )
    if not sol.ok:
        return report

    wanted = set(candidates)
    for item, used in sorted(sol.raw_used.items(), key=lambda kv: -kv[1]):
        if item in wanted and used > _EPS:
            report.missing.append(_explain(item, used, req, game, table, unlocked_buildings))
    return report


def describe(report: SupplyReport, game: GameData) -> list[str]:
    """Report lines for an infeasible plan. Empty when nothing could be demonstrated."""
    # First, because it is the only claim here that is certain rather than probed.
    lines = list(report.unmakeable)
    if not report.candidates:
        return lines
    names = ", ".join(game.item_name(c) for c in report.candidates)
    if not report.rescued:
        lines.append(
            f"not a raw-supply problem on its own: a free supply of {names} still leaves "
            "this INFEASIBLE, so something else is binding too"
        )
        return lines
    if not report.missing:
        return lines
    lines.append(
        "unavailable raw input is a cause: with these supplied, and nothing else "
        "changed, the same plan solves"
    )
    for m in report.missing:
        # Never quote `used`: the probe's draw against free supply is not a requirement.
        needs = f" -- needs {', '.join(m.needs)}" if m.needs else ""
        lines.append(f"missing raw: {m.name} ({m.reason}){needs}")
    return lines

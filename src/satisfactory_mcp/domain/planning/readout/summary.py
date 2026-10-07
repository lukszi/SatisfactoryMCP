"""A solved plan as plain data for the workbench, and the stamp the web passes to a push.

The shape is ``SolveResponse`` in docs/planner_slice_contract.md §11.2. Facts only: MW are
the exact figures ``slice_of`` bills, an unknown is ``None`` and never 0, and nothing is
ranked. ``plan_factory`` renders the same report as text.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from typing_extensions import TypedDict

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ...spatial import nodes as nodes_mod
from ...world import pin
from ...world.state import WorldState
from ..solver.graph import chain_depth_of_rates
from ..solver.model import MW, ProcessRow, Solution
from ..solver.prepare import PlanFailure, PreparedPlan
from ..solver.scenario import PlanRequest, build_scenario, shard_stock
from ..stored import provenance
from . import payback
from .report import PlanFactoryReport, build_plan_report
from .slice import PlanSlice
from .views import (
    ItemRate,
    PaybackView,
    PlanGraph,
    PlanGraphEdge,
    PlanGraphNode,
    SolveResponse,
    SolveRow,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..stored.plan_args import PlanArgs
    from ..stored.planlog.records import PlanState, Stamp
    from ..stored.views import PlanStamp

__all__ = [
    "failure_cause",
    "names_for",
    "power_view",
    "production_graph",
    "solve_summary",
    "stamp_for",
]

_EPS = 1e-6


class _SummaryBase(TypedDict):
    """The fields every summary carries, at their no-plan values until a plan solves."""

    plan_id: str
    machines: int
    processes: int
    mw_draw: float | None
    mw_generated: float | None
    mw_net: float | None
    grid_import: bool
    exports: list[ItemRate]
    inputs: list[ItemRate]
    rows: list[SolveRow]
    graph: PlanGraph
    shards: int | None
    sloops_used: int
    power: PaybackView
    token: str


def _name(g: GameData, item: str) -> str:
    return "MW" if item == MW else g.item_name(item)


def _rates(g: GameData, rates: dict[str, float], sign: int) -> list[ItemRate]:
    rows: list[ItemRate] = [
        {"item": _name(g, item), "per_min": round(abs(rate), 4)}
        for item, rate in rates.items()
        if rate * sign > _EPS
    ]
    return sorted(rows, key=lambda r: -r["per_min"])


def _main_id(g: GameData, proc: ProcessRow) -> str | None:
    """The item a row is for: its recipe's main product, else its largest output."""
    recipe = g.recipes.get(proc["recipe"] or "")
    if recipe is not None and recipe.main_product is not None:
        return recipe.main_product
    made = [(rate, item) for item, rate in proc["rates"].items() if rate > _EPS and item != MW]
    return max(made)[1] if made else None


def _main_item(g: GameData, proc: ProcessRow) -> str | None:
    item = _main_id(g, proc)
    return g.item_name(item) if item is not None else None


def _recipe_name(g: GameData, proc: ProcessRow) -> str:
    recipe = g.recipes.get(proc["recipe"] or "")
    return recipe.name if recipe is not None else proc["label"]


def _row(g: GameData, proc: ProcessRow, required: set[str], rid: str, depth: int) -> SolveRow:
    return {
        "building": proc["building"] or "",
        "recipe": _recipe_name(g, proc),
        "recipe_id": proc["recipe"],
        "item": _main_item(g, proc),
        "machines": int(proc["machines"]),
        "clock": float(proc["clock"]),
        "last_clock": proc.get("last_clock"),
        "overclock_option": proc.get("overclock_option"),
        "mw": float(proc["mw"]),
        "inputs": _rates(g, proc["rates"], -1),
        "outputs": _rates(g, proc["rates"], 1),
        "required": proc["recipe"] in required,
        "id": rid,
        "depth": depth,
    }


def _num(value: float, dp: int = 1) -> str:
    scale = 10**dp
    text = f"{math.floor(value * scale + 0.5) / scale:,.{dp}f}"
    if dp:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _mw_text(value: float, signed: bool = False) -> str:
    whole = -math.floor(-value + 0.5) if value < 0 else math.floor(value + 0.5)
    return ("+" if signed and whole > 0 else "") + f"{whole:,}" + " MW"


def _row_ids(g: GameData, processes: list[ProcessRow]) -> list[str]:
    ids: list[str] = []
    seen: dict[str, int] = {}
    for proc in processes:
        base = proc["recipe"] or "label:" + str(proc.get("label") or _recipe_name(g, proc))
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return ids


def _rows(
    g: GameData, processes: list[ProcessRow], required: set[str]
) -> list[tuple[SolveRow, ProcessRow]]:
    """Each row beside the process it reads, by building then recipe, with its graph ``id``
    and chain ``depth`` (contract §3)."""
    ordered = sorted(processes, key=lambda p: (p["building"] or "", _recipe_name(g, p)))
    ids = _row_ids(g, ordered)
    depths = chain_depth_of_rates([proc["rates"] for proc in ordered])
    return [
        (_row(g, proc, required, rid, depth), proc)
        for proc, rid, depth in zip(ordered, ids, depths, strict=True)
    ]


def _graph_edges(
    g: GameData, paired: list[tuple[SolveRow, ProcessRow]], exports: dict[str, float]
) -> tuple[list[PlanGraphEdge], list[str]]:
    """Every flow into a row or an export, split across its producers by share, and the
    items nothing in the plan makes (drawn from outside)."""
    made: dict[str, list[tuple[str, float]]] = {}
    for row, proc in paired:
        for item, rate in proc["rates"].items():
            if rate > _EPS and item != MW:
                made.setdefault(item, []).append((row["id"], rate))
        if row["mw"] > 0:
            made.setdefault(MW, []).append((row["id"], row["mw"]))
    demands = [
        (item, row["id"], -rate)
        for row, proc in paired
        for item, rate in proc["rates"].items()
        if rate < -_EPS and item != MW
    ]
    demands += [(item, "ex:" + _name(g, item), rate) for item, rate in exports.items()]

    inputs: dict[str, None] = {}
    edges: list[PlanGraphEdge] = []
    for item, target, need in demands:
        sources = made.get(item) or []
        total = sum(rate for _, rate in sources)
        if not sources or total <= _EPS:
            if item == MW:
                continue
            inputs.setdefault(item)
            edges.append(
                {
                    "source": "in:" + _name(g, item),
                    "target": target,
                    "item": _name(g, item),
                    "per_min": round(need, 4),
                    "text": None,
                }
            )
            continue
        for source, rate in sources:
            share = need * rate / total
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "item": _name(g, item),
                    "per_min": round(share, 4),
                    "text": _mw_text(share) if item == MW else None,
                }
            )
    return edges, list(inputs)


def production_graph(
    g: GameData, paired: list[tuple[SolveRow, ProcessRow]], exports: dict[str, float]
) -> PlanGraph:
    """The graph the workbench draws over rows that carry their ``id`` and ``depth``."""
    edges, inputs = _graph_edges(g, paired, exports)
    top = max((row["depth"] + 1 for row, _ in paired), default=0) + 1
    nodes: list[PlanGraphNode] = [
        {
            "id": "in:" + _name(g, item),
            "kind": "input",
            "label": _name(g, item),
            "detail": "from outside the plan",
            "rank": 0,
            "row": None,
            "item": item,
        }
        for item in inputs
    ]
    for row, proc in paired:
        power = f" · {_mw_text(row['mw'], signed=True)}" if row["mw"] else ""
        nodes.append(
            {
                "id": row["id"],
                "kind": "process",
                "label": row["recipe"],
                "detail": f"{row['building']} ×{row['machines']:,}{power} · "
                f"{_num(row['clock'] * 100, 1)}%",
                "rank": row["depth"] + 1,
                "row": row["id"],
                "item": _main_id(g, proc),
            }
        )
    for item, rate in exports.items():
        amount = _mw_text(rate) if item == MW else _num(rate, 1) + "/min"
        nodes.append(
            {
                "id": "ex:" + _name(g, item),
                "kind": "export",
                "label": "power" if item == MW else _name(g, item),
                "detail": "exported " + amount,
                "rank": top,
                "row": None,
                "item": None if item == MW else item,
            }
        )
    return {"nodes": nodes, "edges": edges}


def _raw(g: GameData, sol: Solution) -> list[ItemRate]:
    """What the plan takes from the world: extractor output plus raw drawn from outside."""
    raw = dict(sol.raw_used)
    for row in sol.processes:
        rates = row["rates"]
        if any(rate < -_EPS and item != MW for item, rate in rates.items()):
            continue
        for item, rate in rates.items():
            if rate > _EPS and item != MW:
                raw[item] = raw.get(item, 0.0) + rate
    return _rates(g, raw, 1)


def _blockers(errors: list[str], failure_notes: list[str]) -> list[str]:
    named = [e for e in errors if e.startswith(("required", "exclude_recipes"))]
    return named + [n for n in failure_notes if n.startswith("required in force")]


def names_for(g: GameData, args: PlanArgs) -> dict[str, str]:
    """Display names for the ids a request can hold: recipes, node instances and items."""
    out: dict[str, str] = {}
    for member in [*args.required, *args.banned, *args.only_recipes, *args.row_overclock]:
        recipe = g.recipes.get(member)
        if recipe is not None:
            out[member] = recipe.name
    wanted = [m for m in args.sources if m.startswith("node:")]
    if wanted:
        table = nodes_mod.load_nodes().by_instance()
        table.update({instance_leaf(k): n for k, n in table.items()})
        for member in wanted:
            node = table.get(member[len("node:") :])
            if node is not None:
                out[member] = g.item_name(node["resource"])
    for member in [*args.exports, *args.export_minimums]:
        if member in g.items:
            out[member] = g.item_name(member)
    return out


def _clip(text: str) -> str:
    for cut in ("; known:", ". "):
        text = text.split(cut)[0]
    for prefix in ("exports: ", "export_minimums: ", "supplied: "):
        text = text.removeprefix(prefix)
    return text


def _request_errors(req: PlanRequest) -> list[str]:
    return [*req.selection.errors, *req.site_errors, *req.recipe_errors]


def failure_cause(prepared: PreparedPlan) -> str:
    """Why a prepared plan failed to solve, in player words."""
    req, failure = prepared.request, prepared.failure
    assert failure is not None, "only a plan that failed has a cause"
    headline, notes = failure.headline, failure.notes
    errors = _request_errors(req) or list(notes)
    required = list(req.required)
    if headline == "no sources selected":
        return "the sources match no resource nodes: " + "; ".join(_clip(e) for e in errors[:3])
    if headline == "unusable exports":
        wrong = [_clip(n) for n in notes if n.startswith(("exports:", "export_minimums:"))]
        return "an export is not an item: " + "; ".join(dict.fromkeys(wrong))
    missing = [
        n.removeprefix("missing raw: ").split(" (")[0]
        for n in notes
        if n.startswith("missing raw: ")
    ]
    if missing:
        return "these sources have no " + ", ".join(missing) + "; add sources that do"
    dead = [n for n in notes if n.startswith(("nothing produces", "nothing in scope makes"))]
    if dead:
        return dead[0]
    named = [_clip(e) for e in errors if e.startswith(("required", "exclude_recipes"))]
    if named:
        return "a recipe setting names nothing usable: " + "; ".join(named)
    if required:
        return "the required recipes rule out every answer; remove one, or lower a rate"
    return "these sources and recipes cannot meet every export minimum; lower a rate or add sources"


def _warnings(
    g: GameData, report: PlanFactoryReport, sol: Solution, bill: PlanSlice, objective: str
) -> list[str]:
    out = [
        f"export at zero: {z['name']} is named in exports but 0/min leaves this plan"
        for z in report.zero_exports
    ]
    if report.water_binding and not report.water_cap_given:
        out.append(f"water extractors capped at {report.water_cap} by an assumption, and binding")
    if bill.shard_rows and report.shard_budget and bill.shards > report.shard_budget["potential"]:
        short = bill.shards - report.shard_budget["potential"]
        out.append(f"power shards: {bill.shards} needed, short by {short:.0f}")
    if report.sloop_gate is not None:
        out.append("sloops asked for, but Production Amplifier is not researched")
    if (
        objective in ("max_item", "min_raw", "min_machines")
        and report.overclocked
        and sol.net_mw < 0
    ):
        out.append(f"objective {objective} does not price power; clocks are pushed up")
    if not report.prepared.audit_ok:
        out.append(f"free-lunch audit returned {report.prepared.audit_value} MW, not 0")
    if report.needed_buildings:
        names = sorted(g.buildings[c].name for c in report.needed_buildings if c in g.buildings)
        out.append("must build first: " + ", ".join(names))
    return out


def power_view(
    g: GameData, st: WorldState, req: PlanRequest, sol: Solution, machines: int, draw_mw: float
) -> PaybackView:
    """``SolveResponse.power`` for a solved request: the horizon's stops and the overclock."""
    info = req.payback
    return payback.view(
        g,
        sol,
        req.scenario,
        machines,
        draw_mw,
        inherited=info["inherited"],
        default_hours=info["default_hours"],
        price_source=info["price_source"],
        mix=info["mix"],
        overclock_inherited=info["overclock_inherited"],
        shards=shard_stock(st),
    )


def _no_power(req: PlanRequest) -> PaybackView:
    sc, info = req.scenario, req.payback
    return {
        "hours": sc.payback_hours,
        "inherited": info["inherited"],
        "default_hours": info["default_hours"],
        "price": sc.power_price,
        "price_source": info["price_source"],
        "mix": info["mix"],
        "splits": False,
        "reason": "",
        "stops": [],
        "overclock": {
            **payback.no_overclock(sc.overclock_last),
            "inherited": info["overclock_inherited"],
            "shards_free": None,
            "shards_craftable": None,
        },
    }


def _empty_summary(st: WorldState, req: PlanRequest) -> _SummaryBase:
    """The fields every summary carries, at their no-plan values."""
    try:
        token = pin.check(st.header, None)
    except Exception:
        token = ""
    return {
        "plan_id": req.plan_id,
        "machines": 0,
        "processes": 0,
        "mw_draw": None,
        "mw_generated": None,
        "mw_net": None,
        "grid_import": False,
        "exports": [],
        "inputs": [],
        "rows": [],
        "graph": {"nodes": [], "edges": []},
        "shards": None,
        "sloops_used": 0,
        "power": _no_power(req),
        "token": token,
    }


def _failure_summary(
    prepared: PreparedPlan, failure: PlanFailure, errors: list[str], empty: _SummaryBase
) -> SolveResponse:
    return {
        "feasible": False,
        "headline": failure.headline,
        "cause": failure_cause(prepared),
        "notes": list(failure.notes),
        "warnings": [],
        "blockers": _blockers(errors, failure.notes),
        **empty,
    }


def solve_summary(
    g: GameData, st: WorldState, kwargs: Mapping[str, object], required: list[str] | None = None
) -> SolveResponse:
    """``SolveResponse`` for ``kwargs``: the solved plan as plain data, or why there is none."""
    return _solve_response(g, st, kwargs, required)


def _solve_response(
    g: GameData, st: WorldState, kwargs: Mapping[str, object], required: list[str] | None
) -> SolveResponse:
    kwargs = dict(kwargs)
    if required and not kwargs.get("required"):
        kwargs["required"] = list(required)
    objective = cast("str", kwargs.get("objective", "max_mw"))
    report = build_plan_report(g, st, kwargs, objective=objective)
    prepared = report.prepared
    req = prepared.request
    empty = _empty_summary(st, req)
    errors = _request_errors(req)
    if prepared.failure is not None:
        return _failure_summary(prepared, prepared.failure, errors, empty)
    sol, bill = prepared.solution, report.bill
    assert sol is not None and bill is not None, "a plan that did not fail is solved and billed"
    paired = _rows(g, sol.processes, set(req.required))
    rows = [r for r, _ in paired]
    graph = production_graph(g, paired, sol.exports)
    notes = [*errors, *prepared.notes]
    machines = round(sol.machines_total)
    mw_draw = round(bill.draw_mw + bill.sink_mw, 2)
    power = power_view(g, st, req, sol, machines, mw_draw)
    if req.excluded:
        notes.append("excluded by request: " + ", ".join(req.excluded))
    return {
        "feasible": True,
        "headline": f"{objective} over {req.selection.description}",
        "cause": "",
        "notes": notes,
        "warnings": _warnings(g, report, sol, bill, objective),
        "blockers": _blockers(errors, []),
        **empty,
        "machines": machines,
        "processes": len(sol.processes),
        "mw_draw": mw_draw,
        "mw_generated": round(bill.generation_mw, 2),
        "mw_net": round(bill.net_mw, 2),
        "grid_import": sol.grid_import_mw > _EPS,
        "exports": [
            {"item": _name(g, item), "per_min": round(rate, 4)}
            for item, rate in sol.exports.items()
        ],
        "inputs": _raw(g, sol),
        "rows": rows,
        "graph": graph,
        "shards": bill.shards,
        "sloops_used": bill.sloops_used,
        "power": power,
    }


def stamp_for(g: GameData, st: WorldState) -> Stamp:
    """The ``planlog.Stamp`` for pushes read against ``st``: plan_id and selector provenance."""

    def stamp(state: PlanState) -> PlanStamp:
        return {
            "plan_id": build_scenario(g, st, **state.kwargs()).plan_id,
            "provenance": provenance.record(g, st, list(state.args.sources) or None),
        }

    return stamp

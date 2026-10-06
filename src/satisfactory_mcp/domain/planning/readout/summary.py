"""A solved plan as plain data for the workbench, and the stamp the web passes to a push.

The shape is ``SolveResponse`` in docs/planner_slice_contract.md §11.2. Facts only: MW are
the exact figures ``slice_of`` bills, an unknown is ``None`` and never 0, and nothing is
ranked. ``plan_factory`` renders the same report as text.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ...spatial import nodes as nodes_mod
from ...world import pin
from ...world.state import WorldState
from ..solver.graph import chain_depth_of_rates
from ..solver.model import MW
from ..solver.scenario import build_scenario, shard_stock
from ..stored import provenance
from . import payback
from .report import PlanFactoryReport, build_plan_report

__all__ = [
    "failure_cause",
    "names_for",
    "power_view",
    "production_graph",
    "solve_summary",
    "stamp_for",
]

_EPS = 1e-6


def _name(g: GameData, item: str) -> str:
    return "MW" if item == MW else g.item_name(item)


def _rates(g: GameData, rates: dict, sign: int) -> list[dict]:
    rows = [
        {"item": _name(g, item), "per_min": round(abs(rate), 4)}
        for item, rate in rates.items()
        if rate * sign > _EPS
    ]
    return sorted(rows, key=lambda r: -r["per_min"])


def _main_id(g: GameData, proc: dict) -> str | None:
    """The item a row is for: its recipe's main product, else its largest output."""
    recipe = g.recipes.get(proc.get("recipe") or "")
    if recipe is not None and recipe.main_product is not None:
        return recipe.main_product
    made = [(rate, item) for item, rate in proc["rates"].items() if rate > _EPS and item != MW]
    return max(made)[1] if made else None


def _main_item(g: GameData, proc: dict) -> str | None:
    item = _main_id(g, proc)
    return g.item_name(item) if item is not None else None


def _row(g: GameData, row: dict, required: set[str]) -> dict:
    recipe = g.recipes.get(row.get("recipe") or "")
    return {
        "building": row["building"] or "",
        "recipe": recipe.name if recipe is not None else row["label"],
        "recipe_id": row.get("recipe"),
        "item": _main_item(g, row),
        "machines": int(row["machines"]),
        "clock": float(row["clock"]),
        "last_clock": row.get("last_clock"),
        "overclock_option": row.get("overclock_option"),
        "mw": float(row["mw"]),
        "inputs": _rates(g, row["rates"], -1),
        "outputs": _rates(g, row["rates"], 1),
        "required": row.get("recipe") in required,
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


def _row_ids(paired: list) -> list[str]:
    ids, seen = [], {}
    for row, proc in paired:
        base = row["recipe_id"] or "label:" + str(proc.get("label") or row["recipe"])
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return ids


def _graph_edges(
    g: GameData, paired: list, ids: list[str], exports: dict
) -> tuple[list[dict], list[str]]:
    """Every flow into a row or an export, split across its producers by share, and the
    items nothing in the plan makes (drawn from outside)."""
    made: dict[str, list[tuple[str, float]]] = {}
    for (row, proc), rid in zip(paired, ids, strict=True):
        for item, rate in proc["rates"].items():
            if rate > _EPS and item != MW:
                made.setdefault(item, []).append((rid, rate))
        if row["mw"] > 0:
            made.setdefault(MW, []).append((rid, row["mw"]))
    demands = [
        (item, rid, -rate)
        for (_, proc), rid in zip(paired, ids, strict=True)
        for item, rate in proc["rates"].items()
        if rate < -_EPS and item != MW
    ]
    demands += [(item, "ex:" + _name(g, item), rate) for item, rate in exports.items()]

    inputs: dict[str, None] = {}
    edges: list[dict] = []
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


def production_graph(g: GameData, paired: list, exports: dict) -> dict:
    """The graph the workbench draws, and ``id``/``depth`` set on every row (contract §3)."""
    ids = _row_ids(paired)
    depths = chain_depth_of_rates([proc["rates"] for _, proc in paired])
    for (row, _), rid, depth in zip(paired, ids, depths, strict=True):
        row["id"], row["depth"] = rid, depth
    edges, inputs = _graph_edges(g, paired, ids, exports)
    top = max((d + 1 for d in depths), default=0) + 1
    nodes = [
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
    for (row, proc), rid in zip(paired, ids, strict=True):
        power = f" · {_mw_text(row['mw'], signed=True)}" if row["mw"] else ""
        nodes.append(
            {
                "id": rid,
                "kind": "process",
                "label": row["recipe"],
                "detail": f"{row['building']} ×{row['machines']:,}{power} · "
                f"{_num(row['clock'] * 100, 1)}%",
                "rank": row["depth"] + 1,
                "row": rid,
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


def _raw(g: GameData, sol) -> list[dict]:
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


def names_for(g: GameData, args) -> dict[str, str]:
    """Display names for the ids a request can hold: recipes, node instances and items."""
    out: dict[str, str] = {}
    for member in [*args.required, *args.banned, *args.only_recipes, *args.row_overclock]:
        recipe = g.recipes.get(member)
        if recipe is not None:
            out[member] = recipe.name
    wanted = [m for m in args.sources if isinstance(m, str) and m.startswith("node:")]
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


def _request_errors(req) -> list[str]:
    return [] if req is None else [*req.selection.errors, *req.site_errors, *req.recipe_errors]


def failure_cause(prepared) -> str:
    """Why a prepared plan failed to solve, in player words."""
    req, failure = prepared.request, prepared.failure
    headline, notes = failure.headline, failure.notes
    errors = _request_errors(req) or list(notes)
    required = list(req.required) if req is not None else []
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


def _warnings(g: GameData, report: PlanFactoryReport, objective: str) -> list[str]:
    sol, bill = report.prepared.solution, report.bill
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


def power_view(g: GameData, st: WorldState, req, sol, machines: int, draw_mw: float) -> dict:
    """``SolveResponse.power`` for a solved request: the horizon's stops and the overclock."""
    info = req.payback
    return payback.view(
        g,
        sol,
        req.scenario,
        machines,
        draw_mw,
        inherited=info.get("inherited", True),
        default_hours=info.get("default_hours", 0.0),
        price_source=info.get("price_source", "grid mix"),
        mix=info.get("mix", []),
        overclock_inherited=info.get("overclock_inherited", True),
        shards=shard_stock(st),
    )


def _no_power(req) -> dict:
    sc = req.scenario if req is not None else None
    info = req.payback if req is not None else {}
    return {
        "hours": sc.payback_hours if sc else 0.0,
        "inherited": info.get("inherited", True),
        "default_hours": info.get("default_hours", 0.0),
        "price": sc.power_price if sc else 0.0,
        "price_source": info.get("price_source", "grid mix"),
        "mix": info.get("mix", []),
        "splits": False,
        "reason": "",
        "stops": [],
        "overclock": {
            **payback.no_overclock(bool(sc and sc.overclock_last)),
            "inherited": info.get("overclock_inherited", True),
            "shards_free": None,
            "shards_craftable": None,
        },
    }


def _empty_summary(st: WorldState, req) -> dict:
    """The fields every summary carries, at their no-plan values."""
    try:
        token = pin.check(st.header, None)
    except Exception:
        token = ""
    return {
        "plan_id": req.plan_id if req is not None else "",
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


def _failure_summary(prepared, errors: list[str], empty: dict) -> dict:
    failure = prepared.failure
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
    g: GameData, st: WorldState, kwargs: dict, required: list[str] | None = None
) -> dict:
    """``SolveResponse`` for ``kwargs``: the solved plan as plain data, or why there is none."""
    kwargs = dict(kwargs)
    if required and not kwargs.get("required"):
        kwargs["required"] = list(required)
    objective = kwargs.get("objective", "max_mw")
    report = build_plan_report(g, st, kwargs, objective=objective)
    prepared = report.prepared
    req = prepared.request
    empty = _empty_summary(st, req)
    errors = _request_errors(req)
    if prepared.failure is not None:
        return _failure_summary(prepared, errors, empty)
    sol, bill = prepared.solution, report.bill
    in_force = set(req.required)
    paired = sorted(
        ((_row(g, p, in_force), p) for p in sol.processes),
        key=lambda rp: (rp[0]["building"], rp[0]["recipe"]),
    )
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
        "warnings": _warnings(g, report, objective),
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


def stamp_for(g: GameData, st: WorldState) -> Callable:
    """The ``planlog.Stamp`` for pushes read against ``st``: plan_id and selector provenance."""

    def stamp(state) -> dict:
        return {
            "plan_id": build_scenario(g, st, **state.kwargs()).plan_id,
            "provenance": provenance.record(g, st, list(state.args.sources) or None),
        }

    return stamp

"""The track view: one stored plan's diff and startup stages matched against the save.

docs/planner-p4_contract.md §3 and §5.2 are the specification; docs/planner_p4.md says what was built.
"""

from __future__ import annotations

from collections import Counter

from ...core.gamedata.model import GameData
from ..factories.select import SelectorError
from ..power.report import biomass_note
from ..spatial import nodes as nodes_mod
from ..world.state import WorldState
from . import summary
from .commission import (
    ENERGISED_CAVEAT,
    MONITORED_STATES,
    NO_MONITOR,
    RANGE_CAVEAT,
    RUNNING_STATES,
    live_feeders,
    partition_id,
)
from .diff import _save_id
from .diff_service import NAMEPLATE_SOURCE, STORED_SOURCE, build_diff_report
from .planlog import PlanState

__all__ = ["CAP", "COST_ROWS", "feeders_view", "job_id", "track_view"]

CAP = 50
COST_ROWS = 12
_VERBS = {"OK": "ok", "UNPAUSE": "unpause", "SETRECIPE": "setrecipe", "BUILD": "build"}


def job_id(key: tuple) -> str:
    return "job:" + "|".join(str(k) for k in key)


def _m(value: float) -> float:
    return round(float(value) / 100.0, 1)


def _positions(st: WorldState) -> dict[str, tuple[float, float]]:
    out = {}
    for group in ("machines", "extractors", "generators"):
        for record in st.projection.get(group, ()):
            pos = record.get("pos")
            if pos:
                out[str(record.get("instance") or "").rsplit(".", 1)[-1]] = (_m(pos[0]), _m(pos[1]))
    return out


def _node_positions() -> dict[str, tuple[float, float]]:
    return {
        str(n["instance"]).rsplit(".", 1)[-1]: (_m(n["x"]), _m(n["y"]))
        for n in nodes_mod.load_nodes().nodes
    }


def _bbox(points: list[tuple[float, float]]) -> list[float] | None:
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _states(counter: Counter) -> list[dict]:
    rows = sorted(((s, n) for s, n in counter.items() if n), key=lambda sn: (-sn[1], sn[0]))
    return [{"state": s, "count": n} for s, n in rows]


def _machine(name: str, where: dict) -> dict:
    x, y = where.get(name, (None, None))
    return {"instance": name, "x_m": x, "y_m": y}


def _main_item(g: GameData, recipe_id: str | None) -> str | None:
    recipe = g.recipes.get(recipe_id or "")
    return recipe.products[0].item if recipe is not None and recipe.products else None


def _row(g, st, r, health, where, nodes, stages_of) -> dict:
    kind = r.key[0] if r.key else ""
    recipe_id = r.key[2] if kind == "recipe" and len(r.key) > 2 else None
    states = Counter(health.get(name, "unmonitored") for name in r.have_instances)
    monitored = sum(n for s, n in states.items() if s in MONITORED_STATES)
    act = [_machine(name, where) for name in r.act_instances[:CAP]]
    targets = []
    for node, metres in r.targets[:CAP]:
        x, y = nodes.get(node, (None, None))
        targets.append({"node": node, "x_m": x, "y_m": y, "m": round(metres, 1)})
    shown = act or [_machine(name, where) for name in r.have_instances[:CAP]]
    points = [(m["x_m"], m["y_m"]) for m in shown if m["x_m"] is not None]
    points += [(t["x_m"], t["y_m"]) for t in targets if t["x_m"] is not None]
    if act:
        selectors = ",".join(f"machine:{m['instance']}" for m in act)
    elif targets:
        selectors = ",".join(f"node:{t['node']}" for t in targets)
    else:
        selectors = ",".join(f"machine:{name}" for name in r.have_instances[:CAP])
    return {
        "id": job_id(r.key),
        "kind": kind,
        "step": r.stage,
        "stages": stages_of.get(r.key, []),
        "process": r.process,
        "building": r.building,
        "recipe_id": recipe_id,
        "item": _main_item(g, recipe_id),
        "need": r.need,
        "have": r.have,
        "have_min": r.have_min,
        "build": r.build,
        "build_max": r.build_max,
        "verb": _VERBS.get(r.verb, r.verb.lower()),
        "count": r.count,
        "reuse": r.reuse,
        "running": sum(n for s, n in states.items() if s in RUNNING_STATES) if monitored else None,
        "states": _states(states),
        "new_building": bool(r.building_id) and st.built(r.building_id) == 0,
        "note": r.note,
        "delta_mw": round(r.delta_mw, 2),
        "act": act,
        "targets": targets,
        "bbox_m": _bbox(points),
        "selectors": selectors,
    }


def _stage(stage, where) -> dict:
    points = [where[name] for row in stage.rows for name in row.instances if name in where]
    return {
        "index": stage.index,
        "machines": stage.machines,
        "built": stage.built,
        "built_max": stage.built_max,
        "running": stage.running,
        "dark": stage.dark,
        "complete": stage.complete,
        "state": stage.describe(),
        "draw_mw": round(stage.draw_mw, 2),
        "generation_mw": round(stage.generation_mw, 2),
        "available_before": round(stage.available_before, 2),
        "available_after": round(stage.available_after, 2),
        "fill_s": round(stage.fill_s, 1),
        "waits_for_fill": stage.waits_for_fill,
        "states": _states(stage.by_state),
        "rows": [
            {
                "row": job_id(row.key),
                "label": row.label,
                "building": row.building,
                "machines": row.machines,
                "total": row.total,
                "built": row.built,
                "built_max": row.built_max,
                "running": row.running,
                "states": _states(row.by_state),
                "draw_mw": round(row.draw_mw, 2),
                "generation_mw": round(row.generation_mw, 2),
                "to_build": row.to_build,
            }
            for row in stage.rows
        ],
        "bbox_m": _bbox(points),
    }


def _power(pw: dict, biomass: bool) -> dict:
    return {
        "generation_mw": round(pw.get("generation_mw", 0.0), 2),
        "draw_mw": round(pw.get("draw_mw", 0.0), 2),
        "headroom_mw": round(pw.get("headroom_mw", 0.0), 2),
        "measured_headroom_mw": round(pw.get("measured_headroom_mw", 0.0), 2),
        "biomass": biomass,
    }


def _startup(run, pw: dict, state: PlanState) -> dict:
    if run is None:
        stored = state.headroom_mw is not None
        return {
            "ok": False,
            "headroom_mw": float(state.headroom_mw if stored else pw.get("headroom_mw", 0.0)),
            "headroom_source": STORED_SOURCE if stored else NAMEPLATE_SOURCE,
            "plant_draw_mw": 0.0,
            "plant_generation_mw": 0.0,
            "minimum_slice_mw": 0.0,
            "warnings": [],
        }
    return {
        "ok": run.ok,
        "headroom_mw": round(run.headroom_mw, 2),
        "headroom_source": run.headroom_source,
        "plant_draw_mw": round(run.plant_draw_mw, 2),
        "plant_generation_mw": round(run.plant_generation_mw, 2),
        "minimum_slice_mw": round(run.minimum_slice_mw, 2),
        "warnings": list(run.warnings),
    }


def _cause(prepared) -> str:
    req, failure = prepared.request, prepared.failure
    errors = [] if req is None else [*req.selection.errors, *req.site_errors, *req.recipe_errors]
    required = list(req.required) if req is not None else []
    return summary._cause(failure.headline, failure.notes, errors or list(failure.notes), required)


def _blank(st: WorldState, state: PlanState, biomass: bool) -> dict:
    pw = st.power_report(biomass=biomass)
    return {
        "key": state.key,
        "rev": state.rev,
        "name": state.name,
        "feasible": True,
        "empty": False,
        "headline": "",
        "cause": "",
        "save_id": _save_id(st),
        "age_note": st.age_note,
        "plan_id": state.plan_id,
        "scope": state.factory,
        "scope_note": "",
        "scope_error": "",
        "drift_note": "",
        "headroom_mw": state.headroom_mw,
        "current": 0,
        "count": 0,
        "partition_id": "",
        "stage_text": "",
        "to_build": 0,
        "to_build_max": 0,
        "actionable": 0,
        "unpause": 0,
        "setrecipe": 0,
        "rows": [],
        "stages": [],
        "startup": _startup(None, pw, state),
        "power": _power(pw, biomass),
        "cost": [],
        "neighbours": [],
        "site": None,
        "notes": [],
        "caveats": [ENERGISED_CAVEAT],
        "monitored": 0,
    }


def track_view(g: GameData, st: WorldState, state: PlanState, *, biomass: bool = False) -> dict:
    """The whole ``TrackResponse`` for one plan version, from one solve."""
    kwargs = state.kwargs()
    objective = kwargs.get("objective") or state.args.objective
    out = _blank(st, state, biomass)
    try:
        report = build_diff_report(
            g,
            st,
            kwargs,
            objective=objective,
            plan=state.key,
            plan_name=state.name,
            biomass=biomass,
            headroom_mw=state.headroom_mw,
            stored=state,
        )
    except SelectorError:
        out["scope_error"] = f"“{state.factory}” has no machines in this save"
        return out

    prepared = report.prepared
    if prepared.request is not None:
        out["plan_id"] = prepared.request.plan_id
    if prepared.failure is not None:
        out.update(feasible=False, headline=prepared.failure.headline, cause=_cause(prepared))
        return out
    out["headline"] = f"{objective} over {prepared.request.selection.description}"
    if report.empty or report.rep is None:
        out["empty"] = True
        return out

    rep, tracking, health = report.rep, report.tracking, report.health
    where = _positions(st)
    nodes = _node_positions() if any(r.targets for r in rep.rows) else {}
    stages_of: dict[tuple, list[int]] = {}
    for stage in tracking.stages if tracking is not None else ():
        for row in stage.rows:
            stages_of.setdefault(row.key, [])
            if stage.index not in stages_of[row.key]:
                stages_of[row.key].append(stage.index)
    rows = [_row(g, st, r, health, where, nodes, stages_of) for r in rep.rows]
    stages = [_stage(s, where) for s in tracking.stages] if tracking is not None else []
    monitored = sum(1 for s in health.values() if s in MONITORED_STATES)

    caveats = [ENERGISED_CAVEAT]
    ranged = any(r["build_max"] is not None and r["build_max"] != r["build"] for r in rows)
    ranged = ranged or any(s["built_max"] != s["built"] for s in stages)
    if ranged:
        caveats.append(RANGE_CAVEAT)
    if monitored == 0:
        caveats.append(NO_MONITOR)
    notes = list(rep.notes)
    if biomass_note(report.power):
        notes.append(biomass_note(report.power))

    sv = report.site_survey
    site = None
    if report.site is not None and sv is not None:
        site = {
            "text": report.site.describe(),
            "planned_total": sv.planned_total,
            "standing_total": sv.standing_total,
            "rows": [
                {"name": r.name, "planned": r.planned, "standing": r.standing} for r in sv.rows
            ],
        }

    out.update(
        save_id=rep.save_id,
        scope_note=report.scope_note,
        drift_note=report.drift_note,
        current=tracking.current if tracking is not None and tracking.ok else 0,
        count=len(stages),
        partition_id=partition_id(tracking) if tracking is not None else "",
        stage_text=tracking.headline(brief=True) if tracking is not None else "",
        to_build=rep.to_build,
        to_build_max=rep.to_build_max,
        actionable=sum(1 for r in rep.rows if r.actionable),
        unpause=sum(r["count"] for r in rows if r["verb"] == "unpause"),
        setrecipe=sum(r["count"] for r in rows if r["verb"] == "setrecipe"),
        rows=rows,
        stages=stages,
        startup=_startup(report.run, report.power, state),
        power=_power(report.power, biomass),
        cost=[
            {
                "item": c.item,
                "name": c.name,
                "need": round(c.need, 2),
                "stock": round(c.stock, 2),
                "short": round(c.shortfall, 2),
                "lines": c.lines,
            }
            for c in rep.cost[:COST_ROWS]
        ],
        neighbours=[{"label": label, "count": n} for label, n in rep.neighbours],
        site=site,
        notes=notes,
        caveats=caveats,
        monitored=monitored,
    )
    return out


def feeders_view(g: GameData, st: WorldState) -> dict:
    """Built extractors whose output reaches a running generator, largest first."""
    found = live_feeders(g, st)
    if not found:
        text = "no built extractor feeds a running generator"
    else:
        total = sum(mw for _, mw in found)
        text = (
            f"{len(found)} extractor(s) feed running generators, {total:,.0f} MW in all: "
            "repiping one mid-startup takes that power out"
        )
    return {"feeders": [{"name": name, "mw": round(mw, 1)} for name, mw in found], "text": text}

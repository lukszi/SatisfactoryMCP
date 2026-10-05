"""The track view: one stored plan's diff and startup stages matched against the save.

docs/planner-p4_contract.md §3 and §5.2 are the specification; docs/planner_p4.md says what was built.
"""

from __future__ import annotations

import re
from collections import Counter

from ...core.gamedata.model import GameData
from ...core.text import ago
from ..factories.select import SelectorError
from ..power.report import biomass_note
from ..spatial import nodes as nodes_mod
from ..spatial.regions import load_regions
from ..world.state import WorldState
from . import built as built_mod
from . import summary
from .commission import (
    ENERGISED_CAVEAT,
    MONITORED_STATES,
    NO_MONITOR,
    RANGE_CAVEAT,
    RUNNING_STATES,
    feeder_records,
    partition_id,
)
from .diff import _save_id
from .diff_service import DEFAULT_HEADROOM, STORED_SOURCE, build_diff_report, default_headroom
from .planlog import PlanState

__all__ = [
    "CAP",
    "COST_ROWS",
    "PAGE_DRIFT",
    "PAGE_ENERGISED",
    "PAGE_NO_MONITOR",
    "PAGE_RANGE",
    "feeders_view",
    "job_id",
    "page_lines",
    "page_text",
    "save_line",
    "site_line",
    "track_view",
]

CAP = 50
COST_ROWS = 12
_VERBS = {"OK": "ok", "UNPAUSE": "unpause", "SETRECIPE": "setrecipe", "BUILD": "build"}

PAGE_ENERGISED = (
    "built is not the same as powered: a machine that produced in the last 5 minutes "
    "certainly had power, one that did not may be unpowered, starved, blocked or idle. "
    "The save does not record which grid a machine is on, so a fully built stage that "
    "sits dark is possible"
)
PAGE_RANGE = (
    "built is a range where a machine cannot be tied to this plan, such as water pumps: "
    "the low end counts only the ones among the plan's own. Running counts every matched "
    "machine, so it can sit above the low end"
)
PAGE_NO_MONITOR = (
    "no productivity monitor covers a matched machine, so the save shows what is built "
    "but not what is powered"
)
PAGE_DRIFT = "the world changed since this plan was saved, so stage numbers may have moved"
_NO_ORDER = "no startup order exists at this scope: "
_PAGE = {ENERGISED_CAVEAT: PAGE_ENERGISED, RANGE_CAVEAT: PAGE_RANGE, NO_MONITOR: PAGE_NO_MONITOR}


def page_text(text: str) -> str:
    """A tool-facing sentence in the page's words: stages, not waves, and no repeat of the
    headline's "no startup order"."""
    if text in _PAGE:
        return _PAGE[text]
    text = text.replace(" -- ", ": ").replace(_NO_ORDER, "")
    text = re.sub(r"(\d%?)\.\.(\d)", "\\1–\\2", text)
    return re.sub(r"\bwave(s?)\b", r"stage\1", text)


def page_lines(text: str) -> list[str]:
    """``page_text`` split into one line per sentence, each starting lower case."""
    out = []
    for part in page_text(text).split(". "):
        part = part.strip().rstrip(".")
        if part[:1].isupper() and part[1:2].islower():
            part = part[0].lower() + part[1:]
        if part:
            out.append(part)
    return out


def _metres(value: float) -> str:
    n = round(float(value))
    return f"{n if n else 0:,}"


def site_line(site) -> str:
    """The recorded site in page words: whole metres, degrees and a spaced footprint."""
    where = f"origin x {_metres(site.x_m)} m, y {_metres(site.y_m)} m"
    if site.z_m is not None:
        where += f", z {_metres(site.z_m)} m"
    if site.origin_label:
        where += f" (from {site.origin_label})"
    where += f", yaw {_metres(site.yaw_deg)}°"
    if not site.has_footprint:
        return where + ", no footprint recorded"
    size = f"{_metres(site.width_m)} × {_metres(site.depth_m)} m"
    source = {"layout": "from the plan layout", "given": "as given", "default": "by default"}.get(
        site.source, ""
    )
    return where + f", footprint {size}" + (f" {source}" if source else "")


def save_line(st: WorldState) -> str:
    """The save as the page names it: file, age, and whether it is an autosave."""
    h = st.projection.get("header", {})
    name = str(h.get("filename") or "?")
    if name.lower().endswith(".sav"):
        name = name[:-4]
    parts = [name]
    written = ago(h.get("mtime_ns"))
    if written:
        parts.append("written " + written)
    if "autosave" in name.lower():
        parts.append("autosave, may lag the game")
    return " · ".join(parts)


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
        "note": r.page_note,
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
        "running": stage.running if stage.monitored else None,
        "dark": stage.dark,
        "complete": stage.complete,
        "state": page_text(stage.describe()),
        "draw_mw": round(stage.draw_mw, 2),
        "generation_mw": round(stage.generation_mw, 2),
        "available_before": stage.available_before,
        "available_after": stage.available_after,
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
                "running": row.running
                if any(s in MONITORED_STATES for s in row.by_state)
                else None,
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
        "headroom_mw": pw.get("headroom_mw", 0.0),
        "measured_headroom_mw": pw.get("measured_headroom_mw", 0.0),
        "biomass": biomass,
    }


def _startup(run, pw: dict, state: PlanState, default: str) -> dict:
    if run is None:
        if state.headroom_mw is not None:
            head, source = float(state.headroom_mw), STORED_SOURCE
        else:
            head, source = default_headroom(pw, default)
        return {
            "ok": False,
            "headroom_mw": head,
            "headroom_source": source,
            "plant_draw_mw": 0.0,
            "plant_generation_mw": 0.0,
            "minimum_slice_mw": 0.0,
            "warnings": [],
        }
    return {
        "ok": run.ok,
        "headroom_mw": run.headroom_mw,
        "headroom_source": run.headroom_source,
        "plant_draw_mw": round(run.plant_draw_mw, 2),
        "plant_generation_mw": round(run.plant_generation_mw, 2),
        "minimum_slice_mw": round(run.minimum_slice_mw, 2),
        "warnings": [line for w in run.warnings for line in page_lines(w)],
    }


def _cause(prepared) -> str:
    req, failure = prepared.request, prepared.failure
    errors = [] if req is None else [*req.selection.errors, *req.site_errors, *req.recipe_errors]
    required = list(req.required) if req is not None else []
    return summary._cause(failure.headline, failure.notes, errors or list(failure.notes), required)


def built_view(found: built_mod.BuiltAt | None, st: WorldState, state: PlanState) -> dict:
    """``TrackResponse.built_at``: where the plan's built machines were found, and progress."""
    if found is None:
        found = built_mod.BuiltAt(mode=built_mod.mode_of(state.factory))
    return {
        "mode": found.mode,
        "confidence": found.confidence,
        "text": found.where() if found.mode != "auto" or found.confidence else "",
        "figure": found.figure(),
        "hint": found.hint,
        "fallback": found.fallback,
        "area": found.area.words if found.area is not None else "",
        "picked": found.picked,
        "built": found.built,
        "built_max": found.built_max,
        "total": found.total,
        "percent": found.percent,
        "percent_max": found.percent_max,
        "candidates": [
            {
                "kind": c.kind,
                "name": c.name,
                "proposal": c.proposal,
                "machines": len(c.machines),
                "rate_share": c.rate_share,
                "bbox_m": c.bbox_m,
            }
            for c in found.candidates[:6]
        ],
        "missing": list(found.missing),
        "also_here": list(found.also_here),
        "foreign": [f"{n} matching nearby belong to “{name}”" for name, n in found.foreign],
        "node_owner": found.node_owner,
        "labels_version": st.labels.version,
    }


def _blank(st: WorldState, state: PlanState, biomass: bool, default: str) -> dict:
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
        "age_note": save_line(st),
        "written_ago": ago(st.projection.get("header", {}).get("mtime_ns")),
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
        "startup": _startup(None, pw, state, default),
        "power": _power(pw, biomass),
        "cost": [],
        "neighbours": [],
        "site": None,
        "notes": [],
        "caveats": [PAGE_ENERGISED],
        "monitored": 0,
        "built_at": built_view(None, st, state),
    }


def track_view(
    g: GameData,
    st: WorldState,
    state: PlanState,
    *,
    biomass: bool = False,
    default: str = DEFAULT_HEADROOM,
) -> dict:
    """The whole ``TrackResponse`` for one plan version, from one solve. ``default`` is the
    save's headroom (measured or nameplate) a plan with none stored is partitioned against."""
    kwargs = state.kwargs()
    objective = kwargs.get("objective") or state.args.objective
    out = _blank(st, state, biomass, default)
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
            default=default,
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
    found = rep.built_at if isinstance(rep.built_at, built_mod.BuiltAt) else None
    placed = found is None or found.built is not None
    out["built_at"] = built_view(found, st, state)
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

    caveats = [PAGE_ENERGISED]
    ranged = any(r["build_max"] is not None and r["build_max"] != r["build"] for r in rows)
    ranged = ranged or any(s["built_max"] != s["built"] for s in stages)
    if ranged:
        caveats.append(PAGE_RANGE)
    if monitored == 0:
        caveats.append(PAGE_NO_MONITOR)
    notes = [page_text(n) for n in rep.notes]
    left_out = report.power.get("biomass_mw") or 0
    if biomass_note(report.power):
        notes.append(f"{left_out:,.0f} MW of biomass burners left out of generation and headroom")

    sv = report.site_survey
    site = None
    if report.site is not None and sv is not None:
        site = {
            "text": site_line(report.site),
            "planned_total": sv.planned_total,
            "standing_total": sv.standing_total,
            "rows": [
                {"name": r.name, "planned": r.planned, "standing": r.standing} for r in sv.rows
            ],
        }

    out.update(
        save_id=rep.save_id,
        scope_note=(
            f"only machines in “{found.picked}” count as built, and nodes other factories "
            "tap are taken"
            if report.scope_note and found is not None
            else ""
        ),
        drift_note=PAGE_DRIFT if report.drift_note else "",
        current=tracking.current if tracking is not None and tracking.ok else 0,
        count=len(stages),
        partition_id=partition_id(tracking) if tracking is not None else "",
        stage_text=tracking.headline(brief=True) if tracking is not None and placed else "",
        to_build=rep.to_build,
        to_build_max=rep.to_build_max,
        actionable=sum(1 for r in rep.rows if r.actionable),
        unpause=sum(r["count"] for r in rows if r["verb"] == "unpause"),
        setrecipe=sum(r["count"] for r in rows if r["verb"] == "setrecipe"),
        rows=rows,
        stages=stages,
        startup=_startup(report.run, report.power, state, default),
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


def feeders_view(g: GameData, st: WorldState, *, biomass: bool = False) -> dict:
    """Built extractors whose output reaches a running generator, largest first.

    Extractors feeding the same generators each carry that generation, so the headline is
    the union reached from any of them, counted once.
    """
    from ..factories.trace import power_at_risk

    found = feeder_records(g, st)
    try:
        regions = load_regions()
    except (OSError, ValueError, KeyError):
        regions = None
    feeders = []
    for record, name, mw in found:
        instance = str(record.get("instance") or "").rsplit(".", 1)[-1]
        pos = record.get("pos")
        feeders.append(
            {
                "name": name,
                "instance": instance,
                "x_m": _m(pos[0]) if pos else None,
                "y_m": _m(pos[1]) if pos else None,
                "mw": round(mw, 1),
                "region": (regions.label_for(pos[0], pos[1]).name if regions and pos else None)
                or "",
            }
        )
    if not found:
        return {
            "feeders": [],
            "total_mw": 0.0,
            "text": "no built extractor feeds a running generator",
        }
    total, _, _ = power_at_risk(st, g, [f["instance"] for f in feeders])
    generation = st.power_report(biomass=biomass).get("generation_mw", 0.0)
    text = (
        f"{len(found)} extractors feed running generators, together {total:,.0f} MW of "
        f"{generation:,.0f} MW generated. Extractors that feed the same generators share "
        "that power, so the rows overlap. Repiping one mid-startup takes its row's power out."
    )
    return {"feeders": feeders, "total_mw": round(total, 1), "text": text}

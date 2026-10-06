"""What a stored plan would meet at a candidate pad: the one preview the page's drag and
chat's ``site_plan(preview=True)`` share. docs/planner-p5_contract.md §4 is the specification.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ...spatial import geo
from ...spatial.places import parse_near
from ...world.state import WorldState
from .. import siting as siting_mod
from ..layout.trunks import plan_trunks
from ..progress import built as built_mod
from ..progress.diff import _save_id, build_diff
from ..progress.diff_service import DEFAULT_HEADROOM, STORED_SOURCE, default_headroom
from ..progress.stages import track
from ..progress.startup import Commissioning, commission
from ..solver.prepare import PreparedPlan, prepare
from ..stored.planlog import PlanState

__all__ = [
    "DRAG_TEXELS",
    "FALLBACK_SIDE_M",
    "NOT_READ",
    "Session",
    "open_session",
    "preview",
    "start_siting",
]

#: Terrain samples while dragging; 0 reads the pad at full 1 m stride (on release).
DRAG_TEXELS = 40_000
WATER_SEARCH_M = 500.0
FIT_CANDIDATES = 3
CANDIDATES = 6
#: The pad a plan starts on when even its layout square cannot be worked out.
FALLBACK_SIDE_M = 200.0
NO_FIELD = "no terrain field on this machine"
NOT_READ = "terrain not read"
Z_PENDING = "terrain height: pending"


@dataclass
class Session:
    """One plan version solved once per drag: everything that does not move with the pad."""

    state: PlanState
    prepared: PreparedPlan
    run: Commissioning | None
    biomass: bool
    failure: str = ""
    pump_head_m: float = 0.0
    now: dict = field(default_factory=dict)
    pads: list[tuple[str, siting_mod.Siting]] = field(default_factory=list)


def _pump_head(g: GameData, st: WorldState) -> float:
    heads = [
        b.head_lift_m
        for c, b in g.buildings.items()
        if b.head_lift_m and c in st.unlocked_building_ids
    ]
    return max(heads, default=0.0)


def open_session(
    g: GameData,
    st: WorldState,
    state: PlanState,
    *,
    biomass: bool = False,
    default: str = DEFAULT_HEADROOM,
) -> Session:
    """Solve and partition ``state`` once; the built figure at its stored site rides along."""
    prepared = prepare(g, st, state.kwargs(), diagnose=False)
    sess = Session(state=state, prepared=prepared, run=None, biomass=biomass)
    if prepared.failure is not None:
        sess.failure = prepared.failure.headline
        return sess
    if not prepared.solution.processes:
        sess.failure = "the plan builds nothing"
        return sess
    pw = st.power_report(biomass=biomass)
    if state.headroom_mw is not None:
        head, source = float(state.headroom_mw), STORED_SOURCE
    else:
        head, source = default_headroom(pw, default)
    sess.run = commission(prepared, g, head, source)
    sess.pump_head_m = _pump_head(g, st)
    sess.now = _built(g, st, sess, state)
    for other in st.plans.plans:
        sit = siting_mod.parse(other)
        if other.key != state.key and sit is not None and sit.has_footprint:
            sess.pads.append((other.name, sit))
    return sess


def _built(g: GameData, st: WorldState, sess: Session, state: PlanState) -> dict:
    prepared = sess.prepared
    found = built_mod.detect(g, st, state, prepared)
    rep = build_diff(
        g, st, prepared.solution, prepared.request, scope=found.scope, biomass=sess.biomass
    )
    low = None
    if found.scope_low is not None:
        low = build_diff(
            g, st, prepared.solution, prepared.request, scope=found.scope_low, biomass=sess.biomass
        )
    built_mod.fill_progress(found, rep, low)
    tracking = track(prepared, sess.run, rep, g, st, plan_name=state.name) if sess.run else None
    placed = found.built is not None
    where = found.where()
    if found.mode == "picked":
        where = f"counted at “{found.picked}” (picked): the pad does not change the count"
    elif found.mode in ("world", "none"):
        where += ": the pad does not change the count"
    return {
        "mode": found.mode,
        "confidence": found.confidence,
        "figure": found.figure(),
        "where": where,
        "hint": found.hint,
        "area": found.area.words if found.area is not None else "",
        "built": found.built,
        "total": found.total,
        "current": tracking.current if tracking is not None and tracking.ok else 0,
        "count": len(tracking.stages) if tracking is not None and tracking.ok else 0,
        "stage_text": tracking.headline(brief=True) if tracking is not None and placed else "",
        "candidates": [
            {"name": c.name, "kind": c.kind, "machines": len(c.machines), "bbox_m": c.bbox_m}
            for c in found.candidates[:CANDIDATES]
        ],
    }


def start_siting(g: GameData, st: WorldState, sess: Session) -> siting_mod.Siting:
    """Where a never-sited plan's pad starts: its ``near:`` centre, its nodes, else the map's
    centre; sized by the layout square."""
    args = sess.state.args
    x = y = None
    for term in args.sources:
        text = str(term).strip()
        if text.casefold().startswith("near:"):
            try:
                place, _r = parse_near(text[5:])
                x, y, _z, _label = siting_mod.resolve_site_origin(st, place)
            except (ValueError, KeyError):
                continue
            break
    rows = (
        [r for r in sess.prepared.request.node_rows if r.get("kind") == "node"]
        if sess.prepared.request
        else []
    )
    if x is None and rows:
        cx, cy = geo.centroid([(r["x"], r["y"]) for r in rows])
        x, y = cx / 100.0, cy / 100.0
    if x is None:
        x0, y0, x1, y1 = geo.MAP_SQUARE_M
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
    try:
        return siting_mod.build_siting(
            g, st, at=f"{x:.2f},{y:.2f}", solution=sess.prepared.solution, plan_kwargs=args.kwargs()
        )
    except ValueError:
        side = FALLBACK_SIDE_M
        return siting_mod.Siting(round(x, 2), round(y, 2), None, 0.0, side, side, "default", "")


def _box_cm(sit: siting_mod.Siting) -> tuple[float, float, float, float]:
    a = math.radians(sit.yaw_deg)
    hw, hd = sit.width_m / 2, sit.depth_m / 2
    ex = abs(hw * math.cos(a)) + abs(hd * math.sin(a))
    ey = abs(hw * math.sin(a)) + abs(hd * math.cos(a))
    return ((sit.x_m - ex) * 100, (sit.y_m - ey) * 100, (sit.x_m + ex) * 100, (sit.y_m + ey) * 100)


def _corners(sit: siting_mod.Siting) -> list[tuple[float, float]]:
    a = math.radians(sit.yaw_deg)
    c, s = math.cos(a), math.sin(a)
    hw, hd = sit.width_m / 2, sit.depth_m / 2
    return [
        (sit.x_m + dx * c - dy * s, sit.y_m + dx * s + dy * c)
        for dx, dy in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))
    ]


def _overlap(a: siting_mod.Siting, b: siting_mod.Siting) -> bool:
    """Separating axes over the two turned rectangles."""
    pa, pb = _corners(a), _corners(b)
    for poly in (pa, pb):
        for i in range(4):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 4]
            nx, ny = y2 - y1, x1 - x2
            ra = [nx * x + ny * y for x, y in pa]
            rb = [nx * x + ny * y for x, y in pb]
            if max(ra) < min(rb) or max(rb) < min(ra):
                return False
    return True


def _terrain(sit: siting_mod.Siting, terrain, cap: int) -> tuple[dict | None, str]:
    if terrain is None:
        return None, NO_FIELD
    x0, y0, x1, y1 = _box_cm(sit)
    try:
        pad = terrain.window(x0, y0, x1, y1, max_texels=cap or 1_000_000)
        near = None
        if pad.water_level_m is None:
            near = terrain.nearest_water(sit.x_m * 100, sit.y_m * 100, WATER_SEARCH_M * 100)
    except (MemoryError, OSError, ValueError):
        return None, NOT_READ
    if pad.water_level_m is not None:
        water_m, below = 0.0, pad.water_below_ground_m
    elif near is not None and near.distance_m is not None:
        water_m = near.distance_m
        below = None if pad.z_median_m is None else round(pad.z_median_m - near.level_m, 1)
    else:
        water_m = below = None
    return {
        "z_min_m": pad.z_min_m,
        "z_median_m": pad.z_median_m,
        "z_max_m": pad.z_max_m,
        "slope_mean_deg": pad.slope_mean_deg,
        "slope_p90_deg": pad.slope_p90_deg,
        "roughness_m": pad.roughness_m,
        "submerged_pct": pad.submerged_pct,
        "nodata_pct": pad.nodata_pct,
        "stride": pad.stride,
        "water_m": water_m,
        "water_below_m": below,
        "cave_pct": pad.cave_pct,
    }, ""


def _covers(sit: siting_mod.Siting, bbox: list[float]) -> bool:
    x0, y0, x1, y1 = bbox
    return all(
        sit.contains_cm(x * 100, y * 100) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    )


def _loses(now: dict, here: dict) -> dict | None:
    had, has = now.get("built"), here.get("built")
    if not had or here.get("mode") != "auto" or (has or 0) >= had:
        return None
    total = here.get("total") or now.get("total") or 0
    text = f"{had} of {total} built here → {has or 0} at the new spot"
    return {"now": had, "here": has or 0, "total": total, "text": text}


def _empty_built() -> dict:
    return {
        "mode": "",
        "confidence": "",
        "figure": "–",
        "where": "",
        "hint": "",
        "area": "",
        "built": None,
        "total": 0,
        "current": 0,
        "count": 0,
        "stage_text": "",
        "candidates": [],
    }


def preview(
    g: GameData,
    st: WorldState,
    sess: Session,
    sit: siting_mod.Siting,
    *,
    terrain=None,
    terrain_cap: int = 0,
    first: bool = False,
) -> dict:
    """``SitePreviewResponse`` for ``sit``; writes nothing. ``terrain`` is a loaded field or None."""
    from ...spatial.regions import load_regions

    x_cm, y_cm = sit.x_m * 100, sit.y_m * 100
    mx0, my0, mx1, my1 = geo.MAP_SQUARE_M
    in_map = all(mx0 <= x <= mx1 and my0 <= y <= my1 for x, y in _corners(sit))
    cx0, cy0, cx1, cy1 = geo.CONTENT_BBOX
    try:
        region = load_regions().label_for(x_cm, y_cm).describe()
    except (OSError, ValueError, KeyError):
        region = ""
    stored = siting_mod.parse(sess.state)
    out = {
        "key": sess.state.key,
        "rev": sess.state.rev,
        "save_id": _save_id(st),
        "x_m": sit.x_m,
        "y_m": sit.y_m,
        "yaw_deg": sit.yaw_deg,
        "w_m": sit.width_m,
        "d_m": sit.depth_m,
        "source": sit.source,
        "sited": stored is not None,
        "in_map": in_map,
        "in_content": cx0 <= x_cm <= cx1 and cy0 <= y_cm <= cy1,
        "region": region,
        "z_m": None,
        "z_note": Z_PENDING,
        "terrain": None,
        "terrain_note": "",
        "slabs": [],
        "on_pad": 0,
        "planned": 0,
        "trunks": [],
        "placeless": [],
        "built": _empty_built(),
        "now": sess.now or _empty_built(),
        "basis": "",
        "loses": None,
        "fits": [],
        "overlaps": [],
        "nodes": None,
        "content_bbox_m": [v / 100 for v in geo.CONTENT_BBOX] if first else None,
        "failure": sess.failure,
    }
    if first and sess.prepared.request is not None:
        out["nodes"] = []
    if not in_map:
        return out
    z = siting_mod.ground_z(sit.x_m, sit.y_m, sit.yaw_deg, sit.width_m, sit.depth_m)
    if z is not None:
        out["z_m"], out["z_note"] = z, ""
    out["terrain"], out["terrain_note"] = _terrain(sit, terrain, terrain_cap)
    x0, y0, x1, y1 = _box_cm(sit)
    out["slabs"] = [
        f"{s.tiles:,}-tile slab, {s.storeys} {'storey' if s.storeys == 1 else 'storeys'}"
        for s in st.structures.slabs
        if s.bbox[0] <= x1 and s.bbox[2] >= x0 and s.bbox[1] <= y1 and s.bbox[3] >= y0
    ][:4]
    out["overlaps"] = [name for name, other in sess.pads if _overlap(sit, other)]
    if sess.failure:
        out["terrain_note"] = out["terrain_note"] or sess.failure
        return out
    sol = sess.prepared.solution
    survey = siting_mod.survey(g, st, sit, sol.processes)
    if survey is not None:
        out["on_pad"], out["planned"] = survey.standing_total, survey.planned_total
    dest_z = out["z_m"] if out["z_m"] is not None else (out["terrain"] or {}).get("z_median_m")
    tp = plan_trunks(sess.prepared, g, (x_cm, y_cm))
    chosen = set()
    for t in tp.trunks:
        chosen |= {m.instance for m in t.members}
        lift = t.lift_to_site_m(dest_z)
        pumps = None
        if t.carrier == "pipe" and lift is not None and sess.pump_head_m:
            climb = t.members[0].z / 100.0 - dest_z
            pumps = math.ceil(-climb / sess.pump_head_m - 1e-9) if climb < 0 else 0
        out["trunks"].append(
            {
                "name": t.name,
                "carrier": t.carrier,
                "members": len(t.members),
                "run_m": round(t.run_m, 1),
                "to_site_m": round(t.to_site_m((x_cm, y_cm)), 1),
                "lift_m": lift,
                "pumps": pumps,
            }
        )
    out["placeless"] = [f"{n} {name}" for name, _rate, n in tp.placeless]
    if out["nodes"] is not None:
        out["nodes"] = [
            {
                "instance": str(r["instance"]).rsplit(".", 1)[-1],
                "resource": g.item_name(r["resource"]),
                "x_m": round(r["x"] / 100, 1),
                "y_m": round(r["y"] / 100, 1),
            }
            for r in sess.prepared.request.node_rows
            if r.get("kind") == "node" and str(r["instance"]).rsplit(".", 1)[-1] in chosen
        ]
    moved = dataclasses.replace(sess.state, siting=sit.to_dict())
    here = _built(g, st, sess, moved)
    out["built"] = here
    if stored is None and sess.now.get("area"):
        out["basis"] = f"counted on its pad from now (was: {sess.now['area']})"
    out["loses"] = _loses(sess.now, here)
    when = str(st.header.get("save_datetime") or "")
    fits = []
    for c in here["candidates"] if here["mode"] == "auto" else []:
        if c["bbox_m"] and not _covers(sit, c["bbox_m"]):
            fits.append(
                {
                    "name": c["name"],
                    "machines": c["machines"],
                    "value": siting_mod.fit_to_bbox(c["bbox_m"], c["name"], when),
                }
            )
    out["fits"] = fits[:FIT_CANDIDATES]
    return out

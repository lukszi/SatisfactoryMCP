"""What a stored plan would meet at a candidate pad: the one preview the page's drag and
chat's ``site_plan(preview=True)`` share. docs/planner-p5_contract.md §4 is the specification.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ...spatial import geo, heightfield
from ...spatial.places import parse_near
from ...world.state import WorldState
from .. import siting as siting_mod
from ..layout.trunks import plan_trunks
from ..progress import built as built_mod
from ..progress.diff import build_diff, save_id
from ..progress.diff_service import DEFAULT_HEADROOM, resolve_headroom
from ..progress.stages import track
from ..progress.startup import Commissioning, commission
from ..solver.prepare import PreparedPlan, prepare
from ..stored.planlog import PlanState
from .views import (
    SiteBuilt,
    SiteFit,
    SiteLoss,
    SitePreviewNode,
    SitePreviewResponse,
    SiteTerrain,
    SiteTrunk,
)

__all__ = [
    "DRAG_TEXELS",
    "FALLBACK_SIDE_M",
    "NOT_READ",
    "PreviewSession",
    "initial_siting",
    "open_session",
    "preview",
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


def _empty_built() -> SiteBuilt:
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


@dataclass
class PreviewSession:
    """One plan version solved once per drag: everything that does not move with the pad."""

    stored: PlanState
    prepared: PreparedPlan
    commissioning: Commissioning | None
    biomass: bool
    failure: str = ""
    pump_head_m: float = 0.0
    #: The built figure at the stored site, as ``_built_progress`` words it.
    built_now: SiteBuilt = field(default_factory=_empty_built)
    pads: list[tuple[str, siting_mod.Siting]] = field(
        default_factory=list[tuple[str, siting_mod.Siting]]
    )


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
    stored: PlanState,
    *,
    biomass: bool = False,
    default: str = DEFAULT_HEADROOM,
) -> PreviewSession:
    """Solve and partition ``stored`` once; the built figure at its stored site rides along."""
    prepared = prepare(g, st, stored.kwargs(), diagnose=False)
    sess = PreviewSession(stored=stored, prepared=prepared, commissioning=None, biomass=biomass)
    if prepared.failure is not None:
        sess.failure = prepared.failure.headline
        return sess
    if prepared.solution is None or not prepared.solution.processes:
        sess.failure = "the plan builds nothing"
        return sess
    power = st.power_report(biomass=biomass)
    head, source = resolve_headroom(power, stored=stored, default=default)
    sess.commissioning = commission(prepared, g, head, source)
    sess.pump_head_m = _pump_head(g, st)
    sess.built_now = _built_progress(g, st, sess, stored)
    for other in st.plans.plans:
        sit = siting_mod.parse(other)
        if other.key != stored.key and sit is not None and sit.has_footprint:
            sess.pads.append((other.name, sit))
    return sess


def _built_progress(
    g: GameData, st: WorldState, sess: PreviewSession, stored: PlanState
) -> SiteBuilt:
    """What counts as built for ``stored`` at its site, and the stage that puts it in."""
    prepared = sess.prepared
    found = built_mod.detect(g, st, stored, prepared)
    solution, request = prepared.solution, prepared.request
    assert solution is not None and request is not None, "a session that solved has both"
    diff = build_diff(g, st, solution, request, scope=found.scope, biomass=sess.biomass)
    low = None
    if found.scope_low is not None:
        low = build_diff(g, st, solution, request, scope=found.scope_low, biomass=sess.biomass)
    built_mod.fill_progress(found, diff, low)
    tracking = None
    if sess.commissioning:
        tracking = track(prepared, sess.commissioning, diff, g, st, plan_name=stored.name)
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


def initial_siting(g: GameData, st: WorldState, sess: PreviewSession) -> siting_mod.Siting:
    """Where a never-sited plan's pad starts: its ``near:`` centre, its nodes, else the map's
    centre; sized by the layout square."""
    args = sess.stored.args
    x: float | None = None
    y: float | None = None
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
    centre = geo.centroid([(r["x"], r["y"]) for r in rows]) if x is None and rows else None
    if centre is not None:
        x, y = centre[0] / 100.0, centre[1] / 100.0
    if x is None or y is None:
        x0, y0, x1, y1 = geo.MAP_SQUARE_M
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
    try:
        return siting_mod.build_siting(
            g, st, at=f"{x:.2f},{y:.2f}", solution=sess.prepared.solution, plan_kwargs=args.kwargs()
        )
    except ValueError:
        side = FALLBACK_SIDE_M
        return siting_mod.Siting(round(x, 2), round(y, 2), None, 0.0, side, side, "default", "")


def _pad_terrain(
    sit: siting_mod.Siting, terrain: heightfield.Field | None, cap: int
) -> tuple[SiteTerrain | None, str]:
    if terrain is None:
        return None, NO_FIELD
    x0, y0, x1, y1 = sit.bbox_cm()
    try:
        pad = terrain.window(x0, y0, x1, y1, max_texels=cap or 1_000_000)
        near = None
        if pad.water_level_m is None:
            near = terrain.nearest_water(sit.x_m * 100, sit.y_m * 100, WATER_SEARCH_M * 100)
    except (MemoryError, OSError, ValueError):
        return None, NOT_READ
    water_m: float | None
    below: float | None
    if pad.water_level_m is not None:
        water_m, below = 0.0, pad.water_below_ground_m
    elif near is not None and near.distance_m is not None:
        water_m = near.distance_m
        level = near.level_m
        below = (
            None if pad.z_median_m is None or level is None else round(pad.z_median_m - level, 1)
        )
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


def _progress_lost(now: SiteBuilt, here: SiteBuilt) -> SiteLoss | None:
    """The ``loses`` block: auto detection counts fewer built at the pad than at the site."""
    had, has = now.get("built"), here.get("built")
    if not had or here.get("mode") != "auto" or (has or 0) >= had:
        return None
    total = here.get("total") or now.get("total") or 0
    text = f"{had} of {total} built here → {has or 0} at the new spot"
    return {"now": had, "here": has or 0, "total": total, "text": text}


def _preview_base(
    st: WorldState, sess: PreviewSession, sit: siting_mod.Siting, include_static: bool
) -> SitePreviewResponse:
    """The response with the pad's own facts filled and every measured field still blank;
    ``token`` is the web's to fill."""
    from ...spatial.regions import load_regions

    x_cm, y_cm = sit.x_m * 100, sit.y_m * 100
    mx0, my0, mx1, my1 = geo.MAP_SQUARE_M
    cx0, cy0, cx1, cy1 = geo.CONTENT_BBOX
    try:
        region = load_regions().label_for(x_cm, y_cm).describe()
    except (OSError, ValueError, KeyError):
        region = ""
    out: SitePreviewResponse = {
        "key": sess.stored.key,
        "rev": sess.stored.rev,
        "save_id": save_id(st),
        "token": "",
        "x_m": sit.x_m,
        "y_m": sit.y_m,
        "yaw_deg": sit.yaw_deg,
        "w_m": sit.width_m,
        "d_m": sit.depth_m,
        "source": sit.source,
        "sited": siting_mod.parse(sess.stored) is not None,
        "in_map": all(mx0 <= x <= mx1 and my0 <= y <= my1 for x, y in sit.corners_m()),
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
        "now": sess.built_now,
        "basis": "",
        "loses": None,
        "fits": [],
        "overlaps": [],
        "nodes": None,
        "content_bbox_m": [v / 100 for v in geo.CONTENT_BBOX] if include_static else None,
        "failure": sess.failure,
    }
    if include_static and sess.prepared.request is not None:
        out["nodes"] = []
    return out


def _trunk_rows(
    g: GameData, sess: PreviewSession, x_cm: float, y_cm: float, dest_z: float | None
) -> tuple[list[SiteTrunk], list[str], set[str]]:
    """The plan's trunks toward the pad, its placeless inputs, and the nodes the trunks use."""
    trunks = plan_trunks(sess.prepared, g, (x_cm, y_cm))
    rows: list[SiteTrunk] = []
    chosen: set[str] = set()
    for trunk in trunks.trunks:
        chosen |= {m.instance for m in trunk.members}
        lift = trunk.lift_to_site_m(dest_z)
        pumps = None
        if trunk.carrier == "pipe" and lift is not None and dest_z is not None and sess.pump_head_m:
            climb = trunk.members[0].z / 100.0 - dest_z
            pumps = math.ceil(-climb / sess.pump_head_m - 1e-9) if climb < 0 else 0
        rows.append(
            {
                "name": trunk.name,
                "carrier": trunk.carrier,
                "members": len(trunk.members),
                "run_m": round(trunk.run_m, 1),
                "to_site_m": round(trunk.to_site_m((x_cm, y_cm)), 1),
                "lift_m": lift,
                "pumps": pumps,
            }
        )
    placeless = [f"{n} {name}" for name, _rate, n in trunks.placeless]
    return rows, placeless, chosen


def _chosen_nodes(g: GameData, sess: PreviewSession, chosen: set[str]) -> list[SitePreviewNode]:
    request = sess.prepared.request
    if request is None:
        return []
    return [
        {
            "instance": instance_leaf(r["instance"]),
            "resource": g.item_name(r["resource"]),
            "x_m": round(r["x"] / 100, 1),
            "y_m": round(r["y"] / 100, 1),
        }
        for r in request.node_rows
        if r.get("kind") == "node" and instance_leaf(r["instance"]) in chosen
    ]


def _fit_candidates(sit: siting_mod.Siting, here: SiteBuilt, when: str) -> list[SiteFit]:
    """Up to ``FIT_CANDIDATES`` auto-detected clusters the pad does not cover, as fitted pads."""
    fits: list[SiteFit] = []
    for c in here["candidates"] if here["mode"] == "auto" else []:
        bbox = c["bbox_m"]
        if bbox and not _covers(sit, bbox):
            fits.append(
                {
                    "name": c["name"],
                    "machines": c["machines"],
                    "value": siting_mod.fit_to_bbox(bbox, c["name"], when),
                }
            )
    return fits[:FIT_CANDIDATES]


def preview(
    g: GameData,
    st: WorldState,
    sess: PreviewSession,
    sit: siting_mod.Siting,
    *,
    terrain: heightfield.Field | None = None,
    terrain_cap: int = 0,
    include_static: bool = False,
) -> dict:
    """``SitePreviewResponse`` for ``sit``; writes nothing. ``terrain`` is a loaded field or None.

    ``include_static`` adds what does not move with the pad: the plan's chosen nodes and the
    playable box. A plain dict for the text presenter, which reads it as one.
    """
    out = _preview_base(st, sess, sit, include_static)
    if not out["in_map"]:
        return dict(out)
    z = siting_mod.ground_z(sit.x_m, sit.y_m, sit.yaw_deg, sit.width_m, sit.depth_m)
    if z is not None:
        out["z_m"], out["z_note"] = z, ""
    out["terrain"], out["terrain_note"] = _pad_terrain(sit, terrain, terrain_cap)
    x0, y0, x1, y1 = sit.bbox_cm()
    out["slabs"] = [
        f"{s.tiles:,}-tile slab, {s.storeys} {'storey' if s.storeys == 1 else 'storeys'}"
        for s in st.structures.slabs
        if s.bbox[0] <= x1 and s.bbox[2] >= x0 and s.bbox[1] <= y1 and s.bbox[3] >= y0
    ][:4]
    out["overlaps"] = [name for name, other in sess.pads if sit.overlaps(other)]
    solution = sess.prepared.solution
    if sess.failure or solution is None:
        out["terrain_note"] = out["terrain_note"] or sess.failure
        return dict(out)
    survey = siting_mod.survey(g, st, sit, solution.processes)
    if survey is not None:
        out["on_pad"], out["planned"] = survey.standing_total, survey.planned_total
    terrain_read = out["terrain"]
    dest_z = out["z_m"] if out["z_m"] is not None else (terrain_read or {}).get("z_median_m")
    out["trunks"], out["placeless"], chosen = _trunk_rows(
        g, sess, sit.x_m * 100, sit.y_m * 100, dest_z
    )
    if out["nodes"] is not None:
        out["nodes"] = _chosen_nodes(g, sess, chosen)
    moved = dataclasses.replace(sess.stored, siting=sit.to_dict())
    here = _built_progress(g, st, sess, moved)
    out["built"] = here
    if not out["sited"] and sess.built_now.get("area"):
        out["basis"] = f"counted on its pad from now (was: {sess.built_now['area']})"
    out["loses"] = _progress_lost(sess.built_now, here)
    out["fits"] = _fit_candidates(sit, here, str(st.header.get("save_datetime") or ""))
    return dict(out)

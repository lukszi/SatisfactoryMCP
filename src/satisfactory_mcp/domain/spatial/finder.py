"""Resource node search, once: the selection, each node's status, its fields and their ranking.

``search_resource_nodes``, ``rank_build_sites`` and the World routes answer from here, so a
tool and a page asked the same question read the same rows. Presentation stays with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from ...core.text import num
from ..world.sites import selector as site_selector
from . import geo, heightfield, ranking
from . import nodes as nodes_mod
from . import regions as regions_mod
from .origin import resolve_origin
from .select import Selection, select_nodes

__all__ = [
    "FIELD_LINK_M",
    "STATUSES",
    "VIEWS",
    "FieldView",
    "NodeFind",
    "SiteRank",
    "choices",
    "fields",
    "find_nodes",
    "rank",
    "site_view",
    "status_of",
]

VIEWS = ("fields", "nodes", "nearest")
STATUSES = ("all", "free", "tapped")
PURITIES = ("pure", "normal", "impure")
KINDS = ("node", "well_sat", "geyser")
FIELD_LINK_M = 200.0

WATER = "Desc_Water_C"
WATER_PUMP = "Build_WaterPump_C"


def status_of(row: dict) -> str:
    if row["tapped"]:
        return "tapped"
    return "free" if row["reachable"] else "locked"


def _leaf(instance: str) -> str:
    return str(instance).rsplit(".", 1)[-1]


@dataclass
class FieldView:
    members: list[dict]
    centroid: tuple[float, float, float]
    diameter_m: float
    region: str | None
    grid: str
    direction: str
    purities: dict[str, int]
    resources: list[str]
    total: float
    free: float
    locked: bool
    spoiler: bool
    distance_m: float | None

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def key(self) -> str:
        return "field:" + min(_leaf(m["instance"]) for m in self.members)

    @property
    def selector(self) -> str:
        return site_selector(self.centroid, self.diameter_m)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [m["x"] for m in self.members]
        ys = [m["y"] for m in self.members]
        return min(xs), min(ys), max(xs), max(ys)


def fields(
    rows: list[dict], origin: tuple[float, float] | None = None, link_m: float = FIELD_LINK_M
) -> list[FieldView]:
    rm = regions_mod.load_regions()
    out = []
    for c in geo.cluster(rows, link_m=link_m):
        cx, cy, _cz = c.centroid
        out.append(
            FieldView(
                members=c.members,
                centroid=c.centroid,
                diameter_m=c.diameter_m,
                region=rm.label_for(cx, cy).name,
                grid=geo.grid_cell(cx, cy),
                direction=geo.direction_of(cx, cy),
                purities=c.purities(),
                resources=sorted({m["resource"] for m in c.members}),
                total=sum(m["rate"] for m in c.members),
                free=sum(m["rate"] for m in c.members if not m["tapped"] and m["reachable"]),
                locked=not all(m["reachable"] for m in c.members),
                spoiler=not any(m["reachable"] for m in c.members),
                distance_m=(
                    None
                    if origin is None
                    else min(geo.distance_m((m["x"], m["y"]), origin) for m in c.members)
                ),
            )
        )
    return out


@dataclass
class NodeFind:
    view: str
    status: str = "all"
    error: str | None = None
    selection: Selection = field(default_factory=Selection)
    selectors: list[str] = field(default_factory=list)
    origin: tuple[float, float] | None = None
    where: str = ""
    rows: list[dict] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    unit: str = ""
    total: float = 0.0
    free: float = 0.0
    locked_rate: float = 0.0
    elevation: tuple[float, float] | None = None
    water: dict | None = None
    notes: list[str] = field(default_factory=list)
    skew: nodes_mod.TableSkew | None = None
    save_read: bool = False
    hidden: int = 0

    @property
    def description(self) -> str:
        return self.selection.description

    @property
    def errors(self) -> list[str]:
        return self.selection.errors

    @property
    def unselected(self) -> bool:
        return bool(self.selection.errors) and not self.selection.nodes

    @property
    def mixed(self) -> bool:
        return len(self.resources) > 1

    @cached_property
    def fields(self) -> list[FieldView]:
        return fields(self.rows, self.origin)

    @cached_property
    def drifted(self) -> set[str]:
        return nodes_mod.drifted(self.skew, [r["instance"] for r in self.rows])


def _unit(game, resources: list[str]) -> str:
    if len(resources) > 1:
        return "mixed"
    item = game.items.get(resources[0]) if resources else None
    return "m3/min" if item is not None and item.is_fluid else "/min"


def _water(st, game) -> dict:
    wv = st.water_volumes()
    pump = game.buildings.get(WATER_PUMP)
    return {
        "bodies": dict(wv["volumes"]),
        "pumps": wv["pumps"],
        "per_pump_m3_min": pump.extract_rate("normal", 1.0) if pump else None,
        "sea_level_m": wv["sea_level_m"],
        "sea_level_span_m": wv["sea_level_span_m"],
    }


WATER_NOTE = (
    "open water carries NO NODE: a Water Extractor is placed on a shoreline, has no "
    "purity and draws a flat rate, so there is no node cap for it to be free "
    "against. Every row above is a fracking satellite, which does sit on a node"
)
WATER_BODY_NOTE = (
    "a body is the FGWaterVolume each pump's mExtractableResource names. Its "
    "SHAPE is level geometry and is not in the save, so this says how many "
    "separate shorelines are already worked, not how much is left in them -- "
    "and sea level is measured off those pumps, not assumed"
)


def page_notes(found: NodeFind, st) -> list[str]:
    """The tool's notes in the page's words; table age travels as ``stale`` instead."""
    notes = list(found.selection.errors)
    if WATER in found.resources:
        notes.append(
            "open water is not a node: a water extractor on a shoreline draws a flat rate, "
            "so it is never counted as free; the water rows here are fracking satellites"
        )
    if st is None:
        notes.append("no save read, so every node shows as free")
        return notes
    if found.locked_rate:
        notes.append(
            f"{found.locked_rate:,.0f} {'m³/min' if found.unit == 'm3/min' else 'per min'} left out of free: "
            "it needs an extractor not unlocked yet"
        )
    unmatched = len(nodes_mod.unresolved_extractors(st.projection))
    if unmatched:
        notes.append(
            f"{unmatched:,} extractors are not matched to a node (mostly water pumps), "
            "so free may read high"
        )
    return notes


def find_nodes(
    st,
    game,
    *,
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: str | None = None,
    kind: str | None = None,
    status: str = "all",
    view: str = "fields",
    near: str | None = None,
    resolve_resource=None,
    hide_locked: bool = False,
) -> NodeFind:
    """The node search behind ``search_resource_nodes`` and ``/api/world/nodes``.

    ``view`` and ``status`` arrive validated. ``st`` may be ``None``: the table needs no
    save, and without one every node reads as free and reachable, which a note says.
    ``hide_locked`` drops the nodes no unlocked extractor can work before anything is
    counted (the page's spoiler switch); ``hidden`` says how many.
    """
    table = nodes_mod.load_nodes()
    found = NodeFind(view=view, status=status, save_read=st is not None)
    spec = list(sources or [])
    for extra, value in (("resource", resource), ("purity", purity), ("kind", kind)):
        if value:
            spec.append(f"{extra}:{value}")
    found.selectors = spec
    if near:
        try:
            found.origin, found.where = resolve_origin(st, near)
        except ValueError as exc:
            found.error = f"! {exc}"
            return found
    if view == "nearest" and found.origin is None:
        found.error = "! the nearest view needs a place to measure from"
        return found

    found.selection = select_nodes(
        spec or None, table.nodes, resolve_resource=resolve_resource, st=st
    )
    if found.unselected:
        return found
    rows = nodes_mod.annotate(
        found.selection.nodes,
        game,
        st.projection if st else None,
        st.unlocked_building_ids if st else None,
    )
    if status == "free":
        rows = [r for r in rows if not r["tapped"]]
    elif status == "tapped":
        rows = [r for r in rows if r["tapped"]]
    if hide_locked:
        kept = [r for r in rows if status_of(r) != "locked"]
        found.hidden = len(rows) - len(kept)
        rows = kept
    if found.origin is not None:
        for r in rows:
            r["distance_m"] = geo.distance_m((r["x"], r["y"]), found.origin)
    if view == "nearest":
        rows.sort(key=lambda r: r["distance_m"])
    elif view == "nodes":
        rows.sort(key=lambda r: (-r["rate"], r["instance"]))
    found.rows = rows
    if not rows:
        return found

    found.resources = sorted({r["resource"] for r in rows})
    found.unit = _unit(game, found.resources)
    found.total = sum(r["rate"] for r in rows)
    found.free = nodes_mod.capacity(rows, only_free=True)
    found.locked_rate = sum(r["rate"] for r in rows if not r["reachable"])

    notes = []
    if WATER in found.resources:
        notes.append(WATER_NOTE)
        if st is not None:
            notes.append(WATER_BODY_NOTE)
            found.water = _water(st, game)
    notes += found.selection.errors
    if st is None:
        notes.append("no save read: tapped/free unknown, everything shown as free")
    else:
        if found.locked_rate:
            notes.append(
                f"{num(found.locked_rate)} excluded from free: needs an extractor this "
                "world has not unlocked (marked LOCKED)"
            )
        unres = nodes_mod.unresolved_extractors(st.projection)
        if unres:
            notes.append(
                f"{len(unres)} extractor(s) unmatched to a node (mostly water pumps), "
                "so free may be overstated"
            )
        found.skew = nodes_mod.skew_for_save(st.header, table)
        notes += nodes_mod.skew_notes(found.skew, [r["instance"] for r in rows])
    found.notes = notes

    zs = [r["z"] / 100.0 for r in rows if "z" in r]
    if zs and any(game.items[r].is_fluid for r in found.resources if r in game.items):
        found.elevation = (min(zs), max(zs))
    return found


def choices(game) -> dict:
    table = nodes_mod.load_nodes()
    counts: dict[str, int] = {}
    for n in table.nodes:
        counts[n["resource"]] = counts.get(n["resource"], 0) + 1
    names = {rid: (game.item_name(rid) if rid in game.items else rid) for rid in counts}
    return {
        "resources": [
            {"id": rid, "name": names[rid], "nodes": counts[rid]}
            for rid in sorted(counts, key=lambda r: names[r])
        ],
        "purities": sorted({n["purity"] for n in table.nodes}),
        "kinds": sorted({n["kind"] for n in table.nodes}),
    }


@dataclass
class SiteRank:
    resource: str
    selection: Selection
    rows: list[dict] = field(default_factory=list)
    scored: list[ranking.SiteScore] = field(default_factory=list)
    terrain: bool = False
    consumer_z: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def unselected(self) -> bool:
        return bool(self.selection.errors) and not self.selection.nodes


def rank(
    st, game, resource: str, sources: list[str] | None = None, resolve_resource=None
) -> SiteRank:
    """Candidate fields of one resource, best first; ``resource`` is a resolved class id."""
    table = nodes_mod.load_nodes()
    spec = [*(sources or []), f"resource:{resource}"]
    sel = select_nodes(spec, table.nodes, resolve_resource=resolve_resource, st=st)
    out = SiteRank(resource=resource, selection=sel)
    if out.unselected:
        return out
    out.rows = nodes_mod.annotate(sel.nodes, game, st.projection, st.unlocked_building_ids)
    terrain = heightfield.load_field()
    out.terrain = terrain is not None
    out.consumer_z = st.consumer_z()
    out.scored = ranking.rank_sites(
        geo.cluster(out.rows, link_m=FIELD_LINK_M),
        infra=st.infra_points(),
        consumer_z=out.consumer_z,
        terrain=terrain,
    )
    out.notes = nodes_mod.skew_notes(
        nodes_mod.skew_for_save(st.header, table), [r["instance"] for r in out.rows]
    )
    return out


def site_view(sc: ranking.SiteScore, rm=None) -> dict:
    rm = rm or regions_mod.load_regions()
    cx, cy, _cz = sc.centroid
    raw = sc.raw
    return {
        "score": sc.score,
        "region": rm.label_for(cx, cy).name,
        "grid": geo.grid_cell(cx, cy),
        "x": cx,
        "y": cy,
        "selector": site_selector(sc.centroid, sc.cluster.diameter_m),
        "nodes": raw["nodes"],
        "untapped": raw["untapped_rate"],
        "spread_m": raw["spread_m"],
        "to_infra_m": raw["distance_to_infra_m"],
        "purity": raw["purity_quality"],
        "alt_m": raw["altitude_vs_consumer_m"],
        "rough_m": raw["pad_roughness_m"],
        "slope_deg": raw["pad_slope_deg"],
        "wet_pct": raw["pad_submerged_pct"],
    }

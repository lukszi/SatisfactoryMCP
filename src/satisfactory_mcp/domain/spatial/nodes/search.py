"""Resource node search, once: the selection, each node's status, its fields and their ranking.

``search_resource_nodes``, ``rank_build_sites`` and the World routes answer from here, so a
tool and a page asked the same question read the same rows. Both wordings of the notes live
here too: ``NodeSearchResult.notes`` for the tool and ``page_notes`` for the page.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from ....core.gamedata.constants import WATER_PUMP
from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ....core.text import num
from ...world.sites import near_selector
from .. import geo, heightfield, ranking
from .. import regions as regions_mod
from ..places import resolve_place
from . import extraction as node_extraction
from . import skew as node_skew
from . import table as node_table
from .extraction import AnnotatedNode, measure_from
from .selectors import ResourceResolver, Selection, select_nodes
from .views import NodeChoices, SiteRow, WaterSummary

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = [
    "STATUSES",
    "VIEWS",
    "FieldView",
    "NodeSearchResult",
    "SiteRank",
    "filter_choices",
    "find_nodes",
    "group_into_fields",
    "page_notes",
    "rank_build_sites",
    "site_row",
    "status_of",
]

VIEWS = ("fields", "nodes", "nearest")
STATUSES = ("all", "free", "tapped")

WATER = "Desc_Water_C"


def status_of(row: Mapping[str, object]) -> str:
    if row["tapped"]:
        return "tapped"
    return "free" if row["reachable"] else "locked"


@dataclass
class FieldView:
    members: list[AnnotatedNode]
    centroid: tuple[float, float, float]
    diameter_m: float
    region: str | None
    grid: str
    direction: str
    purities: dict[str, int]
    resources: list[str]
    total_rate: float
    free_rate: float
    locked: bool
    spoiler: bool
    distance_m: float | None

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def key(self) -> str:
        return "field:" + min(instance_leaf(m["instance"]) for m in self.members)

    @property
    def selector(self) -> str:
        return near_selector(self.centroid, self.diameter_m)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [m["x"] for m in self.members]
        ys = [m["y"] for m in self.members]
        return min(xs), min(ys), max(xs), max(ys)


def group_into_fields(
    rows: list[AnnotatedNode],
    origin: tuple[float, float] | None = None,
    link_m: float = geo.FIELD_LINK_M,
) -> list[FieldView]:
    """Annotated node rows clustered into fields, each placed and totalled."""
    region_map = regions_mod.load_regions()
    out: list[FieldView] = []
    for cluster in geo.cluster(rows, link_m=link_m):
        cx, cy, _cz = cluster.centroid
        out.append(
            FieldView(
                members=cluster.members,
                centroid=cluster.centroid,
                diameter_m=cluster.diameter_m,
                region=region_map.label_for(cx, cy).name,
                grid=geo.grid_cell(cx, cy),
                direction=geo.direction_of(cx, cy),
                purities=cluster.purities(),
                resources=sorted({m["resource"] for m in cluster.members}),
                total_rate=sum(m["rate"] for m in cluster.members),
                free_rate=node_extraction.untapped_rate(cluster.members),
                locked=not all(m["reachable"] for m in cluster.members),
                spoiler=not any(m["reachable"] for m in cluster.members),
                distance_m=(
                    None
                    if origin is None
                    else min(geo.distance_m((m["x"], m["y"]), origin) for m in cluster.members)
                ),
            )
        )
    return out


@dataclass
class NodeSearchResult:
    view: str
    status: str = "all"
    error: str | None = None
    selection: Selection = field(default_factory=Selection)
    selectors: list[str] = field(default_factory=list[str])
    origin: tuple[float, float] | None = None
    where: str = ""
    rows: list[AnnotatedNode] = field(default_factory=list[AnnotatedNode])
    resources: list[str] = field(default_factory=list[str])
    unit: str = ""
    total: float = 0.0
    free: float = 0.0
    locked_rate: float = 0.0
    elevation: tuple[float, float] | None = None
    water: WaterSummary | None = None
    notes: list[str] = field(default_factory=list[str])
    skew: node_skew.TableSkew | None = None
    save_read: bool = False
    #: Extractors the save does not join to a node, which makes ``free`` read high.
    unmatched_extractors: int = 0

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
        return group_into_fields(self.rows, self.origin)

    @cached_property
    def drifted_leaf_names(self) -> set[str]:
        return node_skew.drifted_leaf_names(self.skew, [r["instance"] for r in self.rows])


def _rate_unit(game: GameData, resources: list[str]) -> str:
    if len(resources) > 1:
        return "mixed"
    item = game.items.get(resources[0]) if resources else None
    return "m3/min" if item is not None and item.is_fluid else "/min"


def _water_summary(st: WorldState, game: GameData) -> WaterSummary:
    water_volumes = st.water_volumes()
    pump = game.buildings.get(WATER_PUMP)
    return {
        "bodies": dict(water_volumes["volumes"]),
        "pumps": water_volumes["pumps"],
        "per_pump_m3_min": pump.extract_rate("normal", 1.0) if pump else None,
        "sea_level_m": water_volumes["sea_level_m"],
        "sea_level_span_m": water_volumes["sea_level_span_m"],
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


def page_notes(found: NodeSearchResult, st: WorldState | None) -> list[str]:
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
    if found.unmatched_extractors:
        notes.append(
            f"{found.unmatched_extractors:,} extractors are not matched to a node (mostly water "
            "pumps), so free may read high"
        )
    return notes


def _selector_spec(
    sources: list[str] | None, resource: str | None, purity: str | None, kind: str | None
) -> list[str]:
    """The source selectors plus the plain filter parameters spelled as their terms."""
    spec = list(sources or [])
    for name, value in (("resource", resource), ("purity", purity), ("kind", kind)):
        if value:
            spec.append(f"{name}:{value}")
    return spec


def _filter_by_status(rows: list[AnnotatedNode], status: str) -> list[AnnotatedNode]:
    if status == "free":
        return [r for r in rows if not r["tapped"]]
    if status == "tapped":
        return [r for r in rows if r["tapped"]]
    return rows


def _by_rate(row: AnnotatedNode) -> tuple[float, str]:
    return -row["rate"], row["instance"]


def _ordered_for_view(
    rows: list[AnnotatedNode], origin: tuple[float, float] | None, view: str
) -> list[AnnotatedNode]:
    """The rows in the view's order; measured from ``origin`` when there is one."""
    if origin is None:
        if view == "nodes":
            rows.sort(key=_by_rate)
        return rows
    measured = measure_from(rows, origin)
    if view == "nearest":
        measured.sort(key=lambda r: r["distance_m"])
    elif view == "nodes":
        measured.sort(key=_by_rate)
    return [*measured]


def _tool_notes(
    found: NodeSearchResult,
    st: WorldState | None,
    game: GameData,
    table: node_table.NodeTable,
) -> list[str]:
    """The tool's notes; sets ``found.water`` and ``found.skew`` on the way."""
    notes: list[str] = []
    if WATER in found.resources:
        notes.append(WATER_NOTE)
        if st is not None:
            notes.append(WATER_BODY_NOTE)
            found.water = _water_summary(st, game)
    notes += found.selection.errors
    if st is None:
        notes.append("no save read: tapped/free unknown, everything shown as free")
        return notes
    if found.locked_rate:
        notes.append(
            f"{num(found.locked_rate)} excluded from free: needs an extractor this "
            "world has not unlocked (marked LOCKED)"
        )
    if found.unmatched_extractors:
        notes.append(
            f"{found.unmatched_extractors} extractor(s) unmatched to a node (mostly water pumps), "
            "so free may be overstated"
        )
    found.skew = node_skew.skew_for_save(st.header, table)
    notes += node_skew.skew_notes(found.skew, [r["instance"] for r in found.rows])
    return notes


def _fluid_elevation_span(
    rows: list[AnnotatedNode], resources: list[str], game: GameData
) -> tuple[float, float] | None:
    """``(lowest, highest)`` node height in metres when a fluid is among the resources."""
    heights_m = [r["z"] / 100.0 for r in rows if "z" in r]
    if heights_m and any(game.items[r].is_fluid for r in resources if r in game.items):
        return (min(heights_m), max(heights_m))
    return None


def find_nodes(
    st: WorldState | None,
    game: GameData,
    *,
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: str | None = None,
    kind: str | None = None,
    status: str = "all",
    view: str = "fields",
    near: str | None = None,
    resolve_resource: ResourceResolver | None = None,
) -> NodeSearchResult:
    """The node search behind ``search_resource_nodes`` and ``/api/world/nodes``.

    ``view`` and ``status`` arrive validated. ``st`` may be ``None``: the table needs no
    save, and without one every node reads as free and reachable, which a note says.
    """
    table = node_table.load_nodes()
    found = NodeSearchResult(view=view, status=status, save_read=st is not None)
    spec = _selector_spec(sources, resource, purity, kind)
    found.selectors = spec
    if near:
        try:
            found.origin, found.where = resolve_place(st, near)
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
    if st is not None:
        found.unmatched_extractors = len(node_extraction.unresolved_extractors(st.projection))
    rows = node_extraction.annotate_for_save(found.selection.nodes, game, st)
    rows = _ordered_for_view(_filter_by_status(rows, status), found.origin, view)
    found.rows = rows
    if not rows:
        return found

    found.resources = sorted({r["resource"] for r in rows})
    found.unit = _rate_unit(game, found.resources)
    found.total = sum(r["rate"] for r in rows)
    found.free = node_extraction.untapped_rate(rows)
    found.locked_rate = sum(r["rate"] for r in rows if not r["reachable"])
    found.notes = _tool_notes(found, st, game, table)
    found.elevation = _fluid_elevation_span(rows, found.resources, game)
    return found


def filter_choices(game: GameData) -> NodeChoices:
    """Every resource the node table holds, with its node count, and the purities and kinds."""
    table = node_table.load_nodes()
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
    rows: list[AnnotatedNode] = field(default_factory=list[AnnotatedNode])
    scored: list[ranking.SiteScore] = field(default_factory=list[ranking.SiteScore])
    terrain: bool = False
    consumer_z: float | None = None
    notes: list[str] = field(default_factory=list[str])

    @property
    def unselected(self) -> bool:
        return bool(self.selection.errors) and not self.selection.nodes


def rank_build_sites(
    st: WorldState,
    game: GameData,
    resource: str,
    sources: list[str] | None = None,
    resolve_resource: ResourceResolver | None = None,
) -> SiteRank:
    """Candidate fields of one resource, best first; ``resource`` is a resolved class id."""
    table = node_table.load_nodes()
    spec = [*(sources or []), f"resource:{resource}"]
    selection = select_nodes(spec, table.nodes, resolve_resource=resolve_resource, st=st)
    out = SiteRank(resource=resource, selection=selection)
    if out.unselected:
        return out
    out.rows = node_extraction.annotate_for_save(selection.nodes, game, st)
    terrain = heightfield.load_field()
    out.terrain = terrain is not None
    out.consumer_z = st.consumer_z()
    out.scored = ranking.rank_sites(
        geo.cluster(out.rows, link_m=geo.FIELD_LINK_M),
        infra=st.infra_points(),
        consumer_z=out.consumer_z,
        terrain=terrain,
    )
    out.notes = node_skew.skew_notes(
        node_skew.skew_for_save(st.header, table), [r["instance"] for r in out.rows]
    )
    return out


def site_row(score: ranking.SiteScore, region_map: regions_mod.RegionMap | None = None) -> SiteRow:
    """One ranked site as the tool and the page both list it."""
    region_map = region_map or regions_mod.load_regions()
    cx, cy, _cz = score.centroid
    raw = score.raw
    return {
        "score": score.score,
        "region": region_map.label_for(cx, cy).name,
        "grid": geo.grid_cell(cx, cy),
        "x": cx,
        "y": cy,
        "selector": near_selector(score.centroid, score.cluster.diameter_m),
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

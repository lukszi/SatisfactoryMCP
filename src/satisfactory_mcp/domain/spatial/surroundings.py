"""What is at a place: where the player stands, and what surrounds any coordinate.

``whereami``, ``describe_location``, ``/api/world/here`` and ``/api/inspect`` answer from
here. Coordinates are centimetres in and out; the callers convert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..collectibles import service as collectibles
from ..world import conduits as conduits_mod
from . import elevation, geo
from . import nodes as nodes_mod
from . import regions as regions_mod
from .nodes import search as node_search

__all__ = [
    "PICKUP_REACH_M",
    "PlayerSurroundings",
    "PointDescription",
    "describe_point",
    "nearest_nodes",
    "pickups_near",
    "player_surroundings",
]

PICKUP_REACH_M = 500.0
FIELD_REACH_M = 500.0
NEAREST = 5
FIELDS = 3
PICKUPS = 5


@dataclass
class PlayerSurroundings:
    player: tuple[float, float, float] | None
    radius_m: float
    label: regions_mod.Label | None = None
    nodes: list[dict] = field(default_factory=list)
    nearest_building: tuple[str, float] | None = None
    pawns: int = 0
    skew: nodes_mod.TableSkew | None = None
    notes: list[str] = field(default_factory=list)


def _with_distances(rows: list[dict], x: float, y: float, *, direction: bool = False) -> list[dict]:
    """``rows`` each stamped with ``distance_m`` from the point, and its compass word."""
    for row in rows:
        row["distance_m"] = geo.distance_m((row["x"], row["y"]), (x, y))
        if direction:
            row["direction"] = geo.direction_of(row["x"], row["y"], x, y)
    return rows


def _nearest_building(st, game, x: float, y: float) -> tuple[str, float] | None:
    """The closest placed record's building name and distance, or ``None`` for none."""
    builds = [(r["cls"], pos) for r in st.all_records() if (pos := r.get("pos"))]
    closest = min(
        builds,
        key=lambda build: geo.distance_m((build[1][0], build[1][1]), (x, y)),
        default=None,
    )
    if closest is None:
        return None
    cls, pos = closest
    name = game.buildings[cls].name if cls in game.buildings else cls
    return name, geo.distance_m((pos[0], pos[1]), (x, y))


def player_surroundings(st, game, radius_m: float = 500.0) -> PlayerSurroundings:
    """The player's position and the nodes within ``radius_m`` of it, nearest first."""
    pos = st.player_position()
    out = PlayerSurroundings(player=pos, radius_m=radius_m, pawns=len(st.players))
    if pos is None:
        return out
    x, y, _z = pos
    out.label = regions_mod.load_regions().label_for(x, y)
    table = nodes_mod.load_nodes()
    near = nodes_mod.annotate_for_save(table.within((x, y), radius_m), game, st)
    _with_distances(near, x, y, direction=True)
    near.sort(key=lambda n: n["distance_m"])
    out.nodes = near
    out.nearest_building = _nearest_building(st, game, x, y)
    out.skew = nodes_mod.skew_for_save(st.header, table)
    out.notes = nodes_mod.skew_notes(out.skew, [n["instance"] for n in near])
    return out


def nearest_nodes(st, game, x: float, y: float, limit: int = NEAREST) -> list[dict]:
    """The ``limit`` nodes closest to a point, annotated, each with ``distance_m``."""
    table = nodes_mod.load_nodes()
    ranked = sorted(table.nodes, key=lambda n: geo.distance_m((x, y), (n["x"], n["y"])))[:limit]
    return _with_distances(nodes_mod.annotate_for_save(ranked, game, st), x, y)


def _fields_near(st, game, x: float, y: float) -> list[node_search.FieldView]:
    table = nodes_mod.load_nodes()
    close = {
        n["resource"]
        for n in table.nodes
        if geo.distance_m((x, y), (n["x"], n["y"])) <= FIELD_REACH_M
    }
    out: list[node_search.FieldView] = []
    for resource_id in sorted(close):
        rows = nodes_mod.annotate_for_save(table.by_resource(resource_id), game, st)
        out += [
            f for f in node_search.group_into_fields(rows, (x, y)) if f.distance_m <= FIELD_REACH_M
        ]
    out.sort(key=lambda f: f.distance_m)
    return out


def pickups_near(st, x: float, y: float) -> list[dict]:
    table = st.collectibles
    if table is None:
        return []
    pedestals = {c for c in table.by_category if table.pedestal_of(c)}
    reach = PICKUP_REACH_M * geo.CM_PER_M
    close = [
        c
        for c, rows in table.by_category.items()
        if c not in pedestals
        and any(abs(r["x"] - x) <= reach and abs(r["y"] - y) <= reach for r in rows)
    ]
    out = []
    for category in close:
        for r in st.placements(category, remaining_only=True):
            r["distance_m"] = geo.distance_m((r["pos"][0], r["pos"][1]), (x, y))
            if r["distance_m"] <= PICKUP_REACH_M:
                out.append(r)
    if not out:
        return out
    out.sort(key=lambda r: r["distance_m"])
    have = set(collectibles.found(st))
    for r in out:
        r["label"] = collectibles.label(r["category"])
        r["spoiler"] = collectibles.is_spoiler(r["category"], have)
    return out


@dataclass
class PointDescription:
    x: float
    y: float
    radius_m: float
    label: regions_mod.Label
    probe: elevation.Elevation
    conduits: dict[str, int] | None
    conduit_radius_m: float
    nearest: list[dict]
    fields: list[node_search.FieldView]
    fields_total: int
    pickups: list[dict]
    pickups_total: int | None
    pickups_spoilers: int
    skew: nodes_mod.TableSkew | None
    notes: list[str]


def describe_point(
    st,
    game,
    x: float,
    y: float,
    radius_m: float = 200.0,
    terrain_field=None,
    hint_z_cm: float | None = None,
) -> PointDescription:
    """Region, sampled elevation, conduits, nearest nodes, fields and pickups at a point.

    ``radius_m`` is the elevation reach; conduits count within ``conduits.NEAR_RADIUS_M``.

    ``st`` may be ``None``: the node table and the regions need no save, and what needs one
    comes back empty (``conduits`` and ``pickups_total`` as ``None``).
    """
    table = nodes_mod.load_nodes()
    probe = elevation.probe(
        x,
        y,
        elevation.sample_points(table, st),
        radius_m,
        terrain_field=terrain_field,
        hint_z_cm=hint_z_cm,
    )
    fields = _fields_near(st, game, x, y)
    pickups = pickups_near(st, x, y) if st is not None else []
    skew = nodes_mod.skew_for_save(st.header if st else None, table)
    return PointDescription(
        x=x,
        y=y,
        radius_m=radius_m,
        label=regions_mod.load_regions().label_for(x, y),
        probe=probe,
        conduits=(
            conduits_mod.near_counts(st.conduit_runs, x, y, conduits_mod.NEAR_RADIUS_M)
            if st
            else None
        ),
        conduit_radius_m=conduits_mod.NEAR_RADIUS_M,
        nearest=nearest_nodes(st, game, x, y),
        fields=fields[:FIELDS],
        fields_total=len(fields),
        pickups=pickups[:PICKUPS],
        pickups_total=len(pickups) if st is not None and st.collectibles is not None else None,
        pickups_spoilers=sum(1 for p in pickups if p["spoiler"]),
        skew=skew,
        notes=nodes_mod.position_notes(
            skew, [n["instance"] for n in table.within((x, y), radius_m)]
        ),
    )

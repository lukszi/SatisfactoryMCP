"""The cooked meshes: geometry reads, collision hulls and ``ExtendedBounds``."""

from __future__ import annotations

import struct
import time
from typing import NamedTuple, TypeAlias, TypedDict, TypeVar

import numpy as np

from mapgen.gamedata.install import open_package
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I32Grid, I64Grid
from satisfactory_mcp.core.gameassets import nanite, staticmesh
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    PackageView,
    ScriptObjects,
    ZenExport,
    property_tags,
)

__all__ = [
    "BOUNDS_INSIDE_MIN",
    "BOUNDS_PAD_CM",
    "BOUNDS_PAD_FRACTION",
    "CLIFF_SOURCES",
    "DIRECT_SAMPLES_MIN",
    "ROCK_DIRS",
    "CookedMesh",
    "MeshBounds",
    "MeshBox",
    "MeshGeometry",
    "bounds_pair",
    "clamp_triangles",
    "finer_source",
    "read_hull",
    "read_mesh_geometry",
    "winding_sign",
]


#: Which mesh trees carry terrain geometry. Everything else placed in the world is a tree,
#: a plant, a building or a prop, and none of those are ground.
ROCK_DIRS = ("/World/Environment/Rock/", "/World/Environment/Caves/")


#: Where the cliff layer's triangles may come from, finest first. The set of MESHES is
#: still decided by the cooked collision hull; this is only which of a mesh's own
#: descriptions of itself gets rasterised.
CLIFF_SOURCES = ("nanite", "lod0", "hull")

#: How far past its own ``ExtendedBounds`` a vertex may sit. Both a decode check -- most of
#: a candidate's vertices must be inside, which a misread stream cannot manage -- and, per
#: triangle, a clamp against stray far vertices. The sidecar records how often it fires.
BOUNDS_PAD_CM = staticmesh.BOUNDS_PAD_CM
BOUNDS_PAD_FRACTION = staticmesh.BOUNDS_PAD_FRACTION
BOUNDS_INSIDE_MIN = staticmesh.BOUNDS_INSIDE_MIN


#: How many source vertices a texel needs before its height is a measurement rather than an
#: interpolation across a triangle wider than itself. ``terrain.sample`` evaluates this rule
#: at its own texel size, so it is stated here only once.
DIRECT_SAMPLES_MIN = 1

#: ``(Origin, BoxExtent)``: a mesh's local box about its origin, in cm.
MeshBox: TypeAlias = tuple[tuple[float, float, float], tuple[float, float, float]]

_Tris = TypeVar("_Tris", I32Grid, I64Grid)


class CookedMesh(NamedTuple):
    """A rock mesh's finest geometry, mesh-local cm, and its ``ExtendedBounds`` padded."""

    verts: F32Grid
    tris: I64Grid
    low: F64Grid
    high: F64Grid


class MeshGeometry(TypedDict):
    """``read_mesh_geometry``'s meshes and the counts the sidecar records."""

    geometry: dict[str, CookedMesh]
    sources: dict[str, str]
    by_source: dict[str, int]
    failures: dict[str, str]
    wanted: int
    closed_manifolds: int
    nanite_closed: int
    nanite_checked: int
    hull_triangles: int
    verts: int
    tris: int
    seconds: float


class _MeshRead(NamedTuple):
    cooked: CookedMesh
    source: str
    hull_triangles: int
    #: The hull meets the closed-manifold Euler relation; ``nanite_closed`` is ``None``
    #: unless the source is Nanite, else whether its triangles leave no boundary edge.
    hull_closed: bool
    nanite_closed: bool | None


def finer_source(
    store: IoStore,
    package: str,
    view: PackageView,
    export: ZenExport | None,
    low: F64Grid,
    high: F64Grid,
) -> tuple[str, tuple[F32Grid, I64Grid]] | None:
    """The finest geometry this mesh ships, and which one that was -- or ``None``, as for a
    package without a ``StaticMesh`` export.

    Nanite first, LOD 0 second; ``sidecar_blocks.cliff_source`` records what each costs.
    A finer source is accepted only if it clears the same bounds check the hull does: the
    mesh's own serialised ``ExtendedBounds`` is the one statement available that does not
    come from this reader, so it is what stops plausible garbage from shipping.
    """
    if export is None:
        return None
    tail = staticmesh.render_tail(view, export)
    try:
        parsed = staticmesh.parse_render_data(tail)
    except staticmesh.ParseError:
        return None

    resource = staticmesh.load_nanite(store, package, view, parsed, tail)
    if resource is not None:
        decoded = nanite.decode_resource(resource)
        problems = staticmesh.page_table_problems(resource, staticmesh.bulk_size(view, resource))
        problems += nanite.identity_checks(resource, decoded)
        positions: F32Grid = decoded["positions"]
        triangles: I64Grid = decoded["triangles"]
        if not problems and len(triangles) and _inside_bounds(positions, low, high):
            return "nanite", (positions, triangles)

    got = staticmesh.lod0_buffers(tail, parsed)
    if got is not None and len(got[1]) and _inside_bounds(got[0], low, high):
        return "lod0", (got[0], got[1])
    return None


def _inside_bounds(verts: F32Grid, low: F64Grid, high: F64Grid) -> bool:
    if not np.isfinite(verts).all():
        return False
    pad = BOUNDS_PAD_CM + BOUNDS_PAD_FRACTION * float(np.max(np.asarray(high) - np.asarray(low)))
    inside = ((verts >= low - pad) & (verts <= high + pad)).all(axis=1)
    return bool(inside.mean() >= BOUNDS_INSIDE_MIN)


def clamp_triangles(verts: F32Grid, tris: _Tris, low: F64Grid, high: F64Grid) -> tuple[_Tris, int]:
    """``tris`` less every triangle with a vertex outside ``low``..``high``, and how many
    that dropped; ``tris`` itself when every vertex is inside."""
    keep = ((verts >= low) & (verts <= high)).all(axis=1)
    if keep.all():
        return tris, 0
    good = keep[tris].all(axis=1)
    return tris[good], int((~good).sum())


def _read_cooked(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, mesh: str
) -> _MeshRead | str:
    """One rock mesh at its finest, or the sentence the sidecar's failures record."""
    package = index.path_for(mesh)
    if not package:
        return "not in the container"
    try:
        view = PackageView(store.read_path(package), scripts)
    except Exception as exc:
        return f"unreadable package: {type(exc).__name__}"
    export = staticmesh.static_mesh_export(view)
    if export is None:
        return "no StaticMesh export"
    bounds = staticmesh.extended_bounds(view, export)
    if bounds is None:
        return "no ExtendedBounds, so a decode could not be checked"
    low, high = bounds
    hull, why = staticmesh.collision_hull(view, low, high)
    if hull is None:
        return why or "no collision hull"
    hull_verts, hull_tris, pad = hull
    chosen = finer_source(store, package, view, export, low, high)
    source, (verts, tris) = ("hull", (hull_verts, hull_tris)) if chosen is None else chosen
    cooked = CookedMesh(
        np.ascontiguousarray(verts, dtype=np.float32),
        np.ascontiguousarray(tris, dtype=np.int64),
        low - pad,
        high + pad,
    )
    # The closed-manifold Euler relation on the hull. Not a gate -- cave walls, floors and
    # merged arch pieces are open shells -- but noise essentially never satisfies it.
    closed = hull_tris.shape[0] == 2 * hull_verts.shape[0] - 4
    nanite_closed = nanite.boundary_edges(tris) == 0 if source == "nanite" else None
    return _MeshRead(cooked, source, int(hull_tris.shape[0]), closed, nanite_closed)


def read_mesh_geometry(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    meshes: list[str],
    progress: bool = True,
) -> MeshGeometry:
    """The finest geometry every placed rock mesh ships, over the hull-equivalent set.

    Only ``ROCK_DIRS`` are opened: a tree's collision is a tree, and the point of this layer
    is the geometry the landscape does not contain. **The cooked collision hull decides the
    SET**, and a mesh with no hull is skipped: those are cave pillars, holes and merged
    floors, roofs to a max-Z sampler (``sidecar_blocks.cliff_source`` has the cost).
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    geometry: dict[str, CookedMesh] = {}
    sources: dict[str, str] = {}
    failures: dict[str, str] = {}
    hull_tris = closed = manifolds = checked_manifold = 0
    started = time.time()
    for count, mesh in enumerate(wanted):
        read = _read_cooked(store, scripts, index, mesh)
        if isinstance(read, str):
            failures[mesh] = read
            continue
        hull_tris += read.hull_triangles
        closed += read.hull_closed
        if read.nanite_closed is not None:
            checked_manifold += 1
            manifolds += read.nanite_closed
        sources[mesh] = read.source
        geometry[mesh] = read.cooked
        if progress and count % 25 == 0:
            print(f"  {count}/{len(wanted)} rock meshes, {time.time() - started:.0f}s", flush=True)
    return {
        "geometry": geometry,
        "sources": sources,
        "by_source": {s: sum(1 for v in sources.values() if v == s) for s in CLIFF_SOURCES},
        "failures": failures,
        "wanted": len(wanted),
        "closed_manifolds": closed,
        "nanite_closed": manifolds,
        "nanite_checked": checked_manifold,
        "hull_triangles": hull_tris,
        "verts": sum(cooked.verts.shape[0] for cooked in geometry.values()),
        "tris": sum(cooked.tris.shape[0] for cooked in geometry.values()),
        "seconds": time.time() - started,
    }


def winding_sign(verts: F32Grid | F64Grid, tris: I32Grid | I64Grid) -> float:
    """+1 if this mesh's triangle normals point outward, -1 if inward, 0 if it cannot tell.

    A max-Z field wants only the up-facing half of a closed rock, and which half that is
    depends on the winding the cooker emitted, so it is measured per mesh from the
    divergence of the face normals about the centroid. 0 is an open shell, where the
    question is meaningless and every triangle is therefore kept.
    """
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(b - a, c - a)
    centre = verts.mean(0)
    divergence = float((normals * ((a + b + c) / 3 - centre)).sum())
    scale = float(np.abs(normals).sum() * np.abs(verts - centre).max()) + 1e-9
    ratio = divergence / scale
    return 1.0 if ratio > 0.02 else (-1.0 if ratio < -0.02 else 0.0)


def read_hull(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, mesh: str
) -> tuple[F32Grid, I64Grid] | None:
    """A mesh's cooked collision trimesh, mesh-local cm, or ``None`` where it ships none."""
    view = open_package(store, scripts, index, mesh)
    if view is None:
        return None
    export = staticmesh.static_mesh_export(view)
    bounds = staticmesh.extended_bounds(view, export) if export is not None else None
    if bounds is None:
        return None
    hull, _why = staticmesh.collision_hull(view, *bounds)
    if hull is None:
        return None
    return hull[0].astype(np.float32), hull[1].astype(np.int64)


def bounds_pair(found: dict[str, bytes]) -> MeshBox | None:
    """``(Origin, BoxExtent)`` out of an already-parsed tag set, as two double triples."""
    origin, extent = found.get("Origin", b""), found.get("BoxExtent", b"")
    if len(origin) != 24 or len(extent) != 24:
        return None
    ox, oy, oz = struct.unpack("<3d", origin)
    ex, ey, ez = struct.unpack("<3d", extent)
    return (ox, oy, oz), (ex, ey, ez)


def _extended_bounds(view: PackageView) -> MeshBox | None:
    """A ``StaticMesh``'s own ``ExtendedBounds``: the mesh's local box about its origin."""
    for export in view.exports:
        payload = view.props(export["slot"]).get("ExtendedBounds")
        if not payload:
            continue
        entries, _end = property_tags(payload, view.pkg.names, 0)
        pair = bounds_pair({tag.name: tag.payload for tag in entries if tag.name})
        if pair is not None:
            return pair
    return None


class MeshBounds:
    """``ExtendedBounds`` per static mesh, read once each.

    A cache because the plane-backed blueprints all name the same mesh: 215 of them asking
    the container would be 215 package reads for one answer.
    """

    def __init__(self, store: IoStore, scripts: ScriptObjects, index: AssetIndex) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self._cache: dict[str, MeshBox | None] = {}

    def extended_bounds(self, mesh_path: str) -> MeshBox | None:
        if mesh_path not in self._cache:
            view = open_package(self.store, self.scripts, self.index, mesh_path)
            self._cache[mesh_path] = _extended_bounds(view) if view is not None else None
        return self._cache[mesh_path]

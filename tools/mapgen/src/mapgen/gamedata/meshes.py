"""The cooked meshes: geometry reads, collision hulls and ``ExtendedBounds``."""

from __future__ import annotations

import struct
import time

import numpy as np

from satisfactory_mcp.core.gameassets import nanite, staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView, property_tags

__all__ = [
    "BOUNDS_INSIDE_MIN",
    "BOUNDS_PAD_CM",
    "BOUNDS_PAD_FRACTION",
    "CLIFF_SOURCES",
    "DIRECT_SAMPLES_MIN",
    "ROCK_DIRS",
    "MeshBounds",
    "bounds_pair",
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
#: interpolation across a triangle wider than itself. ``tools/gen_map_renders.py`` imports
#: this rule and evaluates it at its own texel size, so it is stated here only once.
DIRECT_SAMPLES_MIN = 1


def finer_source(store, package: str, view, export, low, high) -> tuple[str, tuple] | None:
    """The finest geometry this mesh ships, and which one that was -- or ``None``.

    Nanite first, LOD 0 second: at the placement transform their median world edges are
    0.48 m and 1.33 m against the collision hull's 2.43 m. 25 of this build's rock meshes
    carry no Nanite resource at all -- sea rocks, corals, part of the cave interior set --
    so a Nanite-only layer loses about 365,000 texels and still looks like a field.

    A finer source is accepted only if it clears the same bounds check the hull does: the
    mesh's own serialised ``ExtendedBounds`` is the one statement available that does not
    come from this reader, so it is what stops plausible garbage from shipping.
    """
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
        if not problems and len(decoded["triangles"]):
            candidate = (decoded["positions"], decoded["triangles"])
            if _inside_bounds(candidate[0], low, high):
                return "nanite", candidate

    got = staticmesh.lod0_buffers(tail, parsed)
    if got is not None and len(got[1]) and _inside_bounds(got[0], low, high):
        return "lod0", (got[0], got[1])
    return None


def _inside_bounds(verts: np.ndarray, low, high) -> bool:
    if not np.isfinite(verts).all():
        return False
    pad = BOUNDS_PAD_CM + BOUNDS_PAD_FRACTION * float(np.max(np.asarray(high) - np.asarray(low)))
    inside = ((verts >= low - pad) & (verts <= high + pad)).all(axis=1)
    return bool(inside.mean() >= BOUNDS_INSIDE_MIN)


def read_mesh_geometry(store, scripts, index, meshes: list[str], progress: bool = True) -> dict:
    """The finest geometry every placed rock mesh ships, over the hull-equivalent set.

    Only ``ROCK_DIRS`` are opened: a tree's collision is a tree, and the point of this layer
    is the geometry the landscape does not contain.

    **The cooked collision hull decides the SET**, and a mesh with no hull is skipped -- 21
    of the 130 use ``CTF_UseSimpleAndComplex`` and ship only convex hulls. Extending the
    layer to the 120 hull-less meshes costs 1.66 points of ``frac_lt_0.25m`` and 10.9 m of
    p90 under a max-Z sampler, because they are cave pillars, cave holes and merged cave
    floors: roofs.
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    geometry: dict[str, tuple] = {}
    sources: dict[str, str] = {}
    failures: dict[str, str] = {}
    hull_tris = 0
    closed = 0
    manifolds = 0
    checked_manifold = 0
    started = time.time()
    for count, mesh in enumerate(wanted):
        package = index.path_for(mesh)
        if not package:
            failures[mesh] = "not in the container"
            continue
        try:
            view = PackageView(store.read_path(package), scripts)
        except Exception as exc:
            failures[mesh] = f"unreadable package: {type(exc).__name__}"
            continue
        export = staticmesh.static_mesh_export(view)
        if export is None:
            failures[mesh] = "no StaticMesh export"
            continue
        bounds = staticmesh.extended_bounds(view, export)
        if bounds is None:
            failures[mesh] = "no ExtendedBounds, so a decode could not be checked"
            continue
        low, high = bounds
        hull, why = staticmesh.collision_hull(view, low, high)
        if hull is None:
            failures[mesh] = why
            continue
        hull_verts, hull_tris_array, pad = hull
        hull_tris += hull_tris_array.shape[0]
        # The closed-manifold Euler relation on the hull. Not a gate -- cave walls, floors
        # and merged arch pieces are open shells and are meant to be -- but noise satisfies
        # it essentially never, so the count is evidence that this is geometry.
        if hull_tris_array.shape[0] == 2 * hull_verts.shape[0] - 4:
            closed += 1

        chosen = finer_source(store, package, view, export, low, high)
        if chosen is None:
            source, (verts, tris) = "hull", (hull_verts, hull_tris_array)
        else:
            source, (verts, tris) = chosen
        if source == "nanite":
            checked_manifold += 1
            manifolds += nanite.boundary_edges(tris) == 0
        sources[mesh] = source
        geometry[mesh] = (
            np.ascontiguousarray(verts, dtype=np.float32),
            np.ascontiguousarray(tris, dtype=np.int64),
            low - pad,
            high + pad,
        )
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
        "verts": sum(v.shape[0] for v, _t, _lo, _hi in geometry.values()),
        "tris": sum(t.shape[0] for _v, t, _lo, _hi in geometry.values()),
        "seconds": time.time() - started,
    }


def winding_sign(verts: np.ndarray, tris: np.ndarray) -> float:
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


def read_hull(store, scripts, index, mesh: str) -> tuple[np.ndarray, np.ndarray] | None:
    """A mesh's cooked collision trimesh, mesh-local cm, or ``None`` where it ships none."""
    package = index.path_for(mesh)
    if not package:
        return None
    try:
        view = PackageView(store.read_path(package), scripts)
    except Exception:
        return None
    export = staticmesh.static_mesh_export(view)
    bounds = staticmesh.extended_bounds(view, export) if export is not None else None
    if bounds is None:
        return None
    hull, _why = staticmesh.collision_hull(view, *bounds)
    if hull is None:
        return None
    return hull[0].astype(np.float32), hull[1].astype(np.int64)


def bounds_pair(found: dict[str, bytes]) -> tuple[tuple, tuple] | None:
    """``(Origin, BoxExtent)`` out of an already-parsed tag set, as two double triples."""
    origin, extent = found.get("Origin", b""), found.get("BoxExtent", b"")
    if len(origin) != 24 or len(extent) != 24:
        return None
    return struct.unpack("<3d", origin), struct.unpack("<3d", extent)


def _extended_bounds(view: PackageView) -> tuple[tuple, tuple] | None:
    """A ``StaticMesh``'s own ``ExtendedBounds``: the mesh's local box about its origin."""
    for export in view.exports:
        payload = view.props(export["slot"]).get("ExtendedBounds")
        if not payload:
            continue
        entries, _end = property_tags(payload, view.pkg.names, 0)
        pair = bounds_pair({name: raw for name, _kind, raw, _value in entries})
        if pair is not None:
            return pair
    return None


class MeshBounds:
    """``ExtendedBounds`` per static mesh, read once each.

    A cache because the plane-backed blueprints all name the same mesh: 215 of them asking
    the container would be 215 package reads for one answer.
    """

    def __init__(self, store, scripts, index) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self._cache: dict[str, tuple | None] = {}

    def extended_bounds(self, mesh_path: str) -> tuple[tuple, tuple] | None:
        if mesh_path not in self._cache:
            bounds = None
            package = self.index.path_for(mesh_path)
            if package:
                try:
                    bounds = _extended_bounds(
                        PackageView(self.store.read_path(package), self.scripts)
                    )
                except Exception:
                    bounds = None
            self._cache[mesh_path] = bounds
        return self._cache[mesh_path]

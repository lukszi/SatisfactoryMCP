"""The cooked meshes: decode, the max-Z raster, cliffs, tops, and the water-actor boxes."""

from __future__ import annotations

import math
import struct
import time

import numpy as np

from satisfactory_mcp.core.gameassets import nanite, staticmesh
from satisfactory_mcp.core.gameassets.packages import (
    PackageView,
    class_name_of,
    property_tags,
    quat_rotate,
    world_transform,
)

__all__ = [
    "ARCH_MARK",
    "BOUNDS_INSIDE_MIN",
    "BOUNDS_PAD_CM",
    "BOUNDS_PAD_FRACTION",
    "CLIFF_SOURCES",
    "DIRECT_SAMPLES_MIN",
    "EXCLUDED_MESHES",
    "EXCLUDED_OWNERS",
    "OVERSIZE_CM",
    "RASTER_FLUSH",
    "ROCK_DIRS",
    "WATER_BOX_COMPONENTS",
    "WATER_CLASS_PREFIXES",
    "WATER_CLASS_TOKENS",
    "WATER_PLANE_MESH",
    "WATER_SURFACE_CLASSES",
    "MaxZRaster",
    "MeshBounds",
    "finer_source",
    "is_water_class",
    "rasterise_cliffs",
    "rasterise_top",
    "read_hull",
    "read_mesh_geometry",
    "rotation_matrix",
    "water_actor_box",
    "winding_sign",
]


#: Which mesh trees carry terrain geometry. Everything else placed in the world is a tree,
#: a plant, a building or a prop, and none of those are ground.
ROCK_DIRS = ("/World/Environment/Rock/", "/World/Environment/Caves/")

#: Actors whose meshes must never enter the field. The resource-node mesh is the whole list
#: and the reason is circularity: the field is validated against the node table.
EXCLUDED_OWNERS = frozenset({"NodeMeshActor_C"})

#: A mesh basename containing this is an arch, and an arch is a roof: a max-Z fold would put
#: it over the ground beneath it, so it is dropped before the fold rather than masked after.
#: Masking after blanks a texel an arch won even where a real rock stood second in it.
ARCH_MARK = "Arc"

#: Scaled local extent past which a placement is scenery rather than terrain: the sky dome
#: and the ocean shells. 600 m is an order of magnitude above the largest real rock.
OVERSIZE_CM = 60000.0

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

#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000

#: A water actor is one whose class name carries a water word and one of the game's own
#: class prefixes. Deliberately a shape rather than a list: 849 actors on build 495413
#: across nine classes, and a build that adds a tenth should be found, not missed.
WATER_CLASS_TOKENS = ("Water", "Ocean", "Lake", "River")
WATER_CLASS_PREFIXES = ("BP_", "FG", "BPW")

#: Which of those classes are a water SURFACE, i.e. whose box top may set a level. The
#: exclusions are the argument. ``BP_WaterFallTool_02_C`` is water and is not a surface --
#: its box top is the lip of the fall, tens of metres above the pool it feeds -- and the
#: two ``BP_WaterPlane_C`` are developer backdrops carrying no transform at all.
WATER_SURFACE_CLASSES = frozenset(
    {
        "FGWaterVolume",
        "BP_Water_C",
        "BP_LakeWater_C",
        "BPW_OceanSplineTool_02_C",
        "BP_TranslucentWater_C",
        "BP_River_PROT_C",
    }
)

#: Component classes that can state a box. Order of preference is in ``_component_box``.
WATER_BOX_COMPONENTS = frozenset(
    {
        "BoxComponent",
        "BrushComponent",
        "StaticMeshComponent",
        "InstancedStaticMeshComponent",
        "HierarchicalInstancedStaticMeshComponent",
    }
)

#: The plane the water blueprints draw themselves with. Their cooked instances name no
#: ``StaticMesh`` -- the construction script assigns it -- so this asset's own
#: ``ExtendedBounds`` stands in: of 215 such planes, the 187 whose centre falls inside an
#: ``FGWaterVolume`` sit on that volume's top to a median of 1.3 cm. Read from the container
#: rather than hard-coded, so a resized plane moves it.
WATER_PLANE_MESH = "/Game/FactoryGame/World/Environment/Water/Mesh/WaterPlane"


#: Rock meshes kept out of every layer by name. CliffPillar_03 is passable in game.
EXCLUDED_MESHES = frozenset({"CliffPillar_03"})


def rotation_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """UE's ``FRotationMatrix``: rows are the local X, Y, Z axes in world space.

    Written out rather than composed from three rotations, because UE's order and sign
    conventions are its own.
    """
    p, y, r = np.radians([pitch, yaw, roll])
    sp, cp = np.sin(p), np.cos(p)
    sy, cy = np.sin(y), np.cos(y)
    sr, cr = np.sin(r), np.cos(r)
    return np.array(
        [
            [cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
        ]
    )


# --------------------------------------------------------------------------------------
# Stage 3: the cooked Chaos triangle meshes, and the max-Z overlay they rasterise into.
# --------------------------------------------------------------------------------------


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


class MaxZRaster:
    """Scatter-max rasteriser over the landscape frame: the highest triangle wins a texel.

    Triangles arrive faster than they can be reduced -- 120 M of them across the placements
    -- so candidates are buffered and folded in batches by a lexsort on (texel, z) and a
    take-last.

    ``sample`` is where in a texel, in texels, its value is taken: 0 at the vertex
    ``x0 + col * scale``, which is where ``heightfield`` reads every plane; 0.5 at the
    texel centre, which is a render's pixel.
    """

    def __init__(
        self,
        width: int,
        height: int,
        x0_cm: float,
        y0_cm: float,
        scale: float,
        *,
        sample: float = 0.0,
    ) -> None:
        self.width, self.height = width, height
        self.x0, self.y0, self.scale = x0_cm, y0_cm, scale
        self.sample = sample
        self.z = np.full(height * width, -np.inf, dtype=np.float32)
        self.src = np.zeros(height * width, dtype=np.uint16)
        self.density = np.zeros(height * width, dtype=np.uint32)
        self._idx: list[np.ndarray] = []
        self._z: list[np.ndarray] = []
        self._s: list[np.ndarray] = []
        self._n = 0
        self._samples: list[np.ndarray] = []
        self._sample_n = 0

    def count_samples(self, points: np.ndarray) -> None:
        """Record which texel each SOURCE VERTEX landed in. The density plane, accumulated.

        Not the fold's question: the fold answers every texel a triangle covers, however
        large the triangle, while this counts only the texels the geometry sampled. A texel
        with no samples still has a height, and that height is a plane interpolation.

        A vertex counts for the texel whose sample point is nearest it, the convention
        ``add`` writes heights under; two would put density half a texel off its heights.
        """
        shift = 0.5 - self.sample
        col = np.floor((points[:, 0] - self.x0) / self.scale + shift).astype(np.int64)
        row = np.floor((points[:, 1] - self.y0) / self.scale + shift).astype(np.int64)
        ok = (col >= 0) & (col < self.width) & (row >= 0) & (row < self.height)
        if not ok.any():
            return
        self._samples.append(row[ok] * self.width + col[ok])
        self._sample_n += int(ok.sum())
        if self._sample_n > RASTER_FLUSH:
            self.flush_samples()

    def flush_samples(self) -> None:
        """Reduce the buffered sample texels into the density plane.

        Sorted and run-length counted rather than ``bincount``-ed: a bincount over the frame
        allocates a 43-million-element temporary on every one of dozens of flushes.
        """
        if not self._samples:
            return
        idx = np.concatenate(self._samples)
        self._samples, self._sample_n = [], 0
        unique, counts = np.unique(idx, return_counts=True)
        self.density[unique] += counts.astype(np.uint32)

    def flush(self) -> None:
        if not self._idx:
            return
        idx = np.concatenate(self._idx)
        z = np.concatenate(self._z)
        src = np.concatenate(self._s)
        self._idx, self._z, self._s, self._n = [], [], [], 0
        order = np.lexsort((z, idx))
        idx, z, src = idx[order], z[order], src[order]
        last = np.empty(idx.size, bool)
        last[-1] = True
        last[:-1] = idx[1:] != idx[:-1]
        idx, z, src = idx[last], z[last], src[last]
        better = z > self.z[idx]
        self.z[idx[better]] = z[better]
        self.src[idx[better]] = src[better]

    def add(self, tri: np.ndarray, source_id: int) -> None:
        """Buffer every texel covered by ``tri`` (M, 3, 3) in world cm, with its plane Z.

        Bucketed by bounding-box span so one vectorised barycentric test runs over a whole
        bucket at a fixed candidate-grid size, instead of every triangle paying for the
        largest one's box.
        """
        fx = (tri[:, :, 0] - self.x0) / self.scale
        fy = (tri[:, :, 1] - self.y0) / self.scale
        z = tri[:, :, 2]
        x0 = np.floor(fx.min(1) - 0.5)
        x1 = np.ceil(fx.max(1) + 0.5)
        y0 = np.floor(fy.min(1) - 0.5)
        y1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(x1 - x0, y1 - y0).astype(np.int32)
        for size in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if not pick.any():
                continue
            steps = np.arange(size + 1, dtype=np.float32)
            ox, oy = np.meshgrid(steps, steps)
            gx = x0[pick][:, None] + ox.ravel()[None, :] + self.sample
            gy = y0[pick][:, None] + oy.ravel()[None, :] + self.sample
            ax, ay = fx[pick, 0][:, None], fy[pick, 0][:, None]
            bx, by = fx[pick, 1][:, None], fy[pick, 1][:, None]
            cx, cy = fx[pick, 2][:, None], fy[pick, 2][:, None]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            l3 = 1.0 - l1 - l2
            col = np.floor(gx).astype(np.int32)
            row = np.floor(gy).astype(np.int32)
            ok = (
                (l1 >= -1e-6)
                & (l2 >= -1e-6)
                & (l3 >= -1e-6)
                & (col >= 0)
                & (col < self.width)
                & (row >= 0)
                & (row < self.height)
            )
            if not ok.any():
                continue
            plane = l1 * z[pick, 0][:, None] + l2 * z[pick, 1][:, None] + l3 * z[pick, 2][:, None]
            self._idx.append(row[ok].astype(np.int64) * self.width + col[ok])
            self._z.append(plane[ok].astype(np.float32))
            self._s.append(np.full(int(ok.sum()), source_id, np.uint16))
            self._n += int(ok.sum())
        if self._n > RASTER_FLUSH:
            self.flush()

    def result(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.flush()
        self.flush_samples()
        z = self.z.reshape(self.height, self.width)
        return (
            np.where(np.isfinite(z), z, np.nan).astype(np.float32),
            self.src.reshape(self.height, self.width),
            self.density.reshape(self.height, self.width),
        )


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


def rasterise_cliffs(sweep: dict, geometry: dict, frame: dict, progress: bool = True) -> dict:
    """Transform, cull and rasterise every placed rock into a 1 m max-Z overlay, in cm.

    Culling, in the order it costs least: an excluded owner, a mesh with no cooked geometry,
    an arch, an oversized shell, then the downward-facing half of the triangles, then the
    triangles outside the mesh's own padded bounds. That last cull is per triangle rather
    than per mesh, because the defect it removes is one stray vertex in an otherwise good
    mesh.
    """
    placements = sweep["placements"]
    meshes, owners = sweep["meshes"], sweep["owners"]
    windings = {m: winding_sign(v, t) for m, (v, t, _lo, _hi) in geometry.items()}
    raster = MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    dropped = {"owner": 0, "excluded_mesh": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    used = 0
    triangles = 0
    samples = 0
    clamped = 0
    started = time.time()
    for count, row in enumerate(placements):
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh.rsplit("/", 1)[-1] in EXCLUDED_MESHES:
            dropped["excluded_mesh"] += 1
            continue
        if mesh not in geometry:
            dropped["no_geometry"] += 1
            continue
        if mesh_id in arch_ids:
            dropped["arch"] += 1
            continue
        verts, tris, low, high = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        # The bounds clamp is applied in LOCAL space, where the mesh's own ExtendedBounds
        # live, so it costs one comparison per vertex instead of a transformed box per
        # placement -- and it is the same box for all 200 copies of a rock.
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
            if tris.size == 0:
                continue
        world = (verts * scale) @ matrix + row[2:5].astype(np.float32)
        facing = windings[mesh] * np.sign(scale[0] * scale[1] * scale[2])
        if facing != 0:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * facing) > 0]
        if tris.shape[0]:
            raster.add(world[tris], mesh_id + 1)
            triangles += tris.shape[0]
            # The density plane counts the vertices of the triangles that SURVIVED the
            # facing cull, not every vertex of the mesh: a downward-facing vertex is not a
            # sample of the surface this field describes, and counting it would call a
            # texel measured because the underside of a rock passed over it.
            surviving = np.unique(tris)
            raster.count_samples(world[surviving])
            samples += surviving.size
        used += 1
        if progress and count % 4000 == 0:
            print(
                f"  {count}/{len(placements)} placements, {used} rasterised, "
                f"{triangles / 1e6:.1f} M triangles, {time.time() - started:.0f}s",
                flush=True,
            )
    z_cm, _src, density = raster.result()
    return {
        "z_cm": z_cm,
        "density": density,
        "placements_total": len(placements),
        "placements_used": used,
        "dropped": dropped,
        "arch_meshes": len(arch_ids & set(np.unique(placements[:, 0]).astype(int))),
        "triangles": int(triangles),
        "samples": int(samples),
        "triangles_out_of_bounds": clamped,
        "seconds": time.time() - started,
    }


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


def rasterise_top(sweep: dict, frame: dict, store, scripts, index, progress: bool = True) -> dict:
    """Arches and foliage boulders as a 1 m max-Z overlay, from their collision trimeshes.

    These are what the ground deliberately leaves out: an arch is a roof over buildable
    ground, and a boulder is painted foliage the placement sweep never sees.
    """
    started = time.time()
    raster = MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )
    placements, meshes, owners = sweep["placements"], sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    hulls: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    arches = boulders = triangles = 0
    missing: set[str] = set()
    for row in placements:
        mesh_id = int(row[0])
        if mesh_id not in arch_ids or owners[int(row[1])] in EXCLUDED_OWNERS:
            continue
        mesh = meshes[mesh_id]
        if mesh not in hulls:
            hulls[mesh] = read_hull(store, scripts, index, mesh)
        if hulls[mesh] is None:
            missing.add(mesh)
            continue
        verts, tris = hulls[mesh]
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        world = (verts * row[8:11].astype(np.float32)) @ matrix + row[2:5].astype(np.float32)
        raster.add(world[tris], 1)
        arches += 1
        triangles += tris.shape[0]
    for mesh, mats in sweep["foliage"].items():
        if mesh not in hulls:
            hulls[mesh] = read_hull(store, scripts, index, mesh)
        if hulls[mesh] is None:
            missing.add(mesh)
            continue
        verts, tris = hulls[mesh]
        for start in range(0, len(mats), 512):
            chunk = mats[start : start + 512].astype(np.float32)
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 2)
        boulders += len(mats)
        triangles += tris.shape[0] * len(mats)
    z_cm, _src, _density = raster.result()
    if progress:
        print(f"  top overlay: {arches} arches, {boulders} boulders, {time.time() - started:.0f}s")
    return {
        "z_cm": z_cm,
        "arch_placements": arches,
        "foliage_instances": boulders,
        "foliage_by_mesh": {m.rsplit("/", 1)[-1]: len(v) for m, v in sweep["foliage"].items()},
        "meshes_without_trimesh": sorted(m.rsplit("/", 1)[-1] for m in missing),
        "triangles": int(triangles),
        "seconds": time.time() - started,
    }


# --------------------------------------------------------------------------------------
# Stage 5, part one: the world-space bounding box of every water actor.
# --------------------------------------------------------------------------------------


def is_water_class(name: str) -> bool:
    """Whether a class name is one of the world's water actors. A shape, not a list."""
    return name.startswith(WATER_CLASS_PREFIXES) and any(t in name for t in WATER_CLASS_TOKENS)


def _bounds_pair(found: dict[str, bytes]) -> tuple[tuple, tuple] | None:
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
        pair = _bounds_pair({name: raw for name, _kind, raw, _value in entries})
        if pair is not None:
            return pair
    return None


def _box_sphere_bounds(payload: bytes, names) -> tuple[tuple, tuple] | None:
    """An ``FBoxSphereBounds``, unwrapping the ``CachedBounds`` container it arrives in."""
    entries, _end = property_tags(payload, names, 0)
    found = {name: raw for name, _kind, raw, _value in entries}
    if "Value" in found:
        return _box_sphere_bounds(found["Value"], names)
    return _bounds_pair(found)


def _agg_geom_box(payload: bytes, names) -> tuple[list[float], list[float]] | None:
    """The union of every convex element's ``ElemBox`` in a cooked ``FKAggregateGeom``.

    This is where an ``FGWaterVolume`` keeps its shape. A cooked BSP brush holds its
    vertices in WORLD space and its component transform is legitimately the identity, so 270
    of these decode with no ``RelativeLocation`` anywhere on the actor. An ``FBox`` is 3
    doubles of min, 3 of max and a validity byte.
    """
    entries, _end = property_tags(payload, names, 0)
    low = [math.inf] * 3
    high = [-math.inf] * 3
    found = 0
    for name, _kind, array, _value in entries:
        if name not in ("ConvexElems", "BoxElems") or len(array) < 4:
            continue
        count = struct.unpack_from("<I", array, 0)[0]
        position = 4
        for _ in range(count):
            elements, position = property_tags(array, names, position)
            for inner, _k, blob, _v in elements:
                if inner == "ElemBox" and len(blob) >= 48:
                    minimum = struct.unpack_from("<3d", blob, 0)
                    maximum = struct.unpack_from("<3d", blob, 24)
                    for axis in range(3):
                        low[axis] = min(low[axis], minimum[axis])
                        high[axis] = max(high[axis], maximum[axis])
                    found += 1
            if position >= len(array):
                break
    return (low, high) if found else None


def _corners_to_world(low, high, transform) -> tuple[list[float], list[float]]:
    """A local box through a world transform, eight corners at a time.

    Corner by corner rather than centre-plus-extent, because a rotated volume's world AABB
    is the box AROUND the rotated box, not the unrotated box moved. 486 of the 837 water
    actors are rotated.
    """
    location, rotation, scale = transform
    out_low = [math.inf] * 3
    out_high = [-math.inf] * 3
    for x in (low[0], high[0]):
        for y in (low[1], high[1]):
            for z in (low[2], high[2]):
                turned = quat_rotate(rotation, (x * scale[0], y * scale[1], z * scale[2]))
                for axis in range(3):
                    value = location[axis] + turned[axis]
                    out_low[axis] = min(out_low[axis], value)
                    out_high[axis] = max(out_high[axis], value)
    return out_low, out_high


class MeshBounds:
    """``ExtendedBounds`` per static mesh, read once each, plus the water plane's own.

    A cache because the plane-backed blueprints all name the same mesh: 215 of them asking
    the container would be 215 package reads for one answer.
    """

    def __init__(self, store, scripts, index) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self._cache: dict[str, tuple | None] = {}

    def of(self, mesh_path: str) -> tuple[tuple, tuple] | None:
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

    @property
    def plane(self) -> tuple[tuple, tuple] | None:
        return self.of(WATER_PLANE_MESH)


def _component_box(view: PackageView, slot: int, name: str, meshes: MeshBounds):
    """One component's LOCAL box and where it came from, or ``(None, None)``.

    Four sources, tried in the order they are trustworthy: the component's own
    ``BoxExtent``, a BSP volume's cooked ``BrushBodySetup.AggGeom``, an instanced
    component's ``CachedBounds``, and a ``StaticMeshComponent``'s mesh ``ExtendedBounds``.
    That last falls back to the water plane's when the cooked instance names no mesh, which
    is the normal case here and is flagged in the returned source name rather than hidden.
    """
    props = view.props(slot)
    if len(props.get("BoxExtent", b"")) == 24:
        extent = struct.unpack("<3d", props["BoxExtent"])
        return ([-e for e in extent], list(extent)), "BoxComponent.BoxExtent"
    if name == "BrushComponent":
        setup = view.export_ref(props.get("BrushBodySetup", b""))
        geometry = view.props(setup).get("AggGeom") if setup is not None else None
        box = _agg_geom_box(geometry, view.pkg.names) if geometry else None
        return (box, "BrushBodySetup.AggGeom") if box else (None, None)
    if name in ("InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent"):
        cached = props.get("CachedBounds")
        pair = _box_sphere_bounds(cached, view.pkg.names) if cached else None
        source = "InstancedStaticMeshComponent.CachedBounds"
    elif name == "StaticMeshComponent":
        mesh = view.import_path(props.get("StaticMesh", b"")) if "StaticMesh" in props else None
        pair = meshes.of(mesh) if mesh else None
        source = "StaticMesh.ExtendedBounds"
        if pair is None:
            pair = meshes.plane
            source = "WaterPlane.ExtendedBounds (assumed)"
    else:
        return None, None
    if pair is None:
        return None, None
    origin, extent = pair
    low = [origin[axis] - extent[axis] for axis in range(3)]
    high = [origin[axis] + extent[axis] for axis in range(3)]
    return (low, high), source


def water_actor_box(view: PackageView, actor: int, classes, meshes: MeshBounds):
    """One water actor's world AABB in centimetres, and the box sources it came from.

    The union over every box-like component in the actor's export subtree, each taken to
    world space through its own composed ``AttachParent`` chain.

    The last block is a refusal: a mesh's ``ExtendedBounds`` is centred on the mesh's own
    origin, so an actor whose only box is an assumed plane and which states no transform
    anywhere would land at the world origin -- a parse artefact, not a placement. It cannot
    catch a ``BrushComponent``, whose vertices are already world-space and whose identity
    transform is correct.
    """
    stack = [actor]
    seen: set[int] = set()
    low = [math.inf] * 3
    high = [-math.inf] * 3
    sources: set[str] = set()
    positioned = view.props(actor).get("RelativeLocation") is not None
    while stack:
        slot = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        stack.extend(view.children.get(slot, []))
        name = class_name_of(view.class_of.get(slot))
        if name not in WATER_BOX_COMPONENTS:
            continue
        local, source = _component_box(view, slot, name, meshes)
        if local is None:
            continue
        transform, _parent = world_transform(view, slot, classes)
        if transform is None:
            transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
        if view.props(slot).get("RelativeLocation") is not None or transform[0] != (0.0, 0.0, 0.0):
            positioned = True
        corner_low, corner_high = _corners_to_world(local[0], local[1], transform)
        for axis in range(3):
            low[axis] = min(low[axis], corner_low[axis])
            high[axis] = max(high[axis], corner_high[axis])
        sources.add(source)
    if not sources or not all(math.isfinite(v) for v in low + high):
        return None, sources
    if not positioned and all("assumed" in source for source in sources):
        return None, set()
    return tuple(low + high), sources

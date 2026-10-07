"""The collision pack (``rocks.npz``): the surface the player and the build gun stand on."""

from __future__ import annotations

import io
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypeAlias, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.level.sweep import Sweep, flagged_tags, quat_axes, world_levels
from mapgen.gamedata.meshes import ROCK_DIRS, clamp_triangles, winding_sign
from mapgen.gamedata.placements import (
    EXCLUDED_MESHES,
    EXCLUDED_OWNERS,
    OVERSIZE_CM,
    PLACEMENT_MESH,
    PLACEMENT_OWNER,
    is_arch,
    mesh_name,
    placement_transform,
    rotation_matrix,
)
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I8Grid, I32Grid, I64Grid, U8Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import (
    PackageView,
    ZenExport,
    class_name_of,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "CAVE_FLOOR_CLASS",
    "COMPLEX_AS_SIMPLE",
    "COMPONENT_TRIMESH_SEARCH",
    "COMPONENT_VERTEX_MAX_CM",
    "NO_COLLISION",
    "PRIMITIVE_SEGMENTS",
    "SPLINE_MESH_CLASS",
    "CaveFloors",
    "CollisionMesh",
    "CollisionMeshes",
    "PackCounts",
    "RockPackArrays",
    "collision_mesh",
    "component_trimesh",
    "encode_rock_pack",
    "is_pack_mesh",
    "read_collision_meshes",
    "rock_pack_arrays",
    "simple_collision",
    "sweep_cave_floors",
]


SPLINE_MESH_CLASS = "SplineMeshComponent"
COMPLEX_AS_SIMPLE = "CTF_UseComplexAsSimple"
NO_COLLISION = "NoCollision"

#: A cave floor is a spline actor whose components each carry their own cooked trimesh.
CAVE_FLOOR_CLASS = "BP_CaveFloor_C"

#: Rings and segments a sphere or capsule element is tessellated into. 12 keeps the facets
#: within 3.5% of the radius, and these are corals and kelp.
PRIMITIVE_SEGMENTS = 12

#: Where a component BodySetup's trimesh marker may sit, and the largest believable vertex.
COMPONENT_TRIMESH_SEARCH = 4000
COMPONENT_VERTEX_MAX_CM = 1e7


class CollisionMesh(TypedDict):
    """The collision one mesh gives the player: mesh-local cm, and where it came from.

    A ``trimesh`` mesh also says which render LOD the cooker traced and how many triangles
    that LOD and the cooked trimesh have.
    """

    verts: F32Grid
    tris: I64Grid
    winding: float
    source: Literal["trimesh", "simple"]
    lod_for_collision: NotRequired[int]
    render_lod_triangles: NotRequired[int | None]
    cooked_triangles: NotRequired[int]


class CollisionMeshes(TypedDict):
    meshes: dict[str, CollisionMesh]
    failures: dict[str, str]


class CaveFloors(TypedDict):
    """Every cave-floor spline component's trimesh in world cm, and the counts."""

    pieces: list[tuple[F64Grid, I64Grid]]
    actors: int
    undecoded: int
    seconds: float


class RockPackArrays(TypedDict):
    """``rocks.npz``: the meshes once each, and every placed instance of them."""

    mesh_verts: F32Grid
    mesh_vstart: I64Grid
    mesh_tris: I32Grid
    mesh_tstart: I64Grid
    mesh_winding: I8Grid
    inst_mesh: I32Grid
    inst_kind: U8Grid
    inst_matrix: F64Grid
    inst_origin: F64Grid
    inst_lo: F64Grid
    inst_hi: F64Grid


class PackCounts(TypedDict):
    """The counts ``rocks.json`` records about the pack."""

    meshes: int
    meshes_by_source: dict[str, int]
    trimesh_equals_render_lod: int
    trimesh_render_lod_unread: int
    mesh_failures: dict[str, str]
    open_shells: int
    cave_floor_actors: int
    cave_floor_pieces: int
    cave_floor_undecoded: int
    instances_by_kind: dict[str, int]
    placed_triangles_by_kind: dict[str, int]
    local_triangles: int
    dropped: dict[str, int]


#: One placed copy: mesh index, kind, row-vector matrix (scale folded in), origin in cm.
_Instance: TypeAlias = tuple[int, int, F64Grid, F64Grid]


def is_pack_mesh(mesh: str) -> bool:
    """A rock, or an arch anywhere: the ``top`` plane's arch set has no directory test."""
    return any(d in mesh for d in ROCK_DIRS) or is_arch(mesh)


def _body_setup(view: PackageView) -> ZenExport | None:
    return next(
        (e for e in view.exports if class_name_of(view.class_of.get(e["slot"])) == "BodySetup"),
        None,
    )


def _enum_name(view: PackageView, payload: bytes | None) -> str:
    """An enum property's value name, ``""`` where there is none to read."""
    return (view.read_fname(payload) if payload else None) or ""


def _element_points(group: str, element: dict[str, bytes]) -> F64Grid | None:
    """The points whose convex hull is one ``AggGeom`` element, element-local cm."""
    if group == "ConvexElems":
        data = element.get("VertexData", b"")
        count = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        if not count or len(data) != 4 + 24 * count:
            return None
        return np.frombuffer(data, "<f8", 3 * count, 4).reshape(-1, 3).copy()

    def scalar(key: str) -> float:
        raw = element.get(key, b"")
        return struct.unpack("<f", raw)[0] if len(raw) == 4 else 0.0

    def triple(key: str) -> F64Grid:
        raw = element.get(key, b"")
        return np.array(struct.unpack("<3d", raw) if len(raw) == 24 else (0.0, 0.0, 0.0))

    centre = triple("Center")
    pitch, yaw, roll = (float(v) for v in triple("Rotation"))
    turn = rotation_matrix(pitch, yaw, roll).astype(np.float64)
    if group == "BoxElems":
        half = np.array([scalar("X"), scalar("Y"), scalar("Z")]) / 2.0
        signs = [[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        local = np.array(signs) * half
    elif group in ("SphereElems", "SphylElems"):
        radius = scalar("Radius")
        reach = scalar("Length") / 2.0 if group == "SphylElems" else 0.0
        lat = np.linspace(-np.pi / 2, np.pi / 2, PRIMITIVE_SEGMENTS + 1)
        lon = np.linspace(0, 2 * np.pi, PRIMITIVE_SEGMENTS, endpoint=False)
        la, lo = np.meshgrid(lat, lon)
        unit = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)
        shell = unit.reshape(-1, 3) * radius
        cap = np.array([0.0, 0.0, reach])
        local = np.vstack([shell[shell[:, 2] >= 0] + cap, shell[shell[:, 2] <= 0] - cap])
    else:
        return None
    return local @ turn + centre


def simple_collision(view: PackageView, agg: bytes) -> tuple[F32Grid, I64Grid] | None:
    """``AggGeom``'s convex, box, sphere and capsule elements as one outward-wound trimesh."""
    from scipy.spatial import ConvexHull, QhullError

    names = view.pkg.names
    groups, _ = flagged_tags(agg, names, pos=0)
    verts: list[F64Grid] = []
    tris: list[NDArray[np.integer]] = []
    base = 0
    for group, raw in groups.items():
        if len(raw) < 4:
            continue
        pos = 4
        for _ in range(struct.unpack_from("<I", raw, 0)[0]):
            element, pos = flagged_tags(raw, names, pos=pos)
            if NO_COLLISION in _enum_name(view, element.get("CollisionEnabled")):
                continue
            points = _element_points(group, element)
            if points is None or len(points) < 4:
                continue
            try:
                hull = ConvexHull(points)
            except (QhullError, ValueError):
                continue
            faces = hull.simplices.copy()
            a, b, c = (points[faces[:, k]] for k in range(3))
            inward = (np.cross(b - a, c - a) * hull.equations[:, :3]).sum(1) < 0
            faces[inward] = faces[inward][:, ::-1]
            verts.append(points)
            tris.append(faces + base)
            base += len(points)
    if not tris:
        return None
    return np.concatenate(verts).astype(np.float32), np.concatenate(tris).astype(np.int64)


def collision_mesh(view: PackageView, export: ZenExport) -> tuple[CollisionMesh | None, str]:
    """The collision a mesh's ``BodySetup`` gives the player, as ``(mesh, why)``.

    Complex-as-simple meshes collide with their cooked trimesh, which is render LOD
    ``LODForCollision`` triangle for triangle; every other mesh with its simple elements.
    """
    bounds = staticmesh.extended_bounds(view, export)
    body = _body_setup(view)
    if bounds is None or body is None:
        return None, "no ExtendedBounds or no BodySetup"
    tags, _ = flagged_tags(view.pkg.body(body), view.pkg.names)
    complex_as_simple = COMPLEX_AS_SIMPLE in _enum_name(view, tags.get("CollisionTraceFlag"))
    hull, why = staticmesh.collision_hull(view, *bounds)
    if hull is not None and complex_as_simple:
        verts, tris, pad = hull
        tris = clamp_triangles(verts, tris, bounds[0] - pad, bounds[1] + pad)[0]
        lod_raw = view.props(export["slot"]).get("LODForCollision", b"")
        lod = struct.unpack("<i", lod_raw)[0] if len(lod_raw) == 4 else 0
        try:
            lods = staticmesh.parse_render_data(staticmesh.render_tail(view, export))["lods"]
            render = lods[min(lod, len(lods) - 1)].triangles
        except staticmesh.ParseError:
            render = None
        return {
            "verts": verts,
            "tris": tris.astype(np.int64),
            "winding": winding_sign(verts, tris),
            "source": "trimesh",
            "lod_for_collision": lod,
            "render_lod_triangles": render,
            "cooked_triangles": len(hull[1]),
        }, ""
    simple = simple_collision(view, tags["AggGeom"]) if "AggGeom" in tags else None
    if simple is not None:
        return {"verts": simple[0], "tris": simple[1], "winding": 1.0, "source": "simple"}, ""
    return None, why or "no simple collision elements"


def read_collision_meshes(reader: GameReader, meshes: list[str]) -> CollisionMeshes:
    """Every placed rock and foliage boulder's collision, by mesh path."""
    found: dict[str, CollisionMesh] = {}
    failures: dict[str, str] = {}
    for mesh in meshes:
        package = reader.index.path_for(mesh)
        try:
            view = PackageView(reader.store.read_path(package), reader.scripts) if package else None
        except Exception as exc:
            failures[mesh] = f"unreadable package: {type(exc).__name__}"
            continue
        export = staticmesh.static_mesh_export(view) if view is not None else None
        if view is None or export is None:
            failures[mesh] = "no StaticMesh export"
            continue
        got, why = collision_mesh(view, export)
        if got is None:
            failures[mesh] = why
        else:
            found[mesh] = got
    return {"meshes": found, "failures": failures}


def component_trimesh(view: PackageView, body_slot: int) -> tuple[F64Grid, I64Grid] | None:
    """A level component's own cooked trimesh, component-local cm, or ``None``."""
    blob = staticmesh.render_tail(view, view.exports[body_slot])
    start = 0
    while True:
        at = blob.find(staticmesh.TRIMESH_MARKER, start)
        if at < 0 or at > COMPONENT_TRIMESH_SEARCH:
            return None
        start = at + 1
        count = struct.unpack_from("<I", blob, at + 5)[0] if at + 9 <= len(blob) else 0
        pos = at + 9
        if count <= 2 or pos + 12 * count + 8 > len(blob):
            continue
        verts = np.frombuffer(blob, "<f4", 3 * count, pos).reshape(count, 3)
        if not np.isfinite(verts).all() or np.abs(verts).max() > COMPONENT_VERTEX_MAX_CM:
            continue
        pos += 12 * count
        tris = struct.unpack_from("<I", blob, pos + 4)[0]
        pos += 8
        for width, dtype in ((2, "<u2"), (4, "<u4")):
            if tris and pos + 3 * tris * width <= len(blob):
                faces = np.frombuffer(blob, dtype, 3 * tris, pos).reshape(tris, 3)
                if faces.max() < count:
                    return verts.astype(np.float64), faces.astype(np.int64)


def sweep_cave_floors(reader: GameReader) -> CaveFloors:
    """Every ``BP_CaveFloor_C`` spline component's trimesh, in world cm."""
    pieces: list[tuple[F64Grid, I64Grid]] = []
    actors = undecoded = 0
    started = time.time()
    for _index, _total, _path, view in world_levels(reader.store, reader.scripts):
        for slot, class_path in view.class_of.items():
            if class_name_of(class_path) != CAVE_FLOOR_CLASS:
                continue
            actors += 1
            for child in view.children.get(slot, []):
                if class_name_of(view.class_of.get(child)) != SPLINE_MESH_CLASS:
                    continue
                reference = view.props(child).get("BodySetup")
                body = view.export_ref(reference) if reference else None
                transform = world_transform(view, child, reader.classes)[0]
                got = component_trimesh(view, body) if body is not None else None
                if got is None or transform is None:
                    undecoded += 1
                    continue
                location, quat, scale = transform
                world = (got[0] * np.array(scale)) @ quat_axes(quat) + np.array(location)
                pieces.append((world, got[1]))
    return {
        "pieces": pieces,
        "actors": actors,
        "undecoded": undecoded,
        "seconds": time.time() - started,
    }


@dataclass
class _PackMeshes:
    """The pack's meshes in order, each once, with the instances that place them."""

    verts: list[F32Grid] = field(default_factory=list)
    tris: list[I32Grid] = field(default_factory=list)
    winding: list[float] = field(default_factory=list)
    instances: list[_Instance] = field(default_factory=list)

    def add(self, verts: F32Grid, tris: NDArray[np.integer], winding: float) -> int:
        self.verts.append(verts.astype(np.float32))
        self.tris.append(tris.astype(np.int32))
        self.winding.append(winding)
        return len(self.verts) - 1

    def world_boxes(self) -> list[tuple[F64Grid, F64Grid]]:
        """Each instance's world box: its mesh's local box, every corner transformed."""
        boxes: list[tuple[F64Grid, F64Grid]] = []
        for mesh, _kind, matrix, origin in self.instances:
            lo = self.verts[mesh].min(0).astype(np.float64)
            hi = self.verts[mesh].max(0).astype(np.float64)
            corners = np.array(
                [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            )
            world = corners @ matrix + origin
            boxes.append((world.min(0), world.max(0)))
        return boxes


def _place_instances(
    sweep: Sweep, have: dict[str, CollisionMesh], mesh_ids: dict[str, int], pack: _PackMeshes
) -> dict[str, int]:
    """Add the placements and foliage boulders the pack keeps; what it dropped, by why.

    The cliff pass's culls, except that arches and simple-collision rocks are kept under
    their own kind instead of dropped.
    """
    from satisfactory_mcp.domain.spatial.heightfield import collision_pack as rocks

    meshes, owners = sweep["meshes"], sweep["owners"]
    dropped = {"owner": 0, "excluded_mesh": 0, "no_collision": 0, "oversize": 0}
    for row in sweep["placements"]:
        mesh = meshes[int(row[PLACEMENT_MESH])]
        if not is_pack_mesh(mesh):
            continue
        if owners[int(row[PLACEMENT_OWNER])] in EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh_name(mesh) in EXCLUDED_MESHES:
            dropped["excluded_mesh"] += 1
            continue
        if mesh not in have:
            dropped["no_collision"] += 1
            continue
        got = have[mesh]
        rotation, scale, origin = placement_transform(row, np.float64)
        if is_arch(mesh):
            kind = rocks.KIND_ARCH
        elif float(np.abs(got["verts"] * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        else:
            kind = rocks.KIND_ROCK if got["source"] == "trimesh" else rocks.KIND_ROCK_SIMPLE
        pack.instances.append((mesh_ids[mesh], kind, np.diag(scale) @ rotation, origin))
    for mesh, mats in sweep["foliage"].items():
        if mesh not in have:
            dropped["no_collision"] += len(mats)
            continue
        for m in mats:
            pack.instances.append(
                (mesh_ids[mesh], rocks.KIND_BOULDER, m[:3, :3].copy(), m[3, :3].copy())
            )
    return dropped


def rock_pack_arrays(
    sweep: Sweep, collision: CollisionMeshes, floors: CaveFloors
) -> tuple[RockPackArrays, PackCounts]:
    """The arrays of ``rocks.npz`` and the counts its sidecar records.

    The cliff pass's placement set and culls, with arches, foliage boulders, simple-collision
    rocks and cave floors kept under their own kind instead of dropped.
    """
    from satisfactory_mcp.domain.spatial.heightfield import collision_pack as rocks

    have = collision["meshes"]
    pack = _PackMeshes()
    mesh_ids = {
        m: pack.add(have[m]["verts"], have[m]["tris"], have[m]["winding"]) for m in sorted(have)
    }
    dropped = _place_instances(sweep, have, mesh_ids, pack)
    for piece_verts, piece_tris in floors["pieces"]:
        floor = pack.add(
            piece_verts.astype(np.float32),
            piece_tris,
            winding_sign(piece_verts.astype(np.float32), piece_tris),
        )
        pack.instances.append((floor, rocks.KIND_CAVE_FLOOR, np.eye(3), np.zeros(3)))
    boxes = pack.world_boxes()
    inst = pack.instances
    arrays: RockPackArrays = {
        "mesh_verts": np.concatenate(pack.verts),
        "mesh_vstart": np.cumsum([0] + [len(v) for v in pack.verts]).astype(np.int64),
        "mesh_tris": np.concatenate(pack.tris),
        "mesh_tstart": np.cumsum([0] + [len(t) for t in pack.tris]).astype(np.int64),
        "mesh_winding": np.array(pack.winding, np.int8),
        "inst_mesh": np.array([i[0] for i in inst], np.int32),
        "inst_kind": np.array([i[1] for i in inst], np.uint8),
        "inst_matrix": np.array([i[2] for i in inst], np.float64).reshape(-1, 3, 3),
        "inst_origin": np.array([i[3] for i in inst], np.float64).reshape(-1, 3),
        "inst_lo": np.array([b[0] for b in boxes], np.float64).reshape(-1, 3),
        "inst_hi": np.array([b[1] for b in boxes], np.float64).reshape(-1, 3),
    }
    return arrays, _pack_counts(arrays, collision, floors, dropped)


def _pack_counts(
    arrays: RockPackArrays,
    collision: CollisionMeshes,
    floors: CaveFloors,
    dropped: dict[str, int],
) -> PackCounts:
    from satisfactory_mcp.domain.spatial.heightfield import collision_pack as rocks

    have = collision["meshes"]
    kinds = np.bincount(arrays["inst_kind"], minlength=len(rocks.KIND_NAMES))
    placed = np.diff(arrays["mesh_tstart"])[arrays["inst_mesh"]]
    trimesh = [g for g in have.values() if g["source"] == "trimesh"]
    return {
        "meshes": len(have),
        "meshes_by_source": {
            s: sum(1 for g in have.values() if g["source"] == s) for s in ("trimesh", "simple")
        },
        "trimesh_equals_render_lod": sum(
            1 for g in trimesh if g.get("render_lod_triangles") == g.get("cooked_triangles")
        ),
        "trimesh_render_lod_unread": sum(
            1 for g in trimesh if g.get("render_lod_triangles") is None
        ),
        "mesh_failures": {mesh_name(m): why for m, why in collision["failures"].items()},
        "open_shells": int((arrays["mesh_winding"] == 0).sum()),
        "cave_floor_actors": floors["actors"],
        "cave_floor_pieces": len(floors["pieces"]),
        "cave_floor_undecoded": floors["undecoded"],
        "instances_by_kind": {rocks.KIND_NAMES[k]: int(n) for k, n in enumerate(kinds) if n},
        "placed_triangles_by_kind": {
            rocks.KIND_NAMES[k]: int(placed[arrays["inst_kind"] == k].sum())
            for k, n in enumerate(kinds)
            if n
        },
        "local_triangles": len(arrays["mesh_tris"]),
        "dropped": dropped,
    }


def encode_rock_pack(
    reader: GameReader, sweep: Sweep, build_pin: str, build_raw: JsonObject
) -> dict[str, bytes]:
    """``rocks.npz`` and ``rocks.json`` as bytes, ready for the field's directory."""
    from satisfactory_mcp.domain.spatial.heightfield import collision_pack as rocks

    started = time.time()
    wanted = sorted({m for m in sweep["meshes"] if is_pack_mesh(m)} | set(sweep["foliage"]))
    collision = read_collision_meshes(reader, wanted)
    floors = sweep_cave_floors(reader)
    arrays, counts = rock_pack_arrays(sweep, collision, floors)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    meta = {
        "description": (
            "The collision surface of every placed rock, arch, foliage boulder and cave "
            "floor: one mesh each, mesh-local cm, plus a transform per placement. Cut from the "
            "reader's own install by tools/gen_world_heightmap.py; never committed."
        ),
        "generator": "tools/gen_world_heightmap.py --rocks",
        "rocks_version": rocks.ROCKS_VERSION,
        "transcribed": datetime.now(UTC).strftime("%Y-%m-%d"),
        "sources": {"game": {"game_version_pinned": build_pin, "build_raw": build_raw}},
        "kinds": {str(k): v for k, v in rocks.KIND_NAMES.items()},
        "arrays": (
            "mesh_verts (V, 3) f32 local cm, sliced per mesh by mesh_vstart; mesh_tris (T, 3) "
            "i32 into the mesh's own vertices, sliced by mesh_tstart; mesh_winding +1 outward, "
            "-1 inward, 0 open; inst_*: mesh, kind, row-vector matrix and origin "
            "(world = local @ matrix + origin), world box lo and hi"
        ),
        "counts": counts,
        "seconds": round(time.time() - started, 1),
        "digest": sha256_hex(buffer.getvalue()),
    }
    return {
        rocks.DATA_NAME: buffer.getvalue(),
        rocks.META_NAME: json.dumps(meta, indent=1).encode("utf-8"),
    }

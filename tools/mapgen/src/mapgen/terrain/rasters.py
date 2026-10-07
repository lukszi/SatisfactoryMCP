"""The rocks, arches and boulders the field is built from, rasterised into the render's grid."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NamedTuple, TypeAlias, TypedDict, overload

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.level.sweep import Sweep, sweep_levels
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.gamedata.meshes import MeshBounds, read_hull, read_mesh_geometry, winding_sign
from mapgen.gamedata.placements import (
    ARCH_MARK,
    EXCLUDED_MESHES,
    EXCLUDED_OWNERS,
    OVERSIZE_CM,
    rotation_matrix,
)
from mapgen.gamedata.vegetation.trees import is_tree
from mapgen.gamedata.water.falls import read_fall
from mapgen.terrain.render_meshes import (
    InstanceSpans,
    Shape,
    instance_y_spans,
    is_render_only_foliage,
)
from satisfactory_mcp.core.arrays import F32Grid, FloatGrid, I64Grid, U16Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects

__all__ = [
    "DIRECT_SUBSAMPLES",
    "TOP_FOLIAGE_BATCH",
    "CliffGeometry",
    "Geometry",
    "PreparedPlacement",
    "TopItems",
    "TopMeta",
    "add_placements",
    "direct_placements",
    "placed",
    "rasterise_direct_band",
    "read_cliff_geometry",
    "sweep_world",
    "top_items",
]

#: How many sub-samples per output texel per axis the direct pass rasterises at. The pass
#: costs 4x per doubling and the silhouette is already at 0.229 m, an eighth of the 1 m
#: staircase this regime exists to remove. Raise it with ``--direct-subsamples`` and the
#: sidecar records what was run. A silhouette is antialiased only by these sub-samples:
#: rock heights are never blurred across one.
DIRECT_SUBSAMPLES = 1

#: Foliage instances transformed per batch in the top pass.
TOP_FOLIAGE_BATCH = 512

#: The world's placed meshes by path, at the finest geometry each ships: ``(verts, tris)``.
Geometry: TypeAlias = dict[str, tuple[F32Grid, I64Grid]]


class PreparedPlacement(NamedTuple):
    """One rock ready for the direct pass: its mesh, transform, facing and y extent in cm."""

    mesh: str
    mesh_id: int
    matrix: F32Grid
    scale: F32Grid
    offset: F32Grid
    facing: int
    y_min_cm: float
    y_max_cm: float
    family: int | None = None


class CliffGeometry(TypedDict):
    """``read_cliff_geometry``: the sweep, every rock's clamped geometry, and the tallies."""

    sweep: Sweep
    geometry: Geometry
    meshes: int
    by_source: dict[str, int]
    verts: int
    tris: int
    triangles_out_of_bounds: int
    seconds_sweep: float
    seconds_decode: float


class TopItems(NamedTuple):
    """What the top pass draws: the arches, the foliage boulders, and every shape they use."""

    arches: list[PreparedPlacement]
    boulders: dict[str, InstanceSpans]
    shapes: dict[str, Shape]


class TopMeta(TypedDict):
    """``top_items``' record: how many arches and boulders, what decoded them, and the arches
    dropped as oversized or off the raster."""

    arch_placements: int
    arches_dropped: dict[str, int]
    foliage_instances: int
    foliage_sources: dict[str, str]
    meshes_skipped: list[str]


# --------------------------------------------------------------------------------------
# The direct pass: the same rocks the field is built from, rasterised into THIS grid.
# --------------------------------------------------------------------------------------


def read_cliff_geometry(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    classes: ClassFacts,
    progress: bool = True,
    sweep: Sweep | None = None,
) -> CliffGeometry:
    """The world's placements and the finest triangles every placed rock ships.

    The field's own sweep (``sweep_levels``) and finest-source ladder (``read_mesh_geometry``)
    over the same hull-equivalent mesh set. The one thing done here is their per-triangle
    bounds clamp, hoisted out of the placement loop: it is a test in the mesh's own local
    space against its own padded ``ExtendedBounds``, the same for every copy of a rock.
    """
    if sweep is None:
        sweep = sweep_world(store, scripts, index, classes, progress)
    read = read_mesh_geometry(store, scripts, index, sweep["meshes"], progress)
    geometry: Geometry = {}
    clamped = 0
    for mesh, (verts, tris, low, high) in read["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
        if tris.size:
            geometry[mesh] = (verts, np.ascontiguousarray(tris))
    return {
        "sweep": sweep,
        "geometry": geometry,
        "meshes": len(geometry),
        "by_source": read["by_source"],
        "verts": int(sum(v.shape[0] for v, _t in geometry.values())),
        "tris": int(sum(t.shape[0] for _v, t in geometry.values())),
        "triangles_out_of_bounds": clamped,
        "seconds_sweep": round(sweep["seconds"], 1),
        "seconds_decode": round(read["seconds"], 1),
    }


def sweep_world(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    classes: ClassFacts,
    progress: bool = True,
) -> Sweep:
    """The field generator's sweep, also harvesting the render-only foliage and the trees."""
    sweep = sweep_levels(
        store,
        scripts,
        classes,
        MeshBounds(store, scripts, index),
        progress,
        extra_foliage=lambda mesh: is_render_only_foliage(mesh) or is_tree(mesh),
        read_actor=read_fall,
    )
    extra = sweep["extra_foliage"]
    sweep["trees"] = {m: mats for m, mats in extra.items() if is_tree(m)}
    sweep["extra_foliage"] = {m: mats for m, mats in extra.items() if is_render_only_foliage(m)}
    return sweep


def _box_corners() -> F32Grid:
    """The eight corners of the unit cube, scaled onto a mesh's own vertex box."""
    return np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)


def _on_raster(world_cm: NDArray[np.floating]) -> bool:
    """Whether a placement's world box, as corners in cm, reaches into the map's frame."""
    lo, hi = world_cm.min(0) / 100.0, world_cm.max(0) / 100.0
    return bool(
        hi[0] >= BOUNDS_M["x_min_m"]
        and lo[0] <= BOUNDS_M["x_max_m"]
        and hi[1] >= BOUNDS_M["y_min_m"]
        and lo[1] <= BOUNDS_M["y_max_m"]
    )


def direct_placements(
    sweep: Sweep, geometry: Geometry, families: NDArray[np.integer] | None = None
) -> tuple[list[PreparedPlacement], dict[str, int]]:
    """Every placement the field's own cliff layer rasterises, with its world Y span.

    The five culls are the field's (``rasterise_cliffs``), in its order: an excluded owner,
    an excluded mesh, a mesh with no cooked geometry, an arch, an oversized shell. Any of
    them applied differently here would draw a different world from the field it blends with.

    ``families``, one code per placement row, is carried as each entry's raster source id.
    The **Y span** in world centimetres of the placement's transformed vertex box is the
    whole of the band selection: a bounding-interval test over the placements.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    windings = {mesh: winding_sign(v, t) for mesh, (v, t) in geometry.items()}
    corners = _box_corners()
    prepared: list[PreparedPlacement] = []
    dropped = {"owner": 0, "excluded_mesh": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    for i, row in enumerate(sweep["placements"]):
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh: str = meshes[mesh_id]
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
        verts, _tris = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        offset = row[2:5].astype(np.float32)
        low, high = verts.min(0), verts.max(0)
        box = low + corners * (high - low)
        world_y = ((box * scale) @ matrix + offset)[:, 1]
        facing = int(windings[mesh] * np.sign(scale[0] * scale[1] * scale[2]))
        family = None if families is None else int(families[i])
        prepared.append(
            PreparedPlacement(
                mesh,
                mesh_id,
                matrix,
                scale,
                offset,
                facing,
                float(world_y.min()),
                float(world_y.max()),
                family,
            )
        )
    return prepared, dropped


@overload
def rasterise_direct_band(
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
    with_source: Literal[False] = False,
) -> F32Grid: ...
@overload
def rasterise_direct_band(
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
    with_source: Literal[True],
) -> tuple[F32Grid, U16Grid]: ...
def rasterise_direct_band(
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
    with_source: bool = False,
) -> F32Grid | tuple[F32Grid, U16Grid]:
    """One band of the output, max-Z rasterised from the triangles. ``nan`` where none fell.

    ``with_source`` also returns the winning triangle's source id: the placement's family
    when ``direct_placements`` was given families.

    The rasteriser is ``MaxZRaster`` itself, pointed at a grid whose origin is this band's
    north-west corner and whose spacing is this render's, divided by the sub-sampling.
    Sampled at ``col + 0.5`` in grid units and written to ``col`` (``sample=0.5``), which is
    exactly ``frame_coordinates``' pixel centres when the origin is the frame's own corner.

    The facing cull runs per placement as it does in the field, and then the triangles are
    cut down to the ones whose own Y interval reaches this band.
    """
    raster = MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples, sample=0.5
    )
    add_placements(raster, prepared, geometry, y0_cm, y0_cm + rows * scale_cm)
    z, source, _density = raster.result()
    return (z, source) if with_source else z


def add_placements(
    raster: MaxZRaster,
    prepared: Sequence[PreparedPlacement],
    geometry: Geometry,
    y_lo: float,
    y_hi: float,
) -> None:
    """Every prepared placement whose Y span reaches ``[y_lo, y_hi]``, into ``raster``.

    The source id is an entry's family when it has one, else its mesh id plus one.
    """
    for entry in prepared:
        found = placed(entry, geometry, y_lo, y_hi)
        if found is None:
            continue
        world, tris = found
        if entry.facing:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * entry.facing) > 0]
            if not tris.size:
                continue
        raster.add(world[tris], entry.mesh_id + 1 if entry.family is None else entry.family)


def placed(
    entry: PreparedPlacement, geometry: Geometry, y_lo: float, y_hi: float
) -> tuple[FloatGrid, I64Grid] | None:
    """A placement's vertices in world cm and its triangles whose Y interval reaches
    ``[y_lo, y_hi]``; None where none does."""
    if entry.y_max_cm < y_lo or entry.y_min_cm > y_hi:
        return None
    verts, tris = geometry[entry.mesh]
    world = (verts * entry.scale) @ entry.matrix + entry.offset
    ty = world[:, 1][tris]
    tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
    return (world, tris) if tris.size else None


def top_items(
    store: IoStore, scripts: ScriptObjects, index: AssetIndex, sweep: Sweep, geometry: Geometry
) -> tuple[TopItems, TopMeta]:
    """The arches and foliage boulders ``top.i16.z`` carries, at their finest geometry.

    Arches are placements, prepared like rocks but never culled by facing: an arch is an
    open shell often enough that a winding guess would drop its deck. An arch larger than
    ``OVERSIZE_CM`` or wholly off the raster is dropped, as ``direct_placements`` drops a
    rock: the distant scenery arches. Boulders are foliage instances with their own 4x4
    matrices. Both fall back to the collision trimesh the field rasterised when no finer
    source decodes.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    shapes: dict[str, Shape | None] = dict(geometry)
    finest = read_mesh_geometry(store, scripts, index, list(sweep["foliage"]), False)
    for mesh, (verts, tris, low, high) in finest["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        shapes[mesh] = (verts, tris[keep[tris].all(axis=1)])

    def shape(mesh: str) -> Shape | None:
        if mesh not in shapes:
            shapes[mesh] = read_hull(store, scripts, index, mesh)
        return shapes[mesh]

    corners = _box_corners()
    arches: list[PreparedPlacement] = []
    skipped: set[str] = set()
    dropped = {"oversize": 0, "off_raster": 0}
    for row in sweep["placements"]:
        mesh_id = int(row[0])
        if mesh_id not in arch_ids or owners[int(row[1])] in EXCLUDED_OWNERS:
            continue
        mesh: str = meshes[mesh_id]
        found = shape(mesh)
        if found is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        verts = found[0]
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        scale, offset = row[8:11].astype(np.float32), row[2:5].astype(np.float32)
        if float(np.abs(verts * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        low, high = verts.min(0), verts.max(0)
        world = ((low + corners * (high - low)) * scale) @ matrix + offset
        if not _on_raster(world):
            dropped["off_raster"] += 1
            continue
        arches.append(
            PreparedPlacement(
                mesh,
                mesh_id,
                matrix,
                scale,
                offset,
                0,
                float(world[:, 1].min()),
                float(world[:, 1].max()),
            )
        )
    boulders: dict[str, InstanceSpans] = {}
    for mesh, mats in sweep["foliage"].items():
        found = shape(mesh)
        if found is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        boulders[mesh] = instance_y_spans(found[0], np.asarray(mats, np.float32))
    drawn = {mesh: found for mesh, found in shapes.items() if found is not None}
    return TopItems(arches, boulders, drawn), {
        "arch_placements": len(arches),
        "arches_dropped": dropped,
        "foliage_instances": int(sum(len(group.mats) for group in boulders.values())),
        "foliage_sources": finest["sources"],
        "meshes_skipped": sorted(skipped),
    }

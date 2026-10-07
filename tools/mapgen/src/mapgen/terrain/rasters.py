"""The rocks, arches and boulders the field is built from, rasterised into the render's grid."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, TypeAlias, TypedDict, overload

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_BAND_ROWS,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    PLANE_DTYPES,
    STORAGE_BANDS,
    DirectStamp,
    band_spans,
    rewrite_planes,
    write_sidecar,
)
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
from satisfactory_mcp.core.arrays import F32Grid, I64Grid, U8Grid, U16Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects

__all__ = [
    "DIRECT_RASTER_ROLE",
    "DIRECT_SUBSAMPLES",
    "TOP_FOLIAGE_BATCH",
    "TOP_RASTER_ROLE",
    "BandPlanes",
    "BandRaster",
    "CliffGeometry",
    "Geometry",
    "PreparedPlacement",
    "RasterStats",
    "TopItems",
    "TopMeta",
    "add_placements",
    "direct_placements",
    "fold_band",
    "pixel_coverage",
    "rasterise_direct_band",
    "read_cliff_geometry",
    "reduce_direct",
    "reduce_source",
    "sweep_world",
    "top_items",
    "write_banded_raster",
]

#: How many sub-samples per output texel per axis the direct pass rasterises at. The pass
#: costs 4x per doubling and the silhouette is already at 0.229 m, an eighth of the 1 m
#: staircase this regime exists to remove. Raise it with ``--direct-subsamples`` and the
#: sidecar records what was run. A silhouette is antialiased only by these sub-samples:
#: rock heights are never blurred across one.
DIRECT_SUBSAMPLES = 1

#: Foliage instances transformed per batch in the top pass.
TOP_FOLIAGE_BATCH = 512

#: What a direct or top cache's sidecar says it holds.
DIRECT_RASTER_ROLE = (
    "max-Z of the cliff geometry on this render's own grid, in world centimetres, "
    "with the count of sub-samples that hit something beside it. Written once and "
    "read by every layer; deleted at the end of the run unless --keep-direct."
)
TOP_RASTER_ROLE = (
    "max-Z of the arches and foliage boulders on this render's own grid, in world "
    "centimetres, with the count of sub-samples that hit something beside it. Written once "
    "and read by every layer; deleted at the end of the run unless --keep-direct."
)

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


class RasterStats(DirectStamp):
    """A direct or top cache's sidecar: its stamp, and what the pass wrote."""

    storage: str
    sub_texel_m: float
    texels_with_geometry: int
    share_of_the_sheet: float
    seconds: float
    band_rows: int
    role: str


#: A band its rasteriser has folded onto the output grid itself: plane name -> rows.
BandPlanes: TypeAlias = dict[str, NDArray[np.generic]]


class BandRaster(Protocol):
    """One band of a banded raster: max-Z in cm, and with it the winning source per sample;
    or the band's planes, already on the output grid."""

    def __call__(self, x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int,
                 subsamples: int, /) -> F32Grid | tuple[F32Grid, U16Grid] | BandPlanes: ...  # fmt: skip


# --------------------------------------------------------------------------------------
# The direct pass: the same rocks the field is built from, rasterised into THIS grid.
# --------------------------------------------------------------------------------------


def read_cliff_geometry(store: IoStore, scripts: ScriptObjects, index: AssetIndex,
                        classes: ClassFacts, progress: bool = True,
                        sweep: Sweep | None = None) -> CliffGeometry:  # fmt: skip
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


def sweep_world(store: IoStore, scripts: ScriptObjects, index: AssetIndex, classes: ClassFacts,
                progress: bool = True) -> Sweep:  # fmt: skip
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
        hi[0] >= BOUNDS_M["x_min_m"] and lo[0] <= BOUNDS_M["x_max_m"]
        and hi[1] >= BOUNDS_M["y_min_m"] and lo[1] <= BOUNDS_M["y_max_m"]
    )  # fmt: skip


def direct_placements(sweep: Sweep, geometry: Geometry, families: NDArray[np.integer] | None = None
                      ) -> tuple[list[PreparedPlacement], dict[str, int]]:  # fmt: skip
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
        prepared.append(PreparedPlacement(mesh, mesh_id, matrix, scale, offset, facing,
                                          float(world_y.min()), float(world_y.max()), family))  # fmt: skip
    return prepared, dropped


@overload
def rasterise_direct_band(prepared: Sequence[PreparedPlacement], geometry: Geometry,
                          x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int,
                          subsamples: int, with_source: Literal[False] = False) -> F32Grid: ...  # fmt: skip
@overload
def rasterise_direct_band(prepared: Sequence[PreparedPlacement], geometry: Geometry,
                          x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int,
                          subsamples: int, with_source: Literal[True]) -> tuple[F32Grid, U16Grid]: ...  # fmt: skip
def rasterise_direct_band(prepared: Sequence[PreparedPlacement], geometry: Geometry,
                          x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int,
                          subsamples: int, with_source: bool = False
                          ) -> F32Grid | tuple[F32Grid, U16Grid]:  # fmt: skip
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


def add_placements(raster: MaxZRaster, prepared: Sequence[PreparedPlacement], geometry: Geometry,
                   y_lo: float, y_hi: float) -> None:  # fmt: skip
    """Every prepared placement whose Y span reaches ``[y_lo, y_hi]``, into ``raster``.

    The source id is an entry's family when it has one, else its mesh id plus one.
    """
    for entry in prepared:
        if entry.y_max_cm < y_lo or entry.y_min_cm > y_hi:
            continue
        verts, tris = geometry[entry.mesh]
        world = (verts * entry.scale) @ entry.matrix + entry.offset
        if entry.facing:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * entry.facing) > 0]
            if not tris.size:
                continue
        ty = world[:, 1][tris]
        tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
        if not tris.size:
            continue
        raster.add(world[tris], entry.mesh_id + 1 if entry.family is None else entry.family)


def top_items(store: IoStore, scripts: ScriptObjects, index: AssetIndex, sweep: Sweep,
              geometry: Geometry) -> tuple[TopItems, TopMeta]:  # fmt: skip
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
        arches.append(PreparedPlacement(mesh, mesh_id, matrix, scale, offset, 0,
                                        float(world[:, 1].min()), float(world[:, 1].max())))  # fmt: skip
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


def reduce_direct(sub_z: F32Grid, rows: int, cols: int, subsamples: int) -> tuple[F32Grid, U8Grid]:
    """A sub-sampled band folded onto the output grid: mean height and coverage count.

    The mean is over the sub-samples that HIT something and the count comes back beside it,
    which is the difference between "half a rock and half the ground behind it" and "a rock
    at half its height".
    """
    if subsamples == 1:
        hit = np.isfinite(sub_z)
        return np.where(hit, sub_z, 0.0).astype(np.float32), hit.astype(np.uint8)
    block = sub_z.reshape(rows, subsamples, cols, subsamples)
    hit = np.isfinite(block)
    count = hit.sum((1, 3)).astype(np.uint8)
    total = np.where(hit, block, 0.0).sum((1, 3), dtype=np.float32)
    return (total / np.maximum(count, 1)).astype(np.float32), count


def reduce_source(sub_z: F32Grid, sub_source: NDArray[np.integer], rows: int, cols: int,
                  subsamples: int) -> U8Grid:  # fmt: skip
    """The source id of each output texel's highest sub-sample; 0 where nothing fell."""
    hit = np.isfinite(sub_z)
    if subsamples == 1:
        return np.where(hit, sub_source, 0).astype(np.uint8)
    z = np.where(hit, sub_z, -np.inf).reshape(rows, subsamples, cols, subsamples)
    src = np.where(hit, sub_source, 0).reshape(rows, subsamples, cols, subsamples)
    z = z.transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    src = src.transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    best = np.take_along_axis(src, z.argmax(-1)[..., None], -1)[..., 0]
    return best.astype(np.uint8)


def fold_band(sub: F32Grid | tuple[F32Grid, U16Grid] | BandPlanes, rows: int, cols: int,
              subsamples: int) -> BandPlanes:  # fmt: skip
    """A band raster's answer as the planes of its cache, on the output grid."""
    if isinstance(sub, dict):
        return sub
    sub_z, sub_source = sub if isinstance(sub, tuple) else (sub, None)
    band_z, band_coverage = reduce_direct(sub_z, rows, cols, subsamples)
    planes: BandPlanes = {DIRECT_Z_NAME: band_z, DIRECT_COVERAGE_NAME: band_coverage}
    if sub_source is not None:
        planes[DIRECT_FAMILY_NAME] = reduce_source(sub_z, sub_source, rows, cols, subsamples)
    return planes


def pixel_coverage(coverage: NDArray[np.generic], subsamples: int) -> F32Grid:
    """The share of a pixel's sub-samples a triangle hit, in [0, 1]. No neighbour is read."""
    return coverage.astype(np.float32) / np.float32(subsamples * subsamples)


def write_banded_raster(band_raster: BandRaster, directory: Path, size: int, subsamples: int,
                        stamp: DirectStamp, progress: bool, *,
                        role: str = DIRECT_RASTER_ROLE) -> RasterStats:  # fmt: skip
    """Rasterise every placed rock into the render's own grid, ``DIRECT_BAND_ROWS`` at a time,
    into a cache ``cached_raster`` reads back only under the same ``stamp``.

    A ``band_raster`` that returns ``(z, source)`` also writes the family plane, and one that
    returns ``BandPlanes`` writes those; the first band says which, before any plane is
    opened, and every band after it must agree.
    """
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    x0_cm = BOUNDS_M["x_min_m"] * 100
    started = time.time()

    def band_at(top: int, bottom: int) -> BandPlanes:
        y0_cm = BOUNDS_M["y_min_m"] * 100 + top * step_cm
        sub = band_raster(x0_cm, y0_cm, step_cm, bottom - top, size, subsamples)
        return fold_band(sub, bottom - top, size, subsamples)

    spans = list(band_spans(size, DIRECT_BAND_ROWS))
    first = band_at(*spans[0])
    names = tuple(first)
    covered = 0
    with rewrite_planes(directory, names, size, DIRECT_BAND_ROWS, clear=PLANE_DTYPES) as planes:
        for band, (top, bottom) in enumerate(spans):
            got = first if band == 0 else band_at(top, bottom)
            if tuple(got) != names:
                raise ValueError(f"band {band} of {directory.name} changed what it returns")
            for plane, name in zip(planes, names, strict=True):
                plane.write(top, got[name])
            covered += int(np.count_nonzero(got[DIRECT_COVERAGE_NAME]))
            if progress and band % 8 == 0:
                print(
                    f"  {directory.name}: {bottom / size:5.1%} of {size}x{size} at "
                    f"{step_cm / 100 / subsamples:.4f} m, {covered / 1e6:.1f} M texels, "
                    f"{time.time() - started:5.1f}s",
                    flush=True,
                )
    stats: RasterStats = {
        **stamp,
        "storage": STORAGE_BANDS,
        "sub_texel_m": round(step_cm / 100 / subsamples, 5),
        "texels_with_geometry": covered,
        "share_of_the_sheet": round(100 * covered / (size * size), 3),
        "seconds": round(time.time() - started, 1),
        "band_rows": DIRECT_BAND_ROWS,
        "role": role,
    }
    write_sidecar(directory / CACHE_SIDECAR_NAME, stats)
    return stats

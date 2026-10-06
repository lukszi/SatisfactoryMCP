"""The rocks, arches, boulders and render-only meshes, rasterised into the render's own grid."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from mapgen.cache import (
    DIRECT_CACHE_SIDECAR,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    MESH_CACHE_SIDECAR,
    MESH_CLASS_NAME,
    MESH_Z_NAME,
    cached_meshes,
    mesh_stamp,
)
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.mesh import (
    ARCH_MARK,
    EXCLUDED_MESHES,
    EXCLUDED_OWNERS,
    OVERSIZE_CM,
    MaxZRaster,
    MeshBounds,
    finer_source,
    read_hull,
    read_mesh_geometry,
    rotation_matrix,
    winding_sign,
)
from mapgen.gamedata.sweep import is_top_foliage, sweep_levels
from mapgen.gamedata.trees import is_tree
from mapgen.gamedata.waterfalls import read_fall
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS

__all__ = [
    "DIRECT_BAND_ROWS",
    "DIRECT_SUBSAMPLES",
    "MESH_CLASS_NAMES",
    "MESH_CORAL",
    "MESH_FOLIAGE_BATCH",
    "MESH_ROCK",
    "MESH_SHELL",
    "MESH_TERRACE",
    "RENDER_ONLY_DIRS",
    "RENDER_ONLY_FOLIAGE_MARKS",
    "RENDER_ONLY_FOLIAGE_MESHES",
    "RENDER_ONLY_MESHES",
    "TITAN_LEAVES",
    "TITAN_MARK",
    "TITAN_TRUNK",
    "TOP_FOLIAGE_BATCH",
    "add_placements",
    "direct_placements",
    "is_render_only_foliage",
    "is_render_only_static",
    "mesh_class",
    "mesh_items",
    "mesh_pass",
    "pixel_coverage",
    "rasterise_direct",
    "rasterise_direct_band",
    "rasterise_mesh_band",
    "rasterise_meshes",
    "rasterise_top_band",
    "read_cliff_geometry",
    "read_shape",
    "reduce_direct",
    "reduce_source",
    "sweep_world",
    "titan_class",
    "titan_items",
    "top_items",
]

#: How many sub-samples per output texel per axis the direct pass rasterises at. The pass
#: costs 4x per doubling and the silhouette is already at 0.229 m, an eighth of the 1 m
#: staircase this regime exists to remove. Raise it with ``--direct-subsamples`` and the
#: sidecar records what was run. A silhouette is antialiased only by these sub-samples:
#: rock heights are never blurred across one.
DIRECT_SUBSAMPLES = 1


#: Rows of the output the direct pass rasterises at a time. A whole 32768 square of float32
#: is 4.3 GB and the render already holds 3.2 GB of output; 256 rows is 34 MB, and a
#: triangle at the 0.48 m median touches one band or two, so a per-placement y-bbox test is
#: all the selection needed. Also the colour bands' size and one row of 256 px tiles.
DIRECT_BAND_ROWS = 256


#: Foliage instances transformed per batch in the top pass.
TOP_FOLIAGE_BATCH = 512


#: Which meshes are drawn on the map and never enter the heightfield.
RENDER_ONLY_DIRS = (
    "/World/Environment/Foliage/Coral/",
    "/World/Environment/UnderWater/",
    "/World/Environment/HotSpring/",
)
RENDER_ONLY_FOLIAGE_MARKS = ("/Rubble/", "SeaRock")
RENDER_ONLY_FOLIAGE_MESHES = frozenset({"SmoothRock_03", "SnakeStone_01"})
RENDER_ONLY_MESHES = EXCLUDED_MESHES
ROCK_CLASS_MARKS = ("CliffPillar", "Rubble", "RockPile", "SeaRock", "SmoothRock", "SnakeStone")

#: Mesh classes, as stored in the cache's class plane. 0 is nothing.
MESH_CORAL, MESH_SHELL, MESH_ROCK, MESH_TERRACE = 1, 2, 3, 4
MESH_CLASS_NAMES = {
    MESH_CORAL: "coral",
    MESH_SHELL: "shell",
    MESH_ROCK: "rock",
    MESH_TERRACE: "terrace",
}

#: The Titan forest's static trees, drawn by the painted layer only, in a raster of their own.
TITAN_MARK = "TitanTree"
TITAN_TRUNK, TITAN_LEAVES = 4, 5


MESH_FOLIAGE_BATCH = 512


# --------------------------------------------------------------------------------------
# The direct pass: the same rocks the field is built from, rasterised into THIS grid.
# --------------------------------------------------------------------------------------


def read_cliff_geometry(
    store, scripts, index, classes, progress: bool = True, sweep: dict | None = None
) -> dict:
    """The world's placements and the finest triangles every placed rock ships.

    Two calls into ``tools/gen_world_heightmap.py``: the same pass over the same 4,521
    ``*.umap`` the field was cut from, and the same finest-source ladder over the same
    hull-equivalent mesh set.

    The one thing done here is the generator's per-triangle bounds clamp, hoisted out of the
    placement loop. It is a test in the mesh's own local space against the mesh's own padded
    ``ExtendedBounds``, so it gives the same answer for all two hundred copies of a rock.
    """
    if sweep is None:
        sweep = sweep_world(store, scripts, index, classes, progress)
    read = read_mesh_geometry(store, scripts, index, sweep["meshes"], progress)
    geometry: dict[str, tuple[np.ndarray, np.ndarray]] = {}
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


def sweep_world(store, scripts, index, classes, progress: bool = True) -> dict:
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


def direct_placements(sweep: dict, geometry: dict, families=None) -> tuple[list, dict]:
    """Every placement the field's own cliff layer rasterises, with its world Y span.

    The four culls are the generator's, in the generator's order: an excluded owner, a mesh
    with no cooked geometry, an arch, an oversized shell. Any of them applied differently
    here would draw a render of a different world from the field it is blended with.

    ``families``, one code per placement row, is carried as each entry's raster source id.

    What is added is the **Y span**, in world centimetres, of the placement's transformed
    vertex box. That is the whole of the band selection: a bounding-interval test over
    24,000 placements is a numpy comparison rather than a search.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    windings = {mesh: winding_sign(v, t) for mesh, (v, t) in geometry.items()}
    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    prepared: list[tuple] = []
    dropped = {"owner": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    for i, row in enumerate(sweep["placements"]):
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in EXCLUDED_OWNERS:
            dropped["owner"] += 1
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
        facing = windings[mesh] * float(np.sign(scale[0] * scale[1] * scale[2]))
        prepared.append(
            (
                mesh,
                mesh_id,
                matrix,
                scale,
                offset,
                facing,
                float(world_y.min()),
                float(world_y.max()),
                *(() if families is None else (int(families[i]),)),
            )
        )
    return prepared, dropped


def rasterise_direct_band(
    prepared: list,
    geometry: dict,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
    with_source: bool = False,
):
    """One band of the output, max-Z rasterised from the triangles. ``nan`` where none fell.

    ``with_source`` also returns the winning triangle's source id: the placement's family
    when ``direct_placements`` was given families.

    The rasteriser is ``gen_world_heightmap.MaxZRaster`` itself, pointed at a grid whose
    origin is this band's north-west corner and whose spacing is this render's, divided by
    the sub-sampling. Sampled at ``col + 0.5`` in grid units and written to ``col``
    (``sample=0.5``), which is exactly ``frame_coordinates``' pixel centres when the origin is the frame's
    own corner, so nothing is half a texel out.

    The facing cull runs per placement as it does in the generator, and then the triangles
    are cut down to the ones whose own Y interval reaches this band.
    """
    raster = MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples, sample=0.5
    )
    add_placements(raster, prepared, geometry, y0_cm, y0_cm + rows * scale_cm)
    z, source, _density = raster.result()
    return (z, source) if with_source else z


def add_placements(raster, prepared: list, geometry: dict, y_lo: float, y_hi: float) -> None:
    """Every prepared placement whose Y span reaches ``[y_lo, y_hi]``, into ``raster``.

    The source id is an entry's ninth field when it has one, else its mesh id plus one.
    """
    for entry in prepared:
        mesh, mesh_id, matrix, scale, offset, facing, span_lo, span_hi = entry[:8]
        if span_hi < y_lo or span_lo > y_hi:
            continue
        verts, tris = geometry[mesh]
        world = (verts * scale) @ matrix + offset
        if facing != 0.0:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * facing) > 0]
            if not tris.size:
                continue
        ty = world[:, 1][tris]
        tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
        if not tris.size:
            continue
        raster.add(world[tris], entry[8] if len(entry) > 8 else mesh_id + 1)


def top_items(store, scripts, index, sweep: dict, geometry: dict) -> tuple[dict, dict]:
    """The arches and foliage boulders ``top.i16.z`` carries, at their finest geometry.

    Arches are placements, prepared like rocks but never culled by facing: an arch is an
    open shell often enough that a winding guess would drop its deck. Boulders are foliage
    instances with their own 4x4 matrices. Both fall back to the collision trimesh the
    field rasterised when no finer source decodes.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    shapes: dict[str, tuple[np.ndarray, np.ndarray] | None] = dict(geometry)
    finest = read_mesh_geometry(store, scripts, index, list(sweep["foliage"]), False)
    for mesh, (verts, tris, low, high) in finest["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        shapes[mesh] = (verts, tris[keep[tris].all(axis=1)])

    def shape(mesh):
        if mesh not in shapes:
            shapes[mesh] = read_hull(store, scripts, index, mesh)
        return shapes[mesh]

    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    arches, skipped = [], set()
    for row in sweep["placements"]:
        mesh_id = int(row[0])
        if mesh_id not in arch_ids or owners[int(row[1])] in EXCLUDED_OWNERS:
            continue
        mesh = meshes[mesh_id]
        if shape(mesh) is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        verts = shapes[mesh][0]
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        scale, offset = row[8:11].astype(np.float32), row[2:5].astype(np.float32)
        low, high = verts.min(0), verts.max(0)
        world_y = (((low + corners * (high - low)) * scale) @ matrix + offset)[:, 1]
        arches.append(
            (mesh, mesh_id, matrix, scale, offset, 0.0, float(world_y.min()), float(world_y.max()))
        )
    boulders = {}
    for mesh, mats in sweep["foliage"].items():
        if shape(mesh) is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        mats = np.asarray(mats, np.float32)
        reach = float(np.linalg.norm(shapes[mesh][0], axis=1).max())
        reach *= np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
        boulders[mesh] = (mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)
    items = {"arches": arches, "boulders": boulders, "shapes": shapes}
    return items, {
        "arch_placements": len(arches),
        "foliage_instances": int(sum(len(v[0]) for v in boulders.values())),
        "foliage_sources": finest["sources"],
        "meshes_skipped": sorted(skipped),
    }


def rasterise_top_band(
    items: dict, x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int, subsamples: int
) -> np.ndarray:
    """One band of arches and boulders, max-Z on the render's pixel centres."""
    raster = MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples, sample=0.5
    )
    y_hi = y0_cm + rows * scale_cm
    add_placements(raster, items["arches"], items["shapes"], y0_cm, y_hi)
    for mesh, (mats, span_lo, span_hi) in items["boulders"].items():
        verts, tris = items["shapes"][mesh]
        picked = mats[(span_hi >= y0_cm) & (span_lo <= y_hi)]
        for start in range(0, len(picked), TOP_FOLIAGE_BATCH):
            chunk = picked[start : start + TOP_FOLIAGE_BATCH]
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 1)
    return raster.result()[0]


def reduce_direct(sub_z: np.ndarray, rows: int, cols: int, subsamples: int):
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


def reduce_source(sub_z: np.ndarray, sub_source: np.ndarray, rows: int, cols: int, subsamples: int):
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


def pixel_coverage(coverage: np.ndarray, subsamples: int) -> np.ndarray:
    """The share of a pixel's sub-samples a triangle hit, in [0, 1]. No neighbour is read."""
    return coverage.astype(np.float32) / np.float32(subsamples * subsamples)


def rasterise_direct(
    band_raster,
    directory: Path,
    size: int,
    subsamples: int,
    stamp: dict,
    progress: bool,
) -> dict:
    """Rasterise every placed rock into the render's own grid, banded, onto disk.

    Banded because a 32768 square of float32 is 4.3 GB and the render already holds 3.2 GB
    of output; 256 rows is 34 MB. On disk because the answer is the same for both layers and
    rasterising 216 M triangles is twenty minutes. The two maps are written beside a sidecar
    naming what they are of, and ``cached_direct`` refuses anything that does not match
    rather than drawing last week's rocks under this week's field.

    A ``band_raster`` that returns ``(z, source)`` also writes the family plane.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / DIRECT_CACHE_SIDECAR).unlink(missing_ok=True)
    z = np.memmap(directory / DIRECT_Z_NAME, np.float32, "w+", shape=(size, size))
    coverage = np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "w+", shape=(size, size))
    family = None
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    x0_cm = BOUNDS_M["x_min_m"] * 100
    covered = 0
    started = time.time()
    for band, top in enumerate(range(0, size, DIRECT_BAND_ROWS)):
        bottom = min(top + DIRECT_BAND_ROWS, size)
        rows = bottom - top
        sub = band_raster(
            x0_cm,
            BOUNDS_M["y_min_m"] * 100 + top * step_cm,
            step_cm,
            rows,
            size,
            subsamples,
        )
        if isinstance(sub, tuple):
            if family is None:
                family = np.memmap(
                    directory / DIRECT_FAMILY_NAME, np.uint8, "w+", shape=(size, size)
                )
            family[top:bottom] = reduce_source(*sub, rows, size, subsamples)
            sub = sub[0]
        band_z, band_coverage = reduce_direct(sub, rows, size, subsamples)
        z[top:bottom] = band_z
        coverage[top:bottom] = band_coverage
        covered += int(np.count_nonzero(band_coverage))
        if progress and band % 8 == 0:
            print(
                f"  {directory.name}: {bottom / size:5.1%} of {size}x{size} at "
                f"{step_cm / 100 / subsamples:.4f} m, {covered / 1e6:.1f} M texels, "
                f"{time.time() - started:5.1f}s",
                flush=True,
            )
    z.flush()
    coverage.flush()
    if family is not None:
        family.flush()
    del z, coverage, family
    stats = {
        **stamp,
        "sub_texel_m": round(step_cm / 100 / subsamples, 5),
        "texels_with_geometry": covered,
        "share_of_the_sheet": round(100 * covered / (size * size), 3),
        "seconds": round(time.time() - started, 1),
        "band_rows": DIRECT_BAND_ROWS,
        "role": (
            "max-Z of the cliff geometry on this render's own grid, in world centimetres, "
            "with the count of sub-samples that hit something beside it. Written once and "
            "read by every layer; deleted at the end of the run unless --keep-direct."
        ),
    }
    (directory / DIRECT_CACHE_SIDECAR).write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


# ----------------------------------------------------------------------- render-only meshes


def is_render_only_static(mesh: str) -> bool:
    return any(d in mesh for d in RENDER_ONLY_DIRS) or mesh.rsplit("/", 1)[-1] in RENDER_ONLY_MESHES


def is_render_only_foliage(mesh: str) -> bool:
    if is_top_foliage(mesh):
        return False
    if mesh.rsplit("/", 1)[-1] in RENDER_ONLY_FOLIAGE_MESHES:
        return True
    return any(d in mesh for d in RENDER_ONLY_DIRS) or any(
        m in mesh for m in RENDER_ONLY_FOLIAGE_MARKS
    )


def mesh_class(mesh: str) -> int:
    name = mesh.rsplit("/", 1)[-1]
    if "Shell" in name:
        return MESH_SHELL
    if "/HotSpring/" in mesh:
        return MESH_TERRACE
    if any(mark in name for mark in ROCK_CLASS_MARKS):
        return MESH_ROCK
    return MESH_CORAL


def read_shape(store, scripts, index, mesh: str):
    """The finest geometry a mesh ships, falling back to its collision hull, or ``None``."""
    package = index.path_for(mesh)
    if not package:
        return None, "none"
    try:
        view = PackageView(store.read_path(package), scripts)
        export = staticmesh.static_mesh_export(view)
        bounds = staticmesh.extended_bounds(view, export) if export is not None else None
        found = finer_source(store, package, view, export, *bounds) if bounds else None
    except Exception:  # a mesh this reader cannot open is one mesh fewer on the map
        found = None
    if found is not None:
        source, (verts, tris) = found
        return (np.asarray(verts, np.float32), np.asarray(tris, np.int64)), source
    hull = read_hull(store, scripts, index, mesh)
    return hull, "hull" if hull is not None else "none"


def titan_class(mesh: str) -> int:
    name = mesh.rsplit("/", 1)[-1]
    if TITAN_MARK not in name:
        return 0
    return TITAN_LEAVES if "Leaves" in name else TITAN_TRUNK


def titan_items(store, scripts, index, sweep: dict) -> tuple[dict, dict]:
    """The Titan trees' placements in ``rasterise_mesh_band``'s format, at their finest mesh."""
    meshes = sweep["meshes"]
    groups: dict[str, list[np.ndarray]] = {}
    for row in sweep["placements"]:
        mesh = meshes[int(row[0])]
        if not titan_class(mesh):
            continue
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = rotation_matrix(*row[5:8]) * row[8:11][:, None]
        matrix[3, :3] = row[2:5]
        groups.setdefault(mesh, []).append(matrix)
    items, shapes, sources = {}, {}, {}
    for mesh, mats in groups.items():
        shape, source = read_shape(store, scripts, index, mesh)
        sources[mesh.rsplit("/", 1)[-1]] = source
        if shape is None or not len(shape[1]):
            continue
        shapes[mesh] = shape
        mats = np.asarray(mats, np.float32)
        reach = float(np.linalg.norm(shape[0], axis=1).max())
        reach = reach * np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
        items[mesh] = (titan_class(mesh), mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)
    counts = {mesh.rsplit("/", 1)[-1]: len(item[1]) for mesh, item in items.items()}
    return {"items": items, "shapes": shapes}, {"placements": counts, "sources": sources}


def mesh_items(store, scripts, index, sweep: dict) -> tuple[dict, dict]:
    """Every render-only placement and foliage instance as ``(class, matrix, offset)`` groups."""
    started = time.time()
    meshes = sweep["meshes"]
    wanted = sorted(
        {
            meshes[int(row[0])]
            for row in sweep["placements"]
            if is_render_only_static(meshes[int(row[0])])
        }
        | set(sweep.get("extra_foliage", {}))
    )
    shapes, sources = {}, {}
    for mesh in wanted:
        shape, source = read_shape(store, scripts, index, mesh)
        sources[mesh.rsplit("/", 1)[-1]] = source
        if shape is not None and len(shape[1]):
            shapes[mesh] = shape
    groups: dict[str, list[np.ndarray]] = {}
    for row in sweep["placements"]:
        mesh = meshes[int(row[0])]
        if mesh not in shapes or not is_render_only_static(mesh):
            continue
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = rotation_matrix(*row[5:8]) * row[8:11][:, None]
        matrix[3, :3] = row[2:5]
        groups.setdefault(mesh, []).append(matrix)
    for mesh, mats in sweep.get("extra_foliage", {}).items():
        if mesh in shapes:
            groups.setdefault(mesh, []).extend(np.asarray(mats))
    items = {}
    for mesh, mats in groups.items():
        mats = np.asarray(mats, np.float32)
        reach = float(np.linalg.norm(shapes[mesh][0], axis=1).max())
        reach = reach * np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
        items[mesh] = (mesh_class(mesh), mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)
    counts = {MESH_CLASS_NAMES[c]: 0 for c in MESH_CLASS_NAMES}
    for cls, mats, _lo, _hi in items.values():
        counts[MESH_CLASS_NAMES[cls]] += len(mats)
    return {"items": items, "shapes": shapes}, {
        "meshes": len(items),
        "instances": counts,
        "sources": sources,
        "seconds": round(time.time() - started, 1),
    }


def rasterise_mesh_band(prepared: dict, x0_cm, y0_cm, scale_cm, rows, cols):
    """One band: max-Z in world cm (nan where empty) and the class of the winning mesh."""
    raster = MaxZRaster(cols, rows, x0_cm, y0_cm, scale_cm, sample=0.5)
    y_hi = y0_cm + rows * scale_cm
    for mesh, (cls, mats, span_lo, span_hi) in prepared["items"].items():
        picked = mats[(span_hi >= y0_cm) & (span_lo <= y_hi)]
        if not len(picked):
            continue
        verts, tris = prepared["shapes"][mesh]
        for start in range(0, len(picked), MESH_FOLIAGE_BATCH):
            chunk = picked[start : start + MESH_FOLIAGE_BATCH]
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), cls)
    z, src, _density = raster.result()
    return z, np.where(np.isfinite(z), src, 0).astype(np.uint8)


def rasterise_meshes(
    prepared, directory: Path, stamp: dict, bounds_m: dict, band_rows: int, progress: bool
) -> dict:
    """Rasterise the render-only meshes into the render's grid, banded, onto disk."""
    size = stamp["size"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MESH_CACHE_SIDECAR).unlink(missing_ok=True)
    z_map = np.memmap(directory / MESH_Z_NAME, np.float32, "w+", shape=(size, size))
    class_map = np.memmap(directory / MESH_CLASS_NAME, np.uint8, "w+", shape=(size, size))
    step_cm = (bounds_m["x_max_m"] - bounds_m["x_min_m"]) * 100 / size
    covered, started = 0, time.time()
    for band, top in enumerate(range(0, size, band_rows)):
        bottom = min(top + band_rows, size)
        y0 = bounds_m["y_min_m"] * 100 + top * step_cm
        z, cls = rasterise_mesh_band(
            prepared, bounds_m["x_min_m"] * 100, y0, step_cm, bottom - top, size
        )
        z_map[top:bottom] = np.where(cls > 0, z, 0.0)
        class_map[top:bottom] = cls
        covered += int(np.count_nonzero(cls))
        if progress and band % 16 == 0:
            print(
                f"  {directory.name}: {bottom / size:5.1%}, {covered / 1e6:.2f} M texels, "
                f"{time.time() - started:5.1f}s",
                flush=True,
            )
    z_map.flush()
    class_map.flush()
    del z_map, class_map
    stats = {**stamp, "texels": covered, "seconds": round(time.time() - started, 1)}
    (directory / MESH_CACHE_SIDECAR).write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def mesh_pass(cache: Path, size: int, build, reader: str, build_items, label: str, quiet: bool):
    """A mesh raster of ``size`` px in ``cache``, reused when its stamp matches.

    ``build_items`` returns ``(prepared, meta)`` for ``rasterise_meshes``. Returns the
    ``(z cm, class)`` maps and the sidecar's source block, keyed by ``reader``.
    """
    stamp = mesh_stamp(size, build, READER_VERSIONS[reader])
    maps = cached_meshes(cache, stamp)
    if maps is not None:
        print(f"reusing the {label} raster already in {cache}")
        recorded = json.loads((cache / MESH_CACHE_SIDECAR).read_text(encoding="utf-8"))
        return maps, {reader: {"reused": recorded}}
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    print(f"rasterising the {label} at {spacing_m:.4f} m")
    prepared, meta = build_items()
    print(f"  {label}: {meta.get('instances', meta.get('placements'))}")
    stats = rasterise_meshes(prepared, cache, stamp, BOUNDS_M, DIRECT_BAND_ROWS, not quiet)
    print(f"  {label} raster: {stats['texels'] / 1e6:.2f} M texels in {stats['seconds']}s")
    return cached_meshes(cache, stamp), {reader: {**meta, "raster": stats}}

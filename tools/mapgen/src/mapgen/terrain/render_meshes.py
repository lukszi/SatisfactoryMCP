"""The render-only meshes and the Titan trees, rasterised for the renders run."""

from __future__ import annotations

import json
import time
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from mapgen.cache import (
    DIRECT_BAND_ROWS,
    MESH_CACHE_SIDECAR,
    MESH_CLASS_NAME,
    MESH_FAMILY_NAME,
    MESH_Z_NAME,
    STORAGE_BANDS,
    cached_mesh_family,
    cached_meshes,
    clear_planes,
    mesh_stamp,
    plane_writer,
)
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.level.sweep import is_top_foliage
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.gamedata.meshes import finer_source, read_hull
from mapgen.gamedata.placements import EXCLUDED_MESHES, placement_material, rotation_matrix
from mapgen.gamedata.rocks.families import FAMILIES, worn_family
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS

__all__ = [
    "MESH_CLASS_MASK",
    "MESH_CLASS_NAMES",
    "MESH_CORAL",
    "MESH_FAMILY_SHIFT",
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
    "is_render_only_foliage",
    "is_render_only_static",
    "mesh_class",
    "mesh_items",
    "mesh_pass",
    "rasterise_mesh_band",
    "rasterise_meshes",
    "read_shape",
    "titan_class",
    "titan_items",
]


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
#: A mesh raster's source code: the class in the low bits, a rock's family above them.
MESH_FAMILY_SHIFT = 3
MESH_CLASS_MASK = (1 << MESH_FAMILY_SHIFT) - 1

#: The Titan forest's static trees, drawn by the painted layer only, in a raster of their own.
TITAN_MARK = "TitanTree"
TITAN_TRUNK, TITAN_LEAVES = 4, 5


MESH_FOLIAGE_BATCH = 512


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


def _mesh_groups(store, scripts, index, sweep: dict, shapes: dict) -> dict[str, tuple]:
    """Per render-only mesh, its instances' matrices and source codes: the class, and for a
    rock the family its placement wears (``worn_family``), shifted above it."""
    meshes, caches = sweep["meshes"], ({}, {})
    groups: dict[str, tuple[list, list]] = {}

    def add(mesh: str, mats, materials) -> None:
        cls = mesh_class(mesh)
        mats_now, codes = groups.setdefault(mesh, ([], []))
        mats_now.extend(mats)
        for material in materials:
            rock = cls == MESH_ROCK
            family = worn_family(store, scripts, index, mesh, material, caches) if rock else 0
            codes.append(cls | family << MESH_FAMILY_SHIFT)

    for i, row in enumerate(sweep["placements"]):
        mesh = meshes[int(row[0])]
        if mesh not in shapes or not is_render_only_static(mesh):
            continue
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = rotation_matrix(*row[5:8]) * row[8:11][:, None]
        matrix[3, :3] = row[2:5]
        add(mesh, [matrix], [placement_material(sweep, i)])
    for mesh, mats in sweep.get("extra_foliage", {}).items():
        if mesh in shapes:
            add(mesh, list(np.asarray(mats)), [None] * len(mats))
    return groups


def mesh_items(store, scripts, index, sweep: dict) -> tuple[dict, dict]:
    """Every render-only placement and foliage instance as ``(codes, matrix, offset)`` groups."""
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
    items = {}
    counts = {MESH_CLASS_NAMES[c]: 0 for c in MESH_CLASS_NAMES}
    families = dict.fromkeys(FAMILIES, 0)
    for mesh, (mats, codes) in _mesh_groups(store, scripts, index, sweep, shapes).items():
        mats, codes = np.asarray(mats, np.float32), np.asarray(codes, np.uint16)
        reach = float(np.linalg.norm(shapes[mesh][0], axis=1).max())
        reach = reach * np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
        items[mesh] = (codes, mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)
        counts[MESH_CLASS_NAMES[mesh_class(mesh)]] += len(mats)
        if mesh_class(mesh) == MESH_ROCK:
            for code in codes >> MESH_FAMILY_SHIFT:
                families[FAMILIES[code]] += 1
    return {"items": items, "shapes": shapes, "families": True}, {
        "meshes": len(items),
        "instances": counts,
        "rock_families": {name: n for name, n in families.items() if n},
        "sources": sources,
        "seconds": round(time.time() - started, 1),
    }


def rasterise_mesh_band(prepared: dict, x0_cm, y0_cm, scale_cm, rows, cols):
    """One band: max-Z in world cm (nan where empty) and the source code of the winning mesh,
    its class or, per instance, its class and rock family (``MESH_FAMILY_SHIFT``)."""
    raster = MaxZRaster(cols, rows, x0_cm, y0_cm, scale_cm, sample=0.5)
    y_hi = y0_cm + rows * scale_cm
    for mesh, (code, mats, span_lo, span_hi) in prepared["items"].items():
        near = (span_hi >= y0_cm) & (span_lo <= y_hi)
        if not near.any():
            continue
        verts, tris = prepared["shapes"][mesh]
        codes, nearby = np.broadcast_to(np.asarray(code), near.shape)[near], mats[near]
        for value in np.unique(codes):
            picked = nearby[codes == value]
            for start in range(0, len(picked), MESH_FOLIAGE_BATCH):
                chunk = picked[start : start + MESH_FOLIAGE_BATCH]
                world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
                raster.add(world[:, tris].reshape(-1, 3, 3), int(value))
    z, src, _density = raster.result()
    return z, np.where(np.isfinite(z), src, 0).astype(np.uint8)


def rasterise_meshes(
    prepared, directory: Path, stamp: dict, bounds_m: dict, band_rows: int, progress: bool,
    storage: str = STORAGE_BANDS,
) -> dict:  # fmt: skip
    """Rasterise the render-only meshes into the render's grid, banded, onto disk: the class
    plane, and the family plane when the items carry families."""
    size = stamp["size"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MESH_CACHE_SIDECAR).unlink(missing_ok=True)
    clear_planes(directory, (MESH_Z_NAME, MESH_CLASS_NAME, MESH_FAMILY_NAME))
    step_cm = (bounds_m["x_max_m"] - bounds_m["x_min_m"]) * 100 / size
    covered, started = 0, time.time()
    names = (MESH_Z_NAME, MESH_CLASS_NAME, MESH_FAMILY_NAME)[: 3 if prepared.get("families") else 2]
    with ExitStack() as planes:
        maps = [planes.enter_context(plane_writer(directory, n, size, storage, band_rows))
                for n in names]  # fmt: skip
        z_map, class_map, family_map = (*maps, None)[:3]
        for band, top in enumerate(range(0, size, band_rows)):
            bottom = min(top + band_rows, size)
            y0 = bounds_m["y_min_m"] * 100 + top * step_cm
            z, code = rasterise_mesh_band(
                prepared, bounds_m["x_min_m"] * 100, y0, step_cm, bottom - top, size
            )
            cls = code & MESH_CLASS_MASK
            z_map.write(top, np.where(cls > 0, z, 0.0))
            class_map.write(top, cls)
            if family_map is not None:
                family_map.write(top, code >> MESH_FAMILY_SHIFT)
            covered += int(np.count_nonzero(cls))
            if progress and band % 16 == 0:
                print(
                    f"  {directory.name}: {bottom / size:5.1%}, {covered / 1e6:.2f} M texels, "
                    f"{time.time() - started:5.1f}s",
                    flush=True,
                )
    stats = {**stamp, "storage": storage, "texels": covered,
             "seconds": round(time.time() - started, 1)}  # fmt: skip
    (directory / MESH_CACHE_SIDECAR).write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def mesh_pass(cache: Path, size: int, build, reader: str, build_items, label: str, quiet: bool):
    """A mesh raster of ``size`` px in ``cache``, reused when its stamp matches.

    ``build_items`` returns ``(prepared, meta)`` for ``rasterise_meshes``. Returns the
    ``(z cm, class)`` maps, plus the family plane when the cache has one, and the sidecar's
    source block, keyed by ``reader``.
    """
    stamp = mesh_stamp(size, build, READER_VERSIONS[reader])
    maps = cached_meshes(cache, stamp)
    if maps is not None:
        print(f"reusing the {label} raster already in {cache}")
        recorded = json.loads((cache / MESH_CACHE_SIDECAR).read_text(encoding="utf-8"))
        return _with_family(cache, stamp, maps), {reader: {"reused": recorded}}
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    print(f"rasterising the {label} at {spacing_m:.4f} m")
    prepared, meta = build_items()
    print(f"  {label}: {meta.get('instances', meta.get('placements'))}")
    stats = rasterise_meshes(prepared, cache, stamp, BOUNDS_M, DIRECT_BAND_ROWS, not quiet)
    print(f"  {label} raster: {stats['texels'] / 1e6:.2f} M texels in {stats['seconds']}s")
    maps = _with_family(cache, stamp, cached_meshes(cache, stamp))
    return maps, {reader: {**meta, "raster": stats}}


def _with_family(cache: Path, stamp: dict, maps):
    family = None if maps is None else cached_mesh_family(cache, stamp)
    return maps if family is None else (*maps, family)

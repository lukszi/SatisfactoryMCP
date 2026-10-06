"""The render-only meshes and the Titan trees, rasterised for the renders run."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypeAlias

import numpy as np
from numpy.typing import NDArray

from mapgen.cache import (
    CACHE_SIDECAR_NAME,
    DIRECT_BAND_ROWS,
    MESH_PLANE_NAMES,
    STORAGE_BANDS,
    MeshMaps,
    MeshStamp,
    Plane,
    band_spans,
    cached_mesh_family,
    cached_meshes,
    mesh_stamp,
    rewrite_planes,
    write_sidecar,
)
from mapgen.common import Refusal
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.level.sweep import is_top_foliage
from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.gamedata.meshes import finer_source, read_hull
from mapgen.gamedata.placements import EXCLUDED_MESHES, placement_material, rotation_matrix
from mapgen.gamedata.rocks.families import FAMILIES, worn_family
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I64Grid, U8Grid, U16Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, PackageView, ScriptObjects
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "MESH_CLASS_MASK",
    "MESH_CLASS_NAMES",
    "MESH_CORAL",
    "MESH_FAMILY_SHIFT",
    "MESH_FOLIAGE_BATCH",
    "MESH_ROCK",
    "MESH_SHELL",
    "MESH_TERRACE",
    "RASTER_UNREADABLE",
    "RENDER_ONLY_DIRS",
    "RENDER_ONLY_FOLIAGE_MARKS",
    "RENDER_ONLY_FOLIAGE_MESHES",
    "RENDER_ONLY_MESHES",
    "TITAN_LEAVES",
    "TITAN_MARK",
    "TITAN_TRUNK",
    "InstanceSpans",
    "MeshGroup",
    "MeshRasterStats",
    "PreparedMeshes",
    "Shape",
    "instance_y_spans",
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

#: Instances transformed per batch.
MESH_FOLIAGE_BATCH = 512

#: The exit code of a run whose mesh raster cannot be read back after it was written.
RASTER_UNREADABLE = 7

#: A mesh's geometry in mesh-local cm: ``(verts, tris)``.
Shape: TypeAlias = tuple[F32Grid, I64Grid]


class InstanceSpans(NamedTuple):
    """Instances of one mesh: their 4x4 matrices, and the world Y interval each can reach."""

    mats: F32Grid
    y_lo_cm: NDArray[np.floating]
    y_hi_cm: NDArray[np.floating]


class MeshGroup(NamedTuple):
    """One mesh's instances: their source codes, 4x4 matrices and world Y intervals."""

    codes: U16Grid
    mats: F32Grid
    y_lo_cm: NDArray[np.floating]
    y_hi_cm: NDArray[np.floating]


@dataclass
class PreparedMeshes:
    """What ``rasterise_mesh_band`` draws: each mesh's instances and its shape, and whether
    the codes carry a rock family above the class (``MESH_FAMILY_SHIFT``)."""

    items: dict[str, MeshGroup]
    shapes: dict[str, Shape]
    families: bool = False


class MeshRasterStats(MeshStamp):
    """A mesh or Titan cache's sidecar: its stamp, and what the pass wrote."""

    storage: str
    texels: int
    seconds: float


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


def titan_class(mesh: str) -> int:
    name = mesh.rsplit("/", 1)[-1]
    if TITAN_MARK not in name:
        return 0
    return TITAN_LEAVES if "Leaves" in name else TITAN_TRUNK


def instance_y_spans(verts: F32Grid, mats: F32Grid) -> InstanceSpans:
    """``mats`` with each instance's world Y reach: the mesh's farthest vertex, scaled by the
    instance's largest axis, either side of its translation."""
    reach = float(np.linalg.norm(verts, axis=1).max())
    reach = reach * np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
    return InstanceSpans(mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)


def _placement_matrix(row: F64Grid) -> F64Grid:
    """A placement row as the 4x4 instance matrix foliage carries: rotation times scale, then
    the translation in the last row."""
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation_matrix(*row[5:8]) * row[8:11][:, None]
    matrix[3, :3] = row[2:5]
    return matrix


def read_shape(store: IoStore, scripts: ScriptObjects, index: AssetIndex,
               mesh: str) -> tuple[Shape | None, str]:  # fmt: skip
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


def titan_items(store: IoStore, scripts: ScriptObjects, index: AssetIndex,
                sweep: dict) -> tuple[PreparedMeshes, JsonObject]:  # fmt: skip
    """The Titan trees' placements in ``rasterise_mesh_band``'s format, at their finest mesh."""
    meshes = sweep["meshes"]
    groups: dict[str, list[F64Grid]] = {}
    for row in sweep["placements"]:
        mesh: str = meshes[int(row[0])]
        if titan_class(mesh):
            groups.setdefault(mesh, []).append(_placement_matrix(row))
    items: dict[str, MeshGroup] = {}
    shapes: dict[str, Shape] = {}
    sources: JsonObject = {}
    for mesh, mats in groups.items():
        shape, source = read_shape(store, scripts, index, mesh)
        sources[mesh.rsplit("/", 1)[-1]] = source
        if shape is None or not len(shape[1]):
            continue
        shapes[mesh] = shape
        codes = np.full(len(mats), titan_class(mesh), np.uint16)
        items[mesh] = MeshGroup(codes, *instance_y_spans(shape[0], np.asarray(mats, np.float32)))
    counts: JsonObject = {mesh.rsplit("/", 1)[-1]: len(group.mats) for mesh, group in items.items()}
    return PreparedMeshes(items, shapes), {"placements": counts, "sources": sources}


def _mesh_groups(store: IoStore, scripts: ScriptObjects, index: AssetIndex, sweep: dict,
                 shapes: Mapping[str, Shape]) -> dict[str, tuple[list[F64Grid], list[int]]]:  # fmt: skip
    """Per render-only mesh, its instances' matrices and source codes: the class, and for a
    rock the family its placement wears (``worn_family``), shifted above it."""
    meshes = sweep["meshes"]
    caches: tuple[dict[str, str | None], dict[str, int]] = ({}, {})
    groups: dict[str, tuple[list[F64Grid], list[int]]] = {}

    def add(mesh: str, mats: list[F64Grid], materials: list[str | None]) -> None:
        cls = mesh_class(mesh)
        mats_now, codes = groups.setdefault(mesh, ([], []))
        mats_now.extend(mats)
        for material in materials:
            rock = cls == MESH_ROCK
            family = worn_family(store, scripts, index, mesh, material, caches) if rock else 0
            codes.append(cls | family << MESH_FAMILY_SHIFT)

    for i, row in enumerate(sweep["placements"]):
        mesh: str = meshes[int(row[0])]
        if mesh not in shapes or not is_render_only_static(mesh):
            continue
        add(mesh, [_placement_matrix(row)], [placement_material(sweep, i)])
    for mesh, mats in sweep.get("extra_foliage", {}).items():
        if mesh in shapes:
            add(mesh, list(np.asarray(mats)), [None] * len(mats))
    return groups


def mesh_items(store: IoStore, scripts: ScriptObjects, index: AssetIndex,
               sweep: dict) -> tuple[PreparedMeshes, JsonObject]:  # fmt: skip
    """Every render-only placement and foliage instance, grouped by mesh with their codes."""
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
    shapes: dict[str, Shape] = {}
    sources: JsonObject = {}
    for mesh in wanted:
        shape, source = read_shape(store, scripts, index, mesh)
        sources[mesh.rsplit("/", 1)[-1]] = source
        if shape is not None and len(shape[1]):
            shapes[mesh] = shape
    items: dict[str, MeshGroup] = {}
    counts = {MESH_CLASS_NAMES[c]: 0 for c in MESH_CLASS_NAMES}
    families = dict.fromkeys(FAMILIES, 0)
    for mesh, (mats, codes) in _mesh_groups(store, scripts, index, sweep, shapes).items():
        group = MeshGroup(np.asarray(codes, np.uint16),
                          *instance_y_spans(shapes[mesh][0], np.asarray(mats, np.float32)))  # fmt: skip
        items[mesh] = group
        counts[MESH_CLASS_NAMES[mesh_class(mesh)]] += len(group.mats)
        if mesh_class(mesh) == MESH_ROCK:
            for code in group.codes >> MESH_FAMILY_SHIFT:
                families[FAMILIES[code]] += 1
    return PreparedMeshes(items, shapes, families=True), {
        "meshes": len(items),
        "instances": {**counts},
        "rock_families": {name: n for name, n in families.items() if n},
        "sources": sources,
        "seconds": round(time.time() - started, 1),
    }


def rasterise_mesh_band(prepared: PreparedMeshes, x0_cm: float, y0_cm: float, scale_cm: float,
                        rows: int, cols: int) -> tuple[F32Grid, U8Grid]:  # fmt: skip
    """One band: max-Z in world cm (nan where empty) and the source code of the winning mesh,
    its class or, per instance, its class and rock family (``MESH_FAMILY_SHIFT``)."""
    raster = MaxZRaster(cols, rows, x0_cm, y0_cm, scale_cm, sample=0.5)
    y_hi = y0_cm + rows * scale_cm
    for mesh, group in prepared.items.items():
        near = (group.y_hi_cm >= y0_cm) & (group.y_lo_cm <= y_hi)
        if not near.any():
            continue
        verts, tris = prepared.shapes[mesh]
        codes, nearby = group.codes[near], group.mats[near]
        for value in np.unique(codes):
            picked = nearby[codes == value]
            for start in range(0, len(picked), MESH_FOLIAGE_BATCH):
                chunk = picked[start : start + MESH_FOLIAGE_BATCH]
                world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
                raster.add(world[:, tris].reshape(-1, 3, 3), int(value))
    z, src, _density = raster.result()
    return z, np.where(np.isfinite(z), src, 0).astype(np.uint8)


def rasterise_meshes(prepared: PreparedMeshes, directory: Path, stamp: MeshStamp,
                     bounds_m: Mapping[str, float], band_rows: int,
                     progress: bool) -> MeshRasterStats:  # fmt: skip
    """Rasterise the render-only meshes into the render's grid, banded, onto disk: the class
    plane, and the family plane when the items carry families."""
    size = stamp["size"]
    step_cm = (bounds_m["x_max_m"] - bounds_m["x_min_m"]) * 100 / size
    covered, started = 0, time.time()
    names = MESH_PLANE_NAMES if prepared.families else MESH_PLANE_NAMES[:2]
    with rewrite_planes(directory, names, size, band_rows, clear=MESH_PLANE_NAMES) as planes:
        for band, (top, bottom) in enumerate(band_spans(size, band_rows)):
            y0 = bounds_m["y_min_m"] * 100 + top * step_cm
            z, code = rasterise_mesh_band(
                prepared, bounds_m["x_min_m"] * 100, y0, step_cm, bottom - top, size
            )
            cls = code & MESH_CLASS_MASK
            planes[0].write(top, np.where(cls > 0, z, 0.0))
            planes[1].write(top, cls)
            if prepared.families:
                planes[2].write(top, code >> MESH_FAMILY_SHIFT)
            covered += int(np.count_nonzero(cls))
            if progress and band % 16 == 0:
                print(
                    f"  {directory.name}: {bottom / size:5.1%}, {covered / 1e6:.2f} M texels, "
                    f"{time.time() - started:5.1f}s",
                    flush=True,
                )
    stats: MeshRasterStats = {**stamp, "storage": STORAGE_BANDS, "texels": covered,
                              "seconds": round(time.time() - started, 1)}  # fmt: skip
    write_sidecar(directory / CACHE_SIDECAR_NAME, stats)
    return stats


def mesh_pass(cache: Path, size: int, build: str | None, reader: str,
              build_items: Callable[[], tuple[PreparedMeshes, JsonObject]], label: str,
              quiet: bool) -> tuple[MeshMaps, dict[str, object]]:  # fmt: skip
    """A mesh raster of ``size`` px in ``cache``, reused when its stamp matches.

    ``build_items`` returns ``(prepared, meta)`` for ``rasterise_meshes``. Returns the
    ``(z cm, class)`` maps, plus the family plane when the cache has one, and the sidecar's
    source block, keyed by ``reader``. A raster that does not read back is a refusal.
    """
    stamp = mesh_stamp(size, build, READER_VERSIONS[reader])
    maps = cached_meshes(cache, stamp)
    if maps is not None:
        print(f"reusing the {label} raster already in {cache}")
        recorded = json.loads((cache / CACHE_SIDECAR_NAME).read_text(encoding="utf-8"))
        return _with_family(cache, stamp, maps), {reader: {"reused": recorded}}
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    print(f"rasterising the {label} at {spacing_m:.4f} m")
    prepared, meta = build_items()
    print(f"  {label}: {meta.get('instances', meta.get('placements'))}")
    stats = rasterise_meshes(prepared, cache, stamp, BOUNDS_M, DIRECT_BAND_ROWS, not quiet)
    print(f"  {label} raster: {stats['texels'] / 1e6:.2f} M texels in {stats['seconds']}s")
    maps = cached_meshes(cache, stamp)
    if maps is None:
        raise Refusal(RASTER_UNREADABLE,
                      f"the {label} raster in {cache} could not be read back after writing it")  # fmt: skip
    return _with_family(cache, stamp, maps), {reader: {**meta, "raster": stats}}


def _with_family(cache: Path, stamp: MeshStamp, maps: tuple[Plane, Plane]) -> MeshMaps:
    family = cached_mesh_family(cache, stamp)
    return maps if family is None else (*maps, family)

"""The field's cliff and top layers: rock meshes folded max-Z onto the 1 m grid."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.maxz_raster import INSTANCE_BATCH, MaxZRaster
from mapgen.gamedata.meshes import CookedMesh, clamp_triangles, read_hull, winding_sign
from mapgen.gamedata.placements import (
    EXCLUDED_OWNERS,
    PLACEMENT_MESH,
    PLACEMENT_OWNER,
    arch_mesh_ids,
    cliff_cull,
    mesh_name,
    placement_transform,
)
from satisfactory_mcp.core.arrays import F32Grid, I64Grid, U32Grid

if TYPE_CHECKING:
    from mapgen.gamedata.level.landscape import LandscapeFrame
    from mapgen.gamedata.level.sweep import Sweep

__all__ = [
    "CliffRaster",
    "TopOverlay",
    "rasterise_cliffs",
    "rasterise_top",
]


class CliffRaster(TypedDict):
    """What ``rasterise_cliffs`` returns: the overlay and the counts the sidecar records."""

    z_cm: F32Grid
    density: U32Grid
    placements_total: int
    placements_used: int
    dropped: dict[str, int]
    arch_meshes: int
    triangles: int
    samples: int
    triangles_out_of_bounds: int
    seconds: float


class TopOverlay(TypedDict):
    """What ``rasterise_top`` returns: the overlay and the counts the sidecar records."""

    z_cm: F32Grid
    arch_placements: int
    foliage_instances: int
    foliage_by_mesh: dict[str, int]
    meshes_without_trimesh: list[str]
    triangles: int
    seconds: float


def _frame_raster(frame: LandscapeFrame) -> MaxZRaster:
    return MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )


def rasterise_cliffs(
    sweep: Sweep, geometry: dict[str, CookedMesh], frame: LandscapeFrame, progress: bool = True
) -> CliffRaster:
    """Transform, cull and rasterise every placed rock into a 1 m max-Z overlay, in cm.

    Culling, in the order it costs least: ``cliff_cull``'s placement culls, then the
    downward-facing half of the triangles, then the triangles outside the mesh's own padded
    bounds. That last cull is per triangle rather than per mesh, because the defect it
    removes is one stray vertex in an otherwise good mesh.
    """
    placements = sweep["placements"]
    meshes, owners = sweep["meshes"], sweep["owners"]
    windings = {m: winding_sign(cooked.verts, cooked.tris) for m, cooked in geometry.items()}
    raster = _frame_raster(frame)
    arch_ids = arch_mesh_ids(meshes)
    dropped = {"owner": 0, "excluded_mesh": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    used = triangles = samples = clamped = 0
    started = time.time()
    for count, row in enumerate(placements):
        culled = cliff_cull(row, meshes, owners, geometry, arch_ids)
        if culled is not None:
            dropped[culled] += 1
            continue
        mesh_id = int(row[PLACEMENT_MESH])
        mesh = meshes[mesh_id]
        verts, tris, low, high = geometry[mesh]
        matrix, scale, offset = placement_transform(row, np.float32)
        # The bounds clamp is applied in LOCAL space, where the mesh's own ExtendedBounds
        # live, so it costs one comparison per vertex instead of a transformed box per
        # placement -- and it is the same box for all 200 copies of a rock.
        tris, out_of_bounds = clamp_triangles(verts, tris, low, high)
        clamped += out_of_bounds
        if tris.size == 0:
            continue
        world = (verts * scale) @ matrix + offset
        tris = _up_facing(world, tris, windings[mesh] * np.sign(scale[0] * scale[1] * scale[2]))
        if tris.shape[0]:
            raster.add(world[tris], mesh_id + 1)
            triangles += tris.shape[0]
            # The density plane counts the vertices of the triangles that SURVIVED the
            # facing cull: a downward-facing vertex is not a sample of this surface.
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
    z_cm, _source, density = raster.result()
    placed_ids = {int(i) for i in np.unique(placements[:, PLACEMENT_MESH])}
    return {
        "z_cm": z_cm,
        "density": density,
        "placements_total": len(placements),
        "placements_used": used,
        "dropped": dropped,
        "arch_meshes": len(arch_ids & placed_ids),
        "triangles": int(triangles),
        "samples": int(samples),
        "triangles_out_of_bounds": clamped,
        "seconds": time.time() - started,
    }


def _up_facing(world: NDArray[np.floating], tris: I64Grid, facing: float) -> I64Grid:
    """The triangles whose world normal points the way ``facing`` says is up; all on 0."""
    if facing == 0:
        return tris
    corner = world[tris[:, 0]]
    normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
    return tris[(normals[:, 2] * facing) > 0]


def rasterise_top(
    sweep: Sweep, frame: LandscapeFrame, reader: GameReader, progress: bool = True
) -> TopOverlay:
    """Arches and foliage boulders as a 1 m max-Z overlay, from their collision trimeshes.

    These are what the ground deliberately leaves out: an arch is a roof over buildable
    ground, and a boulder is painted foliage the placement sweep never sees.
    """
    started = time.time()
    raster = _frame_raster(frame)
    placements, meshes, owners = sweep["placements"], sweep["meshes"], sweep["owners"]
    arch_ids = arch_mesh_ids(meshes)
    hulls: dict[str, tuple[F32Grid, I64Grid] | None] = {}
    arches = boulders = triangles = 0
    missing: set[str] = set()

    def hull(mesh: str) -> tuple[F32Grid, I64Grid] | None:
        if mesh not in hulls:
            hulls[mesh] = read_hull(reader.store, reader.scripts, reader.index, mesh)
        if hulls[mesh] is None:
            missing.add(mesh)
        return hulls[mesh]

    for row in placements:
        mesh_id = int(row[PLACEMENT_MESH])
        if mesh_id not in arch_ids or owners[int(row[PLACEMENT_OWNER])] in EXCLUDED_OWNERS:
            continue
        found = hull(meshes[mesh_id])
        if found is None:
            continue
        verts, tris = found
        matrix, scale, offset = placement_transform(row, np.float32)
        world = (verts * scale) @ matrix + offset
        raster.add(world[tris], 1)
        arches += 1
        triangles += tris.shape[0]
    for mesh, mats in sweep["foliage"].items():
        found = hull(mesh)
        if found is None:
            continue
        verts, tris = found
        for start in range(0, len(mats), INSTANCE_BATCH):
            chunk = mats[start : start + INSTANCE_BATCH].astype(np.float32)
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 2)
        boulders += len(mats)
        triangles += tris.shape[0] * len(mats)
    z_cm, _source, _density = raster.result()
    if progress:
        print(f"  top overlay: {arches} arches, {boulders} boulders, {time.time() - started:.0f}s")
    return {
        "z_cm": z_cm,
        "arch_placements": arches,
        "foliage_instances": boulders,
        "foliage_by_mesh": {mesh_name(m): len(v) for m, v in sweep["foliage"].items()},
        "meshes_without_trimesh": sorted(mesh_name(m) for m in missing),
        "triangles": int(triangles),
        "seconds": time.time() - started,
    }

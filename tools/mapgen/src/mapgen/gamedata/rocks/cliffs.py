"""The field's cliff and top layers: rock meshes folded max-Z onto the 1 m grid."""

from __future__ import annotations

import time

import numpy as np

from mapgen.gamedata.maxz_raster import MaxZRaster
from mapgen.gamedata.meshes import read_hull, winding_sign
from mapgen.gamedata.placements import (
    ARCH_MARK,
    EXCLUDED_MESHES,
    EXCLUDED_OWNERS,
    OVERSIZE_CM,
    rotation_matrix,
)

__all__ = [
    "rasterise_cliffs",
    "rasterise_top",
]


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

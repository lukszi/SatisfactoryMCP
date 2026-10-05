"""Recipe 6's two additions to the renders: the ocean's crisp shore and the render-only meshes.

The shore: near the sea, water coverage comes from the drawn surface crossing the ocean level,
antialiased to one pixel, instead of from the 3.66 m artwork mask. Rivers and lakes keep
recipe 5's rule. The meshes: coral, shells, CliffPillar_03 and rubble, which the artwork
draws as land and the heightfield leaves out, rasterised for the map only. docs/
spatial-and-map.md section 27 has the measurements behind every constant here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView
from satisfactory_mcp.domain.spatial import heightfield as hf
from tools import gen_world_heightmap as gen

#: The sea surface the coast is drawn at, metres. The one number to change if the sandbars
#: turn out dry in game (-17.4 is what the artwork, the paint and the foliage agree on).
OCEAN_LEVEL_M = -17.0

#: Measured water counts as ocean when its level is this close to ``OCEAN_LEVEL_M``.
OCEAN_LEVEL_BAND_M = 0.5

#: How far past measured ocean water the crossing rule reaches, metres.
OCEAN_REACH_M = 48.0

#: A render-only mesh is drawn only where its top stands within this of the water surface or
#: above it, so seabed coral roots do not speckle the sea.
MESH_REACH_M = 0.6

#: The lift over which a pixel counts as wholly covered by a render-only mesh, metres.
MESH_FULL_LIFT_M = 0.25

#: Which meshes are drawn on the map and never enter the heightfield.
RENDER_ONLY_DIRS = ("/World/Environment/Foliage/Coral/", "/World/Environment/UnderWater/")
RENDER_ONLY_FOLIAGE_MARKS = ("/Rubble/", "SeaRock")
RENDER_ONLY_MESHES = gen.EXCLUDED_MESHES

#: Mesh classes, as stored in the cache's class plane. 0 is nothing.
MESH_CORAL, MESH_SHELL, MESH_ROCK = 1, 2, 3
MESH_CLASS_NAMES = {MESH_CORAL: "coral", MESH_SHELL: "shell", MESH_ROCK: "rock"}

MESH_CACHE_DIR_NAME = "meshes.cache"
MESH_Z_NAME = "meshes.z.f32"
MESH_CLASS_NAME = "meshes.class.u8"
MESH_CACHE_SIDECAR = "meta.json"
MESH_FOLIAGE_BATCH = 512


# ----------------------------------------------------------------------- the shore


def ocean_reach(field) -> tuple[np.ndarray, dict]:
    """1 where the crossing rule applies: within the reach of measured ocean water."""
    water = field._water_raster()
    grades = field._water_quality_raster()
    if water is None or grades is None:
        return np.zeros((field.height, field.width), np.uint8), {"absent": "no water planes"}
    level_m = water.astype(np.float32) / np.float32(hf.DM_PER_M)
    ocean = (
        (grades == hf.WATER_MEASURED)
        & (water != hf.NODATA)
        & (np.abs(level_m - OCEAN_LEVEL_M) <= OCEAN_LEVEL_BAND_M)
    )
    distance = ndimage.distance_transform_edt(~ocean) * (field.spacing_cm / 100.0)
    reach = (distance <= OCEAN_REACH_M).astype(np.uint8)
    return reach, {
        "ocean_texels": int(ocean.sum()),
        "reach_texels": int(reach.sum()),
        "level_m": OCEAN_LEVEL_M,
        "level_band_m": OCEAN_LEVEL_BAND_M,
        "reach_m": OCEAN_REACH_M,
    }


def shore_terms(z_m: np.ndarray, spacing_m: float) -> dict:
    """The crossing of the ocean level through each pixel: coverage, depth, the edge, and how
    far above the waterline a dry pixel is, in metres across the ground."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    grade = np.maximum(np.hypot(d_east, d_south), np.float32(1e-3))
    per_px = np.maximum(grade * spacing_m, np.float32(1e-3))
    depth = np.float32(OCEAN_LEVEL_M) - z_m
    return {
        "cover": np.clip(depth / per_px + 0.5, 0.0, 1.0),
        "depth_m": np.maximum(depth, 0.0),
        "edge": np.clip(1.0 - np.abs(depth) / per_px, 0.0, 1.0),
        "above_m": np.maximum(-depth, 0.0) / grade,
        "below_m": np.maximum(depth, 0.0) / grade,
    }


def blend_water(reach, old_cover, old_depth_fraction, shore: dict | None, full_m: float) -> dict:
    """Recipe 6's water where ``reach`` is 1 and recipe 5's where it is 0, blended between.

    Returns ``cover`` (water share of the pixel), ``depth`` (the tint fraction),
    ``depth_m`` (metres, for the optics), ``ocean`` (the reach) and ``edge`` (where the
    crossing passes through the pixel).
    """
    if shore is None:
        zero = np.zeros_like(old_cover)
        return {
            "cover": old_cover,
            "depth": old_depth_fraction,
            "ocean": zero,
            "edge": zero,
            "above_m": np.full_like(old_cover, np.inf),
            "below_m": np.full_like(old_cover, np.inf),
            "depth_m": old_depth_fraction * np.float32(full_m),
        }
    keep = 1.0 - reach
    return {
        "cover": reach * shore["cover"] + keep * old_cover,
        "depth": reach * np.clip(shore["depth_m"] / full_m, 0.0, 1.0) + keep * old_depth_fraction,
        "depth_m": reach * shore["depth_m"] + keep * old_depth_fraction * np.float32(full_m),
        "ocean": reach,
        "edge": reach * shore["edge"],
        "above_m": np.where(reach > 0, shore["above_m"], np.inf),
        "below_m": np.where(reach > 0, shore["below_m"], np.inf),
    }


def water_composite(
    land, water: dict, shade, optics: dict, shallow, deep, shade_floor, shade_range
):
    """Ground and water in one pass, sRGB 0..255. Outside the ocean reach this is recipe 5.

    Over the sea the water's opacity rises from ``edge_alpha`` at the line to one with an
    exponential depth fade of e-folding ``clarity_m``, over ground darkened by ``wet_darken``.
    Optional, per style: ``wet_band`` darkens the sand just above the line and ``foam`` lays a
    faint line over the shallowest water.
    """
    land = wet_band(land, water, optics.get("wet_band"))
    ocean = water["ocean"]
    fade = 1.0 - np.exp(-water["depth_m"] / np.float32(optics["clarity_m"]))
    a0 = np.float32(optics["edge_alpha"])
    opacity = (ocean * (a0 + (1.0 - a0) * fade) + (1.0 - ocean))[..., None]
    wet = (1.0 - (1.0 - np.float32(optics["wet_darken"])) * ocean)[..., None]
    tint = water["depth"][..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (shade_floor + shade_range * shade[..., None])
    under = land * wet * (1.0 - opacity) + colour * opacity
    cover = water["cover"][..., None]
    rgb = land * (1.0 - cover) + under * cover
    stroke = np.float32(optics.get("stroke", 0.0))
    if stroke:
        rgb = rgb * (1.0 - stroke * water["edge"][..., None])
    return add_foam(rgb, water, optics.get("foam"), np.float32(255.0))


def wet_band(land, water: dict, band: dict | None):
    """Ground within ``band["m"]`` of the waterline, multiplied towards ``band["tint"]``."""
    if not band or not band.get("m"):
        return land
    reach = np.clip(1.0 - water["above_m"] / np.float32(band["m"]), 0.0, 1.0)
    weight = (reach * reach * water["ocean"])[..., None]
    return land * (1.0 - weight + weight * np.asarray(band["tint"], np.float32))


def add_foam(rgb, water: dict, foam: dict | None, white):
    """A faint line along the waterline, towards ``white``: water shallower than
    ``max_depth_m`` and within ``width_m`` of the line across the ground."""
    if not foam or not foam.get("strength"):
        return rgb
    shallow = np.clip(1.0 - water["depth_m"] / np.float32(foam["max_depth_m"]), 0.0, 1.0)
    shallow = shallow * np.clip(1.0 - water["below_m"] / np.float32(foam["width_m"]), 0.0, 1.0)
    weight = (np.float32(foam["strength"]) * shallow * water["cover"] * water["ocean"])[..., None]
    return rgb * (1.0 - weight) + white * np.float32(foam.get("white", 1.0)) * weight


# ----------------------------------------------------------------------- render-only meshes


def is_render_only_static(mesh: str) -> bool:
    return any(d in mesh for d in RENDER_ONLY_DIRS) or mesh.rsplit("/", 1)[-1] in RENDER_ONLY_MESHES


def is_render_only_foliage(mesh: str) -> bool:
    if gen.is_top_foliage(mesh):
        return False
    return any(d in mesh for d in RENDER_ONLY_DIRS) or any(
        m in mesh for m in RENDER_ONLY_FOLIAGE_MARKS
    )


def mesh_class(mesh: str) -> int:
    name = mesh.rsplit("/", 1)[-1]
    if "Shell" in name:
        return MESH_SHELL
    if any(mark in name for mark in ("CliffPillar", "Rubble", "RockPile", "SeaRock")):
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
        found = gen.finer_source(store, package, view, export, *bounds) if bounds else None
    except Exception:  # a mesh this reader cannot open is one mesh fewer on the map
        found = None
    if found is not None:
        source, (verts, tris) = found
        return (np.asarray(verts, np.float32), np.asarray(tris, np.int64)), source
    hull = gen.read_hull(store, scripts, index, mesh)
    return hull, "hull" if hull is not None else "none"


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
        matrix[:3, :3] = gen.rotation_matrix(*row[5:8]) * row[8:11][:, None]
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
    raster = gen.MaxZRaster(cols, rows, x0_cm, y0_cm, scale_cm, sample=0.5)
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


def mesh_stamp(size: int, build: str | None, reader_version: int) -> dict:
    return {"size": int(size), "game_version_pinned": build, "reader_version": int(reader_version)}


def cached_meshes(directory: Path, stamp: dict):
    """``(z cm, class)`` memory maps if the cache is this one, else ``None``."""
    try:
        recorded = json.loads((directory / MESH_CACHE_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    size = stamp["size"]
    try:
        return (
            np.memmap(directory / MESH_Z_NAME, np.float32, "r", shape=(size, size)),
            np.memmap(directory / MESH_CLASS_NAME, np.uint8, "r", shape=(size, size)),
        )
    except (OSError, ValueError):
        return None


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


def composite_meshes(z_m, mesh_z_cm, mesh_class_band, water_level_m, composite):
    """``z_m`` raised by the meshes standing near or above the water; and their weight.

    ``composite`` is the renderer's raise-only lift (``composite_top``). The weight is how
    much of the drawn surface is the mesh: 0 where it did not raise the ground.
    """
    level = np.where(np.isfinite(water_level_m), water_level_m, -np.inf)
    keep = (mesh_class_band > 0) & (mesh_z_cm / np.float32(100.0) > level - MESH_REACH_M)
    raised = composite(z_m, mesh_z_cm, keep.astype(np.uint8))
    weight = np.clip((raised - z_m) / np.float32(MESH_FULL_LIFT_M), 0.0, 1.0)
    return raised, weight, np.where(keep, mesh_class_band, 0).astype(np.uint8)

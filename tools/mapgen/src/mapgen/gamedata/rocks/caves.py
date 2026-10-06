"""The cave masks (``--caves``)."""

from __future__ import annotations

import math
import struct
import time

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM, sample_grid
from mapgen.gamedata.level.sweep import (
    FOLIAGE_CLASSES,
    LEVEL_DIR,
    LEVEL_SUFFIX,
    flagged_tags,
    foliage_instances,
)
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    class_name_of,
    quat_rotate,
    root_component,
    world_transform,
)
from satisfactory_mcp.domain.spatial import caves

__all__ = [
    "CAVE_BUFFER_CELLS",
    "CAVE_CELL_CM",
    "CAVE_FLOOR_CLASS",
    "CAVE_MARKER_DIRS",
    "CAVE_MARKER_EXCLUDED",
    "CAVE_MASK_PX",
    "CAVE_SOUND_MARK",
    "build_caves",
    "cave_volume_hulls",
    "convex_elems",
    "is_cave_marker",
    "sweep_caves",
]


# --------------------------------------------------------------------------------------
# The cave masks (``--caves``): their own directory, read beside any field version.
# --------------------------------------------------------------------------------------

#: Foliage under these mesh trees is cave decoration. ``SM_NonCave_*`` stalactites hang
#: under open-air overhangs, a median 12 m below the surface, and would flag cliffs.
CAVE_MARKER_DIRS = ("/Caves/", "/CaveFloor/")
CAVE_MARKER_EXCLUDED = "SM_NonCave_"

#: An ``FGAmbientVolume`` whose ``mAmbientSettings`` basename contains this is a cave.
CAVE_SOUND_MARK = "cave"

CAVE_CELL_CM = 800.0
CAVE_BUFFER_CELLS = 2
CAVE_MASK_PX = int(GRID_PX * SPACING_CM / CAVE_CELL_CM) + 2


def is_cave_marker(mesh: str) -> bool:
    return any(d in mesh for d in CAVE_MARKER_DIRS) and CAVE_MARKER_EXCLUDED not in mesh


def convex_elems(payload: bytes, names: list[str]) -> list[np.ndarray]:
    """``AggGeom.ConvexElems[*].VertexData`` as local (n, 3) float64 arrays."""
    agg, _ = flagged_tags(payload, names, pos=0)
    raw = agg.get("ConvexElems")
    if not raw or len(raw) < 4:
        return []
    out = []
    pos = 4
    for _ in range(struct.unpack_from("<I", raw, 0)[0]):
        element, pos = flagged_tags(raw, names, pos=pos)
        data = element.get("VertexData", b"")
        count = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        if count and len(data) == 4 + 24 * count:
            out.append(np.frombuffer(data, "<f8", 3 * count, 4).reshape(-1, 3))
    return out


def cave_volume_hulls(view, slot: int, classes) -> list[np.ndarray] | None:
    """A cave ambient volume's brush hulls in world cm, or ``None`` if it is not a cave."""
    settings = view.props(slot).get("mAmbientSettings")
    path = view.import_path(settings) if settings else None
    if not path or CAVE_SOUND_MARK not in path.rsplit("/", 1)[-1].lower():
        return None
    root = root_component(view, slot)
    if root is None:
        return []
    brush = view.props(root).get("BrushBodySetup")
    body_setup = view.export_ref(brush) if brush else None
    transform = world_transform(view, root, classes)[0]
    if body_setup is None or transform is None:
        return []
    tags, _ = flagged_tags(view.pkg.body(view.exports[body_setup]), view.pkg.names)
    if "AggGeom" not in tags:
        return []
    location, quat, scale = transform
    rotation = np.stack([np.array(quat_rotate(quat, tuple(axis))) for axis in np.eye(3)])
    return [
        (local * np.array(scale)) @ rotation + np.array(location)
        for local in convex_elems(tags["AggGeom"], view.pkg.names)
    ]


def sweep_caves(store, scripts, classes, progress: bool = True) -> dict:
    """Cave sound-volume hulls and cave-decoration positions, in one pass over the world."""
    hulls: list[np.ndarray] = []
    markers: list[np.ndarray] = []
    volumes = empty = 0
    started = time.time()
    paths = level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    for index, total, _path, view in walk_levels(store, scripts, paths=paths):
        for slot, class_path in view.class_of.items():
            name = class_name_of(class_path)
            if name == "FGAmbientVolume":
                found = cave_volume_hulls(view, slot, classes)
                if found is None:
                    continue
                volumes += 1
                empty += not found
                hulls += found
            elif name in FOLIAGE_CLASSES:
                instances = foliage_instances(view, slot, classes, wanted=is_cave_marker)
                if instances is not None:
                    markers.append(instances[1][:, 3, :3].copy())
        if progress and index % 1000 == 0:
            print(f"  {index}/{total} packages, {time.time() - started:.0f}s", flush=True)
    return {
        "hulls": hulls,
        "volumes": volumes,
        "volumes_without_hull": empty,
        "markers": np.concatenate(markers) if markers else np.zeros((0, 3)),
        "seconds": time.time() - started,
    }


def _cells(x_cm: np.ndarray, y_cm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    col = np.floor((x_cm - ORIGIN_X_CM) / CAVE_CELL_CM).astype(int)
    row = np.floor((y_cm - ORIGIN_Y_CM) / CAVE_CELL_CM).astype(int)
    return row, col


def build_caves(found: dict, height_dm: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    """The mask and hull arrays for ``caves.npz``, plus the counts the sidecar records."""
    from scipy.spatial import ConvexHull, QhullError

    mask = np.zeros((CAVE_MASK_PX, CAVE_MASK_PX), np.uint8)
    marks = found["markers"]
    ground = sample_grid(height_dm, marks[:, 0], marks[:, 1])
    under = marks[:, 2] / 100.0 < ground - caves.INSIDE_DEPTH_M
    row, col = _cells(marks[under, 0], marks[under, 1])
    on = (row >= 0) & (row < CAVE_MASK_PX) & (col >= 0) & (col < CAVE_MASK_PX)
    hit = np.zeros(mask.shape, bool)
    hit[row[on], col[on]] = True
    hit = ndimage.binary_dilation(hit, iterations=CAVE_BUFFER_CELLS)
    mask[hit] |= caves.BIT_MARKERS

    planes, starts, boxes = [], [0], []
    degenerate = 0
    centres = (np.arange(CAVE_MASK_PX) + 0.5) * CAVE_CELL_CM
    for verts in found["hulls"]:
        try:
            solid = ConvexHull(verts)
            flat = ConvexHull(verts[:, :2])
        except (QhullError, ValueError):
            degenerate += 1
            continue
        planes.append(solid.equations)
        starts.append(starts[-1] + len(solid.equations))
        boxes.append(np.r_[verts.min(0), verts.max(0)])
        r0, c0 = _cells(verts[:, 0].min(keepdims=True), verts[:, 1].min(keepdims=True))
        r1, c1 = _cells(verts[:, 0].max(keepdims=True), verts[:, 1].max(keepdims=True))
        r0, c0 = max(int(r0[0]), 0), max(int(c0[0]), 0)
        r1, c1 = min(int(r1[0]), CAVE_MASK_PX - 1), min(int(c1[0]), CAVE_MASK_PX - 1)
        if r1 < r0 or c1 < c0:
            continue
        xs = ORIGIN_X_CM + centres[c0 : c1 + 1]
        ys = ORIGIN_Y_CM + centres[r0 : r1 + 1]
        gx, gy = np.meshgrid(xs, ys)
        # Every cell the plan touches, so the reader may skip the 3D test where the bit is off.
        eq = flat.equations
        reach = CAVE_CELL_CM * math.sqrt(0.5)
        touch = gx[..., None] * eq[:, 0] + gy[..., None] * eq[:, 1] + eq[:, 2] <= reach
        mask[r0 : r1 + 1, c0 : c1 + 1][touch.all(-1)] |= caves.BIT_HULL

    arrays = {
        "mask": mask,
        "planes": np.concatenate(planes) if planes else np.zeros((0, 4)),
        "starts": np.array(starts, np.int64),
        "boxes": np.array(boxes, np.float64).reshape(-1, 6),
    }
    cell_km2 = (CAVE_CELL_CM / 1e5) ** 2
    counts = {
        "volumes": found["volumes"],
        "volumes_without_hull": found["volumes_without_hull"],
        "hulls": len(boxes),
        "hulls_degenerate": degenerate,
        "markers": len(marks),
        "markers_under_ground": int(under.sum()),
        "marker_km2": round(float(((mask & caves.BIT_MARKERS) != 0).sum()) * cell_km2, 2),
        "hull_km2": round(float(((mask & caves.BIT_HULL) != 0).sum()) * cell_km2, 2),
        "flagged_km2": round(float((mask != 0).sum()) * cell_km2, 2),
    }
    return arrays, counts


#: A cave floor is a spline actor whose components each carry their own cooked trimesh.
CAVE_FLOOR_CLASS = "BP_CaveFloor_C"

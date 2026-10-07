"""The cave masks (``--caves``): their own directory, read beside any field version."""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from typing import TypedDict

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM, sample_grid
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.level.sweep import (
    FOLIAGE_CLASSES,
    flagged_tags,
    foliage_instances,
    quat_axes,
    world_levels,
)
from satisfactory_mcp.core.arrays import BoolMask, F64Grid, I16Grid, I64Grid, U8Grid
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    class_name_of,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial.heightfield import cave_masks as caves

__all__ = [
    "CAVE_BUFFER_CELLS",
    "CAVE_CELL_CM",
    "CAVE_MARKER_DIRS",
    "CAVE_MARKER_EXCLUDED",
    "CAVE_MASK_PX",
    "CAVE_SOUND_MARK",
    "CaveArrays",
    "CaveSweep",
    "build_caves",
    "cave_volume_hulls",
    "convex_elems",
    "is_cave_marker",
    "sweep_caves",
]


#: Foliage under these mesh trees is cave decoration. ``SM_NonCave_*`` stalactites hang
#: under open-air overhangs, a median 12 m below the surface, and would flag cliffs.
CAVE_MARKER_DIRS = ("/Caves/", "/CaveFloor/")
CAVE_MARKER_EXCLUDED = "SM_NonCave_"

#: An ``FGAmbientVolume`` whose ``mAmbientSettings`` basename contains this is a cave.
CAVE_SOUND_MARK = "cave"

CAVE_CELL_CM = 800.0
CAVE_BUFFER_CELLS = 2
CAVE_MASK_PX = int(GRID_PX * SPACING_CM / CAVE_CELL_CM) + 2


class CaveSweep(TypedDict):
    """``sweep_caves``' two signals: sound-volume hulls and cave-decoration positions."""

    hulls: list[F64Grid]
    volumes: int
    volumes_without_hull: int
    markers: F64Grid
    seconds: float


class CaveArrays(TypedDict):
    """``caves.npz``: the cell mask, and every cave hull's planes and box."""

    mask: U8Grid
    planes: F64Grid
    starts: I64Grid
    boxes: F64Grid


@dataclass
class _HullBits:
    planes: list[F64Grid] = field(default_factory=list)
    starts: list[int] = field(default_factory=lambda: [0])
    boxes: list[F64Grid] = field(default_factory=list)
    degenerate: int = 0


def is_cave_marker(mesh: str) -> bool:
    return any(d in mesh for d in CAVE_MARKER_DIRS) and CAVE_MARKER_EXCLUDED not in mesh


def convex_elems(payload: bytes, names: list[str]) -> list[F64Grid]:
    """``AggGeom.ConvexElems[*].VertexData`` as local (n, 3) float64 arrays."""
    agg, _ = flagged_tags(payload, names, pos=0)
    raw = agg.get("ConvexElems")
    if not raw or len(raw) < 4:
        return []
    out: list[F64Grid] = []
    pos = 4
    for _ in range(struct.unpack_from("<I", raw, 0)[0]):
        element, pos = flagged_tags(raw, names, pos=pos)
        data = element.get("VertexData", b"")
        count = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        if count and len(data) == 4 + 24 * count:
            out.append(np.frombuffer(data, "<f8", 3 * count, 4).reshape(-1, 3))
    return out


def cave_volume_hulls(view: PackageView, slot: int, classes: ClassFacts) -> list[F64Grid] | None:
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
    rotation = quat_axes(quat)
    return [
        (local * np.array(scale)) @ rotation + np.array(location)
        for local in convex_elems(tags["AggGeom"], view.pkg.names)
    ]


def sweep_caves(reader: GameReader, progress: bool = True) -> CaveSweep:
    """Cave sound-volume hulls and cave-decoration positions, in one pass over the world."""
    hulls: list[F64Grid] = []
    markers: list[F64Grid] = []
    volumes = empty = 0
    started = time.time()
    for index, total, _path, view in world_levels(reader.store, reader.scripts):
        for slot, class_path in view.class_of.items():
            name = class_name_of(class_path)
            if name == "FGAmbientVolume":
                found = cave_volume_hulls(view, slot, reader.classes)
                if found is None:
                    continue
                volumes += 1
                empty += not found
                hulls += found
            elif name in FOLIAGE_CLASSES:
                instances = foliage_instances(view, slot, reader.classes, wanted=is_cave_marker)
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


def _cells(x_cm: F64Grid, y_cm: F64Grid) -> tuple[I64Grid, I64Grid]:
    col = np.floor((x_cm - ORIGIN_X_CM) / CAVE_CELL_CM).astype(np.int64)
    row = np.floor((y_cm - ORIGIN_Y_CM) / CAVE_CELL_CM).astype(np.int64)
    return row, col


def _marker_bits(mask: U8Grid, marks: F64Grid, height_dm: I16Grid) -> BoolMask:
    """Set ``BIT_MARKERS`` around every marker under the ground; which markers those were."""
    ground = sample_grid(height_dm, marks[:, 0], marks[:, 1])
    under = marks[:, 2] / 100.0 < ground - caves.INSIDE_DEPTH_M
    row, col = _cells(marks[under, 0], marks[under, 1])
    on = (row >= 0) & (row < CAVE_MASK_PX) & (col >= 0) & (col < CAVE_MASK_PX)
    hit = np.zeros(mask.shape, bool)
    hit[row[on], col[on]] = True
    hit = ndimage.binary_dilation(hit, iterations=CAVE_BUFFER_CELLS)
    mask[hit] |= caves.BIT_MARKERS
    return under


def _hull_bits(mask: U8Grid, hulls: list[F64Grid]) -> _HullBits:
    """Set ``BIT_HULL`` on every cell a hull's plan touches; the hulls' planes and boxes."""
    from scipy.spatial import ConvexHull, QhullError

    found = _HullBits()
    centres = (np.arange(CAVE_MASK_PX) + 0.5) * CAVE_CELL_CM
    for verts in hulls:
        try:
            solid = ConvexHull(verts)
            flat = ConvexHull(verts[:, :2])
        except (QhullError, ValueError):
            found.degenerate += 1
            continue
        found.planes.append(solid.equations)
        found.starts.append(found.starts[-1] + len(solid.equations))
        found.boxes.append(np.r_[verts.min(0), verts.max(0)])
        r0, c0 = _cells(verts[:, 0].min(keepdims=True), verts[:, 1].min(keepdims=True))
        r1, c1 = _cells(verts[:, 0].max(keepdims=True), verts[:, 1].max(keepdims=True))
        top, left = max(int(r0[0]), 0), max(int(c0[0]), 0)
        bottom, right = min(int(r1[0]), CAVE_MASK_PX - 1), min(int(c1[0]), CAVE_MASK_PX - 1)
        if bottom < top or right < left:
            continue
        xs = ORIGIN_X_CM + centres[left : right + 1]
        ys = ORIGIN_Y_CM + centres[top : bottom + 1]
        gx, gy = np.meshgrid(xs, ys)
        # Every cell the plan touches, so the reader may skip the 3D test where the bit is off.
        eq = flat.equations
        reach = CAVE_CELL_CM * math.sqrt(0.5)
        touch = gx[..., None] * eq[:, 0] + gy[..., None] * eq[:, 1] + eq[:, 2] <= reach
        mask[top : bottom + 1, left : right + 1][touch.all(-1)] |= caves.BIT_HULL
    return found


def build_caves(found: CaveSweep, height_dm: I16Grid) -> tuple[CaveArrays, JsonObject]:
    """The mask and hull arrays for ``caves.npz``, plus the counts the sidecar records."""
    mask = np.zeros((CAVE_MASK_PX, CAVE_MASK_PX), np.uint8)
    marks = found["markers"]
    under = _marker_bits(mask, marks, height_dm)
    hull = _hull_bits(mask, found["hulls"])
    arrays: CaveArrays = {
        "mask": mask,
        "planes": np.concatenate(hull.planes) if hull.planes else np.zeros((0, 4)),
        "starts": np.array(hull.starts, np.int64),
        "boxes": np.array(hull.boxes, np.float64).reshape(-1, 6),
    }
    cell_km2 = (CAVE_CELL_CM / 1e5) ** 2
    counts: JsonObject = {
        "volumes": found["volumes"],
        "volumes_without_hull": found["volumes_without_hull"],
        "hulls": len(hull.boxes),
        "hulls_degenerate": hull.degenerate,
        "markers": len(marks),
        "markers_under_ground": int(under.sum()),
        "marker_km2": round(float(((mask & caves.BIT_MARKERS) != 0).sum()) * cell_km2, 2),
        "hull_km2": round(float(((mask & caves.BIT_HULL) != 0).sum()) * cell_km2, 2),
        "flagged_km2": round(float((mask != 0).sum()) * cell_km2, 2),
    }
    return arrays, counts

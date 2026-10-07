"""The game's rivers: each ``BP_River_PROT_C`` spline mesh read as a Hermite ribbon.

A river is a chain of ``SplineMeshComponent`` sections bending the flat ``SM_RiverPlane``
along a cubic Hermite curve. Its water surface is that plane: a centreline height and a
half width along the curve, nothing else. docs/map/water.md section 34.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from typing import TypeAlias, TypedDict

import numpy as np
import numpy.typing as npt
from scipy import ndimage

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.meshes import MeshBounds
from mapgen.gamedata.water.actors import WATER_SURFACE_CLASSES, water_box_tops
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I32Grid, I64Grid
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    class_name_of,
    quat_rotate,
    world_transform,
)
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "RIVER_CLASS",
    "RIVER_PLANE_HALF_WIDTH_CM",
    "RIVER_VOLUME_CLASS",
    "RIVER_VOLUME_INSIDE",
    "RIVER_VOLUME_TOP_M",
    "SAMPLE_STEP_M",
    "RibbonPlanes",
    "RiverActor",
    "RiverRecord",
    "RiverSamples",
    "box_tops",
    "hermite",
    "ribbon_planes",
    "river_actor",
    "river_volumes",
    "sample_rivers",
]

#: The river blueprint. Its boxes are one AABB around the whole river, so only the spline
#: says where along it the water stands.
RIVER_CLASS = "BP_River_PROT_C"

#: ``SM_RiverPlane``'s half width across the curve, used when its bounds cannot be read.
RIVER_PLANE_HALF_WIDTH_CM = 500.0

#: Centreline sample spacing along each section.
SAMPLE_STEP_M = 0.5

#: Two section ends closer than this are one joint, not an open end of the river.
END_JOIN_M = 0.05

#: A section's open end cuts the plane within this many half widths of it.
CAP_REACH = 3.0

#: Extra metres past the plane's edge the ribbon planes are filled to.
RIBBON_REACH_M = 8.0

_OTHER_SURFACES = WATER_SURFACE_CLASSES - {RIVER_CLASS}

#: The volume class that names no material of its own: a lake's, the sea's, or a river's.
RIVER_VOLUME_CLASS = "FGWaterVolume"

#: Such a volume lying at least this share inside a river's box, with its top within this
#: many metres of the box's, is the river's own: 56 of the 270 on build 502094.
RIVER_VOLUME_INSIDE = 0.9
RIVER_VOLUME_TOP_M = 1.5


class RiverActor(TypedDict):
    """One river actor: its name, its plane meshes, and a row per section (``_section``)."""

    actor: str
    meshes: list[str]
    sections: list[list[float]]


#: ``river_actor``'s record as the level sweep collects it: a ``RiverActor``.
RiverRecord: TypeAlias = dict[str, str | list[str] | list[list[float]]]

#: Centreline samples in metres, one row each: ``x``, ``y``, ``z``, ``hw`` (float64),
#: ``river`` and ``section`` (int32), and ``cap``, the outward direction at open ends.
RiverSamples: TypeAlias = dict[str, F64Grid | I32Grid]


class RibbonPlanes(TypedDict):
    """The ribbons on a grid: the plane's height, its half width, and distance over it."""

    level_m: F32Grid
    u: F32Grid
    half_m: F32Grid


def _doubles(value: JsonValue | None, count: int) -> tuple[float, ...] | None:
    if not isinstance(value, dict):
        return None
    hex_bytes = value.get("_raw")
    if not isinstance(hex_bytes, str):
        return None
    raw = bytes.fromhex(hex_bytes)
    return struct.unpack(f"<{count}d", raw[: 8 * count]) if len(raw) >= 8 * count else None


def _section(
    view: PackageView, slot: int, classes: ClassFacts, half_width_cm: float
) -> list[float] | None:
    """One spline-mesh section in world cm: p0, t0, p1, t1 and the half width at each end."""
    params = view.props(slot).get("SplineParams")
    if not params:
        return None
    decoded: JsonObject = view.decode_struct(params)
    p0, p1 = _doubles(decoded.get("StartPos"), 3), _doubles(decoded.get("EndPos"), 3)
    if p0 is None or p1 is None:
        return None
    t0 = _doubles(decoded.get("StartTangent"), 3) or (0.0, 0.0, 0.0)
    t1 = _doubles(decoded.get("EndTangent"), 3) or (0.0, 0.0, 0.0)
    s0 = _doubles(decoded.get("StartScale"), 2) or (1.0, 1.0)
    s1 = _doubles(decoded.get("EndScale"), 2) or (1.0, 1.0)
    transform: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None
    transform = world_transform(view, slot, classes)[0]
    if transform is None:
        return None
    loc, quat, scale = transform

    def turn(v: Sequence[float]) -> tuple[float, float, float]:
        return quat_rotate(quat, (v[0] * scale[0], v[1] * scale[1], v[2] * scale[2]))

    row: list[float] = []
    for point, tangent in ((p0, t0), (p1, t1)):
        row += [loc[k] + c for k, c in enumerate(turn(point))]
        row += list(turn(tangent))
    across = half_width_cm * abs(scale[1])
    return [*row, s0[0] * across, s1[0] * across]


def river_actor(
    view: PackageView, actor: int, classes: ClassFacts, meshes: MeshBounds
) -> RiverRecord:
    """Every river-plane section under one river actor, in curve order where it can tell."""
    sections: list[list[float]] = []
    mesh_names: set[str] = set()
    for slot in view.children.get(actor, []):
        if class_name_of(view.class_of.get(slot)) != "SplineMeshComponent":
            continue
        props = view.props(slot)
        mesh = view.import_path(props["StaticMesh"]) if "StaticMesh" in props else None
        bounds = meshes.extended_bounds(mesh) if mesh else None
        half: float = bounds[1][1] if bounds else RIVER_PLANE_HALF_WIDTH_CM
        row = _section(view, slot, classes, half)
        if row is not None:
            sections.append([round(v, 2) for v in row])
            mesh_names.add((mesh or "?").rsplit("/", 1)[-1])
    return {
        "actor": view.exports[actor]["name"],
        "meshes": sorted(mesh_names),
        "sections": sections,
    }


def hermite(rows: F64Grid, s: F64Grid) -> F64Grid:
    """Points on each section's cubic at parameters ``s``: ``(len(rows), len(s), 3)``."""
    s = s[None, :, None]
    p0, t0, p1, t1 = (rows[:, None, k : k + 3] for k in (0, 3, 6, 9))
    return (
        (2 * s**3 - 3 * s**2 + 1) * p0
        + (s**3 - 2 * s**2 + s) * t0
        + (-2 * s**3 + 3 * s**2) * p1
        + (s**3 - s**2) * t1
    )


def sample_rivers(rivers: Sequence[RiverActor], step_m: float = SAMPLE_STEP_M) -> RiverSamples:
    """Centreline samples in metres: x, y, z, half width, which river and section, and at an
    open end of a river (a section end no other section continues) the outward direction."""
    out: dict[str, list[npt.NDArray[np.float64]]] = {k: [] for k in ("x", "y", "z", "hw")}
    ids: dict[str, list[I32Grid]] = {"river": [], "section": []}
    section_id = 0
    for river_id, river in enumerate(rivers):
        for section in river["sections"]:
            row = np.asarray(section, np.float64)
            chord_m = math.dist(row[0:3], row[6:9]) / 100.0
            n = max(2, math.ceil(chord_m / step_m) + 1)
            s = np.linspace(0.0, 1.0, n)
            points = hermite(row[None, :], s)[0] / 100.0
            for k, axis in enumerate("xyz"):
                out[axis].append(points[:, k])
            out["hw"].append(((1 - s) * row[12] + s * row[13]) / 100.0)
            ids["river"].append(np.full(n, river_id, np.int32))
            ids["section"].append(np.full(n, section_id, np.int32))
            section_id += 1
    floats = {k: np.concatenate(v) if v else np.zeros(0, np.float64) for k, v in out.items()}
    ints = {k: np.concatenate(v) if v else np.zeros(0, np.int32) for k, v in ids.items()}
    x, y, section = floats["x"], floats["y"], ints["section"]
    return {
        "x": x,
        "y": y,
        "z": floats["z"],
        "hw": floats["hw"],
        "river": ints["river"],
        "section": section,
        "cap": _open_ends(x, y, ints["river"], section),
    }


def _open_ends(x: F64Grid, y: F64Grid, river_ids: I32Grid, section: I32Grid) -> F64Grid:
    """``(n, 2)``: the outward unit direction at each open section end, zero elsewhere."""
    cap = np.zeros((len(x), 2))
    if not len(x):
        return cap
    first = np.flatnonzero(np.r_[True, section[1:] != section[:-1]])
    last = np.r_[first[1:] - 1, len(x) - 1]
    ends = np.r_[first, last]
    inner = np.r_[first + 1, last - 1]
    points = np.stack([x[ends], y[ends]], 1)
    river = river_ids[ends]
    for k, (end, toward) in enumerate(zip(ends, inner, strict=True)):
        gap = np.hypot(*(points - points[k]).T)
        gap[k] = np.inf
        if (gap[river == river[k]] < END_JOIN_M).any():
            continue
        out = np.array([x[end] - x[toward], y[end] - y[toward]])
        cap[end] = out / max(float(np.hypot(*out)), 1e-9)
    return cap


def _refine(
    samples: RiverSamples,
    pixel: tuple[I64Grid, I64Grid],
    start: I64Grid,
    origin_m: tuple[float, float],
    spacing_m: float,
) -> tuple[F64Grid, F64Grid, F64Grid]:
    """Exact distance to the nearest centreline piece near each texel's nearest sample.

    ``pixel`` is the texels' ``(rows, cols)`` and ``start`` each one's nearest sample.
    """
    rows, cols = pixel
    px = origin_m[0] + cols * spacing_m
    py = origin_m[1] + rows * spacing_m
    x, y, z, hw = samples["x"], samples["y"], samples["z"], samples["hw"]
    section, cap = samples["section"], samples["cap"]
    first = np.flatnonzero(np.r_[True, section[1:] != section[:-1]])
    ends = (np.repeat(first, np.diff(np.r_[first, len(x)])),
            np.repeat(np.r_[first[1:] - 1, len(x) - 1], np.diff(np.r_[first, len(x)])))  # fmt: skip
    best = np.full(px.shape, np.inf)
    level = np.zeros(px.shape)
    half = np.zeros(px.shape)
    last = len(x) - 1
    for offset in range(-3, 3):
        a = np.clip(start + offset, 0, last - 1)
        b = a + 1
        same = section[a] == section[b]
        dx, dy = x[b] - x[a], y[b] - y[a]
        length2 = np.maximum(dx * dx + dy * dy, 1e-12)
        t = np.where(same, np.clip(((px - x[a]) * dx + (py - y[a]) * dy) / length2, 0, 1), 0.0)
        d = np.hypot(px - (x[a] + t * dx), py - (y[a] + t * dy))
        hw_t = hw[a] + t * (hw[b] - hw[a])
        u = d / np.maximum(hw_t, 1e-3)
        for end in (ends[0][a], ends[1][a]):
            beyond = (px - x[end]) * cap[end, 0] + (py - y[end]) * cap[end, 1]
            near = np.hypot(px - x[end], py - y[end]) <= CAP_REACH * hw[end]
            past = (beyond > 0) & near
            u = np.maximum(u, np.where(past, 1.0 + beyond / np.maximum(hw_t, 1e-3), 0))
        take = u < best
        best = np.where(take, u, best)
        level = np.where(take, z[a] + t * (z[b] - z[a]), level)
        half = np.where(take, hw_t, half)
    return best, level, half


def ribbon_planes(
    samples: RiverSamples,
    reach_m: float = RIBBON_REACH_M,
    shape: tuple[int, int] = (GRID_PX, GRID_PX),
    hang: tuple[F32Grid | F64Grid, float] | None = None,
) -> RibbonPlanes:
    """The ribbons on the 1 m grid: the plane's height, its half width, and ``u``.

    ``u`` is distance from the centreline over the half width, 1 at the plane's edge. All
    three are NaN past ``reach_m`` beyond that edge.
    Where two planes overlap, the higher one wins, as it would seen from above; past both
    edges, the nearer in half widths. ``hang`` is ``(ground_m, metres)``: a plane standing
    more than that over the ground, or over none, gives way to one that does not.
    """
    spacing_m = SPACING_CM / 100.0
    x0_m, y0_m = ORIGIN_X_CM / 100.0, ORIGIN_Y_CM / 100.0
    level = np.full(shape, np.nan, np.float32)
    half_m = np.full(shape, np.nan, np.float32)
    u_plane = np.full(shape, np.inf, np.float32)
    for river_id in np.unique(samples["river"]):
        pick = np.nonzero(samples["river"] == river_id)[0]
        margin = float(samples["hw"][pick].max()) + reach_m + 2.0
        col = (samples["x"][pick] - x0_m) / spacing_m
        row = (samples["y"][pick] - y0_m) / spacing_m
        c0 = max(int(col.min() - margin), 0)
        c1 = min(int(col.max() + margin) + 2, shape[1])
        r0 = max(int(row.min() - margin), 0)
        r1 = min(int(row.max() + margin) + 2, shape[0])
        if c1 <= c0 or r1 <= r0:
            continue
        nearest = np.full((r1 - r0, c1 - c0), -1, np.int64)
        rr, cc = np.round(row).astype(int) - r0, np.round(col).astype(int) - c0
        inside = (rr >= 0) & (rr < r1 - r0) & (cc >= 0) & (cc < c1 - c0)
        nearest[rr[inside], cc[inside]] = pick[inside]
        dist, (ir, ic) = ndimage.distance_transform_edt(nearest < 0, return_indices=True)
        wr, wc = np.nonzero(dist <= margin)
        start = nearest[ir[wr, wc], ic[wr, wc]]
        u, z, hw = _refine(samples, (wr + r0, wc + c0), start, (x0_m, y0_m), spacing_m)
        keep = u * hw <= hw + reach_m
        gr, gc = wr[keep] + r0, wc[keep] + c0
        u_new, u_old = u[keep], u_plane[gr, gc]
        higher = z[keep] > np.nan_to_num(level[gr, gc], nan=-np.inf)
        if hang is not None:
            ground = hang[0][gr, gc]
            with np.errstate(invalid="ignore"):
                hangs = ~(z[keep] - ground <= hang[1])
                held = level[gr, gc] - ground <= hang[1]
            higher = (higher & ~(hangs & held)) | (~hangs & ~held)
        on_plane = (u_new <= 1) & ((u_old > 1) | higher)
        better = on_plane | ((u_new > 1) & (u_old > 1) & (u_new < u_old))
        u_plane[gr[better], gc[better]] = u[keep][better]
        level[gr[better], gc[better]] = z[keep][better]
        half_m[gr[better], gc[better]] = hw[keep][better]
    u_plane[~np.isfinite(u_plane)] = np.nan
    return {"level_m": level, "u": u_plane, "half_m": half_m}


def river_volumes(
    boxes: Sequence[tuple[str, Sequence[float]]],
) -> list[tuple[str, Sequence[float]]]:
    """``boxes`` with each river's own volumes named as its box: a classless volume
    (``RIVER_VOLUME_CLASS``) at least ``RIVER_VOLUME_INSIDE`` of whose footprint lies inside
    a river box, its top within ``RIVER_VOLUME_TOP_M`` of that box's. Its top is the river's
    upper end, as the river box's is, not a lake's surface."""
    rivers = [box for name, box in boxes if name == RIVER_CLASS]
    return [
        (RIVER_CLASS if name == RIVER_VOLUME_CLASS and _inside_a_river(box, rivers) else name, box)
        for name, box in boxes
    ]


def _inside_a_river(box: Sequence[float], rivers: Sequence[Sequence[float]]) -> bool:
    x0, y0, _z0, x1, y1, z1 = box
    area = (x1 - x0) * (y1 - y0)
    for rx0, ry0, _rz0, rx1, ry1, rz1 in rivers:
        across = max(0.0, min(x1, rx1) - max(x0, rx0)) * max(0.0, min(y1, ry1) - max(y0, ry0))
        nested = area > 0 and across >= RIVER_VOLUME_INSIDE * area
        if nested and abs(z1 - rz1) <= RIVER_VOLUME_TOP_M * 100:
            return True
    return False


def box_tops(
    boxes: Iterable[tuple[str, Sequence[float]]],
    river: bool,
    shape: tuple[int, int] = (GRID_PX, GRID_PX),
) -> F32Grid:
    """The highest top of the river boxes (or of every other surface box) over each texel, m.

    ``water_box_tops`` with one of two class sets, for ``palette.water.rivers``.
    """
    return water_box_tops(boxes, {RIVER_CLASS} if river else _OTHER_SURFACES, shape)[0]

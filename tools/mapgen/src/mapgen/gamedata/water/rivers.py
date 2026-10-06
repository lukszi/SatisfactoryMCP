"""The game's rivers: each ``BP_River_PROT_C`` spline mesh read as a Hermite ribbon.

A river is a chain of ``SplineMeshComponent`` sections bending the flat ``SM_RiverPlane``
along a cubic Hermite curve. Its water surface is that plane: a centreline height and a
half width along the curve, nothing else. docs/spatial-and-map.md section 34.
"""

from __future__ import annotations

import math
import struct

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.water.actors import WATER_SURFACE_CLASSES
from satisfactory_mcp.core.gameassets.packages import class_name_of, quat_rotate, world_transform

__all__ = [
    "RIVER_CLASS",
    "RIVER_PLANE_HALF_WIDTH_CM",
    "SAMPLE_STEP_M",
    "box_tops",
    "hermite",
    "ribbon_planes",
    "river_actor",
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


def _doubles(struct_value, count: int) -> tuple[float, ...] | None:
    if not isinstance(struct_value, dict) or "_raw" not in struct_value:
        return None
    raw = bytes.fromhex(struct_value["_raw"])
    return struct.unpack(f"<{count}d", raw[: 8 * count]) if len(raw) >= 8 * count else None


def _section(view, slot: int, classes, half_width_cm: float) -> list[float] | None:
    """One spline-mesh section in world cm: p0, t0, p1, t1 and the half width at each end."""
    params = view.props(slot).get("SplineParams")
    if not params:
        return None
    d = view.decode_struct(params)
    p0, p1 = _doubles(d.get("StartPos"), 3), _doubles(d.get("EndPos"), 3)
    if p0 is None or p1 is None:
        return None
    t0 = _doubles(d.get("StartTangent"), 3) or (0.0, 0.0, 0.0)
    t1 = _doubles(d.get("EndTangent"), 3) or (0.0, 0.0, 0.0)
    s0 = _doubles(d.get("StartScale"), 2) or (1.0, 1.0)
    s1 = _doubles(d.get("EndScale"), 2) or (1.0, 1.0)
    transform, _parent = world_transform(view, slot, classes)
    if transform is None:
        return None
    loc, quat, scale = transform

    def turn(v):
        return quat_rotate(quat, (v[0] * scale[0], v[1] * scale[1], v[2] * scale[2]))

    row: list[float] = []
    for point, tangent in ((p0, t0), (p1, t1)):
        row += [loc[k] + c for k, c in enumerate(turn(point))]
        row += list(turn(tangent))
    across = half_width_cm * abs(scale[1])
    return [*row, s0[0] * across, s1[0] * across]


def river_actor(view, actor: int, classes, meshes) -> dict:
    """Every river-plane section under one river actor, in curve order where it can tell."""
    sections, mesh_names = [], set()
    for slot in view.children.get(actor, []):
        if class_name_of(view.class_of.get(slot)) != "SplineMeshComponent":
            continue
        props = view.props(slot)
        mesh = view.import_path(props["StaticMesh"]) if "StaticMesh" in props else None
        bounds = meshes.of(mesh) if mesh else None
        half = bounds[1][1] if bounds else RIVER_PLANE_HALF_WIDTH_CM
        row = _section(view, slot, classes, half)
        if row is not None:
            sections.append([round(v, 2) for v in row])
            mesh_names.add((mesh or "?").rsplit("/", 1)[-1])
    return {"actor": view.exports[actor]["name"], "meshes": sorted(mesh_names),
            "sections": sections}  # fmt: skip


def hermite(rows: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Points on each section's cubic at parameters ``s``: ``(len(rows), len(s), 3)``."""
    s = s[None, :, None]
    p0, t0, p1, t1 = (rows[:, None, k : k + 3] for k in (0, 3, 6, 9))
    return (
        (2 * s**3 - 3 * s**2 + 1) * p0
        + (s**3 - 2 * s**2 + s) * t0
        + (-2 * s**3 + 3 * s**2) * p1
        + (s**3 - s**2) * t1
    )


def sample_rivers(rivers: list[dict], step_m: float = SAMPLE_STEP_M) -> dict[str, np.ndarray]:
    """Centreline samples in metres: x, y, z, half width, which river and section, and at an
    open end of a river (a section end no other section continues) the outward direction."""
    out: dict[str, list] = {k: [] for k in ("x", "y", "z", "hw", "river", "section")}
    section_id = 0
    for river_id, river in enumerate(rivers):
        for row in river["sections"]:
            row = np.asarray(row, np.float64)
            chord_m = math.dist(row[0:3], row[6:9]) / 100.0
            n = max(2, math.ceil(chord_m / step_m) + 1)
            s = np.linspace(0.0, 1.0, n)
            points = hermite(row[None, :], s)[0] / 100.0
            for k, axis in enumerate("xyz"):
                out[axis].append(points[:, k])
            out["hw"].append(((1 - s) * row[12] + s * row[13]) / 100.0)
            out["river"].append(np.full(n, river_id, np.int32))
            out["section"].append(np.full(n, section_id, np.int32))
            section_id += 1
    empty = {"river": np.int32, "section": np.int32}
    samples = {
        k: np.concatenate(v) if v else np.zeros(0, empty.get(k, np.float64)) for k, v in out.items()
    }
    samples["cap"] = _open_ends(samples)
    return samples


def _open_ends(samples: dict) -> np.ndarray:
    """``(n, 2)``: the outward unit direction at each open section end, zero elsewhere."""
    x, y, section = samples["x"], samples["y"], samples["section"]
    cap = np.zeros((len(x), 2))
    if not len(x):
        return cap
    first = np.flatnonzero(np.r_[True, section[1:] != section[:-1]])
    last = np.r_[first[1:] - 1, len(x) - 1]
    ends = np.r_[first, last]
    inner = np.r_[first + 1, last - 1]
    points = np.stack([x[ends], y[ends]], 1)
    river = samples["river"][ends]
    for k, (end, toward) in enumerate(zip(ends, inner, strict=True)):
        gap = np.hypot(*(points - points[k]).T)
        gap[k] = np.inf
        if (gap[river == river[k]] < END_JOIN_M).any():
            continue
        out = np.array([x[end] - x[toward], y[end] - y[toward]])
        cap[end] = out / max(float(np.hypot(*out)), 1e-9)
    return cap


def _refine(samples, rows, cols, start, x0_m, y0_m, spacing_m):
    """Exact distance to the nearest centreline piece near each texel's nearest sample."""
    px = x0_m + cols * spacing_m
    py = y0_m + rows * spacing_m
    x, y, z, hw, section, cap = (samples[k] for k in ("x", "y", "z", "hw", "section", "cap"))
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
    samples: dict, reach_m: float = RIBBON_REACH_M, shape=(GRID_PX, GRID_PX), hang=None
) -> dict[str, np.ndarray]:
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
        u, z, hw = _refine(samples, wr + r0, wc + c0, start, x0_m, y0_m, spacing_m)
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


def box_tops(boxes: list, river: bool, shape=(GRID_PX, GRID_PX)) -> np.ndarray:
    """The highest top of the river boxes (or of every other surface box) over each texel, m."""
    tops = np.full(shape, np.nan, np.float32)
    for name, box in boxes:
        if name not in WATER_SURFACE_CLASSES or (name == RIVER_CLASS) != river:
            continue
        x0, y0, _z0, x1, y1, z1 = box
        col0 = max(0, math.ceil((x0 - ORIGIN_X_CM) / SPACING_CM))
        col1 = min(shape[1], math.floor((x1 - ORIGIN_X_CM) / SPACING_CM) + 1)
        row0 = max(0, math.ceil((y0 - ORIGIN_Y_CM) / SPACING_CM))
        row1 = min(shape[0], math.floor((y1 - ORIGIN_Y_CM) / SPACING_CM) + 1)
        if col1 <= col0 or row1 <= row0:
            continue
        window = tops[row0:row1, col0:col1]
        np.fmax(window, np.float32(z1 / 100.0), out=window)
    return tops

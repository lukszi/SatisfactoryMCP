"""The seabed coral carpet: the crater-grass rosettes that read blue in the Spire Coast shallows.

The paint command harvests their foliage instances in its own level walk and writes two planes
into the paint store on the 1 m grid: how much of each texel a rosette covers, and the highest
rosette top over it. The painted style draws them into the seabed under the water.
docs/spatial-and-map.md section 32 has the evidence and the numbers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.install import GameReader
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I16Grid, U8Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "CARPET_MESHES",
    "COVER_NAME",
    "FOOTPRINT_STEP_CM",
    "TOP_NAME",
    "carpet_planes",
    "footprint",
    "is_carpet",
    "write_carpet",
]

#: Foliage meshes that make up the carpet, by asset name.
CARPET_MESHES = frozenset({"SM_CraterGrass_01"})

COVER_NAME = "carpet.u8.z"
TOP_NAME = "carpet_top.i16.z"

#: The footprint sample spacing in mesh space, centimetres.
FOOTPRINT_STEP_CM = 10.0

#: Instances placed per batch, so a batch's sample points stay a few hundred MB.
_BATCH = 4096


def is_carpet(mesh: str) -> bool:
    return mesh.rsplit("/", 1)[-1] in CARPET_MESHES


def _half_hull(points: Iterable[tuple[float, ...]]) -> list[tuple[float, ...]]:
    """One chain of the monotone-chain hull: every turn kept counter-clockwise."""
    out: list[tuple[float, ...]] = []
    for p in points:
        while (
            len(out) >= 2
            and (
                (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])
            )
            <= 0
        ):
            out.pop()
        out.append(p)
    return out[:-1]


def _hull(points: F64Grid) -> F64Grid:
    """Convex hull of 2-D points, counter-clockwise (Andrew's monotone chain)."""
    pts: list[tuple[float, ...]] = sorted(set(map(tuple, np.round(points, 3))))
    return np.array(_half_hull(pts) + _half_hull(pts[::-1]), np.float64)


def footprint(
    verts: F32Grid | F64Grid, step_cm: float = FOOTPRINT_STEP_CM
) -> tuple[F64Grid, float]:
    """``(points, top_cm)``: local XY cell centres inside the plan view's hull, and the top.

    The hull rather than the triangles: the blades are near-vertical cards, and from above a
    rosette reads as the patch it spans.
    """
    corners = np.asarray(verts, np.float64)
    hull = _hull(corners[:, :2])
    low, high = hull.min(0), hull.max(0)
    xs = np.arange(low[0] + step_cm / 2, high[0], step_cm)
    ys = np.arange(low[1] + step_cm / 2, high[1], step_cm)
    gx, gy = (g.ravel() for g in np.meshgrid(xs, ys))
    inside = np.ones(gx.size, bool)
    for a, b in zip(hull, np.roll(hull, -1, 0), strict=True):
        inside &= (b[0] - a[0]) * (gy - a[1]) - (b[1] - a[1]) * (gx - a[0]) >= 0
    return np.stack([gx[inside], gy[inside]], 1), float(corners[:, 2].max())


def carpet_planes(
    instances: Mapping[str, F32Grid | F64Grid],
    shapes: Mapping[str, tuple[F64Grid, float]],
    grid: int,
    x0_cm: float,
    y0_cm: float,
    spacing_cm: float,
    step_cm: float,
) -> tuple[U8Grid, I16Grid]:
    """``(cover u8, top dm i16)`` on the grid: covered share of each texel, and the top."""
    cells = grid * grid
    area = np.zeros(cells, np.float64)
    top = np.full(cells, -np.inf, np.float32)
    for mesh, mats in instances.items():
        if mesh not in shapes:
            continue
        points, top_cm = shapes[mesh]
        local = np.column_stack([points, np.zeros(len(points))])
        for start in range(0, len(mats), _BATCH):
            m = np.asarray(mats[start : start + _BATCH], np.float64)
            world = np.einsum("kj,njm->nkm", local, m[:, :3, :3]) + m[:, None, 3, :3]
            col = np.floor((world[..., 0] - x0_cm) / spacing_cm).astype(np.int64)
            row = np.floor((world[..., 1] - y0_cm) / spacing_cm).astype(np.int64)
            scale = np.linalg.norm(m[:, :3, :3], axis=2)
            weight = (step_cm * step_cm / spacing_cm**2) * scale[:, 0:1] * scale[:, 1:2]
            peak = (m[:, 3, 2] + top_cm * scale[:, 2])[:, None]
            ok = (col >= 0) & (col < grid) & (row >= 0) & (row < grid)
            idx = (row * grid + col)[ok]
            area += np.bincount(idx, np.broadcast_to(weight, col.shape)[ok], minlength=cells)
            np.maximum.at(top, idx, np.broadcast_to(peak, col.shape)[ok].astype(np.float32))
    cover = np.round(np.clip(area, 0.0, 1.0) * 255).astype(np.uint8).reshape(grid, grid)
    top_dm = np.where(
        np.isfinite(top), np.clip(np.round(top / 10.0), -32767, 32767), hf.NODATA
    ).astype(np.int16)
    return cover, top_dm.reshape(grid, grid)


def write_carpet(
    instances: Mapping[str, F32Grid], game: GameReader, grid: int
) -> tuple[dict[str, bytes], dict[str, JsonObject], JsonObject]:
    """Encoded planes for the paint store on the render frame, their files entries, and the
    meta block that describes them."""
    shapes: dict[str, tuple[F64Grid, float]] = {}
    sources: dict[str, JsonValue] = {}
    for mesh in sorted(instances):
        got: dict[str, object] = staticmesh.extract(game.store, game.scripts, game.index, mesh)
        source = got.get("route", got.get("error"))
        sources[mesh.rsplit("/", 1)[-1]] = source if isinstance(source, str) else None
        indices, verts = got.get("idx"), got.get("verts")
        if not (isinstance(indices, np.ndarray) and isinstance(verts, np.ndarray)):
            continue
        if got.get("ok") and len(indices):
            shapes[mesh] = footprint(verts)
    frame = (ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM)
    cover, top = carpet_planes(instances, shapes, grid, *frame, FOOTPRINT_STEP_CM)
    payload = {COVER_NAME: hf.encode_u8(cover), TOP_NAME: hf.encode_i16(top)}
    files: dict[str, JsonObject] = {
        COVER_NAME: {"shape": [grid, grid], "kind": "u8", "scale": 255},
        TOP_NAME: {"shape": [grid, grid], "kind": "i16", "units": "dm", "nodata": hf.NODATA},
    }
    meta: JsonObject = {
        "meshes": {m.rsplit("/", 1)[-1]: len(v) for m, v in sorted(instances.items())},
        "sources": sources,
        "footprint_cm2": {
            m.rsplit("/", 1)[-1]: round(len(p) * FOOTPRINT_STEP_CM**2)
            for m, (p, _t) in shapes.items()
        },
        "texels": int(np.count_nonzero(cover)),
    }
    return payload, files, meta

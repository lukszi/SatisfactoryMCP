"""The rocks' collision surface: the collision pack's format and a vertical-ray index over it.

``tools/gen_world_heightmap.py`` writes ``rocks.npz`` beside the field: every placed rock's
collision mesh once, in mesh-local cm, plus one transform per placement. ``RockIndex``
cuts it into 64 m world tiles on first touch, keeps them in a byte-bounded LRU, and
answers every surface a vertical line through a point crosses. Absent is normal: no pack,
no index, and ``heightfield`` reads its rasters as before. Design and measurements:
docs/spatial-and-map.md section 24.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "CACHE_BYTES",
    "DATA_NAME",
    "GROUND_KINDS",
    "KIND_ARCH",
    "KIND_BOULDER",
    "KIND_CAVE_FLOOR",
    "KIND_NAMES",
    "KIND_ROCK",
    "KIND_ROCK_SIMPLE",
    "META_NAME",
    "ROCKS_VERSION",
    "TILE_CM",
    "Hits",
    "RockIndex",
    "floor_note",
    "load_rocks",
]

DATA_NAME = "rocks.npz"
META_NAME = "rocks.json"
ROCKS_VERSION = 1

#: Placement kinds, which are the file format: do not renumber.
KIND_ROCK = 0
KIND_ROCK_SIMPLE = 1
KIND_ARCH = 2
KIND_BOULDER = 3
KIND_CAVE_FLOOR = 4
KIND_NAMES = {
    KIND_ROCK: "rock",
    KIND_ROCK_SIMPLE: "rock, simple collision",
    KIND_ARCH: "arch",
    KIND_BOULDER: "foliage boulder",
    KIND_CAVE_FLOOR: "cave floor",
}

#: The kinds the ``ground`` surface folds: the generator's cliff set. The rest are roofs or
#: shelves a hint can pick, and ``top`` folds everything.
GROUND_KINDS = frozenset({KIND_ROCK})

TILE_CM = 6400.0
CELL_CM = 100.0
CELLS = int(TILE_CM / CELL_CM)
CACHE_BYTES = 64 * 1024 * 1024

#: Barycentric slack, so a line down a shared edge hits both triangles rather than neither.
EDGE_EPS = 1e-7

#: Two hits closer than this are one surface: the seam between two triangles.
SAME_SURFACE_CM = 1.0

_UP = 1
_KIND_SHIFT = 1


def floor_note(z_m: float) -> str:
    """The line a height answer carries when ``Reading.cave_floor`` holds. Never a ceiling."""
    return f"in a cave: floor {z_m:.1f} m, the rock collision just under the given height"


@dataclass(frozen=True)
class Hits:
    """Every surface a vertical line crosses, highest first, in metres."""

    z_m: tuple[float, ...]
    up: tuple[bool, ...]
    kind: tuple[int, ...]

    def standing(self, kinds: frozenset[int] | None = None) -> list[float]:
        """Up-facing surfaces, optionally of some kinds only: what a thing can rest on."""
        return [
            z
            for z, u, k in zip(self.z_m, self.up, self.kind, strict=True)
            if u and (kinds is None or k in kinds)
        ]


@dataclass
class _Tile:
    x0: float
    y0: float
    tris: np.ndarray
    flags: np.ndarray
    order: np.ndarray
    offsets: np.ndarray

    @property
    def nbytes(self) -> int:
        return self.tris.nbytes + self.flags.nbytes + self.order.nbytes + self.offsets.nbytes


class RockIndex:
    """Vertical multi-hit rays over the collision pack, tiled lazily."""

    def __init__(
        self, arrays: dict[str, np.ndarray], meta: dict[str, Any], cache_bytes: int = CACHE_BYTES
    ) -> None:
        self.meta = meta
        self.verts = np.asarray(arrays["mesh_verts"], np.float32)
        self.vstart = np.asarray(arrays["mesh_vstart"], np.int64)
        self.tris = np.asarray(arrays["mesh_tris"], np.int32)
        self.tstart = np.asarray(arrays["mesh_tstart"], np.int64)
        self.winding = np.asarray(arrays["mesh_winding"], np.int8)
        self.inst_mesh = np.asarray(arrays["inst_mesh"], np.int32)
        self.inst_kind = np.asarray(arrays["inst_kind"], np.uint8)
        self.inst_matrix = np.asarray(arrays["inst_matrix"], np.float64)
        self.inst_origin = np.asarray(arrays["inst_origin"], np.float64)
        self.inst_lo = np.asarray(arrays["inst_lo"], np.float64)
        self.inst_hi = np.asarray(arrays["inst_hi"], np.float64)
        self.inst_flip = np.sign(np.linalg.det(self.inst_matrix)) if len(self.inst_mesh) else []
        self.cache_bytes = cache_bytes
        self._tiles: OrderedDict[tuple[int, int], _Tile] = OrderedDict()
        self._bytes = 0
        self.tiles_built = 0
        self.tiles_evicted = 0

    @property
    def cached_bytes(self) -> int:
        return self._bytes

    def _build(self, tx: int, ty: int) -> _Tile:
        x0, y0 = tx * TILE_CM, ty * TILE_CM
        x1, y1 = x0 + TILE_CM, y0 + TILE_CM
        lo, hi = self.inst_lo, self.inst_hi
        ids = np.nonzero((lo[:, 0] <= x1) & (hi[:, 0] >= x0) & (lo[:, 1] <= y1) & (hi[:, 1] >= y0))[
            0
        ]
        parts, flags = [], []
        for i in ids:
            m = self.inst_mesh[i]
            local = self.verts[self.vstart[m] : self.vstart[m + 1]].astype(np.float64)
            world = local @ self.inst_matrix[i] + self.inst_origin[i]
            world[:, 0] -= x0
            world[:, 1] -= y0
            tri = world[self.tris[self.tstart[m] : self.tstart[m + 1]]]
            xs, ys = tri[:, :, 0], tri[:, :, 1]
            keep = (
                (xs.max(1) >= 0)
                & (xs.min(1) <= TILE_CM)
                & (ys.max(1) >= 0)
                & (ys.min(1) <= TILE_CM)
            )
            if not keep.any():
                continue
            tri = tri[keep]
            facing = float(self.winding[m]) * self.inst_flip[i]
            if facing:
                nz = (tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1]) - (
                    tri[:, 1, 1] - tri[:, 0, 1]
                ) * (tri[:, 2, 0] - tri[:, 0, 0])
                up = nz * facing > 0
            else:
                up = np.ones(len(tri), bool)
            parts.append(tri.reshape(-1, 9).astype(np.float32))
            flags.append((up.astype(np.uint8) * _UP) | (int(self.inst_kind[i]) << _KIND_SHIFT))
        if parts:
            tris = np.concatenate(parts)
            flag = np.concatenate(flags).astype(np.uint8)
        else:
            tris, flag = np.zeros((0, 9), np.float32), np.zeros(0, np.uint8)
        order, offsets = _cell_index(tris)
        return _Tile(x0, y0, tris, flag, order, offsets)

    def tile(self, tx: int, ty: int) -> _Tile:
        key = (tx, ty)
        got = self._tiles.get(key)
        if got is not None:
            self._tiles.move_to_end(key)
            return got
        got = self._build(tx, ty)
        self.tiles_built += 1
        self._tiles[key] = got
        self._bytes += got.nbytes
        while self._bytes > self.cache_bytes and len(self._tiles) > 1:
            _, old = self._tiles.popitem(last=False)
            self._bytes -= old.nbytes
            self.tiles_evicted += 1
        return got

    def hits(self, x_cm: float, y_cm: float) -> Hits:
        """Every collision surface on the vertical line through a point, highest first."""
        tile = self.tile(math.floor(x_cm / TILE_CM), math.floor(y_cm / TILE_CM))
        px, py = x_cm - tile.x0, y_cm - tile.y0
        col = min(int(px // CELL_CM), CELLS - 1)
        row = min(int(py // CELL_CM), CELLS - 1)
        cell = row * CELLS + col
        pick = tile.order[tile.offsets[cell] : tile.offsets[cell + 1]]
        if not len(pick):
            return Hits((), (), ())
        t = tile.tris[pick].astype(np.float64)
        ax, ay, az, bx, by, bz, cx, cy, cz = t.T
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        ok = np.abs(den) > 1e-9
        den = np.where(ok, den, 1.0)
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
        l3 = 1.0 - l1 - l2
        inside = ok & (l1 >= -EDGE_EPS) & (l2 >= -EDGE_EPS) & (l3 >= -EDGE_EPS)
        if not inside.any():
            return Hits((), (), ())
        z = (l1 * az + l2 * bz + l3 * cz)[inside]
        flag = tile.flags[pick][inside]
        order = np.argsort(-z, kind="stable")
        z, flag = z[order], flag[order]
        out_z: list[float] = []
        out_up: list[bool] = []
        out_kind: list[int] = []
        for value, f in zip(z.tolist(), flag.tolist(), strict=True):
            up, kind = bool(f & _UP), f >> _KIND_SHIFT
            if out_z and abs(out_z[-1] * 100.0 - value) < SAME_SURFACE_CM and out_kind[-1] == kind:
                out_up[-1] = out_up[-1] or up
                continue
            out_z.append(value / 100.0)
            out_up.append(up)
            out_kind.append(kind)
        return Hits(tuple(out_z), tuple(out_up), tuple(out_kind))


def _cell_index(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Triangles per 1 m cell of a tile, as a CSR: ``order[offsets[c]:offsets[c+1]]``."""
    offsets = np.zeros(CELLS * CELLS + 1, np.int64)
    if not len(tris):
        return np.zeros(0, np.int32), offsets
    xs, ys = tris[:, [0, 3, 6]], tris[:, [1, 4, 7]]
    c0 = np.clip(np.floor(xs.min(1) / CELL_CM), 0, CELLS - 1).astype(np.int64)
    c1 = np.clip(np.floor(xs.max(1) / CELL_CM), 0, CELLS - 1).astype(np.int64)
    r0 = np.clip(np.floor(ys.min(1) / CELL_CM), 0, CELLS - 1).astype(np.int64)
    r1 = np.clip(np.floor(ys.max(1) / CELL_CM), 0, CELLS - 1).astype(np.int64)
    nx, ny = c1 - c0 + 1, r1 - r0 + 1
    count = nx * ny
    owner = np.repeat(np.arange(len(tris), dtype=np.int64), count)
    k = np.arange(int(count.sum()), dtype=np.int64) - np.repeat(np.cumsum(count) - count, count)
    width = np.repeat(nx, count)
    cell = (np.repeat(r0, count) + k // width) * CELLS + np.repeat(c0, count) + k % width
    order = np.argsort(cell, kind="stable")
    np.add.at(offsets, cell + 1, 1)
    return owner[order].astype(np.int32), np.cumsum(offsets)


_ARRAYS = (
    "mesh_verts",
    "mesh_vstart",
    "mesh_tris",
    "mesh_tstart",
    "mesh_winding",
    "inst_mesh",
    "inst_kind",
    "inst_matrix",
    "inst_origin",
    "inst_lo",
    "inst_hi",
)


def load_rocks(directory: Path, build: str | None = None) -> RockIndex | None:
    """The pack in ``directory``, or ``None`` if absent, unparsable, or cut from another build."""
    try:
        meta = json.loads((directory / META_NAME).read_text(encoding="utf-8"))
        pinned = ((meta.get("sources") or {}).get("game") or {}).get("game_version_pinned")
        if build is not None and pinned != build:
            return None
        if int(meta.get("rocks_version", 0)) != ROCKS_VERSION:
            return None
        with np.load(directory / DATA_NAME, allow_pickle=False) as data:
            arrays = {name: np.asarray(data[name]) for name in _ARRAYS}
        meshes = len(arrays["mesh_winding"])
        if arrays["mesh_vstart"].size != meshes + 1 or arrays["mesh_tstart"].size != meshes + 1:
            return None
        if arrays["inst_matrix"].shape[1:] != (3, 3):
            return None
        return RockIndex(arrays, meta)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None

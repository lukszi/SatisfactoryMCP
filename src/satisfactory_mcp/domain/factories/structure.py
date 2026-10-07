"""Foundation slabs: what the player physically built as one thing.

Foundations touching face to face, or stacked within ``LINK_Z``, are one slab; ramps, stairs
and walls join as nodes of their own so a chain of them can bridge, and catwalks never do.
Everything here is in CENTIMETRES, the save's own units, so it uses ``math.dist`` against cm
thresholds rather than ``geo.distance_m``. docs/save-projection.md §6.2a has the evidence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TypeAlias

from ...core.saveio import rows as saverows
from ...core.saveio.records import iter_machine_records
from ...core.saveio.schema import Projection
from ...core.unionfind import UnionFind
from .views import StructureSummary

__all__ = [
    "LINK_XY",
    "LINK_Z",
    "STAND_ON",
    "TILE_CM",
    "Slab",
    "Structures",
    "build_structures",
]

#: Face-adjacency cutoff in cm. Between a shared face (800) and a shared corner (1131).
LINK_XY = 830.0

#: Vertical reach in cm for stacked floors: purity climbs to 1600 and then stops (§6.2a).
LINK_Z = 1600.0

#: How far under a machine to look for the tile it stands on, in cm.
STAND_ON = 600.0

#: How far below a machine its tile may lie and still be the one it stands on, in cm.
MAX_TILE_BELOW_CM = 1200.0

#: One storey of a slab, in cm.
STOREY_CM = 400.0

#: A foundation tile's edge in cm, which is also the spatial-index cell.
TILE_CM = 800.0

#: Connective tissue. Catwalks are POINTEDLY absent -- see the module docstring.
_BRIDGE = ("Ramp", "Stair", "Wall")
_FOUNDATION = ("Foundation", "Platform")

#: A piece's ``(x, y, z)`` in centimetres.
Point3: TypeAlias = tuple[float, float, float]
#: Piece indices by the ``TILE_CM`` cell they stand in.
Cells: TypeAlias = dict[tuple[int, int], list[int]]


@dataclass
class Slab:
    """One connected platform.

    ``bbox`` is the tiles' XY bounding box in cm. It cannot be derived from ``centre``:
    that is the tile MEAN, so ``centre +- extent/2`` invents corners an L-shaped platform
    does not have.
    """

    index: int
    tiles: int
    centre: tuple[float, float, float]
    extent: tuple[float, float]
    z_span: tuple[float, float]
    bbox: tuple[float, float, float, float]

    @property
    def storeys(self) -> int:
        return max(1, round((self.z_span[1] - self.z_span[0]) / STOREY_CM) + 1)


@dataclass
class Structures:
    """Slabs, and which slab each machine stands on."""

    slabs: list[Slab] = field(default_factory=list[Slab])
    slab_of: dict[str, int] = field(default_factory=dict[str, int])

    def machines_on(self, index: int) -> list[str]:
        return sorted(m for m, s in self.slab_of.items() if s == index)

    def groups(self) -> list[list[str]]:
        """Machine sets by slab, largest first. Machines on no slab are omitted --
        they are ground-built and this signal has nothing to say about them."""
        by_slab: dict[int, list[str]] = defaultdict(list)
        for machine, index in self.slab_of.items():
            by_slab[index].append(machine)
        return sorted((sorted(v) for v in by_slab.values()), key=len, reverse=True)

    def summary(self) -> StructureSummary:
        return {
            "slabs": len(self.slabs),
            "tiles": sum(s.tiles for s in self.slabs),
            "machines_on_slabs": len(self.slab_of),
        }


def _grid(points: list[Point3]) -> Cells:
    cells: Cells = defaultdict(list)
    for i, p in enumerate(points):
        cells[(int(p[0] // TILE_CM), int(p[1] // TILE_CM))].append(i)
    return cells


def _neighbourhood(cells: Cells, cx: int, cy: int) -> list[int]:
    out: list[int] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out += cells.get((cx + dx, cy + dy), ())
    return out


def _split_pieces(projection: Projection) -> tuple[list[Point3], list[Point3]]:
    """``(foundation tiles, bridging pieces)`` as ``(x, y, z)`` in cm."""
    tiles: list[Point3] = []
    walkways: list[Point3] = []
    for piece in saverows.iter_structures(projection):
        # ``""`` for a class index the table cannot resolve: a piece with no name is neither.
        cls = piece.cls or ""
        point = (piece.x, piece.y, piece.z)
        if any(k in cls for k in _FOUNDATION):
            tiles.append(point)
        elif any(k in cls for k in _BRIDGE):
            walkways.append(point)
    return tiles, walkways


def _union_pieces(nodes: list[Point3], link_xy: float, link_z: float) -> UnionFind[int]:
    """Join every pair of pieces touching face to face, or stacked within ``link_z``."""
    cells = _grid(nodes)
    union = UnionFind[int]()
    for (cx, cy), members in cells.items():
        near = _neighbourhood(cells, cx, cy)
        for i in members:
            a = nodes[i]
            for j in near:
                if j <= i:
                    continue
                b = nodes[j]
                if math.dist(a[:2], b[:2]) > link_xy:
                    continue
                if abs(a[2] - b[2]) <= link_z:
                    union.union(i, j)
    return union


def _slabs_of(tiles: list[Point3], union: UnionFind[int]) -> tuple[list[Slab], dict[int, int]]:
    """The slabs, largest first, and each tile's slab index."""
    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tiles)):
        grouped[union.find(i)].append(i)
    slabs: list[Slab] = []
    tile_slab: dict[int, int] = {}
    for members in sorted(grouped.values(), key=len, reverse=True):
        xs = [tiles[i][0] for i in members]
        ys = [tiles[i][1] for i in members]
        zs = [tiles[i][2] for i in members]
        index = len(slabs)
        slabs.append(
            Slab(
                index=index,
                tiles=len(members),
                centre=(sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)),
                extent=(max(xs) - min(xs), max(ys) - min(ys)),
                z_span=(min(zs), max(zs)),
                bbox=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
        for i in members:
            tile_slab[i] = index
    return slabs, tile_slab


def _assign_machines(
    projection: Projection, tiles: list[Point3], tile_slab: dict[int, int]
) -> dict[str, int]:
    """Each machine's slab: the nearest tile UNDER it, so an upper-floor machine does not
    claim the ground-level slab it happens to sit above."""
    tile_cells = _grid(tiles)
    slab_of: dict[str, int] = {}
    for _group, leaf, record in iter_machine_records(projection):
        pos = record.get("pos")
        if not pos:
            continue
        cx, cy = int(pos[0] // TILE_CM), int(pos[1] // TILE_CM)
        best: int | None = None
        best_d = STAND_ON
        for i in _neighbourhood(tile_cells, cx, cy):
            tile = tiles[i]
            if not (-STAND_ON <= pos[2] - tile[2] <= MAX_TILE_BELOW_CM):
                continue
            d = math.dist(tile[:2], pos[:2])
            if d < best_d:
                best, best_d = i, d
        if best is not None:
            slab_of[leaf] = tile_slab[best]
    return slab_of


def build_structures(
    projection: Projection,
    link_xy: float = LINK_XY,
    link_z: float = LINK_Z,
) -> Structures:
    """Group foundations into slabs and assign each machine to the one beneath it."""
    tiles, walkways = _split_pieces(projection)
    if not tiles:
        return Structures()
    union = _union_pieces(tiles + walkways, link_xy, link_z)
    slabs, tile_slab = _slabs_of(tiles, union)
    return Structures(slabs=slabs, slab_of=_assign_machines(projection, tiles, tile_slab))

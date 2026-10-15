"""The foundation grids already built: what a dragged pad may align its edges to.

Every foundation in the game data is 8 x 8 m and the build gun lays neighbours on one
lattice, so a run of touching tiles shares a turn (mod 90°) and an 8 m phase. Nothing here
assumes a world-wide grid: each lattice is read from the tiles themselves.
docs/planner-p5_contract.md §5.4 is the specification.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping

from ....core.saveio import rows as saverows
from ....core.unionfind import UnionFind
from .views import SiteGrid

__all__ = ["GRIDS_MAX", "TILE_M", "foundation_grids"]

TILE_M = 8.0

#: Largest grids first; a save with more is answered with these.
GRIDS_MAX = 2000

#: Turns and phases closer than these are one lattice: build-gun floats, not a new grid.
_YAW_Q_DEG = 0.1
_PHASE_Q_M = 0.05

#: Half a tile's diagonal: a tile centre's reach in any turn.
_REACH_M = TILE_M * math.sqrt(2) / 2

# A passthrough sits in a foundation's hole; it carries no footprint of its own.
_SKIP = ("Passthrough",)


def _lattice(x: float, y: float, yaw: float) -> tuple[float, int, int, float, float]:
    """``(turn, cell u, cell v, phase u, phase v)`` of a tile centre in metres."""
    turn = round((yaw % 90.0) / _YAW_Q_DEG) * _YAW_Q_DEG % 90.0
    a = math.radians(turn)
    u = x * math.cos(a) + y * math.sin(a)
    v = -x * math.sin(a) + y * math.cos(a)
    pu = round((u % TILE_M) / _PHASE_Q_M) * _PHASE_Q_M % TILE_M
    pv = round((v % TILE_M) / _PHASE_Q_M) * _PHASE_Q_M % TILE_M
    return turn, round((u - pu) / TILE_M), round((v - pv) / TILE_M), pu, pv


def foundation_grids(projection: Mapping[str, object]) -> list[SiteGrid]:
    """Each connected run of foundations on one lattice, largest first.

    A grid is ``yaw_deg`` (0…90), ``anchor_m`` (one tile's centre, so lines run at
    ``anchor ± 4 + k·8`` along the turned axes), ``bbox_m`` round its tiles and ``tiles``.
    Stacked storeys share cells and so one grid.
    """
    tiles: list[tuple[float, float]] = []
    keys: list[tuple[float, float, float]] = []
    cells: dict[tuple[float, float, float], dict[tuple[int, int], int]] = defaultdict(dict)
    for piece in saverows.iter_structures(projection):
        cls = piece.cls or ""
        if "Foundation" not in cls or any(s in cls for s in _SKIP) or piece.yaw is None:
            continue
        x, y = piece.x / 100.0, piece.y / 100.0
        turn, cu, cv, pu, pv = _lattice(x, y, piece.yaw)
        key = (turn, pu, pv)
        on = cells[key]
        if (cu, cv) in on:
            continue
        on[(cu, cv)] = len(tiles)
        tiles.append((x, y))
        keys.append(key)
    union = UnionFind[int]()
    for on in cells.values():
        for (cu, cv), i in on.items():
            union.find(i)
            for du, dv in ((1, 0), (0, 1), (1, 1), (1, -1)):
                j = on.get((cu + du, cv + dv))
                if j is not None:
                    union.union(i, j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tiles)):
        groups[union.find(i)].append(i)
    out: list[SiteGrid] = []
    for members in sorted(groups.values(), key=lambda m: (-len(m), m[0])):
        xs = [tiles[i][0] for i in members]
        ys = [tiles[i][1] for i in members]
        first = tiles[members[0]]
        out.append(
            {
                "yaw_deg": keys[members[0]][0],
                "anchor_m": [round(first[0], 3), round(first[1], 3)],
                "bbox_m": [
                    round(min(xs) - _REACH_M, 1),
                    round(min(ys) - _REACH_M, 1),
                    round(max(xs) + _REACH_M, 1),
                    round(max(ys) + _REACH_M, 1),
                ],
                "tiles": len(members),
            }
        )
    return out[:GRIDS_MAX]

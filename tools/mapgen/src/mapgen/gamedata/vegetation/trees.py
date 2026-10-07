"""Tree instances as crowns: where each one stands, how tall it is and how wide.

The species comes from the foliage mesh, its size from the mesh's ``ExtendedBounds``, and
each instance's own scale from its world matrix. Design notes: tools/mapgen/README.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.meshes import MeshBounds
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, U8Grid

__all__ = [
    "CROWN_DEFAULT_M",
    "CROWN_M",
    "CROWN_MIN_RADIUS_M",
    "CROWN_TOP_MAX_M",
    "RADIUS_BINS_M",
    "TREE_MARKS",
    "Crown",
    "TreeTable",
    "canopy_cover",
    "crown_radius",
    "crown_species",
    "is_tree",
    "tree_table",
]

#: Past this, height only lengthens the column under a lifted crown (Mangrove_Tall_01: 228 m).
CROWN_TOP_MAX_M = 80.0

#: Under this a crown is a trunk, and a trunk's shadow is a line the map cannot hold.
CROWN_MIN_RADIUS_M = 0.75


@dataclass(frozen=True)
class Crown:
    """One species at scale 1, in metres, about the mesh's own pivot."""

    top_m: float
    radius_m: float
    centre_xy_m: tuple[float, float]


@dataclass(frozen=True)
class TreeTable:
    """One row per placed tree, world metres; ``species`` indexes ``names``."""

    x_m: F32Grid
    y_m: F32Grid
    base_m: F32Grid
    height_m: F32Grid
    radius_m: F32Grid
    species: U8Grid
    names: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.x_m.size)

    def within(self, x0_m: float, y0_m: float, x1_m: float, y1_m: float) -> TreeTable:
        """The trees whose crown reaches into the box."""
        r = self.radius_m
        keep = (
            (self.x_m + r >= x0_m)
            & (self.x_m - r <= x1_m)
            & (self.y_m + r >= y0_m)
            & (self.y_m - r <= y1_m)
        )
        return TreeTable(
            self.x_m[keep],
            self.y_m[keep],
            self.base_m[keep],
            self.height_m[keep],
            self.radius_m[keep],
            self.species[keep],
            self.names,
        )


def crown_species(meshes: MeshBounds, names: Iterable[str]) -> dict[str, Crown]:
    """``{mesh: Crown}`` from each tree mesh's ``ExtendedBounds``; unreadable ones are left out."""
    out: dict[str, Crown] = {}
    for mesh in names:
        bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None
        bounds = meshes.extended_bounds(mesh)
        if bounds is None:
            continue
        (ox, oy, oz), (ex, ey, ez) = bounds
        out[mesh] = Crown(
            top_m=(oz + ez) / 100.0,
            radius_m=float(np.sqrt(abs(ex * ey))) / 100.0,
            centre_xy_m=(ox / 100.0, oy / 100.0),
        )
    return out


def tree_table(trees: Mapping[str, F32Grid | F64Grid], species: Mapping[str, Crown]) -> TreeTable:
    """Every instance of every known species, from its ``(n, 4, 4)`` world matrices."""
    names = tuple(sorted(m for m in trees if m in species))
    parts: list[list[F64Grid]] = [[] for _ in range(6)]
    for index, mesh in enumerate(names):
        mats = np.asarray(trees[mesh], np.float64)
        crown = species[mesh]
        axes = np.linalg.norm(mats[:, :3, :3], axis=2)
        local = np.array([crown.centre_xy_m[0] * 100.0, crown.centre_xy_m[1] * 100.0, 0.0])
        centre = local @ mats[:, :3, :3] + mats[:, 3, :3]
        height = np.minimum(crown.top_m * axes[:, 2], CROWN_TOP_MAX_M)
        radius = crown.radius_m * np.sqrt(axes[:, 0] * axes[:, 1])
        keep = (height > 0) & (radius >= CROWN_MIN_RADIUS_M)
        for slot, values in enumerate(
            (
                centre[:, 0] / 100.0,
                centre[:, 1] / 100.0,
                mats[:, 3, 2] / 100.0,
                height,
                radius,
                np.full(len(mats), index),
            )
        ):
            parts[slot].append(values[keep])
    x, y, base, height, radius, kind = (np.concatenate(p) if p else np.zeros(0) for p in parts)
    return TreeTable(
        x.astype(np.float32),
        y.astype(np.float32),
        base.astype(np.float32),
        height.astype(np.float32),
        radius.astype(np.float32),
        kind.astype(np.uint8),
        names,
    )


#: Tree foliage, and a crown radius in metres by name fragment (first match wins).
TREE_MARKS = (
    "/Foliage/Trees/",
    "/Coral/CoralTree",
    "Bamboo",
    "Palm",
    "palm",
    "Kapok",
    "Mangrove",
    "SM_Trunk_01",
    "CraterTree",
)
CROWN_M = (
    ("Kapok", 14.0),
    ("DioTree", 10.0),
    ("Diospyros", 9.0),
    ("AncientPine", 9.0),
    ("GreenTree", 8.0),
    ("PollenTree", 7.0),
    ("PurpleTree", 7.0),
    ("AmberTree", 7.0),
    ("BalloonTree", 6.0),
    ("FunnelTree", 6.0),
    ("SnakeLegs", 6.0),
    ("CraterTree", 6.0),
    ("SnailBottom", 6.0),
    ("Mangrove", 6.0),
    ("SwampTree", 5.0),
    ("Uppochner", 5.0),
    ("BananaTree", 4.0),
    ("SM_Trunk_01", 4.0),
    ("Palm", 3.5),
    ("palm", 3.5),
    ("Yucca", 3.0),
    ("CoralTree", 3.0),
    ("Stump", 1.5),
    ("Bamboo", 1.5),
)
CROWN_DEFAULT_M = 4.0
#: Measured radii are snapped to these, so the canopy costs one blur per bin.
RADIUS_BINS_M = np.array([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 11.0, 15.0, 20.0])


def is_tree(mesh: str) -> bool:
    return any(mark in mesh for mark in TREE_MARKS) and "Fallen" not in mesh


def crown_radius(mesh: str) -> float:
    return next((r for key, r in CROWN_M if key in mesh), CROWN_DEFAULT_M)


def canopy_cover(
    trees: Mapping[str, F32Grid], grid: int, radii: Mapping[str, float] | None = None
) -> tuple[F32Grid, dict[str, int]]:
    """Crown cover in [0, 1]: ``1 - exp(-crown area per m^2)``, crowns blurred by radius.

    ``trees`` holds each mesh's 4x4 matrices. ``radii`` is the measured crown radius per
    mesh, scaled per tree; a mesh without one keeps the guessed ``crown_radius``.
    """
    by_radius: dict[float, list[F32Grid]] = {}
    for mesh, given in trees.items():
        mats = np.asarray(given)
        measured = (radii or {}).get(mesh)
        if measured is None:
            by_radius.setdefault(crown_radius(mesh), []).append(mats[:, 3, :3])
            continue
        scale = np.linalg.norm(mats[:, :3, :3][:, :2], axis=2).mean(1)
        snapped = RADIUS_BINS_M[
            np.abs(np.subtract.outer(measured * scale, RADIUS_BINS_M)).argmin(1)
        ]
        for radius in np.unique(snapped):
            by_radius.setdefault(float(radius), []).append(mats[snapped == radius, 3, :3])
    area = np.zeros((grid, grid), np.float32)
    counts: dict[str, int] = {}
    for radius, parts in sorted(by_radius.items()):
        points = np.concatenate(parts)
        col = np.floor((points[:, 0] - ORIGIN_X_CM) / SPACING_CM).astype(np.int64)
        row = np.floor((points[:, 1] - ORIGIN_Y_CM) / SPACING_CM).astype(np.int64)
        ok = (col >= 0) & (col < grid) & (row >= 0) & (row < grid)
        hits = np.zeros((grid, grid), np.float32)
        np.add.at(hits, (row[ok], col[ok]), np.float32(np.pi * radius * radius))
        area += ndimage.gaussian_filter(hits, radius / 1.5)
        counts[str(radius)] = int(ok.sum())
    return np.float32(1.0) - np.exp(-area), counts

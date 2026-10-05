"""Tree instances as crowns: where each one stands, how tall it is and how wide.

The species comes from the foliage mesh, its size from the mesh's ``ExtendedBounds``, and
each instance's own scale from its world matrix. Design notes: tools/mapgen/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mapgen.gamedata.paint import is_tree

__all__ = [
    "CROWN_MIN_RADIUS_M",
    "CROWN_TOP_MAX_M",
    "Crown",
    "TreeTable",
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

    x_m: np.ndarray
    y_m: np.ndarray
    base_m: np.ndarray
    height_m: np.ndarray
    radius_m: np.ndarray
    species: np.ndarray
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
            *(a[keep] for a in (self.x_m, self.y_m, self.base_m, self.height_m, self.radius_m)),
            self.species[keep],
            self.names,
        )


def crown_species(meshes, names) -> dict[str, Crown]:
    """``{mesh: Crown}`` from each tree mesh's ``ExtendedBounds``; unreadable ones are left out."""
    out = {}
    for mesh in names:
        bounds = meshes.of(mesh)
        if bounds is None:
            continue
        (ox, oy, oz), (ex, ey, ez) = bounds
        out[mesh] = Crown(
            top_m=(oz + ez) / 100.0,
            radius_m=float(np.sqrt(abs(ex * ey))) / 100.0,
            centre_xy_m=(ox / 100.0, oy / 100.0),
        )
    return out


def tree_table(trees: dict[str, np.ndarray], species: dict[str, Crown]) -> TreeTable:
    """Every instance of every known species, from its ``(n, 4, 4)`` world matrices."""
    names = tuple(sorted(m for m in trees if m in species))
    parts: list[list[np.ndarray]] = [[] for _ in range(6)]
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

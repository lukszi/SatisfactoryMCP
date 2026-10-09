"""The crown sprite cache as the stamps read it: one float32 atlas, every channel times alpha.

Each species level is the cache's rectangle with its one-texel gutter, a tile of an
``texels.Atlas``: colour (linear), normal and crown top (cm over the pivot), each times alpha,
then alpha, so a bilinear read between a crown and its gutter fades all of them together and
the gutter's alpha of 0 adds nothing. docs/map/light-and-crowns.md section 36, "Drawing".
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.sprites.store import GUTTER, SpriteAtlas, normal_xy
from mapgen.terrain.texels import Atlas
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I64Grid

__all__ = ["ALPHA", "CHANNELS", "NORMAL", "RGB", "TOP", "CrownAtlas", "crown_atlas"]

#: The channels of a texel: colour, normal and top, each times alpha, then alpha last.
RGB, NORMAL, TOP, ALPHA = slice(0, 3), slice(3, 6), 6, 7
CHANNELS = 8


class CrownAtlas:
    """The texels and tiles the stamps read, each tile's corner (its gutter's) and texel in
    mesh cm, how far from the pivot a read of it can find any alpha, and the species.

    Per species: ``mid_cm``, its alpha-weighted crown height over its pivot; ``top_cm``, its
    highest texel; ``widest_cm``, the farthest reach of any of its tiles. ``levels`` hands
    each species' tiles out as arrays and takes them back, for the colour calibration;
    ``version`` counts the times they were taken back, for a device's copy.
    """

    def __init__(
        self,
        atlas: Atlas,
        corner_cm: F64Grid,
        texel_cm: F64Grid,
        first: I64Grid,
        counts: I64Grid,
        names: Sequence[str],
    ) -> None:
        self.atlas, self.corner_cm, self.texel_cm = atlas, corner_cm, texel_cm
        self.first, self.counts, self.names = first, counts, list(names)
        self.version = 0
        self.reach_cm = np.array([self._reach(k) for k in range(len(texel_cm))], np.float64)
        species = range(len(self.names))
        self.mid_cm = np.array([self._mid(k) for k in species], np.float64)
        self.top_cm = np.array([self._top(k) for k in species], np.float64)
        self.widest_cm = np.array(
            [self.reach_cm[first[k] : first[k] + counts[k]].max(initial=0.0) for k in species]
        )

    def tile(self, row: int) -> F32Grid:
        """The texels of tile ``row``, a view."""
        x, y, w, h = (int(v) for v in self.atlas.tiles[row])
        return self.atlas.texels[y : y + h, x : x + w]

    @property
    def levels(self) -> list[list[F32Grid]]:
        """Every species' tiles, finest first, as copies."""
        return [
            [self.tile(int(self.first[k]) + n).copy() for n in range(int(self.counts[k]))]
            for k in range(len(self.names))
        ]

    @levels.setter
    def levels(self, levels: Sequence[Sequence[F32Grid]]) -> None:
        for k, tiles in enumerate(levels):
            for n, texels in enumerate(tiles):
                self.tile(int(self.first[k]) + n)[...] = texels
        self.version += 1

    def _mid(self, species: int) -> float:
        texels = self.tile(int(self.first[species]))
        return float(texels[..., TOP].sum() / max(float(texels[..., ALPHA].sum()), 1e-6))

    def _top(self, species: int) -> float:
        texels = self.tile(int(self.first[species]))
        alpha = texels[..., ALPHA]
        tops = np.divide(texels[..., TOP], alpha, out=np.zeros_like(alpha), where=alpha > 0)
        return float(tops.max(initial=0.0))

    def _reach(self, row: int) -> float:
        """The farthest from the pivot a read of tile ``row`` finds alpha: a covered texel's
        centre and a texel's diagonal, the reach of the bilinear tent round it."""
        texels = self.tile(row)
        rows, cols = np.nonzero(texels[..., ALPHA] > 0)
        if not len(rows):
            return 0.0
        step = self.texel_cm[row]
        x = self.corner_cm[row, 0] + (cols + 0.5) * step
        y = self.corner_cm[row, 1] + (rows + 0.5) * step
        return float(np.hypot(x, y).max() + step * np.sqrt(2.0))


def crown_atlas(sprites: SpriteAtlas) -> CrownAtlas:
    """The sprite cache decoded: linear colour, unit normal and top, each times alpha."""
    rgba = sprites.colour.astype(np.float32) / np.float32(255.0)
    alpha = rgba[..., 3]
    xy = normal_xy(sprites.normal)
    z = np.sqrt(np.clip(1.0 - (xy * xy).sum(-1), 0.0, 1.0))
    planes = [*np.moveaxis(srgb_unit_to_linear(rgba[..., :3]), -1, 0), xy[..., 0], xy[..., 1], z]
    planes.append(sprites.top.astype(np.float32))
    texels = np.stack([p * alpha for p in planes] + [alpha], -1).astype(np.float32)
    rec, g = sprites.records, GUTTER
    tiles = np.stack(
        [rec["x"] - g, rec["y"] - g, rec["width"] + 2 * g, rec["height"] + 2 * g], 1
    ).astype(np.int32)
    texel = rec["texel_cm"].astype(np.float64)
    corner = np.stack([rec["x0_cm"] - g * texel, rec["y0_cm"] - g * texel], 1).astype(np.float64)
    first = sprites.first.astype(np.int64)
    counts = sprites.levels.astype(np.int64)
    return CrownAtlas(Atlas(texels, tiles), corner, texel, first, counts, sprites.names)

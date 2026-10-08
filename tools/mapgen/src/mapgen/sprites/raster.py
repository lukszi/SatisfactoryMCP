"""A tree mesh rasterised from straight above with its textures: colour, normal, alpha, top.

Each sprite texel is sampled ``SUBSAMPLES`` squared times. A sample takes the highest
triangle over it whose leaf mask passes there (``fill.top_hits``, or its CUDA twin under
``--gpu``); its colour and normal are the material's at the hit (``shade.shade``, or its
twin): the albedo at the hit's UV, the tangent basis turned by the normal map. A texel's
alpha is its share of samples hit. docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from mapgen import jit
from mapgen.gamedata.vegetation.crown_sprites import SPRITE_M
from mapgen.gamedata.vegetation.tree_surface import SlotTexture, SurfaceMesh
from mapgen.sprites import fill, shade
from mapgen.sprites.fill import Hits, Triangles
from mapgen.sprites.shade import Shading, ShadingTables
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I32Grid, I64Grid, U8Grid

__all__ = [
    "BIN",
    "SAMPLE_CM",
    "SPRITE_CM",
    "SUBSAMPLES",
    "AlphaTextures",
    "SpriteGrid",
    "SpritePlanes",
    "pack_alpha",
    "rasterise",
    "resolve",
    "shade_samples",
    "sprite_grid",
    "top_hits",
    "triangle_setup",
]

#: A sprite texel, cm, and the samples per texel side.
SPRITE_CM = SPRITE_M * 100.0
SUBSAMPLES = 4
SAMPLE_CM = SPRITE_CM / SUBSAMPLES
#: Samples per side of a bin: a kernel thread reads the triangles of its bin only.
BIN = 32
#: Below this a triangle seen from above has no area, in squared samples.
FLAT_AREA = 1e-6
#: A triangle whose UVs leave [0, 1] by more than this tiles its texture, and wraps.
UV_EDGE = 1e-3


@dataclass(frozen=True)
class SpriteGrid:
    """A sprite's texels: the corner (mesh cm) and the size, rows along +Y."""

    x0_cm: float
    y0_cm: float
    width: int
    height: int

    @property
    def rows(self) -> int:
        return self.height * SUBSAMPLES

    @property
    def cols(self) -> int:
        return self.width * SUBSAMPLES


@dataclass(frozen=True)
class AlphaTextures:
    """Every slot's mask, flat, and per slot ``(offset, width, height, masked)``."""

    texels: U8Grid
    table: I32Grid


@dataclass(frozen=True)
class SpritePlanes:
    """One species from above on its sprite grid: alpha in [0, 1], straight linear colour,
    unit normal (x along +X, y along +Y, z up), crown top in cm over the pivot."""

    x0_cm: float
    y0_cm: float
    alpha: F32Grid
    colour: F32Grid
    normal: F32Grid
    top_cm: F32Grid


def sprite_grid(verts: F32Grid) -> SpriteGrid:
    """The texels over ``verts`` and one more on each side, on ``SPRITE_CM`` multiples."""
    low = np.floor(verts[:, :2].min(0) / SPRITE_CM) - 1
    high = np.ceil(verts[:, :2].max(0) / SPRITE_CM) + 1
    width, height = (int(v) for v in (high - low))
    return SpriteGrid(float(low[0] * SPRITE_CM), float(low[1] * SPRITE_CM), width, height)


def triangle_setup(surface: SurfaceMesh, drawn: BoolMask, grid: SpriteGrid) -> Triangles:
    """The drawn triangles in sample units, each with its edges, depths, UVs and bounds.

    Positions are measured from the grid's corner in samples, so sample ``(r, c)`` sits at
    ``(c + 0.5, r + 0.5)``. Each triangle keeps its first vertex and the two edges from it,
    the reciprocal of their cross product, and its depth and UV at the first vertex with
    their steps along the edges; triangles that stand edge-on are dropped.
    """
    source: I64Grid = np.flatnonzero(drawn)
    corner = np.array([grid.x0_cm, grid.y0_cm])
    p = (surface.verts[surface.tris[source], :2].astype(np.float64) - corner) / SAMPLE_CM
    z = surface.verts[surface.tris[source], 2].astype(np.float64)
    uv = surface.uvs[surface.tris[source]].astype(np.float64)
    p0 = p[:, 0].astype(np.float32)
    q1 = (p[:, 1] - p[:, 0]).astype(np.float32)
    q2 = (p[:, 2] - p[:, 0]).astype(np.float32)
    area = q1[:, 0].astype(np.float64) * q2[:, 1] - q1[:, 1].astype(np.float64) * q2[:, 0]
    keep = np.abs(area) > FLAT_AREA
    with np.errstate(divide="ignore"):
        inv = (1.0 / area).astype(np.float32)
    columns = [p0[:, 0], p0[:, 1], q1[:, 0], q1[:, 1], q2[:, 0], q2[:, 1], inv]
    for values in (z, uv[..., 0], uv[..., 1]):
        columns += [values[:, 0], values[:, 1] - values[:, 0], values[:, 2] - values[:, 0]]
    setup = np.stack([np.asarray(c, np.float32) for c in columns], axis=1)
    lo = np.floor(p.min(1) - 0.5).astype(np.int64)
    hi = np.ceil(p.max(1) - 0.5).astype(np.int64)
    tiled = ((uv < -UV_EDGE) | (uv > 1.0 + UV_EDGE)).any(axis=(1, 2))
    bounds = np.stack(
        [
            np.clip(lo[:, 0], 0, grid.cols - 1),
            np.clip(hi[:, 0], 0, grid.cols - 1),
            np.clip(lo[:, 1], 0, grid.rows - 1),
            np.clip(hi[:, 1], 0, grid.rows - 1),
            surface.slots[source],
            tiled,
        ],
        axis=1,
    ).astype(np.int32)
    bins = _bins(bounds[keep], grid)
    return Triangles(
        np.ascontiguousarray(setup[keep]), np.ascontiguousarray(bounds[keep]), source[keep], *bins
    )


def _bins(bounds: I32Grid, grid: SpriteGrid) -> tuple[I32Grid, I32Grid, int]:
    """Each bin's triangles in index order: ``(start, triangles, bins across)``."""
    across = -(-grid.cols // BIN)
    down = -(-grid.rows // BIN)
    c0, c1, r0, r1 = (bounds[:, k] // BIN for k in range(4))
    wide, tall = c1 - c0 + 1, r1 - r0 + 1
    count = (wide * tall).astype(np.int64)
    tri = np.repeat(np.arange(len(bounds), dtype=np.int64), count)
    step = np.arange(int(count.sum()), dtype=np.int64) - np.repeat(np.cumsum(count) - count, count)
    row = r0.astype(np.int64)[tri] + step // wide.astype(np.int64)[tri]
    col = c0.astype(np.int64)[tri] + step % wide.astype(np.int64)[tri]
    key = row * across + col
    order = np.argsort(key, kind="stable")
    start = np.zeros(across * down + 1, np.int32)
    start[1:] = np.cumsum(np.bincount(key, minlength=across * down))
    return start, tri[order].astype(np.int32), across


def pack_alpha(textures: Sequence[SlotTexture]) -> AlphaTextures:
    """Every slot's mask in one flat array, a row of ``(offset, width, height, masked)``
    each; a slot past the list, or none at all, reads as an unmasked 1 x 1."""
    parts: list[U8Grid] = []
    table = np.zeros((max(len(textures), 1), 4), np.int32)
    offset = 0
    for k, texture in enumerate(textures):
        h, w = texture.alpha.shape
        table[k] = (offset, w, h, int(texture.masked))
        parts.append(texture.alpha.ravel())
        offset += h * w
    texels = np.concatenate(parts) if parts else np.full(1, 255, np.uint8)
    return AlphaTextures(np.ascontiguousarray(texels, np.uint8), table)


def top_hits(tris: Triangles, alpha: AlphaTextures, grid: SpriteGrid) -> Hits:
    """The highest hit per sample: the numpy reference, or its CUDA twin under ``--gpu``."""
    if jit.gpu_on():
        from mapgen.sprites import gpu

        return gpu.top_hits(tris, alpha.texels, alpha.table, grid.rows, grid.cols, BIN)
    return fill.top_hits(tris, alpha.texels, alpha.table, grid.rows, grid.cols)


def shade_samples(hits: Hits, tris: Triangles, tables: ShadingTables, grid: SpriteGrid) -> Shading:
    """Every hit's colour and normal: the numpy reference, or its CUDA twin under ``--gpu``."""
    origin = (grid.x0_cm, grid.y0_cm)
    if jit.gpu_on():
        from mapgen.sprites import gpu

        return gpu.shade_samples(hits, tris, tables, origin, SAMPLE_CM)
    return shade.shade(hits, tris, tables, origin, SAMPLE_CM)


def _sums(texel: I64Grid, weights: F32Grid, size: int) -> F64Grid:
    """``weights`` summed per texel, float even when no sample hit."""
    return np.bincount(texel, weights, minlength=size).astype(np.float64)


def resolve(
    hits: Hits,
    tris: Triangles,
    surface: SurfaceMesh,
    textures: Sequence[SlotTexture],
    grid: SpriteGrid,
) -> SpritePlanes:
    """The samples' hits shaded and gathered into texels: alpha, mean colour, mean normal,
    highest top."""
    shaded = shade_samples(hits, tris, shade.shading_tables(surface, tris, textures), grid)
    hit = np.flatnonzero(hits.tri.ravel() >= 0)
    colour = shaded.colour.reshape(-1, 3)[hit]
    normal = shaded.normal.reshape(-1, 3)[hit]
    texel = (hit // grid.cols // SUBSAMPLES) * grid.width + (hit % grid.cols) // SUBSAMPLES
    size = grid.width * grid.height
    count = np.bincount(texel, minlength=size).astype(np.float32)
    shape = (grid.height, grid.width)
    alpha = (count / np.float32(SUBSAMPLES * SUBSAMPLES)).reshape(shape)
    mean = [_sums(texel, w, size) / np.maximum(count, 1) for w in colour.T]
    summed = np.stack([_sums(texel, w, size) for w in normal.T], axis=1)
    summed /= np.maximum(np.linalg.norm(summed, axis=1, keepdims=True), 1e-9)
    summed[count == 0] = (0.0, 0.0, 1.0)
    top = np.full(size, -np.inf, np.float32)
    np.maximum.at(top, texel, hits.z.ravel()[hit])
    return SpritePlanes(
        x0_cm=grid.x0_cm,
        y0_cm=grid.y0_cm,
        alpha=alpha.astype(np.float32),
        colour=np.stack(mean, axis=1).reshape(*shape, 3).astype(np.float32),
        normal=summed.reshape(*shape, 3).astype(np.float32),
        top_cm=np.where(np.isfinite(top), top, 0.0).reshape(shape).astype(np.float32),
    )


def rasterise(surface: SurfaceMesh, textures: Sequence[SlotTexture]) -> SpritePlanes | None:
    """``surface`` from above with ``textures``; None where no slot is drawn."""
    drawable = np.array([t.kind != "skip" for t in textures] + [False], bool)
    drawn = drawable[np.minimum(surface.slots, len(textures))]
    if not drawn.any():
        return None
    grid = sprite_grid(surface.verts[np.unique(surface.tris[drawn])])
    tris = triangle_setup(surface, drawn, grid)
    hits = top_hits(tris, pack_alpha(textures), grid)
    return resolve(hits, tris, surface, textures, grid)

"""Tree crowns drawn into the render's own grid, a band at a time, from the paint store.

Each tree stamps its species' sprite, turned and scaled, at the mip whose texel is closest
to the output pixel. Taller crowns are laid over lower ones. A species the render-only mesh
pass draws is no crown. docs/map/light-and-crowns.md section 36 describes the planes a band
returns. The stamps run as a numba kernel unless ``mapgen.jit`` selects ``_stamp``, their
reference (docs/map/renders.md section 41).
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from pathlib import Path
from typing import NamedTuple, TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.gamedata.vegetation.crown_sprites import (
    CROWNS_NAME,
    MATERIAL_NONE,
    SPRITE_M,
    SPRITES_NAME,
    CrownsBlock,
    CrownSpecies,
    DecodedSprite,
    decode_records,
    decode_sprites,
)
from mapgen.jit import kernels_on
from mapgen.terrain.render_meshes import is_render_only_foliage
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I64Grid

__all__ = [
    "COVER_TOP_MIN",
    "DOME_SIGMA_M",
    "CrownBand",
    "CrownSet",
    "LitCrowns",
    "MipAtlas",
    "load_crowns",
    "meshed_species",
    "mip_atlas",
    "sprite_levels",
    "stamp_crowns",
]

#: Smoothing of a crown's top before it is lit: leaf cards are not a surface.
DOME_SIGMA_M = 0.75
#: Cover under which a texel says nothing about the crown's height.
COVER_TOP_MIN = 0.25
#: Channels of a mip: cover, cover-weighted linear rgb (3), dome height (m), top (cm).
_COVER, _RGB, _DOME, _TOP = 0, slice(1, 4), 4, 5
#: A crown's colour where its material names none.
_FALLBACK_RGB = (0.05, 0.08, 0.03)


class CrownBand(TypedDict):
    """The crowns stamped over a band: cover, linear colour, dome and top."""

    cover: F32Grid
    rgb: F32Grid
    dome_m: F32Grid
    top_cm: F32Grid


class LitCrowns(CrownBand):
    """A band's crowns with their domes lit: ``ndl`` is the dome's sun term."""

    ndl: F32Grid


class MipAtlas(NamedTuple):
    """Every species' mips in one array, as the stamp kernel reads them: ``texels`` holds
    each mip's texels, rim included, row after row, in float64, which holds any mip exactly;
    ``mips`` each mip's first texel, height and width; ``first`` each species' finest mip's
    row of ``mips`` and ``counts`` how many it has."""

    texels: F64Grid
    mips: I64Grid
    first: I64Grid
    counts: I64Grid


def mip_atlas(levels: Sequence[Sequence[NDArray[np.floating]]]) -> MipAtlas:
    """``levels`` (per species, its mips) laid out as one ``MipAtlas``."""
    every = [level for mips in levels for level in mips]
    sizes = np.array([level.shape[0] * level.shape[1] for level in every], np.int64)
    starts = np.cumsum(sizes) - sizes
    shapes = np.array([level.shape[:2] for level in every], np.int64).reshape(-1, 2)
    count = np.array([len(mips) for mips in levels], np.int64)
    channels = every[0].shape[2] if every else 6
    texels = np.concatenate([level.reshape(-1, channels) for level in every] or
                            [np.zeros((0, channels))]).astype(np.float64)  # fmt: skip
    return MipAtlas(texels, np.column_stack([starts, shapes]), np.cumsum(count) - count, count)


class CrownSet:
    """Records sorted by y, each tree's reach and crown lift, and every species' mips.

    ``mid_cm`` is a species' cover-weighted crown height, where a leaning tree's crown is
    shifted to; ``top_cm`` its highest texel, which orders the stamping. ``names`` are the
    species' names, as the paint store gives them. ``atlas()`` lays ``levels`` out for the
    kernel once, and again only when ``levels`` is replaced by another list.
    """

    def __init__(self, records: NDArray[np.void], levels: list[list[F32Grid]],
                 origins: list[tuple[float, float]], reach_cm: F32Grid, mid_cm: F32Grid,
                 top_cm: F32Grid, names: Sequence[str] = ()) -> None:  # fmt: skip
        self.records = records[np.argsort(records["y"], kind="stable")]
        self.levels, self.origins, self.names = levels, origins, list(names)
        species, rec = self.records["species"], self.records
        lean = np.hypot(rec["axis_x"], rec["axis_y"])
        self.lift_cm = mid_cm[species] * rec["scale_z"]
        self.height_cm = rec["z"] + top_cm[species] * rec["scale_z"] * rec["axis_z"]
        self.reach_cm = reach_cm[species] * rec["scale"] + top_cm[species] * rec["scale_z"] * lean
        self.max_reach_cm = float(self.reach_cm.max()) if len(rec) else 0.0
        self._atlas: tuple[list[list[F32Grid]], MipAtlas] | None = None

    def atlas(self) -> MipAtlas:
        """``levels`` as one ``MipAtlas``, kept while ``levels`` is the same list."""
        held = self._atlas
        if held is None or held[0] is not self.levels:
            held = (self.levels, mip_atlas(self.levels))
            self._atlas = held
        return held[1]


def sprite_levels(
    sprite: DecodedSprite, colours: Sequence[Sequence[float] | None]
) -> list[F32Grid]:
    """One species' mips: level 0 at ``SPRITE_M``, each next one twice as coarse."""
    cover = sprite["cover"].astype(np.float32) / 255.0
    slot = sprite["slot"]
    rgb = np.zeros((*cover.shape, 3), np.float32)
    fallback = np.array(next((c for c in colours if c is not None), _FALLBACK_RGB))
    for k in map(int, np.unique(slot)):
        if k == MATERIAL_NONE:
            continue
        colour = colours[k] if k < len(colours) and colours[k] is not None else fallback
        rgb[slot == k] = colour
    top = np.where(cover >= COVER_TOP_MIN, sprite["top_cm"].astype(np.float32), 0.0)
    dome = ndimage.gaussian_filter(cover * top / 100.0, DOME_SIGMA_M / SPRITE_M)
    level: F32Grid = np.dstack([cover, rgb * cover[..., None], dome, top]).astype(np.float32)
    out = [level]
    while max(level.shape[:2]) > 2:
        h, w = level.shape[0] + level.shape[0] % 2, level.shape[1] + level.shape[1] % 2
        even = np.pad(level, ((0, h - level.shape[0]), (0, w - level.shape[1]), (0, 0)))
        blocks = even.reshape(h // 2, 2, w // 2, 2, -1)
        level = blocks.mean((1, 3))
        level[..., _TOP] = blocks[..., _TOP].max((1, 3))
        out.append(level)
    return [np.pad(level, ((1, 1), (1, 1), (0, 0))) for level in out]


def meshed_species(species: list[CrownSpecies]) -> BoolMask:
    """Per species, whether the render-only mesh pass draws it: the coral trees."""
    return np.array([is_render_only_foliage(e.get("mesh", "")) for e in species], bool)


def load_crowns(
    paint_dir: Path, block: CrownsBlock | None, files: Container[str]
) -> CrownSet | None:
    """The crowns of a paint store, or ``None`` for a store written before they existed.

    ``block`` and ``files`` are the store's ``crowns`` block and its file names. The trees
    of a ``meshed_species`` are left out: their mesh is drawn, lit by its own top.
    """
    if not block or CROWNS_NAME not in files:
        return None
    records = decode_records((paint_dir / CROWNS_NAME).read_bytes())
    species = block["species"]
    records = records[~meshed_species(species)[records["species"]]]
    sprites = decode_sprites(
        (paint_dir / SPRITES_NAME).read_bytes(),
        [entry["sprite"] for entry in species if "sprite" in entry],
    )
    levels: list[list[F32Grid]] = []
    origins: list[tuple[float, float]] = []
    reach: list[float] = []
    mid: list[float] = []
    high: list[float] = []
    for entry, sprite in zip(species, sprites, strict=True):
        colours = [m["linear"] for m in entry["materials"]]
        levels.append(sprite_levels(sprite, colours))
        weight = levels[-1][0][..., _COVER]
        mid.append(float((weight * levels[-1][0][..., _TOP]).sum() / max(weight.sum(), 1e-6)))
        high.append(float(levels[-1][0][..., _TOP].max()))
        x0, y0 = sprite["x0_cm"], sprite["y0_cm"]
        origins.append((x0, y0))
        ys, xs = np.nonzero(sprite["cover"])
        step = SPRITE_M * 100.0
        far = np.hypot(x0 + (xs + 0.5) * step, y0 + (ys + 0.5) * step)
        reach.append(float(far.max()) + 2 * step if len(far) else 0.0)
    return CrownSet(
        records, levels, origins, np.array(reach, np.float32), np.array(mid, np.float32),
        np.array(high, np.float32), [entry.get("name", "") for entry in species],
    )  # fmt: skip


def _bilinear(level: F32Grid, u: NDArray[np.floating],
              v: NDArray[np.floating]) -> NDArray[np.floating]:  # fmt: skip
    """A zero-bordered mip sampled at texel coordinates ``(u, v)`` inside its rim."""
    w = level.shape[1]
    flat = level.reshape(-1, level.shape[2])
    x, y = u + np.float32(0.5), v + np.float32(0.5)
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    at = y0.astype(np.intp) * w + x0.astype(np.intp)
    top = flat[at] * (1 - fx) + flat[at + 1] * fx
    bottom = flat[at + w] * (1 - fx) + flat[at + w + 1] * fx
    return top * (1 - fy) + bottom * fy


def stamp_crowns(crowns: CrownSet, x_cm: F64Grid, y_cm: F64Grid, step_cm: float) -> CrownBand:
    """One band of crowns on the pixel centres ``x_cm`` by ``y_cm``, world cm ``step_cm``
    apart: cover, linear colour, dome height, top.

    ``cover`` and ``rgb`` are composited front over back, tallest last; ``dome_m`` is the
    highest crown's smoothed height above its own base, for the light; ``top_cm`` is the
    highest crown top in world cm, ``nan`` where none stands. Each pixel is placed on a
    sprite from its own centre, so a crown draws the same in any band or window.
    """
    rows, cols = len(y_cm), len(x_cm)
    x0_cm, y0_cm = float(x_cm[0]) - step_cm / 2, float(y_cm[0]) - step_cm / 2
    cover = np.zeros((rows, cols), np.float32)
    rgb = np.zeros((rows, cols, 3), np.float32)
    dome = np.zeros((rows, cols), np.float32)
    top = np.full((rows, cols), -np.inf, np.float32)
    y_hi = y0_cm + rows * step_cm
    ys = crowns.records["y"]
    lo = np.searchsorted(ys, y0_cm - crowns.max_reach_cm)
    hi = np.searchsorted(ys, y_hi + crowns.max_reach_cm, side="right")
    picked = np.arange(lo, hi)
    reach, xs = crowns.reach_cm[picked], crowns.records["x"][picked]
    near = (ys[picked] + reach >= y0_cm) & (ys[picked] - reach <= y_hi)
    near &= (xs + reach >= x0_cm) & (xs - reach <= x0_cm + cols * step_cm)
    picked = picked[near]
    order = picked[np.argsort(crowns.height_cm[picked], kind="stable")]
    planes = (cover, rgb, dome, top)
    if kernels_on() and x_cm.dtype == y_cm.dtype == np.float64:
        _stamp_compiled(crowns, order, (x_cm, y_cm), step_cm, planes)
    else:
        for i in order:
            _stamp(crowns, int(i), (x_cm, y_cm), step_cm, planes)
    return {
        "cover": cover,
        "rgb": rgb,
        "dome_m": dome,
        "top_cm": np.where(top > -np.inf, top, np.nan).astype(np.float32),
    }


def _stamp(crowns: CrownSet, i: int, centres: tuple[F64Grid, F64Grid], step_cm: float,
           planes: tuple[F32Grid, F32Grid, F32Grid, F32Grid]) -> None:  # fmt: skip
    cover, rgb, dome, top = planes
    rows, cols = cover.shape
    x_cm, y_cm = centres
    x0_cm, y0_cm = float(x_cm[0]) - step_cm / 2, float(y_cm[0]) - step_cm / 2
    tree = crowns.records[i]
    reach_cm, lift_cm = float(crowns.reach_cm[i]), float(crowns.lift_cm[i])
    species = int(tree["species"])
    scale = float(tree["scale"])
    levels = crowns.levels[species]
    cx = float(tree["x"]) + lift_cm * float(tree["axis_x"])
    cy = float(tree["y"]) + lift_cm * float(tree["axis_y"])
    c0 = max(int(np.floor((cx - reach_cm - x0_cm) / step_cm)), 0)
    c1 = min(int(np.ceil((cx + reach_cm - x0_cm) / step_cm)) + 1, cols)
    r0 = max(int(np.floor((cy - reach_cm - y0_cm) / step_cm)), 0)
    r1 = min(int(np.ceil((cy + reach_cm - y0_cm) / step_cm)) + 1, rows)
    if c0 >= c1 or r0 >= r1:
        return
    texel_cm = SPRITE_M * 100.0 * scale
    log_ratio = float(np.log2(max(step_cm / texel_cm, 1.0)))
    mip_level = int(np.clip(np.round(log_ratio), 0, len(levels) - 1))
    level = levels[mip_level]
    texel = np.float32(texel_cm * (1 << mip_level))
    ox, oy = crowns.origins[species]
    px = (x_cm[c0:c1] - cx)[None, :]
    py = (y_cm[r0:r1] - cy)[:, None]
    yaw = np.radians(float(tree["yaw"]))
    cos, sin = np.float32(np.cos(yaw)), np.float32(np.sin(yaw))
    u = (cos * px + sin * py - ox * scale) / texel
    v = (cos * py - sin * px - oy * scale) / texel
    h, w = level.shape[0] - 2, level.shape[1] - 2
    inside = (u > -0.5) & (u < w + 0.5) & (v > -0.5) & (v < h + 0.5)
    if not inside.any():
        return
    got = _bilinear(level, u[inside], v[inside])
    a = np.clip(got[:, _COVER], 0.0, 1.0)
    window = (slice(r0, r1), slice(c0, c1))
    under = cover[window][inside]
    cover[window][inside] = a + under * (1.0 - a)
    rgb[window][inside] = got[:, _RGB] + rgb[window][inside] * (1.0 - a)[:, None]
    scale_z = float(tree["scale_z"])
    dome[window][inside] = np.maximum(dome[window][inside], got[:, _DOME] * scale_z)
    rise = np.where(a >= COVER_TOP_MIN, got[:, _TOP] * scale_z * float(tree["axis_z"]), -np.inf)
    top[window][inside] = np.maximum(top[window][inside], float(tree["z"]) + rise)


def _stamp_compiled(crowns: CrownSet, order: I64Grid, centres: tuple[F64Grid, F64Grid],
                    step_cm: float, planes: tuple[F32Grid, F32Grid, F32Grid, F32Grid]) -> None:  # fmt: skip
    """``_stamp`` for every tree of ``order`` by the kernel, from ``_placements``."""
    from mapgen.terrain import kernels

    atlas = crowns.atlas()
    spans, poses = _placements(crowns, order, centres, step_cm, planes[0].shape)
    kernels.stamp(atlas.texels, atlas.mips, spans, poses, centres, COVER_TOP_MIN, planes)


def _placements(crowns: CrownSet, order: I64Grid, centres: tuple[F64Grid, F64Grid],
                step_cm: float, shape: tuple[int, ...]) -> tuple[I64Grid, F64Grid]:  # fmt: skip
    """What ``_stamp`` works out per tree before it reads a texel, for the trees of ``order``
    at once and with the same float64 operations: ``(mip, r0, r1, c0, c1)``, ``mip`` the row
    of the ``MipAtlas``, and ``(cx, cy, cos, sin, ox * scale, oy * scale, texel, scale_z,
    axis_z, z)``."""
    x_cm, y_cm = centres
    x0_cm, y0_cm = float(x_cm[0]) - step_cm / 2, float(y_cm[0]) - step_cm / 2
    atlas, rec = crowns.atlas(), crowns.records[order]

    def wide(name: str) -> F64Grid:
        return rec[name].astype(np.float64)

    reach, lift = crowns.reach_cm[order].astype(np.float64), crowns.lift_cm[order].astype(np.float64)  # fmt: skip
    species, scale = rec["species"].astype(np.int64), wide("scale")
    cx, cy = wide("x") + lift * wide("axis_x"), wide("y") + lift * wide("axis_y")
    c0 = np.maximum(np.floor((cx - reach - x0_cm) / step_cm).astype(np.int64), 0)
    c1 = np.minimum(np.ceil((cx + reach - x0_cm) / step_cm).astype(np.int64) + 1, shape[1])
    r0 = np.maximum(np.floor((cy - reach - y0_cm) / step_cm).astype(np.int64), 0)
    r1 = np.minimum(np.ceil((cy + reach - y0_cm) / step_cm).astype(np.int64) + 1, shape[0])
    texel_cm = SPRITE_M * 100.0 * scale
    finest = np.log2(np.maximum(step_cm / texel_cm, 1.0))
    level = np.clip(np.round(finest), 0, atlas.counts[species] - 1).astype(np.int64)
    texel = (texel_cm * np.left_shift(1, level)).astype(np.float32)
    origin = np.asarray(crowns.origins, np.float64).reshape(-1, 2)[species]
    yaw = np.radians(wide("yaw"))
    cos, sin = np.cos(yaw).astype(np.float32), np.sin(yaw).astype(np.float32)
    spans = np.column_stack([atlas.first[species] + level, r0, r1, c0, c1])
    poses = np.column_stack([cx, cy, cos, sin, origin[:, 0] * scale, origin[:, 1] * scale,
                             texel, wide("scale_z"), wide("axis_z"), wide("z")])  # fmt: skip
    return spans, poses.astype(np.float64)

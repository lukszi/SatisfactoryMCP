"""Tree crowns drawn into the render's own grid, a band at a time, from the crown sprites.

Each tree stamps its species' sprite (``terrain/crown_atlas.py``), turned by its yaw, scaled,
and shifted along its trunk, at the mip whose texel is nearest the output pixel. Taller crowns
are laid over lower ones. What each tree needs is worked out on the host (``crown_placements``);
the stamps are the numpy reference here, numba's ``terrain/kernels.py`` or, under ``--gpu``,
``render/gpu/crowns.py``, all to the same bits (docs/map/renders.md section 41). A species the
render-only mesh pass draws is no crown. docs/map/light-and-crowns.md section 36, "Drawing".
"""

from __future__ import annotations

from collections.abc import Container
from pathlib import Path
from typing import NamedTuple, TypedDict

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.vegetation.crown_sprites import (
    CROWNS_NAME,
    CrownsBlock,
    CrownSpecies,
    decode_records,
)
from mapgen.jit import kernels_on
from mapgen.terrain.crown_atlas import ALPHA, NORMAL, RGB, TOP, CrownAtlas
from mapgen.terrain.render_meshes import is_render_only_foliage
from mapgen.terrain.texels import Atlas, sample_atlas
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, I32Grid, I64Grid

__all__ = [
    "COVER_TOP_MIN",
    "DOME_SIGMA_M",
    "CrownBand",
    "CrownSet",
    "LitCrowns",
    "Placements",
    "crown_placements",
    "load_crowns",
    "meshed_species",
    "stamp_crowns",
    "stamp_placed",
]

#: The canopy light's smoothing of the crown tops (``lighting/spans/canopy.py``).
DOME_SIGMA_M = 0.75
#: Cover under which a pixel says nothing about the crown's height.
COVER_TOP_MIN = np.float32(0.25)

_ONE = np.float32(1.0)


class CrownBand(TypedDict):
    """The crowns stamped over a band: cover, then colour and normal times cover, laid front
    over back; the highest crown top in world cm, ``nan`` where none stands."""

    cover: F32Grid
    rgb: F32Grid
    normal: F32Grid
    top_cm: F32Grid


class LitCrowns(CrownBand):
    """A band's crowns with their sun term: ``ndl``, the scene's sun term of each normal."""

    ndl: F32Grid


class Placements(NamedTuple):
    """What a band's stamps read per tree, in their order: the atlas tile; the stamp's centre
    in world cm; ``(cos, sin, x, y, texel, rise, z, opacity)`` as float32, the tile's corner
    and texel in the tree's scaled cm, its top's rise per cm and base; the rows and columns
    ``(r0, r1, c0, c1)`` of the band it can reach."""

    tile: I32Grid
    centre: F64Grid
    pose: F32Grid
    box: I64Grid


class CrownSet:
    """Records sorted by y, each tree's reach, crown lift and height, over a ``CrownAtlas``.

    A record's ``species`` is the atlas's. ``lift_cm`` is the species' mean crown height times
    the tree's z scale, where a leaning tree's crown is shifted to; ``height_cm`` its highest
    texel in world cm, which orders the stamping. ``opacity`` scales every crown's alpha.
    """

    def __init__(self, records: NDArray[np.void], atlas: CrownAtlas, opacity: float = 1.0) -> None:
        self.records = records[np.argsort(records["y"], kind="stable")]
        self.atlas, self.opacity = atlas, np.float32(opacity)
        rec = self.records
        species = rec["species"].astype(np.int64)
        self.lift_cm = atlas.mid_cm[species] * rec["scale_z"]
        self.height_cm = rec["z"] + atlas.top_cm[species] * rec["scale_z"] * rec["axis_z"]
        self.reach_cm = atlas.widest_cm[species] * rec["scale"]
        self.max_reach_cm = float(self.reach_cm.max()) if len(rec) else 0.0

    @property
    def names(self) -> list[str]:
        return self.atlas.names

    @property
    def levels(self) -> list[list[F32Grid]]:
        """Every species' tiles, as ``CrownAtlas.levels`` hands them out and takes them back."""
        return self.atlas.levels

    @levels.setter
    def levels(self, levels: list[list[F32Grid]]) -> None:
        self.atlas.levels = levels


def meshed_species(species: list[CrownSpecies]) -> BoolMask:
    """Per species, whether the render-only mesh pass draws it: the coral trees."""
    return np.array([is_render_only_foliage(e.get("mesh", "")) for e in species], bool)


def load_crowns(
    paint_dir: Path, block: CrownsBlock | None, files: Container[str], atlas: CrownAtlas
) -> CrownSet | None:
    """The trees of a paint store over ``atlas``; ``None`` for a store without crowns.

    ``block`` and ``files`` are the store's ``crowns`` block and its file names. The trees of a
    ``meshed_species`` are left out, their mesh is drawn; so are those of a species the atlas
    has no sprite of.
    """
    if not block or CROWNS_NAME not in files:
        return None
    records = decode_records((paint_dir / CROWNS_NAME).read_bytes())
    species = block["species"]
    index = {name: k for k, name in enumerate(atlas.names)}
    to_atlas = np.array([index.get(entry["name"], -1) for entry in species], np.int64)
    mapped = to_atlas[records["species"]]
    keep = ~meshed_species(species)[records["species"]] & (mapped >= 0)
    records = records[keep]
    records["species"] = mapped[keep]
    return CrownSet(records, atlas)


def crown_placements(crowns: CrownSet, x_cm: F64Grid, y_cm: F64Grid, step_cm: float) -> Placements:
    """The trees that reach the pixel centres ``x_cm`` by ``y_cm``, lowest top first, and what
    their stamps read, in float64 until each value is cast where the stamps take it."""
    rows, cols = len(y_cm), len(x_cm)
    x0_cm, y0_cm = float(x_cm[0]) - step_cm / 2, float(y_cm[0]) - step_cm / 2
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
    rec, atlas = crowns.records[order], crowns.atlas

    def wide(name: str) -> F64Grid:
        return rec[name].astype(np.float64)

    species, scale = rec["species"].astype(np.int64), wide("scale")
    finest = np.log2(np.maximum(step_cm / (atlas.texel_cm[atlas.first[species]] * scale), 1.0))
    tile = atlas.first[species] + np.clip(np.round(finest), 0, atlas.counts[species] - 1)
    tile = tile.astype(np.int64)
    lift = crowns.lift_cm[order].astype(np.float64)
    cx, cy = wide("x") + lift * wide("axis_x"), wide("y") + lift * wide("axis_y")
    r = atlas.reach_cm[tile] * scale
    box = np.column_stack(
        [
            np.maximum(np.floor((cy - r - y0_cm) / step_cm).astype(np.int64), 0),
            np.minimum(np.ceil((cy + r - y0_cm) / step_cm).astype(np.int64) + 1, rows),
            np.maximum(np.floor((cx - r - x0_cm) / step_cm).astype(np.int64), 0),
            np.minimum(np.ceil((cx + r - x0_cm) / step_cm).astype(np.int64) + 1, cols),
        ]
    )
    yaw = np.radians(wide("yaw"))
    pose = np.column_stack(
        [
            np.cos(yaw),
            np.sin(yaw),
            atlas.corner_cm[tile, 0] * scale,
            atlas.corner_cm[tile, 1] * scale,
            atlas.texel_cm[tile] * scale,
            rec["scale_z"] * rec["axis_z"],
            rec["z"],
            np.full(len(rec), crowns.opacity),
        ]
    ).astype(np.float32)
    return Placements(tile.astype(np.int32), np.column_stack([cx, cy]), pose, box)


def stamp_crowns(crowns: CrownSet, x_cm: F64Grid, y_cm: F64Grid, step_cm: float) -> CrownBand:
    """One band of crowns on the pixel centres ``x_cm`` by ``y_cm``, world cm ``step_cm``
    apart. Each pixel is placed on a sprite from its own centre, so a crown draws the same in
    any band or window."""
    placed = crown_placements(crowns, x_cm, y_cm, step_cm)
    return stamp_placed(crowns.atlas.atlas, placed, (x_cm, y_cm))


def stamp_placed(atlas: Atlas, placed: Placements, centres: tuple[F64Grid, F64Grid]) -> CrownBand:
    """The placed trees stamped in turn over an empty band: numba's kernel where it runs,
    else the reference."""
    x_cm, y_cm = centres
    rows, cols = len(y_cm), len(x_cm)
    planes = (
        np.zeros((rows, cols), np.float32),
        np.zeros((rows, cols, 3), np.float32),
        np.zeros((rows, cols, 3), np.float32),
        np.full((rows, cols), -np.inf, np.float32),
    )
    if kernels_on():
        from mapgen.terrain import kernels

        args = (placed.tile, placed.centre, placed.pose, placed.box)
        kernels.stamp(atlas.texels, atlas.tiles, args, centres, COVER_TOP_MIN, planes)
    else:
        for k in range(len(placed.tile)):
            _stamp(atlas, placed, k, centres, planes)
    cover, rgb, normal, top = planes
    found = np.where(top > -np.inf, top, np.nan).astype(np.float32)
    return {"cover": cover, "rgb": rgb, "normal": normal, "top_cm": found}


def _stamp(
    atlas: Atlas,
    placed: Placements,
    k: int,
    centres: tuple[F64Grid, F64Grid],
    planes: tuple[F32Grid, F32Grid, F32Grid, F32Grid],
) -> None:
    """Tree ``k`` laid over the planes: every operation float32, in the kernels' order."""
    r0, r1, c0, c1 = (int(v) for v in placed.box[k])
    if r0 >= r1 or c0 >= c1:
        return
    cover, rgb, normal, top = planes
    cos, sin, ox, oy, texel, rise, z, opacity = placed.pose[k]
    dx = (centres[0][c0:c1] - placed.centre[k, 0]).astype(np.float32)[None, :]
    dy = (centres[1][r0:r1] - placed.centre[k, 1]).astype(np.float32)[:, None]
    u = (cos * dx + sin * dy - ox) / texel
    v = (cos * dy - sin * dx - oy) / texel
    t = sample_atlas(atlas, np.full(u.shape, placed.tile[k], np.int32), u, v)
    a = np.clip(t[..., ALPHA] * opacity, 0, 1)
    keep = _ONE - a
    window = (slice(r0, r1), slice(c0, c1))
    cover[window] = a + cover[window] * keep
    rgb[window] = t[..., RGB] * opacity + rgb[window] * keep[..., None]
    n = t[..., NORMAL]
    turned = np.stack([cos * n[..., 0] - sin * n[..., 1], sin * n[..., 0] + cos * n[..., 1]], -1)
    normal[window][..., :2] = turned * opacity + normal[window][..., :2] * keep[..., None]
    normal[window][..., 2] = n[..., 2] * opacity + normal[window][..., 2] * keep
    seen = a >= COVER_TOP_MIN
    rises = np.divide(t[..., TOP], t[..., ALPHA], out=np.zeros_like(a), where=seen)
    height = z + rises * rise
    top[window] = np.where(seen & (height > top[window]), height, top[window])

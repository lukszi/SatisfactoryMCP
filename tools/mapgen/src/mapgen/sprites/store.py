"""The crown sprite cache: every species' sprite and its mips packed into one atlas.

The layout is a stamp kernel's: three planes over one atlas (``colour`` RGBA8, sRGB colour
and alpha; ``normal`` two bytes, x and y over 127.5 about 127.5, z up the remainder; ``top``
uint16, crown top cm over the pivot), and one ``RECORD`` row per species and level naming
its rectangle and where its corner sits in mesh cm. A level's texel ``(r, c)`` centres on
``(x0_cm + (c + 0.5) texel_cm, y0_cm + (r + 0.5) texel_cm)``, rows along +Y, as the paint
store's sprites. A one-texel gutter round every rectangle holds its edge colour at alpha 0,
so a bilinear read inside never sees a neighbour. docs/map/light-and-crowns.md section 36,
"Crown sprites".
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy import ndimage

from mapgen.cache import CACHE_SIDECAR_NAME, read_sidecar, write_sidecar
from mapgen.common import LOCAL_DIR
from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.sprites.raster import SPRITE_CM, SpritePlanes
from satisfactory_mcp.core.arrays import F32Grid, U8Grid, U16Grid, U32Grid
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = [
    "ATLAS_NAME",
    "ATLAS_WIDTH",
    "FORMAT",
    "GUTTER",
    "RECORD",
    "SPRITES_DIR",
    "SPRITES_DIR_NAME",
    "SpriteAtlas",
    "encode_atlas",
    "level_planes",
    "mip_chain",
    "read_sprites",
    "sprite_stamp",
    "write_sprites",
]

SPRITES_DIR_NAME = "crown-sprites"
SPRITES_DIR = LOCAL_DIR / SPRITES_DIR_NAME
ATLAS_NAME = "atlas.npz"
#: The atlas layout's version: the planes, their encoding and the record.
FORMAT = 1
ATLAS_WIDTH = 2048
GUTTER = 1
#: A mip chain stops at the first level whose longer side is at most this.
MIP_LAST_SIDE = 4

#: One species' level: its rectangle in the atlas, its corner and texel in mesh cm, its
#: highest crown top (cm over the pivot), and how far from the pivot its farthest covered
#: texel reaches (cm), so a stamp can bound a tree under any yaw before it reads a texel.
RECORD = np.dtype(
    [
        ("species", "<u2"),
        ("level", "u1"),
        ("x", "<u2"),
        ("y", "<u2"),
        ("width", "<u2"),
        ("height", "<u2"),
        ("x0_cm", "<f4"),
        ("y0_cm", "<f4"),
        ("texel_cm", "<f4"),
        ("top_max_cm", "<f4"),
        ("reach_cm", "<f4"),
    ]
)


@dataclass(frozen=True)
class SpriteAtlas:
    """The packed planes, a record per species and level, each species' first record and
    level count, and the species' names in index order."""

    colour: U8Grid
    normal: U8Grid
    top: U16Grid
    records: npt.NDArray[np.void]
    first: U32Grid
    levels: U8Grid
    names: list[str]


def sprite_stamp(build: str | None) -> dict[str, object]:
    """What a sprite cache must agree with to be read: the build, the reader, the layout."""
    return {
        "game_version_pinned": build,
        "reader_version": READER_VERSIONS["crown_sprites"],
        "format": FORMAT,
        "texel_cm": SPRITE_CM,
    }


def _half(plane: F32Grid) -> F32Grid:
    """``plane`` summed over 2 x 2 blocks, padded with zeros to even sides."""
    h, w = plane.shape[:2]
    pad = [(0, h % 2), (0, w % 2)] + [(0, 0)] * (plane.ndim - 2)
    p = np.pad(plane, pad)
    return p.reshape(p.shape[0] // 2, 2, p.shape[1] // 2, 2, *p.shape[2:]).sum((1, 3))


def mip_chain(planes: SpritePlanes) -> list[SpritePlanes]:
    """``planes`` and each half-size level under it, to ``MIP_LAST_SIDE``: alpha and the
    alpha-weighted colour averaged, normals summed by alpha and renormalised, the top as
    its alpha-weighted mean. The corner stays put; each level's texel is twice the last's."""
    chain = [planes]
    while max(chain[-1].alpha.shape) > MIP_LAST_SIDE:
        last = chain[-1]
        a = last.alpha
        weight = _half(a)
        total = np.maximum(weight, np.float32(1e-9))
        normal = _half(last.normal * a[..., None])
        normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-9)
        normal[weight == 0] = (0.0, 0.0, 1.0)
        chain.append(
            SpritePlanes(
                x0_cm=last.x0_cm,
                y0_cm=last.y0_cm,
                alpha=(weight / 4.0).astype(np.float32),
                colour=(_half(last.colour * a[..., None]) / total[..., None]).astype(np.float32),
                normal=normal.astype(np.float32),
                top_cm=np.where(weight > 0, _half(last.top_cm * a) / total, 0.0).astype(np.float32),
            )
        )
    return chain


def _srgb_u8(linear: F32Grid) -> U8Grid:
    c = np.clip(linear, 0.0, 1.0)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.round(s * 255.0).astype(np.uint8)


def _texels(planes: SpritePlanes) -> tuple[U8Grid, U8Grid, U16Grid]:
    """One level as stored, ``GUTTER`` texels round it, the colour carried out from the
    nearest covered texel so a read at the edge does not fade to black."""
    g = GUTTER
    alpha = np.pad(planes.alpha, g)
    colour = np.pad(planes.colour, ((g, g), (g, g), (0, 0)))
    normal = np.pad(planes.normal, ((g, g), (g, g), (0, 0)))
    covered = alpha > 0
    if covered.any() and not covered.all():
        _d, (rows, cols) = ndimage.distance_transform_edt(~covered, return_indices=True)
        colour = colour[rows, cols]
        normal = normal[rows, cols]
    rgba = np.dstack([_srgb_u8(colour), np.round(np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    xy = np.round((np.clip(normal[..., :2], -1, 1) + 1.0) * 127.5).astype(np.uint8)
    top = np.round(np.clip(np.pad(planes.top_cm, g), 0, 65535)).astype(np.uint16)
    return rgba, xy, top


def _shelves(sizes: Sequence[tuple[int, int]], width: int) -> tuple[list[tuple[int, int]], int]:
    """Each ``(w, h)`` rectangle's corner on shelves ``width`` wide, tallest first; and the
    height used."""
    order = sorted(range(len(sizes)), key=lambda k: (-sizes[k][1], -sizes[k][0], k))
    at: list[tuple[int, int]] = [(0, 0)] * len(sizes)
    x = y = shelf = 0
    for k in order:
        w, h = sizes[k]
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        at[k] = (x, y)
        x += w
        shelf = max(shelf, h)
    return at, y + shelf


def encode_atlas(sprites: Sequence[tuple[str, SpritePlanes]]) -> SpriteAtlas:
    """Every species' mip chain packed on shelves, with its records."""
    levels: list[tuple[int, int, SpritePlanes, tuple[U8Grid, U8Grid, U16Grid]]] = []
    first = np.zeros(len(sprites), np.uint32)
    counts = np.zeros(len(sprites), np.uint8)
    for k, (_name, planes) in enumerate(sprites):
        chain = mip_chain(planes)
        first[k], counts[k] = len(levels), len(chain)
        levels += [(k, n, level, _texels(level)) for n, level in enumerate(chain)]
    sizes = [(t[0].shape[1], t[0].shape[0]) for *_rest, t in levels]
    corners, height = _shelves(sizes, ATLAS_WIDTH)
    colour = np.zeros((height, ATLAS_WIDTH, 4), np.uint8)
    normal = np.full((height, ATLAS_WIDTH, 2), 128, np.uint8)
    top = np.zeros((height, ATLAS_WIDTH), np.uint16)
    records = np.zeros(len(levels), RECORD)
    for row, ((k, n, level, (rgba, xy, tops)), (x, y)) in enumerate(zip(levels, corners)):
        h, w = tops.shape
        colour[y : y + h, x : x + w] = rgba
        normal[y : y + h, x : x + w] = xy
        top[y : y + h, x : x + w] = tops
        records[row] = (
            k,
            n,
            x + GUTTER,
            y + GUTTER,
            w - 2 * GUTTER,
            h - 2 * GUTTER,
            level.x0_cm,
            level.y0_cm,
            SPRITE_CM * 2**n,
            float(level.top_cm.max(initial=0.0)),
            _reach(level, SPRITE_CM * 2**n),
        )
    return SpriteAtlas(colour, normal, top, records, first, counts, [n for n, _p in sprites])


def _reach(level: SpritePlanes, texel_cm: float) -> float:
    """The farthest any covered texel's corner stands from the pivot, cm."""
    rows, cols = np.nonzero(level.alpha > 0)
    if not len(rows):
        return 0.0
    x = level.x0_cm + (cols + 0.5) * texel_cm
    y = level.y0_cm + (rows + 0.5) * texel_cm
    return float(np.hypot(x, y).max() + texel_cm * np.sqrt(0.5))


def level_planes(atlas: SpriteAtlas, species: int, level: int = 0) -> SpritePlanes:
    """One species' level read back out of the atlas: linear colour, unit normal."""
    rec = atlas.records[int(atlas.first[species]) + level]
    x, y, w, h = (int(rec[f]) for f in ("x", "y", "width", "height"))
    rgba = atlas.colour[y : y + h, x : x + w].astype(np.float32) / np.float32(255.0)
    xy = atlas.normal[y : y + h, x : x + w].astype(np.float32) / np.float32(127.5) - 1.0
    z = np.sqrt(np.clip(1.0 - (xy * xy).sum(-1), 0.0, 1.0))
    return SpritePlanes(
        x0_cm=float(rec["x0_cm"]),
        y0_cm=float(rec["y0_cm"]),
        alpha=rgba[..., 3],
        colour=srgb_unit_to_linear(rgba[..., :3]).astype(np.float32),
        normal=np.dstack([xy, z]).astype(np.float32),
        top_cm=atlas.top[y : y + h, x : x + w].astype(np.float32),
    )


def write_sprites(
    directory: Path, stamp: Mapping[str, object], atlas: SpriteAtlas, species: list[JsonObject]
) -> Path:
    """The atlas and its sidecar under ``directory``. The sidecar goes first and comes back
    last, so a write cut short is a miss rather than a new atlas under an old stamp."""
    directory.mkdir(parents=True, exist_ok=True)
    sidecar = directory / CACHE_SIDECAR_NAME
    sidecar.unlink(missing_ok=True)
    staging = directory / (ATLAS_NAME + ".tmp.npz")
    np.savez_compressed(
        staging,
        colour=atlas.colour,
        normal=atlas.normal,
        top=atlas.top,
        records=atlas.records,
        first=atlas.first,
        levels=atlas.levels,
    )
    os.replace(staging, directory / ATLAS_NAME)
    recorded: dict[str, object] = {
        **stamp,
        "atlas": {"width": int(atlas.colour.shape[1]), "height": int(atlas.colour.shape[0])},
        "record": [[str(p) for p in field] for field in RECORD.descr],
        "species": species,
    }
    write_sidecar(sidecar, recorded)
    return directory / ATLAS_NAME


def read_sprites(
    directory: Path, stamp: Mapping[str, object]
) -> tuple[SpriteAtlas, JsonObject] | None:
    """The cached atlas and its sidecar if the sidecar carries ``stamp``, else None."""
    recorded = read_sidecar(directory / CACHE_SIDECAR_NAME, stamp)
    if recorded is None:
        return None
    try:
        with np.load(directory / ATLAS_NAME) as found:
            planes = {k: found[k] for k in found.files}
    except (OSError, ValueError, KeyError):
        return None
    species = recorded.get("species")
    names = (
        [str(s.get("name")) for s in species if isinstance(s, dict)]
        if isinstance(species, list)
        else []
    )
    atlas = SpriteAtlas(
        planes["colour"],
        planes["normal"],
        planes["top"],
        planes["records"],
        planes["first"],
        planes["levels"],
        names,
    )
    return atlas, recorded

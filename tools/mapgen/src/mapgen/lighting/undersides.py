"""Where a crown's underside is: each tree species' from its own mesh, laid where its crown is.

The light casts a crown as a span from its underside to its top, so the sun passes beneath
it (``spans/march.py``). A species' underside is the height under which ``LEAF_LOW_SHARE`` of
its leaf area seen from above lies, as a share of its crown top; ``stamp_undersides`` lays
each tree's share on the paint store's grid where that tree's crown is the highest, as the
crown-top plane picks its tree. The Titan trees are a slab of their own (``TITAN_SLAB_M``).
docs/map/light-and-crowns.md section 29, "What casts as a span".
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from mapgen.gamedata.install import GameReader, missing_container, open_game
from mapgen.gamedata.vegetation.crown_sprites import (
    CROWNS_NAME,
    SPRITE_M,
    SPRITES_NAME,
    CrownSpecies,
    DecodedSprite,
    SpeciesMesh,
    decode_records,
    decode_sprites,
    read_species,
)
from mapgen.lighting.occluders import UNDER_SCALE, UNDER_TITAN, CrownGrid
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = [
    "CROWN_UNDERSIDE",
    "LEAF_LOW_SHARE",
    "TITAN_SLAB_M",
    "UNDER_SCALE",
    "UNDER_TITAN",
    "leaf_low_cm",
    "paint_undersides",
    "species_undersides",
    "stamp_undersides",
]

#: The share of a species' leaf area, seen from above, that hangs below its underside: the
#: lowest few leaves of a palm or a bush do not make the whole crown a column.
LEAF_LOW_SHARE = 0.05

#: A crown's underside as a share of its top's height where its mesh says nothing.
CROWN_UNDERSIDE = 0.5

#: The Titan canopy's thickness under its top, metres: the rendered-look study's value.
TITAN_SLAB_M = 12.0

#: Sprite texels at or above this cover are the crown's footprint, as the crown-top plane's.
_TOP_COVER_MIN = 64
#: Trees placed per batch.
_BATCH = 256
#: Crown tops in the stamp's sort key, a step of a quarter centimetre.
_KEY_STEPS_PER_CM = 4.0


def leaf_low_cm(mesh: SpeciesMesh, kinds: Sequence[str]) -> float | None:
    """The height over the pivot under which ``LEAF_LOW_SHARE`` of the leaf area seen from
    above lies, cm; the bark's for a species with no leaf, None for neither."""
    kind = np.array(list(kinds) + ["skip"])[np.minimum(mesh.slots, len(kinds))]
    for wanted in ("leaf", "bark"):
        tris = mesh.tris[kind == wanted]
        if len(tris):
            break
    else:
        return None
    v = mesh.verts[tris].astype(np.float64)
    a, b = v[:, 1, :2] - v[:, 0, :2], v[:, 2, :2] - v[:, 0, :2]
    area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    height = v[:, :, 2].mean(axis=1)
    order = np.argsort(height, kind="stable")
    share = np.cumsum(area[order]) / max(float(area.sum()), 1e-9)
    at = min(int(np.searchsorted(share, LEAF_LOW_SHARE)), len(order) - 1)
    return float(height[order][at])


def species_undersides(
    game: GameReader | None, species: Sequence[CrownSpecies], tops_cm: Sequence[float]
) -> F32Grid:
    """Each species' underside as a share of its crown top, 0 to 1; ``CROWN_UNDERSIDE`` for a
    species whose mesh cannot be read."""
    out = np.full(len(species), CROWN_UNDERSIDE, np.float32)
    if game is None:
        return out
    for k, (entry, top) in enumerate(zip(species, tops_cm, strict=True)):
        mesh = read_species(game, entry["mesh"]) if top > 0 else None
        low = None if mesh is None else leaf_low_cm(mesh, [m["kind"] for m in entry["materials"]])
        if low is not None:
            out[k] = np.float32(np.clip(low / top, 0.0, 1.0))
    return out


def stamp_undersides(
    records: NDArray[np.void],
    sprites: Sequence[DecodedSprite],
    under: F32Grid,
    shape: tuple[int, int],
    grid: CrownGrid,
) -> U8Grid:
    """Per texel of the crown-top plane, the underside byte of the tree whose crown is the
    highest there; 0 where none stands.

    Each tree is placed as ``crown_sprites.stamp_tops`` places it, so the texels are the
    plane's; a tie of tops keeps the lower underside.
    """
    rows, cols = shape
    step = grid["spacing_cm"]
    x0, y0 = grid["x0_cm"] - step / 2, grid["y0_cm"] - step / 2
    best = np.full(rows * cols, np.iinfo(np.int64).min, np.int64)
    share = np.round(np.clip(under, 0.0, 1.0) * UNDER_SCALE).astype(np.int64)
    for species, sprite in enumerate(sprites):
        picked = records[records["species"] == species]
        ys, xs = np.nonzero(sprite["cover"] >= _TOP_COVER_MIN)
        if not len(picked) or not len(xs):
            continue
        lx = sprite["x0_cm"] + (xs + 0.5) * SPRITE_M * 100.0
        ly = sprite["y0_cm"] + (ys + 0.5) * SPRITE_M * 100.0
        lz = sprite["top_cm"][ys, xs].astype(np.float32)
        low_first = UNDER_SCALE - share[species]
        for start in range(0, len(picked), _BATCH):
            chunk = picked[start : start + _BATCH]
            yaw = np.radians(chunk["yaw"])[:, None]
            scale, rise = chunk["scale"][:, None], chunk["scale_z"][:, None] * lz
            wx = chunk["x"][:, None] + scale * (lx * np.cos(yaw) - ly * np.sin(yaw))
            wy = chunk["y"][:, None] + scale * (lx * np.sin(yaw) + ly * np.cos(yaw))
            wx = wx + rise * chunk["axis_x"][:, None]
            wy = wy + rise * chunk["axis_y"][:, None]
            wz = chunk["z"][:, None] + rise * chunk["axis_z"][:, None]
            col = np.floor((wx - x0) / step).astype(np.int64)
            row = np.floor((wy - y0) / step).astype(np.int64)
            ok = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
            key = np.round(wz[ok].astype(np.float64) * _KEY_STEPS_PER_CM).astype(np.int64)
            np.maximum.at(best, row[ok] * cols + col[ok], key * 256 + low_first)
    have = best > np.iinfo(np.int64).min
    return np.where(have, UNDER_SCALE - (best & 255), 0).astype(np.uint8).reshape(shape)


def paint_undersides(
    paint_dir: Path,
    species: Sequence[CrownSpecies],
    shape: tuple[int, int],
    grid: CrownGrid,
    game_dir: Path | None,
) -> U8Grid | None:
    """The paint store's underside plane on its crown grid, each species' share read off its
    mesh in the install at ``game_dir``; None for a store without its trees."""
    if not (paint_dir / CROWNS_NAME).is_file() or not (paint_dir / SPRITES_NAME).is_file():
        return None
    records = decode_records((paint_dir / CROWNS_NAME).read_bytes())
    sprites = decode_sprites(
        (paint_dir / SPRITES_NAME).read_bytes(), [e["sprite"] for e in species if "sprite" in e]
    )
    game = None
    if game_dir is not None and missing_container(game_dir) is None:
        game = open_game(game_dir)
    tops = [float(sprite["top_cm"].max()) for sprite in sprites]
    return stamp_undersides(records, sprites, species_undersides(game, species, tops), shape, grid)

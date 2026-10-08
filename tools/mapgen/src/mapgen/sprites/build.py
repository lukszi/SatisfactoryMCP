"""One species' crown sprite: the mesh raster always, the game's top view where it is usable.

The raster gives every species its geometry: footprint, crown top and a normal. Where the
species' octahedral billboard holds a top view whose texel is no coarser than
``ATLAS_TEXEL_MAX_M`` and whose footprint overlaps the raster's by ``ATLAS_IOU_MIN``, the
sprite takes that view's alpha, colour and normal instead, on the raster's top.
docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.vegetation import billboards
from mapgen.gamedata.vegetation.billboards import Billboard, TopView
from mapgen.gamedata.vegetation.tree_surface import (
    SizedTextureReader,
    SurfaceMesh,
    read_surface,
    slot_textures,
)
from mapgen.sprites.align import (
    Placement,
    frame_widths,
    normal_from_halves,
    place_view,
)
from mapgen.sprites.raster import SAMPLE_CM, SPRITE_CM, SpritePlanes, rasterise
from satisfactory_mcp.core.arrays import F32Grid
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "ATLAS",
    "ATLAS_IOU_MIN",
    "ATLAS_TEXEL_MAX_M",
    "RASTER",
    "SpeciesSprite",
    "atlas_planes",
    "pad_planes",
    "species_sprite",
    "trim_planes",
]

ATLAS = "atlas"
RASTER = "mesh raster"

#: A top view coarser than this is not drawn: the finest sheet's pixel is 0.229 m, and a
#: view at its own pixel is blurred twice by the stamp's resampling.
ATLAS_TEXEL_MAX_M = 0.2
#: A top view whose footprint overlaps the raster's less than this is taken to be
#: misplaced, or of another version of the mesh.
ATLAS_IOU_MIN = 0.6
#: Texels of room round the raster for a top view's footprint.
ATLAS_PAD = 8


@dataclass(frozen=True)
class SpeciesSprite:
    """A species' sprite, the source it came from, and what was measured on the way."""

    name: str
    mesh: str
    source: str
    planes: SpritePlanes
    record: JsonObject


def pad_planes(planes: SpritePlanes, pad: int) -> SpritePlanes:
    """``planes`` with ``pad`` empty texels on every side, the corner moved to match."""
    width = ((pad, pad), (pad, pad))
    normal = np.pad(planes.normal, (*width, (0, 0)))
    normal[..., 2] = np.where(np.pad(planes.alpha, width) > 0, normal[..., 2], 1.0)
    return SpritePlanes(
        x0_cm=planes.x0_cm - pad * SPRITE_CM,
        y0_cm=planes.y0_cm - pad * SPRITE_CM,
        alpha=np.pad(planes.alpha, width),
        colour=np.pad(planes.colour, (*width, (0, 0))),
        normal=normal,
        top_cm=np.pad(planes.top_cm, width),
    )


def trim_planes(planes: SpritePlanes) -> SpritePlanes:
    """``planes`` cut to the texels it covers and one more on each side, as the raster's own
    grid is."""
    rows, cols = np.nonzero(planes.alpha > 0)
    if not len(rows):
        return planes
    r0, c0 = max(int(rows.min()) - 1, 0), max(int(cols.min()) - 1, 0)
    r1, c1 = int(rows.max()) + 2, int(cols.max()) + 2
    cut = (slice(r0, r1), slice(c0, c1))
    return SpritePlanes(
        x0_cm=planes.x0_cm + c0 * SPRITE_CM,
        y0_cm=planes.y0_cm + r0 * SPRITE_CM,
        alpha=np.ascontiguousarray(planes.alpha[cut]),
        colour=np.ascontiguousarray(planes.colour[cut]),
        normal=np.ascontiguousarray(planes.normal[cut]),
        top_cm=np.ascontiguousarray(planes.top_cm[cut]),
    )


def _filled_top(top_cm: F32Grid, alpha: F32Grid) -> F32Grid:
    """The raster's crown top, carried to every texel the top view covers and it does not:
    each takes its nearest raster texel's."""
    have = top_cm > 0
    if not have.any():
        return top_cm
    _dist, (rows, cols) = ndimage.distance_transform_edt(~have, return_indices=True)
    return np.where(alpha > 0, top_cm[rows, cols], 0.0).astype(np.float32)


def atlas_planes(raster: SpritePlanes, placed: F32Grid) -> SpritePlanes:
    """The top view's planes on the raster's grid (``place_view``'s stack): its alpha, its
    straight colour, its normal, and the raster's top."""
    alpha = np.clip(placed[..., 0], 0.0, 1.0).astype(np.float32)
    covered = np.maximum(alpha, np.float32(1e-6))[..., None]
    colour = np.where(alpha[..., None] > 0, placed[..., 1:4] / covered, 0.0)
    normal = normal_from_halves(placed[..., 4:7], alpha, raster.normal)
    return SpritePlanes(
        x0_cm=raster.x0_cm,
        y0_cm=raster.y0_cm,
        alpha=alpha,
        colour=colour.astype(np.float32),
        normal=normal,
        top_cm=_filled_top(raster.top_cm, alpha),
    )


def _mean_colour(planes: SpritePlanes) -> list[JsonValue]:
    """The alpha-weighted mean linear colour, for the record."""
    total = max(float(planes.alpha.sum()), 1e-9)
    mean = (planes.colour * planes.alpha[..., None]).sum((0, 1)) / total
    return [round(float(v), 4) for v in mean]


def _billboard_record(board: Billboard | None) -> JsonObject:
    if board is None:
        return {"billboard": None}
    return {"billboard": board.kind, "billboard_material": board.material.rsplit("/", 1)[-1]}


def _top_view_choice(
    view: TopView, raster: SpritePlanes, surface: SurfaceMesh
) -> tuple[SpritePlanes, Placement] | None:
    """The top view on the padded raster grid, and how it was placed; None without bounds."""
    if surface.bounds is None:
        return None
    widths = frame_widths(surface.verts, *surface.bounds)
    placement, placed = place_view(view, raster, widths)
    return atlas_planes(raster, placed), placement


def species_sprite(
    game: GameReader, name: str, mesh: str, texture_rgba: SizedTextureReader
) -> SpeciesSprite | None:
    """``mesh``'s sprite, or None where its mesh or every material slot is unreadable."""
    surface = read_surface(game, mesh)
    if surface is None:
        return None
    textures = slot_textures(game, surface, SAMPLE_CM, texture_rgba)
    found = rasterise(surface, textures)
    if found is None:
        return None
    raster = pad_planes(found, ATLAS_PAD)
    board = billboards.find_billboard(game, surface.materials)
    record: JsonObject = {
        **_billboard_record(board),
        "raster_area_m2": round(float(raster.alpha.sum()) * (SPRITE_CM / 100.0) ** 2, 2),
        "raster_linear": _mean_colour(raster),
        "slots": [t.kind + ("+mask" if t.masked else "") for t in textures],
    }
    view = billboards.top_view(board, texture_rgba) if board is not None else None
    choice = _top_view_choice(view, raster, surface) if view is not None else None
    if choice is None:
        return SpeciesSprite(name, mesh, RASTER, trim_planes(raster), record)
    planes, placement = choice
    record.update(
        frame_rule=placement.rule,
        frame_cm=round(placement.frame_cm, 1),
        atlas_iou=round(placement.iou, 3),
        atlas_texel_m=round(placement.texel_m, 3),
        atlas_area_m2=round(float(planes.alpha.sum()) * (SPRITE_CM / 100.0) ** 2, 2),
        atlas_linear=_mean_colour(planes),
        normal_agreement=round(_normal_agreement(planes, raster), 3),
    )
    usable = placement.texel_m <= ATLAS_TEXEL_MAX_M and placement.iou >= ATLAS_IOU_MIN
    if not usable:
        return SpeciesSprite(name, mesh, RASTER, trim_planes(raster), record)
    return SpeciesSprite(name, mesh, ATLAS, trim_planes(planes), record)


def _normal_agreement(atlas: SpritePlanes, raster: SpritePlanes) -> float:
    """The mean cosine between the two sources' normals where both cover a texel well."""
    both = (atlas.alpha > 0.5) & (raster.alpha > 0.5)
    if not both.any():
        return 0.0
    return float((atlas.normal[both] * raster.normal[both]).sum(-1).mean())

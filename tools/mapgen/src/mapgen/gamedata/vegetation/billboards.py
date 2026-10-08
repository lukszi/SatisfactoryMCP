"""The game's own billboards of a tree, and the straight-down view where one was baked.

A tree mesh carries its far-distance stand-in as a material slot. Three kinds are cooked:
the billboard generator's octahedral atlas (``MM_OctaBillboardMat``: eight views round the
tree and one from straight above, a 3 x 3 grid), impostor grids (``Imposter_Master``: views
round the tree only, at one elevation), and SpeedTree billboards (side views, and a crown from
above of a sibling variant, not the mesh's). Only the first holds the mesh's own top view.
docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.vegetation.crown_sprites import parameter_chain
from mapgen.gamedata.vegetation.tree_surface import SizedTextureReader
from satisfactory_mcp.core.arrays import F32Grid, U8Grid

__all__ = [
    "IMPOSTOR",
    "MASTERS",
    "OCTAHEDRAL",
    "OCTA_FRAMES",
    "OCTA_TOP",
    "SPEEDTREE",
    "Billboard",
    "TopView",
    "find_billboard",
    "top_view",
]

OCTAHEDRAL = "octahedral"
IMPOSTOR = "impostor"
SPEEDTREE = "speedtree"

#: Billboard kind -> (the master material it derives from, its colour and normal parameters).
MASTERS: dict[str, tuple[str, str, str]] = {
    OCTAHEDRAL: ("MM_OctaBillboardMat", "Albedo", "Normal"),
    IMPOSTOR: ("Imposter_Master", "BaseColor_Alpha", "Normal"),
    SPEEDTREE: ("MM_SpeedTreeImposter", "SpeedTree_Alb", "SpeedTree_Normal"),
}

#: The octahedral atlas: frames per side, and the (row, column) of the view from above.
OCTA_FRAMES = 3
OCTA_TOP = (2, 2)

#: The longest atlas side read: a top frame of up to 683 texels.
ATLAS_SIDE_MAX = 2048


@dataclass(frozen=True)
class Billboard:
    """A species' billboard material: its kind, path, textures and scalar parameters."""

    kind: str
    material: str
    colour: str | None
    normal: str | None
    scalar: dict[str, float]


@dataclass(frozen=True)
class TopView:
    """The octahedral atlas's frame from above, in its own texels (row 0 at the top).

    ``colour`` is the render target's linear albedo, straight; the billboard material's own
    ``Brightness`` and ``Desaturation`` make up for how a card is shaded and are not applied,
    so the view's albedo is the mesh's: the leaf textures' within a few percent where both
    are measured. ``alpha`` is the coverage. ``normal_plus`` is the normal as the render
    target kept it: each component clamped at zero, so a negative x or y reads 0 and z, which
    faces the camera, is whole.
    """

    colour: F32Grid
    alpha: F32Grid
    normal_plus: F32Grid
    billboard: Billboard


def find_billboard(game: GameReader, materials: Sequence[str | None]) -> Billboard | None:
    """The first slot whose material derives from a billboard master, octahedral first."""
    found: list[Billboard] = []
    for path in materials:
        if not path:
            continue
        chain = parameter_chain(game, path)
        parents = " ".join(p["parent"] or "" for p in chain)
        for kind, (master, colour_param, normal_param) in MASTERS.items():
            if master not in parents:
                continue
            textures = {k: v for p in reversed(chain) for k, v in p["texture"].items()}
            scalar = {k: v for p in reversed(chain) for k, v in p["scalar"].items()}
            found.append(
                Billboard(
                    kind, path, textures.get(colour_param), textures.get(normal_param), scalar
                )
            )
    order = list(MASTERS)
    return min(found, key=lambda b: order.index(b.kind)) if found else None


def _frame(image: U8Grid, cell: tuple[int, int]) -> U8Grid:
    side = image.shape[0] // OCTA_FRAMES
    row, col = cell
    return image[row * side : (row + 1) * side, col * side : (col + 1) * side]


def top_view(billboard: Billboard, texture_rgba: SizedTextureReader) -> TopView | None:
    """The octahedral billboard's view from above, or None for another kind or no texture."""
    if billboard.kind != OCTAHEDRAL or billboard.colour is None or billboard.normal is None:
        return None
    try:
        colour = texture_rgba(billboard.colour, ATLAS_SIDE_MAX)
        normal = texture_rgba(billboard.normal, ATLAS_SIDE_MAX)
    except Exception:  # an atlas that does not decode is no top view
        return None
    frame = _frame(colour, OCTA_TOP).astype(np.float32) / np.float32(255.0)
    up = _frame(normal, OCTA_TOP).astype(np.float32) / np.float32(255.0)
    if up.shape[:2] != frame.shape[:2]:
        zoom = (frame.shape[0] / up.shape[0], frame.shape[1] / up.shape[1], 1.0)
        up = ndimage.zoom(up, zoom, order=1, mode="nearest")[: frame.shape[0], : frame.shape[1]]
    return TopView(
        colour=np.ascontiguousarray(frame[..., :3], np.float32),
        alpha=np.ascontiguousarray(np.float32(1.0) - frame[..., 3], np.float32),
        normal_plus=np.ascontiguousarray(up[..., :3], np.float32),
        billboard=billboard,
    )

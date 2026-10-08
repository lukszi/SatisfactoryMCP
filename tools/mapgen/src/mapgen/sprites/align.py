"""The octahedral billboard's top view laid on the species' sprite grid.

Three conventions of the billboard generator, read off all twelve atlases against the mesh
raster (docs/map/light-and-crowns.md section 36, "Crown sprites"):

* the frame is centred on the mesh's pivot;
* the frame turned by ``np.rot90(frame, OCTA_TURNS)`` has rows along +Y and columns along
  +X, as the sprite grid has, and its normal's red is +X and green +Y;
* the frame's width is one of two measures of the mesh, the generator's setting not being
  in the cook: twice the bounds' top over the pivot, or the diameter of the sphere round the
  bounds' centre that holds every vertex. The one whose footprint overlaps the raster's
  more is taken.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from mapgen.gamedata.vegetation.billboards import TopView
from mapgen.sprites.raster import SPRITE_CM, SUBSAMPLES, SpritePlanes
from satisfactory_mcp.core.arrays import F32Grid, F64Grid

__all__ = [
    "FRAME_RULES",
    "OCTA_TURNS",
    "Placement",
    "frame_widths",
    "iou",
    "normal_from_halves",
    "place_view",
    "sample_frame",
]

#: Quarter turns, ``np.rot90``'s sense, from the frame as decoded to the sprite grid.
OCTA_TURNS = 3
#: The frame widths the generator was seen to use, by name.
FRAME_RULES = ("height", "sphere")


@dataclass(frozen=True)
class Placement:
    """How a top view sits on a sprite grid: the rule and width taken, the overlap with the
    raster's alpha, and the ground one atlas texel spans, m."""

    rule: str
    frame_cm: float
    iou: float
    texel_m: float


def frame_widths(verts: F32Grid, low: F64Grid, high: F64Grid) -> dict[str, float]:
    """Each rule's frame width, cm: ``height`` twice the bounds' top, ``sphere`` the diameter
    round the bounds' centre that holds every vertex."""
    centre = (np.asarray(low, np.float64) + np.asarray(high, np.float64)) / 2.0
    reach = float(np.linalg.norm(verts.astype(np.float64) - centre, axis=1).max())
    return {"height": 2.0 * float(high[2]), "sphere": 2.0 * reach}


def sample_frame(
    image: F32Grid, frame_cm: float, x0_cm: float, y0_cm: float, shape: tuple[int, int]
) -> F32Grid:
    """``image`` (the turned frame, h x w x channels) on a sprite grid, each texel the mean of
    ``SUBSAMPLES`` squared bilinear reads, zero off the frame."""
    side = image.shape[0]
    step = SPRITE_CM / SUBSAMPLES
    offsets = (np.arange(SUBSAMPLES) + 0.5) * step
    xs = x0_cm + (np.arange(shape[1])[:, None] * SPRITE_CM + offsets).ravel()
    ys = y0_cm + (np.arange(shape[0])[:, None] * SPRITE_CM + offsets).ravel()
    col = (xs / frame_cm + 0.5) * side - 0.5
    row = (ys / frame_cm + 0.5) * side - 0.5
    at = np.stack(
        [
            np.broadcast_to(row[:, None], (row.size, col.size)),
            np.broadcast_to(col[None, :], (row.size, col.size)),
        ]
    )
    out = np.empty((*shape, image.shape[2]), np.float32)
    for k in range(image.shape[2]):
        fine = ndimage.map_coordinates(image[..., k], at, order=1, cval=0.0)
        out[..., k] = fine.reshape(shape[0], SUBSAMPLES, shape[1], SUBSAMPLES).mean((1, 3))
    return out


def iou(a: F32Grid, b: F32Grid) -> float:
    """The soft overlap of two alphas: the sum of their minimum over that of their maximum."""
    return float(np.minimum(a, b).sum() / max(float(np.maximum(a, b).sum()), 1e-9))


def place_view(
    view: TopView, raster: SpritePlanes, widths: dict[str, float]
) -> tuple[Placement, F32Grid]:
    """The top view on the raster's grid by the rule that overlaps it more: the placement,
    and the planes ``(alpha, premultiplied colour, premultiplied normal halves)`` stacked."""
    turned = np.dstack(
        [
            np.rot90(view.alpha, OCTA_TURNS),
            np.rot90(view.colour * view.alpha[..., None], OCTA_TURNS),
            np.rot90(view.normal_plus * view.alpha[..., None], OCTA_TURNS),
        ]
    ).astype(np.float32)
    shape = raster.alpha.shape
    best: tuple[Placement, F32Grid] | None = None
    for rule in FRAME_RULES:
        frame_cm = widths[rule]
        planes = sample_frame(turned, frame_cm, raster.x0_cm, raster.y0_cm, shape)
        overlap = iou(planes[..., 0], raster.alpha)
        texel_m = frame_cm / view.alpha.shape[0] / 100.0
        if best is None or overlap > best[0].iou:
            best = (Placement(rule, frame_cm, overlap, texel_m), planes)
    assert best is not None
    return best


def normal_from_halves(halves: F32Grid, alpha: F32Grid, hint: F32Grid) -> F32Grid:
    """Unit normals from a render target that kept each component's positive half.

    z, which faces the camera, is whole; a negative x or y read 0, so what the length
    leaves over is put on the axes that read 0, shared as the raster's own normal ``hint``
    leans there, and evenly where it does not.
    """
    a = np.maximum(alpha, np.float32(1e-6))[..., None]
    x, y, z = np.moveaxis(np.clip(halves / a, 0.0, 1.0), -1, 0)
    rest = np.clip(1.0 - x * x - y * y - z * z, 0.0, None)
    wx = np.where(x <= 0.0, np.maximum(-hint[..., 0], 0.0) ** 2 + 1e-3, 0.0)
    wy = np.where(y <= 0.0, np.maximum(-hint[..., 1], 0.0) ** 2 + 1e-3, 0.0)
    share = wx + wy
    open_ = share > 0
    nx = x - np.where(open_, np.sqrt(rest * wx / np.where(open_, share, 1.0)), 0.0)
    ny = y - np.where(open_, np.sqrt(rest * wy / np.where(open_, share, 1.0)), 0.0)
    n = np.stack([nx, ny, z], axis=-1)
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
    return n.astype(np.float32)

"""A tree mesh as the crown sprite raster draws it: LOD 0 with its first UV set and vertex
normals, and each material slot's albedo and leaf mask at the size the raster samples them.
docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.materials import MaterialParameters, mesh_materials
from mapgen.gamedata.vegetation.crown_sprites import (
    ALBEDO_PARAMS,
    ALPHA_NOT_MASK,
    LEAF_ALPHA_MIN,
    MASK_CLEAR_MEAN,
    MASK_PARAMS,
    MATERIAL_NONE,
    OPAQUE_MEAN,
    material_kind,
    parameter_chain,
)
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I64Grid, U8Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView

__all__ = [
    "MASK_BINARY",
    "MASK_EDGE",
    "MASK_SIDE",
    "TEXTURE_SIDE_MAX",
    "TEXTURE_SIDE_MIN",
    "SizedTextureReader",
    "SlotTexture",
    "SurfaceMesh",
    "is_mask",
    "read_surface",
    "slot_texture",
    "slot_textures",
    "texture_side",
    "uv_scale_cm",
]

#: An asset path and the longest side wanted, to ``(H, W, 4)`` uint8 RGBA.
SizedTextureReader = Callable[[str, int], U8Grid]

#: The decoded sizes a slot's texture is read at: a leaf card under a few metres is drawn
#: from a few dozen texels, a tiled bark from no more than this.
TEXTURE_SIDE_MIN = 16
TEXTURE_SIDE_MAX = 1024
#: A leaf is read at least this large: a smaller mip has blurred its cut-out into a gradient.
MASK_SIDE = 256

#: A channel is a mask when this share of its texels lie within ``MASK_EDGE`` of 0 or 255.
MASK_BINARY = 0.85
MASK_EDGE = 32


@dataclass(frozen=True)
class SurfaceMesh:
    """One tree mesh's LOD 0: positions (cm), UV set 0, vertex normals, triangles, each
    triangle's material slot, the slot list, and the mesh's ``ExtendedBounds`` corners."""

    verts: F32Grid
    uvs: F32Grid
    normals: F32Grid
    tris: I64Grid
    slots: I64Grid
    materials: list[str | None]
    bounds: tuple[F64Grid, F64Grid] | None = None


@dataclass(frozen=True)
class SlotTexture:
    """One material slot as the raster samples it.

    ``albedo`` is linear, with the instance's ``Brightness`` and ``Saturation`` applied;
    ``alpha`` is the leaf mask (255 where the slot has none) and ``masked`` whether the raster
    tests it. A skipped slot carries a 1 x 1 texture and is never drawn.
    """

    kind: str
    albedo: F32Grid
    alpha: U8Grid
    masked: bool


def read_surface(game: GameReader, mesh: str) -> SurfaceMesh | None:
    """LOD 0 of ``mesh`` with its UVs and normals, or None where the walk did not reach them."""
    package = game.index.path_for(mesh)
    if not package:
        return None
    try:
        view = PackageView(game.store.read_path(package), game.scripts)
        export = staticmesh.static_mesh_export(view)
        if export is None:
            return None
        tail = staticmesh.render_tail(view, export)
        parsed = staticmesh.parse_render_data(tail)
        got = staticmesh.lod0_buffers(tail, parsed)
        surface = staticmesh.lod0_surface(tail, parsed)
    except Exception:  # a mesh this reader cannot open has no raster
        return None
    if got is None or surface is None:
        return None
    verts, tris, _max = got
    slots = np.full(len(tris), MATERIAL_NONE, np.int64)
    for section in parsed["lods"][0].sections:
        first = section.first_index // 3
        slots[first : first + section.triangles] = section.material
    uvs, normals = surface
    return SurfaceMesh(
        verts=np.asarray(verts, np.float32),
        uvs=uvs,
        normals=normals,
        tris=tris,
        slots=slots,
        materials=mesh_materials(view, export),
        bounds=staticmesh.extended_bounds(view, export),
    )


def uv_scale_cm(surface: SurfaceMesh, slot: int) -> float | None:
    """How many centimetres of the mesh one UV unit spans on ``slot``'s triangles: the median
    over triangles of the square root of their area ratio. None for a slot with no area."""
    tris = surface.tris[surface.slots == slot]
    if not len(tris):
        return None
    p = surface.verts[tris].astype(np.float64)
    t = surface.uvs[tris].astype(np.float64)
    world = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    uv = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    ok = (uv > 1e-9) & (world > 1e-6)
    if not ok.any():
        return None
    return float(np.median(np.sqrt(world[ok] / uv[ok])))


def texture_side(scale_cm: float | None, sample_cm: float) -> int:
    """The decoded side whose texel spans about one raster sample: a power of two, clamped."""
    if scale_cm is None:
        return TEXTURE_SIDE_MIN
    side = 2 ** round(float(np.log2(max(scale_cm / sample_cm, 1.0))))
    return int(min(max(side, TEXTURE_SIDE_MIN), TEXTURE_SIDE_MAX))


def _skip() -> SlotTexture:
    return SlotTexture("skip", np.zeros((1, 1, 3), np.float32), np.zeros((1, 1), np.uint8), False)


def is_mask(channel: U8Grid) -> bool:
    """Whether a channel reads as a cut-out: nearly every texel near 0 or near 255, and some
    of each. A subsurface or roughness channel is a gradient and fails the first test."""
    near = (channel <= MASK_EDGE) | (channel >= 255 - MASK_EDGE)
    cover = float((channel.astype(np.float32) > np.float32(255.0 * LEAF_ALPHA_MIN)).mean())
    return float(near.mean()) >= MASK_BINARY and MASK_CLEAR_MEAN < cover <= OPAQUE_MEAN


def _mask_alpha(alphas: Sequence[U8Grid], shape: tuple[int, int]) -> U8Grid | None:
    """The first channel that is a mask (``is_mask``), at ``shape``; None if none is."""
    for alpha in alphas:
        if not is_mask(alpha):
            continue
        if alpha.shape != shape:
            rows = np.arange(shape[0]) * alpha.shape[0] // shape[0]
            cols = np.arange(shape[1]) * alpha.shape[1] // shape[1]
            alpha = alpha[np.ix_(rows, cols)]
        return np.ascontiguousarray(alpha, np.uint8)
    return None


def slot_texture(
    chain: Sequence[MaterialParameters], kind: str, side: int, texture_rgba: SizedTextureReader
) -> SlotTexture:
    """A slot's albedo and mask from its material chain, read at ``side``.

    The albedo is the instance's own or its nearest parent's, as ``material_colour`` reads
    it. A bark is never masked. A leaf's mask is the first channel that is one: the packed
    ``ORMA`` map's blue (where the wind-plant master keeps the cut-out), its alpha, then the
    albedo's alpha unless that carries subsurface. A slot whose textures do not decode is
    skipped.
    """
    if kind == "skip":
        return _skip()
    textures = [p["texture"] for p in chain]
    param = next((n for t in textures for n in ALBEDO_PARAMS if n in t), None)
    if param is None:
        return _skip()
    texture = next(t[param] for t in textures if param in t)
    masks = [t[n] for t in textures for n in MASK_PARAMS if n in t][:1]
    mask_paths = [m for m in masks if isinstance(m, str)]
    if not isinstance(texture, str) or len(mask_paths) != len(masks):
        return _skip()
    if kind == "leaf":
        side = max(side, MASK_SIDE)
    try:
        rgba = texture_rgba(texture, side)
        packed = [texture_rgba(m, side) for m in mask_paths]
    except Exception:  # no decodable mip: the slot draws nothing
        return _skip()
    alphas = [channel for orma in packed for channel in (orma[..., 2], orma[..., 3])]
    if not any(mark in param for mark in ALPHA_NOT_MASK):
        alphas.append(rgba[..., 3])
    shape = (rgba.shape[0], rgba.shape[1])
    alpha = _mask_alpha(alphas, shape) if kind == "leaf" else None
    linear = srgb_unit_to_linear(rgba[..., :3].astype(np.float32) / np.float32(255.0))
    scalar = {k: v for p in reversed(chain) for k, v in p["scalar"].items()}
    linear = linear * np.float32(scalar.get("Brightness", 1.0))
    lum = linear @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    saturation = np.float32(scalar.get("Saturation", 1.0))
    linear = np.clip(lum[..., None] + (linear - lum[..., None]) * saturation, 0.0, 1.0)
    return SlotTexture(
        kind=kind,
        albedo=np.ascontiguousarray(linear, np.float32),
        alpha=np.full(shape, 255, np.uint8) if alpha is None else alpha,
        masked=alpha is not None,
    )


def slot_textures(
    game: GameReader, surface: SurfaceMesh, sample_cm: float, texture_rgba: SizedTextureReader
) -> list[SlotTexture]:
    """Every material slot of ``surface`` as the raster samples it, each texture read at the
    size whose texel spans about ``sample_cm`` on that slot's triangles."""
    out: list[SlotTexture] = []
    for slot, path in enumerate(surface.materials):
        if path is None:
            out.append(_skip())
            continue
        chain = parameter_chain(game, path)
        kind = material_kind(path, chain[0]) if chain else material_kind(path)
        side = texture_side(uv_scale_cm(surface, slot), sample_cm)
        out.append(slot_texture(chain, kind, side, texture_rgba))
    return out

"""A tree mesh as the crown sprite raster draws it: LOD 0 with its first UV set and tangent
basis, and each material slot's albedo, leaf mask, normal map, moss and spherical normals at
the size the raster samples them. docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

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
    "MOSS_RAMP",
    "MOSS_SIDE",
    "NORMAL_PARAMS",
    "PIVOT_PARAM",
    "SPHERE_PARAM",
    "TEXTURE_SIDE_MAX",
    "TEXTURE_SIDE_MIN",
    "SizedTextureReader",
    "SlotShading",
    "SlotTexture",
    "SurfaceMesh",
    "is_mask",
    "read_surface",
    "slot_shading",
    "slot_texture",
    "slot_textures",
    "tangent_normals",
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

#: Texture parameters that carry a tangent-space normal map, in the order tried.
NORMAL_PARAMS = ("Normal", "Grass Normal", "Baked Normal")
#: A bark's moss: its colour is the mean of this decoded size; where it lies is a ramp on the
#: normal's z from ``1 - Fall Off``, steep as ``Contrast / Fall Off``, these where unset.
MOSS_SIDE = 64
MOSS_RAMP = (0.5, 1.0)
#: The wind-plant master's spherical normals: how far, and about which point.
SPHERE_PARAM = "Spherical Normals Influence"
PIVOT_PARAM = "1.2 Style wind Crown Pivot"


@dataclass(frozen=True)
class SurfaceMesh:
    """One tree mesh's LOD 0: positions (cm), UV set 0, vertex normals, triangles, each
    triangle's material slot, the slot list; the tangents and bitangent signs, the vertex
    colours (RGBA8) where the mesh keeps them, and the mesh's ``ExtendedBounds`` corners."""

    verts: F32Grid
    uvs: F32Grid
    normals: F32Grid
    tris: I64Grid
    slots: I64Grid
    materials: list[str | None]
    tangents: F32Grid | None = None
    signs: F32Grid | None = None
    colours: U8Grid | None = None
    bounds: tuple[F64Grid, F64Grid] | None = None


@dataclass(frozen=True)
class SlotShading:
    """What a slot's material does to a sample beyond its albedo.

    ``normal_map`` holds tangent-space unit normals (h x w x 3) or is None. A bark with a moss
    layer wears ``moss`` (linear) where its normal's z passes ``moss_low``, fully from
    ``1 / moss_gain`` above it. ``sphere`` is how far its normals bend toward the direction
    from ``pivot_cm`` (mesh cm), as a foliage material's spherical normals do.
    """

    normal_map: F32Grid | None = None
    moss: tuple[float, float, float] | None = None
    moss_low: float = 1.0
    moss_gain: float = 0.0
    sphere: float = 0.0
    pivot_cm: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SlotTexture:
    """One material slot as the raster samples it.

    ``albedo`` is linear, with the instance's ``Brightness`` and ``Saturation`` applied;
    ``alpha`` is the leaf mask (255 where the slot has none) and ``masked`` whether the raster
    tests it; ``shading`` its normal map, moss and spherical normals. A skipped slot carries a
    1 x 1 texture and is never drawn.
    """

    kind: str
    albedo: F32Grid
    alpha: U8Grid
    masked: bool
    shading: SlotShading = field(default_factory=SlotShading)


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
    return SurfaceMesh(
        verts=np.asarray(verts, np.float32),
        uvs=surface.uvs,
        normals=surface.normals,
        tangents=surface.tangents,
        signs=surface.signs,
        colours=surface.colours,
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
        shading=slot_shading(chain, side, texture_rgba),
    )


def tangent_normals(rgba: U8Grid) -> F32Grid:
    """A two-channel normal map as unit tangent-space vectors: x and y from red and green over
    127.5 about 127.5, z what the unit length leaves."""
    xy = rgba[..., :2].astype(np.float32) / np.float32(127.5) - np.float32(1.0)
    z = np.sqrt(np.maximum(np.float32(1.0) - (xy * xy).sum(-1), np.float32(0.0)))
    n = np.dstack([xy, z])
    return np.ascontiguousarray(n / np.linalg.norm(n, axis=-1, keepdims=True), np.float32)


def slot_shading(
    chain: Sequence[MaterialParameters], side: int, texture_rgba: SizedTextureReader
) -> SlotShading:
    """A slot's normal map (``NORMAL_PARAMS``, the first found), its moss and its spherical
    normals, from the material chain. The moss is its texture's mean, tinted; how the
    master ramps it in is not in the cook, so ``MOSS_RAMP`` stands for it."""
    textures = {k: v for p in reversed(chain) for k, v in p["texture"].items()}
    scalar = {k: v for p in reversed(chain) for k, v in p["scalar"].items()}
    vector = {k: v for p in reversed(chain) for k, v in p["vector"].items()}
    normal_map = None
    path = next((textures[n] for n in NORMAL_PARAMS if textures.get(n)), None)
    if path is not None:
        try:
            normal_map = tangent_normals(texture_rgba(path, side))
        except Exception:  # an undecodable normal map leaves the vertex normals
            normal_map = None
    moss, low, gain = None, 1.0, 0.0
    moss_path = textures.get("Moss Albedo")
    if moss_path:
        try:
            mean = (
                srgb_unit_to_linear(
                    texture_rgba(moss_path, MOSS_SIDE)[..., :3].astype(np.float32)
                    / np.float32(255.0)
                )
                .reshape(-1, 3)
                .mean(0)
            )
        except Exception:  # no moss texture: the bark keeps its own colour
            mean = None
        if mean is not None:
            tint = vector.get("Moss Color Tint", (1.0, 1.0, 1.0, 1.0))
            moss = tuple(float(m * t) for m, t in zip(mean, tint[:3], strict=True))
            fall = max(scalar.get("Fall Off", MOSS_RAMP[0]), 1e-3)
            low, gain = 1.0 - fall, scalar.get("Contrast", MOSS_RAMP[1]) / fall
    pivot = vector.get(PIVOT_PARAM, (0.0, 0.0, 0.0, 0.0))
    return SlotShading(
        normal_map=normal_map,
        moss=(moss[0], moss[1], moss[2]) if moss is not None else None,
        moss_low=float(low),
        moss_gain=float(gain),
        sphere=float(scalar.get(SPHERE_PARAM, 0.0)),
        pivot_cm=(float(pivot[0]), float(pivot[1]), float(pivot[2])),
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

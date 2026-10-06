"""Tree crowns for the paint store: one top-down sprite per species, one record per tree.

A sprite is the species' LOD 0 rasterised from above: leaf cover, the top of the crown and
which material is on top, at ``SPRITE_M``. A record is a tree's position, yaw, scale and
species. docs/spatial-and-map.md section 36 describes both and how they are drawn.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import NotRequired, Required, TypeAlias, TypedDict

import numpy as np
import numpy.typing as npt

from mapgen.gamedata.ground.landscape_albedo import srgb_unit_to_linear
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.materials import material_parameters, mesh_materials
from mapgen.gamedata.maxz_raster import MaxZRaster
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I64Grid, U8Grid, U16Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView

__all__ = [
    "ALBEDO_PARAMS",
    "ALPHA_NOT_MASK",
    "BARK_MARKS",
    "CROWNS_NAME",
    "CROWN_RECORD",
    "CROWN_TOP_NAME",
    "MASK_CLEAR_MEAN",
    "MASK_PARAMS",
    "MATERIAL_NONE",
    "OPAQUE_MEAN",
    "SKIP_MARKS",
    "SPRITES_NAME",
    "SPRITE_M",
    "TAU_MAX",
    "BuiltCrowns",
    "CrownMaterial",
    "CrownSpecies",
    "CrownSprite",
    "CrownStats",
    "MaterialColour",
    "SpeciesMesh",
    "SpriteIndex",
    "build_crowns",
    "crown_records",
    "decode_records",
    "decode_sprites",
    "encode_records",
    "encode_sprites",
    "equivalent_radius_m",
    "leaf_mask",
    "material_colour",
    "material_kind",
    "optical_depths",
    "rasterise_sprite",
    "read_species",
    "species_name",
    "stamp_tops",
]


#: How a material's albedo texture is read: ``(H, W, 4)`` uint8 RGBA for an asset path.
TextureReader: TypeAlias = Callable[[str], U8Grid]


class CrownMaterial(TypedDict):
    """One material slot of a tree species: its path, kind, mean linear colour and opacity."""

    path: str | None
    kind: str
    linear: list[float] | None
    opacity: float | None


class SpriteIndex(TypedDict):
    """Where one sprite sits in the sprite blob, its size, and its corner in mesh cm."""

    offset: int
    width: int
    height: int
    x0_cm: float
    y0_cm: float


class CrownSpecies(TypedDict, total=False):
    """One tree species in the paint store's ``crowns`` block."""

    name: Required[str]
    mesh: Required[str]
    materials: Required[list[CrownMaterial]]
    radius_m: Required[float]
    top_m: Required[float]
    instances: int
    sprite: SpriteIndex


class CrownSprite(TypedDict):
    """One species seen from above: cover 0-255, crown top (cm over the pivot), top slot."""

    x0_cm: float
    y0_cm: float
    cover: U8Grid
    top_cm: U16Grid
    slot: U8Grid


class MaterialColour(TypedDict):
    """``material_colour``'s answer: the kind, mean linear albedo, mask cover and texture."""

    kind: str
    linear: list[float] | None
    opacity: float | None
    texture: NotRequired[str]


class CrownStats(TypedDict):
    """How many trees ``crown_records`` kept, and the most any of them leans."""

    instances: int
    tilt_max_deg: float


@dataclass(frozen=True)
class SpeciesMesh:
    """One tree mesh's LOD 0: vertices, triangles, each triangle's slot, and the slot list."""

    verts: F32Grid
    tris: I64Grid
    slots: I64Grid
    materials: list[str | None]


@dataclass(frozen=True)
class BuiltCrowns:
    """``build_crowns``' species table, sprites and records, and what it left out."""

    species: list[CrownSpecies]
    sprites: list[CrownSprite]
    records: npt.NDArray[np.void]
    skipped: dict[str, str]
    stats: CrownStats


CROWNS_NAME = "crowns.rec.z"
SPRITES_NAME = "crowns.sprites.z"
CROWN_TOP_NAME = "crown.i16.z"

#: Sprite texel, metres: under the render's 0.229 m, so the finest sheet resamples down.
SPRITE_M = 0.125

#: One tree: position (cm), yaw (degrees), xy and z scale, the trunk axis as a unit
#: vector (a leaning tree's crown stands off its base), species index.
CROWN_RECORD = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("yaw", "<f4"),
        ("scale", "<f4"),
        ("scale_z", "<f4"),
        ("axis_x", "<f4"),
        ("axis_y", "<f4"),
        ("axis_z", "<f4"),
        ("species", "<u2"),
    ]
)

#: Material-slot byte in a sprite where no triangle fell.
MATERIAL_NONE = 255

#: Texture parameters that carry a leaf or bark albedo, in the order tried.
ALBEDO_PARAMS = (
    "Albedo",
    "SpeedTree_Alb",
    "BaseColor",
    "Base Color",
    "Diffuse",
    "Color",
    "Albedo(RGB,SSS)",
    "Grass Albedo",
)
#: An albedo whose alpha is subsurface, not a mask.
ALPHA_NOT_MASK = ("SSS",)
#: The packed occlusion-roughness-metal-alpha map: its alpha is the leaf mask.
MASK_PARAMS = ("ORMA", "ORMA Map")
#: Never seen from above, or not geometry at all.
SKIP_MARKS = ("imposter", "_bb", "billboard", "liana", "ivy", "worldgridmaterial")
BARK_MARKS = ("bark", "trunk", "root", "stem", "wood", "moss")

#: An alpha this close to 1 everywhere, or to 0, carries no mask.
OPAQUE_MEAN = 0.995
MASK_CLEAR_MEAN = 0.02
#: An alpha texel over this is leaf.
LEAF_ALPHA_MIN = 0.33
#: Optical depth cap per triangle: an opaque card still lets a stack of cards read as a stack.
TAU_MAX = 3.0

#: Trees placed per batch in ``stamp_tops``.
_STAMP_BATCH = 256
#: Sprite cover (0-255) at which a texel has a crown top.
_TOP_COVER_MIN = 64


def species_name(mesh: str) -> str:
    return mesh.rsplit("/", 1)[-1]


def material_kind(path: str | None, parameters: Mapping[str, object] | None = None) -> str:
    """``skip``, ``bark`` or ``leaf``, by the material's name and a moss tint."""
    low = (path or "").rsplit("/", 1)[-1].lower()
    if not low or any(mark in low for mark in SKIP_MARKS):
        return "skip"
    if any(mark in low for mark in BARK_MARKS):
        return "bark"
    vectors = parameters.get("vector") if parameters else None
    if isinstance(vectors, Mapping) and "Moss Color Tint" in vectors:
        return "bark"
    return "leaf"


def rasterise_sprite(
    verts: F32Grid, tris: I64Grid, slots: I64Grid, tau: F32Grid
) -> CrownSprite | None:
    """One species from above: cover in [0, 1], crown top (cm above the pivot), top slot.

    ``slots`` is each triangle's material slot and ``tau`` each slot's optical depth, so a
    texel under three half-transparent cards is denser than one under one.
    """
    if not len(tris):
        return None
    step = SPRITE_M * 100.0
    low = np.floor(verts[:, :2].min(0) / step) - 1
    high = np.ceil(verts[:, :2].max(0) / step) + 1
    width, height = (int(v) for v in (high - low).astype(int))
    raster = _DepthRaster(width, height, (low[0] * step, low[1] * step), step, tau)
    world = verts[tris]
    for slot in np.unique(slots):
        raster.add(world[slots == slot], int(slot))
    z, top_slot, _density = raster.result()
    cover = 1.0 - np.exp(-raster.depth.reshape(height, width))
    hit = np.isfinite(z)
    return {
        "x0_cm": float(low[0] * step),
        "y0_cm": float(low[1] * step),
        "cover": np.round(cover * 255).astype(np.uint8),
        "top_cm": np.where(hit, np.clip(z, 0, 65535), 0).astype(np.uint16),
        "slot": np.where(hit, top_slot, MATERIAL_NONE).astype(np.uint8),
    }


class _DepthRaster(MaxZRaster):
    """``MaxZRaster`` that also sums each covering triangle's optical depth per texel."""

    def __init__(
        self, width: int, height: int, origin_cm: tuple[float, float], scale: float, tau: F32Grid
    ) -> None:
        super().__init__(width, height, origin_cm[0], origin_cm[1], scale, sample=0.5)
        self.tau = np.asarray(tau, np.float32)
        self.depth = np.zeros(width * height, np.float32)

    def flush(self) -> None:
        if self._idx:
            idx = np.concatenate(self._idx)
            src = np.concatenate(self._s)
            self.depth += np.bincount(idx, weights=self.tau[src], minlength=self.depth.size).astype(
                np.float32
            )
        super().flush()


def equivalent_radius_m(cover: U8Grid) -> float:
    """The radius of a solid disc that hides as much ground as the sprite does."""
    area = float(cover.sum()) / 255.0 * SPRITE_M * SPRITE_M
    return round(float(np.sqrt(area / np.pi)), 2)


def crown_records(
    trees: Mapping[str, F32Grid | F64Grid], species: Sequence[str]
) -> tuple[npt.NDArray[np.void], CrownStats]:
    """Every tree's record from its 4x4 world matrix; and how far from upright they stand."""
    index = {name: i for i, name in enumerate(species)}
    parts: list[npt.NDArray[np.void]] = []
    tilt_max = 0.0
    for mesh, given in trees.items():
        name = species_name(mesh)
        if name not in index:
            continue
        mats = np.asarray(given, np.float64)
        axes = mats[:, :3, :3]
        sx = np.linalg.norm(axes[:, 0], axis=1)
        sy = np.linalg.norm(axes[:, 1], axis=1)
        sz = np.linalg.norm(axes[:, 2], axis=1)
        up = axes[:, 2, 2] / np.maximum(sz, 1e-9)
        tilt_max = max(tilt_max, float(np.degrees(np.arccos(np.clip(up.min(), -1, 1)))))
        rec = np.zeros(len(mats), CROWN_RECORD)
        rec["x"], rec["y"], rec["z"] = mats[:, 3, 0], mats[:, 3, 1], mats[:, 3, 2]
        rec["yaw"] = np.degrees(np.arctan2(axes[:, 0, 1], axes[:, 0, 0]))
        rec["scale"] = 0.5 * (sx + sy)
        rec["scale_z"] = sz
        axis = axes[:, 2] / np.maximum(sz, 1e-9)[:, None]
        rec["axis_x"], rec["axis_y"], rec["axis_z"] = axis[:, 0], axis[:, 1], axis[:, 2]
        rec["species"] = index[name]
        parts.append(rec)
    records = np.concatenate(parts) if parts else np.zeros(0, CROWN_RECORD)
    return records, {"instances": len(records), "tilt_max_deg": round(tilt_max, 2)}


def encode_records(records: npt.NDArray[np.void]) -> bytes:
    return zlib.compress(np.ascontiguousarray(records, CROWN_RECORD).tobytes(), 6)


def decode_records(blob: bytes) -> npt.NDArray[np.void]:
    return np.frombuffer(zlib.decompress(blob), CROWN_RECORD).copy()


def encode_sprites(sprites: Sequence[CrownSprite]) -> tuple[bytes, list[SpriteIndex]]:
    """All sprites in one zlib stream (cover, top, slot planes back to back), and their index."""
    chunks: list[bytes] = []
    index: list[SpriteIndex] = []
    offset = 0
    for sprite in sprites:
        h, w = sprite["cover"].shape
        body = sprite["cover"].tobytes() + sprite["top_cm"].tobytes() + sprite["slot"].tobytes()
        index.append(
            {
                "offset": offset,
                "width": int(w),
                "height": int(h),
                "x0_cm": sprite["x0_cm"],
                "y0_cm": sprite["y0_cm"],
            }
        )
        chunks.append(body)
        offset += len(body)
    return zlib.compress(b"".join(chunks), 6), index


def decode_sprites(blob: bytes, index: Sequence[SpriteIndex]) -> list[dict]:
    """Each sprite back out of the blob: its index entry with its three planes added."""
    raw = zlib.decompress(blob)
    out: list[dict] = []
    for entry in index:
        h, w, at = entry["height"], entry["width"], entry["offset"]
        n = h * w
        cover = np.frombuffer(raw, np.uint8, n, at).reshape(h, w)
        top = np.frombuffer(raw, "<u2", n, at + n).reshape(h, w)
        slot = np.frombuffer(raw, np.uint8, n, at + 3 * n).reshape(h, w)
        out.append({**entry, "cover": cover, "top_cm": top, "slot": slot})
    return out


def stamp_tops(
    records: npt.NDArray[np.void],
    sprites: Sequence[CrownSprite],
    grid: int,
    x0_cm: float,
    y0_cm: float,
    step_cm: float,
) -> F32Grid:
    """The crown top on a coarse grid, world cm, ``nan`` where no crown: nearest, max."""
    top = np.full(grid * grid, -np.inf, np.float32)
    for species, sprite in enumerate(sprites):
        picked = records[records["species"] == species]
        ys, xs = np.nonzero(sprite["cover"] >= _TOP_COVER_MIN)
        if not len(picked) or not len(xs):
            continue
        lx = sprite["x0_cm"] + (xs + 0.5) * SPRITE_M * 100.0
        ly = sprite["y0_cm"] + (ys + 0.5) * SPRITE_M * 100.0
        lz = sprite["top_cm"][ys, xs].astype(np.float32)
        for start in range(0, len(picked), _STAMP_BATCH):
            chunk = picked[start : start + _STAMP_BATCH]
            yaw = np.radians(chunk["yaw"])[:, None]
            scale, rise = chunk["scale"][:, None], chunk["scale_z"][:, None] * lz
            wx = chunk["x"][:, None] + scale * (lx * np.cos(yaw) - ly * np.sin(yaw))
            wy = chunk["y"][:, None] + scale * (lx * np.sin(yaw) + ly * np.cos(yaw))
            wx = wx + rise * chunk["axis_x"][:, None]
            wy = wy + rise * chunk["axis_y"][:, None]
            wz = chunk["z"][:, None] + rise * chunk["axis_z"][:, None]
            col = np.floor((wx - x0_cm) / step_cm).astype(np.int64)
            row = np.floor((wy - y0_cm) / step_cm).astype(np.int64)
            ok = (col >= 0) & (col < grid) & (row >= 0) & (row < grid)
            np.maximum.at(top, row[ok] * grid + col[ok], wz[ok].astype(np.float32))
    top = top.reshape(grid, grid)
    return np.where(np.isfinite(top), top, np.nan)


def leaf_mask(alphas: Sequence[F32Grid], shape: tuple[int, int]) -> F32Grid:
    """The first alpha that is a mask, at the albedo's size: neither all opaque nor all clear,
    with some texel over ``LEAF_ALPHA_MIN``. All ones when none is."""
    for alpha in alphas:
        if not MASK_CLEAR_MEAN < alpha.mean() <= OPAQUE_MEAN:
            continue
        if alpha.shape != tuple(shape):
            rows = np.arange(shape[0]) * alpha.shape[0] // shape[0]
            cols = np.arange(shape[1]) * alpha.shape[1] // shape[1]
            alpha = alpha[np.ix_(rows, cols)]
        leaf = alpha > LEAF_ALPHA_MIN
        if leaf.any():
            return leaf.astype(np.float32)
    return np.ones(shape, np.float32)


def _parameter_chain(game: GameReader, path: str) -> list[Mapping[str, object]]:
    """The material instance's parameters, then its parents', at most four deep."""
    chain: list[Mapping[str, object]] = []
    view_path: str | None = path
    for _ in range(4):
        package = game.index.path_for(view_path) if view_path else None
        if not package:
            break
        try:
            params: Mapping[str, object] = material_parameters(
                PackageView(game.store.read_path(package), game.scripts)
            )
        except Exception:  # an unreadable parent ends the chain
            break
        chain.append(params)
        parent = params["parent"]
        view_path = parent if isinstance(parent, str) else None
    return chain


def _named(params: Mapping[str, object], kind: str) -> dict[str, object]:
    """One kind of a material's parameters (``scalar``, ``vector``, ``texture``) by name."""
    found = params.get(kind)
    if not isinstance(found, Mapping):
        return {}
    return {name: value for name, value in found.items() if isinstance(name, str)}


def material_colour(game: GameReader, path: str, texture_rgba: TextureReader) -> MaterialColour:
    """A material's kind, mean linear albedo under its own mask, and the mask's cover.

    The albedo texture is the instance's own or its nearest parent's. ``Brightness`` and
    ``Saturation`` are applied as the prototype did; nothing else in the graph is read.
    """
    chain = _parameter_chain(game, path)
    kind = material_kind(path, chain[0]) if chain else material_kind(path)
    entry: MaterialColour = {"kind": kind, "linear": None, "opacity": None}
    if entry["kind"] == "skip":
        return entry
    textures = [_named(p, "texture") for p in chain]
    param = next((n for t in textures for n in ALBEDO_PARAMS if n in t), None)
    if param is None:
        return entry
    texture = next(t[param] for t in textures if param in t)
    masks = [t[n] for t in textures for n in MASK_PARAMS if n in t][:1]
    mask_paths = [m for m in masks if isinstance(m, str)]
    if not isinstance(texture, str) or len(mask_paths) != len(masks):
        return entry  # an unresolved path decodes nothing, as an undecodable mip
    try:
        rgba = texture_rgba(texture).astype(np.float32) / 255.0
        alphas = [texture_rgba(m)[..., 3].astype(np.float32) / 255.0 for m in mask_paths]
    except Exception:  # no decodable mip: the species falls back to the canopy colour
        return entry
    if not any(mark in param for mark in ALPHA_NOT_MASK):
        alphas.append(rgba[..., 3])
    mask = leaf_mask(alphas, (rgba.shape[0], rgba.shape[1]))
    linear = srgb_unit_to_linear(rgba[..., :3])
    mean = (linear * mask[..., None]).sum((0, 1)) / max(float(mask.sum()), 1.0)
    scalar = {
        k: float(v)
        for p in reversed(chain)
        for k, v in _named(p, "scalar").items()
        if isinstance(v, (int, float))
    }
    mean = mean * scalar.get("Brightness", 1.0)
    lum = float(mean @ np.array([0.2126, 0.7152, 0.0722]))
    mean = np.clip(lum + (mean - lum) * scalar.get("Saturation", 1.0), 0.0, 1.0)
    entry.update(
        texture=texture,
        linear=[round(float(v), 5) for v in mean],
        opacity=round(float(mask.mean()), 3),
    )
    return entry


def optical_depths(slots: Sequence[MaterialColour]) -> F32Grid:
    """Each material slot's optical depth, for every slot byte: none for a skipped or empty
    slot, ``-ln(1 - opacity)`` capped at ``TAU_MAX``, a missing opacity counted opaque."""
    cap = 1.0 - np.exp(-TAU_MAX)
    depths = [
        0.0
        if s["kind"] == "skip"
        else -np.log(1.0 - min(1.0 if s["opacity"] is None else s["opacity"], cap))
        for s in slots
    ]
    return np.array(depths + [0.0] * (MATERIAL_NONE + 1 - len(slots)), np.float32)


def _species_entry(mesh: str, found: SpeciesMesh, slots: Sequence[MaterialColour],
                   sprite: CrownSprite) -> CrownSpecies:  # fmt: skip
    materials: list[CrownMaterial] = [
        {"path": p, "kind": s["kind"], "linear": s["linear"], "opacity": s["opacity"]}
        for p, s in zip(found.materials, slots, strict=True)
    ]
    return {
        "name": species_name(mesh),
        "mesh": mesh,
        "materials": materials,
        "radius_m": equivalent_radius_m(sprite["cover"]),
        "top_m": round(float(sprite["top_cm"].max()) / 100.0, 2),
    }


def build_crowns(
    game: GameReader, trees: Mapping[str, F32Grid | F64Grid], texture_rgba: TextureReader
) -> BuiltCrowns:
    """The species table, sprites and records of every tree mesh ``trees`` names."""
    colours: dict[str, MaterialColour] = {}
    species: list[CrownSpecies] = []
    sprites: list[CrownSprite] = []
    skipped: dict[str, str] = {}
    for mesh in sorted(trees):
        found = read_species(game, mesh)
        if found is None:
            skipped[species_name(mesh)] = "no LOD 0"
            continue
        slots: list[MaterialColour] = []
        for path in found.materials:
            if path and path not in colours:
                colours[path] = material_colour(game, path, texture_rgba)
            known = colours.get(path) if path else None
            slots.append(known or {"kind": "skip", "linear": None, "opacity": None})
        drawn = [s["kind"] != "skip" for s in slots]
        keep = np.array([s < len(drawn) and drawn[s] for s in found.slots], bool)
        tau = optical_depths(slots)
        sprite = rasterise_sprite(found.verts, found.tris[keep], found.slots[keep], tau)
        if sprite is None:
            skipped[species_name(mesh)] = "no drawn material"
            continue
        sprites.append(sprite)
        species.append(_species_entry(mesh, found, slots, sprite))
    records, stats = crown_records(trees, [s["name"] for s in species])
    counts = np.bincount(records["species"], minlength=len(species))
    for entry, n in zip(species, counts, strict=True):
        entry["instances"] = int(n)
    return BuiltCrowns(species, sprites, records, skipped, stats)


def read_species(game: GameReader, mesh: str) -> SpeciesMesh | None:
    """One tree mesh's LOD 0 triangles, each section's material slot, and the slot list."""
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
    except Exception:  # a tree this reader cannot open keeps the soft canopy only
        return None
    if got is None:
        return None
    verts, tris, _max = got
    slots = np.full(len(tris), MATERIAL_NONE, np.int64)
    for section in parsed["lods"][0].sections:
        first = section.first_index // 3
        slots[first : first + section.triangles] = section.material
    return SpeciesMesh(
        verts=np.asarray(verts, np.float32),
        tris=tris,
        slots=slots,
        materials=mesh_materials(view, export),
    )

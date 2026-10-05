"""The game-painted satellite style: the landscape's own paint, lit and coloured in linear light.

Ground colour is the game's baked landscape colour where it has one, else the paint-layer
weights of ``data/local/paint/`` times each layer's albedo, under the tree canopy; rocks take
their cliff family's tint and top layer, trees stand over them where their crowns reach,
arches and the render-only meshes take their own colours, and the Titan trees can be laid
over everything; then a sky-and-sun light, a highlight shoulder and Beer-Lambert water.
Every number is in ``palette/palettes/satellite-painted.json``. docs/spatial-and-map.md
sections 27 and 28 explain each step and the weak spots it addresses.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.bake import BAKE_NAME, bake_have
from mapgen.gamedata.paint import CANOPY_NAME, CROWN_NAME, META_NAME, PIGMENT_NAME
from mapgen.gamedata.rockfamily import FAMILIES
from mapgen.lighting.hillshade import sun_dot
from mapgen.palette.shore import add_foam, wet_band
from mapgen.terrain.rasters import MESH_CORAL, MESH_SHELL, TITAN_LEAVES, TITAN_TRUNK
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "ROCK_GRID_M",
    "PaintedGround",
    "bake_table",
    "biome_grid",
    "canopy_over_rock",
    "dry_land_range",
    "layer_table",
    "linear_from_oklab",
    "linear_to_srgb",
    "load_paint_meta",
    "mix_layers",
    "oklab",
    "painted_colours",
    "ramp_position",
    "rock_surface",
    "sample_titan",
    "seam_blend",
    "srgb_to_linear",
    "titan_over",
]

#: OKLab, Björn Ottosson's matrices.
_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    np.float32,
)
_M1_INV = np.linalg.inv(_M1).astype(np.float32)
_M2_INV = np.linalg.inv(_M2).astype(np.float32)

#: The rock colour's grid, coarser than the paint: it is a 25 m blur of it.
ROCK_GRID_M = 4


def srgb_to_linear(values) -> np.ndarray:
    c = np.asarray(values, np.float32) / np.float32(255.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(values) -> np.ndarray:
    c = np.clip(values, 0.0, 1.0)
    return (
        np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * 255.0
    ).astype(np.float32)


def oklab(linear) -> np.ndarray:
    return np.cbrt(np.asarray(linear, np.float32) @ _M1.T) @ _M2.T


def linear_from_oklab(lab) -> np.ndarray:
    return (np.asarray(lab, np.float32) @ _M2_INV.T) ** 3 @ _M1_INV.T


def ramp_position(height_m, lo_m: float, hi_m: float, cdf_m: np.ndarray, equalised: float):
    """Ground height to [0, 1] over dry land: part linear in metres, part equal-area."""
    linear = np.clip((height_m - lo_m) / max(hi_m - lo_m, 1e-6), 0.0, 1.0)
    quantile = np.interp(height_m, cdf_m, np.linspace(0.0, 1.0, len(cdf_m)))
    return ((1.0 - equalised) * linear + equalised * quantile).astype(np.float32)


def dry_land_range(field, lo_pct: float, hi_pct: float) -> tuple[float, float, np.ndarray]:
    """The ramp's height range and CDF, over dry land only (waterq dry, height known)."""
    height = field._height_dm[::4, ::4]
    grades = field._water_quality_raster()
    dry = height != hf.NODATA
    if grades is not None:
        dry &= grades[::4, ::4] == hf.WATER_DRY
    metres = height[dry].astype(np.float32) / hf.DM_PER_M
    lo, hi = np.percentile(metres, [lo_pct, hi_pct])
    cdf = np.percentile(metres[(metres >= lo) & (metres <= hi)], np.linspace(0, 100, 101))
    return float(lo), float(hi), cdf.astype(np.float32)


def load_paint_meta(paint_dir: Path) -> dict | None:
    try:
        return json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _plane(paint_dir: Path, meta: dict, name: str) -> np.ndarray:
    entry = meta["files"][name]
    shape = entry["shape"]
    flat_width = shape[1] * (shape[2] if len(shape) > 2 else 1)
    decode = hf.decode_i16 if entry.get("kind") == "i16" else hf.decode_u8
    grid = decode((paint_dir / name).read_bytes(), shape[0], flat_width)
    return grid.reshape(shape)


def bake_table(meta: dict) -> dict[str, np.ndarray] | None:
    """The paint layers' albedo refitted to the bake, or ``None`` for a store without one."""
    fit = meta["albedo_linear"].get("layers_bake_fit")
    if not fit or BAKE_NAME not in meta["files"]:
        return None
    return {name: np.asarray(value, np.float32) for name, value in fit.items()}


def layer_table(meta: dict, palette: dict) -> dict[str, np.ndarray]:
    """Linear albedo per paint layer, with the palette's WetSand correction applied."""
    darkening = np.float32(palette["albedo_darkening"])
    table = {
        name: np.asarray(value, np.float32) * darkening
        for name, value in meta["albedo_linear"]["layers"].items()
    }
    wet, sand = table.get("WetSand_LayerInfo"), table.get("Sand_LayerInfo")
    if wet is not None and sand is not None:
        lab = oklab(wet)
        lab[0] = oklab(sand)[0] * np.float32(palette["wet_sand_lightness_of_sand"])
        table["WetSand_LayerInfo"] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return table


def mix_layers(weights: dict[str, np.ndarray], table: dict[str, np.ndarray], shape) -> tuple:
    """Weight-normalised mix of the layers' albedos; and where any layer was painted."""
    acc = np.zeros((*shape, 3), np.float32)
    total = np.zeros(shape, np.float32)
    for name, weight in weights.items():
        if name not in table:
            continue
        w = weight.astype(np.float32) / np.float32(255.0)
        acc += w[..., None] * table[name]
        total += w
    have = total > 0
    return acc / np.maximum(total, 1e-6)[..., None], have


def seam_blend(rgb: np.ndarray, origins, size_px: int, palette: dict) -> tuple[np.ndarray, int]:
    """Soften paint steps that sit exactly on landscape-component edges.

    A component painted solid with one layer meets its neighbour in a straight 127 m line. The
    jump across every component edge is measured; where it is a step rather than a gradient,
    the colour is blended towards its own blur over ``seam_blend_m``.
    """
    rows, cols = rgb.shape[:2]
    edge = np.zeros((rows, cols), bool)
    for row, col in origins:
        r0, r1 = max(row, 0), min(row + size_px, rows)
        c0, c1 = max(col, 0), min(col + size_px, cols)
        if r0 >= r1 or c0 >= c1:
            continue
        for r in (row, row + size_px - 1):
            if 0 <= r < rows:
                edge[r, c0:c1] = True
        for c in (col, col + size_px - 1):
            if 0 <= c < cols:
                edge[r0:r1, c] = True
    tone = np.sqrt(np.clip(rgb, 0.0, 1.0))
    jump = np.zeros((rows, cols), np.float32)
    jump[:, 1:-1] = np.abs(tone[:, 2:] - tone[:, :-2]).max(-1)
    jump[1:-1, :] = np.maximum(jump[1:-1, :], np.abs(tone[2:] - tone[:-2]).max(-1))
    lo, hi = palette["seam_jump"]
    step = np.where(edge, np.clip((jump - lo) / (hi - lo), 0.0, 1.0), 0.0).astype(np.float32)
    sigma = float(palette["seam_blend_m"])
    weight = np.clip(ndimage.gaussian_filter(step, sigma) * np.sqrt(2 * np.pi) * sigma, 0.0, 1.0)
    out = rgb.copy()
    for k in range(3):
        soft = ndimage.gaussian_filter(rgb[..., k], sigma)
        out[..., k] = rgb[..., k] * (1.0 - weight) + soft * weight
    return out, int((step > 0).sum())


def biome_grid(biome: dict, rows: int, cols: int) -> np.ndarray:
    """The biome raster's index under every texel of the 1 m grid. Nearest."""
    width = biome["width"]
    r = np.clip(((np.arange(rows) + 0.5) * width / rows).astype(np.int64), 0, width - 1)
    c = np.clip(((np.arange(cols) + 0.5) * width / cols).astype(np.int64), 0, width - 1)
    return biome["area"][np.ix_(r, c)]


class PaintedGround:
    """Everything the painted style samples per band, built once from the paint store."""

    def __init__(self, paint_dir: Path, palette: dict, field, biome: dict, area_names: list[str]):
        meta = load_paint_meta(paint_dir)
        if meta is None:
            raise FileNotFoundError(f"no paint store at {paint_dir}")
        self.meta, self.palette = meta, palette
        grid = meta["grid"]
        rows, cols = grid["height"], grid["width"]
        table = bake_table(meta) if palette.get("ground") == "bake" else None
        self.baked = table is not None
        table = table if self.baked else layer_table(meta, palette)
        weights = {
            entry["layer"]: _plane(paint_dir, meta, name)
            for name, entry in meta["files"].items()
            if "layer" in entry
        }
        albedo, have = mix_layers(weights, table, (rows, cols))
        darkening = np.float32(1.0 if self.baked else palette["albedo_darkening"])
        for name, value in meta["albedo_linear"]["overlays"].items():
            if name in weights:
                w = (weights[name].astype(np.float32) / 255.0)[..., None]
                albedo = albedo * (1.0 - w) + np.asarray(value, np.float32) * darkening * w
        del weights
        if not self.baked:
            albedo = self._pigment(paint_dir, albedo, rows, cols)
        albedo, self.seam_texels = seam_blend(
            albedo, meta["components"], meta["component_px"], palette
        )
        index = biome_grid(biome, rows, cols)
        bake_w = None
        if self.baked:
            albedo, have, bake_w = self._bake(paint_dir, albedo, have)
        albedo = self._fallback(albedo, have, index, len(area_names))
        albedo = self._biome_tint(albedo, index, area_names, bake_w)
        del bake_w
        self.albedo = [albedo[..., k].astype(np.float16) for k in range(3)]
        self.rock = self._rock(albedo)
        del albedo
        self.canopy = _plane(paint_dir, meta, CANOPY_NAME)
        self.crown = _plane(paint_dir, meta, CROWN_NAME) if CROWN_NAME in meta["files"] else None
        self._families(meta.get("rock_families") or {})
        # Render-grid rasters the pipeline attaches: the direct pass's family plane, and the
        # Titan tree raster as (z cm, class, factor, row0, col0).
        self.rock_family = None
        self.titan = None
        titan = palette.get("titan_trees") or {}
        self.titan_rgb = {
            TITAN_LEAVES: srgb_to_linear(titan.get("leaves", (0, 0, 0))),
            TITAN_TRUNK: srgb_to_linear(titan.get("trunk", (0, 0, 0))),
        }
        self.canopy_rgb = np.asarray(meta["albedo_linear"]["canopy"], np.float32) * np.float32(
            palette["canopy_dark"]
        )
        self.mesh_rgb = {
            MESH_CORAL: srgb_to_linear(palette["mesh_colours"]["coral"]),
            MESH_SHELL: srgb_to_linear(palette["mesh_colours"]["shell"]),
        }
        self.seabed_coral = srgb_to_linear(palette["mesh_colours"]["coral_seabed"])
        water = palette["water"]
        self.water = {
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
            "deep": srgb_to_linear(water["deep"]),
            "deep_tau_m": np.float32(water["deep_tau_m"]),
            "bed": np.float32(water["bed_wet"]),
            "inland_floor": np.float32(water.get("inland_floor", 0.0)),
        }
        lo, hi, cdf = dry_land_range(field, palette["ramp_lo_pct"], palette["ramp_hi_pct"])
        self.ramp = (lo, hi, cdf)

    def _families(self, families: dict) -> None:
        """Lookup tables by family code: the rock tint, the top layer and whether there is one."""
        n = len(FAMILIES)
        self.family_tint = np.ones((n, 3), np.float32)
        self.family_top = np.zeros((n, 3), np.float32)
        self.family_has_top = np.zeros(n, np.float32)
        for name, entry in families.items():
            if name not in FAMILIES:
                continue
            code = FAMILIES.index(name)
            if entry.get("tint"):
                self.family_tint[code] = entry["tint"]
            if entry.get("top"):
                self.family_top[code] = entry["top"]
                self.family_has_top[code] = 1.0

    def _bake(self, paint_dir, albedo, have):
        """The bake where it exists, feathered over ``have_blur_m`` into the paint mix."""
        rgb = _plane(paint_dir, self.meta, BAKE_NAME)
        ok = bake_have(rgb)
        soft = ndimage.gaussian_filter(ok.astype(np.float32), self.palette["have_blur_m"])
        w = (np.clip(soft * 2.0 - 1.0, 0.0, 1.0) * ok).astype(np.float32)
        for start in range(0, albedo.shape[0], 512):
            block = slice(start, start + 512)
            weight = w[block][..., None]
            albedo[block] = albedo[block] * (1.0 - weight) + srgb_to_linear(rgb[block]) * weight
        self.bake_share = float(ok.mean())
        return albedo, have | ok, w

    def _pigment(self, paint_dir, albedo, rows, cols):
        strength = np.float32(self.palette["pigment"])
        if not strength:
            return albedo
        texture = srgb_to_linear(_plane(paint_dir, self.meta, PIGMENT_NAME))
        side = texture.shape[0]
        coords = (np.arange(rows, dtype=np.float32) + 0.5) * side / rows - 0.5
        rr, cc = np.meshgrid(
            coords, (np.arange(cols, dtype=np.float32) + 0.5) * side / cols - 0.5, indexing="ij"
        )
        for k in range(3):
            tint = ndimage.map_coordinates(texture[..., k], [rr, cc], order=1, mode="nearest")
            albedo[..., k] *= (1.0 - strength) + strength * tint
        return albedo

    def _fallback(self, albedo, have, index, areas: int):
        """Off the landscape: the median paint of the biome, blurred, faded in over the edge."""
        p = self.palette
        sample = (slice(None, None, 4), slice(None, None, 4))
        idx, ok = index[sample], have[sample]
        flat = albedo[sample]
        global_median = np.median(flat[ok], axis=0) if ok.any() else np.full(3, 0.2, np.float32)
        medians = np.tile(global_median, (areas, 1)).astype(np.float32)
        for i in np.unique(idx[ok]):
            picked = flat[ok & (idx == i)]
            if len(picked) >= 50:
                medians[i] = np.median(picked, axis=0)
        fallback = medians[index]
        for k in range(3):
            fallback[..., k] = ndimage.gaussian_filter(fallback[..., k], p["fallback_blur_m"])
        weight = ndimage.gaussian_filter(have.astype(np.float32), p["have_blur_m"])[..., None]
        return albedo * weight + fallback * (1.0 - weight)

    def _biome_tint(self, albedo, index, area_names: list[str], keep=None):
        """A subtle per-biome OKLab hue offset, so biomes painted with one layer separate.

        ``keep`` is where the colour is the game's own bake, which is left as it is.
        """
        p = self.palette
        strength = np.float32(p["biome_tint_strength"])
        offsets = np.zeros((len(area_names), 2), np.float32)
        for i, name in enumerate(area_names):
            offsets[i] = p["biome_tint_ab"].get(name, (0.0, 0.0))
        shift = offsets[index] * strength
        for k in range(2):
            shift[..., k] = ndimage.gaussian_filter(shift[..., k], p["biome_tint_blur_m"])
        out = np.empty_like(albedo)
        for start in range(0, albedo.shape[0], 512):
            block = slice(start, start + 512)
            lab = oklab(np.clip(albedo[block], 1e-7, None))
            lab[..., 1:] += shift[block]
            out[block] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
            if keep is not None:
                weight = keep[block][..., None]
                out[block] = out[block] * (1.0 - weight) + albedo[block] * weight
        return out

    def _rock(self, albedo):
        """Rock colour on a coarse grid: the game's rock albedo, tinted by the ground around."""
        p = self.palette
        step = ROCK_GRID_M
        rock_lab = oklab(np.asarray(self.meta["albedo_linear"]["rock"], np.float32))
        near = np.stack(
            [
                ndimage.gaussian_filter(albedo[..., k], p["rock_tint_blur_m"])[::step, ::step]
                for k in range(3)
            ],
            -1,
        )
        lab = oklab(np.clip(near, 1e-7, None))
        lab[..., 0] = (
            rock_lab[0] * (1 - p["rock_tint_lightness"]) + lab[..., 0] * p["rock_tint_lightness"]
        )
        lab[..., 0] += np.float32(p["rock_lightness_add"])
        lab[..., 1:] = (
            rock_lab[1:] * (1 - p["rock_tint_chroma"]) + lab[..., 1:] * p["rock_tint_chroma"]
        )
        rock = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        return [rock[..., k].astype(np.float32) for k in range(3)]


def _unit_luminance(colour) -> np.ndarray:
    c = np.asarray(colour, np.float32)
    return c / (c @ np.array([0.2126, 0.7152, 0.0722], np.float32))


def _light(p: dict, ndl, ndl_flat) -> np.ndarray:
    """Sky plus sun, equal to one on flat ground."""
    ambient = np.float32(p["ambient"])
    return (
        ambient * _unit_luminance(p["sky"])
        + (1 - ambient) * _unit_luminance(p["sun"]) * (ndl / ndl_flat)[..., None]
    )


def rock_surface(rock_rgb, scene: dict, ground: PaintedGround) -> np.ndarray:
    """Rock in its cliff family's tint, with the family's top layer on its up-facing faces."""
    if ground.rock_family is None:
        return rock_rgb
    band, *_sheet, spacing_m = scene["grid"]
    code = np.asarray(ground.rock_family[band])
    rgb = rock_rgb * ground.family_tint[code]
    d_south, d_east = np.gradient(scene["z_m"], spacing_m)
    nz = 1.0 / np.sqrt(1.0 + d_east * d_east + d_south * d_south)
    lo, hi = ground.palette["rock_top"]["up"]
    up = ndimage.uniform_filter(np.clip((nz - lo) / (hi - lo), 0.0, 1.0), 3)
    weight = (up * ground.family_has_top[code])[..., None]
    return rgb * (1.0 - weight) + ground.family_top[code] * weight


def canopy_over_rock(g, canopy, rock, scene: dict, ground: PaintedGround, sample):
    """The canopy laid over rock wherever the drawn surface is no higher than a crown top."""
    if ground.crown is None:
        return g
    crown_m = sample(ground.crown) / np.float32(hf.DM_PER_M)
    seen = (scene["z_m"] <= crown_m)[..., None]
    cover = canopy * rock * seen
    return g * (1.0 - cover) + ground.canopy_rgb * cover


def sample_titan(titan, sheet) -> tuple | None:
    """The Titan tree raster bilinear on this band: ``(z m, cover, class)`` or ``None``."""
    z_cm, cls, factor, row0, col0 = titan
    lo, hi, c0, c1 = sheet
    fr = (np.arange(lo, hi, dtype=np.float32) + 0.5) / factor - 0.5 - row0
    fc = (np.arange(c0, c1, dtype=np.float32) + 0.5) / factor - 0.5 - col0
    r_lo, r_hi = max(int(np.floor(fr[0])), 0), min(int(np.floor(fr[-1])) + 2, cls.shape[0])
    c_lo, c_hi = max(int(np.floor(fc[0])), 0), min(int(np.floor(fc[-1])) + 2, cls.shape[1])
    if r_lo >= r_hi or c_lo >= c_hi:
        return None
    cut = np.asarray(cls[r_lo:r_hi, c_lo:c_hi])
    if not cut.any():
        return None
    height = np.asarray(z_cm[r_lo:r_hi, c_lo:c_hi], np.float32) / np.float32(100.0)
    have = (cut > 0).astype(np.float32)
    r = np.clip(fr - r_lo, 0, cut.shape[0] - 1)
    c = np.clip(fc - c_lo, 0, cut.shape[1] - 1)
    r0, c0_ = (
        np.minimum(r.astype(np.int64), cut.shape[0] - 2),
        np.minimum(c.astype(np.int64), cut.shape[1] - 2),
    )
    r0, c0_ = np.maximum(r0, 0), np.maximum(c0_, 0)
    tr, tc = np.clip(r - r0, 0, 1)[:, None], np.clip(c - c0_, 0, 1)[None, :]
    cover = np.zeros((len(fr), len(fc)), np.float32)
    weighted = np.zeros_like(cover)
    for dr, wr in ((0, 1.0 - tr), (1, tr)):
        for dc, wc in ((0, 1.0 - tc), (1, tc)):
            rr = np.minimum(r0 + dr, cut.shape[0] - 1)[:, None]
            cc = np.minimum(c0_ + dc, cut.shape[1] - 1)[None, :]
            w = wr * wc * have[rr, cc]
            cover += w
            weighted += w * height[rr, cc]
    z = weighted / np.maximum(cover, 1e-6)
    nearest = cut[np.rint(r).astype(np.int64)[:, None], np.rint(c).astype(np.int64)[None, :]]
    return z, cover, nearest


def titan_over(out, scene: dict, ground: PaintedGround) -> np.ndarray:
    """The Titan trees over the finished pixel at the style's opacity; 0 turns them off."""
    p = ground.palette
    opacity = np.float32((p.get("titan_trees") or {}).get("opacity", 0.0))
    if ground.titan is None or not opacity:
        return out
    _band, lo, hi, c0, c1, spacing_m = scene["grid"]
    found = sample_titan(ground.titan, (lo, hi, c0, c1))
    if found is None:
        return out
    z_t, cover, cls = found
    above = cover * (z_t >= scene["z_m"] - np.float32(0.5))
    surface = np.where(cover > 0, z_t, scene["z_m"])
    albedo = np.zeros(out.shape, np.float32)
    for which, rgb in ground.titan_rgb.items():
        albedo = np.where((cls == which)[..., None], rgb, albedo)
    lit = albedo * _light(p, sun_dot(surface, spacing_m), scene["ndl_flat"]) * p["exposure"]
    alpha = (opacity * np.clip(above, 0.0, 1.0))[..., None]
    return out * (1.0 - alpha) + lit * alpha


def painted_colours(scene: dict, ground: PaintedGround, sample, sample_rock) -> np.ndarray:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    p = ground.palette
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    canopy = np.clip(sample(ground.canopy) / 255.0 * p["canopy_gain"], 0.0, 1.0)[..., None]
    g = albedo * (1.0 - canopy) + ground.canopy_rgb * canopy
    rock_rgb = rock_surface(
        np.stack([sample_rock(plane) for plane in ground.rock], -1), scene, ground
    )
    rock = scene["rock_weight"][..., None]
    g = g * (1.0 - rock) + rock_rgb * rock
    g = canopy_over_rock(g, canopy, rock, scene, ground, sample)
    mesh_w = scene.get("mesh_weight")
    if mesh_w is not None and mesh_w.any():
        cls = scene["mesh_class"]
        colour = rock_rgb.copy()
        for which, rgb in ground.mesh_rgb.items():
            colour = np.where((cls == which)[..., None], rgb, colour)
        under = (cls == MESH_CORAL) & (scene["water"]["depth_m"] > 0)
        colour = np.where(under[..., None], ground.seabed_coral, colour)
        g = g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]

    lab = oklab(np.clip(g, 1e-7, None))
    lab[..., 1:] *= np.float32(p["chroma_gain"])
    lo, hi, cdf = ground.ramp
    lab[..., 0] += np.float32(p["altitude_lift"]) * ramp_position(
        scene["z_m"], lo, hi, cdf, p["ramp_equalised"]
    )
    g = np.clip(linear_from_oklab(lab), 0.0, 1.0)

    borrow = scene["borrow"]
    borrow = np.where(borrow < 1.0, 1.0 + (borrow - 1.0) * np.float32(p["borrow_ink_damp"]), borrow)
    light = _light(p, scene["ndl"], scene["ndl_flat"])
    exposure = np.float32(p["exposure"])
    lit = g * light * (exposure * borrow)[..., None]

    water = scene["water"]
    lit = wet_band(lit, water, p["shore"].get("wet_band"))
    w = ground.water
    depth = water["depth_m"][..., None]
    floor = w["inland_floor"] * (1.0 - water["ocean"])[..., None]
    transmit = np.exp(-w["k"] * depth) * (1.0 - floor)
    bed = g * exposure * w["bed"]
    under = bed * transmit + w["body"] * (1.0 - transmit) + w["sky"]
    open_sea = 1.0 - np.exp(-depth / w["deep_tau_m"])
    under = under * (1.0 - open_sea) + w["deep"] * open_sea
    cover = water["cover"][..., None]
    out = lit * (1.0 - cover) + under * cover
    stroke = np.float32(p["shore"]["stroke"])
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, p["shore"].get("foam"), np.float32(1.0))
    out = titan_over(out, scene, ground)

    shoulder = np.float32(p["shoulder"])
    over = np.maximum(out - shoulder, 0.0)
    out = np.where(
        out < shoulder, out, shoulder + (1 - shoulder) * (1 - np.exp(-over / (1 - shoulder)))
    )
    return linear_to_srgb(out)

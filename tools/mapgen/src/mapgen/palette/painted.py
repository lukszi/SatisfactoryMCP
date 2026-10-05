"""The game-painted satellite style: the landscape's own paint, lit and coloured in linear light.

Ground colour is the game's baked landscape colour where it has one (the paint store's bake,
or a ``GroundBake`` handed in), else the paint-layer weights of ``data/local/paint/`` times
each layer's albedo, tinted by the PigmentMap; a per-layer colour transfer to calibrated
targets; under the tree canopy; rocks take their cliff family's tint and top layer, trees stand
over them where their crowns reach, arches and the render-only meshes take their own colours,
and the Titan trees can be laid over everything; then a sky-and-sun light, an exposure gain
with a soft shoulder and Beer-Lambert water over a seabed that carries the coral carpet. Every
number is in ``palette/palettes/satellite-painted.json``. docs/spatial-and-map.md sections 27
and 30 to 32.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.bake import BAKE_NAME, bake_have
from mapgen.gamedata.carpet import COVER_NAME, TOP_NAME
from mapgen.gamedata.paint import CANOPY_NAME, CROWN_NAME, META_NAME, PIGMENT_NAME
from mapgen.gamedata.rockfamily import FAMILIES
from mapgen.gamedata.waterbodies import CLASSES, OCEAN, WATER_BODIES_NAME, classify
from mapgen.palette.calibration import (
    area_ids,
    display_to_ground,
    display_to_linear,
    layer_transfer,
    median_lab,
    sampled_rgb,
    scoped_planes,
    split_weight,
    tone,
    transfer_op,
)
from mapgen.palette.colour import (
    LUMA,
    flat_light,
    linear_from_oklab,
    linear_to_srgb,
    oklab,
    srgb_to_linear,
)
from mapgen.palette.shore import OCEAN_LEVEL_M, add_foam, optical_depth, wet_band
from mapgen.palette.trees import over_crowns, sample_titan, titan_over
from mapgen.terrain.crowns import load_crowns
from mapgen.terrain.rasters import MESH_CORAL, MESH_SHELL, MESH_TERRACE, TITAN_LEAVES, TITAN_TRUNK
from mapgen.terrain.sample import ClassMix, class_taps
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "ROCK_GRID_M",
    "GroundBake",
    "PaintedGround",
    "area_ids",
    "bake_table",
    "biome_grid",
    "canopy_over_rock",
    "display_to_ground",
    "display_to_linear",
    "dry_land_range",
    "ground_albedo",
    "layer_table",
    "layer_transfer",
    "linear_from_oklab",
    "linear_to_srgb",
    "load_carpet",
    "load_paint_meta",
    "load_water_bodies",
    "mix_layers",
    "oklab",
    "over_crowns",
    "painted_colours",
    "ramp_position",
    "rock_surface",
    "sample_titan",
    "seam_blend",
    "split_weight",
    "srgb_to_linear",
    "titan_over",
    "tone",
    "transfer_op",
    "water_table",
]

#: The rock colour's grid, coarser than the paint: it is a 25 m blur of it.
ROCK_GRID_M = 4

#: One row per water class: absorption, body, deep colour, deep tau, turbidity, bed tint.
WATER_TABLE_COLUMNS = (3, 3, 3, 1, 1, 3)


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


def load_water_bodies(paint_dir: Path, meta: dict) -> dict | None:
    """The store's water actors and hot-spring terraces; None for a store that predates them."""
    if WATER_BODIES_NAME not in meta.get("files", {}):
        return None
    return json.loads((paint_dir / WATER_BODIES_NAME).read_text(encoding="utf-8"))


def water_table(palette: dict) -> np.ndarray:
    """``CLASSES`` rows of linear optics; a class the palette leaves out draws as the ocean."""
    water = palette["water"]
    ocean = {
        "k_per_m": water["k_per_m"],
        "body": water["body"],
        "deep": water["deep"],
        "deep_tau_m": water["deep_tau_m"],
        "turbidity": 0.0,
        "bed_tint": [1.0, 1.0, 1.0],
    }
    rows = []
    for name in CLASSES:
        entry = palette.get("water_classes", {}).get(name, ocean)
        rows.append(
            [
                *entry["k_per_m"],
                *srgb_to_linear(entry["body"]),
                *srgb_to_linear(entry["deep"]),
                entry["deep_tau_m"],
                entry["turbidity"],
                *entry["bed_tint"],
            ]
        )
    return np.asarray(rows, np.float32)


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


def load_carpet(paint_dir: Path, meta: dict, palette: dict):
    """The seabed carpet's cover (u8) and top (metres, float16), or ``None`` without it.

    The rosettes are spread into patches, ``1 - exp(-gain * blurred share)``. No-data tops take
    the highest top within the blur, so the sampler never blends a sentinel into a patch edge.
    """
    style = palette.get("carpet", {})
    if COVER_NAME not in meta["files"] or not style.get("strength"):
        return None
    share = _plane(paint_dir, meta, COVER_NAME).astype(np.float32) / np.float32(255.0)
    spread = ndimage.gaussian_filter(share, style["blur_m"])
    cover = np.round((1.0 - np.exp(-style["gain"] * spread)) * 255).astype(np.uint8)
    top = _plane(paint_dir, meta, TOP_NAME)
    reach = 2 * int(np.ceil(2 * style["blur_m"])) + 1
    near = ndimage.grey_dilation(top, size=reach)
    missing = top == hf.NODATA
    top = np.where(missing, near, top).astype(np.float32) / np.float32(hf.DM_PER_M)
    top[missing & (near == hf.NODATA)] = np.float32(-1000.0)
    return cover, top.astype(np.float16)


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


@dataclass(frozen=True)
class GroundBake:
    """The game's baked ground colour on the paint grid: linear RGB and where it exists."""

    linear: np.ndarray
    have: np.ndarray

    @classmethod
    def from_srgb(cls, rgb, have) -> GroundBake:
        """From 8-bit sRGB; black texels inside ``have`` are holes in the bake, not ground."""
        rgb = np.asarray(rgb)
        linear = np.empty(rgb.shape, np.float32)
        for k in range(3):
            linear[..., k] = srgb_to_linear(rgb[..., k])
        return cls(linear, np.asarray(have, bool) & (rgb.astype(np.uint16).sum(-1) >= 3))


def ground_albedo(paint, have, bake: GroundBake | None, feather_m: float) -> tuple:
    """The ground albedo source: the bake where it exists, else the paint mix.

    Returns ``(albedo, have, bake_weight)``; ``bake_weight`` is None without a bake.
    """
    if bake is None:
        return paint, have, None
    inside = bake.have.astype(np.float32)
    weight = np.clip(ndimage.gaussian_filter(inside, feather_m) * 2.0 - 1.0, 0.0, 1.0) * inside
    w = weight[..., None]
    return paint * (1.0 - w) + bake.linear.astype(np.float32) * w, have | bake.have, weight


class PaintedGround:
    """Everything the painted style samples per band, built once from the paint store."""

    def __init__(
        self,
        paint_dir: Path,
        palette: dict,
        field,
        biome: dict,
        area_names: list[str],
        bake: GroundBake | None = None,
    ):
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
        if not self.baked:
            albedo = self._pigment(paint_dir, albedo, rows, cols)
        albedo, self.seam_texels = seam_blend(
            albedo, meta["components"], meta["component_px"], palette
        )
        if self.baked and bake is None:
            albedo, have, self.bake_weight = self._bake(paint_dir, albedo, have)
        else:
            blur = palette["have_blur_m"]
            albedo, have, self.bake_weight = ground_albedo(albedo, have, bake, blur)
        self.albedo_source = "paint" if self.bake_weight is None else "bake"
        index = biome_grid(biome, rows, cols)
        self.area_names = area_names
        self.area_assets = list(biome.get("assets_by_index") or [])
        self.coarse_index = index[::ROCK_GRID_M, ::ROCK_GRID_M]
        albedo = self._calibrate(albedo, weights)
        del weights
        albedo = self._fallback(albedo, have, index, len(area_names))
        albedo = self._biome_tint(albedo, index, area_names)
        self.albedo = [albedo[..., k].astype(np.float16) for k in range(3)]
        self.rock = self._rock(albedo)
        del albedo
        self.canopy = _plane(paint_dir, meta, CANOPY_NAME)
        drawn = palette.get("crowns", {}).get("draw", False)
        self.crowns = load_crowns(paint_dir, meta) if drawn else None
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
        canopy_rgb = np.asarray(meta["albedo_linear"]["canopy"], np.float32) * np.float32(
            palette["canopy_dark"]
        )
        colours = {k: srgb_to_linear(v) for k, v in palette["mesh_colours"].items()}
        targets = palette["calibration"]
        if "canopy" in targets:
            canopy_rgb = self._target(targets["canopy"])
        for name, hex_colour in targets.get("meshes", {}).items():
            colours[name] = self._target(hex_colour)
        scoped = targets.get("areas", [])
        self.canopy_rgb = scoped_planes(
            canopy_rgb,
            [
                (self._area_weight(e["areas"]), self._target(e["canopy"]))
                for e in scoped
                if "canopy" in e
            ],
        )
        self.mesh_rgb = {
            cls: scoped_planes(
                colours[name],
                [
                    (self._area_weight(e["areas"]), self._target(e["meshes"][name]))
                    for e in scoped
                    if name in e.get("meshes", {})
                ],
            )
            for cls, name in (
                (MESH_CORAL, "coral"),
                (MESH_SHELL, "shell"),
                (MESH_TERRACE, "terrace"),
            )
        }
        self.opaque_water = [
            (self._area_weight(e["areas"]), display_to_linear(palette, e["water"]))
            for e in scoped
            if "water" in e
        ]
        self.carpet = load_carpet(paint_dir, meta, palette)
        water = palette["water"]
        self.seabed_coral = colours["coral_seabed"] / np.float32(water["bed_wet"])
        self.water = {
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
            "deep": srgb_to_linear(water["deep"]),
            "deep_tau_m": np.float32(water["deep_tau_m"]),
            "bed": np.float32(water["bed_wet"]),
            "inland_floor": np.float32(water.get("inland_floor", 0.0)),
            "opaque_tau_m": np.float32(water["opaque_tau_m"]),
        }
        lo, hi, cdf = dry_land_range(field, palette["ramp_lo_pct"], palette["ramp_hi_pct"])
        self.ramp = (lo, hi, cdf)
        self.water_class, self.water_rows = None, water_table(palette)
        self.source = {"seam_texels_blended": self.seam_texels}
        self.water_source = "a paint store without water bodies: all water draws as the ocean"
        bodies = load_water_bodies(paint_dir, meta)
        if bodies is not None:
            self._classify_water(bodies, field, (index, area_names))
        self.source["water_classes"] = self.water_source

    def _classify_water(self, bodies: dict, field, biome: tuple) -> None:
        water, grades = field._water_raster(), field._water_quality_raster()
        if water is None or grades is None:
            self.water_source = "the field has no water level or quality plane"
            return
        level = np.where(water == hf.NODATA, np.nan, water / np.float32(hf.DM_PER_M))
        wet = grades != hf.WATER_DRY
        level = level.astype(np.float32)
        self.water_class, counts = classify(level, wet, bodies, biome, OCEAN_LEVEL_M)
        self.water_source = {"source": f"paint/{WATER_BODIES_NAME}", **counts}

    def water_optics(self, taps, river=None) -> dict | None:
        """Per-pixel optics for a band with inland water; None draws every pixel as the ocean.

        ``river`` is the ribbon's share of each pixel's water, drawn with the river row.
        """
        if self.water_class is None:
            return None
        mix = ClassMix(class_taps(self.water_class, taps), OCEAN)
        ribbon = river is not None and bool(np.any(river > 0))
        if mix.classes() <= {0, OCEAN} and not ribbon:
            return None
        rows = mix.of(self.water_rows)
        if ribbon:
            rows += river[..., None] * (self.water_rows[CLASSES.index("river")] - rows)
        parts = np.split(rows, np.cumsum(WATER_TABLE_COLUMNS)[:-1], axis=-1)
        k, body, deep, tau, turbidity, tint = parts
        return {
            **self.water,
            "k": k,
            "body": body,
            "deep": deep,
            "deep_tau_m": tau,
            "turbidity": turbidity,
            "tint": tint,
        }

    def provenance(self) -> dict:
        """What the sidecar records about this ground beyond the paint store's digest."""
        crowns: dict | str = "not drawn by this palette"
        if self.palette.get("crowns", {}).get("draw"):
            block = self.meta.get("crowns")
            crowns = "not in this paint store" if self.crowns is None else {
                "species": len(block["species"]),
                "trees": len(self.crowns.records),
                "rule": "one top-down sprite per species from its LOD 0, per-tree yaw, "
                "scale and lean; tallest over lowest; hidden under a higher surface",
            }  # fmt: skip
        return {**self.source, "crowns": crowns}

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

    def _biome_tint(self, albedo, index, area_names: list[str]):
        """A subtle per-biome OKLab hue offset, so biomes painted with one layer separate.

        Where the colour is the game's own bake (``bake_weight``) it is left as it is.
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
        if self.bake_weight is not None:
            w = self.bake_weight[..., None]
            out = out * (1.0 - w) + albedo * w
        return out

    def _target(self, hex_colour: str) -> np.ndarray:
        return np.clip(linear_from_oklab(display_to_ground(self.palette, hex_colour)), 0.0, 1.0)

    def _area_weight(self, keys) -> np.ndarray:
        """Membership of the listed areas on the coarse grid, blurred over ``area_blur_m``."""
        wanted = area_ids(self.area_names, self.area_assets, keys)
        mask = np.isin(self.coarse_index, wanted).astype(np.float32)
        sigma = self.palette["calibration"]["area_blur_m"] / ROCK_GRID_M
        return np.clip(ndimage.gaussian_filter(mask, sigma), 0.0, 1.0)

    def _fine_share(self, coarse: np.ndarray, rows: int, cols: int) -> np.ndarray:
        """A coarse 0..1 weight on the 1 m grid as a 0..255 share, nearest."""
        r = np.minimum(np.arange(rows) // ROCK_GRID_M, coarse.shape[0] - 1)
        c = np.minimum(np.arange(cols) // ROCK_GRID_M, coarse.shape[1] - 1)
        return np.round(coarse[np.ix_(r, c)] * 255.0).astype(np.uint8)

    def _calibrate(self, albedo, weights):
        """Move each layer's median colour onto its target, measured on whatever albedo came.

        An area entry's layer target takes that layer's weight inside its areas; the global
        target takes the rest, and each one's source median is measured on its own side.
        """
        cal = self.palette["calibration"]
        sample = (slice(None, None, 4), slice(None, None, 4))
        total = sum(w[sample].astype(np.float32) for w in weights.values())
        flat = albedo[sample]
        split = dict(weights)
        jobs, scoped = [], {}
        for i, entry in enumerate(cal.get("areas", [])):
            layers = {k: v for k, v in entry.get("layers", {}).items() if k in weights}
            if not layers:
                continue
            share = self._fine_share(self._area_weight(entry["areas"]), *albedo.shape[:2])
            inside = share[sample] >= 128
            for name, hex_colour in layers.items():
                key = f"{name}@{i}"
                split[key], split[name] = split_weight(split[name], share)
                jobs.append((key, name, hex_colour, inside))
                scoped[name] = scoped.get(name, np.zeros_like(inside)) | inside
        for name, hex_colour in cal["layers"].items():
            if name in weights:
                outside = ~scoped[name] if name in scoped else np.ones(total.shape, bool)
                jobs.append((name, name, hex_colour, outside))
        ops, self.calibration = {}, {}
        for key, name, hex_colour, where in jobs:
            pure = (weights[name][sample] >= cal["pure_share"] * np.maximum(total, 1.0)) & where
            if pure.sum() < cal["min_texels"]:
                continue
            source = median_lab(flat[pure])
            ops[key] = transfer_op(source, display_to_ground(self.palette, hex_colour))
            self.calibration[key] = {"texels": int(pure.sum()), "dL": round(ops[key][0], 4)}
        return layer_transfer(albedo, split, ops) if ops else albedo

    def _rock(self, albedo):
        """Rock colour on a coarse grid: the game's rock albedo, tinted by the ground around.

        Each area entry's rock target, then the default ``rock`` target everywhere else, sets
        the chroma and hue and moves the lightness by the median offset, keeping its variation.
        """
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
        cal = p["calibration"]
        groups = [
            (self._area_weight(e["areas"])[: lab.shape[0], : lab.shape[1]], e["rock"])
            for e in cal.get("areas", [])
            if "rock" in e
        ]
        mask = np.zeros(lab.shape[:2], np.float32)
        for weight, _ in groups:
            mask += weight
        if "rock" in cal:
            groups.append((np.clip(1.0 - mask, 0.0, 1.0), cal["rock"]))
            mask = mask + groups[-1][0]
        norm = np.maximum(mask, 1.0)
        shift = np.zeros_like(lab)
        for weight, hex_colour in groups:
            if not (weight > 0.5).any():
                continue
            target = display_to_ground(p, hex_colour)
            moved = np.empty_like(lab)
            moved[..., 0] = lab[..., 0] + (target[0] - np.median(lab[weight > 0.5][:, 0]))
            moved[..., 1:] = target[1:]
            shift += (moved - lab) * (weight / norm)[..., None]
        lab = lab + shift
        mask = np.minimum(mask, 1.0)
        rock = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        if cal.get("rock_keeps_exposure"):
            rock *= (mask + (1.0 - mask) / np.float32(p["tone"]["gain"]))[..., None]
        return [rock[..., k].astype(np.float32) for k in range(3)]


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


def canopy_over_rock(g, canopy, rock, scene: dict, ground: PaintedGround, sample, rgb=None):
    """The canopy laid over rock wherever the drawn surface is no higher than a crown top.

    ``rgb`` is the canopy colour already sampled onto the band; the ground's constant if None.
    """
    if ground.crown is None:
        return g
    crown_m = sample(ground.crown) / np.float32(hf.DM_PER_M)
    seen = (scene["z_m"] <= crown_m)[..., None]
    cover = canopy * rock * seen
    return g * (1.0 - cover) + (ground.canopy_rgb if rgb is None else rgb) * cover


def _carpet_bed(under, scene, ground: PaintedGround, sample):
    """``under`` with the seabed carpet seen through the water above its own top."""
    p, w = ground.palette["carpet"], ground.water
    depth = scene["water"]["depth_m"]
    top = sample(ground.carpet[1])
    above = np.clip(scene["z_m"] + depth - top, 0.0, None) * np.float32(p["depth_scale"])
    cover = sample(ground.carpet[0]) / np.float32(255.0) * np.float32(p["strength"])
    cover = np.where(depth > 0.0, np.clip(cover, 0.0, 1.0), 0.0)[..., None]
    transmit = np.exp(-w["k"] * above[..., None])
    rgb = srgb_to_linear(p["colour"])
    seen = rgb * transmit + w["body"] * (1.0 - transmit) + w["sky"]
    return under * (1.0 - cover) + seen * cover


def painted_colours(scene: dict, ground: PaintedGround, sample, sample_rock) -> np.ndarray:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    p = ground.palette
    crowns = scene.get("crowns")
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    gain = p["canopy_gain"] * (1.0 if crowns is None else p["crowns"]["canopy_kept"])
    canopy = np.clip(sample(ground.canopy) / 255.0 * gain, 0.0, 1.0)[..., None]
    canopy_rgb = sampled_rgb(ground.canopy_rgb, sample_rock)
    g = albedo * (1.0 - canopy) + canopy_rgb * canopy
    rock_rgb = rock_surface(
        np.stack([sample_rock(plane) for plane in ground.rock], -1), scene, ground
    )
    rock = scene["rock_weight"][..., None]
    g = g * (1.0 - rock) + rock_rgb * rock
    g = canopy_over_rock(g, canopy, rock, scene, ground, sample, canopy_rgb)
    mesh_w = scene.get("mesh_weight")
    if mesh_w is not None and mesh_w.any():
        cls = scene["mesh_class"]
        colour = rock_rgb.copy()
        for which, rgb in ground.mesh_rgb.items():
            colour = np.where((cls == which)[..., None], sampled_rgb(rgb, sample_rock), colour)
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
    light = flat_light(p, scene["ndl"], scene["ndl_flat"])
    exposure = np.float32(p["exposure"] * p["tone"]["gain"])
    lit = g * light * (exposure * borrow)[..., None]

    water = scene["water"]
    lit = wet_band(lit, water, p["shore"].get("wet_band"))
    w = scene.get("water_optics") or ground.water
    depth = optical_depth(water, p["shore"].get("river"))[..., None]
    floor = w["inland_floor"] * (1.0 - water["ocean"])[..., None]
    bed = g * exposure * w["bed"]
    if "turbidity" in w:
        floor = np.maximum(floor, w["turbidity"])
        bed = bed * w["tint"]
    transmit = np.exp(-w["k"] * depth) * (1.0 - floor)
    under = bed * transmit + w["body"] * (1.0 - transmit) + w["sky"]
    if ground.carpet is not None:
        under = _carpet_bed(under, scene, ground, sample)
    open_sea = 1.0 - np.exp(-depth / w["deep_tau_m"])
    under = under * (1.0 - open_sea) + w["deep"] * open_sea
    if ground.opaque_water:
        murk = 1.0 - np.exp(-depth / w["opaque_tau_m"])
        for weight, colour in ground.opaque_water:
            s = sample_rock(weight)[..., None] * murk
            under = under * (1.0 - s) + colour * s
    cover = water["cover"][..., None]
    out = lit * (1.0 - cover) + under * cover
    stroke = np.float32(p["shore"]["stroke"])
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, p["shore"].get("foam"), np.float32(1.0))
    if crowns is not None:
        out = over_crowns(out, crowns, scene, p, np.float32(p["ambient"]), exposure)
    out = titan_over(out, scene, ground)

    y = np.maximum(out @ LUMA, 1e-7)
    t = p["tone"]
    out = out * (tone(y, t["knee"], t["white"]) / y)[..., None]
    return linear_to_srgb(out)

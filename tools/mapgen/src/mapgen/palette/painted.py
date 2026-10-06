"""The game-painted satellite style: the landscape's own paint, lit and coloured in linear light.

Ground colour is the game's baked landscape colour where it has one (the paint store's bake,
or a ``GroundBake`` handed in), else the paint-layer weights of ``data/local/paint/`` times
each layer's albedo, tinted by the PigmentMap; a per-layer colour transfer to calibrated
targets; under the tree canopy; rocks take their family's target or tint and top layer, trees stand
over them where their crowns reach, arches and the render-only meshes take their own colours,
and the Titan trees can be laid over everything; then a sky-and-sun light, an exposure gain
with a soft shoulder and Beer-Lambert water over a seabed with the coral carpet and sunk
crowns. Every number is in ``palette/palettes/satellite-painted.json``. docs/spatial-and-map.md
sections 27, 30 to 32 and 36; the water ``palette/optics.py``, rocks ``palette/surfaces.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import SPACING_CM
from mapgen.gamedata.ground.bake import BAKE_NAME, STAMP_RING_MIN, bake_have, stamp_windows
from mapgen.gamedata.ground.paint_store import CANOPY_NAME, CROWN_NAME, META_NAME, PIGMENT_NAME
from mapgen.gamedata.water.bodies import CLASSES
from mapgen.palette.calibration import (
    area_ids,
    display_to_ground,
    display_to_linear,
    layer_transfer,
    median_lab,
    rehome_offshore,
    sampled_rgb,
    scoped_planes,
    split_weight,
    tone,
    transfer_op,
    with_derived,
)
from mapgen.palette.colour import (
    LUMA,
    flat_light,
    linear_from_oklab,
    linear_to_srgb,
    oklab,
    srgb_to_linear,
)
from mapgen.palette.optics import (
    WATER_TABLE_COLUMNS,
    carpet_bed,
    class_optics,
    load_carpet,
    load_water_bodies,
    underwater,
    water_classes,
    water_table,
)
from mapgen.palette.optics import paint_plane as _plane
from mapgen.palette.shore import OCEAN_LEVEL_BAND_M, OCEAN_LEVEL_M, add_foam, wet_band, wet_mix
from mapgen.palette.surfaces import (
    canopy_over_rock,
    family_cells,
    family_tables,
    family_targets,
    mesh_surface,
    rock_surface,
    sunk_specks,
)
from mapgen.palette.trees import (
    band_crowns,
    crown_calibration,
    over_crowns,
    sample_titan,
    titan_over,
)
from mapgen.terrain.crowns import load_crowns
from mapgen.terrain.rasters import MESH_CORAL, MESH_SHELL, MESH_TERRACE, TITAN_LEAVES, TITAN_TRUNK
from satisfactory_mcp.core.gameassets.maparea import NO_MANS_LAND
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "ROCK_GRID_M",
    "WATER_TABLE_COLUMNS",
    "GroundBake",
    "PaintedGround",
    "area_ids",
    "bake_table",
    "biome_grid",
    "canopy_over_rock",
    "carpet_bed",
    "display_to_ground",
    "display_to_linear",
    "dry_land_range",
    "ground_albedo",
    "hidden_ground",
    "land_cells",
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
    "patch_stamps",
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


def bake_table(meta: dict) -> dict[str, np.ndarray] | None:
    """The paint layers' albedo refitted to the bake, or ``None`` for a store without one."""
    fit = meta["albedo_linear"].get("layers_bake_fit")
    if not fit or BAKE_NAME not in meta["files"]:
        return None
    return {name: np.asarray(value, np.float32) for name, value in fit.items()}


def hidden_ground(ok: np.ndarray) -> np.ndarray:
    """Holes the bake's own cover encloses: landscape the game hides, a crater's pit or a
    cave's mouth, where the paint under it is never seen."""
    return ndimage.binary_fill_holes(ok) & ~ok


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


def land_cells(field, shape: tuple[int, int]) -> np.ndarray | None:
    """Ground the sea does not cover, on a grid of ``shape`` over the field, nearest; None for a
    field without water planes. Water at the ocean's level is sea whatever its grade, and so is
    the void off the landscape."""
    water, grades = field._water_raster(), field._water_quality_raster()
    if water is None or grades is None:
        return None
    rows = np.minimum(np.arange(shape[0]) * field.height // shape[0], field.height - 1)
    cols = np.minimum(np.arange(shape[1]) * field.width // shape[1], field.width - 1)
    pick = np.ix_(rows, cols)
    level = water[pick].astype(np.float32) / np.float32(hf.DM_PER_M)
    sea = (grades[pick] != hf.WATER_DRY) & (water[pick] != hf.NODATA)
    sea &= np.abs(level - OCEAN_LEVEL_M) <= OCEAN_LEVEL_BAND_M
    return (field._height_dm[pick] != hf.NODATA) & ~sea


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


def patch_stamps(rgb, ok, paint, nodes_m) -> int:
    """Over each node's stamp, in place on the sRGB bake ``rgb`` where ``ok``: the linear paint
    mix scaled by the median ratio of bake to paint on the ring where the bake comes back
    (``stamp_windows``). Returns the texels replaced outright."""
    replaced = 0
    for window, keep in stamp_windows(nodes_m, rgb.shape[:2]):
        bake, mix, have = srgb_to_linear(rgb[window]), paint[window], ok[window]
        ring = have & (keep > 0) & (keep < 1) & (mix.min(-1) > 0)
        ratio = np.ones(3, np.float32)
        if ring.sum() >= STAMP_RING_MIN:
            ratio = np.median(bake[ring] / mix[ring], axis=0)
        k = keep[..., None]
        patched = np.round(linear_to_srgb(bake * k + np.clip(mix * ratio, 0.0, 1.0) * (1 - k)))
        write = (have & (keep < 1))[..., None]
        rgb[window] = np.where(write, patched, rgb[window]).astype(np.uint8)
        replaced += int((have & (keep == 0)).sum())
    return replaced


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
    """Everything the painted style samples per band, built once from the paint store.

    ``stamps`` are the ``(x, y)`` metres of the nodes whose stamp the store's bake carries.
    """

    def __init__(
        self,
        paint_dir: Path,
        palette: dict,
        field,
        biome: dict,
        area_names: list[str],
        stamps: np.ndarray | None = None,
        bake: GroundBake | None = None,
    ):
        meta = load_paint_meta(paint_dir)
        if meta is None:
            raise FileNotFoundError(f"no paint store at {paint_dir}")
        self.meta, self.palette = meta, palette
        self.source: dict = {}
        self._stamps = stamps
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
        self.coarse_index = self._coarse_areas(index, field)
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
        self.family_tint, self.family_top, self.family_has_top = family_tables(
            meta.get("rock_families") or {}, palette
        )
        # Render-grid rasters the pipeline attaches: the direct pass's family plane (with
        # ``attach_families``), and the Titan tree raster as (z cm, class, factor, row0, col0).
        self.rock_family, self.family_rock = None, {}
        self.source["rock_family_targets"] = {}
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
            (
                self._area_weight(e["areas"]),
                display_to_linear(palette, e["water"]),
                CLASSES.index(e["water_class"]),
            )
            for e in scoped
            if "water" in e
        ]
        self.crown_ops, self.crown_measured = [], {}
        if self.crowns is not None:
            self.crown_ops = self._crown_ops(targets)
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
        self.source["seam_texels_blended"] = self.seam_texels
        self.source["water_classes"] = "not classified: all water draws as the ocean"
        self._bodies = load_water_bodies(paint_dir, meta)
        self._biome = (biome, area_names)

    def classify_water(self, field, planes=None):
        """The class plane from the water as it will be drawn, and what the sidecar records.

        ``planes`` is ``(level_dm, grades)`` after rivers and perched water took their share;
        the field's own planes when None.
        """
        water, grades = planes or (field._water_raster(), field._water_quality_raster())
        if self._bodies is None:
            found = "a paint store without water bodies: all water draws as the ocean"
        elif water is None or grades is None:
            found = "the field has no water level or quality plane"
        else:
            biome, names = self._biome
            index = biome_grid(biome, *grades.shape)
            self.water_class, found = water_classes(water, grades, self._bodies, (index, names))
        self.source["water_classes"] = found
        return found

    def water_optics(self, taps, river=None) -> dict | None:
        """Per-pixel optics for a band with inland water; None draws every pixel as the ocean.

        ``river`` is the ribbon's share of each pixel's water, drawn with the river row. The
        opaque area water's classes come back as shares, so it lands on its own class only.
        """
        if self.water_class is None:
            return None
        shares = tuple({cid for *_rest, cid in getattr(self, "opaque_water", ())})
        return class_optics(self.water_class, self.water_rows, self.water, taps, river, shares)

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
                "calibration": self.crown_measured,
            }  # fmt: skip
        return {**self.source, "crowns": crowns}

    def _crown_ops(self, targets: dict) -> list:
        """The crowns' transfers on the rock grid (``trees.crown_calibration``)."""
        grid = (self.coarse_index.shape, SPACING_CM * ROCK_GRID_M)
        ops, self.crown_measured = crown_calibration(
            self.crowns, self.palette, targets, grid, self._area_weight
        )
        return ops

    def _bake(self, paint_dir, albedo, have):
        """The bake where it exists, feathered over ``have_blur_m`` into the paint mix.

        A hole the bake encloses is ground the game hides, so its paint (one solid layer per
        component) is dropped and the biome fallback draws there instead. A node's stamp is
        patched over first (``patch_stamps``).
        """
        rgb = _plane(paint_dir, self.meta, BAKE_NAME)
        ok = bake_have(rgb)
        if self._stamps is not None:
            rgb = np.array(rgb)
            texels = patch_stamps(rgb, ok, albedo, self._stamps)
            self.source["bake_stamps_patched"] = {"nodes": len(self._stamps), "texels": texels}
        soft = ndimage.gaussian_filter(ok.astype(np.float32), self.palette["have_blur_m"])
        w = (np.clip(soft * 2.0 - 1.0, 0.0, 1.0) * ok).astype(np.float32)
        for start in range(0, albedo.shape[0], 512):
            block = slice(start, start + 512)
            weight = w[block][..., None]
            albedo[block] = albedo[block] * (1.0 - weight) + srgb_to_linear(rgb[block]) * weight
        self.bake_share = float(ok.mean())
        hidden = hidden_ground(ok)
        self.source["hidden_ground_texels"] = int(hidden.sum())
        return albedo, (have | ok) & ~hidden, w

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

    def _coarse_areas(self, index, field) -> np.ndarray:
        """The area raster on the rock grid, with the areas' offshore pieces rehomed: the
        game's map gives the sea north of the Spire Coast, islands and all, to the Rocky
        Desert, whose rock and sand targets are not theirs."""
        coarse = index[::ROCK_GRID_M, ::ROCK_GRID_M]
        land = land_cells(field, coarse.shape)
        if land is None:
            return coarse
        out = rehome_offshore(coarse, self.area_names, land, NO_MANS_LAND)
        self.source["offshore_cells_rehomed"] = int((out != coarse).sum())
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
        cal = with_derived(self.palette["calibration"])
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
        self._rock_lab = lab if cal.get("families") else None
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

    def attach_families(self, plane) -> None:
        """The direct pass's family plane, and the rock of each family the palette gives a
        target (``surfaces.family_targets``), which holds over any area's rock."""
        self.rock_family, self.family_rock = plane, {}
        base, self._rock_lab = getattr(self, "_rock_lab", None), None
        if plane is None or base is None:
            return
        cal = self.palette["calibration"]
        codes = family_cells(plane, base.shape[:2], ROCK_GRID_M * SPACING_CM / 100.0)
        self.family_rock, measured = family_targets(
            base, codes, cal["families"], self.palette, cal["min_texels"]
        )
        self.source.setdefault("rock_family_targets", {}).update(measured)


def painted_colours(scene: dict, ground: PaintedGround, sample, sample_rock) -> np.ndarray:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    p = ground.palette
    crowns = scene.get("crowns")
    scene = {**scene, "water": sunk_specks(scene)}
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    gain = p["canopy_gain"] * (1.0 if crowns is None else p["crowns"]["canopy_kept"])
    canopy = np.clip(sample(ground.canopy) / 255.0 * gain, 0.0, 1.0)[..., None]
    canopy_rgb = sampled_rgb(ground.canopy_rgb, sample_rock)
    g = albedo * (1.0 - canopy) + canopy_rgb * canopy
    area_rock = np.stack([sample_rock(plane) for plane in ground.rock], -1)
    rock = scene["rock_weight"][..., None]
    g = g * (1.0 - rock) + rock_surface(area_rock, scene, ground, sample_rock) * rock
    g = canopy_over_rock(g, canopy, rock, scene, ground, sample, canopy_rgb)
    g = mesh_surface(g, area_rock, scene, ground, sample_rock)

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
    trees = band_crowns(scene, ground, sample_rock, exposure)
    lit = wet_band(lit, water, p["shore"].get("wet_band"))
    under = underwater(g, scene, ground, sample, sample_rock, exposure, trees)
    out = wet_mix(lit, under, water["cover"][..., None])
    stroke = np.float32(p["shore"]["stroke"])
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, p["shore"].get("foam"), np.float32(1.0))
    if trees is not None:
        out = over_crowns(out, trees)
    out = titan_over(out, scene, ground)

    y = np.maximum(out @ LUMA, 1e-7)
    t = p["tone"]
    out = out * (tone(y, t["knee"], t["white"]) / y)[..., None]
    return linear_to_srgb(out)

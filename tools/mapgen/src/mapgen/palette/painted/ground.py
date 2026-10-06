"""The game-painted style's ground, built once per run from the paint store.

Ground colour is the game's baked landscape colour where it has one (the store's bake, or a
``GroundBake`` handed in), else the paint layers' weights times each layer's albedo, tinted by
the PigmentMap; each layer moved onto its calibrated target; the biome's median off the
landscape; rock on a coarse grid in its area's or its family's colour. Every number is in
``palette/palettes/satellite-painted.json``; docs/spatial-and-map.md sections 27 and 30 to 32.
``band.py`` draws it a band at a time.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import TypeAlias, cast

import numpy as np
from scipy import ndimage

from mapgen.cache import Plane, TitanPlanes
from mapgen.colour import linear_from_oklab, oklab, srgb_to_linear
from mapgen.gamedata.frame import SPACING_CM
from mapgen.gamedata.ground.bake import BAKE_NAME, bake_have
from mapgen.gamedata.ground.paint_store import CANOPY_NAME, CROWN_NAME, PIGMENT_NAME
from mapgen.gamedata.water.bodies import WATER_CLASSES
from mapgen.palette.painted.albedo import (
    GroundBake,
    bake_table,
    ground_albedo,
    hidden_ground,
    layer_table,
    load_paint_meta,
    load_water_bodies,
    mix_layers,
    paint_plane,
    patch_stamps,
    seam_blend,
)
from mapgen.palette.painted.calibration import (
    area_ids,
    display_to_ground,
    display_to_linear,
    layer_transfer,
    median_lab,
    rehome_offshore,
    scoped_planes,
    split_weight,
    transfer_op,
    with_derived,
)
from mapgen.palette.painted.optics import base_water, class_optics, load_carpet, water_table
from mapgen.palette.painted.shapes import (
    BandTaps,
    BiomeGrid,
    CalibrationArea,
    CalibrationStyle,
    Carpet,
    ClassOptics,
    ColourPlanes,
    CrownOp,
    FieldPlanes,
    FloatGrid,
    OpaqueWater,
    PaintedPalette,
    PaintPlane,
    Ramp,
    WaterBase,
)
from mapgen.palette.painted.surfaces import family_cells, family_tables, family_targets
from mapgen.palette.painted.trees import crown_calibration, titan_colours
from mapgen.palette.painted.water_classes import water_classes
from mapgen.palette.styles import dry_land_range
from mapgen.palette.water.shore import OCEAN_LEVEL_BAND_M, OCEAN_LEVEL_M
from mapgen.terrain.crown_stamp import CrownSet, load_crowns
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_SHELL, MESH_TERRACE
from satisfactory_mcp.core.arrays import BoolMask, F16Grid, F64Grid, I16Grid, U8Grid
from satisfactory_mcp.core.gameassets.maparea import NO_MANS_LAND
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["ROCK_GRID_M", "PaintedGround", "biome_grid", "land_cells"]

#: The rock colour's grid, coarser than the paint: it is a 25 m blur of it.
ROCK_GRID_M = 4

#: The render-only meshes the painted style colours, by class, with their palette names.
MESH_COLOUR_NAMES = ((MESH_CORAL, "coral"), (MESH_SHELL, "shell"), (MESH_TERRACE, "terrace"))

#: Every fourth texel each way: where the medians of the paint are measured.
_EVERY_4TH = (slice(None, None, 4), slice(None, None, 4))

#: A layer's colour transfer to measure: its key, layer, display target and where it counts.
LayerJob: TypeAlias = tuple[str, str, str, BoolMask]


def biome_grid(biome: BiomeGrid, rows: int, cols: int) -> U8Grid:
    """The biome raster's index under every texel of the 1 m grid. Nearest."""
    width = biome["width"]
    r = np.clip(((np.arange(rows) + 0.5) * width / rows).astype(np.int64), 0, width - 1)
    c = np.clip(((np.arange(cols) + 0.5) * width / cols).astype(np.int64), 0, width - 1)
    return biome["area"][np.ix_(r, c)]


def land_cells(field: FieldPlanes, shape: tuple[int, int]) -> BoolMask | None:
    """Ground the sea does not cover, on a grid of ``shape`` over the field, nearest; None for a
    field without water planes. Water at the ocean's level is sea whatever its grade, and so is
    the void off the landscape."""
    water, grades = field.water_raster(), field.water_quality_raster()
    if water is None or grades is None:
        return None
    height = field.height_dm
    if height is None:
        return None
    rows = np.minimum(np.arange(shape[0]) * field.height // shape[0], field.height - 1)
    cols = np.minimum(np.arange(shape[1]) * field.width // shape[1], field.width - 1)
    pick = np.ix_(rows, cols)
    level = water[pick].astype(np.float32) / np.float32(hf.DM_PER_M)
    sea = (grades[pick] != hf.WATER_DRY) & (water[pick] != hf.NODATA)
    sea &= np.abs(level - OCEAN_LEVEL_M) <= OCEAN_LEVEL_BAND_M
    return (height[pick] != hf.NODATA) & ~sea


def _fine_share(coarse: FloatGrid, rows: int, cols: int) -> U8Grid:
    """A coarse 0..1 weight on the 1 m grid as a 0..255 share, nearest."""
    r = np.minimum(np.arange(rows) // ROCK_GRID_M, coarse.shape[0] - 1)
    c = np.minimum(np.arange(cols) // ROCK_GRID_M, coarse.shape[1] - 1)
    return np.round(coarse[np.ix_(r, c)] * 255.0).astype(np.uint8)


class PaintedGround:
    """Everything the painted style samples per band, built once from the paint store.

    ``stamps`` are the ``(x, y)`` metres of the nodes whose stamp the store's bake carries.
    """

    def __init__(
        self,
        paint_dir: Path,
        palette: Mapping[str, object],
        field: FieldPlanes,
        biome: BiomeGrid,
        area_names: list[str],
        stamps: F64Grid | None = None,
        bake: GroundBake | None = None,
    ) -> None:
        meta = load_paint_meta(paint_dir)
        if meta is None:
            raise FileNotFoundError(f"no paint store at {paint_dir}")
        self.meta = meta
        # The palette file's JSON; styles.painted_style hands it over untyped.
        self.palette: PaintedPalette = cast(PaintedPalette, palette)
        self.source: JsonObject = {}
        self._stamps = stamps
        rows, cols = meta["grid"]["height"], meta["grid"]["width"]
        albedo, have, weights, seam_texels = self._paint_albedo(paint_dir, bake, (rows, cols))
        index = biome_grid(biome, rows, cols)
        self.area_names = area_names
        self.area_assets: list[str] = list(biome.get("assets_by_index") or [])
        self.coarse_index = self._coarse_areas(index, field)
        albedo = self._calibrate(albedo, weights)
        del weights
        albedo = self._fallback(albedo, have, index, len(area_names))
        albedo = self._biome_tint(albedo, index, area_names)
        self.albedo: list[F16Grid] = [albedo[..., k].astype(np.float16) for k in range(3)]
        self.rock: list[FloatGrid] = self._rock(albedo)
        del albedo
        self._trees(paint_dir)
        self.rock_family: Plane | None = None
        self.family_rock: dict[int, list[FloatGrid]] = {}
        self.source["rock_family_targets"] = {}
        self.titan: TitanPlanes | None = None
        self.titan_rgb: dict[int, FloatGrid] = titan_colours(self.palette)
        colours = self._mesh_colours()
        self.canopy_rgb: ColourPlanes = scoped_planes(
            self._canopy_colour(), self._area_targets(lambda e: e.get("canopy"))
        )
        self.mesh_rgb: dict[int, ColourPlanes] = {
            cls: scoped_planes(colours[name], self._area_targets(_mesh_target(name)))
            for cls, name in MESH_COLOUR_NAMES
        }
        self.opaque_water: list[OpaqueWater] = self._opaque_water()
        self.crown_ops: list[CrownOp] = []
        self.crown_measured: JsonObject = {}
        self._calibrate_crowns(self.palette["calibration"])
        self.carpet: Carpet | None = load_carpet(paint_dir, meta, self.palette)
        self.seabed_coral: FloatGrid = colours["coral_seabed"] / np.float32(
            self.palette["water"]["bed_wet"]
        )
        self.water: WaterBase = base_water(self.palette)
        self.ramp: Ramp = dry_land_range(
            field, self.palette["ramp_lo_pct"], self.palette["ramp_hi_pct"]
        )
        self.water_class: U8Grid | None = None
        self.water_rows = water_table(self.palette)
        self.source["seam_texels_blended"] = seam_texels
        self.source["water_classes"] = "not classified: all water draws as the ocean"
        self._bodies = load_water_bodies(paint_dir, meta)
        self._biome = (biome, area_names)

    def classify_water(
        self, field: FieldPlanes, planes: tuple[I16Grid | None, U8Grid | None] | None = None
    ) -> str | JsonObject:
        """The class plane from the water as it will be drawn, and what the sidecar records.

        ``planes`` is ``(level_dm, grades)`` after rivers and perched water took their share;
        the field's own planes when None.
        """
        water, grades = planes or (field.water_raster(), field.water_quality_raster())
        found: str | JsonObject
        if self._bodies is None:
            found = "a paint store without water bodies: all water draws as the ocean"
        elif water is None or grades is None:
            found = "the field has no water level or quality plane"
        else:
            biome, names = self._biome
            index = biome_grid(biome, grades.shape[0], grades.shape[1])
            self.water_class, found = water_classes(water, grades, self._bodies, (index, names))
        self.source["water_classes"] = found
        return found

    def water_optics(self, taps: BandTaps, river: FloatGrid | None = None) -> ClassOptics | None:
        """Per-pixel optics for a band with inland water; None draws every pixel as the ocean.

        ``river`` is the ribbon's share of each pixel's water, drawn with the river row. The
        opaque area water's classes come back as shares, so it lands on its own class only.
        """
        if self.water_class is None:
            return None
        shares = tuple({cid for *_rest, cid in self.opaque_water})
        return class_optics(self.water_class, self.water_rows, self.water, taps, river, shares)

    def provenance(self) -> JsonObject:
        """What the sidecar records about this ground beyond the paint store's digest."""
        return {**self.source, "crowns": self._crowns_provenance()}

    def attach_families(self, plane: Plane | None) -> None:
        """The direct pass's family plane, and the rock of each family the palette gives a
        target (``surfaces.family_targets``), which holds over any area's rock."""
        self.rock_family, self.family_rock = plane, {}
        base, self._rock_lab = self._rock_lab, None
        if plane is None or base is None:
            return
        cal = self.palette["calibration"]
        codes = family_cells(
            plane, (base.shape[0], base.shape[1]), ROCK_GRID_M * SPACING_CM / 100.0
        )
        self.family_rock, measured = family_targets(
            base, codes, cal.get("families", {}), self.palette, cal["min_texels"]
        )
        found = self.source.setdefault("rock_family_targets", {})
        if isinstance(found, dict):
            found.update(measured)

    def attach_titan(self, titan: TitanPlanes | None) -> None:
        """The Titan tree raster on the render grid, as ``(z cm, class, factor, row0, col0)``."""
        self.titan = titan

    # -- building the ground -------------------------------------------------------------------

    def _paint_albedo(
        self, paint_dir: Path, bake: GroundBake | None, shape: tuple[int, int]
    ) -> tuple[FloatGrid, BoolMask, dict[str, PaintPlane], int]:
        """The paint layers mixed, overlaid, pigmented and seam-blended, under the bake where
        there is one: ``(albedo, have, layer weights, seam texels blended)``."""
        meta, palette = self.meta, self.palette
        table = bake_table(meta) if palette.get("ground") == "bake" else None
        baked = table is not None
        if table is None:
            table = layer_table(meta, palette)
        weights = {
            entry["layer"]: paint_plane(paint_dir, meta, name)
            for name, entry in meta["files"].items()
            if "layer" in entry
        }
        albedo, have = mix_layers(weights, table, shape)
        darkening = np.float32(1.0 if baked else palette["albedo_darkening"])
        for name, value in meta["albedo_linear"]["overlays"].items():
            if name in weights:
                w = (weights[name].astype(np.float32) / 255.0)[..., None]
                albedo = albedo * (1.0 - w) + np.asarray(value, np.float32) * darkening * w
        if not baked:
            albedo = self._pigment(paint_dir, albedo, *shape)
        albedo, seam_texels = seam_blend(albedo, meta["components"], meta["component_px"], palette)
        if baked and bake is None:
            albedo, have, self.bake_weight = self._bake(paint_dir, albedo, have)
        else:
            blur = palette["have_blur_m"]
            albedo, have, self.bake_weight = ground_albedo(albedo, have, bake, blur)
        return albedo, have, weights, seam_texels

    def _trees(self, paint_dir: Path) -> None:
        """The canopy share, the crowns when the palette draws them, the crown tops, and the
        rock families' tints and tops."""
        meta, palette = self.meta, self.palette
        self.canopy: PaintPlane = paint_plane(paint_dir, meta, CANOPY_NAME)
        drawn = palette.get("crowns", {}).get("draw", False)
        self.crowns: CrownSet | None = (
            load_crowns(paint_dir, cast("dict[str, object]", meta)) if drawn else None
        )
        self.crown: PaintPlane | None = (
            paint_plane(paint_dir, meta, CROWN_NAME) if CROWN_NAME in meta["files"] else None
        )
        self.family_tint, self.family_top, self.family_has_top = family_tables(
            meta.get("rock_families") or {}, palette
        )

    def _mesh_colours(self) -> dict[str, FloatGrid]:
        """Each render-only mesh's linear colour: the palette's, or its calibration target."""
        colours = {k: srgb_to_linear(v) for k, v in self.palette["mesh_colours"].items()}
        for name, hex_colour in self.palette["calibration"].get("meshes", {}).items():
            colours[name] = self._target(hex_colour)
        return colours

    def _canopy_colour(self) -> FloatGrid:
        """The canopy's linear colour: its calibration target, else the store's canopy, darker."""
        targets = self.palette["calibration"]
        if "canopy" in targets:
            return self._target(targets["canopy"])
        canopy = np.asarray(self.meta["albedo_linear"]["canopy"], np.float32)
        return canopy * np.float32(self.palette["canopy_dark"])

    def _area_targets(
        self, pick: Callable[[CalibrationArea], str | None]
    ) -> list[tuple[FloatGrid, FloatGrid]]:
        """Each area entry ``pick`` finds a target in: its weight and the target as colour."""
        found: list[tuple[FloatGrid, FloatGrid]] = []
        for entry in self.palette["calibration"].get("areas", []):
            hex_colour = pick(entry)
            if hex_colour is not None:
                found.append((self._area_weight(entry["areas"]), self._target(hex_colour)))
        return found

    def _opaque_water(self) -> list[OpaqueWater]:
        """Each area entry's opaque water: its weight, linear colour and the class it holds."""
        found: list[OpaqueWater] = []
        for entry in self.palette["calibration"].get("areas", []):
            colour = entry.get("water")
            if colour is None:
                continue
            water_class = entry.get("water_class")
            if water_class is None:
                raise ValueError(f"calibration area {entry['areas']} has water but no water_class")
            found.append(
                OpaqueWater(
                    self._area_weight(entry["areas"]),
                    display_to_linear(self.palette, colour),
                    WATER_CLASSES.index(water_class),
                )
            )
        return found

    def _calibrate_crowns(self, targets: CalibrationStyle) -> None:
        """The crowns moved onto their species targets, and their transfers and what was
        measured (``trees.crown_calibration``); nothing without crowns."""
        if self.crowns is None:
            return
        rows, cols = self.coarse_index.shape[0], self.coarse_index.shape[1]
        grid = ((rows, cols), SPACING_CM * ROCK_GRID_M)
        found = crown_calibration(self.crowns, self.palette, targets, grid, self._area_weight)
        self.crowns.levels = found.levels
        self.crown_ops, self.crown_measured = found.ops, found.measured

    def _crowns_provenance(self) -> JsonValue:
        if not self.palette.get("crowns", {}).get("draw"):
            return "not drawn by this palette"
        block = self.meta.get("crowns")
        species = None if block is None else block.get("species")
        if self.crowns is None or not isinstance(species, list):
            return "not in this paint store"
        return {
            "species": len(species),
            "trees": len(self.crowns.records),
            "rule": "one top-down sprite per species from its LOD 0, per-tree yaw, "
            "scale and lean; tallest over lowest; hidden under a higher surface",
            "calibration": self.crown_measured,
        }

    def _bake(
        self, paint_dir: Path, albedo: FloatGrid, have: BoolMask
    ) -> tuple[FloatGrid, BoolMask, FloatGrid]:
        """The bake where it exists, feathered over ``have_blur_m`` into the paint mix.

        A hole the bake encloses is ground the game hides, so its paint (one solid layer per
        component) is dropped and the biome fallback draws there instead. A node's stamp is
        patched over first (``patch_stamps``).
        """
        rgb = paint_plane(paint_dir, self.meta, BAKE_NAME)
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
        hidden = hidden_ground(ok)
        self.source["hidden_ground_texels"] = int(hidden.sum())
        return albedo, (have | ok) & ~hidden, w

    def _pigment(self, paint_dir: Path, albedo: FloatGrid, rows: int, cols: int) -> FloatGrid:
        strength = np.float32(self.palette["pigment"])
        if not strength:
            return albedo
        texture = srgb_to_linear(paint_plane(paint_dir, self.meta, PIGMENT_NAME))
        side = texture.shape[0]
        coords = (np.arange(rows, dtype=np.float32) + 0.5) * side / rows - 0.5
        rr, cc = np.meshgrid(
            coords, (np.arange(cols, dtype=np.float32) + 0.5) * side / cols - 0.5, indexing="ij"
        )
        for k in range(3):
            tint = ndimage.map_coordinates(texture[..., k], [rr, cc], order=1, mode="nearest")
            albedo[..., k] *= (1.0 - strength) + strength * tint
        return albedo

    def _fallback(self, albedo: FloatGrid, have: BoolMask, index: U8Grid, areas: int) -> FloatGrid:
        """Off the landscape: the median paint of the biome, blurred, faded in over the edge."""
        palette = self.palette
        idx, ok = index[_EVERY_4TH], have[_EVERY_4TH]
        flat = albedo[_EVERY_4TH]
        global_median = np.median(flat[ok], axis=0) if ok.any() else np.full(3, 0.2, np.float32)
        medians = np.tile(global_median, (areas, 1)).astype(np.float32)
        for i in np.unique(idx[ok]):
            picked = flat[ok & (idx == i)]
            if len(picked) >= 50:
                medians[i] = np.median(picked, axis=0)
        fallback = medians[index]
        for k in range(3):
            fallback[..., k] = ndimage.gaussian_filter(fallback[..., k], palette["fallback_blur_m"])
        weight = ndimage.gaussian_filter(have.astype(np.float32), palette["have_blur_m"])[..., None]
        return albedo * weight + fallback * (1.0 - weight)

    def _biome_tint(self, albedo: FloatGrid, index: U8Grid, area_names: list[str]) -> FloatGrid:
        """A subtle per-biome OKLab hue offset, so biomes painted with one layer separate.

        Where the colour is the game's own bake (``bake_weight``) it is left as it is.
        """
        palette = self.palette
        strength = np.float32(palette["biome_tint_strength"])
        offsets = np.zeros((len(area_names), 2), np.float32)
        for i, name in enumerate(area_names):
            offsets[i] = palette["biome_tint_ab"].get(name, (0.0, 0.0))
        shift = offsets[index] * strength
        for k in range(2):
            shift[..., k] = ndimage.gaussian_filter(shift[..., k], palette["biome_tint_blur_m"])
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

    def _coarse_areas(self, index: U8Grid, field: FieldPlanes) -> U8Grid:
        """The area raster on the rock grid, with the areas' offshore pieces rehomed: the
        game's map gives the sea north of the Spire Coast, islands and all, to the Rocky
        Desert, whose rock and sand targets are not theirs."""
        coarse = index[::ROCK_GRID_M, ::ROCK_GRID_M]
        land = land_cells(field, (coarse.shape[0], coarse.shape[1]))
        if land is None:
            return coarse
        out = rehome_offshore(coarse, self.area_names, land, NO_MANS_LAND)
        self.source["offshore_cells_rehomed"] = int((out != coarse).sum())
        return out

    def _target(self, hex_colour: str) -> FloatGrid:
        return np.clip(linear_from_oklab(display_to_ground(self.palette, hex_colour)), 0.0, 1.0)

    def _area_weight(self, keys: Collection[str]) -> FloatGrid:
        """Membership of the listed areas on the coarse grid, blurred over ``area_blur_m``."""
        wanted = area_ids(self.area_names, self.area_assets, keys)
        mask = np.isin(self.coarse_index, wanted).astype(np.float32)
        sigma = self.palette["calibration"]["area_blur_m"] / ROCK_GRID_M
        return np.clip(ndimage.gaussian_filter(mask, sigma), 0.0, 1.0)

    def _calibrate(self, albedo: FloatGrid, weights: Mapping[str, PaintPlane]) -> FloatGrid:
        """Move each layer's median colour onto its target, measured on whatever albedo came.

        An area entry's layer target takes that layer's weight inside its areas; the global
        target takes the rest, and each one's source median is measured on its own side.
        """
        cal = with_derived(self.palette["calibration"])
        sample = _EVERY_4TH
        flat = albedo[sample]
        total = np.zeros(flat.shape[:2], np.float32)
        for w in weights.values():
            total += w[sample]
        split = dict(weights)
        jobs = self._area_layer_jobs(cal, weights, split, (albedo.shape[0], albedo.shape[1]))
        scoped: dict[str, BoolMask] = {}
        for _key, name, _hex, inside in jobs:
            scoped[name] = scoped.get(name, np.zeros_like(inside)) | inside
        for name, hex_colour in cal["layers"].items():
            if name in weights:
                outside = ~scoped[name] if name in scoped else np.ones(total.shape, bool)
                jobs.append((name, name, hex_colour, outside))
        ops: dict[str, tuple[float, FloatGrid]] = {}
        self.calibration: JsonObject = {}
        for key, name, hex_colour, where in jobs:
            pure = (weights[name][sample] >= cal["pure_share"] * np.maximum(total, 1.0)) & where
            if pure.sum() < cal["min_texels"]:
                continue
            source = median_lab(flat[pure])
            ops[key] = transfer_op(source, display_to_ground(self.palette, hex_colour))
            self.calibration[key] = {"texels": int(pure.sum()), "dL": round(ops[key][0], 4)}
        return layer_transfer(albedo, split, ops) if ops else albedo

    def _area_layer_jobs(
        self,
        cal: CalibrationStyle,
        weights: Mapping[str, PaintPlane],
        split: dict[str, PaintPlane],
        shape: tuple[int, int],
    ) -> list[LayerJob]:
        """Each area entry's layer targets, with the layer's weight in ``split`` cut into the
        share inside its areas (``layer@entry``) and the rest."""
        jobs: list[LayerJob] = []
        for i, entry in enumerate(cal.get("areas", [])):
            layers = {k: v for k, v in entry.get("layers", {}).items() if k in weights}
            if not layers:
                continue
            share = _fine_share(self._area_weight(entry["areas"]), *shape)
            inside = share[_EVERY_4TH] >= 128
            for name, hex_colour in layers.items():
                key = f"{name}@{i}"
                split[key], split[name] = split_weight(split[name], share)
                jobs.append((key, name, hex_colour, inside))
        return jobs

    def _rock(self, albedo: FloatGrid) -> list[FloatGrid]:
        """Rock colour on a coarse grid: the game's rock albedo, tinted by the ground around.

        Each area entry's rock target, then the default ``rock`` target everywhere else, sets
        the chroma and hue and moves the lightness by the median offset, keeping its variation.
        """
        palette = self.palette
        lab = self._tinted_rock(albedo)
        cal = palette["calibration"]
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
            target = display_to_ground(palette, hex_colour)
            moved = np.empty_like(lab)
            moved[..., 0] = lab[..., 0] + (target[0] - np.median(lab[weight > 0.5][:, 0]))
            moved[..., 1:] = target[1:]
            shift += (moved - lab) * (weight / norm)[..., None]
        lab = lab + shift
        mask = np.minimum(mask, 1.0)
        rock = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        if cal.get("rock_keeps_exposure"):
            rock *= (mask + (1.0 - mask) / np.float32(palette["tone"]["gain"]))[..., None]
        return [rock[..., k].astype(np.float32) for k in range(3)]

    def _tinted_rock(self, albedo: FloatGrid) -> FloatGrid:
        """The game's rock albedo in OKLab on the rock grid, moved toward the ground around."""
        palette = self.palette
        step = ROCK_GRID_M
        rock_lab = oklab(np.asarray(self.meta["albedo_linear"]["rock"], np.float32))
        blur = palette["rock_tint_blur_m"]
        near = np.stack(
            [ndimage.gaussian_filter(albedo[..., k], blur)[::step, ::step] for k in range(3)], -1
        )
        lab = oklab(np.clip(near, 1e-7, None))
        lightness, chroma = palette["rock_tint_lightness"], palette["rock_tint_chroma"]
        lab[..., 0] = rock_lab[0] * (1 - lightness) + lab[..., 0] * lightness
        lab[..., 0] += np.float32(palette["rock_lightness_add"])
        lab[..., 1:] = rock_lab[1:] * (1 - chroma) + lab[..., 1:] * chroma
        return lab


def _mesh_target(name: str) -> Callable[[CalibrationArea], str | None]:
    return lambda entry: entry.get("meshes", {}).get(name)

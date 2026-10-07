"""The palettes and the two drawn layers' colour painters.

Each style is a JSON file in ``palettes/``, refused at import unless it has its style's shape
(``palette.schema``); the constants below are read out of them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.ground.biome import BiomeRaster
from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette.scene import (
    FloatGrid,
    SatelliteScene,
    ShadedScene,
    field_heights,
)
from mapgen.palette.schema import (
    PaintedPalette,
    Palette,
    ReliefPalette,
    SatellitePalette,
    ShoreOptics,
    ShoreStyle,
    TerrainPalette,
    TitanTreesStyle,
    checked,
)
from mapgen.palette.water.shore import blend_where, water_composite
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid, U8Grid
from satisfactory_mcp.core.gameassets.maparea import NO_MANS_LAND
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue, require_object
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "BIOME_BLEND_TEXELS",
    "BIOME_COLOURS",
    "HIGH_HI_M",
    "HIGH_LIFT",
    "HIGH_LO_M",
    "HIGH_RGB",
    "LAYER_STYLES",
    "NOISE_OCTAVES",
    "NOISE_SEED",
    "NOISE_SMOOTH",
    "NO_MANS_LAND_RGB",
    "PAINTED_DIGEST",
    "PAINTED_PALETTE",
    "PALETTE_DIR",
    "PIT_EDGE_RGB",
    "PIT_RGB",
    "PLAIN_LAYERS",
    "RAMP_HI_PCT",
    "RAMP_LO_PCT",
    "RAMP_STOPS",
    "RELIEF_PALETTES",
    "ROCK_HI_DEG",
    "ROCK_LO_DEG",
    "ROCK_RGB",
    "SATELLITE_DIGEST",
    "SATELLITE_PALETTE",
    "SATELLITE_SHORE",
    "SATELLITE_WATER_DEEP",
    "SATELLITE_WATER_SHALLOW",
    "SEA_RGB",
    "SHORE_OPTICS",
    "STYLE_DIGESTS",
    "TERRAIN_DIGEST",
    "TERRAIN_PALETTE",
    "TERRAIN_SHORE",
    "UNKNOWN_BIOME_RGB",
    "VOID_EDGE_RGB",
    "VOID_MOST",
    "VOID_RIM_RGB",
    "WATER_DEEP",
    "WATER_SHALLOW",
    "biome_colour_field",
    "biome_index",
    "biome_lookup",
    "dry_land_range",
    "load_palette",
    "noise_fields",
    "painted_style",
    "palette_digest",
    "ramp",
    "ramp_position",
    "ramp_range",
    "satellite_colours",
    "terrain_colours",
    "with_sea",
    "with_void",
]

#: The palettes are files, one per style id, and a style's digest is the hash of its file's
#: canonical JSON, so an edit without a version bump still reads as a different style.
PALETTE_DIR = Path(__file__).resolve().parent / "palettes"
LAYER_STYLES = {
    "terrain": "terrain-hypsometric",
    "satellite": "satellite-biome",
    "painted": "satellite-painted",
    "relief": "relief-muted",
    "relief-dark": "relief-night",
}


def palette_digest(palette: Mapping[str, object]) -> str:
    """The digest of a palette: the sha256 of its canonical JSON."""
    canonical = json.dumps(palette, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_hex(canonical)


def load_palette(style: str) -> tuple[JsonObject, str]:
    """One palette file as read, and its digest."""
    loaded: JsonValue = json.loads((PALETTE_DIR / f"{style}.json").read_text(encoding="utf-8"))
    palette = require_object(loaded)
    return palette, palette_digest(palette)


def _layer_palette(shape: type[Palette], layer: str) -> tuple[Palette, str]:
    """A layer's palette, refused unless it has ``shape``, and its digest."""
    palette, digest = load_palette(LAYER_STYLES[layer])
    return checked(shape, palette, LAYER_STYLES[layer]), digest


TERRAIN_PALETTE, TERRAIN_DIGEST = _layer_palette(TerrainPalette, "terrain")
SATELLITE_PALETTE, SATELLITE_DIGEST = _layer_palette(SatellitePalette, "satellite")
PAINTED_PALETTE, PAINTED_DIGEST = _layer_palette(PaintedPalette, "painted")
#: The relief layers share one painter (``palette.relief``), one palette each.
RELIEF_PALETTES = {
    layer: _layer_palette(ReliefPalette, layer) for layer in ("relief", "relief-dark")
}
STYLE_DIGESTS = {
    "terrain": TERRAIN_DIGEST,
    "satellite": SATELLITE_DIGEST,
    "painted": PAINTED_DIGEST,
    **{layer: digest for layer, (_palette, digest) in RELIEF_PALETTES.items()},
}


def painted_style(no_titan_trees: bool) -> tuple[PaintedPalette, str]:
    """The painted palette and its digest, with the Titan trees switched off on request."""
    if not no_titan_trees:
        return PAINTED_PALETTE, PAINTED_DIGEST
    trees: TitanTreesStyle = {**PAINTED_PALETTE["titan_trees"], "opacity": 0}
    palette: PaintedPalette = {**PAINTED_PALETTE, "titan_trees": trees}
    return palette, palette_digest(palette)


#: The ocean shore's optics per style (recipe 6): opacity at the line, depth fade, wet ground.
TERRAIN_SHORE: ShoreOptics = TERRAIN_PALETTE["shore"]
SATELLITE_SHORE: ShoreOptics = SATELLITE_PALETTE["shore"]
SHORE_OPTICS: dict[str, ShoreStyle] = {
    "terrain": TERRAIN_SHORE,
    "satellite": SATELLITE_SHORE,
    "painted": PAINTED_PALETTE["shore"],
    **{layer: palette["shore"] for layer, (palette, _digest) in RELIEF_PALETTES.items()},
}

#: The ramp's height band, as land percentiles: one spire must not flatten it.
RAMP_LO_PCT = float(TERRAIN_PALETTE["ramp_lo_pct"])
RAMP_HI_PCT = float(TERRAIN_PALETTE["ramp_hi_pct"])

#: The ramp itself: dark green lowland, olive, tan, rock, snow.
RAMP_STOPS = np.array(TERRAIN_PALETTE["ramp_stops"], np.float32)

#: Water, tinted by how deep it is: shallow reads pale and green, deep reads dark blue.
WATER_SHALLOW = np.array(TERRAIN_PALETTE["water_shallow"], np.float32)
WATER_DEEP = np.array(TERRAIN_PALETTE["water_deep"], np.float32)

#: No data, in the page's own ``--sea``, so the map's edge draws no border.
SEA_RGB = np.array([16, 32, 44], np.float32)

#: The void's edge beside the land, lit as the artwork lights it (luma about 70) in the page's
#: hue, darkening to ``SEA_RGB``; a pit's from the artwork's flat grey to black; and the light
#: rim the artwork draws round both. The same in every style.
VOID_EDGE_RGB = np.array([66, 79, 90], np.float32)
PIT_EDGE_RGB = np.array([76, 76, 76], np.float32)
PIT_RGB = np.array([4, 5, 6], np.float32)
VOID_RIM_RGB = np.array([236, 236, 230], np.float32)

#: Past this share of a band under the void's cover or rim, ``with_void`` blends every pixel.
VOID_MOST = 2 / 3

# The satellite layer's own rules (tools/mapgen/README.md, "Design notes").

#: One colour per named area, chosen by eye; not the asset's UI ``mColorPalette``.
BIOME_COLOURS = {name: tuple(colour) for name, colour in SATELLITE_PALETTE["biome_colours"].items()}

#: Biome bleed in raster texels (1.83 m each): 24 is about a tree line, 44 m.
BIOME_BLEND_TEXELS = 24.0

#: The outer coast, which the game names no biome for: a neutral bleached ground.
NO_MANS_LAND_RGB = tuple(SATELLITE_PALETTE["no_mans_land"])

#: An area a later build adds: the same neutral, not a guessed green.
UNKNOWN_BIOME_RGB = NO_MANS_LAND_RGB

#: Bare rock, and the slope band over which the biome's colour gives way to it.
ROCK_RGB = np.array(SATELLITE_PALETTE["rock"], np.float32)
ROCK_LO_DEG = float(SATELLITE_PALETTE["rock_lo_deg"])
ROCK_HI_DEG = float(SATELLITE_PALETTE["rock_hi_deg"])

#: High ground pales over this band of metres, at most HIGH_LIFT of the way to HIGH_RGB.
HIGH_RGB = np.array(SATELLITE_PALETTE["high"], np.float32)
HIGH_LO_M = float(SATELLITE_PALETTE["high_lo_m"])
HIGH_HI_M = float(SATELLITE_PALETTE["high_hi_m"])
HIGH_LIFT = float(SATELLITE_PALETTE["high_lift"])

#: Water seen from above: dark, green in the shallows, near-black in the deep.
SATELLITE_WATER_SHALLOW = np.array(SATELLITE_PALETTE["water_shallow"], np.float32)
SATELLITE_WATER_DEEP = np.array(SATELLITE_PALETTE["water_deep"], np.float32)

#: Two octaves of fixed-seed value noise, sampled by world position so no band shows.
NOISE_SEED = int(SATELLITE_PALETTE["noise_seed"])
NOISE_OCTAVES = tuple(
    (int(size), float(amount)) for size, amount in SATELLITE_PALETTE["noise_octaves"]
)
NOISE_SMOOTH = 1.0


def biome_colour_field(biome: BiomeRaster, table: F32Grid) -> U8Grid:
    """The raster's indices turned into colour, then blurred so no area boundary is a line.

    Done once at the raster's own 4096, one channel at a time, and kept as uint8.
    """
    area = biome["area"]
    field = np.empty(area.shape + (3,), np.uint8)
    for channel in range(3):
        blurred = ndimage.gaussian_filter(
            table[:, channel][area], BIOME_BLEND_TEXELS, mode="nearest"
        )
        field[..., channel] = np.clip(blurred, 0, 255).astype(np.uint8)
    return field


def biome_lookup(biome: BiomeRaster) -> tuple[F32Grid, list[str]]:
    """Per palette index: its RGB in the designed palette, and the name it was drawn as."""
    names = biome["names"]
    rgb = np.zeros((len(names), 3), np.float32)
    drawn: list[str] = []
    for index, name in enumerate(names):
        if name in BIOME_COLOURS:
            colour = BIOME_COLOURS[name]
            drawn.append(name)
        elif name == NO_MANS_LAND or name is None:
            colour = NO_MANS_LAND_RGB
            drawn.append(NO_MANS_LAND)
        else:
            colour = UNKNOWN_BIOME_RGB
            drawn.append(f"{name} (no colour in this file's table)")
        rgb[index] = colour
    return rgb, drawn


def ramp(values: FloatGrid, stops: F32Grid) -> FloatGrid:
    """Linear interpolation along a colour ramp, ``values`` in [0, 1]."""
    position = np.clip(values, 0.0, 1.0) * (len(stops) - 1)
    low = np.clip(np.floor(position), 0, len(stops) - 2).astype(np.int64)
    fraction = (position - low)[..., None].astype(np.float32)
    return stops[low] * (1 - fraction) + stops[low + 1] * fraction


def noise_fields(rng_seed: int) -> list[tuple[F32Grid, float]]:
    """The value-noise octaves, made once and sampled by world position afterwards, so no
    band boundary draws a seam."""
    rng = np.random.default_rng(rng_seed)
    fields: list[tuple[F32Grid, float]] = []
    for size, amount in NOISE_OCTAVES:
        field = rng.standard_normal((size, size), dtype=np.float32)
        fields.append((ndimage.gaussian_filter(field, NOISE_SMOOTH, mode="wrap"), amount))
    return fields


def biome_index(coordinate: FloatGrid, lo_m: float, hi_m: float, width: int) -> I64Grid:
    """Which biome texel a run of world coordinates falls in. Nearest, and never in between:
    an area index is a name, and the average of two names is a third, unrelated area."""
    position = (coordinate - lo_m * 100) / ((hi_m - lo_m) * 100) * width
    return np.clip(position.astype(np.int64), 0, width - 1)


# --------------------------------------------------------------------------------------
# The two layers.
# --------------------------------------------------------------------------------------


def terrain_colours(scene: ShadedScene) -> FloatGrid:
    """The approved preview at full resolution: ramp, shade, borrowed detail, then water.

    ``borrow`` is the artwork's own light multiplied in over the provinces where the field
    has none of its own. Pixels with no data are the caller's to paint.
    """
    z_m, ramp_lo, ramp_hi = scene["z_m"], scene["ramp_lo"], scene["ramp_hi"]
    height = np.clip((z_m - ramp_lo) / max(ramp_hi - ramp_lo, 1e-6), 0.0, 1.0)
    land = ramp(height, RAMP_STOPS) * (scene["shade"] * scene["borrow"])[..., None]
    return water_composite(
        land,
        scene["water"],
        scene["shade"],
        TERRAIN_SHORE,
        WATER_SHALLOW,
        WATER_DEEP,
        WATER_SHADE_FLOOR,
        WATER_SHADE_RANGE,
    )


def satellite_colours(scene: SatelliteScene) -> FloatGrid:
    """Ground colour from the biome, then rock, then altitude, then light, then water.

    In that order: the biome says what grows there, the slope overrules it because nothing
    grows on a cliff face, the altitude bleaches what is left, the hillshade lights all of it
    at once, and the water goes on top because it is a different surface. ``borrow`` rides
    with the hillshade: what is taken from the artwork is light.
    """
    slope, z_m = scene["slope"], scene["z_m"]
    rock = np.clip((slope - ROCK_LO_DEG) / (ROCK_HI_DEG - ROCK_LO_DEG), 0.0, 1.0)[..., None]
    rgb = scene["biome_rgb"] * (1 - rock) + ROCK_RGB * rock
    lift = np.clip((z_m - HIGH_LO_M) / (HIGH_HI_M - HIGH_LO_M), 0.0, 1.0)[..., None] * HIGH_LIFT
    rgb = rgb * (1 - lift) + HIGH_RGB * lift
    land = rgb * scene["noise"][..., None] * (scene["shade"] * scene["borrow"])[..., None]
    return water_composite(
        land,
        scene["water"],
        scene["shade"],
        SATELLITE_SHORE,
        SATELLITE_WATER_SHALLOW,
        SATELLITE_WATER_DEEP,
        WATER_SHADE_FLOOR,
        WATER_SHADE_RANGE,
    )


def with_sea(rgb: FloatGrid, missing: BoolMask) -> FloatGrid:
    """No data in the page's own sea colour, whatever the style."""
    return np.where(missing[..., None], SEA_RGB, rgb)


def with_void(
    rgb: FloatGrid,
    cover: FloatGrid,
    falloff: FloatGrid | None = None,
    pit: FloatGrid | None = None,
    rim: FloatGrid | None = None,
) -> FloatGrid:
    """The void past the world's edge and in its pits, each plane in [0, 1] and the same
    whatever the style. ``cover`` is how much of a pixel the void hides. Past the edge it is
    the page's own sea colour, so the map's edge draws no border; a pit is black. Both are lit
    at their edge and darken over ``falloff``, 0 at the edge, with a light ``rim`` round them;
    without those planes, all of it is the page's sea."""
    weight = cover[..., None]
    if falloff is None or pit is None or rim is None:
        return rgb * (1.0 - weight) + SEA_RGB * weight
    planes = (weight, falloff[..., None], pit[..., None], rim[..., None])
    return blend_where((cover != 0) | (rim != 0), VOID_MOST, _void_blend, rgb, *planes)


def _void_blend(
    rgb: FloatGrid, weight: FloatGrid, deep: FloatGrid, hole: FloatGrid, line: FloatGrid
) -> FloatGrid:
    edge = VOID_EDGE_RGB * (1.0 - hole) + PIT_EDGE_RGB * hole
    colour = edge * (1.0 - deep) + (SEA_RGB * (1.0 - hole) + PIT_RGB * hole) * deep
    return (rgb * (1.0 - weight) + colour * weight) * (1.0 - line) + VOID_RIM_RGB * line


#: The layers ``render.draw.painting`` draws with ``terrain_colours`` or ``satellite_colours``.
PLAIN_LAYERS = frozenset({"terrain", "satellite"})


def ramp_range(field: hf.Field) -> tuple[float, float]:
    """The height band the ramp is stretched over, from the field itself.

    Sampled every fourth texel in each direction: a sixteenth of the arithmetic, and the
    answer moves by less than a decimetre either way.
    """
    sample = field_heights(field)[::4, ::4]
    land = sample[sample != hf.NODATA].astype(np.float32) / hf.DM_PER_M
    return (
        float(np.percentile(land, RAMP_LO_PCT)),
        float(np.percentile(land, RAMP_HI_PCT)),
    )


def ramp_position(
    height_m: FloatGrid, lo_m: float, hi_m: float, cdf_m: F32Grid, equalised: float
) -> F32Grid:
    """Ground height to [0, 1] over dry land: part linear in metres, part equal-area."""
    linear = np.clip((height_m - lo_m) / max(hi_m - lo_m, 1e-6), 0.0, 1.0)
    quantile = np.interp(height_m, cdf_m, np.linspace(0.0, 1.0, len(cdf_m)))
    return ((1.0 - equalised) * linear + equalised * quantile).astype(np.float32)


def dry_land_range(field: hf.Field, lo_pct: float, hi_pct: float) -> tuple[float, float, F32Grid]:
    """The ramp's height range and CDF, over dry land only (waterq dry, height known)."""
    height = field_heights(field)[::4, ::4]
    grades = field.water_quality_raster()
    dry = height != hf.NODATA
    if grades is not None:
        dry &= grades[::4, ::4] == hf.WATER_DRY
    metres = height[dry].astype(np.float32) / hf.DM_PER_M
    lo, hi = np.percentile(metres, [lo_pct, hi_pct])
    cdf = np.percentile(metres[(metres >= lo) & (metres <= hi)], np.linspace(0, 100, 101))
    return float(lo), float(hi), cdf.astype(np.float32)

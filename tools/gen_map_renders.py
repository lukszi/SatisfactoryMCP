"""Draw two base-map layers of this world out of the 1 m heightfield and the game's biomes.

    uv run --extra gen python tools/gen_map_renders.py

``tools/gen_map_image.py`` cuts the game's own drawn map into ``data/local/tiles/``; this
file adds two layers drawn rather than found. **terrain** is a hypsometric ramp under a
north-west hillshade with water tinted by its own depth; **satellite** is the same relief
coloured from the game's own per-pixel biome raster through a palette designed to look like
imagery. Both are 32768x32768 on the **same frame as the artwork sheet** -- x [-3247, 4253]
m, y [-3750, 3750] m -- and cut into the same 256 px pyramid, so the page's tile grid, CRS
and bounds are untouched and a layer is a change of picture and nothing else.

The height under a pixel comes from two regimes and a cross-fade between them. Where
``density.u8.z`` says at least one source vertex landed in the ground under an output texel,
the cliff geometry is rasterised into this grid at 0.229 m -- by ``gen_world_heightmap.py``'s
own sweep, mesh decode, cull rules and ``MaxZRaster``, imported and called here so the only
thing that differs is the grid they are pointed at. Everywhere else, which is the great
majority of the sheet, a Catmull-Rom kernel over the 1 m field answers. The join is a blend
and never a switch: the hillshade is a function of the derivative, so a hard switch between
a rasterised surface and a C1 interpolant would draw the density plane's own boundaries into
the relief as ridges. ``SeamTrace`` measures that along the seam on every run. The lattice
under the kernel takes its landscape from ``terrain.u16.z`` at 7.8 mm, and the arches and
foliage boulders of ``top.i16.z`` are rasterised the same way and composited last
(docs/spatial-and-map.md section 23).

The frame and the artwork slices come from ``tools/gen_map_image.py``, which measured them;
the codec from ``domain.spatial.heightfield``, the pyramid cutter from
``core.gameassets.pyramid``, and the biome decode from ``core.gameassets.maparea``, which
``tools/gen_region_names.py`` reads too. Nothing about any of them is re-decided here,
because three pyramids that retype each other's corners are three pyramids that disagree
about where the world is.

Everything is written under gitignored ``data/local/renders/<layer>/``:
``tiles/{z}/{x}_{y}.png`` for z0..z7, ``tiles@2x/{z}/{x}_{y}.png`` for z0..z5, and a
``meta.json`` the web API reads. Four pins are refused on rather than overwritten -- a field
whose sidecar names another build, a field that is not there, a field with no density plane,
and a direct cache rasterised for another size or build. ``ooz``, ``texture2ddecoder`` and
Pillow are the project's ``gen`` extra and are imported inside the functions that need them,
so a machine without the extra still imports every module and runs the test suite.

The heightfield and the biome raster are derived from Coffee Stain's cooked assets, read out
of the reader's own install. The colours are this file's, and nothing here is committed,
uploaded or redistributed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.maparea import (
    MAP_AREA_CLASS,
    MAP_AREA_PATH,
    NO_MANS_LAND,
    MapAreaError,
    read_map_areas,
)
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import read_str_path
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    PyramidError,
    cut_square,
    cut_square_parallel,
    install_pyramid,
)
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.domain.spatial import heightfield as hf

# The generator that WRITES the field, for the direct regime: its sweep, its mesh decode,
# its cull rules and its rasteriser, called rather than reimplemented.
from tools import gen_world_heightmap as gen
from tools._common import base_parser, require_gen

# The corners every layer is drawn on, the sheet the ARTWORK is drawn at, and how to read
# its four slices out of the container, from the tool that MEASURED them.
from tools.gen_map_image import BOUNDS_M, SHEET_PX, SLICES, TILE_PX, read_slice

#: ``renders/<layer>/`` holds a ``tiles/`` tree of exactly the shape ``data/local/tiles/``
#: has, so the endpoint that serves one serves the others with a directory swapped.
LOCAL_DIR = ROOT / "data" / "local"
RENDERS_DIR_NAME = "renders"
RENDER_SIDECAR_NAME = "meta.json"

#: The layers this file draws, in the order they are cut; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite")

#: How many processes deflate tiles when ``--workers`` is not given. One per core, capped
#: because past a point the cores wait on the disk rather than on zlib, and each interpreter
#: pays to import numpy before it writes a PNG.
WORKER_CAP = 16
DEFAULT_WORKERS = min(os.cpu_count() or 1, WORKER_CAP)

#: Which level ``--check-parallel`` cuts twice. z5 is 1,024 tiles, so the timing means
#: something, and every supported sheet size has it.
CHECK_PARALLEL_Z = 5

#: What a render is drawn at: 32768 px over 7500 m is 0.229 m to the pixel, and z7 is the
#: top of the 1x tree. Derived from the artwork's sheet size rather than typed, because the
#: two are the same frame.
#:
#: z7 is **not** a claim that the 1 m field has more to say -- doubling the sampling of it
#: was measured twice to find no new world. It is a claim about the CLIENT: a browser shown
#: z6 at twice its scale upsamples bilinearly, and bilinear is C0, so the relief comes out
#: ruled into 0.458 m squares. z7 is the same surface evaluated by the same C1 kernel at
#: half the spacing. Over the direct regime it is new information as well, because there the
#: pixels are triangles, and ``_meta.render.two_regime.regimes`` says how much of the sheet
#: that was on the day it ran.
RENDER_PX = SHEET_PX * 4

#: What the @2x tree is cut from, one level shallower than the sheet: a 512 px z6 tree costs
#: as much as the whole 1x pyramid for pixels a hi-DPI client gets by asking for the 1x tile
#: one level deeper, which is what Leaflet's own retina path does.
RENDER_2X_PX = SHEET_PX * 2

#: Which recipe drew the pixels, recorded per layer so a reader looking at a tile can find
#: out which set of rules made it.
RECIPES = {
    1: (
        "terrain: hypsometric ramp over the 1st..99.5th height percentile, NW hillshade at "
        "45 deg, water tinted by depth. satellite: biome palette, slope-driven rock, "
        "elevation lightening, two octaves of noise, the same hillshade and water"
    ),
    2: (
        "recipe 1 at 16384 px, sampled with a C1 Catmull-Rom kernel so the hillshade has no "
        "cell structure; submersion read from waterq.u8.z rather than inferred from a "
        "comparison, level-only water at full alpha and the deep end of the ramp; and the "
        "artwork sheet's luminance high-pass borrowed into the shading wherever the "
        "provenance byte says cliff or fill, faded out towards landscape"
    ),
    3: (
        "recipe 2 at 32768 px, with a two-regime sampler: the cliff geometry decoded from "
        "the container and rasterised into this grid at 0.229 m wherever density.u8.z says "
        "a source vertex landed under the output texel, the Catmull-Rom kernel over the 1 m "
        "field everywhere else, and a density-weighted cross-fade between them so no "
        "province boundary is ever a derivative discontinuity. Plus the fill province "
        "low-passed at its own 3.66 m cell so its 3.9 m terraces stop being contours"
    ),
    4: (
        "recipe 3 with the landscape under the kernel read from terrain.u16.z at 7.8 mm "
        "instead of the decimetre ground plane, under the cliff province as well, and the "
        "arches and foliage boulders the field keeps in top.i16.z rasterised at 0.229 m and "
        "composited over everything by the same coverage-and-lift rule as the rocks"
    ),
}
RECIPE = 4

#: What ``--kernel-only`` draws, and it is a whole recipe rather than recipe 3 with a stage
#: switched off: no geometry opened, no direct regime, no cross-fade and no de-terracing.
#: The sidecar records this number, so a layer drawn that way never claims the recipe above.
RECIPE_KERNEL_ONLY = 2

# --------------------------------------------------------------------------------------
# The biome raster.
# --------------------------------------------------------------------------------------

#: Decoding the raster, resolving each palette index to an ``Area_*`` asset and reading the
#: game's own name for it are ``core.gameassets.maparea``'s. What is here is what this file
#: DRAWS with: a palette of its own, a blur, and the pin the raster is placed on.

# --------------------------------------------------------------------------------------
# The calibration: where the biome raster's 4096 texels go, which the asset does not say.
# --------------------------------------------------------------------------------------

#: The sheet the pin is scored against, and the resolution the scoring runs at. The artwork
#: is the only picture in this repository whose corners have already been measured, which is
#: what makes it the ruler here rather than another thing to calibrate.
CALIBRATION_PX = 1024
CALIBRATION_SHIFT_M = 600
CALIBRATION_STEP_M = 200
CALIBRATION_SCALES = (0.95, 1.05)

#: How much better than its neighbours the pin has to read before this file believes it.
#: 1.15 is well inside the measured gap -- 2.28 against 1.42 -- and well outside the noise.
CALIBRATION_MARGIN = 1.15

#: The committed region table, which ``tools/gen_region_names.py`` derives from this same
#: raster. Its agreement with this decode is therefore a staleness check and not evidence
#: about the pin, which is what ``region_table_is_current`` below makes it.
REGION_TABLE = ROOT / "data" / "region_names.json"

# --------------------------------------------------------------------------------------
# The shading both layers share.
# --------------------------------------------------------------------------------------

#: The sun: north-west at 45 degrees, the convention every relief map uses. Light it from
#: anywhere else and the reader's eye inverts the valleys.
SUN_AZIMUTH_DEG = 315.0
SUN_ALTITUDE_DEG = 45.0

#: How much of the picture the hillshade is allowed to be. A shade of 0 would be black
#: ground; the ramp keeps a fully shadowed slope at 45% of its own colour, which is dark
#: enough to read as shadow and light enough that the colour underneath still says something.
SHADE_FLOOR = 0.45
SHADE_RANGE = 0.55

#: The height band the hypsometric ramp is stretched over, as percentiles of the land. Not
#: min and max: a single 400 m spire would flatten the ramp over the whole rest of the world.
RAMP_LO_PCT = 1.0
RAMP_HI_PCT = 99.5

#: The ramp itself: dark green lowland, olive, tan, rock, snow. Straight off the preview the
#: owner approved.
RAMP_STOPS = np.array(
    [
        [46, 74, 44],
        [86, 116, 56],
        [140, 148, 78],
        [176, 152, 108],
        [190, 170, 150],
        [226, 226, 226],
        [255, 255, 255],
    ],
    np.float32,
)

#: Water, tinted by how deep it is: shallow reads pale and green, deep reads dark blue. The
#: depth is clipped at this many metres, past which more depth is not more colour.
WATER_DEPTH_FULL_M = 40.0
WATER_SHALLOW = np.array([100, 190, 230], np.float32)
WATER_DEEP = np.array([40, 110, 170], np.float32)

#: How deep the water has to be before it is drawn as water and nothing else; under this the
#: two are mixed. The field is a 1 m grid and "is this texel under water" is a step function
#: on it, so a hard test draws every coast as 1 m blocks.
WATER_EDGE_M = 0.9

#: The same edge softened in SPACE as well as in depth, in METRES of ground. The depth
#: feather does nothing where the shore is a cliff -- the water goes from nothing to metres
#: deep across one texel, with no band to blend over -- and much of this world's water sits
#: in box-shaped bodies against exactly that.
#:
#: In metres rather than output pixels, so that doubling the sheet does not quietly halve
#: how much ground the constant means.
WATER_EDGE_BLUR_M = 0.73

#: How much the hillshade is allowed to touch water: some, because a lake that ignores the
#: light sits on the picture rather than in it; not much, because the shading is computed
#: from the ground UNDER the water.
WATER_SHADE_FLOOR = 0.75
WATER_SHADE_RANGE = 0.25

#: No data, in the page's own ``--sea``: the tile disappears into the background rather than
#: drawing a border around the map's edge.
SEA_RGB = np.array([16, 32, 44], np.float32)

# --------------------------------------------------------------------------------------
# Borrowing the artwork's detail where the field's own province is coarse.
# --------------------------------------------------------------------------------------

#: Which provinces of the field carry less information than the artwork does: cliff is
#: rasterised low-poly collision hulls and fill is a 3.9 m block raster, so over them a
#: render drawn from the field alone is smooth because it has nothing to say. Landscape is
#: absent because it is continuous geometry the game evaluates itself, 45.3% of the field.
#:
#: **Both cliff values**, spelled through ``PROV_CLIFF_VALUES`` so a third one cannot be
#: added without this line seeing it. 73% of the shipped field's cliff province is 5, so
#: listing only 4 would withdraw the borrow from three quarters of it.
BORROW_PROVENANCE = (*hf.PROV_CLIFF_VALUES, hf.PROV_FILL)

#: How far the borrow fades out across a province boundary, in field texels (metres). The
#: provenance byte is a hard label on a 1 m grid, so a hard switch between two shading rules
#: would draw the label itself: a coastline of shading style around every island.
BORROW_FEATHER_M = 6.0

#: The scale of artwork detail that is borrowed, in artwork pixels (0.92 m each). The high
#: pass is the sheet minus its own Gaussian blur at this sigma, so what comes across is
#: everything FINER than about 7 m and nothing coarser: the coarse structure is the field's
#: job and the two must not both draw it.
BORROW_DETAIL_SIGMA_PX = 8.0

#: What separates borrowing the artwork's SHADING from tracing its ink. The map is a drawing
#: and a drawing has strokes -- every rock formation is outlined in a hard dark line one or
#: two pixels wide -- so the high pass is first blurred, which turns a stroke into the
#: gradient it stands in for, and then squashed through ``tanh`` at this many standard
#: deviations of itself. A soft clip passes the mid-tones, which are the shading, almost
#: linearly and saturates the outliers, which are the ink.
BORROW_DETAIL_SOFTEN_PX = 1.6
BORROW_DETAIL_SIGMAS = 1.2

#: How much of the result reaches the picture, and how far it may push a pixel either way.
#: Picked by looking, on four crops: at 0.17 the offshore cliff islands are still flat
#: facets, at 0.50 the drawn map's contour rings read as rings. 0.30 is where a collision
#: hull stops being eight flat plates and starts being rock.
BORROW_GAIN = 0.30
BORROW_CLAMP = (0.74, 1.26)

#: Luma weights, Rec. 601, because what is wanted is the artwork's LIGHT and 601 is the
#: weighting that matches how a human sees it. The colour never crosses: an ocean drawn blue
#: contributes its brightness and nothing else.
BORROW_LUMA = np.array([0.299, 0.587, 0.114], np.float32)

# --------------------------------------------------------------------------------------
# The direct regime: which output texels are entitled to the triangles, and how the two
# regimes are joined.
# --------------------------------------------------------------------------------------

#: How many source vertices the ground under one OUTPUT texel has to have contributed before
#: that texel's height is a measurement rather than an interpolation across a triangle wider
#: than itself. The rule is the field generator's, imported rather than retyped; this file
#: only evaluates it at a different spacing. ``density.u8.z`` counts per 1 m texel, so the
#: test against a 0.229 m texel is ``density >= 1 / 0.229**2``, i.e. 19 of them.
DIRECT_SAMPLES_PER_TEXEL = gen.DIRECT_SAMPLES_MIN

#: How many sub-samples per output texel per axis the direct pass rasterises at. The pass
#: costs 4x per doubling and the silhouette is already at 0.229 m, an eighth of the 1 m
#: staircase this regime exists to remove. Raise it with ``--direct-subsamples`` and the
#: sidecar records what was run. A silhouette is antialiased only by these sub-samples:
#: rock heights are never blurred across one.
DIRECT_SUBSAMPLES = 1

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres: the field's own hard ``max`` with its corner rounded. A hard max puts a
#: first-derivative discontinuity where the rock meets the ground, and the hillshade would
#: draw it as a line around the base of every formation on the map. A quarter of a metre
#: sits at most an eighth of one above the hard answer and never below it, so the rule holds
#: exactly rather than nearly.
DIRECT_LIFT_KNEE_M = 0.25


#: Rows of the output the direct pass rasterises at a time. A whole 32768 square of float32
#: is 4.3 GB and the render already holds 3.2 GB of output; 256 rows is 34 MB, and a
#: triangle at the 0.48 m median touches one band or two, so a per-placement y-bbox test is
#: all the selection needed. Also the colour bands' size and one row of 256 px tiles.
DIRECT_BAND_ROWS = 256

#: Where the direct raster is kept between the two layers. Rasterising 216 M triangles is
#: twenty minutes and the answer does not depend on which layer is being coloured, so it is
#: done once, memory-mapped, and deleted at the end of the run unless ``--keep-direct``.
DIRECT_CACHE_DIR_NAME = "direct.cache"
DIRECT_Z_NAME = "direct.z.f32"
DIRECT_COVERAGE_NAME = "direct.cov.u8"
DIRECT_CACHE_SIDECAR = "meta.json"

#: The arch-and-boulder raster, cached the same way and under the same file names.
TOP_CACHE_DIR_NAME = "top.cache"

#: Foliage instances transformed per batch in the top pass.
TOP_FOLIAGE_BATCH = 512

#: What the seam trace calls "at the seam" and "in a pure regime". The statistic is the p99
#: of the second difference of the drawn height along a row, at the seam against the pure
#: regimes on either side, as a ratio; above ``SEAM_RATIO_MAX`` the cross-fade is drawing
#: curvature the surface does not have and the run says so.
#:
#: "At the seam" is the whole BLEND rather than a window around the half-weight line. A
#: feather's second derivative is zero at its own midpoint by symmetry -- it lives at the
#: shoulders -- so a window around ``w = 0.5`` measures the one place a hard join has
#: nothing to show, and a nearly-hard join sails through it. Every 3-texel stencil is
#: therefore sorted into one of three pools by the weights under it: wholly kernel, wholly
#: direct, or straddling.
SEAM_MID = 0.5
SEAM_PURE = 0.02
SEAM_RATIO_MAX = 1.5

#: What a hard switch reads, which is the scale the fade's own number is on. A convex blend
#: of two surfaces can never be rougher than the switch between them, so this is an identity
#: rather than a bound and nothing can fail it.
SEAM_SWITCH_CEILING = 1.0

SEAM_SAME_SURFACE_M = 0.5

#: How far from a crossing the comparison pool is gathered, in output texels. 32 of them is
#: 7.3 m at z7: the ground on either side of the seam and not the rest of the world, which
#: is mostly open ocean and would make any seam look like a spike against it.
SEAM_NEAR_TEXELS = 32

#: How many texels of each pool one band contributes. A systematic sample rather than the
#: whole pool, whose percentile moves in the fourth decimal over tens of millions of texels.
SEAM_SAMPLE_MAX_PER_BAND = 200_000

#: How the fill province stops being terraces. Its cells are 3.66 m and its Z step 3.9 m, so
#: it draws the ocean shelf as flat plateaus with blocky outlines; the low pass is one cell
#: wide, the scale below which that raster says nothing at all. Both numbers come from the
#: generator that decoded the raster.
FILL_DETERRACE_SIGMA_M = gen.FILL_HORIZONTAL_M
FILL_QUANTISATION_M = gen.FILL_VERTICAL_M

# --------------------------------------------------------------------------------------
# The satellite layer's own rules.
# --------------------------------------------------------------------------------------

#: One colour per named area, chosen by eye against crops of this world. NOT the asset's own
#: ``mColorPalette``, which is a UI legend of flat primaries, cyan, magenta and pure white:
#: a satellite render drawn from those would be a highlighter drawing of a world. The scheme
#: here is desaturated, nothing is brighter than about 220, and the greens run from bleached
#: olive on the dry forests to near-black on the canopy that is near-black from above.
BIOME_COLOURS = {
    "Area_DuneDesert": (200, 178, 138),
    "Area_RockyDesert": (166, 138, 102),
    "Area_DesertCanyons": (150, 114, 84),
    "Area_MazeCanyons": (142, 126, 106),
    "Area_AbyssCliffs": (112, 108, 102),
    "Area_crater": (124, 128, 114),
    "Area_Savanna": (148, 142, 94),
    "Area_GrassFields": (110, 128, 76),
    "Area_SpireCoast": (124, 132, 102),
    "Area_LakeForest": (68, 94, 62),
    "Area_NorthernForest": (58, 82, 52),
    "Area_SouthernForest": (62, 86, 54),
    "Area_TitanForest": (46, 68, 46),
    "Area_WesternDuneForest": (120, 124, 84),
    "Area_RedBambooFields": (124, 92, 62),
    "Area_RedJungle": (102, 78, 54),
    "Area_Swamp": (74, 84, 56),
}

#: How far a biome's colour bleeds into its neighbour's, in texels of the raster (1.83 m
#: each). The game's areas are minimap polygons with mathematically hard edges and nothing
#: in an aerial photograph has one. 24 texels is about 44 m: a tree line's worth of
#: transition, and narrow enough that a 300 m biome is still its own colour in the middle.
BIOME_BLEND_TEXELS = 24.0

#: What the outer coast is drawn as. The game names no biome there, so neither does this: a
#: neutral bleached ground that reads as beach and shelf and lets the relief carry it.
NO_MANS_LAND_RGB = (124, 122, 108)

#: And what an index this file has never heard of resolves to -- a new area in a later
#: build. The same neutral rather than a guessed green, so an unrecognised biome looks
#: unremarkable rather than wrong.
UNKNOWN_BIOME_RGB = NO_MANS_LAND_RGB

#: Bare rock, and the slope band over which the biome's own colour gives way to it: below
#: ROCK_LO_DEG nothing is exposed, above ROCK_HI_DEG the ground is rock whatever grows near
#: it.
ROCK_RGB = np.array([132, 122, 108], np.float32)
ROCK_LO_DEG = 20.0
ROCK_HI_DEG = 42.0

#: The high ground: sun-bleached, thin soil, and pale in imagery. Blended in linearly over
#: this band of metres, to at most HIGH_LIFT of the way to HIGH_RGB.
HIGH_RGB = np.array([206, 200, 184], np.float32)
HIGH_LO_M = 340.0
HIGH_HI_M = 580.0
HIGH_LIFT = 0.20

#: Water seen from above rather than drawn on a map: dark, desaturated, green in the
#: shallows and near-black in the deep. The terrain layer's blue is a cartographer's.
SATELLITE_WATER_SHALLOW = np.array([76, 108, 104], np.float32)
SATELLITE_WATER_DEEP = np.array([22, 44, 62], np.float32)

#: Two octaves of value noise, so a flat biome is not a flat fill. Sampled bilinearly out of
#: two small fixed-seed fields rather than generated per band, so the noise is a property of
#: the world position and no band boundary is visible.
NOISE_SEED = 20260731
NOISE_OCTAVES = ((256, 0.055), (1024, 0.035))
NOISE_SMOOTH = 1.0

#: Rows of the output drawn at a time. 256 rows of 16384 costs about 17 MB of float32 per
#: intermediate; the whole sheet at once would be over a gigabyte apiece.
BAND_ROWS = 256

#: The halo each band is computed with, so the hillshade's gradient at a band edge sees the
#: rows on the other side of it -- and so does the water edge's blur, which reaches further
#: than the gradient does. Cropped off before the band is stored, so no output pixel was
#: computed from a one-sided difference or a truncated kernel. Size it against the widest
#: kernel in the band: too small and the render draws a seam every 256 rows.
BAND_HALO = 8


def load_imaging():
    """Pillow, once ``require_gen`` has shown it is there.

    The size limit goes off because Pillow's default guard is a decompression-bomb rule for
    images off the internet, and here an 8192 px sheet is the point.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return Image


# --------------------------------------------------------------------------------------
# Reading the biome raster out of the container.
# --------------------------------------------------------------------------------------


def read_biome(store, scripts) -> dict:
    """The map-area raster as this file wants it: a numpy square and a name per index.

    The decode, the shape checks and the index -> ``Area_*`` resolution are
    ``core.gameassets.maparea``'s. What this adapter adds is the two things only a renderer
    wants: the raster as a numpy array to index a colour table with, and each index
    flattened to the STEM -- ``Area_RedJungle`` rather than ``Area_RedJungle_2`` -- because
    ``BIOME_COLOURS`` is one colour per kind of ground.
    """
    try:
        areas = read_map_areas(store, scripts)
    except MapAreaError as exc:
        raise SystemExit(
            f"{exc} The satellite layer has no other source for what grows where, so "
            "nothing here can be trusted until that is looked at."
        ) from exc
    raster = np.frombuffer(areas.texels, dtype=np.uint8).reshape(areas.width, areas.width)
    names = [None if area is None else area.stem for area in areas.areas]
    return {
        "width": areas.width,
        "area": raster,
        "palette": [tuple(entry) for entry in areas.palette],
        "names": names,
        # The exact asset per index, kept beside the stem because the staleness check below
        # reads a name map that is keyed by asset -- ``Area_crater_1`` and ``Area_crater_2``
        # are one stem and two different named regions.
        "assets_by_index": [None if area is None else area.asset for area in areas.areas],
        "assets": list(areas.assets),
        "distinct_areas": sorted({n for n in names if n and n != NO_MANS_LAND}),
    }


def biome_colour_field(biome: dict, table: np.ndarray) -> np.ndarray:
    """The raster's indices turned into colour, then blurred so no boundary is a line.

    Done once at the raster's own 4096, one channel at a time -- a Gaussian over three
    float32 channels of that square at once is 200 MB held for no reason -- and kept as
    uint8, which is 50 MB and all the precision a colour has.

    The blur is what makes this a picture rather than a choropleth: the game's areas meet
    along a mathematical line, and drawn straight that line is the most conspicuous thing on
    the render.
    """
    field = np.empty(biome["area"].shape + (3,), np.uint8)
    for channel in range(3):
        blurred = ndimage.gaussian_filter(
            table[:, channel][biome["area"]], BIOME_BLEND_TEXELS, mode="nearest"
        )
        field[..., channel] = np.clip(blurred, 0, 255).astype(np.uint8)
    return field


def biome_lookup(biome: dict) -> tuple[np.ndarray, list[str]]:
    """Per palette index: its RGB in the designed palette, and the name it was drawn as."""
    rgb = np.zeros((len(biome["names"]), 3), np.float32)
    drawn = []
    for index, name in enumerate(biome["names"]):
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


# --------------------------------------------------------------------------------------
# The artwork sheet: the ruler the biome pin is scored against, and the detail the coarse
# provinces borrow.
# --------------------------------------------------------------------------------------


def read_artwork_sheet(store, decoder, image_mod):
    """The game's own 8192 px map sheet, stitched out of its four BC1 slices.

    Read from the container rather than from ``data/local/map.png``: the PNG is the same
    pixels, but it is written by a different tool a reader may not have run, and both things
    the sheet is needed for here -- scoring the biome pin and the detail the coarse
    provinces borrow -- are not optional. The slice names, their layout and the ``.ubulk``
    length check are ``tools/gen_map_image.py``'s.
    """
    sheet = image_mod.new("RGB", (SHEET_PX, SHEET_PX))
    for name in SLICES:
        col, row = (int(value) for value in name.split("_")[1].split("-"))
        slice_image = decode_bc1_rgba(decoder, image_mod, read_slice(store, name), TILE_PX)
        sheet.paste(slice_image.convert("RGB"), (col * TILE_PX, row * TILE_PX))
    return sheet


def artwork_detail(sheet) -> tuple[np.ndarray, dict]:
    """The artwork's luminance high pass as int8, and what scaling it took.

    What comes back is everything in the drawn map FINER than ``BORROW_DETAIL_SIGMA_PX``,
    with the coarse structure removed: the coarse structure is the field's job and two
    sources drawing it at once would double every hillside. Luminance only, so the artwork's
    colour never crosses into a render.

    Held as int8 rather than float32: 67 MB against 268, and 1/127th of two and a half
    standard deviations is finer than any of it survives being multiplied into a colour.
    """
    rgb = np.asarray(sheet, np.float32)
    luma = rgb @ BORROW_LUMA
    high = luma - ndimage.gaussian_filter(luma, BORROW_DETAIL_SIGMA_PX, mode="nearest")
    high = ndimage.gaussian_filter(high, BORROW_DETAIL_SOFTEN_PX, mode="nearest")
    spread = float(high.std())
    detail = (np.tanh(high / max(spread * BORROW_DETAIL_SIGMAS, 1e-6)) * 127.0).astype(np.int8)
    return detail, {
        "role": (
            "the artwork sheet's own luminance minus its Gaussian blur, i.e. everything the "
            "drawn map says below about "
            f"{BORROW_DETAIL_SIGMA_PX * (BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / SHEET_PX:.1f}"
            " m and nothing above it"
        ),
        "sheet_px": SHEET_PX,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / SHEET_PX, 4),
        "high_pass_sigma_px": BORROW_DETAIL_SIGMA_PX,
        "soften_sigma_px": BORROW_DETAIL_SOFTEN_PX,
        "luma_weights": [float(value) for value in BORROW_LUMA],
        "measured_std": round(spread, 4),
        "tanh_knee_at_sigmas": BORROW_DETAIL_SIGMAS,
        "why_tanh": (
            "the artwork is a drawing and a drawing has strokes -- every rock formation is "
            "outlined in hard dark ink. A soft clip lets the mid-tones (the shading) through "
            "almost linearly and saturates the outliers (the ink), which is the difference "
            "between borrowing light and tracing lines"
        ),
        "stored_as": "int8, +-127 at full saturation",
    }


def coarse_province(field) -> tuple[np.ndarray, dict]:
    """Where the field is coarser than the artwork, as a feathered 0..255 mask at 1 m.

    ``BORROW_PROVENANCE`` is a hard label on a 1 m grid and the borrow is a change of
    shading rule, so switching on it directly would draw the LABEL: a visible coastline of
    style around every island the fill layer covers. The mask is blurred at the field's own
    resolution and once, rather than per band with a halo wide enough to hold the kernel.

    Stored as uint8: a weight in [0, 1] about to be multiplied by a detail term that is
    itself quantised to 1/127.
    """
    inside = np.isin(field._prov, BORROW_PROVENANCE)
    share = float(inside.mean())
    feather = ndimage.gaussian_filter(
        inside.astype(np.float32), BORROW_FEATHER_M * 100.0 / field.spacing_cm, mode="nearest"
    )
    return (np.clip(feather, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), {
        "provinces": [hf.PROV_NAMES[value] for value in BORROW_PROVENANCE],
        "share_of_the_field": round(100 * share, 2),
        "feather_m": BORROW_FEATHER_M,
        "role": (
            "1 where the field's own province is coarser than the artwork -- rasterised "
            "collision hulls, or a 3.9 m block raster -- 0 over the landscape layer, which "
            "is continuous geometry and keeps shading of its own, and a Gaussian ramp "
            "between them so the provenance byte is never itself drawn"
        ),
    }


# --------------------------------------------------------------------------------------
# The direct regime: which texels the triangles are allowed to answer, and the surface
# underneath them where they are not.
# --------------------------------------------------------------------------------------


def direct_weight(field, spacing_m: float) -> tuple[np.ndarray | None, dict]:
    """Where the geometry outvotes the kernel, as a feathered 0..255 mask at 1 m.

    ``None`` when the field carries no ``density.u8.z``, which is not "no samples anywhere"
    and must never be read as one: a field written before the plane existed knows nothing
    about its own density, so the caller refuses rather than assumes.

    The rule is the generator's: **one source vertex under the output texel**. The plane
    counts per 1 m texel, so the test is scaled by the output texel's own area, and that is
    why fewer texels qualify at 0.229 m than at 0.458 m.

    A **mask and not a weight**. What decides that the rocks are drawn is their own coverage
    of the pixel; what this decides is what to CALL the answer -- a measurement, or the plane
    of a triangle wider than a texel -- and a provenance label is a yes or a no per texel, so
    it is read nearest and never blurred. On the texels where the worst rims are the density
    is zero by construction, so a weight built from it could not reach them anyway.
    """
    density = field.density_raster()
    if density is None:
        return None, {
            "absent": (
                f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its "
                "texels are measurements and which are the rasteriser interpolating across "
                "a triangle wider than a texel. That is the only thing the two-regime "
                "sampler switches on."
            )
        }
    need = DIRECT_SAMPLES_PER_TEXEL / (spacing_m * spacing_m)
    qualifies = density >= min(need, 255.0)
    share = float(qualifies.mean())
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    return (qualifies.astype(np.uint8) * 255), {
        "plane": hf.DENSITY_NAME,
        "rule": (
            f"at least {DIRECT_SAMPLES_PER_TEXEL:g} source vertex under an output texel of "
            f"{spacing_m:.4f} m, i.e. density >= {need:.2f} per 1 m texel"
        ),
        "samples_min_per_output_texel": DIRECT_SAMPLES_PER_TEXEL,
        "density_min_per_field_texel": round(float(need), 2),
        "qualifying_share_of_the_field": round(100 * share, 3),
        "qualifying_share_of_the_cliff_province": round(
            100 * float(qualifies[cliff].mean()) if cliff.any() else 0.0, 2
        ),
        "role": (
            "1 where the cliff geometry sampled the ground finer than this render draws it, "
            "0 where it did not. Provenance and not a gate: what decides that the rocks are "
            "drawn is their own coverage of the pixel, and this decides what to CALL what "
            "was drawn -- a measurement, or the plane of a triangle wider than a texel."
        ),
    }


def ground_lattice(field, heights: np.ndarray) -> tuple[np.ndarray, dict]:
    """The same heights with the CLIFF province removed, which is the surface underneath.

    This is the kernel regime's real input. Interpolating the whole field over a rim
    reconstructs the **fold**: a texel just outside a rock is still a cliff-top height,
    because a cliff-top texel is one of the four the stencil reads, so the drop stays where
    the 1 m lattice put it at any output resolution. Interpolating the lattice UNDERNEATH --
    the landscape and the fill, which are continuous surfaces the game evaluates itself --
    puts the ground where the ground is and lets the rasterised rock decide its own
    silhouette on top of it.

    The holes this leaves are handled by the sampler: where the 4x4 stencil is not whole it
    falls back to 2x2, where nothing under it is known it says so, and the caller
    substitutes the whole field's fold there -- inside a formation, where the rock covers the
    pixel and answers it anyway.
    """
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    ground = np.where(cliff, np.float32(hf.NODATA), heights).astype(np.float32)
    known = field._height_dm != hf.NODATA
    return ground, {
        "role": (
            "the landscape and fill lattices with the cliff province removed, which is what "
            "the kernel regime interpolates. Interpolating the composed field instead "
            "reconstructs its 1 m fold, and a rim reconstructed from a fold is a 1 m "
            "staircase at any output resolution."
        ),
        "removed_share_of_the_field": round(100 * float(cliff.mean()), 2),
        "lattice_share_of_the_field": round(100 * float((known & ~cliff).mean()), 2),
        "where_it_knows_nothing": (
            "inside a formation big enough that no landscape texel survives under it. There "
            "the whole field's own fold stands in, and the rock's coverage is 1, so the rock "
            "is the answer either way"
        ),
    }


def terrain_lattice(field, ground: np.ndarray) -> tuple[np.ndarray, dict]:
    """``ground`` with its landscape replaced by ``terrain.u16.z``, in float decimetres.

    Written wherever the bare landscape has a sample and the province is landscape or
    cliff, so the lattice under a rock is the real terrain rather than a hole. Fill keeps
    its de-terraced value. A field without the plane comes back unchanged and says so.
    """
    plane = field._plane(hf.TERRAIN_NAME) if hasattr(field, "_plane") else None
    grid = getattr(field, "_terrain_grid", None)
    if plane is None or grid is None or grid.get("row_off") is None:
        return ground, {"absent": f"no usable {hf.TERRAIN_NAME}; the decimetre plane is drawn"}
    rows, cols = plane.shape
    window = (
        slice(grid["row_off"], grid["row_off"] + rows),
        slice(grid["col_off"], grid["col_off"] + cols),
    )
    raw = np.asarray(plane)
    z_dm = ((raw.astype(np.float32) - grid["zero"]) / grid["units_per_m"] + grid["offset_m"]) * (
        hf.DM_PER_M
    )
    prov = np.asarray(field._prov)[window]
    use = (raw != 0) & np.isin(prov, (hf.PROV_LANDSCAPE, *hf.PROV_CLIFF_VALUES))
    out = ground.copy()
    target = out[window]
    target[use] = z_dm[use]
    landscape = use & (prov == hf.PROV_LANDSCAPE)
    moved = np.abs(z_dm[landscape] - np.asarray(field._height_dm)[window][landscape])
    return out, {
        "plane": hf.TERRAIN_NAME,
        "vertical_step_m": round(1.0 / grid["units_per_m"], 5),
        "landscape_texels": int(landscape.sum()),
        "under_cliff_texels": int((use & ~landscape).sum()),
        "landscape_moved_max_m": round(float(moved.max()) / hf.DM_PER_M, 4) if moved.size else 0.0,
    }


def deterraced_height(field) -> tuple[np.ndarray, dict]:
    """The field's heights in decimetres, with the fill province's terraces low-passed out.

    Returned as float32 rather than int16: the terracing this removes is 3.9 m tall and the
    decimetre container would put it straight back as a 0.1 m staircase under a hillshade
    computed at 0.229 m. ``hf.NODATA`` survives as itself, so every sampler below reads this
    raster with the test it read the int16 one with.

    The low pass is **normalised over the province and faded by its own weight**, and both
    halves are load-bearing: a Gaussian that ran over the landscape beside a fill texel would
    drag a 1 m measurement into a 3.9 m raster's answer, and a hard edge at the province
    boundary is the artifact this removes, one province over.

    One convolution rather than a contour trace, because smoothing every level's indicator
    with one kernel and summing is, by the linearity of a convolution, the same array as
    smoothing the level field itself.
    """
    height = field._height_dm.astype(np.float32)
    known = field._height_dm != hf.NODATA
    fill = (field._prov == hf.PROV_FILL) & known
    sigma = FILL_DETERRACE_SIGMA_M * 100.0 / field.spacing_cm
    weight = ndimage.gaussian_filter(fill.astype(np.float32), sigma, mode="nearest")
    total = ndimage.gaussian_filter(np.where(fill, height, 0.0), sigma, mode="nearest")
    smooth = total / np.maximum(weight, 1e-6)
    alpha = np.where(fill, np.clip(weight, 0.0, 1.0), 0.0)
    # Clamped to one quantisation step, and that bound is the definition of the artifact
    # rather than a safety margin. A terrace is a 3.9 m step where the world has a ramp, so
    # un-terracing moves a texel by at most one step; a correction larger than that is not
    # de-terracing, it is a low pass erasing a scarp the fill raster really did resolve --
    # and the fill province holds a 300 m drop at the map's edge that would otherwise be
    # rounded off by half of itself.
    limit = FILL_QUANTISATION_M * hf.DM_PER_M
    moved = np.clip(alpha * (smooth - height), -limit, limit)
    out = np.where(known, height + moved, np.float32(hf.NODATA)).astype(np.float32)
    shifted = np.abs(moved[fill]) / hf.DM_PER_M if fill.any() else np.zeros(1, np.float32)
    clamped = float((shifted >= FILL_QUANTISATION_M - 1e-6).mean()) if fill.any() else 0.0
    return out, {
        "province": hf.PROV_NAMES[hf.PROV_FILL],
        "share_of_the_field": round(100 * float(fill.mean()), 2),
        "cell_m": round(FILL_DETERRACE_SIGMA_M, 4),
        "quantisation_m": round(FILL_QUANTISATION_M, 4),
        "sigma_field_texels": round(sigma, 3),
        "moved_median_m": round(float(np.median(shifted)), 4),
        "moved_p99_m": round(float(np.percentile(shifted, 99)), 4),
        "moved_max_m": round(float(shifted.max()), 4),
        "clamped_share_of_the_province": round(100 * clamped, 3),
        "clamp_m": round(FILL_QUANTISATION_M, 4),
        "role": (
            "the interface raster is 3.66 m cells quantised to 3.9 m in Z, so it draws the "
            "ocean shelf and the map's edge as terraces -- flat plateaus with blocky "
            "outlines that no kernel can un-terrace, because those steps are real in the "
            "data and are not in the world. Low-passed at its own cell size, normalised "
            "over the province so no landscape measurement is dragged into it, and faded by "
            "its own weight so the province boundary is not drawn either."
        ),
        "why_not_marching_squares": (
            "smoothing each level's indicator by one kernel and summing them is, by the "
            "linearity of a convolution, the same array as smoothing the level field "
            "itself. The spline along the marching-squares contour and this convolution are "
            "the same operation, and only one of them needs a polyline extracted from "
            "56 million texels."
        ),
    }


# --------------------------------------------------------------------------------------
# Calibration: where do the biome raster's 4096 texels go?
# --------------------------------------------------------------------------------------


def boundary_mask(labels: np.ndarray) -> np.ndarray:
    """Where one area meets another, grown by one so a one-pixel drift still overlaps."""
    edge = np.zeros(labels.shape, bool)
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    return ndimage.binary_dilation(edge)


def sample_area(area: np.ndarray, box: tuple[float, float, float, float], size: int) -> np.ndarray:
    """The raster resampled onto a ``size`` square over the artwork frame, given a pin.

    ``box`` is the pin under test -- where the raster's own corners are being supposed to be
    -- while the output grid is always the artwork square, because that is the frame the
    ruler is in.
    """
    x0, x1, y0, y1 = box
    ax = BOUNDS_M["x_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    ay = BOUNDS_M["y_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    width = area.shape[0]
    u = ((ax[None, :] - x0) / (x1 - x0) * width).astype(np.int64)
    v = ((ay[:, None] - y0) / (y1 - y0) * width).astype(np.int64)
    u = np.broadcast_to(u, (size, size))
    v = np.broadcast_to(v, (size, size))
    inside = (u >= 0) & (u < width) & (v >= 0) & (v < width)
    out = np.full((size, size), 255, np.uint8)
    out[inside] = area[np.clip(v, 0, width - 1)[inside], np.clip(u, 0, width - 1)[inside]]
    return out


def pinned_box(dx_cm: float, dy_cm: float, scale: float) -> tuple[float, float, float, float]:
    """The artwork square, shifted and scaled about its own centre."""
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = (x1 - x0) / 2 * scale
    return (cx + dx_cm - half, cx + dx_cm + half, cy + dy_cm - half, cy + dy_cm + half)


def calibrate_biome(biome: dict, sheet, image_mod) -> dict:
    """Score the pin by the artwork's own edges, and sweep for one that beats it.

    The statistic is the ratio of the sheet's mean edge strength ON the biome raster's area
    boundaries to its mean edge strength everywhere. A boundary that is pinned right lies on
    a shore or a scarp the map draws; one that is pinned wrong lies on flat fill. Being a
    ratio, it cannot be won by a pin that simply produces more boundary.

    The ruler is the sheet this run decoded out of the container, not the PNG a different
    tool may or may not have written beside it, so the pin is scored on every run.
    """
    grey = np.asarray(
        sheet.convert("L").resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS),
        np.float32,
    )
    gy, gx = np.gradient(ndimage.gaussian_filter(grey, 1.0))
    edge = np.hypot(gx, gy)
    everywhere = float(edge.mean())

    def ratio(dx_cm: float, dy_cm: float, scale: float) -> float:
        labels = sample_area(biome["area"], pinned_box(dx_cm, dy_cm, scale), CALIBRATION_PX)
        return float(edge[boundary_mask(labels)].mean() / everywhere)

    at_pin = ratio(0.0, 0.0, 1.0)
    shifts = range(
        -CALIBRATION_SHIFT_M * 100, CALIBRATION_SHIFT_M * 100 + 1, CALIBRATION_STEP_M * 100
    )
    # The pin is NOT in this maximum: the question is whether anything ELSE does better, so
    # the rival set is every candidate that is not the pin itself.
    best = (0.0, 0, 0)
    for dx in shifts:
        for dy in shifts:
            if dx == 0 and dy == 0:
                continue
            score = ratio(dx, dy, 1.0)
            if score > best[0]:
                best = (score, dx, dy)
    scales = {f"x{scale:.2f}": ratio(0.0, 0.0, scale) for scale in CALIBRATION_SCALES}
    rivals = max([best[0], *scales.values()])
    return {
        "method": (
            "the artwork sheet's mean edge strength over the biome raster's area boundaries, "
            "divided by its mean edge strength everywhere. A boundary pinned right sits on a "
            "shore or a scarp the map draws; one pinned wrong sits on flat fill. A ratio, so "
            "a pin cannot win it by making more boundary."
        ),
        "ruler": (
            f"the game's own {SHEET_PX} px map sheet, decoded from its four BC1 slices in "
            "this same run"
        ),
        "resolution_px": CALIBRATION_PX,
        "edge_ratio_at_the_pin": round(at_pin, 4),
        "sweep": (
            f"+-{CALIBRATION_SHIFT_M} m in {CALIBRATION_STEP_M} m steps at true scale, plus "
            + ", ".join(f"x{scale:.2f}" for scale in CALIBRATION_SCALES)
        ),
        "best_rival_shift_m": {"dx": best[1] / 100, "dy": best[2] / 100},
        "edge_ratio_at_the_best_rival_shift": round(best[0], 4),
        "edge_ratio_at_other_scales": {key: round(value, 4) for key, value in scales.items()},
        "margin_over_the_best_rival": round(at_pin / rivals, 4) if rivals else None,
        "margin_required": CALIBRATION_MARGIN,
        "pin_holds": at_pin >= rivals * CALIBRATION_MARGIN,
        "pin": dict(BOUNDS_M),
        "metres_per_texel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / biome["width"], 4),
        "reading": (
            "the raster spans exactly the square the artwork does. Every rival -- a shift in "
            "either direction, a box 5% larger, a box 5% smaller -- reads far below it, "
            "which is what makes this a measurement rather than a preference. pin_holds "
            "false would mean the texture moved, and the run says so instead of quietly "
            "colouring the world off its own biomes."
        ),
    }


def region_table_is_current(biome: dict) -> dict:
    """Is the committed 256 m region table still this raster's own majority downsample?

    ``data/region_names.json`` is derived from THIS asset by ``tools/gen_region_names.py``,
    so agreement is not evidence about the pin: it is either 100% or the committed table was
    cut from a different build, and this is the run that notices. The name policy is read out
    of the table's own ``_meta`` rather than imported, so this stays a check on the artifact
    rather than a second copy of the rules that made it.
    """
    if not REGION_TABLE.is_file():
        return {"skipped": f"{REGION_TABLE.name} is not present, so nothing was compared"}
    table = json.loads(REGION_TABLE.read_text(encoding="utf-8"))
    display = (table.get("_meta") or {}).get("area_display_names")
    if not isinstance(display, dict):
        return {"skipped": f"{REGION_TABLE.name} carries no _meta.area_display_names to read"}
    meta, grid, legend = table["grid_meta"], table["region_grid"], table["legend"]
    cell, gx0, gy0 = meta["cell"], meta["x0"], meta["y0"]
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    width, assets = biome["width"], biome["assets_by_index"]

    compared = agree = 0
    disagreements: dict[str, int] = {}
    for j, row in enumerate(grid):
        for i, letter in enumerate(row):
            if letter == meta["void"]:
                continue
            u = [round((gx0 + (i + k) * cell - x0) / (x1 - x0) * width) for k in (0, 1)]
            v = [round((gy0 + (j + k) * cell - y0) / (y1 - y0) * width) for k in (0, 1)]
            u = [max(0, min(width, value)) for value in u]
            v = [max(0, min(width, value)) for value in v]
            if u[1] <= u[0] or v[1] <= v[0]:
                continue
            values, counts = np.unique(biome["area"][v[0] : v[1], u[0] : u[1]], return_counts=True)
            asset = assets[int(values[counts.argmax()])]
            compared += 1
            want = legend[letter]
            got = display.get(asset or "", display.get("", want))
            if got == want:
                agree += 1
            else:
                key = f"{want} -> {got}"
                disagreements[key] = disagreements.get(key, 0) + 1
    worst = sorted(disagreements.items(), key=lambda kv: -kv[1])[:5]
    return {
        "source": "data/region_names.json, derived from this same asset by gen_region_names.py",
        "cells_compared": compared,
        "cells_agreeing": agree,
        "agreement_pct": round(100 * agree / compared, 1) if compared else None,
        "largest_disagreements": [f"{key} ({count} cells)" for key, count in worst],
        "table_is_current": compared > 0 and agree == compared,
        "reading": (
            "100% or the committed region table was cut from a different build of this "
            "asset, and the fix is to re-run tools/gen_region_names.py. Not a pin and never "
            "was: the corners come from the edge ratio next door."
        ),
    }


# --------------------------------------------------------------------------------------
# Sampling the field onto the output frame.
# --------------------------------------------------------------------------------------


def frame_coordinates(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre world coordinates, centimetres, for a ``size`` square on the frame.

    Row 0 is the northern edge and column 0 the western one, which is the artwork sheet's
    order and the heightfield's: game +Y is south, so the smallest y is the top of the
    picture. Nothing here flips anything.
    """
    x = BOUNDS_M["x_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    y = BOUNDS_M["y_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    return x, y


def grid_position(coordinate: np.ndarray, origin: float, spacing: float, limit: int):
    """Where a run of world coordinates falls on a raster's index axis, clamped to it.

    Clamped rather than masked: the frame is half a metre wider than the field's last vertex
    on two sides, so masking would invent a half-pixel strip of no-data down the east and
    south edges. Past the edge the nearest vertex is what the field's own reader gives too.
    """
    return np.clip((coordinate - origin) / spacing, 0.0, limit - 1.0)


def taps_linear(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The two flanking indices and their weights: ``(2, N)`` each. Plain bilinear.

    What the cubic kernel falls back to where its stencil runs off the data, and what every
    category plane is sampled with: a coverage fraction outside [0, 1] is not one.
    """
    low = np.minimum(np.floor(position).astype(np.int64), max(limit - 2, 0))
    fraction = (position - low).astype(np.float32)
    index = np.stack([low, np.minimum(low + 1, limit - 1)])
    return index, np.stack([1.0 - fraction, fraction])


def taps_cubic(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The four indices around a position and their Catmull-Rom weights: ``(4, N)`` each.

    Cubic convolution with a = -1/2, the interpolating member of that family: it passes
    through every sample it is given and it is **C1**. The hillshade is a function of the
    first derivative, and a C0 kernel sampled at half its own texel spacing rules the relief
    into 1 m squares. The weights sum to exactly one, which is what lets the no-data
    bookkeeping below use their sum as a completeness test.

    Indices are clamped to the grid, so a stencil hanging off the edge repeats the edge
    vertex -- the same answer the field's own reader gives past its last row.
    """
    base = np.floor(position).astype(np.int64)
    t = (position - base).astype(np.float32)
    index = np.stack([np.clip(base + offset, 0, limit - 1) for offset in (-1, 0, 1, 2)])
    weight = np.stack(
        [
            0.5 * t * (t * (2.0 - t) - 1.0),
            0.5 * (t * t * (3.0 * t - 5.0) + 2.0),
            0.5 * t * (t * (4.0 - 3.0 * t) + 1.0),
            0.5 * t * t * (t - 1.0),
        ]
    )
    return index, weight


def resample(raster: np.ndarray, rows, cols, nodata: int | None):
    """Separable interpolation of ``raster`` onto the output grid. Returns (sum, weight).

    Separable, and in that order: the output rows a band needs come from one CONTIGUOUS run
    of source rows, so the row axis is a slice and only the column axis is a gather.
    Interpolating in x first over that short slab and in y second over the result is four
    gathers of the small array and four of the large one, against sixteen of the large one
    if the 4x4 stencil were evaluated directly.

    The no-data bookkeeping rides along: every tap is multiplied by whether its texel had a
    value, and the weights come back separately, so the caller can tell a whole stencil
    (weight exactly one) from a partial one from nothing at all. ``nodata`` of ``None`` says
    the raster has no holes -- a category coverage plane -- and skips it.
    """
    (row_index, row_weight), (col_index, col_weight) = rows, cols
    low, high = int(row_index.min()), int(row_index.max())
    slab = raster[low : high + 1]
    values = slab.astype(np.float32)
    known = None if nodata is None else (slab != nodata).astype(np.float32)
    if known is not None:
        values = values * known

    across = np.zeros((slab.shape[0], col_index.shape[1]), np.float32)
    across_weight = np.zeros_like(across)
    for tap in range(col_index.shape[0]):
        across += col_weight[tap] * values[:, col_index[tap]]
        if known is None:
            across_weight += col_weight[tap]
        else:
            across_weight += col_weight[tap] * known[:, col_index[tap]]

    total = np.zeros((row_index.shape[1], col_index.shape[1]), np.float32)
    total_weight = np.zeros_like(total)
    for tap in range(row_index.shape[0]):
        picked = row_index[tap] - low
        total += row_weight[tap][:, None] * across[picked]
        total_weight += row_weight[tap][:, None] * across_weight[picked]
    return total, total_weight


#: How far the cubic stencil's own weights may fall from one before this file stops
#: believing it. They sum to one exactly wherever every texel under the stencil has a value,
#: so anything below this is a stencil straddling the edge of the data, where a kernel with
#: negative lobes has no business extrapolating.
STENCIL_WHOLE = 1.0 - 1e-4


def sample_surface(raster: np.ndarray, cubic, linear, nodata: int):
    """A height raster on the output grid: cubic inside the data, bilinear at its edge.

    Returns ``(values, missing)``. Where the 4x4 stencil is whole the C1 answer is used;
    where it is not -- a fifth of this field is no-data, so that boundary is long -- the 2x2
    answer is, because a cubic kernel has negative lobes and one straddling a hole
    overshoots; and where even that has nothing under it the caller paints the page's sea.
    """
    smooth, smooth_weight = resample(raster, *cubic, nodata)
    flat, flat_weight = resample(raster, *linear, nodata)
    missing = flat_weight <= 0.0
    near = flat / np.where(missing, 1.0, flat_weight)
    return np.where(smooth_weight >= STENCIL_WHOLE, smooth, near), missing


def sample_plain(raster: np.ndarray, taps) -> np.ndarray:
    """A raster with no holes in it, interpolated onto the output grid. Nothing clipped.

    The weights of either kernel sum to one and there is no no-data to renormalise around,
    so the weighted sum IS the answer. For the two rasters this file makes itself: the
    artwork's signed high pass and the feathered province mask, neither of which is a
    coverage and neither of which may be clipped into [0, 1] on the way through.
    """
    return resample(raster, *taps, None)[0]


def sample_coverage(plane: np.ndarray, taps) -> np.ndarray:
    """What fraction of the ground under each output pixel is in some category, in [0, 1].

    Bilinear and never cubic: a category is a yes or a no on a 1 m grid, and what is wanted
    from it is coverage. A kernel with negative lobes would answer -0.06 of a texel wet,
    which is not a thing a texel can be.
    """
    return np.clip(sample_plain(plane, taps), 0.0, 1.0)


# --------------------------------------------------------------------------------------
# The direct pass: the same rocks the field is built from, rasterised into THIS grid.
# --------------------------------------------------------------------------------------


def read_cliff_geometry(store, scripts, index, classes, progress: bool = True) -> dict:
    """The world's placements and the finest triangles every placed rock ships.

    Two calls into ``tools/gen_world_heightmap.py``: the same pass over the same 4,521
    ``*.umap`` the field was cut from, and the same finest-source ladder over the same
    hull-equivalent mesh set.

    The one thing done here is the generator's per-triangle bounds clamp, hoisted out of the
    placement loop. It is a test in the mesh's own local space against the mesh's own padded
    ``ExtendedBounds``, so it gives the same answer for all two hundred copies of a rock.
    """
    sweep = gen.sweep_levels(
        store, scripts, classes, gen.MeshBounds(store, scripts, index), progress
    )
    read = gen.read_mesh_geometry(store, scripts, index, sweep["meshes"], progress)
    geometry: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    clamped = 0
    for mesh, (verts, tris, low, high) in read["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
        if tris.size:
            geometry[mesh] = (verts, np.ascontiguousarray(tris))
    return {
        "sweep": sweep,
        "geometry": geometry,
        "meshes": len(geometry),
        "by_source": read["by_source"],
        "verts": int(sum(v.shape[0] for v, _t in geometry.values())),
        "tris": int(sum(t.shape[0] for _v, t in geometry.values())),
        "triangles_out_of_bounds": clamped,
        "seconds_sweep": round(sweep["seconds"], 1),
        "seconds_decode": round(read["seconds"], 1),
    }


def direct_placements(sweep: dict, geometry: dict) -> tuple[list, dict]:
    """Every placement the field's own cliff layer rasterises, with its world Y span.

    The four culls are the generator's, in the generator's order: an excluded owner, a mesh
    with no cooked geometry, an arch, an oversized shell. Any of them applied differently
    here would draw a render of a different world from the field it is blended with.

    What is added is the **Y span**, in world centimetres, of the placement's transformed
    vertex box. That is the whole of the band selection: a bounding-interval test over
    24,000 placements is a numpy comparison rather than a search.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if gen.ARCH_MARK in m.rsplit("/", 1)[-1]}
    windings = {mesh: gen.winding_sign(v, t) for mesh, (v, t) in geometry.items()}
    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    prepared: list[tuple] = []
    dropped = {"owner": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    for row in sweep["placements"]:
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in gen.EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh not in geometry:
            dropped["no_geometry"] += 1
            continue
        if mesh_id in arch_ids:
            dropped["arch"] += 1
            continue
        verts, _tris = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > gen.OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = gen.rotation_matrix(*row[5:8]).astype(np.float32)
        offset = row[2:5].astype(np.float32)
        low, high = verts.min(0), verts.max(0)
        box = low + corners * (high - low)
        world_y = ((box * scale) @ matrix + offset)[:, 1]
        facing = windings[mesh] * float(np.sign(scale[0] * scale[1] * scale[2]))
        prepared.append(
            (
                mesh,
                mesh_id,
                matrix,
                scale,
                offset,
                facing,
                float(world_y.min()),
                float(world_y.max()),
            )
        )
    return prepared, dropped


def rasterise_direct_band(
    prepared: list,
    geometry: dict,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
) -> np.ndarray:
    """One band of the output, max-Z rasterised from the triangles. ``nan`` where none fell.

    The rasteriser is ``gen_world_heightmap.MaxZRaster`` itself, pointed at a grid whose
    origin is this band's north-west corner and whose spacing is this render's, divided by
    the sub-sampling. Sampled at ``col + 0.5`` in grid units and written to ``col``
    (``sample=0.5``), which is exactly ``frame_coordinates``' pixel centres when the origin is the frame's
    own corner, so nothing is half a texel out.

    The facing cull runs per placement as it does in the generator, and then the triangles
    are cut down to the ones whose own Y interval reaches this band.
    """
    raster = gen.MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples, sample=0.5
    )
    add_placements(raster, prepared, geometry, y0_cm, y0_cm + rows * scale_cm)
    return raster.result()[0]


def add_placements(raster, prepared: list, geometry: dict, y_lo: float, y_hi: float) -> None:
    """Every prepared placement whose Y span reaches ``[y_lo, y_hi]``, into ``raster``."""
    for mesh, mesh_id, matrix, scale, offset, facing, span_lo, span_hi in prepared:
        if span_hi < y_lo or span_lo > y_hi:
            continue
        verts, tris = geometry[mesh]
        world = (verts * scale) @ matrix + offset
        if facing != 0.0:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * facing) > 0]
            if not tris.size:
                continue
        ty = world[:, 1][tris]
        tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
        if not tris.size:
            continue
        raster.add(world[tris], mesh_id + 1)


def top_items(store, scripts, index, sweep: dict, geometry: dict) -> tuple[dict, dict]:
    """The arches and foliage boulders ``top.i16.z`` carries, at their finest geometry.

    Arches are placements, prepared like rocks but never culled by facing: an arch is an
    open shell often enough that a winding guess would drop its deck. Boulders are foliage
    instances with their own 4x4 matrices. Both fall back to the collision trimesh the
    field rasterised when no finer source decodes.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if gen.ARCH_MARK in m.rsplit("/", 1)[-1]}
    shapes: dict[str, tuple[np.ndarray, np.ndarray] | None] = dict(geometry)
    finest = gen.read_mesh_geometry(store, scripts, index, list(sweep["foliage"]), False)
    for mesh, (verts, tris, low, high) in finest["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        shapes[mesh] = (verts, tris[keep[tris].all(axis=1)])

    def shape(mesh):
        if mesh not in shapes:
            shapes[mesh] = gen.read_hull(store, scripts, index, mesh)
        return shapes[mesh]

    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    arches, skipped = [], set()
    for row in sweep["placements"]:
        mesh_id = int(row[0])
        if mesh_id not in arch_ids or owners[int(row[1])] in gen.EXCLUDED_OWNERS:
            continue
        mesh = meshes[mesh_id]
        if shape(mesh) is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        verts = shapes[mesh][0]
        matrix = gen.rotation_matrix(*row[5:8]).astype(np.float32)
        scale, offset = row[8:11].astype(np.float32), row[2:5].astype(np.float32)
        low, high = verts.min(0), verts.max(0)
        world_y = (((low + corners * (high - low)) * scale) @ matrix + offset)[:, 1]
        arches.append(
            (mesh, mesh_id, matrix, scale, offset, 0.0, float(world_y.min()), float(world_y.max()))
        )
    boulders = {}
    for mesh, mats in sweep["foliage"].items():
        if shape(mesh) is None:
            skipped.add(mesh.rsplit("/", 1)[-1])
            continue
        mats = np.asarray(mats, np.float32)
        reach = float(np.linalg.norm(shapes[mesh][0], axis=1).max())
        reach *= np.linalg.norm(mats[:, :3, :3], axis=2).max(1)
        boulders[mesh] = (mats, mats[:, 3, 1] - reach, mats[:, 3, 1] + reach)
    items = {"arches": arches, "boulders": boulders, "shapes": shapes}
    return items, {
        "arch_placements": len(arches),
        "foliage_instances": int(sum(len(v[0]) for v in boulders.values())),
        "foliage_sources": finest["sources"],
        "meshes_skipped": sorted(skipped),
    }


def rasterise_top_band(
    items: dict, x0_cm: float, y0_cm: float, scale_cm: float, rows: int, cols: int, subsamples: int
) -> np.ndarray:
    """One band of arches and boulders, max-Z on the render's pixel centres."""
    raster = gen.MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples, sample=0.5
    )
    y_hi = y0_cm + rows * scale_cm
    add_placements(raster, items["arches"], items["shapes"], y0_cm, y_hi)
    for mesh, (mats, span_lo, span_hi) in items["boulders"].items():
        verts, tris = items["shapes"][mesh]
        picked = mats[(span_hi >= y0_cm) & (span_lo <= y_hi)]
        for start in range(0, len(picked), TOP_FOLIAGE_BATCH):
            chunk = picked[start : start + TOP_FOLIAGE_BATCH]
            world = np.einsum("vi,nij->nvj", verts, chunk[:, :3, :3]) + chunk[:, None, 3, :3]
            raster.add(world[:, tris].reshape(-1, 3, 3), 1)
    return raster.result()[0]


def composite_top(z_m, top_z_cm, top_coverage, subsamples: int = 1) -> np.ndarray:
    """``z_m`` raised by the top raster through the same coverage and smoothed lift as rocks."""
    z_cm, fraction = top_z_cm, pixel_coverage(top_coverage, subsamples)
    w = np.clip(fraction, 0.0, 1.0)
    delta = z_cm / np.float32(100.0) - z_m
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    return (z_m + w * 0.5 * (delta + np.sqrt(delta * delta + knee * knee))).astype(np.float32)


def reduce_direct(sub_z: np.ndarray, rows: int, cols: int, subsamples: int):
    """A sub-sampled band folded onto the output grid: mean height and coverage count.

    The mean is over the sub-samples that HIT something and the count comes back beside it,
    which is the difference between "half a rock and half the ground behind it" and "a rock
    at half its height".
    """
    if subsamples == 1:
        hit = np.isfinite(sub_z)
        return np.where(hit, sub_z, 0.0).astype(np.float32), hit.astype(np.uint8)
    block = sub_z.reshape(rows, subsamples, cols, subsamples)
    hit = np.isfinite(block)
    count = hit.sum((1, 3)).astype(np.uint8)
    total = np.where(hit, block, 0.0).sum((1, 3), dtype=np.float32)
    return (total / np.maximum(count, 1)).astype(np.float32), count


def pixel_coverage(coverage: np.ndarray, subsamples: int) -> np.ndarray:
    """The share of a pixel's sub-samples a triangle hit, in [0, 1]. No neighbour is read."""
    return coverage.astype(np.float32) / np.float32(subsamples * subsamples)


def direct_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / DIRECT_CACHE_DIR_NAME


def top_cache_dir(out_dir: Path, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / TOP_CACHE_DIR_NAME


def direct_cache_stamp(size: int, subsamples: int, build: str | None) -> dict:
    """What a cached direct raster has to agree with before it is drawn from.

    Three things, each of which is a different picture if it moves: the grid it was
    rasterised onto, how finely it sampled each texel of that grid, and the build of the game
    whose rocks it is. Everything else in the sidecar is a record rather than a key.
    """
    return {"size": int(size), "subsamples": int(subsamples), "game_version_pinned": build}


def cached_direct(directory: Path, stamp: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """The cached raster as two read-only memory maps, or ``None`` if it is not this one."""
    try:
        recorded = json.loads((directory / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    size = stamp["size"]
    try:
        return (
            np.memmap(directory / DIRECT_Z_NAME, np.float32, "r", shape=(size, size)),
            np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "r", shape=(size, size)),
        )
    except (OSError, ValueError):
        return None


def rasterise_direct(
    band_raster,
    directory: Path,
    size: int,
    subsamples: int,
    stamp: dict,
    progress: bool,
) -> dict:
    """Rasterise every placed rock into the render's own grid, banded, onto disk.

    Banded because a 32768 square of float32 is 4.3 GB and the render already holds 3.2 GB
    of output; 256 rows is 34 MB. On disk because the answer is the same for both layers and
    rasterising 216 M triangles is twenty minutes. The two maps are written beside a sidecar
    naming what they are of, and ``cached_direct`` refuses anything that does not match
    rather than drawing last week's rocks under this week's field.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / DIRECT_CACHE_SIDECAR).unlink(missing_ok=True)
    z = np.memmap(directory / DIRECT_Z_NAME, np.float32, "w+", shape=(size, size))
    coverage = np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "w+", shape=(size, size))
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    x0_cm = BOUNDS_M["x_min_m"] * 100
    covered = 0
    started = time.time()
    for band, top in enumerate(range(0, size, DIRECT_BAND_ROWS)):
        bottom = min(top + DIRECT_BAND_ROWS, size)
        rows = bottom - top
        sub = band_raster(
            x0_cm,
            BOUNDS_M["y_min_m"] * 100 + top * step_cm,
            step_cm,
            rows,
            size,
            subsamples,
        )
        band_z, band_coverage = reduce_direct(sub, rows, size, subsamples)
        z[top:bottom] = band_z
        coverage[top:bottom] = band_coverage
        covered += int(np.count_nonzero(band_coverage))
        if progress and band % 8 == 0:
            print(
                f"  {directory.name}: {bottom / size:5.1%} of {size}x{size} at "
                f"{step_cm / 100 / subsamples:.4f} m, {covered / 1e6:.1f} M texels, "
                f"{time.time() - started:5.1f}s",
                flush=True,
            )
    z.flush()
    coverage.flush()
    del z, coverage
    stats = {
        **stamp,
        "sub_texel_m": round(step_cm / 100 / subsamples, 5),
        "texels_with_geometry": covered,
        "share_of_the_sheet": round(100 * covered / (size * size), 3),
        "seconds": round(time.time() - started, 1),
        "band_rows": DIRECT_BAND_ROWS,
        "role": (
            "max-Z of the cliff geometry on this render's own grid, in world centimetres, "
            "with the count of sub-samples that hit something beside it. Written once and "
            "read by every layer; deleted at the end of the run unless --keep-direct."
        ),
    }
    (directory / DIRECT_CACHE_SIDECAR).write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


# --------------------------------------------------------------------------------------
# The seam, measured along a line rather than at a probe.
# --------------------------------------------------------------------------------------


class SeamTrace:
    """Second differences of the drawn height at the join, and what they can be read against.

    A trace rather than probes: probes are sparse relative to a seam, and a ridge one texel
    wide along a density contour is invisible to all of them. What is measured is
    ``|d2z/dx2|`` along every row of every band over the whole square, with each 3-texel
    stencil sorted by the weights under all three of its texels.

    **Read the ratios against the switch, not against the pure regimes.** This file
    composites a rock onto a lattice by the rock's own coverage, so the join IS the rock's
    silhouette -- a real cliff edge, where enormous curvature is the correct answer, and the
    pure-regime ratio reads 138 on the shipped render with every bit of that terrain.
    Restricting the comparison to where the two surfaces agree only moves the join to the
    rock's base, which is also real. The hard ``max`` over the same texels is the one
    reference that is not the terrain, and it is not a gate either: a convex blend cannot be
    rougher than the switch between its own extreme points, so the number says how much of
    that ceiling the fade spends (0.5 on the shipped render).

    The smoothness itself is guaranteed by the arithmetic rather than by this statistic:
    ``blend_regimes`` is a convex combination in the pixel's coverage, plus a positive part
    smoothed by ``DIRECT_LIFT_KNEE_M``.
    """

    def __init__(self) -> None:
        self.pools: dict[str, list[np.ndarray]] = {
            "seam": [],
            "switch": [],
            "pure_direct": [],
            "pure_kernel": [],
            "seam_same_surface": [],
            "pure_same_surface": [],
        }
        self.rows = 0

    @staticmethod
    def _thin(values: np.ndarray) -> np.ndarray:
        """A systematic sample of a pool, so the whole sheet costs a bounded number of MB.

        Every k-th value of a selection already in raster order, which for a percentile is a
        sample rather than a filter.
        """
        stride = max(1, values.size // SEAM_SAMPLE_MAX_PER_BAND)
        return values[::stride].astype(np.float32)

    def _keep(self, name: str, curvature: np.ndarray, mask: np.ndarray) -> None:
        if mask.any():
            self.pools[name].append(self._thin(curvature[mask]))

    def add(self, z_m, z_switched, w, spacing_m: float, delta=None) -> None:
        blended = np.abs(np.diff(z_m, n=2, axis=1)) / (spacing_m * spacing_m)
        switched = np.abs(np.diff(z_switched, n=2, axis=1)) / (spacing_m * spacing_m)
        # A second difference reads three texels, so it belongs to the regime all three of
        # them are in, and to the join if they are not all in one. Classify by the middle
        # weight alone and a sharp join hides: its curvature lands one texel to the side, in
        # a stencil whose middle texel is still pure.
        low, middle, high = w[:, :-2], w[:, 1:-1], w[:, 2:]
        top = np.maximum(np.maximum(low, middle), high)
        bottom = np.minimum(np.minimum(low, middle), high)
        at_seam = (top > SEAM_PURE) & (bottom < 1.0 - SEAM_PURE)
        self.rows += z_m.shape[0]
        if not at_seam.any():
            return
        near = ndimage.maximum_filter1d(at_seam, 2 * SEAM_NEAR_TEXELS + 1, axis=1, mode="nearest")
        self._keep("seam", blended, at_seam)
        self._keep("switch", switched, at_seam)
        self._keep("pure_direct", blended, near & (bottom >= 1.0 - SEAM_PURE))
        self._keep("pure_kernel", blended, near & (top <= SEAM_PURE))
        if delta is None:
            return
        gap = np.abs(delta)
        same = np.minimum(np.minimum(gap[:, :-2], gap[:, 1:-1]), gap[:, 2:]) <= SEAM_SAME_SURFACE_M
        self._keep("seam_same_surface", blended, at_seam & same)
        self._keep("pure_same_surface", blended, near & same & ~at_seam)

    def result(self) -> dict:
        pooled = {
            name: (np.concatenate(values) if values else np.zeros(0, np.float32))
            for name, values in self.pools.items()
        }
        p99 = {
            name: float(np.percentile(values, 99)) if values.size else None
            for name, values in pooled.items()
        }
        if p99["seam"] is None or not p99["switch"]:
            return {
                "measured": False,
                "why": (
                    "no 3-texel stencil straddled the join, which is what a render with no "
                    "rocks in it looks like"
                ),
            }

        def ratio(over: str) -> float | None:
            return None if not p99[over] else round(p99["seam"] / p99[over], 4)

        reference = [p99["pure_direct"], p99["pure_kernel"]]
        beside = max([v for v in reference if v is not None], default=None)
        return {
            "measured": True,
            "method": (
                "|d2z/dx2| along every row of the drawn height, in 1/m. Every 3-texel "
                "stencil is sorted by the weights under ALL THREE of its texels: straddling "
                f"the join, wholly direct (every weight within {SEAM_PURE} of 1) or wholly "
                f"kernel (every weight within {SEAM_PURE} of 0). The two pure pools are "
                f"further restricted to within {SEAM_NEAR_TEXELS} texels of a straddling "
                "stencil. A fourth pool is the height a HARD MAX would have drawn over the "
                "straddling stencils themselves."
            ),
            "texels": {name: int(values.size) for name, values in pooled.items()},
            "p99_curvature": {
                name: (None if value is None else round(value, 5)) for name, value in p99.items()
            },
            "share_of_a_hard_switch": ratio("switch"),
            "share_of_a_hard_switch_ceiling": SEAM_SWITCH_CEILING,
            "against_the_pure_regimes": (None if not beside else round(p99["seam"] / beside, 4)),
            "against_the_terrain_where_the_surfaces_agree": ratio("pure_same_surface"),
            "surfaces_agree_within_m": SEAM_SAME_SURFACE_M,
            "reading": (
                "share_of_a_hard_switch is the number to read and it is a DESCRIPTION, not a "
                "gate: a convex blend of two surfaces cannot be rougher than the switch "
                "between them, because rounding the weight to 0 or 1 is the extreme point of "
                "that blend, so this can never exceed 1 and never fail. What it says is how "
                "much of that ceiling the fade spends. The two numbers beside it are the "
                "design's own reference and a repair of it, and both measure the TERRAIN "
                "rather than the sampler once the composition is by coverage, because then "
                "every join lies on a geometric feature -- the rock's silhouette, or its "
                "base. They are recorded because they were tried. What guarantees the "
                "smoothness is the arithmetic: a convex combination in a continuously "
                "reconstructed coverage, plus a positive part smoothed to C-infinity."
            ),
        }


class RegimeCoverage:
    """How much of the sheet each regime drew, per province of the field underneath it.

    Counted rather than argued, because "the geometry answers this pixel" is a claim about
    how much of a picture. The provinces are the field's own, sampled nearest at output
    resolution: a province is a name and the average of two names is not one.

    The direct bucket is **split by the density plane**, which is all that plane does here.
    It does not decide whether the triangles are drawn -- the rock's own coverage of the
    pixel decides that -- it decides what the drawn answer IS: a texel a source vertex landed
    in is a measurement, and one the rasteriser reached across a triangle wider than itself
    is a facet. The sidecar says which is which rather than letting a reader assume.
    """

    def __init__(self) -> None:
        self.counts: dict[int, list[int]] = {}
        self.weight: dict[int, float] = {}

    def add(self, prov: np.ndarray, w: np.ndarray, measured: np.ndarray) -> None:
        regime = np.where(
            w >= 1.0 - SEAM_PURE,
            np.where(measured, 0, 1),
            np.where(w > SEAM_PURE, 2, 3),
        )
        for value in np.unique(prov):
            key = int(value)
            row = self.counts.setdefault(key, [0, 0, 0, 0])
            here = prov == value
            picked = regime[here]
            for index in range(4):
                row[index] += int(np.count_nonzero(picked == index))
            self.weight[key] = self.weight.get(key, 0.0) + float(w[here].sum())

    NAMES = ("direct_measured", "direct_facet", "faded", "kernel")

    def result(self) -> dict:
        total = sum(sum(row) for row in self.counts.values()) or 1
        out = {
            "definition": (
                f"direct: coverage >= {1 - SEAM_PURE}, split by density.u8.z into the texels "
                "a source vertex landed in (a measurement) and the texels the rasteriser "
                "reached across a triangle wider than the output texel (a facet); faded: "
                f"{SEAM_PURE} < coverage < {1 - SEAM_PURE}, a silhouette; kernel: coverage "
                f"<= {SEAM_PURE}, the landscape and fill lattices alone. mean_w beside them "
                "is the unbucketed answer: how much of the height over that province the "
                "rasterised rocks contributed, averaged."
            ),
            "per_province_pct_of_sheet": {},
        }
        for value, row in sorted(self.counts.items()):
            name = hf.PROV_NAMES.get(value, f"layer {value}")
            here = sum(row) or 1
            out["per_province_pct_of_sheet"][name] = {
                **{key: round(100 * row[i] / total, 4) for i, key in enumerate(self.NAMES)},
                "province_pct_of_sheet": round(100 * here / total, 4),
                "mean_w": round(self.weight.get(value, 0.0) / here, 5),
            }
        pooled = [sum(row[i] for row in self.counts.values()) for i in range(4)]
        out["sheet_pct"] = {
            **{key: round(100 * pooled[i] / total, 4) for i, key in enumerate(self.NAMES)},
            "mean_w": round(sum(self.weight.values()) / total, 5),
        }
        return out


def hillshade(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """North-west relief in [SHADE_FLOOR, SHADE_FLOOR + SHADE_RANGE].

    A dot product against the surface normal rather than slope-and-aspect trigonometry,
    because the array's axes are unambiguous and compass angles are not: rows run south,
    columns run east, and the sun sits north-west. Getting that backwards inverts every
    valley on the map and still looks like terrain.
    """
    azimuth = np.deg2rad(SUN_AZIMUTH_DEG)
    altitude = np.deg2rad(SUN_ALTITUDE_DEG)
    light = np.array(
        [
            np.cos(altitude) * np.sin(azimuth),  # east
            -np.cos(altitude) * np.cos(azimuth),  # south
            np.sin(altitude),  # up
        ],
        np.float32,
    )
    d_south, d_east = np.gradient(z_m, spacing_m)
    lit = (-d_east * light[0] - d_south * light[1] + light[2]) / np.sqrt(
        d_east * d_east + d_south * d_south + 1.0
    )
    return np.clip(lit, 0.0, 1.0) * SHADE_RANGE + SHADE_FLOOR


def slope_degrees(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """How steep the ground is, in degrees. The satellite layer's rock rule reads this."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    return np.degrees(np.arctan(np.hypot(d_east, d_south)))


def ramp(values: np.ndarray, stops: np.ndarray) -> np.ndarray:
    """Linear interpolation along a colour ramp, ``values`` in [0, 1]."""
    position = np.clip(values, 0.0, 1.0) * (len(stops) - 1)
    low = np.clip(np.floor(position), 0, len(stops) - 2).astype(np.int64)
    fraction = (position - low)[..., None].astype(np.float32)
    return stops[low] * (1 - fraction) + stops[low + 1] * fraction


def noise_fields(rng_seed: int) -> list[tuple[np.ndarray, float]]:
    """The value-noise octaves, made once and sampled by world position afterwards.

    Noise generated per band would put a different random field on either side of every band
    boundary and draw 31 horizontal seams across the world.
    """
    rng = np.random.default_rng(rng_seed)
    fields = []
    for size, amount in NOISE_OCTAVES:
        field = rng.standard_normal((size, size), dtype=np.float32)
        fields.append((ndimage.gaussian_filter(field, NOISE_SMOOTH, mode="wrap"), amount))
    return fields


def biome_index(coordinate: np.ndarray, lo_m: float, hi_m: float, width: int) -> np.ndarray:
    """Which biome texel a run of world coordinates falls in. Nearest, and never in between.

    An area index is a name: the average of "desert" and "forest" is whichever unrelated area
    happens to sit between their numbers in a palette nobody ordered.
    """
    position = (coordinate - lo_m * 100) / ((hi_m - lo_m) * 100) * width
    return np.clip(position.astype(np.int64), 0, width - 1)


def sample_noise(fields, rows: np.ndarray, cols: np.ndarray, size: int) -> np.ndarray:
    """The octaves added up at these output pixels, as a multiplier around 1."""
    out = np.ones((len(rows), len(cols)), np.float32)
    for field, amount in fields:
        side = field.shape[0]
        u = (cols.astype(np.float32) + 0.5) * side / size
        v = (rows.astype(np.float32) + 0.5) * side / size
        out += amount * field[np.ix_(v.astype(np.int64) % side, u.astype(np.int64) % side)]
    return out


# --------------------------------------------------------------------------------------
# The two layers.
# --------------------------------------------------------------------------------------


def water_alpha(z_m, water_m, wet: np.ndarray, measured: np.ndarray, blur_px: float):
    """How much of each pixel is water, in [0, 1]. Feathered three ways, for three reasons.

    ``wet`` is the coverage the quality byte gives -- what fraction of the ground under this
    pixel the channel calls water at all -- and ``measured`` is the share of that water whose
    DEPTH was measured against 1 m terrain.

    The depth feather applies only where there IS a depth: the water thins out over the last
    ``WATER_EDGE_M`` and the blend goes with it, which is what a beach looks like. Where the
    level is known and the depth is not, that ramp would be running on a 3.9 m block raster's
    rounding error and would erase three and a half square kilometres of ocean, so those
    texels are drawn at full alpha instead.

    The third feather is in SPACE, for the water that sits in box-shaped bodies against a
    cliff: there the depth goes from nothing to metres across one texel and the depth feather
    has no band to work in. A blur under a metre cannot move a shoreline, only stop it being
    a staircase.
    """
    ramp_alpha = np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0)
    alpha = wet * (measured * ramp_alpha + (1.0 - measured))
    return ndimage.gaussian_filter(alpha, blur_px, mode="nearest")


def water_depth_fraction(z_m, water_m, measured: np.ndarray) -> np.ndarray:
    """How dark the water reads, in [0, 1]: measured depth where there is one, deep where not.

    The colour ramp wants a depth and a level-only texel has none. Drawing those at the
    shallow end -- which is what subtracting a 3.9 m raster from a sea surface gives -- would
    paint the open ocean the pale green of an ankle-deep sheet. They are drawn at the deep end
    because on the shipped field 95.2% of level-only water stands over the fill province and
    98% of its surface levels lie inside a 0.7 m band around the ocean's own -16.99 m.
    """
    known = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)
    return measured * known + (1.0 - measured)


def water_over(rgb, depth, alpha, shade, shallow, deep):
    """Lay water over ground, tinted by its own depth. Both layers want this arithmetic.

    The light touches it only a little, because the hillshade under a lake is computed from
    the lake BED.
    """
    tint = depth[..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def terrain_colours(depth, wet, missing, shade, borrow, z_m, ramp_lo, ramp_hi, **_unused):
    """The approved preview at full resolution: ramp, shade, borrowed detail, water, silence.

    The same arithmetic as the preview the owner picked, scaled up rather than re-tuned,
    plus ``borrow``: the artwork's own light multiplied in over the provinces where the field
    has none of its own.
    """
    height = np.clip((z_m - ramp_lo) / max(ramp_hi - ramp_lo, 1e-6), 0.0, 1.0)
    rgb = ramp(height, RAMP_STOPS) * (shade * borrow)[..., None]
    rgb = water_over(rgb, depth, wet, shade, WATER_SHALLOW, WATER_DEEP)
    return np.where(missing[..., None], SEA_RGB, rgb)


def satellite_colours(depth, wet, missing, shade, borrow, z_m, slope, biome_rgb, noise, **_unused):
    """Ground colour from the biome, then rock, then altitude, then light, then water.

    In that order: the biome says what grows there, the slope overrules it because nothing
    grows on a cliff face, the altitude bleaches what is left, the hillshade lights all of it
    at once because a shadow falls on rock and canopy alike, and the water goes on top
    because it is a different surface rather than a different ground.

    ``borrow`` rides with the hillshade rather than with the colour: what is taken from the
    artwork is light.
    """
    rock = np.clip((slope - ROCK_LO_DEG) / (ROCK_HI_DEG - ROCK_LO_DEG), 0.0, 1.0)[..., None]
    rgb = biome_rgb * (1 - rock) + ROCK_RGB * rock
    lift = np.clip((z_m - HIGH_LO_M) / (HIGH_HI_M - HIGH_LO_M), 0.0, 1.0)[..., None] * HIGH_LIFT
    rgb = rgb * (1 - lift) + HIGH_RGB * lift
    rgb = rgb * noise[..., None] * (shade * borrow)[..., None]
    rgb = water_over(rgb, depth, wet, shade, SATELLITE_WATER_SHALLOW, SATELLITE_WATER_DEEP)
    return np.where(missing[..., None], SEA_RGB, rgb)


LAYER_PAINTERS = {"terrain": terrain_colours, "satellite": satellite_colours}


def ramp_range(field) -> tuple[float, float]:
    """The height band the ramp is stretched over, from the field itself.

    Sampled every fourth texel in each direction: 3.5 million heights is far more than a
    percentile needs and a sixteenth of the arithmetic, and the answer moves by less than a
    decimetre either way.
    """
    sample = field._height_dm[::4, ::4]
    land = sample[sample != hf.NODATA].astype(np.float32) / hf.DM_PER_M
    return (
        float(np.percentile(land, RAMP_LO_PCT)),
        float(np.percentile(land, RAMP_HI_PCT)),
    )


def water_planes(field) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """The two 0/1 planes the water composite is sampled from, and where they came from.

    ``wet`` is "the channel calls this texel water" and ``measured`` is "and it measured the
    depth". Both come off ``waterq.u8.z`` where the field has one. The fallback for a field
    without it -- a water surface standing above the ground -- reads the open ocean as dry,
    because over the fill province the ground is a 3.9 m raster that rounds above a sea
    surface 17 m down, so a missing byte is reported rather than treated as an answer.
    """
    water = field._water_raster()
    if water is None:
        return None, None, "no water raster in this field; nothing is drawn as water"
    grades = field._water_quality_raster()
    if grades is None:
        wet = ((water != hf.NODATA) & (water > field._height_dm)).astype(np.uint8)
        return (
            wet,
            wet,
            (
                "no waterq.u8.z: this field predates the quality byte, so submersion falls "
                "back to a water surface standing above the ground, which is all such a "
                "field can say"
            ),
        )
    return (
        (grades != hf.WATER_DRY).astype(np.uint8),
        (grades == hf.WATER_MEASURED).astype(np.uint8),
        "waterq.u8.z: dry / depth measured against 1 m terrain / level known and depth not",
    )


def blend_regimes(base_m, missing, direct, linear, subsamples):
    """The two-regime height and what it was made of: ``(z_m, missing, w, switched)``.

    This is the field's **own composition rule**, performed at the render's spacing instead
    of read back from the 1 m fold that rule already produced: ``base_m`` is the
    landscape-and-fill lattice interpolated here, and the same rocks go on top of it,
    rasterised at 0.229 m rather than folded onto a metre first.

    ``z = base + w * lift(z_direct - base)``. ``w`` is the direct raster's **coverage** of
    the pixel: the share of its sub-samples a triangle covered, so at one sub-sample a pixel
    is rock at the triangle's own height or ground, and no rock height is ever spread to a
    neighbour across a silhouette. ``lift`` is a **smoothed positive
    part**, which is the other half of the field's rule -- a rock may raise the ground and
    may never lower it -- without the first-derivative discontinuity a hard ``max`` would put
    exactly where the rock meets the ground. It is never negative and sits at most
    ``DIRECT_LIFT_KNEE_M / 2`` above the hard answer.

    Where the lattice knows nothing -- inside a formation big enough that no landscape texel
    survives under it -- the caller passes the whole field's own fold as ``base_m``. There
    the coverage is 1 and the rock is the answer either way.
    """
    z_cm, coverage = direct
    fraction = pixel_coverage(coverage, subsamples)
    w = np.clip(fraction, 0.0, 1.0).astype(np.float32)
    z_direct_m = z_cm / np.float32(100.0)
    delta = z_direct_m - base_m
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    lift = 0.5 * (delta + np.sqrt(delta * delta + knee * knee))
    z_m = base_m + w * lift
    # Where the field has nothing at all and the geometry does -- a rock standing off the
    # edge of the landscape -- the geometry is the whole answer and the pixel stops being
    # no-data. A switch rather than a fade, and allowed to be one: the no-data boundary is
    # already a hard edge the render paints the page's own sea against.
    only_rock = missing & (fraction > 0.0)
    z_m = np.where(only_rock, z_direct_m, z_m)
    w = np.where(only_rock, np.float32(1.0), w)
    # The counterfactual the seam trace measures the blend against: the same two surfaces
    # joined by a switch. Computed here so the trace is never handed two arrays to pair up.
    switched = np.where(w >= SEAM_MID, np.maximum(z_direct_m, base_m), base_m)
    return (
        z_m.astype(np.float32),
        missing & (fraction <= 0.0),
        w,
        switched.astype(np.float32),
    )


def render_layer(
    layer,
    field,
    biome_rgb,
    biome,
    borrow,
    size,
    progress,
    height_dm=None,
    direct=None,
    seam=None,
    regimes=None,
    measured_plane_u8=None,
    overlay=None,
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Banded because the sheet is a billion pixels at 32768 and this recipe holds a dozen
    float32 intermediates over it, four gigabytes apiece whole. Each band is computed with
    BAND_HALO extra rows on both sides and cropped afterwards, so neither the hillshade's
    gradient nor the cubic sampler's stencil nor the water blur's kernel ever sees a band
    edge: a one-sided difference at every 256th row would
    draw 127 horizontal lines across the world.

    ``direct`` is the pair of memory maps the direct pass wrote, with the weight plane and
    the sub-sampling beside them; ``None`` draws the single-regime picture. ``seam`` and
    ``regimes`` are accumulators, passed for the first layer only, both layers drawing the
    identical surface. ``overlay`` is the arch-and-boulder pair of maps and its sub-sampling,
    composited last.
    """
    painter = LAYER_PAINTERS[layer]
    x_cm, y_cm = frame_coordinates(size)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    blur_px = WATER_EDGE_BLUR_M / spacing_m
    detail, province = borrow
    heights = field._height_dm if height_dm is None else height_dm
    ramp_lo, ramp_hi = ramp_range(field)
    noise = noise_fields(NOISE_SEED) if layer == "satellite" else None
    wet_plane, measured_plane, _source = water_planes(field)
    water = field._water_raster()
    out = np.empty((size, size, 3), np.uint8)
    column_index = np.arange(size)

    # The column taps are the same for every band, on both grids the bands sample: the
    # field's 1 m lattice and the artwork's 8192 sheet. Built once.
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    cols_cubic = taps_cubic(field_x, field.width)
    cols_linear = taps_linear(field_x, field.width)
    art_step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SHEET_PX
    art_x0_cm = BOUNDS_M["x_min_m"] * 100 + art_step_cm / 2
    art_cols = taps_linear(grid_position(x_cm, art_x0_cm, art_step_cm, SHEET_PX), SHEET_PX)
    art_y0_cm = BOUNDS_M["y_min_m"] * 100 + art_step_cm / 2
    biome_cols = biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome["width"])
    # Nearest, never in between: a province is a name.
    prov_cols = np.clip(
        np.round((x_cm - field.x0_cm) / field.spacing_cm).astype(np.int64), 0, field.width - 1
    )

    started = time.time()
    for top in range(0, size, BAND_ROWS):
        bottom = min(top + BAND_ROWS, size)
        lo = max(top - BAND_HALO, 0)
        hi = min(bottom + BAND_HALO, size)
        field_y = grid_position(y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
        cubic = (taps_cubic(field_y, field.height), cols_cubic)
        linear = (taps_linear(field_y, field.height), cols_linear)

        z_dm, missing = sample_surface(heights, cubic, linear, hf.NODATA)
        z_m = z_dm / np.float32(hf.DM_PER_M)
        weight = None
        if direct is not None:
            direct_z, direct_coverage, ground, subsamples = direct
            # The base the rocks are composited onto is the lattice UNDERNEATH them, not the
            # field's own fold -- see ``ground_lattice``. Where that lattice knows nothing
            # the fold stands in, which is inside a formation the rock covers anyway.
            ground_dm, ground_missing = sample_surface(ground, cubic, linear, hf.NODATA)
            base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
            z_m, missing, weight, switched = blend_regimes(
                base_m,
                missing,
                (np.asarray(direct_z[lo:hi], np.float32), np.asarray(direct_coverage[lo:hi])),
                linear,
                subsamples,
            )
            if seam is not None:
                keep = slice(top - lo, bottom - lo)
                seam.add(
                    z_m[keep],
                    switched[keep],
                    weight[keep],
                    spacing_m,
                    (np.asarray(direct_z[lo:hi], np.float32) / 100.0 - base_m)[keep],
                )
            if regimes is not None:
                prov_rows = np.clip(
                    np.round((y_cm[top:bottom] - field.y0_cm) / field.spacing_cm).astype(np.int64),
                    0,
                    field.height - 1,
                )
                picked = np.ix_(prov_rows, prov_cols)
                regimes.add(
                    field._prov[picked],
                    weight[top - lo : bottom - lo],
                    measured_plane_u8[picked] > 0,
                )
        if overlay is not None:
            top_z, top_coverage, top_subsamples = overlay
            z_m = composite_top(
                z_m,
                np.asarray(top_z[lo:hi], np.float32),
                np.asarray(top_coverage[lo:hi]),
                top_subsamples,
            )
        if wet_plane is None:
            wet = measured = np.zeros(z_m.shape, np.float32)
            water_m = z_m
        else:
            water_dm, _dry = sample_surface(water, cubic, linear, hf.NODATA)
            water_m = water_dm / np.float32(hf.DM_PER_M)
            wet = sample_coverage(wet_plane, linear)
            measured = sample_coverage(measured_plane, linear) / np.where(wet <= 0.0, 1.0, wet)
            measured = np.clip(measured, 0.0, 1.0)

        art_rows = taps_linear(
            grid_position(y_cm[lo:hi], art_y0_cm, art_step_cm, SHEET_PX), SHEET_PX
        )
        strength = sample_plain(province, linear) / 255.0
        lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, (art_rows, art_cols)) / 127.0)

        shade = hillshade(z_m, spacing_m)
        extra: dict = {}
        if layer == "satellite":
            extra["slope"] = slope_degrees(z_m, spacing_m)
            biome_rows = biome_index(
                y_cm[lo:hi], BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], biome["width"]
            )
            extra["biome_rgb"] = biome_rgb[np.ix_(biome_rows, biome_cols)].astype(np.float32)
            extra["noise"] = sample_noise(noise, np.arange(lo, hi), column_index, size)
        rgb = painter(
            z_m=z_m,
            depth=water_depth_fraction(z_m, water_m, measured),
            wet=water_alpha(z_m, water_m, wet, measured, blur_px),
            missing=missing,
            shade=shade,
            borrow=np.clip(lift, *BORROW_CLAMP),
            ramp_lo=ramp_lo,
            ramp_hi=ramp_hi,
            **extra,
        )
        out[top:bottom] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
        if progress and (top // BAND_ROWS) % 16 == 0:
            done = bottom / size
            print(
                f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                flush=True,
            )
    return out


# --------------------------------------------------------------------------------------
# Installing a layer.
# --------------------------------------------------------------------------------------


def layer_dir(out_dir: Path, layer: str, name: str = RENDERS_DIR_NAME) -> Path:
    return out_dir / name / layer


#: Where a layer sidecar records the heightfield build it was drawn from.
FIELD_PIN_PATH = ("sources", "heightfield", "game_version_pinned")


def pinned_field_build(sidecar: dict) -> str | None:
    """The heightfield build an existing layer sidecar names, or None if it names none."""
    return read_str_path(sidecar.get("_meta"), FIELD_PIN_PATH)


def build_sidecar(
    *,
    layer: str,
    field_meta: dict,
    tiles: dict,
    render: dict,
    extra: dict,
    recipe: int = RECIPE,
    tiles_2x: dict | None = None,
) -> dict:
    """The file the web API reads for this layer, plus the provenance to date it by.

    The four corner keys sit at the top exactly as ``map.json``'s do and ``_meta.tiles``
    carries the same block, so the endpoint reads a render layer with the code it has for
    the artwork one. ``_meta.tiles_2x`` is that block again for the denser tree, and its
    absence is how a layer says it has none.
    """
    build = ((field_meta.get("sources") or {}).get("game") or {}).get("game_version_pinned")
    return {
        **BOUNDS_M,
        "_meta": {
            "description": (
                f"The {layer} base layer: a render of this world drawn from the 1 m "
                "heightfield, and the pyramid cut from it. All of it is local: data/local/ "
                "is gitignored and no map imagery is ever committed to this repository."
            ),
            "bounds": (
                "metres, game axes -- +X east, +Y south. The corners of the in-game map "
                "square, the same frame data/local/map.json pins, so a page can swap base "
                "layers without touching its tile grid."
            ),
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": recipe,
            "recipe_description": RECIPES[recipe],
            "transcribed": datetime.now(UTC).date().isoformat(),
            "sources": {
                "heightfield": {
                    "name": f"data/local/{hf.DIR_NAME}/",
                    "generator": field_meta.get("generator"),
                    "generator_version": field_meta.get("generator_version"),
                    "grid": field_meta.get("grid"),
                    "game_version_pinned": build,
                    "role": "every pixel's height, and the relief and water on it",
                },
                **extra,
            },
            "render": render,
            "tiles": tiles,
            **({"tiles_2x": tiles_2x} if tiles_2x else {}),
            "staleness": (
                "sources.heightfield.game_version_pinned is the build the field under these "
                "pixels was cut from. tools/gen_map_renders.py refuses to replace this layer "
                "unless the field now on disk names the same build; --force says it anyway. "
                "Terrain moves every patch, and a render that quietly disagrees with the "
                "node tables beside it is exactly the drift this project announces."
            ),
        },
    }


def install_layer(
    sheet_rgb,
    image_mod,
    out_dir: Path,
    layer: str,
    workers: int,
    recipe: int = RECIPE,
    name: str = RENDERS_DIR_NAME,
) -> tuple[dict, dict, float]:
    """Cut one layer's two pyramids into place, and say what they wrote and how long it took.

    ``tiles/`` first, because that is what every client can read, then ``tiles@2x/``, which
    a client that cannot find it simply asks for the 1x instead. Each is renamed into place
    on its own, so a run that dies between them never leaves the page without a base map.

    The @2x tree is cut from a **downscale** of the sheet, capped at ``RENDER_2X_PX``: cut
    from the full sheet it would gain a z6 of 512 px tiles weighing as much as the whole 1x
    pyramid, for pixels a hi-DPI client already gets by asking for ``z + 1`` at 1x.
    """
    directory = layer_dir(out_dir, layer, name)
    directory.mkdir(parents=True, exist_ok=True)
    sheet = image_mod.fromarray(sheet_rgb)
    source = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
    started = time.time()
    stats = install_pyramid(sheet, image_mod, directory, source=source, workers=workers)
    dense_px = min(sheet.width, RENDER_2X_PX)
    dense_sheet = (
        sheet if dense_px == sheet.width else sheet.resize((dense_px, dense_px), image_mod.LANCZOS)
    )
    dense = install_pyramid(
        dense_sheet,
        image_mod,
        directory,
        tile_px=PYRAMID_TILE_2X_PX,
        source=source,
        workers=workers,
        dir_name=TILES_2X_DIR_NAME,
    )
    return stats, dense, time.time() - started


def check_parallel(sheet_rgb, image_mod, scratch: Path, workers: int) -> dict:
    """Cut one level twice -- serially and in parallel -- and compare every tile's SHA-256.

    The parallel cutter's claim is **identical bytes** rather than equivalence, which a hash
    settles. On demand rather than every run: it costs one extra cut of one level and it
    guards against a change to the cutter, not against a flaky machine.
    """
    from hashlib import sha256

    sheet = image_mod.fromarray(sheet_rgb)
    level = sheet.resize((PYRAMID_TILE_PX << CHECK_PARALLEL_Z,) * 2, image_mod.LANCZOS)
    digests = {}
    timings = {}
    for name, jobs in (("serial", 1), ("parallel", workers)):
        dest = scratch / name
        dest.mkdir(parents=True, exist_ok=True)
        if jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                # Wake every worker before the clock starts: spawning interpreters that each
                # import numpy costs more than the cutting being measured.
                list(pool.map(int, range(jobs)))
                started = time.time()
                cut_square_parallel(level, dest, CHECK_PARALLEL_Z, PYRAMID_TILE_PX, pool)
                timings[name] = round(time.time() - started, 2)
        else:
            started = time.time()
            cut_square(level, dest, CHECK_PARALLEL_Z, 0, 0, PYRAMID_TILE_PX)
            timings[name] = round(time.time() - started, 2)
        digests[name] = {
            str(path.relative_to(dest)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
            for path in sorted(dest.rglob("*.png"))
        }
    same = digests["serial"] == digests["parallel"]
    shutil.rmtree(scratch, ignore_errors=True)
    return {
        "level": CHECK_PARALLEL_Z,
        "tiles": len(digests["serial"]),
        "seconds_serial": timings["serial"],
        "seconds_parallel": timings["parallel"],
        "speedup": round(timings["serial"] / max(timings["parallel"], 1e-9), 2),
        "workers": workers,
        "byte_identical": same,
        "differing_tiles": sorted(
            name
            for name in digests["serial"]
            if digests["serial"][name] != digests["parallel"].get(name)
        )[:8],
        "method": (
            "the same level cut both ways into two scratch directories, SHA-256 of every "
            "tile compared name by name. The parallel path resamples nothing -- it is handed "
            "the level already resized -- so this is an identity, not a tolerance."
        ),
    }


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "--field",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="the heightfield directory tools/gen_world_heightmap.py wrote",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR,
        help="destination for renders/<layer>/ (gitignored)",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=LAYERS,
        help=f"only this layer (repeatable; default all of {', '.join(LAYERS)})",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=RENDER_PX,
        choices=[RENDER_PX, RENDER_PX // 2, RENDER_PX // 4, RENDER_PX // 8],
        help=(
            f"square edge of each render (default {RENDER_PX}, which is 0.229 m to the "
            "pixel -- see the module docstring for what that is and is not a claim about)"
        ),
    )
    parser.add_argument(
        "--direct-subsamples",
        type=int,
        default=DIRECT_SUBSAMPLES,
        choices=[1, 2, 4],
        help=(
            f"sub-samples per output texel per axis in the direct pass (default "
            f"{DIRECT_SUBSAMPLES}; each doubling costs 4x the rasterising and is the only "
            "antialiasing a silhouette gets)"
        ),
    )
    parser.add_argument(
        "--kernel-only",
        action="store_true",
        help=(
            f"draw recipe {RECIPE_KERNEL_ONLY} instead: the Catmull-Rom kernel everywhere, "
            "no geometry opened, no cross-fade and no de-terracing. The picture this file "
            "drew before, at whatever --size is asked for, and recorded as that recipe"
        ),
    )
    parser.add_argument(
        "--keep-direct",
        action="store_true",
        help="leave the direct.cache/ and top.cache/ rasters behind so the next run reuses them",
    )
    parser.add_argument(
        "--no-top",
        action="store_true",
        help="leave out the arches and foliage boulders the field keeps in top.i16.z",
    )
    parser.add_argument(
        "--renders-name",
        default=RENDERS_DIR_NAME,
        help=(
            f"directory under --out-dir to write into (default {RENDERS_DIR_NAME}, the one the "
            "server reads); a new name keeps the current renders untouched"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"processes deflating tiles (default {DEFAULT_WORKERS}; 1 cuts serially). The "
            "resampling is single-threaded either way, so the tiles are the same bytes"
        ),
    )
    parser.add_argument(
        "--check-parallel",
        action="store_true",
        help=(
            f"cut z{CHECK_PARALLEL_Z} both serially and in parallel and compare every "
            "tile's SHA-256, then carry on"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace layers this run cannot show were drawn from the field now on disk",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-band progress lines")
    args = parser.parse_args()

    layers = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS
    workers = max(1, args.workers)

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pillow_version, pyooz_version = versions["pillow"], versions["pyooz"]
    import texture2ddecoder as decoder

    image_mod = load_imaging()

    field = hf.load_field(args.field)
    if field is None:
        print(
            f"no heightfield at {args.field}. That field is the one input this file cannot "
            "invent -- every pixel of both layers is a height off it -- so there is nothing "
            "to draw. Write it first:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py\n"
            "It reads your own installed game and writes to the same gitignored directory."
        )
        return 4
    field_meta = field.meta
    field_build = field.build
    print(
        f"field: {field.width}x{field.height} at {field.spacing_cm / 100:g} m, build {field_build}"
    )

    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
    weight_plane, weight_meta = (None, {}) if args.kernel_only else direct_weight(field, spacing_m)
    if weight_plane is None and not args.kernel_only:
        print(
            f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its texels "
            "are measurements and which are the cliff rasteriser interpolating across a "
            "triangle wider than a texel. That plane is the only thing the two-regime "
            f"sampler switches on, so recipe {RECIPE} has nothing to draw. Cut a field with "
            f"generator version {gen.GENERATOR_VERSION} or later:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py --force\n"
            "or pass --kernel-only to draw the single-regime picture and say so in the "
            "sidecar."
        )
        return 6
    if weight_plane is not None:
        print(
            f"  a measurement where {weight_meta['rule']} -- "
            f"{weight_meta['qualifying_share_of_the_field']}% of the field, "
            f"{weight_meta['qualifying_share_of_the_cliff_province']}% of its cliff "
            "province. Provenance, not a gate: the rocks are drawn wherever they cover a "
            "pixel"
        )
    recipe = RECIPE_KERNEL_ONLY if args.kernel_only else RECIPE
    heights, deterrace_meta = (None, {}) if args.kernel_only else deterraced_height(field)
    if heights is None:
        print(f"  --kernel-only: drawing recipe {recipe}, the picture before the two regimes")
    else:
        print(
            f"  fill terraces: {deterrace_meta['share_of_the_field']}% of the field low-passed "
            f"at {deterrace_meta['cell_m']} m, moving it a median "
            f"{deterrace_meta['moved_median_m']} m, p99 {deterrace_meta['moved_p99_m']} m, "
            f"clamped at one {deterrace_meta['clamp_m']} m step on "
            f"{deterrace_meta['clamped_share_of_the_province']}% of the province"
        )
    ground, ground_meta = (None, {}) if heights is None else ground_lattice(field, heights)
    terrain_meta: dict = {}
    if ground is not None:
        ground, terrain_meta = terrain_lattice(field, ground)
        if "absent" in terrain_meta:
            print(f"  {terrain_meta['absent']}")
        else:
            print(
                f"  landscape from {hf.TERRAIN_NAME}: {terrain_meta['landscape_texels']} texels "
                f"plus {terrain_meta['under_cliff_texels']} under the cliff province"
            )
    if ground is not None:
        print(
            f"  the lattice under the rocks: {ground_meta['lattice_share_of_the_field']}% of "
            f"the field, with {ground_meta['removed_share_of_the_field']}% of it -- the cliff "
            "province -- taken out so the rocks are composited over the ground rather than "
            "over their own 1 m fold"
        )

    out_dir: Path = args.out_dir
    if not args.force:
        for layer in layers:
            sidecar_path = layer_dir(out_dir, layer, args.renders_name) / RENDER_SIDECAR_NAME
            if not (layer_dir(out_dir, layer, args.renders_name) / TILES_DIR_NAME).is_dir():
                continue
            try:
                existing = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing = {}
            pinned = pinned_field_build(existing if isinstance(existing, dict) else {})
            if pinned != field_build:
                print(
                    f"{layer_dir(out_dir, layer, args.renders_name)} already holds a {layer} pyramid and this "
                    "run cannot show it was drawn from the field now on disk.\n"
                    f"  field on disk: {field_build}\n"
                    f"  those tiles:   {pinned or 'no meta.json, or no build recorded in it'}\n"
                    "A render from another build is a picture of another world's terrain, "
                    "and drift is announced rather than overwritten. Pass --force to "
                    "replace it anyway."
                )
                return 3

    # ---- the artwork sheet, which every layer now needs ------------------------------
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the game's own assets from {paks} with pyooz {pyooz_version}")
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    artwork = read_artwork_sheet(store, decoder, image_mod)
    detail, detail_meta = artwork_detail(artwork)
    print(
        f"  artwork sheet {SHEET_PX}x{SHEET_PX} from {len(SLICES)} BC1 slices; luminance "
        f"high pass at sigma {BORROW_DETAIL_SIGMA_PX} px, std {detail_meta['measured_std']}"
    )
    province, province_meta = coarse_province(field)
    print(
        f"  coarse provenance ({', '.join(province_meta['provinces'])}) is "
        f"{province_meta['share_of_the_field']}% of the field, feathered "
        f"{BORROW_FEATHER_M:g} m"
    )
    borrow = (detail, province)
    _wet_plane, _measured_plane, water_source = water_planes(field)
    print(f"  water: {water_source}")

    # The check cuts the ARTWORK: a real picture with real entropy, so the PNGs are real
    # PNGs rather than a run-length of one colour that compares equal whatever happened.
    parallel_check = None
    if args.check_parallel:
        parallel_check = check_parallel(
            np.asarray(artwork, np.uint8), image_mod, out_dir / "parallel.check", workers
        )
        print(
            f"  parallel cutter: z{parallel_check['level']}, {parallel_check['tiles']} tiles, "
            f"{parallel_check['seconds_serial']}s serial vs "
            f"{parallel_check['seconds_parallel']}s on {workers} workers "
            f"({parallel_check['speedup']}x) -- byte-identical: "
            f"{parallel_check['byte_identical']}"
        )
        if not parallel_check["byte_identical"]:
            print(
                "  the parallel cutter does not reproduce the serial one's bytes. That is "
                "the one thing it promises, so nothing is written: "
                + ", ".join(parallel_check["differing_tiles"])
            )
            return 5

    # ---- the biome raster ------------------------------------------------------------
    biome = None
    if "satellite" in layers:
        biome = read_biome(store, scripts)
        print(
            f"  {biome['width']}x{biome['width']} palette indices, "
            f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
        )
        calibration = calibrate_biome(biome, artwork, image_mod)
        print(
            f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin "
            f"against {calibration['edge_ratio_at_the_best_rival_shift']} for the best "
            f"shift and {calibration['edge_ratio_at_other_scales']} at other scales -- "
            f"margin {calibration['margin_over_the_best_rival']}x over "
            f"{calibration['sweep']}"
        )
        if not calibration["pin_holds"]:
            print(
                "  WARNING: the pin no longer beats its rivals by the required margin. "
                "The biome texture moved, or the artwork sheet did. The layer is still "
                "drawn -- it is the corners that are in question -- and _meta says so."
            )
        agreement = region_table_is_current(biome)
        if "skipped" in agreement:
            print(f"  region table: {agreement['skipped']}")
        else:
            print(
                f"  region table: {agreement['cells_agreeing']} of "
                f"{agreement['cells_compared']} committed cells match this raster "
                f"({agreement['agreement_pct']}%)"
            )
            if not agreement["table_is_current"]:
                print(
                    "  WARNING: data/region_names.json is no longer this asset's own "
                    "downsample, so it was cut from a different build. Re-run:\n"
                    "      uv run --extra gen python tools/gen_region_names.py"
                )
        table, drawn = biome_lookup(biome)
        biome_rgb = biome_colour_field(biome, table)
        biome_source = {
            "biome_raster": {
                "name": "/Game/"
                + MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
                "class": MAP_AREA_CLASS,
                "licence": (
                    "Coffee Stain Studios' own asset, read out of the reader's installed "
                    "copy of the game. Not committed, not redistributed, and served to "
                    "localhost only."
                ),
                "derivation": (
                    f"mAreaData, {biome['width']}x{biome['width']} palette indices; "
                    "mColorToArea resolves each index to a UFGMapArea object"
                ),
                "areas": biome["distinct_areas"],
                "shipped_palette_rgba": [list(entry) for entry in biome["palette"]],
                "shipped_palette_role": (
                    "the game's own UI legend -- flat primaries, cyan, magenta, white. "
                    "Decoded for the record and NOT drawn: see palette below, which is this "
                    "file's own and was written to look like imagery."
                ),
                "palette": {name: list(BIOME_COLOURS[name]) for name in sorted(BIOME_COLOURS)},
                "palette_blend_texels": BIOME_BLEND_TEXELS,
                "palette_fallback": {
                    NO_MANS_LAND: list(NO_MANS_LAND_RGB),
                    "an area this file has no colour for": list(UNKNOWN_BIOME_RGB),
                },
                "index_to_area": {str(i): name for i, name in enumerate(drawn)},
                "index_to_asset": {str(i): name for i, name in enumerate(biome["assets_by_index"])},
                "calibration": calibration,
                "region_table_check": agreement,
                "pyooz_version": pyooz_version,
            }
        }
    else:
        biome_rgb, biome_source = None, {}

    # ---- the cliff geometry and the top overlay, rasterised into this render's own grid
    direct = None
    top = None
    direct_source: dict = {}
    top_source: dict = {}
    loaded: dict = {}

    def geometry_once() -> dict:
        if not loaded:
            index = AssetIndex(store)
            loaded["index"] = index
            loaded["geometry"] = read_cliff_geometry(
                store, scripts, index, ClassFacts(store, index), not args.quiet
            )
            got = loaded["geometry"]
            print(
                f"  {got['meshes']} rock meshes, {got['tris'] / 1e6:.2f} M triangles "
                f"{got['by_source']}, swept in {got['seconds_sweep']}s and decoded "
                f"in {got['seconds_decode']}s"
            )
        return loaded["geometry"]

    if weight_plane is not None:
        cache = direct_cache_dir(out_dir, args.renders_name)
        stamp = direct_cache_stamp(args.size, args.direct_subsamples, field_build)
        maps = cached_direct(cache, stamp)
        if maps is None:
            print(
                f"decoding the cliff geometry and rasterising it at {spacing_m:.4f} m"
                + (
                    f" with {args.direct_subsamples}x{args.direct_subsamples} sub-samples"
                    if args.direct_subsamples > 1
                    else ""
                )
            )
            geometry = geometry_once()
            prepared, dropped = direct_placements(geometry["sweep"], geometry["geometry"])
            print(f"  {len(prepared)} placements rasterised, dropped {dropped}")
            cache_stats = rasterise_direct(
                partial(rasterise_direct_band, prepared, geometry["geometry"]),
                cache,
                args.size,
                args.direct_subsamples,
                stamp,
                not args.quiet,
            )
            print(
                f"  direct raster: {cache_stats['texels_with_geometry'] / 1e6:.1f} M texels "
                f"({cache_stats['share_of_the_sheet']}% of the sheet) in "
                f"{cache_stats['seconds']}s"
            )
            direct_source = {
                "cliff_geometry": {
                    "name": "the same placed rock meshes tools/gen_world_heightmap.py folds "
                    "into the 1 m field, decoded here a second time",
                    "licence": (
                        "Coffee Stain Studios' own cooked assets, read out of the reader's "
                        "installed copy of the game. Nothing is committed, redistributed or "
                        "served past localhost."
                    ),
                    "decoder": (
                        "tools/gen_world_heightmap.py's own sweep_levels, read_mesh_geometry, "
                        "rotation_matrix, winding_sign and MaxZRaster, imported and called. "
                        "The grid they are pointed at is the only thing this file changes."
                    ),
                    "meshes": geometry["meshes"],
                    "by_source": geometry["by_source"],
                    "source_triangles": geometry["tris"],
                    "triangles_out_of_bounds": geometry["triangles_out_of_bounds"],
                    "placements_rasterised": len(prepared),
                    "placements_dropped": dropped,
                    "raster": cache_stats,
                    "pyooz_version": pyooz_version,
                }
            }
            maps = cached_direct(cache, stamp)
            del prepared
        else:
            print(f"reusing the direct raster already in {cache}")
            direct_source = {
                "cliff_geometry": {
                    "reused": json.loads((cache / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
                }
            }
        if maps is None:
            print(f"the direct raster in {cache} could not be read back after writing it")
            return 7
        direct = (maps[0], maps[1], ground, args.direct_subsamples)

        if not args.no_top:
            top_cache = top_cache_dir(out_dir, args.renders_name)
            top_maps = cached_direct(top_cache, stamp)
            if top_maps is None:
                print(f"rasterising the arches and foliage boulders at {spacing_m:.4f} m")
                geometry = geometry_once()
                items, top_meta = top_items(
                    store, scripts, loaded["index"], geometry["sweep"], geometry["geometry"]
                )
                print(
                    f"  {top_meta['arch_placements']} arches, "
                    f"{top_meta['foliage_instances']} boulders {top_meta['foliage_sources']}"
                )
                top_stats = rasterise_direct(
                    partial(rasterise_top_band, items),
                    top_cache,
                    args.size,
                    args.direct_subsamples,
                    stamp,
                    not args.quiet,
                )
                print(
                    f"  top raster: {top_stats['texels_with_geometry'] / 1e6:.1f} M texels in "
                    f"{top_stats['seconds']}s"
                )
                top_source = {"top_overlay": {**top_meta, "raster": top_stats}}
                del items
                top_maps = cached_direct(top_cache, stamp)
            else:
                print(f"reusing the top raster already in {top_cache}")
                top_source = {
                    "top_overlay": {
                        "reused": json.loads(
                            (top_cache / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8")
                        )
                    }
                }
            if top_maps is None:
                print(f"the top raster in {top_cache} could not be read back after writing it")
                return 7
            top = (top_maps[0], top_maps[1], args.direct_subsamples)
    loaded.clear()

    # ---- draw and cut ----------------------------------------------------------------
    borrow_source = {
        "artwork_detail": {
            "name": f"the game's own {SHEET_PX} px map sheet, from its four BC1 slices",
            "licence": (
                "Coffee Stain Studios' own artwork, read out of the reader's installed copy "
                "of the game. Its LUMINANCE only, high-passed, and multiplied into shading "
                "-- no pixel of it is drawn and no colour of it crosses. Not committed, not "
                "redistributed, and served to localhost only."
            ),
            **detail_meta,
            "applied_where": province_meta,
            "gain": BORROW_GAIN,
            "clamp": list(BORROW_CLAMP),
            "reading": (
                "the field is one resolution but not one accuracy. Over the landscape "
                "province -- 45.3% of it -- the geometry is continuous and its own shading "
                "is the best there is, so nothing is borrowed. Over cliff and fill it is "
                "rasterised hulls and 3.9 m blocks, which is why those provinces read as "
                "melted wax when drawn from the field alone, and the artwork drew the same "
                "ground at 0.92 m."
            ),
        }
    }
    total_started = time.time()
    seam = SeamTrace() if direct is not None else None
    regimes = RegimeCoverage() if direct is not None else None
    measured: dict = {}
    for layer in layers:
        print(f"drawing {layer} at {args.size}x{args.size}")
        started = time.time()
        sheet = render_layer(
            layer,
            field,
            biome_rgb,
            biome or {"width": 1, "area": np.zeros((1, 1), np.uint8)},
            borrow,
            args.size,
            not args.quiet,
            height_dm=heights,
            direct=direct,
            measured_plane_u8=weight_plane,
            overlay=top,
            # Both layers draw the identical surface, so the seam and the regime table are
            # measured on the first one and quoted for both.
            seam=seam if not measured else None,
            regimes=regimes if not measured else None,
        )
        drew = time.time() - started
        if seam is not None and not measured:
            measured = {"seam_trace": seam.result(), "regimes": regimes.result()}
            trace = measured["seam_trace"]
            if trace.get("measured"):
                print(
                    f"  seam trace: p99 |d2z/dx2| {trace['p99_curvature']['seam']} over the "
                    f"blend against {trace['p99_curvature']['switch']} for the hard max on "
                    f"the same texels -- the fade spends "
                    f"{trace['share_of_a_hard_switch']} of that ceiling; against the terrain "
                    f"beside the join it reads {trace['against_the_pure_regimes']}, which is "
                    "the design's own reference and is measuring the silhouette"
                )
            print(f"  regimes: {measured['regimes']['sheet_pct']}")
        try:
            stats, dense, cut = install_layer(
                sheet, image_mod, out_dir, layer, workers, recipe, args.renders_name
            )
        except PyramidError as exc:
            print(exc)
            return 1
        del sheet
        stats["game_version_pinned"] = field_build
        dense["game_version_pinned"] = field_build
        spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
        render = {
            "width_px": args.size,
            "height_px": args.size,
            "metres_per_pixel": round(spacing_m, 4),
            "sampling": (
                "the field's own composition rule, at this render's spacing. KERNEL: "
                "Catmull-Rom (cubic convolution, a = -1/2) over the LANDSCAPE AND FILL "
                "lattices -- the cliff province taken out, because interpolating the "
                "composed field reconstructs its own 1 m fold and a rim reconstructed from "
                "a fold is a 1 m staircase at any output resolution -- falling back to "
                "bilinear where the 4x4 stencil straddles no data and to nothing where no "
                "texel under it has a value. DIRECT: the cliff geometry rasterised into "
                f"this grid at {spacing_m:.4f} m and composited on top of that lattice by "
                "its own coverage of the pixel, raising the ground and never lowering it, "
                "through a smoothed positive part so the line where a rock meets the ground "
                "is not a derivative discontinuity the hillshade would draw. density.u8.z "
                "does not gate any of this: it says which of the drawn texels are "
                "measurements and which are the plane of a triangle wider than a texel, and "
                "_meta.render.two_regime.regimes counts both"
            )
            if direct is not None
            else (
                "Catmull-Rom (cubic convolution, a = -1/2) over the 1 m field per output "
                "pixel wherever the 4x4 stencil is whole, bilinear where it straddles the "
                "edge of the data, and nothing at all where no texel under it has a value. "
                "--kernel-only: no geometry was opened and no direct regime was drawn"
            ),
            "two_regime": {
                "enabled": direct is not None,
                "subsamples_per_axis": args.direct_subsamples if direct is not None else None,
                "silhouette_antialiasing": (
                    "none: a pixel is rock where a triangle covers its centre, at the "
                    "triangle's own height, and ground where none does. Rock heights are "
                    "never blurred across a silhouette"
                )
                if args.direct_subsamples == 1
                else (
                    f"{args.direct_subsamples}x{args.direct_subsamples} sub-samples per output "
                    "texel, box-folded"
                ),
                "composition": (
                    "the field's own rule at this render's spacing: the landscape and fill "
                    "lattices interpolated with the C1 kernel, and the cliff geometry "
                    "rasterised at 0.229 m composited over them by its own coverage, raising "
                    "the ground and never lowering it. What this replaced was interpolating "
                    "the 1 m FOLD of that composition, which reconstructs a rim as the 1 m "
                    "staircase the fold put it on however fine the output grid is"
                ),
                "ground_lattice": ground_meta,
                "terrain_lattice": terrain_meta,
                "top_overlay": top is not None,
                "measurement_rule": weight_meta,
                "lift_knee_m": DIRECT_LIFT_KNEE_M,
                "fill_deterrace": deterrace_meta,
                **measured,
            },
            "z7": (
                "interpolated-smooth. 32768 px is NOT a claim that the field has more to "
                "say -- that was measured twice on this pipeline and refused twice, and the "
                "high-frequency energy per pixel falls at every doubling. What z7 is, is the "
                "same surface evaluated by the same C1 kernel at half the spacing, which a "
                "client cannot produce for itself: a browser shown z6 at twice its scale "
                "upsamples it BILINEARLY, and bilinear is C0, so the relief it draws is "
                "ruled into 0.458 m squares. The exception is the direct regime, where the "
                "pixels are triangles rather than an interpolation and z7 genuinely resolves "
                "geometry the 1 m field folds away -- see two_regime.regimes for how much of "
                "the sheet that is."
            )
            if args.size >= RENDER_PX
            else None,
            "hillshade": (
                f"sun at azimuth {SUN_AZIMUTH_DEG} deg, altitude {SUN_ALTITUDE_DEG} deg, "
                f"shade in [{SHADE_FLOOR}, {SHADE_FLOOR + SHADE_RANGE}], computed at the "
                "output's own spacing"
            ),
            "water": {
                "source": water_source,
                "depth_ramp_m": WATER_DEPTH_FULL_M,
                "edge_feather_m": WATER_EDGE_M,
                "edge_blur_m": WATER_EDGE_BLUR_M,
                "edge_blur_px": round(WATER_EDGE_BLUR_M / spacing_m, 3),
                "level_only": (
                    "full alpha and the deep end of the ramp. 95.2% of level-only water "
                    "stands over the fill province and 98% of its surface levels lie in a "
                    "0.7 m band around the ocean's own -16.99 m, so it is the ocean, and a "
                    "depth ramp run on a 3.9 m raster's rounding error is what used to draw "
                    "3.572 km2 of it as land"
                ),
            },
            "seconds_to_draw": round(drew, 1),
            "seconds_to_cut": round(cut, 1),
            "cut_workers": workers,
            **({"parallel_cutter_check": parallel_check} if parallel_check else {}),
            "imaging": {"name": "pillow", "version": pillow_version},
        }
        sidecar = build_sidecar(
            layer=layer,
            recipe=recipe,
            field_meta=field_meta,
            tiles=stats,
            tiles_2x=dense,
            render=render,
            extra={
                **borrow_source,
                **direct_source,
                **top_source,
                **(biome_source if layer == "satellite" else {}),
            },
        )
        path = layer_dir(out_dir, layer, args.renders_name) / RENDER_SIDECAR_NAME
        path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
        print(
            f"wrote {layer_dir(out_dir, layer, args.renders_name)}  {stats['count']} tiles over "
            f"z0..z{stats['max_z']} ({stats['bytes'] / 1e6:.1f} MB) plus {dense['count']} "
            f"@2x over z0..z{dense['max_z']} ({dense['bytes'] / 1e6:.1f} MB)  "
            f"(drew {drew:.0f}s, cut {cut:.0f}s)"
        )
    if direct is not None:
        # Let the memory maps go before removing the files under them: on Windows an open
        # mapping refuses the unlink outright.
        direct = maps = top = top_maps = None
        if not args.keep_direct:
            shutil.rmtree(direct_cache_dir(out_dir, args.renders_name), ignore_errors=True)
            shutil.rmtree(top_cache_dir(out_dir, args.renders_name), ignore_errors=True)
    print(f"done in {time.time() - total_started:.0f}s")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

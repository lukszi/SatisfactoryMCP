# mapgen

The offline map generators. They read the installed game and write what the web map draws:
the 1 m heightfield with its caves and rocks, the landscape paint layers, the game's own map
artwork, and the drawn renders served as map tiles.

mapgen is a uv workspace member of this repository (`tools/mapgen/`, its own `pyproject.toml`).
It is not part of the shipped `satisfactory-mcp` wheel. Its dependencies are the root
project's `gen` extra, pinned the same way, because they decide the bytes a generator writes.
Everything it writes lands under the gitignored `data/local/`, and none of it is committed: it
is derived from the game's cooked assets.

## Running it

```
uv sync --all-packages
uv run python -m mapgen <command> [args...]
python -m mapgen --help
```

Each command takes `--help`, and each command that reads the game takes `--game <install dir>`.
The default is one fixed Steam library path (`DEFAULT_GAME` in `common.py`), so pass
`--game` on any other machine.

`tools/gen_world_heightmap.py`, `gen_map_renders.py`, `gen_map_image.py`,
`gen_paint_layers.py` and `check_map_fill.py` still exist at their old paths. Each is a
thin, standard-library-only shim that passes its arguments to the matching command. Every
recorded sidecar names them in `_meta.generator`, so they stay where they are. The server's
job runner starts `python -m mapgen <command>` with `tools/mapgen/src` on the child's path.

A render prints `::stage {"id": ..., "done": ...}` lines beside its human log lines
(`satisfactory_mcp.core.mapprogress`); the job runner reads progress from those first.

## Commands

Times are from one mid-range machine on build 502094. "Budget" is what the Maps tab's
estimate assumes before a job of that kind has run once.

| Command | Old script | Writes (default) | Typical time |
|---|---|---|---|
| `heightmap` | `gen_world_heightmap.py` | `data/local/heightmap/` (planes, `meta.json`, and the rock pack) | 4–6 min; budget 15 min |
| `caves` | `gen_world_heightmap.py --caves` | `data/local/caves/` (`caves.npz`, `meta.json`) | sweep 6 s; budget 2 min |
| `rocks` | `gen_world_heightmap.py --rocks` | `rocks.npz` and `rocks.json` beside the field in `data/local/heightmap/` | 24 s; budget 5 min |
| `paint` | `gen_paint_layers.py` | `data/local/paint/` (104 MB) | 70 s; budget 1 min |
| `artwork` | `gen_map_image.py` | `data/local/` (`map.png`, `map.json`, `tiles/`, `tiles@2x/`) | 3 min; 14 min with `--enhance` |
| `renders` | `gen_map_renders.py` | `data/local/renders/<layer>/` | 3 min at `--size 1024`; about 30 min for two layers at full size |
| `check-fill` | `check_map_fill.py` | nothing, unless `--json <file>` | not measured |

### heightmap

Cuts the 1 m heightfield out of the game: the landscape components, the rock meshes as a
max-Z overlay, the interface raster as fill outside the landscape frame, and the water
actors. A full run also writes the rock pack. It refuses to write if the field misses its
accuracy bound at the resource nodes, and it refuses to replace a field from another build
without `--force`. `--out-dir` picks another folder.
[spatial-and-map.md](../../docs/spatial-and-map.md) §22 describes the planes.

### caves

Writes only the cave masks, reading an existing field at `--field` (default
`data/local/heightmap/`) into `--caves-dir` (default `data/local/caves/`). It refuses to
replace an existing directory without `--force`. See §23.

### rocks

Adds only the collision pack (`rocks.npz`, `rocks.json`) to the field at `--field` and
touches nothing else there. It refuses a field cut from another build, and an existing pack
without `--force`. See §24.

### paint

Extracts the landscape's paint layers once per game build: one weight plane per layer on
the 1 m grid, the tree canopy cover and crown tops, the PigmentMap tint, each layer's albedo,
the landscape's baked ground colour with the layer albedos refitted to it, and the cliff
families' tints and top layers, and the seabed coral carpet's cover and top. Only the
`painted` render layer reads them. See §27 and §30 to §32.

### artwork

Cuts the game's own drawn map into `map.png` and a tile pyramid. `--enhance` adds two
upscaled zoom levels through Real-ESRGAN on the GPU. It downloads the upscaler into the user
cache folder once. `--no-tiles-2x` skips the high-density pyramid. See §17.

### renders

Draws the `terrain`, `satellite`, `painted`, `relief` and `relief-dark` layers from the
heightfield, the game's biome raster and, for `painted`, the paint layers. Each layer is cut into the same 256 px pyramid
as the artwork. The main options:

- `--layer L`, repeatable, picks the layers. The default is all five.
- `--size` takes 1024 to 32768. The smaller sizes are previews.
- `--renders-name` writes beside the current renders instead of over them.
- `--unlit` draws the colour without light, bakes the lighting pyramid into
  `<renders>/light/`, and keeps a default-sun copy in each layer's `tiles/`. See §29.
- `--cache-dir` with `--keep-direct` keeps the geometry rasters, so a later run at the same
  size and build reuses them.
- `--restyle` draws only from those kept caches and exits with code 9 when one is missing or
  was cut for another size or build, so a palette change never turns into a full render.
- `--no-titan-trees` leaves the Titan forest's trees off the painted layer, a style variant
  with its own digest (§30).

A full-size run needs about 10.7 GB of scratch space for those caches. See §25 to §27, and
[maps_contract.md](../../docs/maps_contract.md) for how the server registers the result.

### check-fill

Scores the rebuilt height lattice against held-out landscape through the same functions a
render calls. It checks fill, seams, holes and the sampler. Only the baselines are
emulated. It reads the field and the game and writes nothing unless `--json` names a file.
See §26.

## Package map

Everything is under `src/mapgen/`. Each subpackage owns one provenance axis, so a change can
be traced to the axis it should move.

| Module | Axis | Holds |
|---|---|---|
| `cli.py` | | The `python -m mapgen` command table. Standard library only, so a spawned worker does not import numpy. |
| `common.py` | | Repository root, `data/local`, the default game path, the shared argument parser |
| `pipeline.py` | | The renders orchestrator: arguments, refusals, stage order |
| `heightmap.py` | data | The heightmap, caves and rocks command: arguments, refusals, stage order |
| `artwork.py` | data | The artwork command: arguments, stage order, refusals |
| `check_fill.py` | | The check-fill command |
| `cache.py` | | The stamped raster caches (direct, top, meshes, Titan trees). The on-disk names are unchanged. |
| `gamedata/frame.py` | data | Map frame (read from `geo.MAP_SQUARE_M`), render sizes, heightfield grid |
| `gamedata/sweep.py` | data | Level sweep, foliage, landscape frame, baseline |
| `gamedata/mesh.py` | data | Mesh decode, `MaxZRaster`, cliff and top rasters, water-actor boxes |
| `gamedata/paint.py` | data | The paint command and the paint-layer store |
| `gamedata/bake.py` | data | The landscape's baked ground colour and the layer refit |
| `gamedata/rockfamily.py` | data | Cliff material families: per placement, tint and top layer |
| `gamedata/carpet.py` | data | The seabed coral carpet's harvest and planes, written by the paint command |
| `gamedata/biome.py` | data | Biome raster and its calibration |
| `gamedata/caves.py`, `rocks.py` | data | Cave masks, rock collision pack |
| `gamedata/water.py` | data | The heightfield's water channel: artwork mask, box levels, region masks |
| `gamedata/artwork_sheet.py` | data | The artwork sheet: slice decode, layout proof, corner calibration |
| `terrain/field.py` | data | Heightfield composition, plane encoding, and each layer's sidecar block |
| `terrain/validate.py` | data | Heightfield gates (nodes, bare terrain, water), per-layer accuracy, the water block |
| `terrain/sidecar.py` | data | The heightfield's `meta.json`, its staleness guard, the run's progress lines |
| `terrain/fill.py` | renderer | Lattice rebuild: fill, seams, holes |
| `terrain/sample.py` | renderer | Sampling kernels (PCHIP, Catmull-Rom, linear), resampling |
| `terrain/rasters.py` | renderer | Direct and top rasters on the output grid, render-only meshes |
| `terrain/measure.py` | renderer | `SeamTrace`, `RegimeCoverage` |
| `palette/styles.py` | style | Palette loading, digests and the colour painters |
| `palette/palettes/*.json` | style | One palette per style. Its digest is the file's canonical JSON. |
| `palette/painted.py` | style | The game-painted ground |
| `palette/relief.py` | style | The relief styles' painter (light and dark palettes) |
| `palette/water.py`, `shore.py` | style | Water drawing, shore optics, foam |
| `lighting/hillshade.py` | light | Hillshade, sun term, artwork borrow |
| `lighting/sun.py`, `model.py` | light | The game's sun path and default; the live-light model and its reference |
| `lighting/horizon.py`, `stage.py` | light | Normals, sky view, faded horizons; the stage that writes the lighting pyramid |
| `palette/lightparams.py` | style | What the page's shader reads from a style |
| `tiles/lit.py` | | Installing a layer drawn unlit: `unlit/` and the default-sun copy |
| `tiles/borrowmeta.py` | | The sidecar record of the artwork borrow |
| `lighting/lights/` | light | Light files (empty for now) |
| `tiles/compose.py` | | The band loop that draws a layer |
| `tiles/pyramid.py` | | Installing a layer and cutting its pyramid |
| `tiles/sidecar.py` | | The render sidecar |
| `tiles/recipes.py` | | The recipe numbers and their words, renders and artwork |
| `tiles/rendertext.py` | | The render sidecar's sampling, composition, z7 and level-only text |
| `tiles/artwork_output.py` | | The artwork's `tiles/` and `tiles@2x/` trees, its `map.json`, and the staleness guard that reads it back |
| `enhance/upscaler.py` | recipe | The Real-ESRGAN binary: one-time download into the user cache, digest, smoke test |
| `enhance/pixels.py` | recipe | Pre-sharpen, faint-mark repair and colour fix around the model |
| `enhance/levels.py` | recipe | The enhanced levels (source squares, upscale, repair, tiles) and the seam and low-zoom checks the sidecar records |

Imports run one way. Subpackages never import a command module (`pipeline`, `heightmap`,
`artwork`, `check_fill`, `cli`), and nothing in the package imports `tools/`.

The container opener and the artwork sheet's slice reader are game readers, so they live in
`satisfactory_mcp.core.gameassets.container` beside the IoStore reader.

## Design notes

Why some constants have the values they have. The code keeps a one-line comment and points
here.

### The band loop (`tiles/compose.py`)

`render_layer` draws a sheet 256 rows at a time; at 32768 a whole-sheet float32 intermediate
is four gigabytes. Each band carries `BAND_HALO` rows either side and crops them, because a
one-sided difference at every band edge would draw a line across the world. `direct` is the
rock raster's memory maps with the ground lattice and the sub-sampling, and `overlay` the
arch-and-boulder pair; without them the picture is one regime. `seam` and `regimes` are the
measuring accumulators, passed for the first layer only since every layer draws one surface.
`meshes` is the render-only mesh raster, `reach` the plane where the ocean's crossing rule
applies (`None` keeps recipe 5's water). `painted` and `relief` are the prepared grounds of
those styles, built once per run. `window` draws part of the sheet, which is how crops are
compared.

### Light (`lighting/hillshade.py`)

- **The sun** sits north-west at 45 degrees, the convention every relief map uses. Lit from
  anywhere else, the reader's eye inverts the valleys. `hillshade` is a dot product against
  the surface normal rather than slope-and-aspect trigonometry, because the array's axes are
  unambiguous and compass angles are not: rows run south, columns run east. Getting that
  backwards inverts every valley and still looks like terrain.
- **`SHADE_FLOOR`**: a shade of 0 would be black ground. A fully shadowed slope keeps 45% of
  its own colour, dark enough to read as shadow and light enough that the colour underneath
  still says something.
- **`WATER_SHADE_*`**: a lake that ignores the light sits on the picture rather than in it,
  but the shading is computed from the ground under the water, so it touches water only a
  little.
- **The borrow.** Cliff is rasterised low-poly collision hulls and fill is a 3.9 m block
  raster, so over them a render drawn from the field alone is smooth because it has nothing
  to say. Landscape (45.3% of the field) is continuous geometry the game evaluates itself and
  is left out. `BORROW_PROVENANCE` spells both cliff values through `PROV_CLIFF_VALUES`: 73%
  of the shipped field's cliff province is 5, so listing only 4 would withdraw the borrow
  from three quarters of it.
- **`BORROW_FEATHER_M`**: the provenance byte is a hard label on a 1 m grid, so a hard switch
  between two shading rules would draw the label itself, a coastline of shading style around
  every island. `coarse_province` blurs the mask once at the field's resolution rather than
  per band with a halo wide enough for the kernel, and stores it as uint8 because it is about
  to multiply a term already quantised to 1/127.
- **`BORROW_DETAIL_SIGMA_PX`** (artwork pixels, 0.92 m each): the high pass is the sheet minus
  its own Gaussian blur, so only detail finer than about 7 m crosses. The coarse structure is
  the field's job, and two sources drawing it would double every hillside. The detail is held
  as int8 (67 MB against 268 for float32).
- **`BORROW_DETAIL_SOFTEN_PX`, `BORROW_DETAIL_SIGMAS`**: the map is a drawing, and every rock
  formation is outlined in a hard dark line one or two pixels wide. The high pass is blurred,
  which turns a stroke into the gradient it stands for, then squashed through `tanh`. A soft
  clip passes the mid-tones (the shading) almost linearly and saturates the outliers (the
  ink).
- **`BORROW_GAIN`** was picked by looking at four crops: at 0.17 the offshore cliff islands
  are still flat facets, at 0.50 the drawn map's contour rings read as rings, and 0.30 is
  where a collision hull stops being eight flat plates and starts being rock.
- **`BORROW_LUMA`** is Rec. 601, the weighting that matches how a person sees light. Colour
  never crosses: an ocean drawn blue contributes its brightness and nothing else.

### Water (`palette/water.py`)

- **`WATER_EDGE_M`**: the field is a 1 m grid and "is this texel under water" is a step
  function on it, so a hard test draws every coast as 1 m blocks. Under this depth, water and
  ground are mixed.
- **`WATER_EDGE_BLUR_M`**: the depth feather does nothing where the shore is a cliff. There
  the water goes from nothing to metres deep across one texel, and much of this world's water
  sits in box-shaped bodies against exactly that. A blur under a metre cannot move a
  shoreline, only stop it being a staircase. It is in metres rather than output pixels, so
  doubling the sheet does not halve how much ground it means.
- **Level-only water** (level known, depth not) is drawn at full alpha: on a 3.9 m block
  raster's rounding error the depth ramp would erase three and a half square kilometres of
  ocean. It is drawn at the deep end of the colour ramp, because on the shipped field 95.2% of
  level-only water stands over the fill province and 98% of its surface levels lie inside a
  0.7 m band around the ocean's -16.99 m. Drawing it shallow would paint the open ocean the
  pale green of an ankle-deep sheet.
- **A field without `waterq.u8.z`** falls back to "a water surface stands above the ground".
  That reads the open ocean as dry, because over the fill province the ground is a 3.9 m
  raster that rounds above a sea surface 17 m down, so the missing byte is reported in the
  sidecar.

### Satellite colours (`palette/styles.py`)

- **`BIOME_COLOURS`** were chosen by eye against crops of this world, not taken from the
  asset's `mColorPalette`, a UI legend of flat primaries, cyan, magenta and pure white. The
  scheme is desaturated, nothing is brighter than about 220, and the greens run from bleached
  olive on the dry forests to near-black canopy.
- **`BIOME_BLEND_TEXELS`**: the game's areas are minimap polygons with hard edges, and nothing
  in an aerial photograph has one. 24 texels is about 44 m, a tree line's worth, and narrow
  enough that a 300 m biome keeps its own colour in the middle.
- **`NO_MANS_LAND_RGB`, `UNKNOWN_BIOME_RGB`**: the outer coast has no biome, so it is a
  neutral bleached ground that reads as beach and shelf. An area a later build adds gets the
  same neutral, so it looks unremarkable rather than wrong.
- **`RAMP_*_PCT`** are percentiles, not min and max: a single 400 m spire would flatten the
  ramp over the rest of the world.
- **The noise** is two small fixed-seed fields sampled by world position rather than
  generated per band, so no band boundary shows.

### The seam statistic (`terrain/measure.py`)

The statistic is the p99 of the second difference of the drawn height along a row, at the
seam against the pure regimes on either side, as a ratio. Above `SEAM_RATIO_MAX` the
cross-fade draws curvature the surface does not have, and the run says so. "At the seam" is
the whole blend rather than a window around the half-weight line: a feather's second
derivative is zero at its own midpoint by symmetry, so a window around `w = 0.5` measures the
one place a hard join has nothing to show. Each 3-texel stencil is sorted into one of three
pools by the weights under it: wholly kernel, wholly direct, or straddling.
`SEAM_SWITCH_CEILING` is what a hard switch reads; a convex blend of two surfaces can never be
rougher than the switch between them, so it is an identity rather than a bound.
`SEAM_NEAR_TEXELS` (7.3 m at z7) keeps the comparison to the ground beside the seam, not the
rest of the world, which is mostly open ocean.

### The render sidecar (`tiles/sidecar.py`)

The four corner keys sit at the top exactly as `map.json`'s do, and `_meta.tiles` carries the
same block, so the endpoint reads a render layer with the code it has for the artwork.
`_meta.tiles_2x` is that block again for the denser tree; its absence means the layer has
none.

## Tests

`tools/mapgen/tests` is in the root `testpaths`, so the default `uv run pytest -q` runs it,
and the root `pythonpath` setting finds the package before `uv sync --all-packages` has run.
The generator tests that need numbers from the moved modules live in the root `tests/`. They
read fixtures only.

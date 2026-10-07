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
| `paint` | `gen_paint_layers.py` | `data/local/paint/` (113 MB) | 2 min on a loaded machine; budget 2.5 min |
| `artwork` | `gen_map_image.py` | `data/local/` (`map.png`, `map.json`, `tiles/`, `tiles@2x/`) | 3 min; 14 min with `--enhance` |
| `renders` | `gen_map_renders.py` | `data/local/renders/<layer>/` and `light/` | 3 min at `--size 1024`; about 30 min for two layers at full size, both measured without the light; budget about 12 min more for the light on two layers at full size |
| `check-fill` | `check_map_fill.py` | nothing, unless `--json <file>` | not measured |
| `compress-cache` | | the given raster caches, converted in place | 4 s for a 1.3 GB Titan cache; about 2.5 min for a full set (estimate) |

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
the 1 m grid, the tree canopy cover, the PigmentMap tint, each layer's albedo, the
landscape's baked ground colour with the layer albedos refitted to it, the cliff families'
tints and top layers, and the seabed coral carpet's cover and top. It also writes
`water_bodies.json` (every water actor's box and materials and the hot-spring terraces) and
the tree crowns: a top-down sprite per tree species, a record per tree (position, yaw, scale,
lean, species) and the crown top on the 1 m grid. Only the `painted` render layer reads them.
See §27, §30 to §33 and §36.

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
- `--overwrite-in-use` writes even into a folder holding tiles of a map type the server's
  registry (`data/local/maps/manifest.json`) lists. Without it, a run whose
  `<out-dir>/<renders-name>` holds one is refused with exit code 10 before the field or the
  game is read, and the message names the types. Junctions and links are followed, so where
  `renders` links to the set the default map is drawn from, a run without `--renders-name`
  stops there. `--force` overrides only the build check. With no manifest nothing is in use.
  The server's jobs always write a new folder and never pass it.
- The live-sun light is on by default (`--light`). The colour is drawn without light, the
  lighting pyramid goes into `<renders>/light/`, and each layer's `tiles/` keeps a
  default-sun copy. It applies to every layer and size, to `--kernel-only` and to
  `--restyle`. `--no-light` draws the hillshade into the colour and writes no lighting
  pyramid. `--unlit`, the opt-in from before the light was the default, still means
  `--light`. See §29.
- `--cache-dir` with `--keep-direct` keeps the geometry rasters, so a later run at the same
  size and build reuses them, and the finished light in `light.kept/`, which a later run that
  draws the same surface installs instead of baking (§29, "Kept light").
- `--scratch-dir` puts the light's `light.cache/` elsewhere, such as a fast local disk; by
  default it sits beside the raster caches. It is scratch for one run, kept by no flag: the
  run deletes it however it ends, and the next lit run removes what a killed one left (§29).
- `--light-workers` sets how many processes bake the light. By default it is one a core, at
  most 16, and no more than the free memory holds at 1.5 GB each, counted when the bake starts
  (§29, "The stage"). `--cut-workers` sets how many encode the tiles: by default one a core, at
  most 24, fewer when memory is short, and `1` cuts serially (§17, "Cutting in parallel").
  `--workers N` sets both where its own flag is not given, so older command lines keep their
  meaning.
- `--restyle` draws only from those kept caches and exits with code 9 when one is missing or
  was cut for another size or build, so a palette change never turns into a full render.
- `--no-titan-trees` leaves the Titan forest's trees off the painted layer, a style variant
  with its own digest (§30).
- `--draw-threads N` draws N pieces of the bands at once. The default is 8, no more than the
  cores, and fewer when free memory holds fewer pieces in flight; `1` draws them in turn. The
  tiles are the same bytes either way (§40).
- `--draw-columns N` draws each band in pieces of N output columns, 512 by default. Narrower
  pieces take less memory a thread, and the tiles are the same bytes at any width up to 16384
  (§40, "Column pieces").
- `--gpu` runs the light's horizon march and sky view as CUDA kernels. It needs the root
  project's `gpu` extra (`uv sync --all-extras` installs it) and an NVIDIA driver, and
  refuses with exit code 2 where either is missing. The tiles are the same bytes (§41, "On
  the GPU").

At full size those caches take about 0.9 GB of scratch space, stored as a zstd band store
(§39). A raw cache kept by an older version is 18.5 GB at full size; it is still reused, and
`compress-cache` converts it. The light cache adds 14.5 GB while the run lasts, and 5.4 GB
more with the painted layer; the run deletes it (§29, "Scratch"). See §25 to §27 and §39, and
[maps_contract.md](../../docs/maps_contract.md) for how the server registers the result.

### check-fill

Scores the rebuilt height lattice against held-out landscape through the same functions a
render calls. It checks fill, seams, holes and the sampler. Only the baselines are
emulated. It reads the field and the game and writes nothing unless `--json` names a file.
See §26.

### compress-cache

`python -m mapgen compress-cache <dir>` converts raw raster caches to the zstd band store in
place. `<dir>` is one cache (`direct.cache`, `top.cache`, `meshes.cache`, `titan.cache`) or a
folder holding them, such as `data/local/maps/_cache/<size>`. It reads every band back
before it records the new storage in the sidecar, and only then deletes the raw planes. It
refuses a cache with no `meta.json`, which may still be being written, and on Windows one a
render holds open. `--to <dir>` writes the band store elsewhere and leaves the source alone.
A `--to` that is the source, a folder holding it or a folder inside it, links and junctions
followed, is refused before anything is written. It does not read the game. See §39.

## Package map

Everything is under `src/mapgen/`. Each subpackage owns one provenance axis, so a change can
be traced to the axis it should move.

| Module | Axis | Holds |
|---|---|---|
| `cli.py` | | The `python -m mapgen` command table. Standard library only, so a spawned worker does not import numpy. |
| `common.py` | | Repository root, `data/local`, the default game path, the shared argument parser |
| `colour.py` | | sRGB, linear light and OKLab, the luminance weights and the flat light |
| `pools.py` | | What a pool of workers may take: the free memory, and one BLAS thread in each worker |
| `jit.py` | | The kernel switch: numba-compiled loops, CUDA kernels (`MAPGEN_KERNELS=cuda`, `--gpu`), or the numpy they equal bit for bit (`MAPGEN_KERNELS=numpy`); a cache file per compiled signature |
| `cache.py` | | The stamped caches (direct, top, meshes, Titan trees, rivers) and how a raster cache is stored: band store or raw memory maps. The on-disk names and stamps are unchanged. |
| `bandstore.py` | | The zstd band store: `BandWriter` and the read-only `BandArray` |
| `commands/renders.py` | | The renders orchestrator: arguments, refusals, stage order |
| `commands/heightmap.py` | data | The heightmap command: arguments, refusals, stage order; `--caves` and `--rocks` go to the next row |
| `commands/caves.py`, `rocks.py` | data | The cave masks and the rock collision pack, written beside a field |
| `commands/paint.py` | data | The paint command: one level walk into the paint-layer store |
| `commands/artwork.py` | data | The artwork command: arguments, stage order, refusals |
| `commands/check_fill.py` | | The check-fill command |
| `commands/compress_cache.py` | | The compress-cache command |
| `gamedata/frame.py` | data | Map frame (read from `geo.MAP_SQUARE_M`), render sizes, heightfield grid |
| `gamedata/artwork_sheet.py` | data | The artwork sheet: slice decode, layout proof, corner calibration |
| `gamedata/meshes.py` | data | Mesh geometry reads, collision hulls, `ExtendedBounds` |
| `gamedata/maxz_raster.py` | data | `MaxZRaster`, the max-Z scatter rasteriser cliffs, crown sprites and render meshes share |
| `gamedata/placements.py` | data | A placement's rotation, and the culls by owner, mesh name, arch and size |
| `gamedata/materials.py` | data | Material-instance parameters, and the materials of a mesh's sections |
| `gamedata/nodes.py` | data | The static resource-node table, and the oil nodes the bake stamps |
| `gamedata/install.py` | data | The install opened once: `GameReader`, `open_game`, `missing_container` |
| `gamedata/level/sweep.py` | data | The level sweep: foliage, water actors, landscape components |
| `gamedata/level/landscape.py` | data | The landscape frame and its seam offsets |
| `gamedata/level/fill_raster.py` | data | `HeightData_Test`, the interface raster that fills outside the landscape |
| `gamedata/rocks/cliffs.py` | data | The field's cliff and top rasters |
| `gamedata/rocks/families.py` | data | Rock material families (the cliff layers and desert rock): per placement, tint and top layer |
| `gamedata/rocks/collision_pack.py`, `caves.py` | data | Rock collision pack, cave masks |
| `gamedata/water/actors.py` | data | Which classes are water actors, and each one's box in the world |
| `gamedata/water/channel.py` | data | The heightfield's water channel: artwork mask, box levels |
| `gamedata/water/bodies.py` | data | Water actors' materials, hot-spring terraces, the class of each body |
| `gamedata/water/rivers.py` | data | River splines (`BP_River_PROT_C`), sampling, the 1 m ribbon planes |
| `gamedata/water/falls.py` | data | Waterfall records from the `BP_WaterFallTool_02` actors, and their cache |
| `gamedata/vegetation/trees.py` | data | Tree instances as crowns: species bounds, instance scale, the tree table, canopy cover |
| `gamedata/vegetation/crown_sprites.py` | data | Tree crown sprites from LOD 0, tree records, the crown top plane |
| `gamedata/vegetation/carpet.py` | data | The seabed coral carpet's harvest and planes, written by the paint command |
| `gamedata/ground/paint_store.py` | data | The paint-layer store's folder and file names |
| `gamedata/ground/weightmaps.py` | data | The landscape's paint weightmaps, placed on the 1 m grid |
| `gamedata/ground/landscape_albedo.py` | data | The paint layers' textures and albedo, and the rock families' colours |
| `gamedata/ground/bake.py` | data | The landscape's baked ground colour and the layer refit |
| `gamedata/ground/biome.py` | data | Biome raster and its calibration, region masks |
| `terrain/heightfield/field.py` | data | Heightfield composition and plane encoding |
| `terrain/heightfield/validate.py` | data | Heightfield gates (nodes, bare terrain, water) |
| `terrain/heightfield/sidecar_blocks.py` | data | Each layer's sidecar block, per-layer accuracy, the water block |
| `terrain/heightfield/sidecar.py` | data | The heightfield's `meta.json`, its staleness guard, the run's progress lines |
| `terrain/fill.py` | renderer | Lattice rebuild: fill, seams, holes |
| `terrain/sample.py` | renderer | Sampling kernels (PCHIP, Catmull-Rom, linear), resampling, class planes, value noise |
| `terrain/kernels.py` | renderer | The resampling gathers and the crown stamps compiled by numba |
| `terrain/rasters.py` | renderer | Direct and top rasters on the output grid |
| `terrain/render_meshes.py` | renderer | The render-only meshes and the Titan trees on the output grid |
| `terrain/crown_stamp.py` | renderer | Tree crowns stamped into a band of the output grid |
| `terrain/measure.py` | renderer | `SeamTrace`, `RegimeCoverage` |
| `lighting/hillshade.py` | light | Hillshade and sun term |
| `lighting/borrow.py` | light | The artwork borrow and its sidecar record |
| `lighting/sun.py`, `model.py` | light | The game's sun path and default; the live-light model and its reference |
| `lighting/horizon.py`, `stage.py` | light | Normals, sky view, faded horizons; the drawn surface and one block of its light |
| `lighting/bake.py` | light | The lighting pyramid baked a row of blocks at a time, as the surface's rows come in |
| `lighting/kernels.py` | light | The horizon march and the sky view compiled by numba |
| `lighting/gpu.py`, `gpu.cu` | light | The same two as CUDA kernels, for `--gpu` |
| `lighting/occluders.py` | light | The canopy-top occluder raster the horizons take |
| `lighting/lights/` | light | Light files (empty for now) |
| `palette/styles.py` | style | Palette loading, digests, the colour painters and their height ramp |
| `palette/palettes/*.json` | style | One palette per style. Its digest is the file's canonical JSON. |
| `palette/relief.py` | style | The relief styles' painter (light and dark palettes) |
| `palette/lightparams.py` | style | What the page's shader reads from a style |
| `palette/scene.py` | style | What a band hands a painter: `BandScene`, its water terms, crowns, optics and grid |
| `palette/painted/ground.py` | style | The game-painted ground |
| `palette/painted/albedo.py` | style | The paint store mixed into a ground albedo, and the bake patched over it |
| `palette/painted/calibration.py` | style | Colour calibration: display targets taken back to ground colour |
| `palette/painted/surfaces.py` | style | Rock in its family's colour, the canopy over rock, the render-only meshes |
| `palette/painted/trees.py` | style | Trees over the painted pixel: the Titan forest and per-tree crowns |
| `palette/painted/optics.py` | style | What is seen under each wet pixel, the coral carpet |
| `palette/painted/kernels.py` | style | The colour under the water and its mix, compiled by numba |
| `palette/painted/water_classes.py` | style | The water-class plane and the swamp-to-ocean blends at mouths |
| `palette/water/surface.py`, `shore.py` | style | Water drawing, shore optics, foam |
| `palette/water/wet.py` | style | A band's wet pixels, where the colour under the water is painted |
| `palette/water/kernels.py` | style | The terrain, satellite and relief styles' water, compiled by numba |
| `palette/water/open_sea.py` | style | The open sea's bed past the measured one, and the void planes |
| `palette/water/rivers.py` | style | River water: reconciled with the field's, laid over each band |
| `palette/water/falls.py` | style | Waterfalls: the foam streak, the plunge pool and the mist |
| `palette/water/perched.py` | style | Water levels re-read from the shoreline where a box top is not the surface |
| `render/compose.py` | | The band loop that draws every layer of a run in one pass |
| `render/surface.py` | | One band's ground, composed once for all the layers |
| `render/painting.py` | | One band coloured in one layer's style over that ground |
| `render/stream.py` | | Each settled band handed to its layers' tile trees, the lit ones once the light has its rows |
| `render/drawpool.py` | | How many threads draw a pass's bands, and the pool that keeps their order |
| `render/stencils.py` | | How far each step of a band's draw reads its neighbours, and the band halo that holds them |
| `render/extras.py` | | What a run loads beside the field: meshes, falls, Titan trees and rivers |
| `render/light.py` | | A run drawn unlit: the light baked as the bands come in, a kept light read while it matches, the default-sun relight |
| `render/inuse.py` | | The refusal to write over a registered map type. It reads the manifest as plain JSON, because mapgen may not import `domain.maps`. |
| `tiles/pyramid.py` | | A layer's tile trees, the worker flags, the parallel cutter's self-check |
| `tiles/cutter.py` | | The parallel cutter: every tree of a run cut as its sheets' rows come in, through one encode pool |
| `tiles/levels.py` | | A sheet's pyramid levels resampled in strips as its rows arrive |
| `tiles/imaging.py` | | Pillow as the cutters and pyramids use it |
| `tiles/sidecar.py` | | The render sidecar |
| `tiles/recipes.py` | | The recipe numbers and their words, renders and artwork |
| `tiles/rendertext.py` | | The render sidecar's sampling, composition, z7 and level-only text |
| `tiles/artwork_output.py` | | The artwork's `tiles/` and `tiles@2x/` trees, its `map.json`, and the staleness guard that reads it back |
| `enhance/upscaler.py` | recipe | The Real-ESRGAN binary: one-time download into the user cache, digest, smoke test |
| `enhance/pixels.py` | recipe | Pre-sharpen, faint-mark repair and colour fix around the model |
| `enhance/levels.py` | recipe | The enhanced levels (source squares, upscale, repair, tiles) and the seam and low-zoom checks the sidecar records |

Imports run one way. No module imports a command under `commands/` (only `heightmap` hands
`--caves` and `--rocks` to theirs), `cli` reaches them by name, and nothing in the package
imports `tools/`.

The container opener and the artwork sheet's slice reader are game readers, so they live in
`satisfactory_mcp.core.gameassets.container` beside the IoStore reader.

## Design notes

Why some constants have the values they have. The code keeps a one-line comment and points
here.

### The band loop (`render/compose.py`)

`render_layers` draws every layer of a run in one pass, 256 rows at a time; at 32768 a
whole-sheet float32 intermediate is four gigabytes. Each band's ground is composed once and
every layer's painter colours it; the painted layer, which keeps the meshes in the sea, gets
a second ground that differs in the meshes and the water over them alone. Each band carries
`BAND_HALO` rows either side and crops them, because a one-sided difference at every band
edge would draw a line across the world; the halo is the widest reach in
`render/stencils.py` (docs/map/renders.md section 40). `direct` is the rock raster's two
planes with the ground lattice and the sub-sampling, and `overlay` the arch-and-boulder pair;
without them the picture is one regime. `seam` and `regimes` are the measuring accumulators,
which measure the one ground once. `meshes` is the render-only mesh raster, `reach` the plane
where the ocean's crossing rule applies (`None` keeps recipe 5's water). `painted` and
`relief` (by layer) are the prepared grounds of those styles, built once per run. `falls` are
the prepared waterfalls and `rivers` the `RiverWater` whose ribbons replace the field's river
water. `window` draws part of the sheet, which is how crops are compared. `bands` takes each
band once it is settled, in order, and then no sheet is kept: the renders command cuts the
bands into their tiles as they come (`render/stream.py`, docs/map/renders.md section 42);
without it the sheets come back in memory. Each band is drawn in pieces of `columns` output
columns, and `threads` draws that many pieces at once to the same bytes
(`render/drawpool.py`, `--draw-columns`, `--draw-threads`): the pieces share the pass's
inputs read-only and each writes its own pixels; each band, once its pieces are in, hands
the light its rows and is measured for the seam and regime tables, in band order
(docs/spatial-and-map.md section 40). `render_layer` is one layer alone, in memory.

On a sheet coarser than the paint's 1 m grid (4096 px and below) the painted layer samples
its ground over each pixel's footprint (`terrain.sample.taps_footprint`). One bilinear sample
of the 1 m ground per 3.66 m pixel drew the bake's stippled blends as speckle and its 1 to 2 m
trails as dotted lines, in the Rocky Desert most of all. A pixel no wider than a texel keeps
the bilinear taps, so 8192 px and up draw as before.

### Light (`lighting/hillshade.py`, `lighting/borrow.py`)

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
- **`BORROW_INK_PX`** (7 artwork pixels, 6.4 m): the soft clip still let every outline and
  contour line through as a dark ring around each plateau and rock, in every style; painted
  and relief damp the dark side, terrain and satellite did not. A grey closing, then an
  opening, with a disk this wide removes every stroke narrower than it, dark or light,
  before the high pass; plateau steps and rock shading are wider and stay. 5 px left a
  dotted trace of the strokes. The `tanh` scale is still the spread of the sheet as drawn
  (6.52), so the shading lends as much as before.
- **`BORROW_GAIN`** was picked by looking at four crops: at 0.17 the offshore cliff islands
  are still flat facets, at 0.50 the drawn map's contour rings read as rings, and 0.30 is
  where a collision hull stops being eight flat plates and starts being rock.
- **`BORROW_LUMA`** is Rec. 601, the weighting that matches how a person sees light. Colour
  never crosses: an ocean drawn blue contributes its brightness and nothing else.

### Tree shadows (`lighting/horizon.py`, `lighting/occluders.py`, `render/light.py`)

Shadows are not baked into colour: the lighting stage of section 29 stores faded horizons
and the page's sun picks two directions. Trees join that pass as its `occluder`, so they
shade for any sun.

- **Only where trees are drawn.** The crowns cast into horizons of their own, the atlas's
  second half, and only a style that draws the crowns reads them (`shader_light`'s
  `crowns`, the painted style today; `render.light.crown_layers`, the light sidecar's
  `occluder_layers`). Baked into the one horizon set every style shares, they shaded
  terrain, satellite and relief with trees those styles do not draw: near-black blocks in
  the forests and Red Bamboo, dashes in the desert. A crown cell keeps its horizon only
  where it stands above the ground's, so it is empty away from trees and costs 29% more
  light bytes at 2048.
- **Where the occluder comes from.** A run drawn with the light hands the paint store's 1 m
  crown-top plane (`crown.i16.z`, section 36) to the stage whatever layers it draws, sampled on
  the sheet's grid (`occluders.sheet_crowns`): each pixel averages a box of its own width,
  one texel wide on a sheet finer than the plane, which is the bilinear sample. It gives the
  mean crown top and the covered share, and the stage stands each crown on the surface
  lifted by that share (`horizon.crown_surface`), so a crown keeps its area and its round
  edge and a small one casts a small shadow. The old maximum filter and nearest sample drew
  every crown as a block a pixel too big. Both are memory maps in the light cache. A run
  without a paint store bakes no tree shadows; the light sidecar's `occluder` says which.
- **`OCCLUDER_FADE_M` (25 m, 80 m)**: a crown is porous and its far shadow diffuse, so the
  occluder blocks under its own, shorter fade, beside the ground's `FADE_M`. With the
  ground's fade a Mangrove_Tall_01 at the 80 m cap lays a shadow about 93 m long under the
  16:00 sun (24 degrees); with this fade about 61 m, and a 20 m tree's shrinks from 44 m to
  36 m.
- **Receivers on the crown top.** Where a crown stands, the crown horizon is measured from its
  top, because that is the surface the painted layer draws there. Receiving on the ground
  under the crowns put 71-86% of two forest crops in shadow at every sun, which reads as black
  forest; on the top the forest-edge crop keeps a mean light of 0.61-0.78 under the canopy.
  The ground's horizons are measured on the ground, which is what every other style draws.
- **Crown shape** (`canopy_top`, for a tree table rather than the paint store). A heightfield
  cannot hold the air under a crown, so each tree is a column whose top is a dome.
  `CROWN_RIM` (0.3) puts the rim at 30% of the height: at 0.7 every crown edge was a cliff
  and overlapping crowns drew hard arcs; at 0 the shadow share grew 3-4 points with little
  visual gain.
- **Sizes.** `gamedata/vegetation/trees.py` takes species from the foliage mesh and top and radius from
  its `ExtendedBounds`, which match the LOD0 geometry for all 53 tree meshes (top within
  0.6 m). `CROWN_TOP_MAX_M` (80 m) caps the column under a lifted crown;
  `CROWN_MIN_RADIUS_M` drops trunks and bulbs. `terrain.rasters.sweep_world` harvests
  `is_tree` foliage into `sweep["trees"]` (93,375 instances on build 502094).
- **Not yet:** the crown domes are not in the normal pyramid, so a crown is lit by the ground's
  normal under it.

### Water (`palette/water/surface.py`, `open_sea.py`)

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
  ocean. On the shipped field 95.2% of level-only water stands over the fill province and 98%
  of its surface levels lie inside a 0.7 m band around the ocean's -16.99 m, so it is the
  ocean. There `palette/water/open_sea.py` draws it over a bed of its own, continued from the measured
  bed beside it and settling to 60 m offshore, so every style reads one seabed and the colour
  does not step at the data edge. Level-only water away from the ocean's level, and
  `--kernel-only`, keep the deep end of the ramp.
- **`OPEN_SEA_BLEND_M`, `OPEN_SEA_BLEND_PULL_M`**: a membrane fixed on the measured bed meets
  it in value but not in slope, and the hillshade and the depth tint both draw the kink. Along
  a landscape component's straight edge that kink was a line hundreds of metres long. Within
  100 m of the open sea the measured bed is no longer fixed. It only pulls the membrane, over
  about 25 m, and is laid back over it on a cosine taper, so the bed is smooth in slope too.
  A pull is a smooth constraint where a fixed cell is a hard edge, so its edge bends the slope
  but cannot kink it. Past the 100 m the measured bed is drawn as measured.
- **`OPEN_SEA_TONE_DEPTH_M`**: the artwork draws its water in four flat tones, and over the
  landscape's measured ocean each tone sits on one depth band: 5.3, 3.5 and 1.3 m in the
  median for the three lighter ones, and anything deeper for the teal. Where the open sea has
  no bed, a lighter tone pulls the membrane to its depth over `OPEN_SEA_TONE_PULL_M`, so a
  strip the artwork draws shallow is not drawn deep.
- **The void** (`void_planes`, `styles.with_void`): no data that does not reach the field's
  edge through no data is a pit, the rest is the void past the world's edge. Both are drawn as
  the artwork draws them, lit at the edge and darkening over a Gaussian of `VOID_FALLOFF_M`
  (50 m, the artwork's falloff reaches black in about 110 m), with a light rim. A pit goes from
  the artwork's grey to black, and the void past the edge goes to the page's own sea colour.
  Beside the open sea the bed runs on under the void and the sea fades into it over the same
  falloff, with no lit edge and no rim. Here "the sea" includes dry ground under its level
  within the shore rule's `OCEAN_REACH_M`, which that rule draws as sea. Low ground further
  inland is not counted: the southern lowlands lie under the sea's level. A rock standing in
  the void keeps the void off only above the sea's level; deeper, it is the void's, or the
  sea run on under the void would draw it as water. It is taken out of the height too, before
  anything is drawn, or the lighting stage would shade the void over it as land. A floor the
  fill emptied beside the void past the edge is part of that void. Drawn as a pit, the
  north-east corner's floor was a black rectangle on the page's navy.
- **`VOID_STRIP_M`**: dry ground under the sea's level in a gap at most 8 m wide between the
  open sea and the void past the edge, measured through that ground, is sea as well. The
  artwork's mask leaves it dry, and drawn as land it was a dotted dark line along the void
  beside the 638 m falls and on the north edge. A wider band stays the land the artwork
  draws: at 12 m the rule cut a 15 m spit on the north edge into pieces.
- **Perched water**: a sloped river's box top is its upstream end, and one body's box can
  cover a lower body. `palette/water/perched.py` re-levels such water from its own shoreline
  before it is drawn. A box can also reach past its own fall's lip: **`LIP_DROP_M`** cuts a
  body where the ground falls more than 8 m between neighbours, and the water below the
  drop is re-levelled first, so the basin under a fall is not drawn at the lake's level.
  The thresholds are measured in
  [spatial-and-map.md](../../docs/spatial-and-map.md) §38.
- **A field without `waterq.u8.z`** falls back to "a water surface stands above the ground".
  That reads the open ocean as dry, because over the fill province the ground is a 3.9 m
  raster that rounds above a sea surface 17 m down, so the missing byte is reported in the
  sidecar.

### Rivers (`gamedata/water/rivers.py`, `palette/water/rivers.py`)

- **The plane is the water.** A river is `SM_RiverPlane` bent along Hermite sections, so its
  surface is a height and a half width per point. The depth is that height minus the drawn
  ground.
- **`RIVER_LEVEL_MATCH_M`**: the field levelled river water on the top of the river actor's
  AABB. A texel within 5 cm of that top, with no other box as high, came from it.
- **`RIVER_MAX_DEPTH_M`**: wide sections hang tens of metres over waterfall pits. Past 8 m the
  plane is not over its own channel.
- **`RIVER_OVER_WATER_M`**, **`RIVER_STEP_M`**: a plane hanging over a lake would draw water
  in the air, and a jump between two planes would draw a line along the join.
- **`shore.river`** in each palette: the minimum depth the optics see once in from the bank.
  Without it, a shallow bed reads as a pale path.

spatial-and-map.md section 34 has the measurements.

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

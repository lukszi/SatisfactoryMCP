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
`--game` on any other machine. A command that needs the `gen` extra and lacks it prints the
fix and exits 2.

`tools/gen_world_heightmap.py`, `gen_map_renders.py`, `gen_map_image.py`,
`gen_paint_layers.py` and `check_map_fill.py` still exist at their old paths. Each is a
thin, standard-library-only shim that passes its arguments to the matching command. Every
recorded sidecar names them in `_meta.generator`, so they stay where they are. The server's
job runner starts `python -m mapgen <command>` with `tools/mapgen/src` on the child's path.

A render prints `::stage {"id": ..., "done": ...}` lines beside its human log lines
(`satisfactory_mcp.core.mapprogress`); the job runner reads progress from those first.

The design and its measurements are the map's sections of the spec, 17 to 42, which
[docs/spatial-and-map.md](../../docs/spatial-and-map.md) indexes by file under
[docs/map/](../../docs/map/). A § below is one of them.

## Commands

Times are from one mid-range machine on build 502094. "Budget" is what the Maps tab's
estimate assumes before a job of that kind has run once.

| Command | Old script | Writes (default) | Typical time |
|---|---|---|---|
| `heightmap` | `gen_world_heightmap.py` | `data/local/heightmap/` (planes, `meta.json`, and the rock pack) | 4–6 min; budget 15 min |
| `caves` | `gen_world_heightmap.py --caves` | `data/local/caves/` (`caves.npz`, `meta.json`) | sweep 6 s; budget 2 min |
| `rocks` | `gen_world_heightmap.py --rocks` | `rocks.npz` and `rocks.json` beside the field in `data/local/heightmap/` | 24 s; budget 5 min |
| `paint` | `gen_paint_layers.py` | `data/local/paint/` (113 MB) | 2 min on a loaded machine; budget 2.5 min |
| `calibrate` | | `data/local/paint/targets.derived.json` | 5 s |
| `artwork` | `gen_map_image.py` | `data/local/` (`map.png`, `map.json`, `tiles/`, `tiles@2x/`) | 3 min; 14 min with `--enhance` |
| `renders` | `gen_map_renders.py` | `data/local/renders/<layer>/` and `light/` | 3 min at `--size 1024`; at full size with the light, budget about 58 min for all five layers and 42 min for two, from measured stages ([maps_contract.md](../../docs/maps_contract.md) §4.3); a whole run is measured at the next full render |
| `check-fill` | `check_map_fill.py` | nothing, unless `--json <file>` | not measured |
| `compress-cache` | | the given raster caches, converted in place | 4 s for a 1.3 GB Titan cache; about 2.5 min for a full set (estimate) |

### heightmap

Cuts the 1 m heightfield out of the game: the landscape components, the rock meshes as a
max-Z overlay, the interface raster as fill outside the landscape frame, and the water
actors. A full run also writes the rock pack. It refuses to write if the field misses its
accuracy bound at the resource nodes, and it refuses to replace a field from another build
without `--force`. `--out-dir` picks another folder.
[docs/map/heightfield.md](../../docs/map/heightfield.md) §22, "The planes on disk", describes
the planes.

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
From generator 4 its `meta.json` also keeps the level's noon light, the atmosphere volumes and
the shell colours that `calibrate` reads. §27 lists the files; see also §30 to §33 and §36.

### calibrate

Derives a display colour for every key of the game-painted palette's `calibration` block from
the paint layers, the install's area map and the heightfield's water, through a model of the
game's camera, and writes `targets.derived.json` beside the paint layers (`--out` elsewhere).
It never writes the install. `--check` prints each colour against the screenshot target and
writes nothing. A paint store from before generator 4 is refused with exit code 2. A render
takes the keys the palette's `derived_keys` lists from that file, or derives them itself when
the file is missing or was derived from other data. See §31, "Targets derived from the game
install".

### artwork

Cuts the game's own drawn map into `map.png` and a tile pyramid. `--enhance` adds two
upscaled zoom levels through Real-ESRGAN on the GPU. It downloads the upscaler into the user
cache folder once. `--no-tiles-2x` skips the high-density pyramid. See §17.

### renders

Draws the `terrain`, `satellite`, `painted`, `relief` and `relief-dark` layers from the
heightfield, the game's biome raster and, for `painted`, the paint layers. Each layer is cut
into the same 256 px pyramid as the artwork. A run that cannot draw what its sidecar would
claim is refused with its own exit code (§20, "Refusals"). The main options:

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
  run deletes it however it ends, and the next lit run removes what a killed one left. A
  scratch a render still running holds is refused with exit code 11 (§29, "Scratch").
- `--light-workers` sets how many processes bake the light. By default it is one a core, at
  most 16, and no more than the free memory holds at 2.0 GB each, counted when the bake starts
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
  pieces take less memory a thread, and the tiles are the same bytes at any width (§40,
  "Column pieces" and "Fixed-order sums").
- `--gpu` runs the light's horizon march and sky view as CUDA kernels. It needs the root
  project's `gpu` extra (`uv sync --all-extras` installs it) and an NVIDIA driver, and
  refuses with exit code 12 where either is missing. The bake's log says where its calls
  ran. The tiles are the same bytes (§41, "On the GPU").

At full size those caches take about 0.9 GB of scratch space, stored as a zstd band store
(§39). A raw cache kept by an older version is 18.5 GB at full size; it is still reused, and
`compress-cache` converts it. The light's scratch adds 15.6 GB while the run lasts, and the
crown occluder 5.4 GB more wherever there is a paint store, whatever layers the run draws; the
run deletes it (§29, "Scratch"). With `--keep-direct` the light's default-sun terms, 4.3 GB,
stay in `light.kept/` beside the caches, and its tiles as hard links (§29, "Kept light"). A
cache the run cannot delete at its end is named: "could not remove <dir>: a file in it is
still open". See §25 to §27 and §39, and [maps_contract.md](../../docs/maps_contract.md) for
how the server registers the result.

### check-fill

Scores the rebuilt height lattice against held-out landscape through the same functions a
render calls. It checks fill, seams, holes and the sampler. Only the baselines are
emulated. It reads the field and the game and writes nothing unless `--json` names a file,
and it refuses a field whose terrain grid shares no vertex with the field's. See §26.

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
| `common.py` | | Repository root, `data/local`, the default game path, the shared argument parser, `Refusal`, and `require_gen` |
| `colour.py` | | sRGB, linear light and OKLab, the luminance weights, the tone curve, the sky-and-sun and flat light |
| `pools.py` | | What a pool of workers may take: the free memory, and one BLAS thread in each worker |
| `jit.py` | | The kernel switch: numba-compiled loops, CUDA kernels (`MAPGEN_KERNELS=cuda`, `--gpu`), or the numpy they equal bit for bit (`MAPGEN_KERNELS=numpy`); a cache file per compiled signature |
| `cache.py` | | The stamped caches (direct, top, meshes, Titan trees, rivers): stamps, readers, `rewrite_planes` and the atomic `write_sidecar`. Raster caches are written as band stores; raw memory maps are still read. |
| `bandstore.py` | | The zstd band store: `BandWriter` and the read-only `BandArray` |
| `commands/renders.py` | | The renders orchestrator: arguments, the pass and each layer's install; `render/run/prepare.py` prepares the run |
| `commands/heightmap.py` | data | The heightmap command: arguments, refusals, stage order; `--caves` and `--rocks` go to the next row |
| `commands/caves.py`, `rocks.py` | data | The cave masks and the rock collision pack, written beside a field |
| `commands/paint.py` | data | The paint command: one level walk into the paint-layer store |
| `commands/calibrate.py` | | The calibrate command: display targets derived from the paint store |
| `commands/artwork.py` | data | The artwork command: arguments, stage order, refusals |
| `commands/check_fill.py` | | The check-fill command |
| `commands/compress_cache.py` | | The compress-cache command |
| `gamedata/frame.py` | data | Map frame (read from `geo.MAP_SQUARE_M`), render sizes, the heightfield grid and its texel lookups (`grid_texel`, `grid_index`) |
| `gamedata/artwork_sheet.py` | data | The artwork sheet: slice decode, layout proof, corner calibration |
| `gamedata/meshes.py` | data | Mesh geometry reads at their finest source, collision hulls, `ExtendedBounds` (`MeshBounds`) |
| `gamedata/maxz_raster.py` | data | `MaxZRaster`, the max-Z scatter rasteriser cliffs, crown sprites and render meshes share, and its `INSTANCE_BATCH` |
| `gamedata/placements.py` | data | A placement's row in the sweep, its transform (`placement_transform`, `placement_matrix4`), the culls by owner, mesh name, arch and size (`cliff_cull`), and its material |
| `gamedata/materials.py` | data | Material-instance parameters, and the materials of a mesh's sections |
| `gamedata/nodes.py` | data | The static resource-node table (`load_static_nodes`), and the oil nodes the bake stamps |
| `gamedata/install.py` | data | The install opened once: `GameReader`, `open_game`, `open_package`, `missing_container` |
| `gamedata/level/sweep.py` | data | The level sweep: foliage, water actors, landscape components; `world_levels`, and instances to world (`quat_axes`, `instances_to_world`) |
| `gamedata/level/landscape.py` | data | The landscape frame and its seam offsets |
| `gamedata/level/fill_raster.py` | data | `HeightData_Test`, the interface raster that fills outside the landscape |
| `gamedata/level/curves.py` | data | `FRichCurve` keys and evaluation, and the numbers a tag holds |
| `gamedata/level/lighting.py` | data | The persistent level's noon light and the atmosphere volumes that override it |
| `gamedata/rocks/cliffs.py` | data | The field's cliff and top rasters |
| `gamedata/rocks/families.py` | data | Rock material families (the cliff layers and desert rock): `FamilyResolver`, per placement, tint and top layer |
| `gamedata/rocks/collision_pack.py`, `caves.py` | data | The rock collision pack (`rock_pack_arrays`, `encode_rock_pack`), the cave masks |
| `gamedata/water/actors.py` | data | Which classes are water actors, each one's box in the world, and the boxes' highest top on the 1 m grid (`water_box_tops`, `box_texels`) |
| `gamedata/water/channel.py` | data | The heightfield's water channel: artwork mask, box levels, lower bodies |
| `gamedata/water/bodies.py` | data | Water actors' materials, hot-spring terraces, the class of each body |
| `gamedata/water/rivers.py` | data | River splines (`BP_River_PROT_C`), sampling, the 1 m ribbon planes |
| `gamedata/water/falls.py` | data | Waterfall records from the `BP_WaterFallTool_02` actors, and their cache |
| `gamedata/vegetation/trees.py` | data | Tree instances as crowns: species bounds, instance scale, the tree table, canopy cover |
| `gamedata/vegetation/crown_sprites.py` | data | Tree crown sprites from LOD 0, tree records, the crown top plane |
| `gamedata/vegetation/carpet.py` | data | The seabed coral carpet's harvest and planes, written by the paint command |
| `gamedata/ground/paint_store.py` | data | The paint-layer store's folder and file names |
| `gamedata/ground/weightmaps.py` | data | The landscape's paint weightmaps, placed on the 1 m grid |
| `gamedata/ground/landscape_albedo.py` | data | The paint layers' textures and albedo, the rock families' colours, and the 0–1 sRGB transfer (`srgb_unit_to_linear`) |
| `gamedata/ground/bake.py` | data | The landscape's baked ground colour and the layer refit |
| `gamedata/ground/biome.py` | data | Biome raster and its calibration, region masks |
| `terrain/heightfield/field.py` | data | Heightfield composition and plane encoding |
| `terrain/heightfield/validate.py` | data | Heightfield gates (nodes, bare terrain, water) |
| `terrain/heightfield/sidecar_blocks.py` | data | Each layer's sidecar block, per-layer accuracy, the water block |
| `terrain/heightfield/sidecar.py` | data | The heightfield's `meta.json`, its staleness guard, the run's progress lines |
| `terrain/fill.py` | renderer | Lattice rebuild: fill, seams, holes, pits |
| `terrain/harmonic.py` | renderer | Fills over a mask: nearest, harmonic and biharmonic, the screened membrane, the hole fill |
| `terrain/emptied.py` | renderer | Where the rebuilt lattice is left empty because the artwork draws void: its pits, and the fill past its rim |
| `terrain/solve.py` | renderer | Conjugate gradients with fixed-order sums, for the membranes |
| `terrain/sample.py` | renderer | Sampling kernels (PCHIP, Catmull-Rom, linear), resampling, class planes, value noise |
| `terrain/kernels.py` | renderer | The resampling gathers and the crown stamps compiled by numba |
| `terrain/rasters.py` | renderer | Direct and top rasters on the output grid |
| `terrain/rasters_banded.py` | renderer | A banded raster: each band folded onto the output grid and written to its cache |
| `terrain/top_raster.py`, `archfill.py` | renderer | The top raster with the arches apart (top, underside) and the boulders alone; the arches' sub-metre holes filled |
| `terrain/overhangs.py` | renderer | Under each rock's top, its overhang's underside and the floor beneath it |
| `terrain/render_meshes.py` | renderer | The render-only meshes and the Titan trees on the output grid |
| `terrain/crown_stamp.py` | renderer | Tree crowns stamped into a band of the output grid |
| `terrain/measure.py` | renderer | `SeamTrace`, `RegimeCoverage` |
| `lighting/hillshade.py` | light | Hillshade, the sun term, the flat shade and slope |
| `lighting/borrow.py` | light | The artwork borrow and its sidecar record |
| `lighting/sun.py`, `model.py` | light | The game's sun path and default; the live-light model and its reference |
| `lighting/horizon.py`, `stage.py` | light | Normals, sky view, faded horizons; the drawn surface and one block of its light |
| `lighting/bake.py` | light | The lighting pyramid baked a row of blocks at a time, as the surface's rows come in |
| `lighting/light_tiles.py` | light | The lighting pyramid's tile format, the bake's work files, and the coarser levels |
| `lighting/kernels.py` | light | The horizon march and the sky view compiled by numba |
| `lighting/spans/march.py`, `kernels.py` | light | The march and the sky view over spans (arches, overhangs, crowns), and their numba kernels |
| `lighting/spans/bake.py`, `slabs.py` | light | A block's spans, the atlas's folded bands and the default sun's per-cell shade; the captured spans' sparse store |
| `lighting/spans/holes.py`, `canopy.py` | light | No data in the captured surface, which the light takes as open; the canopy's own light |
| `lighting/gpu.py`, `gpu.cu` | light | The same two as CUDA kernels, for `--gpu` |
| `lighting/occluders.py` | light | The occluders the horizons take: the paint store's crown tops on a render grid (`sheet_crowns`) and a tree table's domes (`canopy_top`) |
| `palette/styles.py` | style | Palette loading, digests, the colour painters and their height ramp |
| `palette/schema.py` | style | The palette files' shapes, and the check at load: a stray or missing key, or an unknown rock family, stops the run with a `PaletteError` naming the place in the file (§28). A key added to a palette needs its field here. |
| `palette/palettes/*.json` | style | One palette per style. Its digest is the file's canonical JSON. |
| `palette/relief.py` | style | The relief styles' painter (light and dark palettes) |
| `palette/lightparams.py` | style | What the page's shader reads from a style |
| `palette/scene.py` | style | What a band hands a painter: `BandScene`, its water terms, crowns, optics and grid |
| `palette/painted/ground.py` | style | The game-painted ground, built once per run from the paint store |
| `palette/painted/band.py` | style | One band of the painted style: `painted_colours`, `painted_ndl` |
| `palette/painted/shapes.py` | style | The painted style's palette, band, paint-store and ground types |
| `palette/painted/albedo.py` | style | The paint store mixed into a ground albedo, and the bake patched over it |
| `palette/painted/calibration.py` | style | Colour calibration: display targets taken back to ground colour |
| `palette/painted/transfer.py` | style | The paint layers moved onto their calibrated targets, texel by texel |
| `palette/painted/derive/camera.py` | style | The camera model: UE5's film curve, the default sky, the exposure, the screenshot discount |
| `palette/painted/derive/scene.py`, `rules.py` | style | The paint store on the 4 m grid, and the rule that derives each calibration key |
| `palette/painted/derive/targets.py`, `palette.py` | style | The light vote, the derived colours, `targets.derived.json`, and the palette a render wears them in |
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
| `palette/water/seams.py` | style | Small level steps inside one sheet of water, feathered into a ramp |
| `palette/water/geodesic.py` | style | Steps counted through a mask, as a flood grows out from its seeds |
| `palette/water/footprints/plane.py` | style | The render-only meshes' land plane: each coral, shell or terrace footprint kept whole on land or left whole to the seabed, and each piece's reading of it |
| `palette/water/footprints/reference.py`, `gpu.py`, `footprints.cu` | style | The land plane's per-texel marks and per-pixel reading in numpy, and the same as CUDA kernels for `--gpu` |
| `render/run/prepare.py` | | Every stage of a run before the first band is drawn, in order (`prepare`) |
| `render/run/inputs.py` | | A run's inputs and their refusals: the field and its lattices, the game, the borrow, the paint and the water |
| `render/run/biome_inputs.py` | | The game's biome raster as the biome layers draw it: read, checked and coloured |
| `render/run/cached_rasters.py` | | The level sweep (`LevelSweep`) and the stamped direct and top rasters, rasterised or read back |
| `render/draw/compose.py` | | The band loop that draws every layer of a run in one pass |
| `render/ground/surface.py` | | One band's ground, composed once for all the layers (`band_grid`, `band_surfaces`) |
| `render/ground/lift.py` | | The raise-only lift by which rocks and the top raster raise the ground |
| `render/ground/floating.py` | | What floats over a piece for the light: the arches and overhangs, and the surface without them |
| `render/ground/void.py` | | The void as a piece draws it, once for every layer, and the land weight the light reads off it |
| `render/draw/archaa.py` | | FXAA on the arches only, a band at a time with its neighbours' rows |
| `render/draw/painting.py` | | One band coloured in one layer's style over that ground (`paint_band`) |
| `render/draw/stream.py` | | Each settled band handed to its layers' tile trees, the lit ones once the light has its rows |
| `render/draw/drawpool.py` | | How many threads draw a pass's bands, and the pool that keeps their order |
| `render/ground/stencils.py` | | How far each step of a band's draw reads its neighbours, and the band halo that holds them |
| `render/run/extras.py` | | What a run loads beside the field: meshes, falls, Titan trees and rivers |
| `render/draw/light.py` | | A run drawn unlit: the scratch claimed and closed, the crown occluder, the light baked as the bands come in, a kept light read while it matches, the default-sun relight |
| `render/draw/kept_light.py` | | The finished light a lit render keeps beside its raster caches, and its install |
| `render/run/inuse.py` | | The refusal to write over a registered map type. It reads the manifest as plain JSON, because mapgen may not import `domain.maps`. |
| `tiles/pyramid.py` | | A layer's tile trees, the worker flags, the parallel cutter's self-check |
| `tiles/cutter.py` | | The parallel cutter: every tree of a run cut as its sheets' rows come in, through one encode pool |
| `tiles/levels.py` | | A sheet's pyramid levels resampled in strips as its rows arrive |
| `tiles/imaging.py` | | Pillow as the cutters and pyramids use it |
| `tiles/layer_meta.py` | | What a layer's `meta.json` says of how it was drawn: the render block and the layer's provenance |
| `tiles/sidecar.py` | | The render layer's `meta.json`, and the field build it pins |
| `tiles/recipes.py` | | The recipe numbers and their words, renders and artwork |
| `tiles/rendertext.py` | | The render sidecar's sampling, composition, z7 and level-only text |
| `tiles/artwork_output.py` | | The artwork's `tiles/` and `tiles@2x/` trees, its `map.json`, and the staleness guard that reads it back |
| `enhance/upscaler.py` | recipe | The Real-ESRGAN binary: one-time download into the user cache, digest, smoke test |
| `enhance/pixels.py` | recipe | Pre-sharpen, faint-mark repair and colour fix around the model |
| `enhance/levels.py` | recipe | The enhanced levels: source squares, upscale, repair, tiles |
| `enhance/checks.py` | recipe | The seam and low-zoom checks the sidecar records |

Imports run one way. No module imports a command under `commands/` (only `heightmap` hands
`--caves` and `--rocks` to theirs), `cli` reaches them by name, and nothing in the package
imports `tools/`.

The container opener and the artwork sheet's slice reader are game readers, so they live in
`satisfactory_mcp.core.gameassets.container` beside the IoStore reader.

## Design notes

Why some constants have the values they have: one line each, and the section that has the
measurement. The code keeps a one-line comment and points here.

### The band loop (`render/draw/compose.py`, `render/ground/surface.py`, `render/ground/lift.py`)

`render_layers` draws every layer of a run in one pass, 256 rows at a time (`BAND_ROWS`): at
32768 a whole-sheet float32 intermediate is four gigabytes. `render/ground/surface.py` composes each
band's ground once (heights, rocks, overlay, meshes, water, borrow), with a second one for the
painted layer's meshes in the sea, `render/draw/painting.py` puts each layer's colour on it, and
`render/draw/stream.py` cuts each settled band into its tiles (§40, §42).

- **`BAND_HALO`**, **`PIECE_HALO`** (16 rows, 16 columns): the widest reach in
  `render/ground/stencils.py`; each band and piece reads that far past its edges and crops it, so no
  edge is a one-sided difference drawn as a line across the world (§40).
- **`DRAW_THREADS`** (8), **`PIECE_COLS`** (512): the pieces share the pass's inputs
  read-only and write their own pixels; narrow pieces keep a thread's memory small, and past
  8 threads they wait on memory (§40, "Column pieces").
- **`DIRECT_LIFT_KNEE_M`** (0.25 m): the rocks raise the ground through a smoothed positive
  part, so a rock's foot is no derivative step for the hillshade to draw (§20).
- **`taps_footprint`**: on a sheet coarser than the paint's 1 m grid the painted ground is
  averaged over each pixel's footprint, or the bake's stippled blends draw as speckle (§30).

### Light (`lighting/hillshade.py`, `lighting/borrow.py`)

- **The sun** sits north-west at 45°, the relief-map convention. `hillshade` is a dot product
  against the surface normal, because rows run south and columns east, and compass angles
  read backwards invert every valley and still look like terrain (§29 for the live sun).
- **`SHADE_FLOOR`** (0.45), **`SHADE_RANGE`** (0.55): a fully shadowed slope keeps 45% of its
  colour, dark enough to read as shadow, light enough that the colour still says something.
- **`WATER_SHADE_FLOOR`** (0.75), **`WATER_SHADE_RANGE`** (0.25): water takes a little of the
  ground's shading, so a lake sits in the picture rather than on it.
- **`BORROW_PROVENANCE`**: cliff (both values, `PROV_CLIFF_VALUES`) and fill borrow the
  artwork's shading; landscape keeps its own (§17).
- **`BORROW_FEATHER_M`** (6 m): the borrow fades on the 1 m provenance label, so the label is
  not drawn as a coastline of shading; `coarse_province` blurs it once and stores uint8 (§17).
- **`BORROW_DETAIL_SIGMA_PX`** (8 artwork pixels): only detail finer than about 7 m crosses;
  the coarse structure is the field's. Held as int8, 67 MB against 268 (§17).
- **`BORROW_INK_PX`** (7 artwork pixels): a grey closing and opening this wide removes the
  drawn map's strokes before the high pass; 5 left a dotted trace (§17).
- **`BORROW_DETAIL_SOFTEN_PX`** (1.6), **`BORROW_DETAIL_SIGMAS`** (1.2): a blur, then a `tanh`
  soft clip that passes the shading and saturates the ink (§17).
- **`BORROW_GAIN`** (0.30): picked by looking at four crops (§17).
- **`BORROW_LUMA`**: Rec. 601; light crosses, colour never does (§17).

### Horizons and tree shadows (`lighting/horizon.py`, `lighting/occluders.py`, `render/draw/light.py`)

Shadows are not baked into colour: the lighting stage stores faded horizons and the page's
sun picks two directions (§29). Trees join it as its `occluder`.

- **`FADE_M`** (40 m, 150 m): a blocker counts fully to 40 m and not past 150 m; without the
  fade a low sun shadows a third to a half of the land (§29).
- **Only where trees are drawn.** The crowns cast into horizons of their own, read only by a
  style that draws them (`shader_light`'s `crowns`, `render.draw.light.crown_layers`, the light
  sidecar's `occluder_layers`); in the shared horizons they drew black blocks on every style
  (§29).
- **`sheet_crowns`**: the paint store's crown tops, whatever layers a run draws, box-averaged
  to the sheet's pixel, mean top and covered share; `horizon.crown_surface` lifts each crown by that share, so a small crown
  casts a small shadow (§29, "Hooks").
- **Spans** (`lighting/spans/march.py`): arches, rock overhangs and crowns block only between their
  underside and their top, so light passes beneath; `CROWN_UNDERSIDE` (0.5) puts a crown's
  underside halfway up its lift, `OVERHANG_CLEAR_M` (2 m) is the gap that makes a rock float
  (§29, "Arches as spans").
- **`OCCLUDER_FADE_M`** (25 m, 80 m): a crown is porous; under the 16:00 sun an 80 m mangrove
  lays a 61 m shadow with it, 93 m with the ground's fade.
- **Receivers on the crown top**: received on the ground under the crowns, 71-86% of two
  forest crops read shadow at every sun; on the top the forest edge keeps a mean light of
  0.61-0.78.
- **`CROWN_RIM`** (0.3), for `canopy_top`'s domes over a tree table: at 0.7 every crown edge
  was a cliff, at 0 the shadow share grew 3-4 points.
- **Sizes.** `gamedata/vegetation/trees.py` takes species from the foliage mesh, and top and
  radius from its `ExtendedBounds`, within 0.6 m of LOD 0 for all 53 tree meshes.
  `CROWN_TOP_MAX_M` (80 m) caps the column under a lifted crown; `CROWN_MIN_RADIUS_M`
  (0.75 m) drops trunks and bulbs (§36).

### Water (`palette/water/surface.py`, `open_sea.py`)

- **`WATER_EDGE_M`** (0.9 m): under this depth water and ground mix, or the 1 m grid draws
  every coast as blocks (§17).
- **`WATER_EDGE_BLUR_M`** (0.73 m): stops a cliff-side shoreline being a staircase without
  moving it; in metres, so the sheet's size does not change it (§17).
- **Level-only water** (level known, depth not) is the ocean, drawn over the open sea's bed;
  away from the ocean's level, and under `--kernel-only`, it keeps the deep end of the ramp
  (§19, §26).
- **`OPEN_SEA_DEPTH_M`** (60 m), **`OPEN_SEA_SETTLE_M`** (400 m): the bed the open sea settles
  to away from every constraint (§26).
- **`OPEN_SEA_BLEND_M`** (100 m), **`OPEN_SEA_BLEND_PULL_M`** (25 m): the measured bed pulls
  the membrane rather than fixing it, so the bed is smooth in slope and the hillshade draws
  no kink (§26).
- **`OPEN_SEA_TONE_DEPTH_M`** (5.3, 3.5, 1.3 m), **`OPEN_SEA_TONE_PULL_M`** (12 m): each of
  the artwork's lighter water tones sits on one depth band, and pulls the bedless open sea to
  it (§26).
- **`VOID_FALLOFF_M`** (50 m): the Gaussian that fits the artwork's darkening into a pit and
  past the world's edge (§26).
- **`VOID_STRIP_M`** (8 m): dry ground under the sea's level between the open sea and the void
  joins the sea; 12 m cut a spit (§26).
- **`LIP_DROP_M`** (8 m): a body is cut where the ground falls more than this, and the water
  below the drop is re-levelled first; measured, not derived (§38).
- **A field without `waterq.u8.z`** falls back to "a water surface stands above the ground",
  which reads the open ocean as dry, so the sidecar reports the missing byte (§19).

### Rivers (`gamedata/water/rivers.py`, `palette/water/rivers.py`)

- **The plane is the water**: a height and a half width per point; the depth is that height
  minus the drawn ground (§34).
- **`RIVER_LEVEL_MATCH_M`** (5 cm): a texel within this of a river box's top, with no other
  box as high, came from that box (§34).
- **`RIVER_MAX_DEPTH_M`** (8 m): past it a plane hangs over a pit, not its own channel (§34).
- **`RIVER_OVER_WATER_M`** (1 m), **`RIVER_STEP_M`** (0.5 m): a plane over a lake, or a jump
  between two planes, would draw water in the air or a line along the join (§34).
- **`shore.river`** in each palette: the least depth the optics see once in from the bank, or
  a shallow bed reads as a pale path (§34).

### Satellite colours (`palette/styles.py`)

- **`BIOME_COLOURS`**: chosen by eye against crops, desaturated and capped below about 220,
  not the asset's `mColorPalette` legend (§17).
- **`BIOME_BLEND_TEXELS`** (24, about 44 m): a tree line's width, narrow enough that a 300 m
  biome keeps its colour in the middle (§17).
- **`NO_MANS_LAND_RGB`**, **`UNKNOWN_BIOME_RGB`**: a neutral bleached ground for the outer
  coast and for an area a later build adds, so it looks unremarkable rather than wrong.
- **`RAMP_LO_PCT`**, **`RAMP_HI_PCT`**: percentiles, not min and max, or one 400 m spire
  flattens the ramp over the rest of the world.
- **The noise**: two fixed-seed fields sampled by world position, so no band edge shows (§17).

### The seam statistic (`terrain/measure.py`)

- **`SEAM_SWITCH_CEILING`** (1.0): what a hard switch reads. A convex blend cannot exceed it,
  so `share_of_a_hard_switch` is a description of the join, not a gate (§20).
- **`SEAM_MID`** (0.5), **`SEAM_PURE`** (0.02): where the counterfactual switch flips, and how
  close to 0 or 1 a weight is one regime alone (§20).
- **`SEAM_SAME_SURFACE_M`** (0.5 m), **`SEAM_NEAR_TEXELS`** (32, 7.3 m at z7): the recorded
  comparisons are kept to the ground beside the join, not the rest of a mostly ocean world
  (§20).
- **`SEAM_SAMPLE_MAX_PER_BAND`**: a systematic sample of each pool per band, so the whole sheet
  costs a bounded number of MB.

### The render sidecar (`tiles/sidecar.py`)

The four corner keys sit at the top exactly as `map.json`'s do, and `_meta.tiles` carries the
same block, so the endpoint reads a render layer with the code it has for the artwork.
`_meta.tiles_2x` is that block again for the denser tree; its absence means the layer has
none.

## Tests

The generator's tests live in the root `tests/mapgen/`; `tools/mapgen/tests` holds the
command table and the architecture checks. Both are in the root `testpaths`, so the default
`uv run pytest -q` runs them, and the root `pythonpath` setting finds the package before
`uv sync --all-packages` has run. They read fixtures only.

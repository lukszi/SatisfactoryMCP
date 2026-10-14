# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the scheme in
[docs/releasing.md](docs/releasing.md). History before 0.1.0 is not reconstructed.

## [Unreleased]

Planned as 0.2.0.

### Upgrade notes

- **BREAKING: do not downgrade after running this version.** Plans move from
  `plans/<world>.json` to a versioned op log under `plans/<world>/`. The migration runs once,
  on first access, leaves the old file in place untouched and first copies it to
  `plans/<world>/backup-v<version>/`. An older version keeps reading that old file, so it
  will not see plans created or changed after the upgrade, and anything it writes there is
  not carried back into the log.
  Do not run an older MCP server next to a newer web server.
- Rebuild the web page (`npm ci && npm run build`) or unzip the release's `static.zip`.
- `world_summary` reports `last_hard_drive_analysed` in place of `last_hard_drive_spent`: the
  save's counter names the drive analysed last, which can still be pending.
- The power ledger leaves out machines and generators on no wire, so `power_report` and the
  header can read lower draw than before; `/api/power/circuits` reports them under `off_grid`.
- Label writes answer a bad name with 400 and an unknown label with 404 (both were 409); a
  409 carries `stale`, `name_taken` and `pin` flags. Names with `/` or over 60 characters
  are refused for new labels.
- *Stage headroom* and *count biomass burners* are shared by the page and chat, in
  `settings.json` in the user data dir. A browser's own earlier value is adopted once. Chat's
  `biomass=` now defaults to that setting, and a new `settings` tool reads and writes it.
- Map generation needs `zstandard`, now in the `gen` extra: stop satisfactory-mcp, then run
  `uv sync --extra gen`. Raster caches kept by an earlier version are still reused;
  `python -m mapgen compress-cache <dir>` shrinks them about 20x.
- Re-run the paint layers (`python -m mapgen paint`, or *paint* in the Maps tab) before the
  next painted render: paint layers from an earlier version keep no daylight, and a painted
  map drawn from them keeps the screenshot colours. Paint layers from before generator 5 keep
  no landscape textures, and a Satellite map drawn from them has no ground detail.

### Added

- Map: the rendered map can be published as a public, map-only static site.
  `npm run build:share` builds a page with only the map, the map-style switch and the sun;
  `python -m mapgen share-export <out>` writes it with the tiles into build-tagged folders,
  checks every file for private data and writes the two manifests last. An Apache 2.4
  template with a strict Content-Security-Policy is in `docs/map/share-apache.conf`;
  docs/map/sharing.md has the steps. On the renders' own volume the tiles are hard-linked
  instead of copied (`--copy` copies them). `--tar <file>` or `--tar -` (stdout) writes the
  same layout as one uncompressed tar straight from the renders, tiles first and the
  manifests and `index.html` last, ready to pipe into `tar -x` on a server. The privacy scan
  reads the text files only (HTML, JS, CSS, JSON, text); tiles are never opened.
- Map generator: `python -m mapgen calibrate` derives the game-painted style's display colours
  from the game install: the level's noon light and its atmosphere volumes, the baked ground,
  the textures and the tree crowns, through a model of the game's camera. It writes
  `targets.derived.json` beside the paint layers; `--check` prints the colours against the
  screenshot targets and writes nothing. The paint layers keep the light and the volumes from
  now on.

- Plans are stored as an append-only op log with revisions, merge against the revision a
  writer saw, undo and restore. The MCP planning tools write through it and journal chat
  activity.
- Web: a planner tab with a versioned workbench that follows chat; routes for plan
  versions, merges, solves, focus and live plan events.
- Web: an inventory section (stock, containers, crates), a progress section (MAM, space
  elevator, hard drives, power shards, somersloops), a recipes codex with a header search
  box, and a supply-path trace drawn on the map for a machine or factory.
- Planner: a payback horizon per plan spreads rows over more, slower machines while the
  saved power, priced at the save's grid mix, repays them; an overclock-last switch builds a
  row one machine short. Both default from shared settings and are on `plan_factory` and
  `plan_layout`.
- Map generator: the render's raster caches are stored compressed, 0.93 GB instead of
  18.5 GB at full size, and read without inflating; the tiles are the same bytes.
  `python -m mapgen compress-cache` converts caches kept before.
- Web map: a map drawn with live light gets a *shade* checkbox under the base-map radios; off,
  the map shows its flat colour. The sun panel splits *shadows* into *terrain shadows* and
  *tree shadows* (the second only on a map that draws the trees) and adds a *hillshade only*
  button: the relief with no cast shadows and no sky light. All are kept in Settings → map.
  The light's controls show on every map that has a light, greyed with the reason where it is
  drawn baked, such as without WebGL2. Tree shadows with terrain shadows off miss the ones that
  fall inside terrain shade, until a later render stores them.
- Web map: a *trees* checkbox beside *shade* on the Satellite map; off, the tree crowns and the
  Titan trees go, with their shadows, and the ground under them shows. It needs a render drawn
  from this version on; on an older one the row shows greyed. With the trees on, a crown over
  the water now takes the live light.
- Map renders: the Satellite layer keeps its trees apart for the page: `unlit/` holds the
  ground without them and a new sparse `trees/` the crowns and Titan trees as lossless RGBA
  WebP. `tiles/` and `tiles@2x/` are unchanged. `/api/maptiles/{id}/{z}/{x}/{y}?kind=trees`
  serves them, answering 204 for a tile with no tree in it, and `X-Map-Light` names them under
  `parts`.
- Map generator: `python -m mapgen crown-sprites` builds a top-down sprite of every tree
  species, in colour, normal and alpha at 0.125 m, into `data/local/crown-sprites/`. Eight
  species take the game's own billboard view from above; the other 45, the Kapok and the
  yuccas among them, are rasterised from their meshes with their leaf and bark textures,
  normal maps, spherical normals and moss. `--gpu` runs the raster's per-sample work on the
  GPU, to the same bytes. The cache also holds the Titan canopy's two leaf meshes and their
  218 placements.
- Map renders: the Satellite map's ground at 8192 px and up shows the game's own landscape
  textures under the baked colour's metre: grass, gravel, sand, forest floor and the other
  paint layers at the repeats the game's shaders use from above, turned in cells as the game
  turns them and height-blended where layers meet, while the colour at every larger scale
  stays the game's bake. The live sun lights the same ground relief through the light's
  normal tiles, on every map with a light. `--gpu` draws it on the GPU, to the same bytes.
  The paint layers (generator 5) now keep the textures, so re-run them before the next render;
  the light's scratch takes 2.1 GB more at full size.

### Changed

- Map renders: the Satellite map's rivers are drawn with the game's own river absorption,
  read from the install's river material, under the sea's sky colour. Shallow rivers show
  their bed under a grey veil; pools and deep runs turn slate blue to the sea's blue, where
  every river was teal. The waterline of a river no longer takes the inland water's opacity
  floor. The style's version goes up, and a new river absorption reader joins the inputs.
- Map renders: on the Satellite map the Blue Crater's kind of lake (translucent water) shows
  its depth: its bed in the shallows, darker and more teal with depth, down to the other
  lakes' deep colour. It had one colour at every depth, so the lakes in the Savanna and the
  Western Dune Forest read flat; the Blue Crater keeps its colour. A lake standing below
  the sea within the sea's shore reach, the basin on the west coast near (-1420, 2325), is
  now as deep as its own water instead of measured from the sea's level. The style's version
  goes up.
- Map renders: dry hollows under the sea's level beside the coast, around the west-coast
  basin and along the west and north edges, are drawn as the ground and rock they are; within
  48 m of the sea they were drawn as deep sea. The sea and the coast do not move.
- Map generator: the light's coarser zoom levels are built while the draw runs, each strip
  as soon as the block rows under it are baked, instead of all after the last row, which
  took about 4 minutes at full size. A full-size render should end about 3 minutes sooner,
  with the same tiles to the byte. Building them holds about 0.3 GB at a time instead of up
  to about 2.5 GB at the end of the run.
- Map renders: the Satellite map's tree crowns are lit a fifth brighter where their shape is
  drawn, which had darkened every crown by about that much on the whole: species keep their
  measured colours on average, and lobes and dips keep their contrast. The style's version
  goes up.
- Map generator: `python -m mapgen calibrate` measures tree crowns under the light leaves get
  seen from above, mostly the sky in their own crown's shade, instead of flat ground's: the
  derived crown colours turn bluer at the same lightness (the canopy 13° off its 1.0 target
  in hue before, 6° now; the red Kapok 28° before, 21° now). Ground colours are unchanged.
  Re-run `python -m mapgen calibrate`, or let the next render derive.
- Map renders: the Satellite map's orange palms take that light too, pale grey-mint instead of
  pale sage, a small step (about 2 ΔE). The style's version goes up.
- Map renders: the Satellite map's tree crowns and the Titan canopy read more three-dimensional
  again: each crown is shaded by its own shape as well as its leaves, sunward lobes lighter and
  the far sides and dips between crowns darker, set by the style's `crowns.relief`. The
  style's version goes up.
- Map renders: under the Satellite map's sea, the seabed is seen at the scale the game's ocean
  material puts on what is behind its surface, so clear shallows read turquoise over sand
  instead of grey-green. The crater-grass patches on the seabed are dark navy, as the game
  shows them, where they were light blue blotches; sunk coral and barnacles keep their own
  colours under the water's tint; the Blue Crater's coral roots, coral root, net fungi, coral
  cactus, crater bush and crater tree each wear their own texture colour. The style's
  version goes up, and so do the render mesh and ocean readers: the mesh cache is rebuilt.
- Map renders: the Satellite map's sea is drawn with the game's own ocean absorption, read
  from the install's ocean material and scaled by the factor the sky fit gives, over its bed,
  and the fitted sky colour at depth. Shallow lagoons turn blue where they were teal, and
  sand shows through the first half metre. Lakes and rivers keep their colours. The style's
  version goes up.
- Map renders: on the Satellite map every tree crown is drawn from its crown sprite, leaves,
  holes and branches in their own colour, lit by their own normals instead of a smooth dome;
  the ancient pines draw olive instead of mustard. A painted render builds the sprite cache
  first where it finds none for the installed build (`--sprites-dir`, default
  `data/local/crown-sprites/`). The crowns are stamped on the GPU with `--gpu`, to the same
  bytes.
- Map renders: the Titan forest's canopy is opaque and drawn from its own leaves' sprites,
  textured and lit by their normals, instead of a see-through, faceted green; the trunks stay
  as before.
- Map renders: the unlit colour of a map drawn with live light (`unlit/`) is lossless WebP
  instead of PNG, about a quarter smaller. Renders made before keep their PNG tiles and are
  served as they are.
- Map names: the game-painted map is now called "Satellite" and the dark relief "Relief".
  Their ids, links and the saved default are unchanged.
- Map renders: a render that names no layers draws terrain and Satellite, and the Maps tab
  ticks both. Without paint layers the Maps tab builds them first.
- Map renders: the game-painted map takes the colours derived from the game install for
  the targets within reach of their screenshots in lightness, chroma and hue (sand, grass, the
  canopy, the Grass Fields rock, the desert rock, the forest moss, the desert gravel, the Red
  Jungle cliffs), for the forest floor, and for the ground layers no screenshot covers (red
  grass, puddles, the Red Jungle ground, sand cracks, pebbles and rock, soil). A derived colour
  that drifts from its screenshot in chroma or hue keeps the screenshot, and the render's
  sidecar says why. A render derives the colours itself when `targets.derived.json` is missing
  or from other data. This shares the one version up every rendered map style takes (under
  "Fixed").
- Map renders: the Satellite map's rock looks rendered. Cliffs, the rocks drawn only by the
  renderer and the landscape's Cliff layer wear the cliff material's own sediment texture,
  laid on the ground at its 20 m with the landscape's rotated cells against tiling, and its
  normal maps' relief; arches and boulders are a lighter warm grey of their own in the
  arches' rock texture; desert rock wears its own rough texture and stays terracotta. A
  cliff's moss, grass or sand lies where the cliff material's own slope mask puts it, in its
  own texture, instead of in noise patches. The textures are read from the game install at
  each render, and `--gpu` reads them on the device to the same bytes.
- Map renders: the Satellite map's desert rock no longer repeats in a visible lattice over
  whole mesas. Its texture is turned in the landscape's cells, as the arches' and the shells'
  are, standing in for the meshes' own UV layouts, and repeats at the game's 17 m, the size
  of the desert meshes seen most from above, instead of 12.5 m. The big grey shells on the
  coasts, which drew as flat colour, wear their own shell texture and relief. The style's
  version goes up.
- Map renders: the Satellite map's sea is turquoise over the shallows, steel blue in the
  lagoons and navy in the deep, fitted to 1.0 screenshots of the sea where it was teal at
  every depth; the seabed coral carpet keeps its colour. Coral caps are a warm grey-pink, as
  official footage shows them from above, where they were mauve. Wet sand keeps more of the
  sand's colour (chroma x1.15). Ground drawn without the game's baked colour, as on the Grass
  Fields' southern slopes, takes each area's step to the baked colour instead of a fixed
  biome hue, so it no longer turns greener at the bake's edge. The style's version goes up.
- Map renders: the Satellite map's colours checked against the game's own baked view once the
  look draws its textures and crown sprites. The green tree crowns take the 1.0 canopy
  screenshot again, a little bluer and less saturated, and the ancient pines lose most of
  their yellow. The Grass Fields' grass is the game's own green instead of a pale yellow-green,
  the Southern Forest's grass-topped cliffs wear its bluer grass, the Dune Desert's arches and
  rib bones are terracotta again, and the red jungle cliffs show their red tops. Palettes can
  now give an area's arches a colour of their own (`calibration.areas[].arches`) and a rock
  family a top the game install does not name (`calibration.tops`). The style's version goes
  up.
- Map renders bake the live-sun lighting by default, from `python -m mapgen renders` and from
  the Maps tab alike, so a new map can be relit for any sun. `--no-light`, or unticking
  "live sun", draws the hillshade into the colour as before. `--unlit`, the old opt-in, is
  still accepted. With the light a full-size render is budgeted at about 5 minutes more and
  needs 15.6 GB more scratch space.
- Map generator: the light's scratch, `light.cache/`, is 5.4 GB smaller at full size with
  the painted layer, because the tree crowns are written once, where the bake reads them.
  `--scratch-dir` moves it off the cache drive. It is still not compressed: nothing reads it
  after the run that wrote it.
- `pioneersav`'s submodules re-export less; the top-level `pioneersav` API is unchanged.
  `pioneersav.properties` no longer exposes `ObjectReference`, `ObjectSlice`, `ParseError`,
  `Reader`, `FIRST_MODERN_BODY` or `TAG_EXTENSIONS`; `objects` no longer lists `ParseError`,
  `save` no longer lists `PLAIN_TRAILER`, and `lightweight` and `trailers` no longer re-export
  `ObjectReference`. Import each from `pioneersav` itself or from its defining module
  (`references`, `objects`, `errors`, `reader`, `versions`, `properties.tags`,
  `properties.payload`).
- Map generator: drawing a layer skips arithmetic whose answer it already had. Planes are
  sampled without the weight sums nothing read, and the void and the water are blended only
  on the pixels they cover. A full-size render draws about 12% faster, 10 to 15 minutes of
  one core over the five layers; the tiles are the same bytes.
- Map generator: a layer is drawn on several threads, 8 by default and fewer when free memory
  is short, so the draw stage of a full-size render takes about a third as long, about an
  hour less; the tiles are the same bytes. It needs about 2 GB of memory a thread at full
  size, 3.4 GB for the painted layer. `--draw-threads 1` draws one band at a time as before.
  The render's `meta.json` records the count as `draw_threads`.
- Map generator: the light bake is about 2.7 times faster at full size on 8 workers (about
  18 minutes instead of 48) and 3.5 times on 16, with the same bytes. Each of its processes
  peaks at 1.1 GB instead of 3.9 GB, and starts numpy with one BLAS thread, which saves
  about 1.5 GB of commit apiece. It now takes up to 16 by default, as many as the free memory
  holds; the new `--light-workers` sets the number.
- Map generator: a layer's three tile trees (`unlit/`, `tiles/`, `tiles@2x/`) are cut through
  one pool of encoders, with the levels resampled on threads while it encodes. A lit layer
  at full size cuts in about a third of the time; the tiles are the same bytes. The new
  `--cut-workers` sets the encoders (default one per core, at most 24, fewer when memory is
  short). `--workers N` still sets both pools wherever their own flag is not given.
- Map generator: tile PNGs are deflated at zlib level 6 instead of `optimize=True`, about a
  seventh of the CPU for about 7% more bytes, and the light's lossless normal tiles at WebP
  effort 2 instead of 4, 2.6 times faster for about 6% more. The tiles decode to the same
  pixels; their files are not the same bytes as before.
- Map generator: a render draws all its layers in one pass over the bands. Each band's
  heights, rocks, water and meshes are composed once and every layer's style colours them,
  where each layer composed them again before; drawing all five layers takes about a third
  less time (on 8 threads, windows of the full-size sheet: 140 s against 214 s), and the
  tiles are the same bytes. A band in flight takes 0.6 GB more memory when the painted layer
  is drawn with another, and a job's progress shows one draw stage for all the layers.
- Map generator: the painted and relief styles work out the colour under the water only on
  the pixels that hold water, where they worked it out for every pixel and kept it on those.
  On bands a fifth to a half wet that part costs a third to two thirds of what it did, and
  the three styles' painters 10 to 17% less CPU; a full-size draw on 8 threads, which waits
  on memory, is no faster by the clock. The tiles are the same bytes.
- Map generator: the light's horizon march and sky view, and the sampler's resampling, run
  as loops compiled by numba, now in the `gen` extra (`uv sync --extra gen`). A full-size
  light block's horizons and sky view take about 8 s instead of 91 s, and the five layers'
  full-size draw about 22% less, about 7 minutes on 8 threads. The tiles are the same bytes.
  Without numba, or with `MAPGEN_KERNELS=numpy`, the generator runs the numpy code as before.
  The first run compiles the loops, about 3 s, and keeps them beside the code.
- Map generator: the tree crowns and the water of every style (the terrain and satellite
  water, the relief styles' and the painted style's colour under the water) are drawn by
  numba-compiled loops too. Those painters take a quarter of the time they did, and a
  full-size draw about a fifth less on one thread; the tiles are the same bytes. The first
  run compiles them, about 3 s more. Each compiled signature is now kept in a file of its
  own, so processes compiling at once, such as the test suite's workers, no longer leave a
  cache that hands one signature another's code.
- The Maps tab's render estimate follows a timed full render: at full size with the light,
  the three layers are budgeted at about 28 minutes and the default two at about 24, where a
  cold render of the three took 27.5 minutes. The paint's preparation, about 2.5 minutes
  whenever the painted layer is drawn, is counted now. Its disk check counts the raster
  caches at 2.2 GB and the light's tiles at 4.1 GB, as a full render writes them.
- Map generator: a lit render that keeps its raster cache keeps its finished light beside it
  (`light.kept/`: the pyramid's tiles as hard links, and the default-sun terms, 4.3 GB at full
  size). A palette-only restyle that draws the same surface installs that light instead of
  baking it again, and the Maps tab budgets it so. The Maps tab's estimate counts those terms
  in what a job keeps. The light's `meta.json` records the `key` it was baked under; the
  tiles are the same bytes.
- Map generator: each band is drawn in pieces of 512 columns, several pieces at once on the
  draw's threads (`--draw-columns` sets the width). On 8 threads the draw's peak memory falls
  from about 15 GB to about 2 GB, so free memory no longer cuts the thread count, and the five
  layers draw in about 30% less time over the densest water of the full-size sheet. The tiles
  are the same bytes at any piece width (with the fixed-order sums below).
- Map generator: each band goes on to its layers' tiles as soon as it is drawn, and the light
  bakes a row of blocks as soon as the surface holds the rows it reads, while the draw goes
  on. No layer's sheet is held whole, in memory or in a file: a full-size run of five layers
  needs 16 GB less scratch, and the Maps tab's disk check no longer counts it. The tiles and
  the light are the same bytes. A light kept before this is baked again once.
- Map generator: `--gpu` runs the light's horizon march and sky view as CUDA kernels. It
  needs the new `gpu` extra (CuPy and its NVRTC) and an NVIDIA driver, and a run where they
  cannot work is refused at once with exit code 12, its own, and the reason on stdout. The
  CPU path stays the default, and the tiles are the same bytes either way; a full-size light
  block's ground horizons take about 3 s instead of 8. The bake logs how many of its calls ran
  on the GPU and how many fell back to the CPU for want of device memory.
- Map generator: with `--gpu` the light's span marches (arches, rock overhangs and the tree
  crowns) and the rules after each march run on the GPU too, a block's planes kept there
  from its first direction to its last, and a block's tiles encode on threads. A 16384 light
  block takes about 18 s instead of 196 s, and a 16384 render's light about 212 s instead of
  401 s (8192: 54 s instead of 130 s); the tiles are the same bytes. At most three blocks
  hold the device at once, and a light process's CUDA context takes 0.10 GB instead of 0.19.
  The CUDA kernels no longer flush subnormal numbers to zero, which CuPy's compile did
  whatever the options said; no map moves.
- Map generator: `--gpu` also runs the draw's relight by the default sun, the arches' FXAA
  and the terrain layer's pieces as CUDA kernels, to the same bytes. At 16384 the relight's
  188 CPU seconds become 6 and the FXAA's 70 none, and the draw takes 172 s instead of 186;
  the run logs where the draw's calls ran. For the painted style's coming look, textures,
  atlas tiles and sprites can now be read and stamped on the device too, to the CPU's bytes.
- Map generator: a render whose light scratch another running render holds is refused with
  exit code 11 and the reason on stdout (it was exit 1 on stderr). The render sidecar's
  `cliff_geometry.placements_dropped` counts the passable `CliffPillar_03` as `excluded_mesh`
  (376 placements) instead of under `no_geometry`.
- Map generator: a pixel's colour channels are summed in one fixed order, elementwise,
  instead of by BLAS: the luminance under the painted style's tone shoulder, OKLab both ways,
  the water classes' mouth blends, the crown and layer colour transfers and the artwork's
  luma; and the rock tops' and coral specks' 3 × 3 mean no longer keeps a running sum. A map
  is now the same bytes at any width, in any column pieces and on any number of threads, and
  its colours no longer depend on the BLAS library. Once, about 0.01% of a full-size painted
  map moves by a level or two, and fewer pixels of the other styles; a 2048 map moves about 500
  painted pixels in each tile tree, a few dozen relief and about ten terrain and satellite.
  This shares the one version up every rendered map style takes for the fixes below.
- Map generator: the open sea's bed is solved by conjugate gradients whose dot products are
  summed in a fixed order, where scipy's took them from BLAS and its bits followed the
  number of BLAS threads. The maps are the same bytes, and the solve takes about 2 s less.
- Map generator (light model 3): arches, rock overhangs
  and tree crowns cast their shadow where the sun's ray meets them, with light passing
  beneath, instead of a wall from their foot, a wedge or a streak from the trunk; the sky
  beside an arch is no longer dimmed as beside a wall. The arches' sub-metre holes are
  filled and their edges antialiased, and nothing else is. The page's live light shades the
  new shadows for a sun on the game's own path. The direct and top raster caches gain planes
  and are rebuilt once, the direct raster taking about 2.4 times as long; a kept light is
  baked again. The light takes two to three times as long under crowns and arches, and each
  of its processes counts 2.0 GB instead of 1.5.
- Map generator (light version 6): the tree canopy is porous. Crowns and the Titan canopy
  let light through by their cover, cast from the crown sprites at full resolution, so canopy
  shade is dappled instead of a solid footprint swept by the sun: the straight diagonal wedges
  in the Titan Forest go, at the default sun and on the page. On a page the trees are lit by
  their own horizon on the canopy top, not the ground's beneath it. The horizon atlas keeps
  97 cells; cells 64 to 95 now hold the ground's horizon on the canopy (`canopy_cell` in the
  light model, replacing `titan_cell`), and an older pyramid still draws. The species'
  undersides and their 33 s per run are gone. Every painted lit map moves, the other styles
  keep their bytes; a kept light is baked again, and the horizon tiles grow by about a sixth.
- Map generator (light version 8): the shade under arches and rock overhangs keeps the game
  path's shade at the sun's azimuth when the sun leaves the path (the free compass, the "map
  NW" preset, the slider's last half hour each end) instead of flipping to none or all of
  it; terrain shadows stay read at the sun's own elevation. The bands get horizon cells of
  their own, so the horizon tiles grow to 129 cells; an older pyramid still draws as before.
  Lit maps move under and beside arches and overhangs; a kept light is baked again.
- Map generator (light version 7): tree shade is smooth between the light's half-resolution
  cells instead of a patch with a hard rim per cell, and a sun off the game's path (the free
  compass, the slider's last half hour each end) keeps the path's tree shade at its azimuth
  instead of none or all of it. Painted lit maps move under and beside the trees; a kept
  light is baked again, and the native horizon tiles of a 2048 render shrink by about a
  quarter.
- Map generator (light version 5): a tree crown casts from where its species' leaves start,
  read off its mesh at the start of a render (a green tree or a Kapok three quarters up, a
  bush from the ground), instead of halfway up every crown, and the Titan trees as a 12 m slab
  under their canopy instead of half their height. The crowns and the Titan trees cast into
  horizon cells of their own, each without the terrain and stored everywhere, so a page can
  show tree shadows without terrain shadows and hide the trees' shadows with the trees; the
  horizon atlas grows from 64 cells to 97 in 13 rows, placed by the light model's new
  `titan_cell` and `ao_cell`. Rocks, cliff feet and gullies take ambient occlusion in the sky
  light every style reads, and the trees' occlusion is a cell of its own; arches and
  overhangs occlude nothing beneath them. Every lit map moves (at 2048, 1.0 to 1.2 million lit
  pixels a layer, the unlit colour none), and a kept light is baked again. The light's scratch
  grows by 3.1 bytes a pixel, 3.3 GB at full size, and a lit render spends about 33 s more
  before it draws. With `--gpu` the occlusion runs on the device, to the same bytes.

### Deprecated

- The map generator scripts `tools/gen_map_renders.py`, `gen_map_image.py`,
  `gen_world_heightmap.py`, `gen_paint_layers.py` and `check_map_fill.py` are now shims for
  `python -m mapgen <command>` and warn when run. They will be removed in 0.3.0.

### Removed

- Map renders: the biome-coloured satellite style and the light relief are no longer drawn.
  `python -m mapgen renders --layer` and the Maps tab offer terrain, painted and relief-dark,
  and a job naming either old layer is refused. Maps already drawn in them stay listed and
  served, as "Biome (old)" and "Relief light (old)", until deleted. The other layers' pixels
  are unchanged.

### Fixed

- Map renders: the Satellite map's desert boulders are terracotta wherever they stand, as
  their own desert material and the game's distant view have them: those on the Spire Coast,
  in the Rocky Desert, the Desert Canyons and the Swamp drew grey. The other shells take the
  warmer grey-brown of the game's distant view. The style's version goes up, and the rock
  raster caches are rebuilt once.
- Map renders: the Satellite map's small shells on the Desert Canyons' cliffs are terracotta,
  as their own desert material and the game's distant view have them, not grey-blue plates on
  the red rock. The style's version goes up, and the render-only mesh cache is rebuilt once.
- Map renders: the straight line across the ground at the south end of the Grass Fields
  (y 3048 m) is gone. The heightmap cut every rock at the edge of the landscape and filled
  the rest from the game's coarse height raster; rocks now go on past that edge where they
  stand at the coarse surface or above it. Re-run `python -m mapgen heightmap` (generator 7);
  the next render rebuilds its rock caches once.
- Map renders: the Satellite map's ground colours hold their targets where the ground is
  drawn. Each paint layer's colour is measured on the ground the map shows, not under rocks,
  and set for the height it lies at, so low ground (the Blue Crater's floor, the beaches' wet
  sand) is no longer drawn darker than its target, nor high ground (the red jungle's
  plateaus) lighter. The Blue Crater's floor gains about 2.5 L. The style's version goes up.
- Map renders: the Satellite map's orange palms, the small shrubs among the Southern Forest's
  purple trees, are pale sage, their leaves' colour in the game's sun, instead of mint. A tree
  species with a colour of its own in the palette now keeps that colour: the green canopy's
  adjustment no longer moves it on. The style's version goes up.
- Map renders: the Satellite map's red jungle and red-grass cliffs lay their grass and moss
  tops by each cliff's own material settings, as the game does, instead of their family's
  alone: about a quarter of those cliffs keep the top off the steeper rims of their shelves.
  The rock raster cache is rebuilt once (it gains a plane), and the style's version goes up.
- Map renders: the Satellite map's boulders in the Dune Desert take their own colour from the
  game's baked distant view, a lighter sand-terracotta, instead of the colour once measured on
  the desert's arches. The style's version goes up.
- Map renders: the swamp's waterlines are drawn at the swamp's own water level, 0.3 m over
  the sea's, so its islands take the outline the game's map gives them and the grey blocks
  of foam on flat island ground are gone.
- Map renders: the canyon lake's arm beside the arch at (2040, -1880) is water again, not a
  flat sand box with straight sides. Water a waterfall pours from stands at least at the
  fall's lip; before, where the lake's box ran on over the cliff, the lake was laid on its
  own bed. Twelve falls hold water up this way.
- Map renders: where the ground drops sharply into a hole, as round the Blue Crater's coral
  disc, the drop follows a smooth outline instead of the 1 m grid's stair steps. Every
  style's version goes up.
- Map renders: the near-black swamp water no longer spills onto the sea between the abyss
  rocks: sea-level water outside the swamp is the sea's.
- Map renders: the flat taupe blocks with stepped outlines along the east desert coast are
  sea. Where the coarse fill past the landscape holds the sea's surface it is drawn as sea,
  its shore is smoothed, and no beach is invented under its cliffs.
- Map renders: lakes and rivers no longer end on the stair-stepped blocks of the game map's
  water mask. Their water reaches up to 3 m past it, to where its surface meets the ground,
  and the mask's outline is smoothed. A river meeting a patch of lake water covers what the
  lake leaves of each pixel, so no outline of ground is drawn round the patch, and a river's
  surface on a steep bend no longer breaks into a checkerboard of small steps. Every style's
  version goes up.
- Map renders: the Satellite map's pink-brown barnacle boulders along the beaches wear their
  own barnacle texture and relief instead of flat colour, as the other rocks and the shells
  do. Their colour stays the coral's. The style's version goes up, and the render-only mesh
  cache is rebuilt once.
- Map renders: the Satellite map's ground turns its texture cells by the scale the game's
  landscape shader reads from the cell noise's green, not its red.
- Maps tab: a render's estimate counts the ground detail's 2.1 GB of scratch from 8192 px,
  where the render starts drawing it, instead of from 16384.
- Map renders: the game-painted map's rock outside the deserts is grey again, not the tan of
  the dirt paths. Its derived colour now comes from the texture the cliff material samples,
  untinted, as the game's own baked distant view of the cliffs has it; it came from two
  textures the cliffs never use, times a tint that view does not show. The Desert Canyons' and
  the Rocky Desert's cliffs are grey with sand tops too, as that view has them; the desert rock
  of the Dune Desert and the desert mesas stays terracotta. Wet sand and the coral caps keep
  their screenshot colours, as their derived ones drift in chroma and hue. Re-run
  `python -m mapgen calibrate`, or let the next render derive.
- Chat saves merge against their base revision and across renames.
- A recalled plan's pinned logistics are journalled with its solve.
- The page resyncs plans and chat activity after a lost or overflowed event stream, and
  pushes writes against the revision the user saw.
- Trace: a factory trace is seeded by label, so a building name cannot win; flows into
  alternate-recipe groups stay on the trace card.
- Search drops locked recipes on the server when spoilers are off.
- Progress hides capability research in an unopened MAM tree.
- Recipe icons are probed once instead of one 404 per item.
- The world power ledger is the sum of its circuits; unwired machines no longer eat headroom.
- Standing Biomass Burners count their 30 MW; generators on no wire have their own list.
- Fluid buffers count towards stock, so the piles add up to the containers holding them.
- Building and hand-craft recipes show amounts per build, not a per-minute figure.
- Chat and the planner quote one net power figure; a recalled plan keeps its objective.
- An old save with no phase pointer no longer marks every Space Elevator phase complete.
- Retired MAM nodes are not listed or counted; item and building ids read as names.
- The Overview action list shows only machines needing action, counted across factories.
- The map and chat name an unnamed cluster the way Detect does, not by its building class.
- Detect numbers its suggestions like the map when filters hide clusters, and a guessed
  name prefers a made item to an ore. A name written after a rename in the same tab no
  longer fails as stale; a world switch mid-detect shows the new world's clusters only.
- Factories: the need-action list leaves paused machines out, name and rename errors show
  under the field, and an old deep link after a rename shows one back link.
- Map clicks work again after a trace is closed; trace run tooltips are escaped.
- Leaving a factory rename unfinished no longer freezes the dashboard or side panel.
- A malformed `%` escape in the address no longer stops the page from loading.
- Power circuit names follow a factory rename without a reload.
- A label holding `/` can be renamed or forgotten; a rename writes the label before
  repointing plans and reports any plan it could not move.
- Files written by a newer version are refused, not overwritten; a plan exporting power
  under any spelling stores it as `MW`.
- `npm run typegen` takes the server port as an argument or from `SATISFACTORY_WEB_PORT`.
- Stopping the map-job runner or the save watcher no longer swallows a cancellation of the
  code that is stopping them.
- `python -m mapgen renders` no longer overwrites a map the registry lists: a run into its
  folder, through a junction or link too, is refused and names it. `--renders-name` writes
  beside it, and `--overwrite-in-use` replaces it anyway.
- Map generator: a mesh or Titan raster that does not read back after it was written stops
  the render with exit code 7, where it was dropped while the sidecar still recorded it. A
  cache the run cannot delete is named. `compress-cache` without the `gen` extra prints the
  fix instead of a traceback.
- The Maps tab's render estimate follows the "live sun" box, counts the light cache's
  scratch space against the free disk, and times a lit render only from an earlier lit one.
- The Maps tab counts a lit job's kept light: its terms, 4.3 GB at full size, among what the
  job keeps, and its tiles, hard links to the map's own, no longer in the cache's size or in
  what clearing the cache frees.
- A render that fails deletes its light scratch too, and the next lit run removes what a
  killed run left. On Windows, a run that would share the scratch of a render already
  drawing is refused at the start, where it used to fail after the slow preparation.
- `python -m mapgen compress-cache --to` on the source cache or its folder deleted the raw
  planes and then reported the cache "left as it was". A `--to` that is the source, holds it
  or lies inside it, through a junction or link too, is now refused before anything is
  written. Raw planes left by an interrupted run are removed only once they match their
  bands, and the report says whether a cache was converted in place or copied.
- Full-size (32768) map renders no longer draw a faint line along water edges every 256
  rows, in every layer and in the live-sun light: each band of rows is now drawn 16 rows
  past its edges instead of 8, enough for the water edge's blur. Smaller sizes are unchanged.
- Tree crowns on the game-painted map are placed from each pixel's own centre, so they no
  longer shift with where a band of rows starts. A few hundred crown pixels of a 2048 map,
  and 0.02% of a full-size one, change once where a crown's edge decides whether it shows.
- A lit render's light no longer depends on which layers it draws or in which order. Drawn
  first, the painted layer put its sea meshes into the light every layer is relit with, and
  a run without the painted layer baked the light without tree shadows. A full render of
  every layer is unchanged.
- Map generator: a triangle wider than 256 texels of a raster was dropped, which left two flat
  cliff tops open in a full-size render. It is rasterised now. The heightfield generator is
  version 6 though the field itself is unchanged, so the next render rebuilds its rock caches.
- Map generator: `CliffPillar_03` was read from the game's unused `Mesh_Old` copy, another
  shape, and 18 of its 376 placements lost their sand family. Asset paths now match a folder
  by whole names.
- A render that read the waterfalls from the game drew overlapping ones in another order than
  a palette-only restyle, which reads them from the cache: 22 satellite and painted pixels of
  a full-size map, and 4 at 8192 and 16384, came out one level apart, and the sidecars
  recorded two digests for the same falls. Both now draw them in the cache's order.
- Map renders take the sun term from one float32 copy, the live-sun page's own, in place of
  several copies in mixed precision. A few dozen lit pixels of a 2048 map move by one level.
- The live-sun light no longer stands a wall at 0 m over the void: where a map has no
  ground, nothing blocks the sun or the sky, so the rims of pits, the chasm and the southern
  and eastern coasts are no longer shaded, and no slope runs down into a hole. The light of a
  full-size map no longer steps along the 4096-pixel grid it is baked in.
- On the game-painted map's baked light, tree crowns and the Titan forest are lit by their own
  top, sky and shadows instead of the ground's beneath them, so a ravine under the Titan
  forest no longer shows through its canopy; the Titan trees cast tree shadows like the other
  crowns. The live-sun page still lights the canopy by the ground until it gets a canopy
  tile.
- The live-sun page draws the shadows of arches, overhangs and tree crowns as the baked map
  does: no longer blotchy and blocky, without the seams along tile edges on cliffs and canopy,
  and a thin shadow no longer breaks into dashes when zoomed out. Horizon tiles that hold such
  a shadow are stored at WebP quality 95 and the rest at 90 (75 before), each cell framed by
  its own edge; a coarser level averages the shade the page draws instead of the horizon. A
  hole of no data just past one of the light's 4096-pixel blocks no longer lights a staircase
  wedge beside it. The light is baked again once, and a page needs this version's build to
  read the new horizon tiles. A full-size map's light grows from about 2.4 to 4.1 GB.
- Map water: two water boxes meeting inside one sheet of water no longer draw a straight line
  where their tops differ by up to a metre; the level is feathered over about 12 m. A river
  hands over to a lake or the sea along a ramp, is drawn across the joints between its
  sections, and fades out where it ends in other water, instead of drawing panels and square
  ends. Waterfall foam fades out at the ends of its lip, and on the game-painted map goes
  under the crowns and the Titan canopy. On the game-painted map, pools a few decimetres
  deep are drawn as water, a hot-spring terrace tints only the water around it instead of
  its whole lake, dry patches inside the swamp no longer draw as teal sea, and the swamp's
  dark water stays off the sea past the landscape's edge.
- Lit map renders draw the edge of the void, a pit's rim and a coast past the world's edge as
  the smooth curve the unlit colour has. The light took the void's soft edge and rim as land
  up to the field's last 1 m texel, so it drew a staircase with a light or dark rim there, and
  a lone texel with data inside the void as a dark square. The live-sun light pyramid's land
  weight changes the same way.
- Map renders no longer draw a step where the ground under the rocks stops: the rock colour
  and the heights switched at its last 1 m texel, a staircase beside a landscape hole and a
  line hundreds of metres long along the landscape's east and south edges. They now blend
  over a few metres.
- Game-painted map colours: blue palms under a sparse tree crown no longer draw pale grey;
  the edges of the Red Jungle's and Red Bamboo Fields' ground layers lose their fire-red rims
  and colour confetti, and forest floor its orange halo; sand and grass on rock tops take
  their ground's colour instead of near white; hot-spring terraces are cream, not white;
  arches no longer wear the moss or sand of the cliff below; and wet sand under a crude oil
  puddle keeps the colour of the wet sand around it.
- Map renders leave out the land the game's height data has past the world's rim, where the
  game's own map draws nothing, and draw the void there: a 0.23 km² island south-east of the
  abyss, a lobe on the east edge and smaller pieces. The open sea beside them moves too, by
  a level at most a kilometre or more away.
- The satellite map no longer shows a quilt of 29 m and 7.3 m squares on flat ground: its
  noise is read smoothly between its cells.
- Map renders keep or drop coral, shells and hot-spring terraces a whole footprint at a time.
  The terrain and relief maps cut them along the game map's coarse waterline, a wall through
  a reef, and kept pieces of reefs standing in the sea where that waterline has a dry patch
  under the sea's level. A footprint that stands on land is now drawn whole, and
  one wholly in the sea is left whole to the seabed; the live-sun light and the game-painted
  map's unlit sun follow it. About 36,000 square metres of a map move: 346 reefs are kept
  whole across the waterline, 1,233 are left whole in the sea, and 30 of the 37 terraces are
  kept whole across their lake's edge. `--gpu` works the footprints out on the GPU too, with
  the same bits. This shares the one version up every rendered map style takes.
- Map generator: a texture that is not square is read at its own aspect. The paint layers read
  the mangrove leaves, the Dypsis palms and the bamboo bark as garbled squares, so those
  crowns' mean colours change at the next `python -m mapgen paint`.
- Every rendered map style is one version up for the map changes of this release, once:
  terrain and satellite 9, game-painted 20, relief and relief dark 7. The live-sun light is
  model 3, so a map baked under model 2 is offered a relight.

## [0.1.0] - 2026-09-27

First tagged version. It records the state of the project at that point rather than a
change set.

### Added

- An MCP server (`satisfactory-mcp`, stdio) with 49 tools, 3 prompts and resources covering
  game data, the player's world read from local saves, stock and storage, factory detection
  and health, map queries, factory planning with an LP optimizer, and hard-drive advice.
- A local web map (`satisfactory-mcp-web`, optional `web` extra): terrain, factories, belts,
  pipes, power wiring, floors and crates, following the saves live.
- `pioneersav`, a first-party save parser run behind a subprocess boundary.
- Factory names and plans kept in the platform's user data directory.

### Fixed

- Save parsing on the anniversary build: the archive version header, the lightweight
  record's trailing count, `InventoryItem` layouts and a fourth conveyor-chain variant
  (thanks @ledairain, #1 and #2).

[Unreleased]: https://github.com/lukszi/SatisfactoryMCP/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lukszi/SatisfactoryMCP/releases/tag/v0.1.0

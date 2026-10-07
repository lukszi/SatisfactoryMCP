# The drawn renders: layers, sampler, recipes and caches

Sections 17, 20, 25, 26, 39 and 40 of the [design spec](../../DESIGN.md): the drawn base layers, how
they sample the field and the rocks, the recipes they draw by, their raster caches, and
how a layer's bands are drawn. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

Numbers 19 to 24 also name sections of parked.md, residency.md and plumbing.md; in these
files they are the map's ([the document set](../../DESIGN.md#the-document-set)).

## 17. Two more base layers, drawn rather than found (2026-07-31)

The page has had one base map since there was a base map: the game's own artwork, cut out of
the reader's install by `tools/gen_map_image.py`. The 1 m heightfield made a second kind
possible and the game turned out to ship the ingredient for a third, so there are now three,
and `/api/maptiles/{layer}/{z}/{x}/{y}` is how a client asks for one.

**`terrain`** is the hypsometric preview the heightmap workflow produced, at full
resolution and unretuned: a green→olive→tan→rock→snow ramp over the 1st..99.5th height
percentile, a north-west hillshade at 45°, water tinted by its own depth, and the page's own
`--sea` where the field knows nothing. It is a cartographer's map — the colour is the height
and nothing else.

**`satellite`** is the same relief with the colour coming from somewhere real.

### The game ships biome geometry after all

`data/region_names.json` says, in its own `known_limitations`, that "the game ships no biome
geometry, so this cannot be regenerated from game data". That is now false and worth
correcting rather than quietly working around.
`/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel`
is a `FGMapAreaTexture`: `mDataWidth` 4096, `mAreaData` a 4096² array of palette indices, and
`mColorToArea` resolving each index to a `UFGMapArea` object with its bounding box. 37
indices, 17 distinct named areas plus `Area_NoMansLand`. A 2026-07 sweep saw the asset,
called it "low prior, custom serialisation" and deferred it; it decodes in twenty lines
through the reader this repository already has.

**Its corners are measured, and the region grid is not what measured them.** Nothing in the
asset says where those 4096 texels go. Four statistics were tried against the world box and
three of them are unusable: least squares on per-area centroids, IoU against the
heightfield's landscape extent, and IoU against the artwork's ocean colour all have optima
hundreds of metres wide and drift to the edge of whatever sweep contains them, because the
landscape extent is a rectangle rather than a shore and `NoMansLand` is a scope difference
rather than a coastline. The one that works asks a sharper question: **does an area boundary
land on something the map draws?** `calibrate_biome` takes the artwork sheet's edge strength
averaged over the raster's boundary texels, divided by its edge strength everywhere. At the
in-game map square that ratio is **1.97**; at ±600 m in any direction the best rival is
**1.33**, at 5% larger **1.28** and 5% smaller **1.24**. So the biome raster spans exactly
the square the artwork does — 4096 texels over 7500 m, 1.831 m to the texel, row 0 north —
and every run re-measures it and refuses to claim the pin if the margin goes.

The check by NAME was reported, was deliberately not the pin, and has since turned into
something else. While `data/region_names.json` was a hand trace of the wiki's map it was an
independent reading of the same world: of its 768 non-void cells, **481 (62.6%) landed on a
named game area** — the other 287 being the outer coast the wiki names and the game leaves as
no-man's-land — and of the **401** comparable by name, **273 agreed (68.1%)**, with one
disagreement dominating: the wiki's Spire Coast ring against four game areas, 62 of the 128
misses. That number is kept as history in the region table's own `_meta.retired_wiki_trace`,
because it is the size of the change the re-derivation made.

It is not evidence any more. The region table is derived from this same asset now, so
`region_table_is_current` asks a staleness question instead: anything under 100% means the
committed table was cut from a different build, and the run says which command fixes it. The
corners still come from the edge ratio, which is the only measurement here sharp enough to tell
100 m from 400 m.

### The palette is designed, and the shipped one is not used

`mColorPalette` has 37 RGBA entries and they are a minimap legend: flat primaries, cyan,
magenta, pure white. Drawing them would produce a highlighter sketch of a world. So they are
decoded, recorded in `_meta` for a reader to see, and ignored; `BIOME_COLOURS` is a table
written by eye against crops, desaturated and capped well below white, and a test holds it to
both of those. Over it go four rules in an order that is the argument: the biome says what
grows there, the **slope** overrules it because nothing grows on a cliff face, the
**altitude** bleaches what is left, the **hillshade** lights rock and canopy alike, and the
water goes on top because it is a different surface rather than a different ground. Two
octaves of fixed-seed value noise keep the flats from being flat fills.

Two things had to be softened before it read as imagery rather than as a choropleth. The
area polygons meet along a mathematical line — they exist to name a place on a minimap, not
to draw one — so the colour field is Gaussian-blurred by 24 texels (≈44 m) before it is
sampled. And the shoreline is feathered twice: over 0.9 m of depth, which handles a beach,
and by 0.73 m of blur, which handles the great deal of this world's water that sits in
box-shaped bodies against a cliff and has no depth band to blend in. (That second one was
0.8 output *pixels* until the sheet doubled, at which point the same constant would have
quietly meant half as much ground; it is in metres now.)

### Layers on the serving side

* **`/api/maptiles/{z}/{x}/{y}` is unchanged and is an alias for `map`.** Not a redirect and
  not a deprecation: the live page addresses the base map there, every cached tile is keyed
  on it, and the layered route is four segments where that one is three, so they cannot
  collide. Both go through one `_serve_tile`.
* **A layer is a directory.** `map` keeps `data/local/tiles/`; renders live at
  `data/local/renders/{layer}/tiles/`, cut on the same frame at the same 256 px into the same
  `{z}/{x}_{y}.png`. Switching layers is switching one path segment — not the CRS, not the
  bounds, not the zoom range.
* **Every header is that layer's own**, read from the sidecar beside its own tiles: depth,
  tile size, corners, build. They are generated by different tools at different times, the
  artwork can be two levels deeper if it was `--enhance`d, and the build tag folds the
  layer's NAME in so two pyramids that agree on every recorded number still cannot share a
  cache key and serve each other's `immutable` tiles.
* **An unknown layer is a 404 listing the ones there are**, not a 422 about a path parameter,
  and it never becomes a filename: the string is looked up in `_layer_dir`, which answers
  `None` for anything that is not a name this module wrote down.

### 16384 px, z6, and no further (2026-07-31)

The renders were 8192 px — 0.92 m to the pixel — and stopped at z5 on the argument that a
1 m field cannot support more. That argument was half right and the wrong half was the
sampler. Bilinear is **C0**: its derivative jumps at every texel boundary, and the hillshade
is a function of the derivative, so sampling the field below its own spacing ruled the relief
into 1 m squares. Swapping to **Catmull-Rom** (cubic convolution, a = −1/2), which is C1, is
what makes a finer grid mean anything: the surface has a continuous gradient and the shading
computed on it does too. Where the 4×4 stencil is not whole — a fifth of this field is
no-data, so that boundary is long — the 2×2 answer is used instead, because a cubic kernel
has negative lobes and one straddling a hole overshoots.

So the renders are now **16384², z0..z6**, 0.458 m to the pixel: 2.18 samples per metre of
source, past Nyquist.

**And 32768 was measured and refused.** On four windows — an offshore cliff island, a
mainland cliff face, the dune field, the Spire Coast — in both layers, the change one
doubling makes (the finer render box-filtered onto the coarser grid, minus the coarser
render, mean absolute per channel) is **3.34 levels of 255 for 4096→8192, 2.69 for
8192→16384, and 1.38 for 16384→32768** — at 3.6× the drawing time and 4× the bytes. More
telling: the **high-frequency energy per pixel falls at every doubling**, in all eight
window-and-layer pairs (15.34 → 14.32 → 13.57 on the mainland cliff). That is a sampler
resolving an interpolant, not a picture finding new world in the source. What 32768 buys is
antialiasing on cliff facets. z6 is the last level with a measurement behind it.

The artwork pyramid can still invent z6 and z7 with an upscaler, because a drawn map has
strokes a model understands. These layers have no such licence.

**Superseded on the size, not on the measurement — see §20.** The renders are 32768², z0..z7
now. Everything above still holds and is still what `_meta` says: doubling the sampling of a
1 m field finds no new world, and the re-run on the v3 field measured the same direction. What
changed is that z7 stopped being sold as information. It is sold as *smoothness a browser
cannot produce*, because the client's own upscale of a z6 tile is bilinear and therefore C0 —
and, over the direct regime, the pixels stopped being a reconstruction of the field at all.

### Borrowing the artwork's shading where the field's province is coarse (2026-07-31)

The 1 m grid is one resolution and **not one accuracy**, and rendering as though it were is
what made the offshore islands look like melted wax. Measured on the shipped field: 45.3% is
landscape — continuous geometry the game evaluates itself, which genuinely sharpens — 21.3%
is cliff, rasterised low-poly collision hulls whose facets resampling can only polish, and
14.0% is fill, a 3.9 m-quantised block raster where resampling does nothing at all.

**2026-07-30, the cliff half of that sentence is out of date and the conclusion is not.**
Heightfield v3 builds the cliff layer from the Nanite leaf rather than the collision hull:
the world-space median triangle edge is 0.48 m, not 2.48 m, and 73% of cliff texels now hold
at least one source vertex. So "low-poly" is wrong and the province genuinely does carry
detail the artwork is not needed for. It is borrowed over anyway, for now, because the gain
was picked by looking at a hull-built field and re-picking it is a separate change with its
own before-and-after — and because the borrow's own province test had to be widened to both
cliff values regardless, or it would have withdrawn itself from three quarters of the
province by renumbering. See [§20 of `parked.md`](../parked.md) for what v3 did and did not fix.

Over the last two provinces, and only over them, the render borrows the artwork sheet's
**luminance high pass**: the game's own 8192 px map, decoded from its four BC1 slices in the
same run, minus its own Gaussian blur at σ = 8 px, multiplied into the shading and faded out
on the provenance byte over 6 m. It is light, never colour — an ocean drawn blue contributes
its brightness and nothing else — and where the provenance says landscape, the shading is the
field's own and nothing is borrowed.

Two things about it are findings rather than settings. **The drawn map has strokes**: every
rock formation is outlined in hard dark ink, and multiplied straight in they come out as
exactly what they are, a line drawing laid over terrain. So the high pass is softened by
1.6 px and squashed through `tanh` at 1.2σ — a soft clip, so the mid-tones (the shading) pass
almost linearly and the outliers (the ink) saturate. And **the gain was picked by looking**:
at 0.17 an offshore cliff island is still eight flat plates with a hint of something on them,
at 0.50 the artwork's contour rings read as rings; 0.30 is where a collision hull becomes
rock and the dune field — landscape province, untouched — still looks exactly as it did.

The same run now reads that sheet for the biome calibration too, which removes a skip path:
the pin used to go unmeasured when `data/local/map.png` was absent, and it is scored against
the container every run.

### Two tile trees, and the second one is the same grid

`tiles@2x/` holds the identical tile **grid** at 512 px a tile: level z is still 2^z tiles a
side over the identical squares of the world. A hi-DPI client asks for the same
`{z}/{x}/{y}`, adds `?px=512`, and draws the answer into the same CSS box — twice the pixels
in each direction, no change to the CRS, the bounds, the zoom range or the tile size the
layer is configured with. It is exactly one level shallower by arithmetic (512·2^z runs out
of sheet before 256·2^z does), so past its top the two carry identical information.

The probe advertises both depths (`X-Map-Tile-2x-Px`, `X-Map-Tile-2x-Max-Z`), absent when a
layer has no such tree, and a request for a density a layer does not have falls back to the
1x tile — which every client can draw at any density. Both trees' numbers ride in one cache
tag, so recutting either changes every URL of that layer.

The client picks it at `devicePixelRatio >= 1.5` rather than `> 1`: a 125% Windows scale
reports 1.25, where the @2x tile is 60% more pixels than the screen can show. It is read once
at probe time and not watched — a window dragged to another monitor is a reload, the same
bargain the CRS already makes.

### Cutting in parallel, and the proof that it is the same bytes

A lit layer writes three trees: `unlit/`, `tiles/` and `tiles@2x/`, 45,055 tiles at 32768,
all at `optimize=True`. Until 2026-10-06 each tree was cut on its own. Every level's Lanczos
ran serially in the parent while the encode processes waited, 37 to 61 s a tree at full
size, and each tree copied the sheet three times on its way to them (a Pillow image, its
bytes, a shared block), about 14 GB. `mapgen/tiles/cutter.py` now cuts a layer's trees
through one pool:

- **One encode pool per layer.** `--cut-workers` processes (default one per logical core, at
  most 24) encode one row of tiles per job, for all three trees. A level's rows are queued as
  soon as its pixels are ready, so the pool encodes the unlit z7 while the levels are
  resampled and the sheet is relit. `--workers N`, as before, sizes both this pool and the
  light bake's where `--cut-workers` or `--light-workers` is not given (section 29).
- **One copy of the sheet per tree.** The sheet is copied into a `shared_memory` block and
  z7 is encoded from that block; no Pillow image of the whole sheet is made. The unlit tree
  encodes from its own block, so the relight can change the sheet underneath it.
- **Encoders without numpy.** The encoder (`core/gameassets/pyramid.encode_tile_row`) reads
  the block through Pillow. numpy's BLAS thread pool commits about 0.75 GB in every process
  that imports it, 18 GB for 24 encoders, and on a busy machine that exhausted the commit
  limit with physical memory to spare.
- **Levels in strips, on threads.** Each level is still one Lanczos downscale of the whole
  sheet. Eight threads compute it in strips of about 128 MiB of source: each strip resizes a
  row range with a `box`, out of source rows cut with a halo of 3 × scale + 4. Pillow
  derives each output row's taps from its position alone and reaches 2.5 × scale + 0.5
  source rows either side, so every strip is the same bytes as those rows of a whole-sheet
  resize. Measured at scales 2 to 128; a halo of 2 × scale fails at every scale. The threads
  run in the parent, because Pillow releases the GIL inside a resize.
- **A level two trees share is resampled once.** `tiles@2x/` is cut from the sheet
  downscaled to 16384, the same resize as `tiles/` z6, so its top level is that block. On a
  sheet of 16384 or less every @2x level is a 1x level.
- **Memory.** Before a new block the cutter checks free memory (`mapgen/pools.py`, shared
  with the light bake), physical and on Windows commit; with less than the block plus 4 GB
  it waits for work in flight. The encoder count
  is capped the same way at 200 MB an encoder. A block is freed when the last job and strip
  reading it finish. A failed strip settles its level only after every strip of it has
  stopped, so no strip writes into a freed block.
- **Serial.** `--cut-workers 1` cuts one tree at a time with `install_pyramid`, the
  reference the parallel path is compared with.

The 15 to 26% of z7 tiles that are one void colour are encoded like any other. A one-colour
tile takes 0.5 to 0.7 ms against about 34 ms for a land tile, so writing cached bytes would
save under 1%.

`--check-parallel` proves it rather than asserting it: the artwork's whole pyramid cut both
ways, SHA-256 of every tile compared name for name, recorded in the sidecar. On an 8192 piece
of renders-v7, 1,365 tiles: 38.0 s serial against 2.3 s on 24 encoders, byte_identical true.

A 2048 render of all five layers, lit, `--workers 2`, ran before and after the change, one
after the other: all 1,125 tiles and light tiles match by SHA-256 (425 unlit, 425 lit, 105
@2x, 170 light). The sidecars differ in their timings and in the encoder count (2, now the
default 24) only. The cut took 0.9 to 1.6 s a layer, against 3.2 to 3.5 s.

Measured on the reference machine (2026-10-06, other renders running), the trees of one lit
layer, the bake skipped and the relight an in-place invert:

| | before | after (24 encoders) |
| --- | --- | --- |
| 8192 piece of renders-v7 `painted` z7, 3,071 tiles | 29.8 s on 8 workers, 23.6 s on 24 | 8.9 s |
| the same piece tiled to 32768, 45,055 tiles | 343 s on 8 workers | 125 s |
| peak working set at 32768 | 13.1 GB | 12.9 GB |

Every tile hashes the same before and after. The peak is no lower: each tree now holds about
6 GB where it held 14, and the difference is spent running the three trees at once. When
memory is short the cutter runs them closer to one after the other. renders-v7 cut its five
layers in 1,826 s on 8 workers; at the 2.75 times measured here that is about 660 s, more
than 19 minutes saved. The relight, serial before, now overlaps the unlit tree.

### Deflate level (2026-10-06)

Every tile PNG, serial or parallel, is now written at zlib level 6 (`pyramid.TILE_PNG`)
instead of `optimize=True`, which is level 9 with a search over the filters. It is lossless
either way, so a tile decodes to the same pixels; only the file differs. Measured on 460 z7
and @2x tiles of renders-v7 (painted, terrain, relief, satellite `unlit/`), each re-encoded
from its decoded pixels three times both ways in one process, CPU per tile:

| Tiles | `optimize=True` | level 6 | Faster | Bytes |
| --- | --- | --- | --- | --- |
| painted z7 | 17.6 ms | 2.6 ms | 6.9× | +7.0% |
| terrain z7 | 18.0 ms | 2.6 ms | 7.0× | +6.9% |
| relief z7 | 16.6 ms | 2.5 ms | 6.6× | +6.1% |
| satellite `unlit/` z7 | 9.8 ms | 1.7 ms | 5.9× | +7.1% |
| painted @2x z5, 512 px | 71.9 ms | 12.4 ms | 5.8× | +6.5% |

Every tile decoded to the same mode and pixels both ways. At full size a lit layer's three
trees, about 3.2 GB in renders-v7, grow by about 0.2 GB, and their encoding takes about a
seventh of the CPU it did. The owner chose the trade on 2026-10-06.

A 2048 render of all five layers, lit, `--workers 2`, before and after this and the normal
tiles' WebP effort (section 29): all 1,125 tiles decode to the same mode and pixels, and the
85 horizon atlases are the same bytes. The files grow 3.3% at that size: the PNG trees 1.8
to 6.3%, the normal tiles 3.3%. The sidecars differ in byte counts and timings only.

### And a swap Windows can refuse

`install_pyramid` renames the finished tree over the old one so a reader meets a whole
pyramid or none. **Windows will not rename a directory anything has open** — an Explorer
window sitting in `tiles/`, the search indexer, a backup agent — and that is the ordinary
state of a directory a person has been looking at, not a rare one: it ended a ten-minute run
with `Access is denied` on the machine this was written on. So there is a second-best, and it
says it is second-best: the swap is done **one level at a time**, each level renamed
atomically over its predecessor, and a reader who catches the middle sees every level present
with some still the old cut rather than a level missing. Which of the two happened is
recorded in the sidecar as `tiles.installed_by`.

### Why a new tool rather than a stage on the heightmap generator

`gen_world_heightmap.py`'s job is to get a field *out of the game* — sweep cooked packages,
rasterise collision meshes, validate against 626 nodes. `gen_map_renders.py` reads that
finished field back through the same public codec any consumer would, adds an input the
heightmap generator has never heard of, and writes pictures. They share an input and nothing
else: no stage, no constant, no intermediate array. Bolting them together would have coupled
a six-minute extraction to a ninety-second render and given one `--force` two meanings. What
*is* shared is shared by import — the codec from `domain.spatial.heightfield`, and the
pyramid cutter, its staging rename and its refusals from `core.gameassets.pyramid`, which
grew one optional `source=` so a level record can say what actually drew it. The frame
itself — the corners, the sheet size, the artwork the biome pin is scored against — still
comes from `gen_map_image.py`, because that is the tool that *measured* it.

### The artwork command, piece by piece (2026-10-05)

`mapgen artwork` (`tools/gen_map_image.py`) is split by concern. `commands/artwork.py` holds only the
arguments, the order of the stages and the refusals.

**Why it is a loader.** A rendered map of this world is Coffee Stain's artwork, so
`/api/mapimage` is a loader and only a loader. The command reads the player's own install into
the gitignored `data/local/`, and none of it is committed or served past localhost.

**The sheet** (`gamedata/artwork_sheet.py`). The in-game world map is four `Texture2D` under
`/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_{col}-{row}`. Each is 4096×4096
`PF_DXT1` with 13 mips, and together they stitch into one 8192×8192 sheet. Both readings of the
name produce a plausible map, because the world is roughly symmetric at a glance. So
`seam_residuals` re-proves the layout on every run, and the run refuses to write if the layout
stops holding. `calibrate` re-measures that the sheet spans the corners the sidecar pins. Alpha
is dropped when it is 255 everywhere, because uniformly opaque alpha is a third of the file.

**The trees** (`tiles/artwork_output.py`). The sheet is cut into `tiles/{z}/{x}_{y}.png`, one
resolution per zoom, so the page fetches a few hundred KB at the whole-world framing instead of
16.2 MB that decodes to 268 MB of RGBA. `map.png` stays as the fallback for a page that finds no
pyramid, and it is the one file a reader can open and look at. `tiles@2x/` is described above.
Each tree is staged and renamed into place on its own, so the pair is never half-swapped and a
failure in the second leaves the first where it was.

The @2x tree is never enhanced. @2x level z holds the same pixels as 1x level z+1 in tiles twice
the size, so an `--enhance` run's z6 and z7 are already reachable: the @2x tree tops out a level
sooner and the client asks for the 1x tile above it, which is the fallback `_tile_tree` was
written around. Cutting @2x from upscaled pixels would be a second GPU pass for resolution the
reader can already get.

**The sidecar** (same module). `map.json` carries the four corners the endpoint
reads, plus `_meta`. `tiles_2x` is left out entirely when there is no such tree, rather than
written as a record saying "absent": `_map_pyramid` answers `max_2x_z: None` for a layer with no
block, which is how `_tile_tree` knows to serve the 1x tile to every client. A block that said
the tree was missing would still be a block, and a block means there is a tree. The renders
write the same shape for the same reason. The staleness guard reads the sidecar back: a picture
or tree from another build is refused (exit 3), and so is a run whose enhancement recipe is
behind the one on disk (exit 5). That second rule compares recipe numbers, not the `enhanced`
boolean: enhancing a plain pyramid is an upgrade, the same recipe is a refresh, a later one an
upgrade again, and only an earlier recipe over a later one is refused. The usual case is a plain
re-run over a sharpened tree, which would halve the map's usable resolution and say nothing; an
older checkout re-cutting a newer recipe's tiles is the other. A sidecar with only
`enhanced: true` was cut by recipe 1, the one pipeline that boolean ever described, and a recipe
value that is not a positive whole number falls back to the boolean. The recipe numbers and
their words live in `tiles/recipes.py` beside the render recipes.

**The enhancement** (`enhance/`). `--enhance` adds z6 and z7, two more zoom levels than the
artwork has pixels: 8192 px is about 0.9 m to the pixel, and a factory is machines eight metres
across. It runs the sheet through Real-ESRGAN 4x on the GPU. It is off by default, because it
needs a 45 MB binary this repository will not vendor and a Vulkan device.

- `enhance/upscaler.py` downloads the binary once into the user cache, checks its digest before
  unpacking, smoke-tests it on its own sample image, and checks that numpy and scipy come from
  one environment.
- `enhance/pixels.py` holds the three passes around the model: `presharpen` on the input,
  `faint_mask` (the hybrid repair) and `colour_fix` on the output. Each says at its definition
  what it repairs. Off its mask the pre-sharpen is exactly the identity, so it cannot leak onto
  a fill. The colour fix works at the output's resolution, with the source Lanczos'd up to meet
  it: blurring the source small and stretching it drifts half again as much.
- `enhance/levels.py` cuts the source squares, runs the model and tiles the result. The squares
  in `in/` are pre-sharpened and are the model's input only. Both repairs re-cut the untouched
  source from the sheet, because they correct towards the source, and correcting towards a
  sharpened copy corrects nothing. The colour fix runs before the core is cropped out of its
  overlap: its blur reaches about 25 px, and a core cropped first would have no neighbour to
  reach into, which is a seam in the making. The same module re-measures every claim into
  `_meta.tiles.enhancement`: the tile seams against boundaries that are not seams, and the low
  levels against the enhanced pixels they did not come from.

The subpackage is its own unit rather than part of `palette/`, because it is not a style: it
changes the artwork's recipe number, not a palette digest. It sits above `tiles/` in the import
order, because it reads the recipe table there and cuts its levels with the same pyramid code.

## 20. The two-regime sampler, and a smoothed z7 (2026-07-31)

### The ragged rim was never the geometry, and it was not the density either

Heightfield v3 put the Nanite leaf under the cliff layer — 11.6× the triangles, world-space
median edge 2.48 m → 0.48 m — and the rims on every rock did not move by a pixel. They could
not: the field is a **1 m max-Z fold**, and a rim drawn from it is a 1 m staircase however
fine the triangles that were folded onto it were.

So `gen_map_renders.py` reads the **triangles** now, by *importing the generator that writes
the field*: `sweep_levels`, `read_mesh_geometry`, `rotation_matrix`, `winding_sign`,
`MaxZRaster` and all four placement culls are called, not copied. The only thing this file
changes is the grid they are pointed at — its own 32768², 0.229 m to the texel — which is
what makes a difference between the render and the field a difference of spacing rather than
of rasteriser. 216 M triangles over 20,233 placements, banded at 256 rows, 806 s, written to
a memory-mapped scratch file so both layers draw from one rasterisation. (Since section 39
the scratch file is a zstd band store.)

**Two things then had to be got right that the parked design got wrong**, and both were
found by looking at the picture rather than at the statistic.

### One: the kernel has to interpolate the lattice, not the fold it produced

The first draft did what the design said — Catmull-Rom over the field, direct rasterisation
where the density plane says so, a cross-fade between them — and the rims **did not move**.
Interpolating the composed field over a rim reconstructs the *fold*: a texel just outside a
rock is still a cliff-top height, because a cliff-top texel is one of the four the stencil
reads. The drop stays exactly where the 1 m lattice put it, at any output resolution. z7 then
draws that staircase *more sharply* than a bilinear upscale of z6 did, which is worse than
the thing it was supposed to beat.

What the kernel has to be given is the surface **underneath**: the landscape and fill
lattices, which are continuous geometry the game evaluates itself, with the cliff province
taken out (`ground_lattice`). The rocks are then composited onto that at 0.229 m — which is
`gen_world_heightmap.py`'s own composition rule, performed at the render's spacing instead of
read back from its own output.

### Two: the density plane is provenance, not a gate

At 0.229 m the design's rule — one source vertex under an output texel, i.e. `density ≥ 19.09`
— is met by **10.77% of the cliff province**, and on a texel that qualifies a 6 m feather
gave the geometry only 0.387 of its own answer. Narrowing the feather was measured (a sweep
of seven widths against the seam trace picked 1 m, which raised that to 0.696), and it was
not enough, because of what the plane says where the worst rims are:

`prov == 4` means *the rasteriser reached this texel by interpolating a triangle wider than
itself*, which is `density == 0` **by construction**. On the 234 m window with the highest
cliff fraction on the map, **0.00%** of texels qualify at z7 — the density is not merely low
there, it is zero, and no weight built from it can reach those rims however it is shaped.

So the plane stopped gating. What decides that the rocks are drawn is **their own coverage of
the pixel**, which is the same rule the field uses one metre coarser; the rocks raise the
ground and never lower it, through a smoothed positive part (`DIRECT_LIFT_KNEE_M`, a quarter
of a metre) so the line where a rock meets the ground is not a derivative discontinuity the
hillshade would draw around every formation. What the density plane decides now is what to
**call** what was drawn — a measurement, or the plane of a triangle wider than a texel — and
`_meta.render.two_regime.regimes` counts both, per province, every run.

That is a real deviation from the parked design and it is the reason the rims smooth.

### The seam, measured along a line — and its premise made explicit

The design was explicit that the seam had to be validated by a **trace** and not by probes.
The statistic is `|d²z/dx²|` along every row of every band, over the whole 32768 square,
sorted into pools by the weights under **all three** texels of each stencil.

Two versions of it were wrong before this one, and both are worth recording because both
*passed*:

1. Pooling around `w = 0.5` measures the one place a hard join has nothing to show — a
   feather's second derivative is zero at its own midpoint by symmetry, and it lives at the
   shoulders. Every join passed, including a switch.
2. Comparing the join against the two pure regimes measures the **terrain** once the
   composition is by coverage, because then the join *is* the rock's silhouette: a real cliff
   edge, where enormous curvature is the correct answer.

What ships is two bounds and both have to hold. **Against the counterfactual**: the same two
surfaces over the same texels joined by the hard `max` the field itself uses — a fade that is
not smoother than the switch it replaces has bought nothing, so the bound is 1.0. **Against
the terrain where the surfaces agree** to within half a metre (`SEAM_SAME_SURFACE_M`) — the
design's own comparison, with the premise it never had to state, and the design's own bound
of 1.5. The harness test drives both with a fade and with a one-texel join and requires the
second to fail.

### What the fill province gets instead

Recipes 3 and 4 de-terraced the fill province: a low pass at the interface raster's 3.66 m
cell size, clamped to one 3.9 m step. That step assumes an 8-bit raster. The raster is
float16, with steps of 0.24–0.49 m, so there was nothing to de-terrace; the blocks on screen
were nearest-neighbour 3.66 m cells. Recipe 5 re-reads the raster instead (section 26), and
`check-fill` keeps the de-terracing only as the baseline its seam check is scored against.

### z7: what it is, and what it is not

The measurement that stopped these renders at 16384 was re-run on the v3 field and came out
the same: high-frequency energy per pixel **falls** at every doubling, 3.05 → 1.50 levels on
the worst cliff window. Doubling the sampling of a 1 m field finds no new world, and z7 is
**not** sold as more information. `_meta.render.z7` says so in those words.

What z7 is, is smoothness the client cannot produce. A browser shown z6 at twice its scale
upsamples it **bilinearly**, and bilinear is C0 — its derivative jumps at every source texel,
so the relief comes out ruled into 0.458 m squares. That is the identical failure that moved
this file off a bilinear sampler in the first place, relocated from the render into the
viewer. A z7 tile is the same surface evaluated by the same C1 kernel at half the spacing.

And over the cliff province it *is* new information at z7, because there the pixels are
triangles rather than a reconstruction of a fold of them.

### The @2x tree stayed at z5

Cutting it from the 32768 sheet would give it a z6 of 512 px tiles weighing about as much as
the entire 1x pyramid — for pixels a hi-DPI client already gets. Leaflet's own retina path
asks for `z + 1` at 1x and draws it at half size, and the 1x tree is now a level deeper than
it was, so the dense tree is cut from `RENDER_2X_PX` (16384) and stays exactly where it has
always been. It is the one place the two trees stopped being the same arithmetic, and the test
that used to assert "one level shallower" asserts the two sizes separately now.

### Refusals

Four pins, all refused on rather than overwritten. The field build under the tiles; a field
that is not there at all; a field with **no density plane**, which is a field from before the
cliff layer became the Nanite leaf and cannot state what any of its cliff texels are — that
run stops and names the generator version that writes one, rather than drawing under a
sidecar claiming recipe 3; and the direct cache, which carries the size, the sub-sampling and
the build it was rasterised for, so a cache from another render is rebuilt. `--kernel-only`
is the honest way to draw without the geometry: it draws **recipe 2 whole** — no rocks, no
lattice split, no de-terracing — and records that recipe number, so a before/after against it
is a comparison of two recipes rather than of one recipe against half of itself.

### Triangles wider than 256 texels (2026-10-07)

`MaxZRaster.add` buckets triangles by the span of their box, up to `MAX_SPAN` (256) texels,
and dropped every wider one without a count. A wider box is now scanned in tiles of 256
texels, clipped to the raster first. A tile tests the same sample points against the same
triangle as one unbounded bucket would, so the tiling changes no texel and no height, and a
narrower triangle takes the path it always took.

Measured on build 502094, counting every pass with a rasteriser that only counts:

| Pass | Wider than 256 texels | Of them on the raster |
| --- | --- | --- |
| field cliffs, 1 m | 3 | 0 |
| field top, 1 m | 3,538 (scenery arches) | 0 |
| render direct, 2048 / 16384 / 32768 | 0 / 31 / 258 | 0 / 0 / 85 |
| render top, 2048 / 16384 / 32768 | 0 / 91 / 1,433 | 0 / 0 / 0 |
| render meshes and Titan trees, all three sizes | 0 | 0 |
| crown sprites, 12.5 cm | 0 (the widest spans 62) | |

So the field's planes are unchanged, and only the 32768 direct raster gains anything: the 85
triangles that left two `CliffSide_01` flat tops open, about 19,000 m² around (-2115, 79) and
(-1437, 31). Scanning them adds 229 tiles across the sheet's bands. The heightfield generator
is version 6, which moves `cliff_geometry` and so every direct and top cache; `render_meshes`
is reader version 4 and `titan_trees` 2. The paint store keeps generator version 3.

## 25. Renders after the terrain work: recipe 4 (2026-10-05)

Four ideas from the terrain study (section 22) were tried on the map renders. Each was
rendered as 176 m crops of a cliff, a boulder field, two arch sites and sloped landscape,
before and after, and measured where a number exists. Build 502094.

| Idea | Verdict | Evidence |
| --- | --- | --- |
| Read the landscape from `terrain.u16.z` (7.8 mm) instead of the decimetre ground plane | **Adopted** | The 0.1 m steps drew contour-like terraces on every gentle slope. On five crops the slope error against the exact surface fell from a median of 2.6° to 0.2–0.3°, p99 from 6.6–29° to 1.6–4.9° |
| Use the engine's two triangles per quad instead of Catmull-Rom | **Rejected** | Exact in height, but the hillshade shows every 1 m triangle as a flat facet. Catmull-Rom over the same samples departs from the triangles by a median 0.9–1.6 mm (p99 13–35 mm), so it does not round creases off visibly. Shading with the game's interpolated vertex normals removes the facets but softens every ridge; it is not used either |
| Draw the arches and foliage boulders the field keeps in `top.i16.z` | **Adopted** | 1,076 arches and 41,351 boulders were missing. The artwork borrow drew the arches as faint ghost stripes with nothing under them. With the overlay drawn, the render's correlation with the game's own map sheet rises from 0.18 to 0.33 and 0.22 to 0.30 on the two arch crops, and is unchanged elsewhere |
| Check the render's cliff raster for the half-texel bug fixed in generator v5 | **Already correct** | The direct pass samples at `col + 0.5` on the frame's corner, which is the pixel centre; `test_the_direct_pass_applies_the_field_s_own_culls_and_lands_where_it_says` pins it. The v3 field's shifted planes only moved the ground lattice's holes: drawing from the v5 field changes 4–6% of cliff-crop pixels by over 0.5 m, with no visible difference |

### What recipe 4 draws

- **The lattice under the kernel is the bare landscape**, wherever the landscape has a sample
  and the province is landscape or cliff. Under a rock this replaces the hole that recipe 3
  left, which used to fall back to the 1 m fold and drew a dark staircase ring around every
  formation. The fill province keeps its de-terraced value.
- **Rock pixels are gated on triangle coverage at the pixel centre**, never on the density
  plane, which only labels the regime statistics. A covered pixel is the triangle's own height
  even where the triangle is wider than the pixel. Recipe 3 then ran a 3x3 tent over heights
  and coverage, which spread rock heights up to 0.23 m across every silhouette and blurred rock
  detail; that tent is gone. Sub-samples (`--direct-subsamples`) are the only antialiasing.
- **Arches and boulders are a second direct pass**, at their finest decoded geometry (Nanite
  for five of the six boulder meshes, LOD 0 for the sea rock), composited last by the same
  coverage and smoothed lift as the rocks. These are the visible meshes; planning heights use
  the collision surface instead.

### Known limits

- The water edge is still a 1 m staircase, because the water quality plane is 1 m.
- The artwork borrow still multiplies the drawn map's arch strokes into the shading, so a
  faint ghost stripe can sit beside an arch where the drawing and the mesh disagree.

## 26. Rebuilt base data and a PCHIP sampler: recipe 5 (2026-10-05)

Recipe 4 drew its gentle ground from `terrain.u16.z`. Recipe 5 rebuilds the rest of the
lattice the kernel reads, and swaps the kernel. Rocks, arches and boulders are drawn exactly as
in section 25. Build 502094.

### What changed

`terrain/fill.py` (then `tools/map_fill.py`) builds the lattice once per run, in about 13 s, before any band is drawn:

| Texels | Source | Share of the field |
| --- | --- | --- |
| Landscape | `terrain.u16.z`, unchanged | 45.39% |
| Cliff province | the field's own heights, copied unchanged | 20.97% |
| Fill | the float16 interface raster: Gaussian with sigma 1 texel, then cubic, then +1.0 m | 13.49% |
| Fill within 48 m of the landscape | as above, plus the landscape's residual carried in by a harmonic solve and a cosine taper | 0.25% |
| Interior holes | biharmonic fill, or harmonic where the biharmonic leaves its border's range by more than 2 m | 0.005% outside the cliff province (35 holes, 14 harmonic, nearly all ground under rock) |
| Pits: no data and ground below -200 m the artwork draws as void | left empty, drawn as the void | 0.55% (681 regions; 210,086 texels of ground) |
| No data out to the field's edge | left empty: the open sea or the void, as the artwork has it | 19.35% |

Shares from the 2048 preview of 2026-10-05, after the pits were taken out; before that the
landscape was 45.46%, the cliff province 21.06%, the fill 13.64% and 0.32%, and 26 interior
holes (0.16%, 11 harmonic) were filled, the pits among them.

- **The de-terracing is gone.** `FILL_VERTICAL_M = 3.9` assumes an 8-bit raster. The raster
  is float16, with steps of 0.24–0.49 m, so there was nothing to de-terrace.
- **Rock is never a constraint.** A hole next to a rock, or the ground under a rock with no
  landscape sample, is filled from the ground around it. The rock's own heights stay in the
  cliff province and are composited by coverage, as before.
- **The open sea past the data** was left the page's colour, and the prototype's water over
  an invented bed was declined. Its straight landscape-component edges then read as a
  two-colour ocean in every style, so since 2026-10-05 a bed is drawn under it after all,
  as drawing support and never as data: see "The open sea, the void and the pits" below.
- **Water over the fill province** is not read against the raster: the raster stores the
  water surface there, so the ground under it is not a sea bed. Until 2026-10-05 it was drawn
  at full alpha and the deep tint; at the ocean's level it now gets the open sea's bed too.
- **Holes under water** stand at least 0.5 m below their own water surface.

The **sampler** is tensor-product PCHIP with Fritsch-Butland slopes: the harmonic mean of the
two secants, or zero where they disagree in sign. It uses the same 4x4 stencil and the same
separable passes as Catmull-Rom. Each 1-D pass stays inside its interval's endpoints, so the
result never leaves the range of its own 2x2 cell. Where the stencil is not whole, the bilinear
fallback is unchanged. The water level is sampled the same way, and the satellite layer's slope
rule reads the same heights. `--kernel-only` (recipe 2) still uses Catmull-Rom.

### Measured

`python -m mapgen check-fill` (then `tools/check_map_fill.py`) runs these checks through the
shipped functions. Only the baselines
are emulated.

| Check | Before | After |
| --- | --- | --- |
| Fill, median absolute error on held-out dry landscape (532,633 texels, east half) | 1.079 m (nearest texel, what the field stores) | 0.519 m |
| Seam, median jump at a fake seam through dry landscape (60 windows) | 0.813 m (nearest raster, recipe 4 de-terracing) | 0.072 m |
| Holes, median MAE over the field's 12 interior hole shapes on known landscape (68 placements) | 0.400 m (nearest neighbour) | 0.101 m |

The sampler was measured on the rebuilt lattice over six 256 m crops: a cliff, rolling ground,
a coast, a boulder-and-arch site, the dry frame edge and the crater hole.

| | PCHIP | Catmull-Rom |
| --- | --- | --- |
| Largest error at the 1 m vertices | 0.0 m | 0.0 m |
| Overshoot outside the 2x2 cell, largest | 0.0 m on every crop | 8.61 m (cliff), 3.90 m (frame edge), 3.29 m (crater hole) |

The fill keeps a median bias of +0.08 m against the landscape. The bias was picked on the west
half and is scored on the east.

The seam trace's share of a hard switch is 1.22, against 1.18 for recipe 4. It is above 1
on both because the smoothed lift is not a convex blend. The seam's own p99 curvature fell
from 3,638 to 3,554, and the pure-kernel p99 fell from 2.11 to 1.24.

### Known limits

- The 1 m staircase along the water edge is unchanged, because the water quality plane is 1 m.
- The rebuilt lattice is not stored. The server's height lookups still read the field as
  generated.

### The open sea, the void and the pits (2026-10-05)

A review of the 2048 preview found the sea split into straight-edged blocks in every style.
Four causes, all in how the shared band loop drew water and no data:

- **Level-only ocean had no bed.** It covers 13.1% of the field (95% over the fill province)
  and was drawn at the deep end of every ramp. The measured ocean beside it has its own depth:
  61 m in the median where the two meet (the game's unsculpted floor), but in places a shelf
  about 8 m deep runs straight up to a landscape component's edge. Every such edge was a
  colour step.
- **No data was sea and void at once.** Every no-data texel was the page's navy. The artwork
  draws 86% of them black, the void past the world's edge, and 356,283 of them (the strip down
  the west edge, among others) as sea; the water channel already calls those level-only water.
- **Pits were filled flat.** The fill gave every interior no-data hole a membrane. The artwork
  draws 14 of them as pits, among them the crater at (-164, 1310) (74,763 texels) and the pits
  at (240, 1608) and (2224, 1566), the second the square notch beside the abyss. Other pits
  are not holes but ground at the landscape's own floor, its lowest height (-254 to -258 m,
  137,337 texels): the abyss pits around (2037, 1283) and (2107, 1485), and the fill at the
  north-east corner (3941, -2935). They drew as flat ground at the bottom of every ramp.
- **Render-only meshes became islands** in the styles that draw ground and water only
  (section 27).

What is drawn now, by `gamedata/water/channel.py`, `terrain/fill.py` and
`palette/water/open_sea.py`:

- **The artwork says sea or void.** `artwork_planes` classifies the decoded sheet on the 1 m
  grid: water is the water channel's own test (`B - R >= 25`), void is anything else with a
  Rec. 601 luma of 110 or less. The pits are black to a flat grey (luma 0 to about 80), the
  ground beige or white (140 and up). Its water comes back as one of the artwork's four flat
  tones, split at `G - R` of 40, 56 and 70 (`WATER_ARTWORK_BANDS`).
- **A pit stays empty.** A region of no data and of ground below -200 m is a pit when at
  least half of it is void in the artwork: 0.88 to 0.95 for the crater and the abyss pits, 0.5
  to 0.7 for a crack under its white outline, 0.44 and less for the holes drawn as ground. Of
  the ground below -200 m the artwork draws 93% as void, and 210,086 of its 213,306 texels lie
  in regions that pass. A pit is taken out of both lattices, so it is drawn as the void; the
  ground under rock beside it is still filled, bounded by it, unless the pit walls it in.
- **The open sea gets a bed.** Under no-data texels the artwork draws as water, and under
  level-only water within 1 m of the ocean's level, `open_sea` writes a bed into the lattice
  the run draws. It is a screened Poisson membrane on a 4 m grid: the measured ocean's own
  depth where the two meet, the surface at a dry coast, and settling to 60 m over about 400 m
  away from both. A coast is dry ground standing at least 1 m above the top of the ocean's
  band (-16 m) in the field as stored; dry ground at or under that top is sea the artwork's
  mask left dry and joins it. The field's fill holds the sea's surface to within 0.7 m, and
  the rebuilt fill stands 1 m above that, so only the stored heights can tell the two apart.
  The water's depth is then read off that bed like any measured
  water's, so every style draws one surface, the hillshade has no 60 m step to light at the
  edge of the data, and the water classes and the relief tint read the same planes. 7.44 M
  texels get a bed, in about 7 s, among them 53,117 dry texels at or under the band's top
  beside the open sea, mostly the rows along the frame's edge the artwork's mask left dry.
- **The void** is every other no-data texel, pits included: 10.84 M. It is drawn in the
  page's navy, softened over 2 m and kept off any rock standing in it, and a pixel shared by
  sea and void counts its water against the part that is not void, so the void's edge is
  never drawn as land.

### The open sea and the void, a second pass (2026-10-06)

The next review still found straight lines in the sea and navy in the pits:

- **A seam east of the swamp**, overview x 1718, y 1147 to 1330, and its corner at y 1147.
  The measured landscape ends at game x 3045 on a 30.8 m plateau, and level-only fill lies
  east of it. Sampled across that edge from y 450 to 1120, the depth had no step: the
  membrane matched the plateau to within 0.6 m. It had a kink. The plateau is flat, while the
  membrane climbs at 0.07 to 0.7 m per metre from the first texel, pulled by the 60 m floor
  and by cliff-province seeds 98 m deep. The hillshade lights the bed and the depth tint
  follows it, so the kink drew a line 670 m long. The 4 m cells next to the seeds were not
  the cause: they put no step into the samples.
- **A darker strip right of the island chain** in waterfall_1, game x -1730 to -1600, y 3100
  to 3400. It is level-only fill closed in by the chain and the void, so no seed holds it
  and the membrane drew it 7.5 to 9.5 m deep. The artwork draws it cyan.
- **Pits in the page's navy**, flat and hard-edged: the crater at (-164, 1310), the abyss
  pits and the pit under ow_perched_5. Satellite's deep sea (#152a3b) and relief dark's
  (#031b2d) are close enough to that navy that the void past the world's edge read as ocean.
- **Hard straight edges where the sea meets the void**: east of the dunes, the south-west
  notch, and both 638 m falls.

What `palette/water/open_sea.py` and `palette/styles.py` draw now:

- **A bed continuous in slope.** Within 100 m of the open sea (`OPEN_SEA_BLEND_M`) the
  measured bed is no longer fixed in the membrane. It pulls the membrane towards itself over
  about 25 m (`OPEN_SEA_BLEND_PULL_M`), and is then laid back over the result on a cosine
  taper, from the membrane at the edge to the measured bed 100 m in. Past that it is drawn as
  measured, to the decimetre. One solve does it (`membrane`): the screened membrane with a
  pull per cell, conjugate gradients on the 4 m grid. A pull is a smooth constraint where a
  fixed cell is a hard edge, so the edge of a pull bends the slope and cannot break it. Across
  the swamp seam, the depth now rises from the plateau over about 60 m with no corner, and
  1.47 M measured texels are blended. A measured shelf narrower than 100 m is held near its
  own depth but no longer exactly: a 30 m wide, 8 m shelf beside open sea draws 10 to 11 m.
- **The artwork's tones as depth.** On the landscape's measured ocean each of the artwork's
  three lighter tones sits on one depth band. The median is 5.3 m for `G - R` about 48 (p10
  to p90: 4.9 to 6.6 m), 3.5 m for about 64 (2.9 to 4.6 m) and 1.3 m for about 78 (0.5 to
  2.1 m). The teal, about 32, has a 42 m median and says only "deeper". Where the open sea
  has no bed, a lighter tone pulls the membrane to its depth over 12 m
  (`OPEN_SEA_TONE_PULL_M`); 109,913 texels carry one. The strip right of the chain now draws
  2 to 4 m deep, matching its tone.
- **Pits are not the void past the edge.** No data that does not reach the field's edge
  through no data is a pit; the rest is the void past the world's edge. A floor the fill
  emptied at the north-east corner (3,939, -2,933) belongs to that void. Drawn as a pit, it
  was a black rectangle on the page's navy. The crater and both abyss pits are pits: 135,744
  texels of the 10.84 M.
- **The artwork's falloff.** The artwork lights a pit or the void past the edge at its
  rim, a white line about 2 m wide, and fades from a flat grey (luma 65 to 76) to black over
  about 110 m. Half the light is gone by 37 m, three quarters by 63 m. A Gaussian of 50 m
  (`VOID_FALLOFF_M`) on the void's mask fits that profile to about 5 points. Every style
  draws both the same way: a light rim, then from the artwork's grey to black in a pit, and
  from a lit tone of the page's navy (#424f5a) to the navy itself past the edge.
- **The sea fades into the void.** Beside the open sea the bed runs on under the void
  (338,807 texels), and the void's cover rises from 0 at its edge to 1 over the same falloff,
  with no lit edge and no rim. A pixel takes its sea share from the ocean around it,
  Gaussian-weighted, so a coast where land and sea meet the void changes treatment smoothly.
  Dry ground under the sea's surface within the shore rule's 48 m of the ocean counts as sea
  here, because that rule draws it as sea. In the south-west notch, the cliffs at -100 m
  beside the void had a rim drawn down the middle of the water. Low ground further inland stays
  land: 3.1 M dry texels lie under the sea's level, most of them in the southern lowlands.
- **Rocks in the void.** The void's cover is still kept off a rock standing in it, but only
  where the rock stands above the sea's level. A cliff mesh 250 m down at the bottom of the
  south-west notch drew as a dark green blob before, and as deep water once the sea ran on
  under the void. Now it is the void's, as the artwork draws it. Such a rock is also taken out
  of the height before the water and colours are drawn (`compose._rock_kept`): kept, it still
  counted as land, and the lighting stage shaded the void over rock 250 to 700 m down into
  near-black silhouettes: x 1,500 to 1,760, y -3,750 to -3,150 on the north edge, and beside
  the 638 m fall at (-1,480, 3,000). On no data it is dropped, so the pixel stays the void's;
  where the sea runs on under the void it keeps only the share the void's cover leaves, so
  the sea fades into the void without a break. About 6,800 pixels of the 2048 preview, 970
  of them dark strips in pits, the abyss pits among them.

Measured on the 2048 preview against the same run before the change, 3.5 to 4.2% of the
sheet's pixels changed per style, all of them in the sea, the void or at their edges. The open
sea's pass now takes about 12 s, up from about 8.

The bed is drawing support, not a measurement: the field, its quality byte and the server's
lookups are unchanged, and `--kernel-only` (recipe 2) still draws the page's sea past the
data. The sidecar records the rule and the counts under `water.level_only` and the pits
under `two_regime.fill_rebuild.pits`.

Known limits:

- A measured shelf that runs straight to a landscape component's edge still shows its
  staircase, now as a soft gradient over a few hundred metres rather than a line.
- A pit's edge is the data's: where the field has ground above -200 m beside a pit and the
  artwork draws void, the ground is drawn.
- 681 regions pass, many of them a few texels of deep ground; those inside the land are drawn
  as small pits, grey with a rim.
- In the satellite and relief dark styles the deep sea and the page's navy are still close in
  colour. Beside the land the void now has a lit edge and a rim, but where the sea fades into
  it nothing marks the edge of the world. Whether the void past the edge should leave the
  page's navy, as the artwork's black does, is open.
- The tones are a hint, not a bed: under the teal the open sea is not held deeper than 6 m,
  and a tone's anti-aliased edge against land can read a metre or two too deep.
- The falls off the edge of the world are still left out (section 35).

### Sunken strips between the open sea and the void (2026-10-06)

The third review found a dotted 1 px dark line along the void beside the waterfall_1 islands
(2048 rows 1884 to 1963, columns 440 to 452), and the same on the void's edge in waterfall_0
and on the north edge. Under it lies fill ground 55 to 165 m under the sea's level that the
artwork's mask leaves dry, where the artwork draws a thin land strip and its rim. The coast
rule joins dry ground under the band's top only within 3 m of the open sea, so the rest was
drawn as land, and at 3.66 m to the pixel only some pixels caught it. The game's ocean box at
-17 m covers it, and the 638 m falls pour over that edge. Dry ground under the band's top in a
gap at most 8 m wide (`VOID_STRIP_M`) between the open sea and the void past the world's edge,
measured through that ground, now joins the sea: it takes the open sea's bed and fades into
the void. Ground beside a pit and wider bands stay the land the artwork draws; at 12 m the
rule cut a 15 m spit on the north edge (x 1,460, y -3,700) into pieces. On the whole field
1,316 texels join, all within 8 m of the void, and 23 of the line's 689 texels stay land. The
sidecar records `strip_m` and `strip_texels_joined` under `water.level_only`.

### Drawing less (2026-10-06)

The band loop did arithmetic whose answer it already had. Three places now skip it, and the
tiles are the same bytes:

- **A plane with no holes is sampled without weights.** `sample_plain` reads the province
  mask, the artwork's high pass, every painted plane, the void's planes and the river
  presence: 40 planes a band in the painted layer. It called `resample`, which also sums the
  stencil's weights for the no-data bookkeeping, a second gather and multiply per tap that
  nothing read. It now sums the values alone, in `resample`'s order, from the same zeroed
  accumulators. `reads_nothing` checks the texels under the band's rows first: when they are
  all zero the sample is 0.0 everywhere, so it is not worked out.
- **The void is drawn where it is.** `with_void` blended every pixel of the band. Where its
  cover and rim are both 0 the blend gives back the pixel, so only the pixels under one of
  them are blended. A band with no void under its rows and no pixel without data returns
  before the void's four planes are sampled, and `_sample_water_surface` and `_rock_kept` skip the
  cover there too.
- **Water is mixed where it is.** The terrain and satellite styles (`water_composite`), the
  painted style and the relief styles all end their water with
  `land * (1 - cover) + under * cover`. `wet_mix` works that out only on the pixels whose
  cover is not 0. The painted and relief styles now also work out the colour under the water
  on those pixels alone ("Painting only the wet pixels", below).

`blend_where` in `palette/water/shore.py` does the masking for the last two. It gathers the touched
pixels, blends them with the same expression and writes them over a copy of the input. Past a
share of the band it blends the whole band instead, because gathering is then slower: 2/3 of
the pixels for the void (`VOID_MOST`), 1/3 for the water (`WET_MIX_MOST`). On a synthetic
272-row band 32,768 wide, in milliseconds:

| Share of the band touched | 5% | 10% | 30% | 60% | 100% |
| --- | --- | --- | --- | --- | --- |
| Void, every pixel | 769 | 724 | 800 | 721 | 748 |
| Void, touched pixels only | 70 | 109 | 305 | 561 | 963 |
| Water mix, every pixel | 133 | 120 | 132 | 124 | 126 |
| Water mix, wet pixels only | 29 | 41 | 96 | 180 | 268 |

On the same band, a plane sampled bilinearly from the 1 m grid without the weights took 38 ms
instead of 70.

**Why the bits do not move.** A pixel left out is left as it was. The full blend gave
`x * 1.0 + y * 0.0` there, which is `x` for every finite `y`. All it could change is the sign
of a zero, `-0.0 + 0.0`, and cutting the band to 8 bits drops that. A plane of zeros sampled
the long way gives `+0.0` too, because the accumulators start at zero. Only a `y` that is not
finite would differ: an under-water colour of NaN or infinity on a dry pixel, or a NaN rock
coverage in a band the void's skip passes over. Neither turned up in the checks below, where
either would have changed a hash; a NaN coverage would also have made that pixel's height NaN
before the void is reached.

**Checked (2026-10-06, build 502094).**

- The same 2048 render, all five layers lit with `--workers 2`, before and after: all 1,125
  tiles, the layers' trees, their @2x trees and the light pyramid, are byte-identical by
  SHA-256. The six sidecars differ only in their timings.
- Three full-width windows of the 32768 sheet, 1,024 rows each at rows 0, 14,336 and 31,744
  (the north edge, the middle and the south edge), all five layers, drawn by separate runs
  before and after: all 15 arrays hash the same.

**Cost.** Both versions of the changed functions ran in one process at those three places,
512 rows each, alternating, twice each. Seconds of CPU on one core, the faster run of each,
scaled to the whole 32768 sheet:

| Layer | Before | After | Saved |
| --- | --- | --- | --- |
| terrain | 799 | 744 | 56 (7%) |
| satellite | 769 | 653 | 116 (15%) |
| painted | 1,546 | 1,346 | 201 (13%) |
| relief | 1,038 | 886 | 152 (15%) |
| relief-dark | 958 | 853 | 105 (11%) |
| All five | 5,110 | 4,482 | 628 (12%) |

Every band of the middle window skipped the void, and every band of the two edge windows had
void and pixels without data. The middle window alone saves more, 929 s over the five layers,
so a full render should save between the two: 10 to 15 minutes of drawing on one core. The
machine was running other renders, so single runs spread by up to a third; alternating keeps
that spread out of the comparison. The windowed runs peaked at 8.3 to 8.7 GB either way: a
masked blend allocates the same output array as the full one, plus the touched pixels.

### Painting only the wet pixels (2026-10-07)

The painted style's colour under the water (`underwater`: the bed through the class optics,
the seabed carpet, the sunk crowns, the open-sea term and the opaque area water) and the
relief styles' water (`_water`: the tint, the shore stroke and the sea-edge fade) were worked
out for every pixel of a band, and the mix then kept them only where the cover is not 0.
Both are now worked out on those pixels alone (`palette/water/wet.py`, `WetPixels`):

- The band's wet pixels are those whose water cover is not 0, the pixels `wet_mix` already
  mixed. Their index is taken once a band.
- Every plane of the band the painter reads is laid out at those pixels along one axis: the
  ground's colour, the water's terms, the class optics and their shares, the crowns, and
  what the samplers read (the carpet, the opaque water's weight, the relief's tint). The
  painter then runs unchanged, on one row of pixels instead of a band. Only those planes are
  passed, so a painter changed to read another fails rather than mixes the band's shape with
  the wet pixels'.
- The mix is worked out on the same pixels and written over a copy of the ground, as
  `wet_mix` does. A dry pixel keeps the ground's value, which is what the mix gave it.
- Past `WET_MOST` of the band the water is painted whole, as before: gathering every plane
  then costs more than the dry pixels' arithmetic it saves.

**Why the bits do not move.** Every step of both painters is per pixel: sums, products,
`exp`, `clip`, powers, `where`. None mixes neighbours, so a pixel's answer does not depend on
which other pixels are worked out beside it. The planes keep their dtypes, and numpy works
the same function on every element of an array whatever its length. The colour space
conversions after the mix (`linear_from_oklab`, the tone curve) still run on the whole band,
since a matrix product's summation order may change with the array's shape. The relief's mix
weight is the cover plus half the shore stroke, and the stroke is 0 wherever the cover is, so
its wet pixels are the same. The samplers still read the whole band before the wet pixels are
taken: they belong to the band loop (`render/painting.py`).

**Measured (2026-10-07, build 502094).** Eight full-width bands of the full-size sheet, at rows
1,024 + 4,096k, were drawn by both versions in one process, alternating, twice each, lit by
the sun (`--no-light`); four of them were captured unlit, as a lit render draws them, and
replayed into both versions, whole and in quarters. Every painted, relief and relief-dark
array is the same bits. The colour under the water alone, its samplers aside, the faster of
five replays, in milliseconds:

| Band, share wet | painted | relief | relief-dark |
| --- | --- | --- | --- |
| row 9,216, 18% | 1,292 → 427 | 667 → 175 | 689 → 170 |
| row 21,504, 25% | 1,324 → 479 | 569 → 213 | 565 → 217 |
| row 17,408, 47% | 1,356 → 892 | 614 → 387 | 634 → 395 |
| row 1,024, 62% | 974 → 792 | 583 → 485 | 598 → 487 |

A piece that is all water costs about 1.3 times as much gathered as whole, which puts
`WET_MOST` at 3/4. Over the eight bands the whole painters' CPU fell from 69.5 to 62.7 s
(painted, 10%), 24.7 to 21.7 s (relief, 12%) and 19.4 to 16.0 s (relief-dark, 17%); the
rest of each painter, the ground and the light, is untouched. The eight bands were 18% to
64% wet.

- The 2048 render, all five layers, lit: all 1,131 tiles, light tiles and sidecars have the
  same content as before; the bytes differ in the sidecars' timings only.
- The three windows of the full-size sheet of "One pass for every layer" (section 40), every
  layer, unlit, on 8 threads with no other render on the machine: every array is
  byte-identical. The passes took 67.5, 41.0 and 28.5 s against 68.2, 40.6 and 25.9 s, and
  the run's CPU fell 3%. The painters' saving is a few percent of a band's whole draw, and on
  8 threads the bands wait on memory (section 40, "Known limits"), so the wall time does not
  show it.

Left out: the class optics (`class_optics`) are still mixed for the whole band, in
`render/painting.py`'s band loop, and the shore terms, the river terms, the wet band and the
foam stay whole-band work.

## 39. Compressed raster caches: the zstd band store (2026-10-06)

The render's raster caches (`direct.cache`, `top.cache`, `meshes.cache`, `titan.cache`) were
headerless raw memory maps: 18.5 GB at 32768, more than the 10.7 GB the job estimate and the
README assumed. Each plane is written once, top to bottom in 256-row bands, and every layer
then reads it top to bottom again, rows `[top - 8, top + 264)` per band. Two other reads exist:
the family plane's strided row gather, once per run, and the Titan raster's half-resolution
window. Nothing reads them at random. They are now a zstd band store, 0.93 GB at 32768, which
the render reads directly: nothing is inflated back to a raw file first. The planes round-trip
bit for bit, so the tiles are the same bytes.

### Measured (2026-10-06, the renders-v5 caches, build 502094)

Every band of every plane was hashed before and after.

| Plane | Raw MiB | Band store MiB | Ratio |
| --- | --- | --- | --- |
| `direct.cache/direct.z.f32` | 4,096 | 709.6 | 5.8 |
| `direct.cache/direct.cov.u8` | 1,024 | 3.5 | 289 |
| `direct.cache/direct.family.u8` | 1,024 | 3.4 | 299 |
| `top.cache/direct.z.f32` | 4,096 | 36.4 | 112 |
| `top.cache/direct.cov.u8` | 1,024 | 3.1 | 331 |
| `meshes.cache/meshes.z.f32` | 4,096 | 119.5 | 34 |
| `meshes.cache/meshes.class.u8` | 1,024 | 6.1 | 167 |
| `titan.cache/meshes.z.f32` | 1,024 | 10.2 | 101 |
| `titan.cache/meshes.class.u8` | 256 | 0.1 | 2,249 |
| All | 17,664 | 892 | 19.8 |

Only 11% of the float32 texels are not zero, and the rock heights hold 80% of the compressed
bytes. Writing all nine planes cost 9.9 s of CPU and reading them back in the render's pattern
17.8 s, against 7.5 s for the memory maps once they are in the page cache. A cold read of the
raw set from the hard disk the caches live on runs at 115 to 147 MB/s, so 120 to 154 s; the band
store is 6 to 8 s of reading. The two coverage planes are redundant at one sub-sample, since
a texel is covered exactly where its height is not zero, but they are kept: they cost 7 MB
compressed.

Other codecs on the same planes: zstd without the shuffle 3.8x on the rock heights, lz4 3.3x,
`numpy.savez_compressed` 3.9x and slow. blosc2 with shuffle and zstd reached 5.5 to 6.5x and is
fast with threads, but it is a heavy dependency and 4.14.1 corrupted data with `TRUNC_PREC`
plus `BYTEDELTA`. Compressing whole files at rest and inflating them before a run saves no peak
disk, adds about 2.5 min per run and a second window in which a crash leaves a half-written
cache. Caching less was not worth it.

### The format

- One file per plane, `<plane>.bands`, beside where the raw `<plane>` was:
  `direct.z.f32.bands`, `meshes.class.u8.bands`.
- One zstd frame per band of 256 rows, the rasteriser's own band and one row of 256 px tiles;
  a last band may be short. Level 1, with the content size and checksum in every frame.
- A plane wider than a byte is byte-shuffled per band before compression: every value's first
  byte, then every second byte, and so on. That is what makes float32 heights compress, 5.8x
  against 3.8x. A one-byte plane goes in as it is.
- After the frames, an int64 offset table of bands + 1 entries, then a 48-byte trailer: the
  magic `MGBANDS1`, rows, columns, band rows, the numpy dtype string and the table's length.
  Little-endian throughout. The file is fsynced when it is closed.
- The sidecar's `storage` names the layout: `zstd-bands-v1`, or `raw`. A sidecar without the
  field is a raw cache from before this section. The stamp did not change, so those caches
  still hit. A storage this reader does not know is a miss.

`mapgen/bandstore.py` holds the format: `BandWriter` writes bands in order and commits on
close, and a writer that fails or is closed short deletes its file. `BandArray` reads.

### Reading

`cache.open_plane` returns a `BandArray` for the band store and the read-only memory map for a
raw cache. `cached_raster`, `cached_family` and `cached_meshes` go through it, and the Titan
trees through `cached_meshes`. Nothing that draws changed.

- `BandArray` decodes a band when it is first asked for and keeps the last three. A band
  loop read spans three bands at most, in order, so each band is decoded once per layer.
  A layer drawn on threads (section 40) shares each `BandArray` between them: a lock covers
  the cache and the decoder, one per plane, and while the layer is drawn the plane keeps
  `2 × threads + 2` bands, the bands in flight and a halo band either side.
- It takes a row, a row slice, or an integer array of rows, then any column index. The
  family gather is decoded band by band. Results are read-only, as the memory maps' were.
- It opens the file for each band and holds no handle between reads. Clearing the cache
  through `DELETE /api/maps/cache` is refused while a job runs, so no reader loses a file.
- At 32768 this costs about 10 s more CPU per layer, and about 0.45 GB more memory for three
  decoded bands of each plane: 96 MB for a float32 plane. It saves about 2 min of reading
  from a cold disk.

### A damaged cache

- Opening a plane reads its trailer and offset table, and checks the shape, type and
  table. A truncated, foreign or mismatched file is a miss, and the run rebuilds the cache.
- A band's payload is checked when it is decoded. A corrupt band raises `BandStoreError`
  naming the file and its rows, after deleting the cache's `meta.json`. The run stops with that
  message and the next run misses and rebuilds, rather than stopping at the same band again.
  Checking every frame at open would decode the whole cache, about 18 s at 32768, on every
  run.
- The rasterisers close and fsync every plane before writing `meta.json`. A run that dies
  part way leaves no sidecar, which is a miss.

### Writing

`write_banded_raster`, including the family plane it opens on the first band that carries one,
and `rasterise_meshes`, which also writes the Titan raster, write through `cache.plane_writer`.
Each first removes its planes in both layouts, so a cache never holds both, and records
`storage` in the sidecar. Passing `storage="raw"` writes the old layout, which is how the
check below was run.

### Converting a kept cache

`python -m mapgen compress-cache <dir>` converts a raw cache in place. `<dir>` is one raster
cache, or a folder holding them such as `data/local/maps/_cache/<size>`.

1. Each plane is written beside the raw one, band by band, with a BLAKE2b digest of every raw
   band.
2. Every band is decoded again and compared with its digest.
3. The sidecar is rewritten with `storage`, through a temporary file and a rename.
4. Only then are the raw planes deleted.

A failure deletes what the command wrote and leaves the raw planes and the sidecar as they
were. It refuses a directory with no readable `meta.json`, which is a cache still being
written or not a cache. On Windows it also refuses a cache whose plane another process holds
open, such as a render reading it: it renames each plane to itself and back, which Windows
refuses for an open file. Elsewhere that test finds nothing. A plane left under its `.probe`
name, by a run killed between those two renames, stops the command until it is renamed back.

`--to <dir>` writes the band store there and leaves the source alone. The target must lie
outside the source. A `--to` that is the source, a folder holding it, or a folder inside it
is refused with exit code 1 before anything is written. Links, junctions and letter case are
resolved first, and each cache's own target is checked against every cache of the run. A
target holding raw planes is refused too, since a band store this command wrote has none.
Each line of the report says which happened: "converted in place" with the raw planes
removed, or "written to" the target with the source untouched.

A cache already in the band store is skipped. Raw planes beside its band files are what a
run killed after the sidecar was rewritten leaves. Each is removed only once its band file
decodes to the raw plane's bytes. One that does not match, or has no band file, is kept and
named, and the exit code is 1. Band files beside a raw cache are never read: a conversion in
place rewrites or removes them, and `--to` clears every plane from the target first.

On a copy of the renders-v5 Titan cache, the command took 1,342 MB to 10.8 MB in 4 to 5 s, at
a peak working set of 97 MB, and the decoded planes hash to the raw files' SHA-256. A whole
32768 set should take 2 to 2.5 min, nearly all of it reading 18.5 GB from a hard disk. That is
an estimate from the read rates above, not a run.

### Checked at 2048 (2026-10-06)

The same 2048 render, all five layers with `--workers 2`, ran twice, one after the other, from
the same field, paint and build. The first run forced the rasterisers to `storage="raw"` and the
second used the band store.

- All 530 tiles, 106 per layer, are byte-identical by SHA-256.
- The render sidecars differ only in their timings and the new `storage` fields.
- The nine cache planes decode to the raw planes' bytes. `compress-cache --to` on the raw run's
  caches wrote band files byte-identical to the band run's own.
- The caches are 72.4 MB raw and 6.0 MB in the band store at 2048, 12x. The coarse sheet has
  rock on 31% of its texels, against 11% at 32768, so previews compress less than a full
  render.
- Both runs peaked at 7.8 GB working set. That is the render's own field, lattice and paint
  state; three decoded bands of all nine 2048 planes come to about 29 MB.
- Wall time was 545 s raw and 601 s banded, on a machine running other work. Decoding a 2048
  cache takes well under a second per layer, so the difference is load, not the store.

A full-size render has not yet been compared with a raw-cache render of the same build. The
planes round-trip bit for bit, so identical tiles are expected there too.

### Compatibility

- `zstandard==0.25.0` is in the `gen` extra. `python -m mapgen renders` asks for it with the
  other `gen` modules, and the Maps tab's generation check includes it.
- An older build of the generator does not find the raw files in a band-store cache, so it
  misses and rebuilds raw. The `.bands` files stay beside them until the next band-store run
  or a cache clear removes them.
- The job estimate's cache term, `CACHE_BYTES_FULL` in `domain/maps/presets.py`, is 1.0 GB at
  32768, scaled by area like the rest.
- The light's scratch (section 29, "Scratch") is not in the band store. It is not a cache:
  nothing reads it after the run that wrote it, which deletes it. Its planes are dense and
  compress 2.6 times, not 20, and the bake reads them in blocks with a halo and writes some a
  block at a time from its processes. `--scratch-dir` moves it off the cache drive instead.

## 40. Drawing a layer's bands on threads (2026-10-06)

`render_layer` drew its 256-row bands one after another on one core. In the lit full-size run
measured for the performance plan, the draw took 5,810 s of 11,452, and a band is bound by
memory bandwidth more than by arithmetic: nearly every step allocates a fresh band-sized
array. The bands now run on a pool of threads, several at once, and the tiles are the same
bytes.

### What runs where

- **The bands are unchanged:** 256 rows, with `BAND_HALO` rows either side, cropped ("The
  halo" below). A band on a thread computes exactly what it computed in turn; only when it
  runs changes. 128-row bands were tried in the research and changed pixels, so the band and
  block geometry stay.
- `render_layers` builds what every band shares, once, before any band starts: the ground's
  sources (`_ground_sources`: the arguments, the column taps, the water planes) and each
  layer's job (`painting.layer_job`: its painter's inputs, the satellite noise). The bands
  only read them.
- `_draw_band` writes its own rows of every layer's sheet and its own rows of the light's
  `Surface` (section 29). From a band's thread nothing else shared is written.
- **The seam trace and the regime table** are measured per band on the band's thread
  (`SeamTrace.measure`, `RegimeCoverage.measure`, which touch nothing shared) and merged by
  the caller in band order (`merge`). The pools, the float sums and the order the provinces
  are first seen in are the serial loop's, so the sidecar's numbers are too. `add` is
  `merge(measure(...))`.
- `tiles/drawpool.in_order` runs the pool. Results come back in band order, at most
  `2 × threads` bands are submitted past the one being waited on, and a failure is raised
  when its band's turn comes, after the bands not yet started are cancelled and the running
  ones have finished. On one thread it is a plain loop on the caller's thread: the serial
  path.

### The halo (2026-10-07)

A step that reads its neighbours draws what the whole sheet would only while all it reads
lies within the band and its halo. `render/stencils.py` lists every such step with its reach,
how many pixels away it reads through every step before it. The tests measure each reach on
the code, by moving one row of the step's input and finding the farthest output row that
moves.

| Stencil | Where | Reach, px |
| --- | --- | --- |
| The gradient | `sun_dot`, `slope_degrees`, `surface_direct`, relief's `_shade`, `shore_terms` | 1 |
| Rock tops | `top_cover`: the gradient's normal, then a 3 × 3 mean of its ramp | 2 |
| The water edge's blur | `water_alpha`: a Gaussian of 0.73 m, cut at 4 σ | 0 at 1024, 1 at 2048, 6 at 16384, 13 at 32768 |
| Sunk specks | `sunk_specks`: a 3 × 3 mean over the water's cover | the blur's (at least the shore's 1) plus 1: 14 at 32768 |
| The seam trace | `SeamTrace.measure`, along its row only | 33, no rows |

`BAND_HALO` is the widest reach across rows at 32768, rounded up to whole steps of 8 rows:
16. It was 8, which held every reach up to 16384 (7) but not the water edge at 32768. There
the rows up to 5 from a band edge drew the blur with the band's own edge in it, 6 in the
painted layer, whose sunk specks read one further, and the light's land weight came from
the same cover. Those rows now draw what the whole sheet would. No other row changes, and no
size below 32768 changes at all. A band is now 288 rows instead of 272, about 6% more to draw;
it still spans three stored bands, so the band stores keep as many as before.

One step depended on where a band starts without reading a neighbour: the crowns placed each
pixel on a sprite from the band's first row, in float32, so the wider halo moved them at
every size. They are placed from each pixel's own centre now (light-and-crowns.md section
36, "Drawing").

`tests/mapgen/test_band_halo.py` draws three bands of the full-size sheet over a lake and
compares them with the same rows drawn as one band. They are equal at the new halo, and at 13
for the terrain and satellite layers, the blur's own reach. At 8 the rows within 5 of a band
edge move: one level of RGB, 0.0013 of the light's land weight.

Measured on build 502094 (2026-10-07), with the crowns placed from the pixel centres in both
runs, the halo at 8 against 16:

- The 2048 render, all five layers lit: every tile and light tile has the same pixels.
- Three windows of the full-size sheet drawn unlit (16 bands over the densest water edges, a
  full-width strip, and the first two bands): every layer moves, and only in rows within 8 of
  a band edge, by one level. 3,504 pixels in all: terrain 1,135, relief-dark 995, relief
  537, satellite 514, painted 323.
- The light at full size is not windowed, so it was not measured. Its land weight comes from
  the same water cover and moves in the same rows.
- The peak commit of the windowed draws on 2 threads stayed at 12.1 to 12.5 GB.

The column pieces of the performance plan read the same table: every stencil but the seam
trace reaches as far along a row as across rows.

### The band stores

The draw's threads share each `BandArray` (section 39). Its cache and its zstd decoder are
not safe to use from two threads at once, so a lock covers both: one lock per plane, so two
planes decode at once and one plane decodes one band at a time. While a pass is drawn on N
threads, each plane keeps `2N + 2` decoded bands (`bands_held`), because the bands in flight
span that many stored bands with their halos. The count drops back to three when the pass
is done. At 32768 one stored band of every plane the painted layer reads comes to about
160 MB, so on 8 threads it holds about 2.9 GB of decoded bands; the other layers read fewer
planes, about 125 MB a band.

### How many threads

`--draw-threads N`; by default 8 (`DRAW_THREADS`), and no more than the machine has cores.
Before the pass the count is cut to what free memory holds: the free memory, less one sheet
(3 bytes a pixel; the sheets are files, "One pass for every layer" below) and 2 GiB, over the
peak one band in flight takes. The free memory is the
one `mapgen/pools.py` reads for the light bake and the cutter too. On Windows it is the
lesser of the free physical memory and the commit left, because an array commits its
whole size when it is allocated: with other work running, the commit ran out at 17 GB while
37 GB of RAM stood free, and an allocation failed. One more band in flight costs about
1.9 GB, and 3.4 GB for the painted layer, at 32768 wide, scaled by the width: the working set
measured at 1, 4 and 8 threads below, so the decoded bands it keeps are counted too. The
performance plan had estimated 1.4 and 2.9 GB. A pass paints its layers in turn over one
ground, so a band of it costs its dearest layer's, and 0.6 GB more (`SEABED_BYTES`) for the
second ground of a pass that draws the painted layer and another (`band_bytes`). With nothing
else running, a 64 GB machine draws every layer on 8 threads. `1` draws the bands in turn.
The run prints the count, and every layer's `meta.json` records it as `render.draw_threads`,
beside `cut_workers`.

### What the threads share, audited

| Shared | Why it is safe |
| --- | --- |
| The heights, lattices, water and void planes, and the rasters | Read only: numpy arrays, `r` memory maps, or band stores whose bands are read-only |
| The field | Its planes are decoded when it loads; the water planes are read in `_ground_sources`, before any band |
| A band's ground | Its own band's only. Every layer's painter reads it, and its arrays are read-only, so a painter that wrote to one would fail rather than change what the next layer reads |
| `PaintedGround`, `ReliefGround`, `RiverWater`, `OpenSea` | Built before the draw. The crowns' calibrated sprites, the water classes and the family targets are written in setup, never by a band |
| Random numbers | The satellite noise comes from a seeded generator, once per run in `layer_job`; the moss patches hash each pixel's position |
| numpy's error state | Per thread since numpy 2; the band code sets no warnings filters, which are process-wide |
| Palettes and colour tables | Module constants, read only |
| The sheets and the light's surface | Each band writes only its own rows |

### The GIL

The band's hot calls are numpy and scipy, and they release the GIL. Measured on this
machine (32 logical cores) with one 32768 band, 272 × 32768 float32: eight calls in turn
against eight at once on eight threads.

| Call | In turn, s | On 8 threads, s | Faster |
| --- | --- | --- | --- |
| `(a * b + a) / (b + 1)` | 0.165 | 0.107 | 1.55× |
| `np.where` | 0.114 | 0.037 | 3.1× |
| `np.clip`, `astype(uint8)` | 0.141 | 0.052 | 2.7× |
| `np.gradient` | 0.268 | 0.123 | 2.2× |
| Column gather `a[:, idx]` (the samplers) | 0.399 | 0.061 | 6.5× |
| Boolean compaction `a[mask]` | 0.313 | 0.059 | 5.3× |
| `np.sqrt`, `**`, `np.cbrt` | 1.300 | 0.269 | 4.8× |
| `(N, 3) @ (3, 3)` (OKLab) | 0.267 | 0.073 | 3.7× |
| `ndimage.gaussian_filter`, σ 13 px (the water edge) | 3.907 | 0.676 | 5.8× |
| `ndimage.uniform_filter` 3 | 1.070 | 0.177 | 6.1× |
| `ndimage.maximum_filter1d` (the seam trace) | 0.432 | 0.097 | 4.4× |
| `np.unique` on bytes (the regime table) | 0.175 | 0.036 | 4.8× |
| zstd, one 35 MB band | 0.275 | 0.067 | 4.1× |

None runs at the speed of one thread, which is what a call holding the GIL would do. The
plain arithmetic gains least, because it waits on memory.

What holds the GIL is Python-level looping: the crowns are stamped tree by tree, each stamp
a few small array operations, and the waterfalls and the regime table's provinces loop the
same way. At full size a tree covers hundreds of pixels and the painted layer still draws
2.8 to 3.7 times faster on 8 threads. At 2048 a tree is a few pixels: profiled there, 106,237
stamps took 8.7 s of the painted layer's 24.1 s, and it draws only 1.65 times faster on 8
threads (24.1 s against 14.6 s).

### Measured (2026-10-06, the 32768 sheet, build 502094)

A window of the full-size sheet: rows 12288 to 16384, which is 16 bands across the middle of
the map, and columns 8192 to 24576, half the width so that 16 threads fit in memory beside
other work. It was drawn through the run's own preparation, unlit and without the light's
surface, with every raster the bands read cut to the window and stored as a band store, so
the threads shared `BandArray`s as in a full render. `master` (`9768976`) drew it in turn,
and the threaded draw on 1, 4, 8 and 16 threads, in one process, one after the other.
Seconds, and how many times faster than the threaded draw on one thread:

| Layer | `master` | 1 thread | 4 | 8 | 16 |
| --- | --- | --- | --- | --- | --- |
| terrain | 51.1 | 52.1 | 24.8 (2.1×) | 20.6 (2.5×) | 19.3 (2.7×) |
| satellite | 56.7 | 61.3 | 27.8 (2.2×) | 21.3 (2.9×) | 18.5 (3.3×) |
| painted | 160.7 | 158.1 | 65.0 (2.4×) | 43.0 (3.7×) | not run |
| relief | 57.3 | 57.9 | 23.5 (2.5×) | 19.8 (2.9×) | 21.0 (2.8×) |
| relief-dark | 58.5 | 59.7 | 22.5 (2.7×) | 19.2 (3.1×) | 19.4 (3.1×) |

- **Every array is byte-identical** by SHA-256: each layer's `master` draw and its four
  threaded draws. Run again on `master` `af97dec`, after the code-quality merge, the arrays
  of `master`'s serial draw and of 1, 8 and 16 threads all matched these, and 8 threads
  drew 2.5 to 3.1 times faster than one on a quieter machine.
- Painted on 8 threads and satellite on 16 ran in a second process, when the machine had the
  memory free. Painted on 16 threads needs about 28 GB at half width, so it ran on a quarter
  of the width instead (columns 12288 to 20480): 60.8 s on one thread, 21.8 s on 8 and 21.7 s
  on 16, all three byte-identical.
- The machine ran other renders and tests throughout, so single timings carry some noise;
  each row was measured in one process within minutes.
- **Memory.** The peak working set over the process's own grew by 0.8 to 1.0 GB a thread at
  half width, and by 1.5 to 1.7 GB for the painted layer, the two more decoded bands per
  plane each thread brings included. Doubled for the full width, that is `BAND_BYTES`.
- **At full size.** Per pixel, the five layers take 6,230 s on one thread and 1,980 s on 8,
  about 3.1 times faster. Scaled to the 5,810 s the draw took in the measured full run, it
  would be about 1,850 s, about 66 minutes less. 16 threads would save under a minute more
  for twice the memory, so 8 is the default.

### Checked at 2048 (2026-10-06)

The same 2048 render, all five layers, lit, `--workers 2`, ran from `master` (`af97dec`) and
then with the threaded draw, one after the other, with the field and paint copied off the
data drive. The second drew every layer on 8 threads.

- All 1,131 files are byte-identical by SHA-256: 955 PNG tiles across `tiles/`, `tiles@2x/`
  and `unlit/`, the 170 WebP tiles of the light pyramid, and the six sidecars once their
  timings are left out.
- The raw sidecars differ only in timings and the new `render.draw_threads`.
- The draw took 7.7, 7.8, 20.4, 8.2 and 8.2 s for terrain, satellite, painted, relief and
  relief-dark before, and 3.0, 2.9, 13.6, 3.0 and 3.3 s after.
- Both runs peaked at a working set of 8.0 GB, the run's setup rather than its draw.
- The same pair from the `master` before the code-quality merge (`9768976`) was
  byte-identical too.
- With all four performance changes of 2026-10-06 merged (lean sampling, the light in strips
  with one BLAS thread a worker, the parallel cutter and this threaded draw, `--workers 2`
  setting both pools), the same render against `master` `af97dec`, one after the other:
  all 1,125 tiles and light tiles byte-identical, the sidecars differing in timings and
  `render.draw_threads` only.

### One pass for every layer (2026-10-07)

The layers drew the same ground band by band, each layer again: the heights, the rocks and
the overlay, the water surface, the meshes and the water over them, the borrowed shading.
That was about half of each layer's draw but the painted layer's. A run now draws every
layer it draws in one pass over the bands (`render_layers`), and each band composes its
ground once (`render/surface.py` `band_surfaces`) before every layer's painter colours it
(`render/painting.py` `paint_band`), in the order of `--layer`.

- **Two grounds, where the meshes need them.** Only the render-only meshes in the water
  depend on the style: every style but painted leaves them to the seabed (section 27). So a
  band composes the rest once, and the meshes and the water over them once per rule the pass
  draws: the seabed rule for the other layers and for the light, the painted layer's own for
  it. Without meshes one ground serves both.
- **Read-only.** The ground's arrays are marked read-only before the first painter reads
  them, so a painter that wrote to one would fail rather than change what the next layer
  draws. None does.
- **Measured once.** The seam trace and the regime table measure the one ground, as the first
  layer's draw did before; the sidecars' numbers are the same.
- **The light** captures the seabed rule's ground once, whatever layers the pass draws
  (light-and-crowns.md section 29, "One capture"), and is baked after the pass, before the
  first layer is cut. The progress lines follow: one `draw` stage, then `light`, then each
  layer's `cut` (maps_contract.md §5.3).
- **The sheets wait in files.** Every layer's sheet is drawn before the first is cut, 3 bytes
  a pixel each, 3.2 GB a layer at full size. They are memory-mapped files in the run's
  scratch, `sheets.cache/<layer>.npy` beside the light's (`--scratch-dir`, else
  `--cache-dir`, else beside the renders; `render/sheets.py`), so the system can write them
  out rather than hold them in memory. Each is deleted once its layer is installed, and the
  folder when the run ends. A run that died leaves its sheets to the next, which empties
  them; the sheets of a render still running are refused, exit 11, as its light scratch is.
  The job's disk check counts them (maps_contract.md §4.2).
- `render.seconds_to_draw` in each layer's `meta.json` is the pass's, the same for every
  layer of the run, and `render.draw_threads` the pass's threads.
- `render_layer` draws one layer alone, in memory, as before: the crops and the tests use it.

**Measured** (2026-10-07, build 502094):

- The 2048 render, all five layers, lit: all 1,131 tiles, light tiles and sidecars have the
  same content as before the pass; the bytes differ in the sidecars' timings only. The draw
  took 18.0 s against 27.9 s for the five layers one by one, and the run's CPU 574 s against
  636 s; both peaked at 9.2 GB of commit.
- Three windows of the full-size sheet, every layer, unlit, on 8 threads, the layers drawn
  one by one and then in one pass, one run after the other with no other render on the
  machine: every array is byte-identical. Seconds:

  | Window | One by one | One pass | Ratio |
  | --- | --- | --- | --- |
  | 16 bands over the densest water edges, half the width | 107.5 | 72.3 | 0.67 |
  | A full-width strip of 4 bands | 63.6 | 41.4 | 0.65 |
  | The first two bands, full width | 43.0 | 26.1 | 0.61 |

  The whole run's CPU, its preparation included, fell from 1,167 to 808 s. At the water
  window's rate the five layers draw the full sheet in about 1,160 s against 1,720 s.
- Memory: the peak commit rose from 13.4 to 16.6 GB on the water window and from 12.7 to
  15.6 GB on the strip. The five windows' sheets, held in memory there, are 0.8 and 0.4 GB of
  it; the rest is the second ground, about 0.6 GB a band at full width (`SEABED_BYTES`).
- A full-size sheet in a file filled band by band in 0.85 s against 0.29 s in memory, and
  copied out for the cutter in 0.29 s either way; with 43 GB free nothing reached the disk
  before it was deleted.

### Known limits

- More threads than 8 drew little faster, and relief slower: the bands wait on memory, not
  on cores.
- The decoded bands the threads need, and the bands in flight, are memory the serial loop
  did not take; the thread count is cut to fit, and `--draw-threads` lowers it further.
- The pass holds every layer's sheet until that layer is cut, in files: 3.2 GB a layer at
  full size. Cutting the tiles as the bands finish would drop them, which is the streaming
  step of the performance plan.

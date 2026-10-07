# The drawn renders: layers, sampler, recipes and caches

Sections 17, 20, 25, 26, 39 and 40 of the [design spec](../../DESIGN.md): the drawn base layers, how
they sample the field and the rocks, the recipes they draw by, their raster caches, and
how a layer's bands are drawn. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

Numbers 19 to 24 also name sections of parked.md, residency.md and plumbing.md; in these
files they are the map's ([the document set](../../DESIGN.md#the-document-set)).

## 17. Two more base layers, drawn rather than found (2026-07-31)

The page's first base map was the game's own artwork, cut out of the reader's install by
`mapgen artwork`. The 1 m heightfield made a second kind possible, and the game ships the
ingredient for a third. The drawn layers are `terrain`, `satellite`, `painted` (section 27),
`relief` and `relief-dark` (section 28); `/api/maptiles/{layer}/{z}/{x}/{y}` is how a client
asks for one.

**`terrain`** is the hypsometric map: a green→olive→tan→rock→snow ramp over the 1st..99.5th
height percentile, a north-west hillshade at 45°, and water tinted by its own depth. The colour
is the height and nothing else.

**`satellite`** is the same relief with the colour coming from the game's biome raster.

### The game ships biome geometry after all

`/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel`
is a `FGMapAreaTexture`: `mDataWidth` 4096, `mAreaData` a 4096² array of palette indices, and
`mColorToArea` resolving each index to a `UFGMapArea` object with its bounding box. 37
indices, 17 distinct named areas plus `Area_NoMansLand`. It decodes through the reader this
repository already has, and the region table of section 7.2 is derived from it.

**Its corners are measured.** Nothing in the asset says where its 4096 texels go. Least
squares on per-area centroids, IoU against the heightfield's landscape extent and IoU against
the artwork's ocean colour all have optima hundreds of metres wide: the landscape extent is a
rectangle rather than a shore, and `NoMansLand` is a scope rather than a coastline. The
statistic that works asks **does an area boundary land on something the map draws?**
`calibrate_biome` takes the artwork sheet's edge strength averaged over the raster's boundary
texels, divided by its edge strength everywhere. At the in-game map square that ratio is
**1.97**; at ±600 m in any direction the best rival is **1.33**, at 5% larger **1.28** and 5%
smaller **1.24**. So the biome raster spans exactly the square the artwork does (4096 texels
over 7500 m, 1.831 m to the texel, row 0 north), and every run re-measures it and refuses to
claim the pin if the margin goes.

`region_table_is_current` checks that the committed region table was cut from this build:
anything under 100% means another build, and the run names the command that fixes it.

### The palette is designed, and the shipped one is not used

`mColorPalette` has 37 RGBA entries and they are a minimap legend: flat primaries, cyan,
magenta, pure white. Drawing them would produce a highlighter sketch of a world. So they are
decoded, recorded in `_meta` for a reader to see, and ignored; `BIOME_COLOURS` is a table
written by eye against crops, desaturated and capped well below white, and a test holds it to
both of those. Over it go four rules in an order that is the argument: the biome says what
grows there, the **slope** overrules it because nothing grows on a cliff face, the
**altitude** bleaches what is left, the **hillshade** lights rock and canopy alike, and the
water goes on top because it is a different surface rather than a different ground. Two
octaves of fixed-seed value noise, sampled by world position, keep the flats from being flat
fills without drawing a band edge.

The area polygons meet along a mathematical line, so the colour field is Gaussian-blurred by
24 texels (≈44 m, `BIOME_BLEND_TEXELS`) before it is sampled: a tree line's width, narrow
enough that a 300 m biome keeps its own colour in the middle. The shoreline is feathered
twice: over 0.9 m of depth (`WATER_EDGE_M`), which handles a beach, and by a 0.73 m blur
(`WATER_EDGE_BLUR_M`), which handles the many box-shaped bodies against a cliff that have no
depth band to blend in. The blur is in metres, so the sheet's size does not change how much
ground it means.

### Layers on the serving side

* **`/api/maptiles/{z}/{x}/{y}` is an alias for `map`.** Not a redirect and not a
  deprecation: the live page addresses the base map there, every cached tile is keyed on it,
  and the layered route is four segments where that one is three, so they cannot collide.
  Both go through one `_serve_tile`.
* **A layer is a directory.** `map` keeps `data/local/tiles/`; renders live at
  `data/local/renders/{layer}/tiles/`, cut on the same frame at the same 256 px into the same
  `{z}/{x}_{y}.png`. Switching layers is switching one path segment, not the CRS, the bounds
  or the zoom range.
* **Every header is that layer's own**, read from the sidecar beside its own tiles: depth,
  tile size, corners, build. The build tag folds the layer's name in, so two pyramids that
  agree on every recorded number still cannot share a cache key and serve each other's
  `immutable` tiles.
* **An unknown layer is a 404 listing the ones there are**, not a 422 about a path parameter,
  and it never becomes a filename: the string is looked up in `_layer_dir`, which answers
  `None` for anything that is not a name this module wrote down.

### Borrowing the artwork's shading where the field's province is coarse (2026-07-31)

The 1 m grid is one resolution and **not one accuracy**. On the current field 45.39% is
landscape, continuous geometry the game evaluates itself, which genuinely sharpens; 20.97% is
cliff, rasterised from the rock meshes; 13.49% is fill, the 3.66 m interface raster (the table
in section 26). Since heightfield v3 the cliff province is the Nanite leaf and carries detail
of its own (section 22, "The planes on disk"). The borrow still covers it: its gain was picked
on a hull-built field, and re-picking it is a separate change with its own before-and-after.
`BORROW_PROVENANCE` names both cliff values, 4 and 5, because 73% of the cliff province is 5
and listing only 4 would withdraw the borrow from three quarters of it.

Over those two provinces, and only there, the render borrows the artwork sheet's **luminance
high pass**: the game's own 8192 px map, decoded from its four BC1 slices in the same run,
minus its own Gaussian blur at σ = 8 px (`BORROW_DETAIL_SIGMA_PX`, so only detail finer than
about 7 m crosses), multiplied into the shading and faded out on the provenance byte over 6 m
(`BORROW_FEATHER_M`, so the label's edge is not drawn as a coastline of shading). It is light,
never colour (`BORROW_LUMA`, Rec. 601): an ocean drawn blue contributes its brightness and
nothing else. Where the provenance says landscape, the shading is the field's own.

**The drawn map has strokes**: every rock formation is outlined in hard dark ink, and
multiplied straight in they come out as a line drawing laid over terrain. So a grey closing
and then an opening 7 artwork pixels wide (`BORROW_INK_PX`) first remove every stroke narrower
than that, dark or light; plateau steps and rock shading are wider and stay (5 px left a dotted
trace). The high pass is then softened by 1.6 px and squashed through `tanh` at 1.2σ, a soft
clip that passes the mid-tones almost linearly and saturates the outliers. **The gain was
picked by looking**: at 0.17 an offshore cliff island is still eight flat plates, at 0.50 the
artwork's contour rings read as rings; 0.30 (`BORROW_GAIN`) is where a collision hull becomes
rock and the dune field, landscape and untouched, still looks exactly as it did.

The same run reads that sheet for the biome calibration, so the pin is scored every run.

### Two tile trees, and the second one is the same grid

`tiles@2x/` holds the identical tile **grid** at 512 px a tile: level z is still 2^z tiles a
side over the identical squares of the world. A hi-DPI client asks for the same
`{z}/{x}/{y}`, adds `?px=512`, and draws the answer into the same CSS box: twice the pixels
in each direction, no change to the CRS, the bounds, the zoom range or the tile size. Section
20 says why it tops out at z5.

The probe advertises both depths (`X-Map-Tile-2x-Px`, `X-Map-Tile-2x-Max-Z`), absent when a
layer has no such tree, and a request for a density a layer does not have falls back to the
1x tile, which every client can draw at any density. Both trees' numbers ride in one cache
tag, so recutting either changes every URL of that layer.

The client picks it at `devicePixelRatio >= 1.5` rather than `> 1`: a 125% Windows scale
reports 1.25, where the @2x tile is 60% more pixels than the screen can show. It is read once
at probe time and not watched; a window dragged to another monitor is a reload, the same
bargain the CRS already makes.

### Cutting in parallel, and the proof that it is the same bytes

A lit layer writes three trees, `unlit/`, `tiles/` and `tiles@2x/`: 45,055 tiles at 32768.
`mapgen/tiles/cutter.py` cuts them through one pool:

- **One encode pool per layer.** `--cut-workers` processes (default one per logical core, at
  most 24) encode one row of tiles per job, for all three trees. A level's rows are queued as
  soon as its pixels are ready, so the pool encodes the unlit z7 while the levels are
  resampled and the sheet is relit. `--workers N` sizes both this pool and the light bake's
  where `--cut-workers` or `--light-workers` is not given (section 29).
- **One copy of the sheet per tree.** The sheet is copied into a `shared_memory` block and
  z7 is encoded from that block; no Pillow image of the whole sheet is made. The unlit tree
  encodes from its own block, so the relight can change the sheet underneath it.
- **Encoders without numpy.** The encoder (`core/gameassets/pyramid.encode_tile_row`) reads
  the block through Pillow. numpy's BLAS thread pool commits about 0.75 GB in every process
  that imports it, 18 GB for 24 encoders, and on a busy machine that exhausted the commit
  limit with physical memory to spare.
- **Levels in strips, on threads.** Each level is one Lanczos downscale of the whole sheet.
  Eight threads compute it in strips of about 128 MiB of source: each strip resizes a row
  range with a `box`, out of source rows cut with a halo of 3 × scale + 4. Pillow derives each
  output row's taps from its position alone and reaches 2.5 × scale + 0.5 source rows either
  side, so every strip is the same bytes as those rows of a whole-sheet resize. Measured at
  scales 2 to 128; a halo of 2 × scale fails at every scale. The threads run in the parent,
  because Pillow releases the GIL inside a resize.
- **A level two trees share is resampled once.** `tiles@2x/` is cut from the sheet
  downscaled to 16384, the same resize as `tiles/` z6, so its top level is that block. On a
  sheet of 16384 or less every @2x level is a 1x level.
- **Memory.** Before a new block the cutter checks free memory (`mapgen/pools.py`, shared
  with the light bake), physical and on Windows commit; with less than the block plus 4 GB
  it waits for work in flight. The encoder count is capped the same way at 200 MB an encoder.
  A block is freed when the last job and strip reading it finish. A failed strip settles its
  level only after every strip of it has stopped, so no strip writes into a freed block.
- **Serial.** `--cut-workers 1` cuts one tree at a time with `install_pyramid`, the
  reference the parallel path is compared with.

The 15 to 26% of z7 tiles that are one void colour are encoded like any other: a one-colour
tile takes 0.5 to 0.7 ms against about 34 ms for a land tile, so caching their bytes would
save under 1%.

`--check-parallel` proves it rather than asserting it: the artwork's whole pyramid cut both
ways, SHA-256 of every tile compared name for name, recorded in the sidecar; a mismatch
refuses the run (exit 5). The three trees of one lit layer at 32768 cut in about 125 s on 24
encoders, at a peak working set of 12.9 GB; each tree holds about 6 GB, and when memory is
short the cutter runs them closer to one after the other.

### Deflate level (2026-10-06)

Every tile PNG, serial or parallel, is written at zlib level 6 (`pyramid.TILE_PNG`) instead of
`optimize=True`, which is level 9 with a search over the filters. It is lossless either way,
so a tile decodes to the same pixels; only the file differs. On 460 z7 and @2x tiles of five
layers, re-encoded from their decoded pixels, level 6 takes 1.7 to 12.4 ms of CPU a tile,
5.8 to 7.0 times less, for 6.1 to 7.1% more bytes. A lit layer's three trees, about 3.2 GB at
full size, grow by about 0.2 GB. The owner chose the trade on 2026-10-06.

### And a swap Windows can refuse

`install_pyramid` renames the finished tree over the old one so a reader meets a whole
pyramid or none. **Windows will not rename a directory anything has open**: an Explorer
window sitting in `tiles/`, the search indexer, a backup agent. That is the ordinary state of
a directory a person has been looking at. So there is a second-best, and it says it is
second-best: the swap is done **one level at a time**, each level renamed atomically over its
predecessor, and a reader who catches the middle sees every level present with some still the
old cut rather than a level missing. Which of the two happened is recorded in the sidecar as
`tiles.installed_by`.

### Why a new tool rather than a stage on the heightmap generator

`mapgen heightmap` gets a field out of the game: it sweeps the cooked packages, rasterises the
meshes and validates against the resource nodes. `mapgen renders` reads that finished field
back through the codec any consumer uses, adds inputs the heightmap command never reads, and
writes pictures. Bolting them together would couple a slow extraction to every render and
give one `--force` two meanings. What they share is shared by import: the codec from
`domain.spatial.heightfield`; the pyramid cutter, its staging rename and its refusals from
`core.gameassets.pyramid`; and the rock sweep and rasteriser from `gamedata` (section 20). The
frame itself (the corners, the sheet size, the artwork the biome pin is scored against) comes
from the artwork command, because that is the command that measured it.

### The artwork command, piece by piece (2026-10-05)

`mapgen artwork` is split by concern. `commands/artwork.py` holds only the arguments, the order
of the stages and the refusals.

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
pyramid, and it is the one file a reader can open and look at. Each tree is staged and renamed
into place on its own, so the pair is never half-swapped and a failure in the second leaves the
first where it was.

The @2x tree is never enhanced. @2x level z holds the same pixels as 1x level z+1 in tiles twice
the size, so an `--enhance` run's z6 and z7 are already reachable: the @2x tree tops out a level
sooner and the client asks for the 1x tile above it, which is the fallback `_tile_tree` was
written around. Cutting @2x from upscaled pixels would be a second GPU pass for resolution the
reader can already get.

**The sidecar** (same module). `map.json` carries the four corners the endpoint reads, plus
`_meta`. `tiles_2x` is left out entirely when there is no such tree, rather than written as a
record saying "absent": `_map_pyramid` answers `max_2x_z: None` for a layer with no block, which
is how `_tile_tree` knows to serve the 1x tile to every client. The renders write the same shape
for the same reason. The staleness guard reads the sidecar back: a picture or tree from another
build is refused (exit 3), and so is a run whose enhancement recipe is behind the one on disk
(exit 5). That second rule compares recipe numbers, not the `enhanced` boolean: enhancing a
plain pyramid is an upgrade, the same recipe is a refresh, a later one an upgrade again, and
only an earlier recipe over a later one is refused. The usual case is a plain re-run over a
sharpened tree, which would halve the map's usable resolution and say nothing. A sidecar with
only `enhanced: true` was cut by recipe 1, and a recipe value that is not a positive whole
number falls back to the boolean. The recipe numbers and their words live in `tiles/recipes.py`
beside the render recipes.

**The enhancement** (`enhance/`). `--enhance` adds z6 and z7, two more zoom levels than the
artwork has pixels: 8192 px is about 0.9 m to the pixel, and a factory is machines eight metres
across. It runs the sheet through Real-ESRGAN 4x on the GPU. It is off by default, because it
needs a 45 MB binary this repository will not vendor and a Vulkan device.

- `enhance/upscaler.py` downloads the binary once into the user cache, checks its digest before
  unpacking, smoke-tests it on its own sample image, and checks that numpy and scipy come from
  one environment.
- `enhance/pixels.py` holds the three passes around the model, each with what it repairs at its
  definition. `presharpen` sharpens the input; off its mask it is exactly the identity, so it
  cannot leak onto a fill. Its band ends at `PRESHARPEN_HI` 10 against the faint mask's
  `FAINT_HI` 14: decoupling the two costs 0.03 of weak-stroke retention and buys a mid
  retention of 1.13 against 1.07. `faint_mask` is the hybrid repair, which blends two
  renderings of the same source square. Its passive grow takes a pixel only when more than 3
  of its 8 neighbours are in, so a mark thickens and a lone speck does not spread, and
  `faint_depth` reads the source luma alone, so every result can be reproduced without any
  model output. `colour_fix` works at the output's resolution, with the source Lanczos'd up to
  meet it (blurring the source small and stretching it drifts half again as much); strokes are
  4 to 8 px at 4x, hence `COLOUR_FIX_SIGMA` 6.
- `enhance/levels.py` cuts the source squares, runs the model and tiles the result. The squares
  in `in/` are pre-sharpened and are the model's input only. Both repairs re-cut the untouched
  source from the sheet, because they correct towards the source, and correcting towards a
  sharpened copy corrects nothing. The colour fix runs before the core is cropped out of its
  overlap: its blur reaches about 25 px, and a core cropped first would have no neighbour to
  reach into, which is a seam in the making.
- `enhance/checks.py` re-measures every claim into `_meta.tiles.enhancement`: the tile seams
  against boundaries that are not seams, and the low levels against the enhanced pixels they
  did not come from. The retention figures stay in those records' own words.

The subpackage is its own unit rather than part of `palette/`, because it is not a style: it
changes the artwork's recipe number, not a palette digest. It sits above `tiles/` in the import
order, because it reads the recipe table there and cuts its levels with the same pyramid code.

## 20. The two-regime sampler, and a smoothed z7 (2026-07-31)

The renders are 32768², z0..z7, 0.229 m to the pixel, and they draw the rocks from their
triangles rather than from the field.

### The ragged rim was never the geometry, and it was not the density either

Heightfield v3 put the Nanite leaf under the cliff layer, 11.6 times the triangles (section 22,
"The planes on disk"), and the rims on every rock did not move by a pixel. They could not: the
field is a **1 m max-Z fold**, and a rim drawn from it is a 1 m staircase however fine the
triangles folded onto it were.

So the renders read the **triangles**, by *importing the code that writes the field*:
`sweep_levels`, `read_mesh_geometry`, `rotation_matrix`, `winding_sign`, `MaxZRaster` and all
of the placement culls are called, not copied. The sidecar's `cliff_geometry.placements_dropped`
counts each cull, `excluded_mesh` among them: 376 placements on build 502094, the passable
`CliffPillar_03`, which the render-only mesh pass draws instead. The only thing the render
changes is the grid they are pointed at, its own 32768², 0.229 m to the texel, so a difference
between the render and the field is one of spacing rather than of rasteriser. About 216 M
triangles over 20,233 placements are rasterised once, banded at 256 rows, into the direct cache
(a zstd band store, section 39) that every layer draws from. Two rules make that draw smooth.

### One: the kernel has to interpolate the lattice, not the fold it produced

Interpolating the composed field over a rim reconstructs the *fold*: a texel just outside a
rock is still a cliff-top height, because a cliff-top texel is one of the four the stencil
reads. The drop stays where the 1 m lattice put it, at any output resolution, and z7 would draw
that staircase *more sharply* than a bilinear upscale of z6.

So the kernel is given the surface **underneath**: the landscape and fill lattices, continuous
geometry with the cliff province taken out (`ground_lattice`). The rocks are then composited
onto that at 0.229 m, by the heightmap command's own composition rule performed at the
render's spacing.

### Two: the density plane is provenance, not a gate

`prov == 4` means *the rasteriser reached this texel by interpolating a triangle wider than
itself*, which is `density == 0` **by construction**. On the 234 m window with the highest
cliff fraction on the map, **0.00%** of texels have one source vertex under an output texel at
z7. No weight built from that plane can reach those rims however it is shaped.

So what decides that the rocks are drawn is **their own coverage of the pixel**, the same rule
the field uses one metre coarser. The rocks raise the ground and never lower it, through a
smoothed positive part (`DIRECT_LIFT_KNEE_M`, a quarter of a metre), so the line where a rock
meets the ground is not a derivative discontinuity the hillshade would draw around every
formation. The density plane decides what to **call** what was drawn, a measurement or the
plane of a triangle wider than a texel, and `_meta.render.two_regime.regimes` counts both, per
province, every run.

### The seam, measured along a line

`SeamTrace` takes `|d²z/dx²|` along every row of every band, over the whole sheet, and sorts
each 3-texel stencil into pools by the weights under **all three** of its texels: straddling
the join, wholly direct or wholly kernel. A trace, because probes miss a ridge one texel wide.

The number to read is `share_of_a_hard_switch`: the join's p99 curvature over the p99 of the
height a hard `max` would draw over the same stencils. It is a description, not a gate. A
convex blend of two surfaces cannot be rougher than the switch between them, so for a convex
blend it never exceeds `SEAM_SWITCH_CEILING` (1.0); a full render reads 1.22 because the
smoothed lift is not a convex blend (section 26). Two more ratios are recorded because they
were tried: against the pure regimes beside the join, and against the terrain where the two
surfaces agree within half a metre (`SEAM_SAME_SURFACE_M`). Once the composition is by
coverage both measure the terrain, because every join lies on the rock's silhouette or its
base, where large curvature is the right answer. Pooling around `w = 0.5` would measure
nothing either: a feather's second derivative is zero at its own midpoint. What keeps the seam
smooth is the arithmetic, a convex combination in a continuously reconstructed coverage plus
a positive part smoothed to C∞.

### z7: what it is, and what it is not

The sampler is **C1**: Catmull-Rom (cubic convolution, a = −1/2) for recipe 2, PCHIP since
recipe 5 (section 26). Bilinear is C0, its derivative jumps at every texel boundary, and the
hillshade is a function of the derivative, so sampling the field below its own spacing with
it rules the relief into squares. Where the 4×4 stencil is not whole the 2×2 answer is used,
because a cubic kernel straddling a hole overshoots.

Doubling the sampling of a 1 m field finds no new world, and z7 is **not** sold as more
information; `_meta.render.z7` says so. Measured on the v3 field over four 512 m windows chosen
by rule (the two squares with the most cliff and the two with the most landscape), with the
artwork's borrow held out:

| Window | High-frequency energy per pixel, 16384 → 32768 | Change per doubling, 8192→16384 / 16384→32768 |
| --- | --- | --- |
| cliff-1 | 3.05 → 1.50 | 2.92 / 2.82 |
| cliff-2 | 3.98 → 1.94 | 3.83 / 3.34 |
| landscape-1 | 0.332 → 0.200 | 0.286 / 0.307 |
| landscape-2 | 0.301 → 0.186 | 0.260 / 0.264 |

Energy is above a 3×3 box low pass and change is the mean absolute difference between the
two sizes, both per channel in levels of 255. A new level would have needed the energy to
**rise** at 16384→32768 on the cliff windows and not on the landscape ones; it halves on both
cliff windows. The change per doubling has all but stopped falling on the cliffs, and on the
landscape too, which nothing here touches: a sampler artifact, not new world. The Nanite leaf
adds 2 to 4% of energy at every size on the cliff windows and changes neither direction.

What z7 is, is smoothness the client cannot produce. A browser shown z6 at twice its scale
upsamples it **bilinearly**, so the relief comes out ruled into 0.458 m squares: the failure a
C1 sampler exists to avoid, moved into the viewer. A z7 tile is the same surface evaluated by
the same C1 kernel at half the spacing. Over the cliff province it *is* new information,
because there the pixels are triangles rather than a reconstruction of a fold of them.

### The @2x tree stayed at z5

Cutting it from the 32768 sheet would give it a z6 of 512 px tiles weighing about as much as
the entire 1x pyramid, for pixels a hi-DPI client already gets: Leaflet's own retina path asks
for `z + 1` at 1x and draws it at half size. So the dense tree is cut from `RENDER_2X_PX`
(16384), and a test asserts the two sizes separately.

### Refusals

A run that cannot draw what its sidecar would claim is refused, with its own exit code, before
it writes. The codes are constants beside the stage that raises them.

| Exit | Constant | When |
| --- | --- | --- |
| 1 | `render/inputs.NO_CONTAINER` | the game's container is not at `--game` |
| 3 | `render/inputs.STALE_LAYER` | a layer folder holds tiles this run cannot show were drawn from the field on disk; `--force` replaces them |
| 4 | `render/inputs.NO_FIELD` | there is no heightfield |
| 5 | `render/inputs.PARALLEL_MISMATCH` | `--check-parallel` found the parallel cutter's bytes differ from the serial one's |
| 6 | `render/inputs.NO_DENSITY` | the field has no `density.u8.z`, so it cannot say which cliff texels are measurements; the message names the generator version that writes one, and `--kernel-only` draws without the geometry |
| 7 | `render/cached_rasters.UNREADABLE_RASTER`, `terrain/render_meshes.RASTER_UNREADABLE` | a raster just written does not read back (section 39) |
| 8 | `render/inputs.NO_PAINT` | the painted layer is asked for and there is no paint store |
| 9 | `render/inputs.RESTYLE_MISS` | `--restyle` and a kept raster cache is missing or was cut for another size, sub-sampling or build |
| 10 | `render/inuse.IN_USE` | the output folder holds a map type the server's registry lists; `--overwrite-in-use` writes anyway |
| 11 | `render/light.SCRATCH_IN_USE` | the light's scratch is held open by a render still running (section 29, "Scratch") |
| 1 | `commands/renders.CUT_FAILED` | the tiles could not be cut into place |

A raster cache carries the size, the sub-sampling and the build it was rasterised for, so a
cache from another render is rebuilt, not refused. `--kernel-only` is the honest way to draw
without the geometry: it draws **recipe 2 whole** (no rocks, no lattice split) and records that
recipe number, so a before/after against it compares two recipes rather than one recipe
against half of itself.

## 25. Renders after the terrain work: recipe 4 (2026-10-05)

Three ideas from the terrain study (section 22) were tried on the map renders, on 176 m crops
of a cliff, a boulder field, two arch sites and sloped landscape. Build 502094.

| Idea | Verdict | Evidence |
| --- | --- | --- |
| Read the landscape from `terrain.u16.z` (7.8 mm) instead of the decimetre ground plane | **Adopted** | The 0.1 m steps drew contour-like terraces on every gentle slope. On five crops the slope error against the exact surface fell from a median of 2.6° to 0.2–0.3°, p99 from 6.6–29° to 1.6–4.9° |
| Use the engine's two triangles per quad instead of the C1 kernel | **Rejected** | Exact in height, but the hillshade shows every 1 m triangle as a flat facet. Catmull-Rom over the same samples departs from the triangles by a median 0.9–1.6 mm (p99 13–35 mm), so it does not round creases off visibly. Shading with the game's interpolated vertex normals removes the facets but softens every ridge; it is not used either |
| Draw the arches and foliage boulders the field keeps in `top.i16.z` | **Adopted** | 1,076 arches and 41,351 boulders were missing, and the artwork borrow drew the arches as faint ghost stripes with nothing under them. With the overlay drawn, the render's correlation with the game's own map sheet rises from 0.18 to 0.33 and 0.22 to 0.30 on the two arch crops, and is unchanged elsewhere |

The direct pass samples at `col + 0.5` on the frame's corner, the pixel centre;
`test_the_direct_pass_applies_the_field_s_own_culls_and_lands_where_it_says` pins it.

### What recipe 4 draws

- **The lattice under the kernel is the bare landscape**, wherever the landscape has a sample
  and the province is landscape or cliff. Under a rock this replaces the hole that fell back
  to the 1 m fold and drew a dark staircase ring around every formation. The fill province is
  rebuilt by recipe 5 (section 26).
- **Rock pixels are gated on triangle coverage at the pixel centre**, never on the density
  plane, which only labels the regime statistics. A covered pixel is the triangle's own height
  even where the triangle is wider than the pixel. No tent or blur runs over the heights, which
  spread rock up to 0.23 m across every silhouette; sub-samples (`--direct-subsamples`) are the
  only antialiasing.
- **Arches and boulders are a second direct pass**, at their finest decoded geometry (Nanite
  for five of the six boulder meshes, LOD 0 for the sea rock), composited last by the same
  coverage and smoothed lift as the rocks. These are the visible meshes; planning heights use
  the collision surface instead (section 24).

### Known limits

- The artwork borrow still multiplies the drawn map's arch strokes into the shading, so a
  faint ghost stripe can sit beside an arch where the drawing and the mesh disagree.

## 26. Rebuilt base data and a PCHIP sampler: recipe 5 (2026-10-05)

Recipe 5 rebuilds the rest of the lattice the kernel reads, and swaps the kernel. Rocks,
arches and boulders are drawn as in section 25. Build 502094.

### What changed

`terrain/fill.py` builds the lattice once per run, in about 13 s, before any band is drawn:

| Texels | Source | Share of the field |
| --- | --- | --- |
| Landscape | `terrain.u16.z`, unchanged | 45.39% |
| Cliff province | the field's own heights, copied unchanged | 20.97% |
| Fill | the float16 interface raster: Gaussian with sigma 1 texel, then cubic, then +1.0 m | 13.49% |
| Fill within 48 m of the landscape | as above, plus the landscape's residual carried in by a harmonic solve and a cosine taper | 0.25% |
| Interior holes | biharmonic fill, or harmonic where the biharmonic leaves its border's range by more than 2 m | 0.005% outside the cliff province (35 holes, 14 harmonic, nearly all ground under rock) |
| Pits: no data and ground below -200 m the artwork draws as void | left empty, drawn as the void | 0.55% (681 regions; 210,086 texels of ground) |
| No data out to the field's edge | left empty: the open sea or the void, as the artwork has it | 19.35% |

Shares from the 2048 preview of 2026-10-05.

- **No de-terracing.** The interface raster is float16, with steps of 0.24–0.49 m, so there
  is nothing to de-terrace; the blocks once on screen were nearest-neighbour 3.66 m cells.
  `check-fill` keeps recipe 4's de-terracing only as the baseline its seam check is scored
  against.
- **Rock is never a constraint.** A hole next to a rock, or the ground under a rock with no
  landscape sample, is filled from the ground around it. The rock's own heights stay in the
  cliff province and are composited by coverage.
- **Water over the fill province** is not read against the raster: the raster stores the
  water surface there, so the ground under it is not a sea bed. At the ocean's level it gets
  the open sea's bed (below).
- **Holes under water** stand at least 0.5 m below their own water surface.

The **sampler** is tensor-product PCHIP with Fritsch-Butland slopes: the harmonic mean of the
two secants, or zero where they disagree in sign. It uses the same 4x4 stencil and the same
separable passes as Catmull-Rom. Each 1-D pass stays inside its interval's endpoints, so the
result never leaves the range of its own 2x2 cell. Where the stencil is not whole, the bilinear
fallback is unchanged. The water level is sampled the same way, and the satellite layer's slope
rule reads the same heights. `--kernel-only` (recipe 2) still uses Catmull-Rom.

### Measured

`python -m mapgen check-fill` runs these checks through the shipped functions; only the
baselines are emulated. It refuses a field whose `terrain_grid` shares no vertex with the
field's grid.

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
half and is scored on the east. The seam trace's share of a hard switch is 1.22 (section 20).

### Known limits

- The 1 m staircase along the water edge is unchanged, because the water quality plane is 1 m.
- The rebuilt lattice is not stored. The server's height lookups still read the field as
  generated.

### The open sea, the void and the pits (2026-10-05)

Past the landscape the field has no data, and where it has water at the ocean's level it has
no bed (section 19). Drawn as it stands, the sea split into straight-edged blocks in every
style: level-only ocean (13.1% of the field, 95% of it over the fill) sat at the deep end of
every ramp beside measured ocean with its own depth, so every landscape component's edge was a
colour step; every no-data texel was one navy, though the artwork draws 86% of them black, the
void past the world's edge, and others as sea; and the pits were filled flat. What
`gamedata/water/channel.py`, `terrain/fill.py` and `palette/water/open_sea.py` draw:

- **The artwork says sea or void.** `artwork_planes` classifies the decoded sheet on the 1 m
  grid: water is the water channel's own test (`B - R >= 25`), void is anything else with a
  Rec. 601 luma of 110 or less. The pits are black to a flat grey (luma 0 to about 80), the
  ground beige or white (140 and up). Its water comes back as one of the artwork's four flat
  tones, split at `G - R` of 40, 56 and 70 (`WATER_ARTWORK_BANDS`).
- **A pit stays empty.** A region of no data and of ground below -200 m is a pit when at least
  half of it is void in the artwork (`pit_mask`): 0.88 to 0.95 for the crater and the abyss
  pits, 0.5 to 0.7 for a crack under its white outline, 0.44 and less for holes drawn as
  ground. A pit is taken out of both lattices, so it is drawn as the void; the ground under
  rock beside it is still filled, bounded by it, unless the pit walls it in.
- **The open sea gets a bed.** Under no-data texels the artwork draws as water, and under
  level-only water within 1 m of the ocean's level, `open_sea` writes a bed into the lattice
  the run draws. It is a screened Poisson membrane on a 4 m grid: the surface at a dry coast,
  settling to 60 m (`OPEN_SEA_DEPTH_M`) over about 400 m away from everything. A coast is dry
  ground standing at least 1 m above the top of the ocean's band (-16 m) in the field as
  stored; dry ground at or under that top is sea the artwork's mask left dry and joins it.
  7.44 M texels get a bed, and so do the 338,807 void texels the sea fades into beside it
  (below); the pass takes about 12 s.
- **Continuous in slope.** Within 100 m of the open sea (`OPEN_SEA_BLEND_M`) the measured bed
  is not fixed in the membrane: it pulls the membrane towards itself over about 25 m
  (`OPEN_SEA_BLEND_PULL_M`) and is laid back over the result on a cosine taper, from the
  membrane at the edge to the measured bed 100 m in. A fixed cell is a hard edge where a pull
  is a smooth constraint, so the hillshade and the depth tint have no kink to draw; a fixed
  edge drew a 670 m line east of the swamp. One solve does it (`membrane`): the screened
  membrane with a pull per cell, conjugate gradients on the 4 m grid. A measured shelf
  narrower than 100 m is held near its own depth but not exactly: a 30 m wide, 8 m shelf
  beside open sea draws 10 to 11 m.
- **The artwork's tones as depth.** On the landscape's measured ocean each of the artwork's
  three lighter tones sits on one depth band: medians 5.3 m for `G - R` about 48 (p10 to p90
  4.9 to 6.6 m), 3.5 m for about 64 (2.9 to 4.6 m) and 1.3 m for about 78 (0.5 to 2.1 m). The
  teal, about 32, has a 42 m median and says only "deeper". Where the open sea has no bed, a
  lighter tone pulls the membrane to its depth over 12 m (`OPEN_SEA_TONE_PULL_M`), so a strip
  the artwork draws shallow, such as the one closed in by the waterfall_1 islands, is not
  drawn deep.
- **Pits are not the void past the edge.** No data that does not reach the field's edge
  through no data is a pit; the rest is the void past the world's edge, a floor the fill
  emptied beside it included (at the north-east corner it drew as a black rectangle). The
  crater and both abyss pits are pits: 135,744 texels of the 10.84 M void.
- **The artwork's falloff.** The artwork lights a pit or the void past the edge at its rim, a
  white line about 2 m wide, and fades from a flat grey (luma 65 to 76) to black over about
  110 m: half the light is gone by 37 m, three quarters by 63 m. A Gaussian of 50 m
  (`VOID_FALLOFF_M`) on the void's mask fits that to about 5 points. Every style draws both
  the same way: a light rim, then from the artwork's grey to black in a pit, and from a lit
  tone of the page's navy (#424f5a) to the navy itself past the edge. The void is softened
  over 2 m, and a pixel shared by sea and void counts its water against the part that is not
  void, so the void's edge is never drawn as land.
- **The sea fades into the void.** Beside the open sea the bed runs on under the void, and
  the void's cover rises from 0 at its edge to 1 over the same falloff, with no lit edge and
  no rim. A pixel takes its sea share from the ocean around it, Gaussian-weighted, so a coast
  where land and sea meet the void changes treatment smoothly. Dry ground under the sea's
  surface within the shore rule's 48 m of the ocean counts as sea here, because that rule
  draws it as sea; low ground further inland stays land (3.1 M dry texels lie under the sea's
  level, most of them in the southern lowlands).
- **Sunken strips.** Dry ground under the band's top in a gap at most 8 m wide
  (`VOID_STRIP_M`) between the open sea and the void past the world's edge, measured through
  that ground, joins the sea: it takes the open sea's bed and fades into the void. The artwork
  leaves those strips dry and draws a thin land strip and its rim, and drawn as land they were
  a dotted dark line beside the waterfall_1 islands and on the north edge. Ground beside a pit
  and wider bands stay land; at 12 m the rule cut a 15 m spit on the north edge into pieces.
  1,316 texels join, all within 8 m of the void.
- **Rocks in the void.** The void's cover is kept off a rock standing in it only where the
  rock stands above the sea's level. A deeper one is the void's, as the artwork draws it, and
  is taken out of the height before the water and colours are drawn (`render/surface.py`
  `_rock_kept`): kept, the lighting stage shaded the void over rock 250 to 700 m down into
  near-black silhouettes. On no data it is dropped; where the sea runs on under the void it
  keeps only the share the void's cover leaves.

The bed is drawing support, not a measurement: the field, its quality byte and the server's
lookups are unchanged, and `--kernel-only` (recipe 2) draws the page's sea past the data. The
sidecar records the rule and the counts under `water.level_only` (with `strip_m` and
`strip_texels_joined`) and the pits under `two_regime.fill_rebuild.pits`.

Known limits:

- A measured shelf that runs straight to a landscape component's edge still shows its
  staircase, as a soft gradient over a few hundred metres rather than a line.
- A pit's edge is the data's: where the field has ground above -200 m beside a pit and the
  artwork draws void, the ground is drawn.
- 681 regions pass, many of them a few texels of deep ground; those inside the land are drawn
  as small pits, grey with a rim.
- In the satellite and relief dark styles the deep sea (#152a3b, #031b2d) and the page's navy
  are close in colour. Beside the land the void has a lit edge and a rim, but where the sea
  fades into it nothing marks the edge of the world. Whether the void past the edge should
  leave the page's navy, as the artwork's black does, is open.
- The tones are a hint, not a bed: under the teal the open sea is not held deeper than 6 m,
  and a tone's anti-aliased edge against land can read a metre or two too deep.
- The falls off the edge of the world are left out (section 35).

### Drawing less (2026-10-06)

The band loop skips arithmetic whose answer it already has, and the tiles are the same bytes:

- **A plane with no holes is sampled without weights.** `sample_plain` reads the province
  mask, the artwork's high pass, every painted plane, the void's planes and the river
  presence: 40 planes a band in the painted layer. `resample` also sums the stencil's weights
  for the no-data bookkeeping, a second gather and multiply per tap that nothing read there.
  `sample_plain` sums the values alone, in `resample`'s order, from the same zeroed
  accumulators. `reads_nothing` checks the texels under the band's rows first: when they are
  all zero the sample is 0.0 everywhere, so it is not worked out.
- **The void is drawn where it is.** Where its cover and rim are both 0, `with_void`'s blend
  gives back the pixel, so only the pixels under one of them are blended. A band with no void
  under its rows and no pixel without data returns before the void's four planes are sampled,
  and `_sample_water_surface` and `_rock_kept` skip the cover there too.
- **Water is mixed where it is.** The terrain and satellite styles (`water_composite`), the
  painted style and the relief styles all end their water with
  `land * (1 - cover) + under * cover`. `wet_mix` works that out only on the pixels whose
  cover is not 0. The colour under the water is still worked out for every pixel.

`blend_where` in `palette/water/shore.py` does the masking for the last two. It gathers the
touched pixels, blends them with the same expression and writes them over a copy of the input.
Past a share of the band it blends the whole band instead, because gathering is then slower:
2/3 of the pixels for the void (`VOID_MOST`), 1/3 for the water (`WET_MIX_MOST`), the
crossings measured on a 272-row band 32,768 wide. The three skips save about 12% of the
draw's CPU over the five layers at full size.

**Why the bits do not move.** A pixel left out is left as it was. The full blend gave
`x * 1.0 + y * 0.0` there, which is `x` for every finite `y`. All it could change is the sign
of a zero, `-0.0 + 0.0`, and cutting the band to 8 bits drops that. A plane of zeros sampled
the long way gives `+0.0` too, because the accumulators start at zero. Only a `y` that is not
finite would differ: an under-water colour of NaN or infinity on a dry pixel, or a NaN rock
coverage in a band the void's skip passes over. Neither turned up in the before-and-after
hashes of a 2048 render and of three full-width 32768 windows, where either would have changed
a hash.

## 39. Compressed raster caches: the zstd band store (2026-10-06)

The render's raster caches (`direct.cache`, `top.cache`, `meshes.cache`, `titan.cache`) are a
zstd band store, 0.93 GB at 32768 against 18.5 GB as raw memory maps, which the render reads
directly: nothing is inflated back to a raw file first. Each plane is written once, top to
bottom in 256-row bands, and every layer then reads it top to bottom again, rows
`[top - 8, top + 264)` per band. Two other reads exist: the family plane's strided row
gather, once per run, and the Titan raster's half-resolution window. Nothing reads them at
random. The planes round-trip bit for bit, so the tiles are the same bytes as from a raw
cache.

### Measured (2026-10-06, the renders-v5 caches, build 502094)

The nine planes go from 17,664 MiB raw to 892 MiB, 19.8 times smaller. Only 11% of the float32
texels are not zero, and the rock heights (`direct.z.f32`, 4,096 to 709.6 MiB, 5.8 times) hold
80% of the compressed bytes; the other float planes shrink 34 to 112 times and the byte planes
167 to 2,249 times. Writing all nine planes costs 9.9 s of CPU and reading them back in the render's
pattern 17.8 s, against 7.5 s for the memory maps once they are in the page cache. A cold read
of the raw set from the hard disk the caches live on runs at 115 to 147 MB/s, so 120 to 154 s;
the band store is 6 to 8 s of reading. The two coverage planes are redundant at one
sub-sample, since a texel is covered exactly where its height is not zero, but they cost 7 MB
compressed and are kept. A 2048 preview's caches compress only 12 times: the coarse sheet has
rock on 31% of its texels.

Other codecs on the same planes: zstd without the shuffle 3.8x on the rock heights, lz4 3.3x,
`numpy.savez_compressed` 3.9x and slow. blosc2 with shuffle and zstd reached 5.5 to 6.5x and is
fast with threads, but it is a heavy dependency and 4.14.1 corrupted data with `TRUNC_PREC`
plus `BYTEDELTA`. Compressing whole files at rest and inflating them before a run saves no peak
disk, adds about 2.5 min per run and a second window in which a crash leaves a half-written
cache.

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
  field is a raw cache from before the band store. The stamp did not change, so those caches
  still hit. A storage this reader does not know is a miss.

`mapgen/bandstore.py` holds the format: `BandWriter` writes bands in order and commits on
close, and a writer that fails or is closed short deletes its file. `BandArray` reads.

### Reading

`cache.open_plane` returns a `BandArray` for the band store and the read-only memory map for a
raw cache. `cached_raster`, `cached_family` and `cached_meshes` go through it, and the Titan
trees through `cached_meshes`.

- `BandArray` decodes a band when it is first asked for and keeps the last three. A band
  loop read spans three bands at most, in order, so each band is decoded once per layer.
  A layer drawn on threads (section 40) shares each `BandArray` between them: a lock covers
  the cache and the decoder, one per plane, and while the layer is drawn the plane keeps
  `2 × threads + 2` bands, the bands in flight and a halo band either side.
- It takes a row, a row slice, or an integer array of rows, then any column index. The
  family gather is decoded band by band. Results are read-only, as the memory maps' were.
  Asked for as a whole array it decodes into a new one, so `__array__(copy=False)` raises
  `ValueError`: there is no view to share.
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
- A raster that does not read back right after it was written, direct, top, mesh or Titan,
  is a refusal with exit code 7 (section 20, "Refusals"), never a raster silently dropped
  while the sidecar records it.

### Writing

Production writes only band stores; raw caches are still read, and only the tests write them.
`write_banded_raster` and `rasterise_meshes`, which also writes the Titan raster, write through
`cache.rewrite_planes`. It deletes the cache's sidecar and its planes in both layouts before
the first band, so a cache never holds both and a run cut short leaves a miss. In
`write_banded_raster` the first band decides whether the family plane is written, and every
band after it must agree. Every cache sidecar, `storage` included, is written atomically
through a temporary file and a rename (`cache.write_sidecar`).

### Converting a kept cache

`python -m mapgen compress-cache <dir>` converts a raw cache in place. `<dir>` is one raster
cache, or a folder holding them such as `data/local/maps/_cache/<size>`. It asks for
`zstandard` before it touches anything, so without the `gen` extra it prints the fix and
exits 2 rather than raising a traceback.

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
a peak working set of 97 MB. A whole 32768 set should take 2 to 2.5 min, nearly all of it
reading 18.5 GB from a hard disk; that is an estimate from the read rates above, not a run.

### Compatibility

- `zstandard==0.25.0` is in the `gen` extra. `python -m mapgen renders` and `compress-cache`
  ask for it with the other `gen` modules, and the Maps tab's generation check includes it.
- An older build of the generator does not find the raw files in a band-store cache, so it
  misses and rebuilds raw. The `.bands` files stay beside them until the next band-store run
  or a cache clear removes them.
- The job estimate's cache term, `CACHE_BYTES_FULL` in `domain/maps/presets.py`, is 1.0 GB at
  32768, scaled by area like the rest.
- A cache the run cannot delete at its end, because a file in it is still open, is named:
  "could not remove <dir>: a file in it is still open".
- The light's scratch (section 29, "Scratch") is not in the band store. It is not a cache:
  nothing reads it after the run that wrote it, which deletes it. Its planes are dense and
  compress 2.6 times, not 20, and the bake reads them in blocks with a halo and writes some a
  block at a time from its processes. `--scratch-dir` moves it off the cache drive instead.

## 40. Drawing a layer's bands on threads (2026-10-06)

A band is bound by memory bandwidth more than by arithmetic: nearly every step allocates a
fresh band-sized array. So `render_layer` runs a layer's 256-row bands on a pool of threads,
several at once, and the tiles are the same bytes as one band after another.

### What runs where

- **The bands are unchanged:** 256 rows, with `BAND_HALO` (8) rows either side, cropped. A
  band on a thread computes exactly what it computes in turn; only when it runs changes.
  128-row bands changed pixels, so the band and block geometry stay. So does the known seam
  where the halo is narrower than the 13 px water blur at 32768: widening the halo changes
  pixels and is a separate item.
- `_layer_job` builds what every band of a layer shares, once, before any band starts: the
  arguments, the column taps, the satellite noise and the water planes. The bands only read
  it.
- `_draw_band` writes its own rows of the sheet and, for the first layer, its own rows of
  the light's `Surface` (section 29). From a band's thread nothing else shared is written.
- **The seam trace and the regime table** are measured per band on the band's thread
  (`SeamTrace.measure`, `RegimeCoverage.measure`, which touch nothing shared) and merged by
  the caller in band order (`merge`). The pools, the float sums and the order the provinces
  are first seen in are the serial loop's, so the sidecar's numbers are too. `add` is
  `merge(measure(...))`.
- `render/drawpool.in_order` runs the pool. Results come back in band order, at most
  `2 × threads` bands are submitted past the one being waited on, and a failure is raised
  when its band's turn comes, after the bands not yet started are cancelled and the running
  ones have finished. On one thread it is a plain loop on the caller's thread: the serial
  path.

### The band stores

The draw's threads share each `BandArray` (section 39). Its cache and its zstd decoder are
not safe to use from two threads at once, so a lock covers both: one lock per plane, so two
planes decode at once and one plane decodes one band at a time. While a layer is drawn on N
threads, each plane keeps `2N + 2` decoded bands (`bands_held`), because the bands in flight
span that many stored bands with their halos. The count drops back to three when the layer
is done. At 32768 one stored band of every plane the painted layer reads comes to about
160 MB, so on 8 threads it holds about 2.9 GB of decoded bands; the other layers read fewer
planes, about 125 MB a band.

### How many threads

`--draw-threads N`; by default 8 (`DRAW_THREADS`), and no more than the machine has cores.
Before each layer the count is cut to what free memory holds: the free memory, less the sheet
(3 bytes a pixel) and 2 GiB, over the peak one band in flight takes. The free memory is the
one `mapgen/pools.py` reads for the light bake and the cutter too. On Windows it is the
lesser of the free physical memory and the commit left, because an array commits its
whole size when it is allocated: with other work running, the commit ran out at 17 GB while
37 GB of RAM stood free, and an allocation failed. One more band in flight costs about
1.9 GB, and 3.4 GB for the painted layer, at 32768 wide, scaled by the width (`BAND_BYTES`,
the working set measured at 1, 4 and 8 threads below, decoded bands included). With nothing
else running, a 64 GB machine draws every layer on 8 threads. `1` draws the bands in turn. The
run prints the count per layer, and the layer's `meta.json` records it as
`render.draw_threads`, beside `cut_workers`.

### What the threads share, audited

| Shared | Why it is safe |
| --- | --- |
| The heights, lattices, water and void planes, and the rasters | Read only: numpy arrays, `r` memory maps, or band stores whose bands are read-only |
| The field | Its planes are decoded when it loads; the water planes are read in `_layer_job`, before any band |
| `PaintedGround`, `ReliefGround`, `RiverWater`, `OpenSea` | Built before the draw. The crowns' calibrated sprites, the water classes and the family targets are written in setup, never by a band |
| Random numbers | The satellite noise comes from a seeded generator, once per layer in `_layer_job`; the moss patches hash each pixel's position |
| numpy's error state | Per thread since numpy 2; the band code sets no warnings filters, which are process-wide |
| Palettes and colour tables | Module constants, read only |
| The sheet and the light's surface | Each band writes only its own rows |

### The GIL

The band's hot calls are numpy, scipy and zstd, and they release the GIL. On one 32768 band,
272 × 32768 float32, eight calls on eight threads ran 1.55 times faster than in turn for plain
arithmetic, which waits on memory, 2.2 to 3.1 times for `np.where`, `np.clip` and
`np.gradient`, and 3.7 to 6.5 times for the samplers' column gathers, boolean compaction, the
cube roots and powers, the OKLab matrix product, the Gaussian, uniform and maximum filters,
`np.unique` and a band's zstd decode. None ran at the speed of one thread, which is what a
call holding the GIL would do.

What holds the GIL is Python-level looping: the crowns are stamped tree by tree, each stamp
a few small array operations, and the waterfalls and the regime table's provinces loop the
same way. At full size a tree covers hundreds of pixels and the painted layer still draws
2.8 to 3.7 times faster on 8 threads. At 2048 a tree is a few pixels: profiled there, 106,237
stamps took 8.7 s of the painted layer's 24.1 s, and it draws only 1.65 times faster on 8
threads.

### Measured (2026-10-06, the 32768 sheet, build 502094)

A window of the full-size sheet: rows 12288 to 16384, which is 16 bands across the middle of
the map, and columns 8192 to 24576, half the width so that 16 threads fit in memory beside
other work. It was drawn through the run's own preparation, unlit, with every raster the bands
read cut to the window and stored as a band store, so the threads shared `BandArray`s as in a
full render. Seconds, and how many times faster than one thread:

| Layer | 1 thread | 4 | 8 | 16 |
| --- | --- | --- | --- | --- |
| terrain | 52.1 | 24.8 (2.1×) | 20.6 (2.5×) | 19.3 (2.7×) |
| satellite | 61.3 | 27.8 (2.2×) | 21.3 (2.9×) | 18.5 (3.3×) |
| painted | 158.1 | 65.0 (2.4×) | 43.0 (3.7×) | not run |
| relief | 57.9 | 23.5 (2.5×) | 19.8 (2.9×) | 21.0 (2.8×) |
| relief-dark | 59.7 | 22.5 (2.7×) | 19.2 (3.1×) | 19.4 (3.1×) |

- Every array is byte-identical by SHA-256 to the serial loop's draw of the same window.
- Painted on 16 threads needs about 28 GB at half width, so it ran on a quarter of the width
  (columns 12288 to 20480): 60.8 s on one thread, 21.8 s on 8 and 21.7 s on 16.
- The machine ran other renders and tests throughout, so single timings carry some noise;
  each row was measured in one process within minutes.
- **Memory.** The peak working set over the process's own grew by 0.8 to 1.0 GB a thread at
  half width, and by 1.5 to 1.7 GB for the painted layer, the two more decoded bands per
  plane each thread brings included. Doubled for the full width, that is `BAND_BYTES`.
- **At full size.** Per pixel, the five layers take 6,230 s on one thread and 1,980 s on 8,
  about 3.1 times faster. 16 threads would save under a minute more for twice the memory, so
  8 is the default.

### Known limits

- More threads than 8 drew little faster, and relief slower: the bands wait on memory, not
  on cores.
- The decoded bands the threads need, and the bands in flight, are memory the serial loop
  did not take; the thread count is cut to fit, and `--draw-threads` lowers it further.
- The layers still recompute the same heights, water and meshes band by band, each layer
  again. Drawing all layers in one pass over the bands is the next step, and a larger one.

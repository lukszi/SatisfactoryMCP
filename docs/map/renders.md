# The drawn renders: layers, sampler, recipes and caches

Sections 17, 20, 25, 26, 39, 40, 41 and 42 of the [design spec](../../DESIGN.md): the drawn base layers,
how they sample the field and the rocks, the recipes they draw by, their raster caches,
how a layer's bands are drawn, the loops compiled for it, and how the bands are cut as they
settle. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

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

### The noise, read between its cells (2026-10-07)

The two octaves are fields of 256 and 1024 texels over the 7500 m square, so their cells are
29.3 m and 7.3 m. `terrain/sample.py` `sample_noise` read them nearest, so every cell was a
flat square with a step at its edge: a quilt of 29 m and 7.3 m blocks on every flat of the
satellite layer, steps of about 1 to 2% in brightness (sweep class 10, auto #4). Each octave is
now read between its texel centres, wrapping, with smoothstep weights: the value at a cell's
centre is the field's, and the slope is continuous across its edge, so no cell edge draws a
line. The fields, their seed and their amounts are unchanged; the spread of the noise moves by
a few percent. The layer's dry pixels move by up to 4 levels on the 2048 render, where about
a fifth of its top level moves, and by up to 5 on the full-size sheet.

Nothing changed in the noise between the sixth and the seventh render. The archived crops
of the Dune Desert (`render-archive/biome-dune-desert`) show the same 29 m blocks in the
fifth and sixth renders under a contrast stretch: those drew the 45° north-west hillshade into
the colour, and its stronger shading hid them. The seventh draws the satellite layer unlit
under the live sun (section 29), whose high noon sun leaves a flat nearly flat, so the
blocks stood out. Each pixel still reads the noise at its own place in the sheet, so a band,
a piece or a thread count cannot move it. Since satellite 9, round 2's one bump.

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

A lit layer writes three trees: `unlit/`, `tiles/` and `tiles@2x/`, 45,055 tiles at 32768.
`mapgen/tiles/cutter.py` cuts every tree of a run while the draw is still going, a band of
rows at a time (section 42), and writes the bytes of the serial cutter, `install_pyramid`:

- **One encode pool.** `--cut-workers` processes (default one per logical core, at most 24)
  encode one row of tiles per job, for every tree of every layer. A row of tiles is queued as
  soon as its rows are in. `--workers N`, as before, sizes both this pool and the light
  bake's where `--cut-workers` or `--light-workers` is not given (section 29).
- **Rows through shared memory.** Each row of tiles is copied into a `shared_memory` block of
  its own, which the encoder reads and which is freed when its job is done. No Pillow image
  of a whole sheet is made.
- **Encoders without numpy.** The encoder (`core/gameassets/pyramid.encode_tile_row`) reads
  the block through Pillow. numpy's BLAS thread pool commits about 0.75 GB in every process
  that imports it, 18 GB for 24 encoders, and on a busy machine that exhausted the commit
  limit with physical memory to spare.
- **Levels in strips.** Each level is still one Lanczos downscale of the whole sheet. A strip
  resizes a range of rows with a `box`, out of source rows cut with a halo of 3 × scale + 4.
  Pillow derives each output row's taps from its position alone and reaches 2.5 × scale + 0.5
  source rows either side, so every strip is the same bytes as those rows of a whole-sheet
  resize. Measured at scales 2 to 128; a halo of 2 × scale fails at every scale. The strips
  run on threads in the parent, because Pillow releases the GIL inside a resize.
- **A level two trees share is resampled once.** `tiles@2x/` is cut from the sheet
  downscaled to 16384, the same resize as `tiles/` z6, so its top level is that level. On a
  sheet of 16384 or less every @2x level is a 1x level.
- **Serial.** `--cut-workers 1` cuts on the caller's thread, each band as it is handed on.
  `install_pyramid` stays the reference: `--check-parallel` and the tests compare with it.

The 15 to 26% of z7 tiles that are one void colour are encoded like any other: a one-colour
tile takes 0.5 to 0.7 ms against about 34 ms for a land tile, so caching their bytes would
save under 1%.

`--check-parallel` proves it rather than asserting it: the artwork's whole pyramid cut both
ways, SHA-256 of every tile compared name for name, recorded in the sidecar; a mismatch
refuses the run (exit 5).

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
(a zstd band store, section 39) that every layer draws from: the bands on threads and the
scan compiled since 2026-10-08 (section 41, "The raster passes"). Two rules make that draw
smooth.

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
| 1 | `render/run/inputs.NO_CONTAINER` | the game's container is not at `--game` |
| 3 | `render/run/inputs.STALE_LAYER` | a layer folder holds tiles this run cannot show were drawn from the field on disk; `--force` replaces them |
| 4 | `render/run/inputs.NO_FIELD` | there is no heightfield |
| 5 | `render/run/inputs.PARALLEL_MISMATCH` | `--check-parallel` found the parallel cutter's bytes differ from the serial one's |
| 6 | `render/run/inputs.NO_DENSITY` | the field has no `density.u8.z`, so it cannot say which cliff texels are measurements; the message names the generator version that writes one, and `--kernel-only` draws without the geometry |
| 7 | `render/run/cached_rasters.UNREADABLE_RASTER`, `terrain/render_meshes.RASTER_UNREADABLE` | a raster just written does not read back (section 39) |
| 8 | `render/run/inputs.NO_PAINT` | the painted layer is asked for and there is no paint store |
| 9 | `render/run/inputs.RESTYLE_MISS` | `--restyle` and a kept raster cache is missing or was cut for another size, sub-sampling or build |
| 10 | `render/run/inuse.IN_USE` | the output folder holds a map type the server's registry lists; `--overwrite-in-use` writes anyway |
| 11 | `render/draw/light.SCRATCH_IN_USE` | the light's scratch is held open by a render still running (section 29, "Scratch") |
| 12 | `jit.NO_GPU` | `--gpu` and the CUDA kernels cannot run here: numba, CuPy or a device is missing (section 41, "On the GPU") |
| 1 | `commands/renders.CUT_FAILED` | the tiles could not be cut into place |

Exit code 2 is argparse's, for a command line it cannot parse.

A raster cache carries the size, the sub-sampling and the build it was rasterised for, so a
cache from another render is rebuilt, not refused. `--kernel-only` is the honest way to draw
without the geometry: it draws **recipe 2 whole** (no rocks, no lattice split) and records that
recipe number, so a before/after against it compares two recipes rather than one recipe
against half of itself.

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

The top pass now drops an arch larger than `OVERSIZE_CM` or wholly off the raster, as the
direct pass drops a rock, and counts them in its sidecar block (`arches_dropped`): the scenery
arches above. On build 502094 that is 93 of 1,076 arches: 68 over 600 m across and 25 more
wholly off the raster, none of them reaching it, so no pixel moves; the tiled scan had
clipped them to nothing.

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

### Where the lattice stops (2026-10-07)

Inside a formation big enough that no landscape texel survives under it, the lattice knows
nothing and the field's own fold stands in: the rock is the whole answer, and the painted
layer colours the pixel as rock by its coverage alone. Elsewhere the rock is composited onto
the lattice and coloured as rock only where it stands proud of it. Both rules switched at the
lattice's last texel, a hard edge on the 1 m grid. Every such edge drew a staircase of rock
colour and a height step, which the hillshade and the light draw as a crease; along the
landscape's straight east and south edges (x 4064 m, y 3048 m), where the cliff province meets
the fill, it was a line several hundred metres long in every layer.

`render/ground/lift.py` `lattice_edge` softens that edge over `LATTICE_EDGE_BLUR_M` (2 m) inside the
lattice: a byte per texel, 255 where the fold stands in, falling to 0 about 6 m in, built once
a draw. A band blends the lattice's height towards the fold, and the rock's share towards its
coverage, by it (`render/ground/surface.py` `_direct_regime`). Where the field has no data either,
the lattice's edge is the void's or a pit's, which the void draws, so it is not softened.
Past about 6 m from the edge every pixel is the same bits as before.

Measured (2026-10-07, build 502094): at 2048 the unlit tiles of each layer move in 21 to 23 of
85 tiles, 699 (relief) to 1,808 (painted) pixels, 20 of the terrain's by more than 15 levels.
On full-size windows the east line at x 4064 m falls from a mean step of 2.9 levels across one
column of the painted layer to 0.2, its neighbours' being 0.1, and the heights' curvature there
from 0.31 to 0.06; along y 3048 m the painted step falls from 2.8 to 0.3. The three windows of
section 40 hold no lattice edge and draw the same bytes.

Left as it is: a step in the field's own heights at that edge. Along y 3048 m the cliff
texels stand 1.1 m under the fill beside them, because the fill's seam band carries only the
landscape's residual and never reads rock (section 26); that step still draws a soft crease in
the hillshade.

### Known limits

- The artwork borrow still multiplies the drawn map's arch strokes into the shading, so a
  faint ghost stripe can sit beside an arch where the drawing and the mesh disagree.

### Arches as spans (2026-10-07)

Recipe 7 keeps its number and draws the arches as spans: the top raster keeps their underside
and the boulders apart and fills the sub-metre holes their open mesh edges leave, the direct
raster finds the rock overhangs, and each layer's arches are antialiased by FXAA, nothing else.
In the light the arches, the overhangs and the crowns cast where the sun's ray meets them. The
rendered styles share round 2's one version up (terrain and satellite 9, painted 20, both
reliefs 7) and the live sun is light model 3. Both are light-and-crowns.md section 29, "Arches
as spans".

## 26. Rebuilt base data and a PCHIP sampler: recipe 5 (2026-10-05)

Recipe 5 rebuilds the rest of the lattice the kernel reads, and swaps the kernel. Rocks,
arches and boulders are drawn as in section 25. Build 502094.

### What changed

`terrain/fill.py` builds the lattice once per run, in about 13 s, before any band is drawn:

| Texels | Source | Share of the field |
| --- | --- | --- |
| Landscape | `terrain.u16.z`, unchanged | 45.39% |
| Cliff province | the field's own heights, copied unchanged | 20.97% |
| Fill | the float16 interface raster: Gaussian with sigma 1 texel, then cubic, then +1.0 m | 13.10% |
| Fill within 48 m of the landscape | as above, plus the landscape's residual carried in by a harmonic solve and a cosine taper | 0.25% |
| Interior holes | biharmonic fill, or harmonic where the biharmonic leaves its border's range by more than 2 m | 0.005% outside the cliff province (35 holes, 13 harmonic, nearly all ground under rock) |
| Pits: no data and ground below -200 m the artwork draws as void | left empty, drawn as the void | 0.36% (680 regions; 107,212 texels of ground) |
| Fill past the artwork's world rim | left empty, drawn as the void ("The fill past the world's rim", below) | 0.57% (20 regions) |
| No data out to the field's edge | left empty: the open sea or the void, as the artwork has it | 19.35% |

Shares from the 2048 preview of 2026-10-05, the fill, the pits and the rim from the field of
2026-10-07 (they do not depend on the render's size). Before the rim was clipped the fill
was 13.49% and the pits 0.55% (681 regions; 210,086 texels of ground, 102,874 of them now
past the rim).

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
  void, so the void's edge is never drawn as land. The light fades out with the void as it is
  drawn (section 29, "The land weight").
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
  is taken out of the height before the water and colours are drawn (`render/ground/surface.py`
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

### The fill past the world's rim (2026-10-07)

The interface raster has heights in places the artwork draws black, past the white line it
draws round the world: a 226,000 m² island south-east of the abyss that the game's map
does not show, a lobe on the east edge north of the abyss cliffs, and smaller pieces. Drawn
as fill they were land or a beach where the artwork has the void (sweep class 18, auto
#11). `terrain/emptied.py`
`void_past_rim` finds the void past the rim, and `fill_field` leaves the fill there empty
before anything is rebuilt, as it leaves a pit (`SOURCE_RIM`, under
`two_regime.fill_rebuild.past_the_rim` in the sidecar). The render then draws it as the
void past the world's edge, with the lit edge and rim line of the section above where it
meets the land, and the sea fading into it where it meets the sea. Since terrain and
satellite 9, game-painted 20, and relief and relief dark 7, round 2's one bump.

- **The void past the rim** is the artwork's void (`artwork_planes`) that outlasts an
  erosion of 4 texels (`RIM_CORE_TEXELS`) and reaches the grid's edge, grown back through
  the void by 8 texels (`RIM_REACH_TEXELS`). Only fill is clipped: the landscape, the cliff
  province and the water stay as they are.
- **Why not every void joined to the edge.** That rule takes 435,301 texels, and a third of
  them are not past the rim: 40,806 lie under the sheet's own dark frame, 3 pixels round its
  north, west and east sides, which runs over the open sea; most of the rest are cliff faces
  the artwork draws dark just inside its rim line, joined to the void through a gap in the
  line or a dark stroke across it. The frame and those strokes are thinner than the erosion,
  and the growth stays inside the void, so it reaches past a gap by 8 texels at most.
- **What it clips** (build 502094): 321,562 texels in 20 regions. The south-east island
  225,974; two blocks in the north-east corner, 59,653 and 21,169; the east edge's lobe
  11,660; a block on the abyss's north rim at (2930, 1145), 1,936; the south-west corner
  788; the rest 244 and less. The north-east blocks and the abyss's are a floor below
  -200 m that the pit rule had emptied already, as had 19,984 texels of the island, so
  218,688 texels are newly empty, nearly all of them the island and the lobe. Fill under the
  rim's light line is kept, so the land ends where the artwork's line does. It takes about
  1 s a run.
- **Downstream.** The open sea counts 218,688 more void texels, 12,464 more under the sea's
  fade into the void and 885 more in the sunken strips beside it. The open sea's bed is one membrane over the whole sheet,
  so it moves well past the clip: by up to 3.6 m within 200 m of a clipped region, where the
  coast it rose to is gone, 0.4 m within 400 m, 16 cm within a kilometre and 2 cm within
  two, measured with the membrane solved to 1e-10; past that, the solve's own tolerance
  (1e-6, within about 5 mm of the exact bed) moves it by up to 1.1 mm.
- **The 2048 render** (against round 2's baseline): in each layer but satellite about
  36,000 pixels of the top level move, 26,700 of them within 50 m of a clipped region (land
  to the void), 8,900 within 400 m (the void's falloff and the bed) by at most 16 levels,
  and 200 to 1,700 further out by one level, where the bed moved. The light's horizon tiles
  are lossy WebP, so every tile whose bed moved at all is encoded anew, up to 36 levels
  apart on the open sea and on the coasts beside it; the tiles lit by the default sun move
  no more pixels there than the unlit ones.
- **The full-size sheet**, unlit, the satellite layer aside: over the island 7.6 M pixels
  move in every layer, over the lobe 0.63 to 0.68 M; of the three gate windows the densest
  water is unchanged, the full-width strip moves 9,600 to 90,000 pixels and the first two
  bands 63 to 425, all by one level and within 1.3 km of a clipped region.

Known limits:

- The fake east beach (4111, -2530) is not past the rim: the artwork draws water there
  inside its line. It is the open sea's bed rising to the coast over the first tens of
  metres, as everywhere beside a cliff, under a bay the artwork draws in its deep teal.
- The fill inside the rim keeps its edge, a staircase of the interface raster's 3.66 m
  texels where it ends, along the east edge between the abyss cliffs and the swamp.

### Drawing less (2026-10-06)

The band loop skips arithmetic whose answer it already has, and the tiles are the same bytes:

- **A plane with no holes is sampled without weights.** `sample_plain` reads the province
  mask, the artwork's high pass, every painted plane, the void's planes and the river
  presence: 40 planes a band in the painted layer. `resample` also sums the stencil's weights
  for the no-data bookkeeping, a second gather and multiply per tap that nothing read there.
  `sample_plain` sums the values alone, in `resample`'s order, from the same zeroed
  accumulators. `reads_nothing` checks the texels the taps read first, the band's rows cut to
  the piece's columns ("Column pieces", section 40): when they are all zero the sample is 0.0
  everywhere, so it is not worked out.
- **The void is drawn where it is.** Where its cover and rim are both 0, `with_void`'s blend
  gives back the pixel, so only the pixels under one of them are blended. The four planes are
  sampled once a piece for every layer and the light (`render/ground/void.py`). A piece with no void
  under it and no pixel without data returns before the void's four planes are sampled, and
  `_sample_water_surface` and `_rock_kept` skip the cover there too.
- **Water is mixed where it is.** The terrain and satellite styles (`water_composite`), the
  painted style and the relief styles all end their water with
  `land * (1 - cover) + under * cover`. `wet_mix` works that out only on the pixels whose
  cover is not 0. The painted and relief styles also work out the colour under the water on
  those pixels alone ("Painting only the wet pixels", below).

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
taken: they belong to the band loop (`render/draw/painting.py`).

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
`render/draw/painting.py`'s band loop, and the shore terms, the river terms, the wet band and the
foam stay whole-band work.

Both painters, with the terrain and satellite styles' `water_composite`, also run as numba
kernels on the same pixels (section 41, "The painters").

## 39. Compressed raster caches: the zstd band store (2026-10-06)

The render's raster caches (`direct.cache`, `top.cache`, `meshes.cache`, `titan.cache`) are a
zstd band store, 0.93 GB at 32768 against 18.5 GB as raw memory maps, which the render reads
directly: nothing is inflated back to a raw file first. Each plane is written once, top to
bottom in 256-row bands, and the draw's one pass (section 40, "One pass for every layer")
then reads it top to bottom again, once for all the layers, rows `[top - 16, top + 272)` per
band (section 40, "The halo"), which the band's column pieces cut to their columns. Two other
reads exist: the family plane's strided row gather, once per run, and the Titan raster's
half-resolution window. Nothing reads them at random. The planes round-trip bit for bit, so
the tiles are the same bytes as from a raw cache.

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
  loop read spans three bands at most, in order, so each band is decoded once a run, for
  every layer the pass draws. The pass's threads (section 40) share each `BandArray`: a lock
  covers the cache and the decoder, one per plane, and while the pass is drawn the plane
  keeps the bands its pieces in flight span and a halo band either side (section 40, "The
  band stores").
- It takes a row, a row slice, or an integer array of rows, then any column index. The
  family gather is decoded band by band. Results are read-only, as the memory maps' were.
  Asked for as a whole array it decodes into a new one, so `__array__(copy=False)` raises
  `ValueError`: there is no view to share.
- It opens the file for each band and holds no handle between reads. Clearing the cache
  through `DELETE /api/maps/cache` is refused while a job runs, so no reader loses a file.
- At 32768 this costs about 10 s more CPU a run, which reads the planes once, and about
  0.45 GB more memory for three decoded bands of each plane: 96 MB for a float32 plane. It
  saves about 2 min of reading from a cold disk.

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
fresh band-sized array. So `render_layers` runs the 256-row bands on a pool of threads,
several at once, in one pass for all the run's layers ("One pass for every layer" below) and
in column pieces ("Column pieces" below), and the tiles are the same bytes as one band after
another.

### What runs where

- **The bands are unchanged:** 256 rows, with `BAND_HALO` rows either side, cropped ("The
  halo" below). Each band is drawn in column pieces ("Column pieces" below), and a piece on a
  thread computes exactly what it computed in turn; only when it runs changes. 128-row bands
  were tried in the research and changed pixels, so the band and block geometry stay.
- `render_layers` builds what every band shares, once, before any band starts: the ground's
  sources (`_ground_sources`: the arguments, the column taps, the water planes) and each
  layer's job (`painting.layer_job`: its painter's inputs, the satellite noise). The pieces
  only read them.
- `_draw_piece` writes its own pixels of every layer's sheet. From a piece's thread nothing
  else shared is written.
- **Each band is settled in order** on the caller's thread once its last piece is in
  (`surface.settle_band`): its rows of the light's `Surface` (section 29) are written, and the
  seam trace and the regime table measure it (`SeamTrace.measure`, `RegimeCoverage.measure`)
  and are merged (`merge`). The pools, the float sums and the order the provinces are first
  seen in are the serial loop's, so the sidecar's numbers are too. `add` is
  `merge(measure(...))`.
- `render/draw/drawpool.in_order` runs the pool. Results come back in order, at most
  `2 × threads` pieces are submitted past the one being waited on, and a failure is raised
  when its piece's turn comes, after the pieces not yet started are cancelled and the running
  ones have finished. On one thread it is a plain loop on the caller's thread: the serial
  path.

### The halo (2026-10-07)

A step that reads its neighbours draws what the whole sheet would only while all it reads
lies within the band and its halo. `render/ground/stencils.py` lists every such step with its reach,
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

The column pieces read the same table ("Column pieces" below): every stencil but the seam
trace reaches as far along a row as across rows, which the tests measure too by moving one
column, so `PIECE_HALO` is 16 columns. The seam trace reads a band only once its pieces are
put together, so no piece edge cuts it (`Stencil.pieces`).

### The band stores

The draw's threads share each `BandArray` (section 39). Its cache and its zstd decoder are
not safe to use from two threads at once, so a lock covers both: one lock per plane, so two
planes decode at once and one plane decodes one band at a time. While a pass is drawn on N
threads, each plane keeps the decoded bands that the `2N` pieces in flight span, and one more
either side for the halos (`bands_held`). The count drops back to three when the pass is
done. Every piece of a band reads the band's columns it needs out of the same decoded bands,
cutting them before they are joined (`BandArray._span`), so a piece copies its own columns
only. At 32768 a band is 64 pieces, so 8 threads hold 4 decoded bands where they held 18
before the pieces; at 2048 a band is 4 pieces, and they hold 7. One stored band of every
plane the painted layer reads comes to about 160 MB at 32768, and about 125 MB for the other
layers.

### How many threads

`--draw-threads N`; by default 8 (`DRAW_THREADS`), and no more than the machine has cores.
Before the pass the count is cut to what free memory holds: the free memory, less one sheet
(3 bytes a pixel, set aside though no sheet is held whole, section 42) and 2 GiB, must
hold the pieces in flight and the decoded bands they read (`pass_bytes`). The free memory is
the one `mapgen/pools.py` reads for the light bake and the cutter too. On Windows it is the
lesser of the free physical memory and the commit left, because an array commits its
whole size when it is allocated: with other work running, the commit ran out at 17 GB while
37 GB of RAM stood free, and an allocation failed. One more piece in flight of the default
512 columns costs about 0.045 GB, and 0.08 GB for the painted layer, scaled by the piece's
width (`PIECE_BYTES`); a pass paints its layers in turn over one ground, so a piece of it
costs its dearest layer's, and 0.015 GB more (`SEABED_BYTES`) for the second ground of a pass
that draws the painted layer and another (`piece_bytes`). The decoded bands come on top,
about 160 MB a band with the painted layer and 125 MB without at 32768 wide ("The band
stores"). Those figures come from the peak commit of windowed draws at 1 and 8 threads
("Column pieces" below); before the pieces, one more band in flight cost 1.9 to 4 GB at full
width. Memory no longer holds the default back on any machine that can hold a sheet. `1`
draws the pieces in turn. The run prints the count, and every layer's `meta.json` records it
as `render.draw_threads`, beside `cut_workers`.

### What the threads share, audited

| Shared | Why it is safe |
| --- | --- |
| The heights, lattices, water and void planes, and the rasters | Read only: numpy arrays, `r` memory maps, or band stores whose bands are read-only |
| The field | Its planes are decoded when it loads; the water planes are read in `_ground_sources`, before any band |
| A piece's ground | Its own piece's only. Every layer's painter reads it, and its arrays are read-only, so a painter that wrote to one would fail rather than change what the next layer reads |
| `PaintedGround`, `ReliefGround`, `RiverWater`, `OpenSea` | Built before the draw. The crowns' calibrated sprites, the water classes and the family targets are written in setup, never by a band |
| Random numbers | The satellite noise comes from a seeded generator, once per run in `layer_job`; the moss patches hash each pixel's position |
| numpy's error state | Per thread since numpy 2; the band code sets no warnings filters, which are process-wide |
| Palettes and colour tables | Module constants, read only |
| The sheets and the light's surface | Each piece writes only its own pixels of the sheets; the caller's thread writes the light's surface, a band at a time in order |

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

### One pass for every layer (2026-10-07)

The layers drew the same ground band by band, each layer again: the heights, the rocks and
the overlay, the water surface, the meshes and the water over them, the borrowed shading.
That was about half of each layer's draw but the painted layer's. A run now draws every
layer it draws in one pass over the bands (`render_layers`), and each band composes its
ground once (`render/ground/surface.py` `band_surfaces`) before every layer's painter colours it
(`render/draw/painting.py` `paint_band`), in the order of `--layer`.

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
  (light-and-crowns.md section 29, "One capture"), and is baked a block row at a time as the
  bands come in (section 42). The progress lines follow: one `draw` stage, then `light`, then
  each layer's `cut` (maps_contract.md §5.3).
- **No sheet waits.** Each band, once settled, goes on to its layers' tile trees (section
  42), so no layer's sheet is held whole, in memory or in a file.
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

### Column pieces (2026-10-07)

A band of the full-size sheet is 288 rows by 32,768 columns with its halo, 38 MB an array of
float32, and its draw makes hundreds of such arrays, each far larger than the processor's
caches and each a fresh allocation the system pages in. Each band is now drawn in pieces of
`--draw-columns` output columns (`PIECE_COLS`, 512 by default), each `PIECE_HALO` columns
past its edges either side and cropped after, as the bands are across rows ("The halo"
above): 288 by 544, 0.6 MB an array. The pieces are the pool's items, in band order: a
full-size band is 64 of them, a 2048 band 4, and the threads draw the pieces of one or two
bands at a time, where they drew one band each before.

- **What a piece reads.** The window's column taps and pixel centres, the layer jobs'
  column data (the biome columns, the painted ground's rock and footprint taps) and the
  rasters a band reads by rows (the direct, top and mesh rasters and the painted ground's
  rock families) are cut to the piece's columns. The painters' `BandGrid` carries the
  piece's place on the sheet, which the Titan trees and the rock tops' patches read, and its
  `band` is the piece's rows and columns of a raster cut to the window.
- **What waits for the band.** The light's surface, the seam trace and the regime table take
  a whole band at a time: each piece hands its output pixels of the planes they read
  (`PieceOwed`), and the caller joins a band's pieces in column order once the last is in
  (`settle_band`). The seam trace's second differences, its 32-texel neighbourhood and its
  thinning, and the regime table's float sums, see the band as they did, so the sidecars'
  numbers are the same, and the seam trace needs no halo of its own.
- **Nothing is computed from a piece's extent.** Every step but the stencils is a function
  of a pixel's own place: the samplers gather by column, the noise and the moss patches hash
  positions, the crowns and the waterfalls are placed from each pixel's centre, and the void
  and the wet-pixel shortcuts decide per piece only to skip work that would leave a pixel as
  it was.
- **Tested equal.** Synthetic sheets drawn in pieces of 7 to 500 columns on 1 to 64 threads
  give the bytes, the light's surface and the seam and regime numbers of whole rows, every
  layer of a pass with its meshes included; and each stencil's reach along a row, measured by
  moving one column, is its entry in the registry.

**One step read its row's width: the painted layer's luminance.** Its tone shoulder summed
each pixel's luminance with BLAS, in an order that followed the row's width, so the pieces
moved 11 of the full-size sheet's 1,073,741,824 painted pixels by one level against whole
rows. Every sum of a pixel's channels is now elementwise in one fixed order ("Fixed-order
sums" below), the same at any width.

**Cutting the samplers to the piece.** `terrain.sample` read a band's whole rows of a field
plane and converted them to float32 before it gathered the columns its taps read, once a
call. In pieces that work repeats for every piece: at 2048 a band spans about 1,050 of the
field's 7,500-texel rows, and four pieces of 512 drew the 2048 sheet in 24.4 s against 18.0 s
in whole rows. The slab is now cut to the columns the taps read as well (`_slab`), and
`reads_nothing` looks only at those texels; the samples are the same bits.

**Measured** (2026-10-07, build 502094), each run alone on the machine:

- The 2048 render, all five layers, lit: every tile, light tile and sidecar has the content
  it had before the pieces, at the default width and in pieces of 256 columns. The draw took
  15.6 s against 18.0 s, and the run's CPU 493 s against 574 s.
- Three windows of the full-size sheet, every layer, unlit, on 8 threads: every array is
  byte-identical to the draw before the pieces, as it was in pieces of 2048 on 2 threads.
  Seconds against the one pass in whole rows, and the peak commit of the pass over the
  process's own:

  | Window | Whole rows | Pieces of 512 | Ratio | Peak commit, GB |
  | --- | --- | --- | --- | --- |
  | 16 bands over the densest water edges, half the width | 68.2 | 47.0 | 0.69 | 15.8 to 2.1 |
  | A full-width strip of 4 bands | 40.6 | 21.3 | 0.53 | 15.1 to 1.7 |
  | The first two bands, full width | 25.9 | 8.6 | 0.33 | 6.5 to 1.1 |

  The strip and the first bands gain most because whole rows drew them on as many threads as
  they had bands, 4 and 2; the water window kept all 8 busy either way, so its ratio is the
  one a full render sees. The run's CPU, its preparation included, fell from 785 to 530 s,
  and its peak commit from 21.1 to 8.6 GB.
- Piece widths against each other, before the samplers were cut, on 8 bands of the water
  window at 16,384 columns: on one thread whole rows took 96.2 s, pieces of 2048 92.0 s, of
  512 77.7 s and of 256 76.0 s; on 8 threads 36.1, 31.8, 30.2 and 33.2 s, with peak commits
  of 14.0, 2.4, 1.2 and 1.0 GB. On a full-width strip of 4 bands one thread took 82.9 s whole
  and 61.4 s in pieces of 512. 512 is the default: the fastest on 8 threads, in half the
  memory of 2048.

### Fixed-order sums (2026-10-07)

A pixel's colour sums depend only on its own channels, never on the width of the array it is
drawn in, the piece, the thread or the BLAS library. Every sum of a pixel's colour channels in
the draw and the paint is written out, elementwise, in one order:
`(c0 w0 + c1 w1) + c2 w2`, each product and each sum rounded to the input's float type on its
own, with no fused multiply-add (`colour.weighted_channels`).

- **What was BLAS.** numpy hands `@` to BLAS, and OpenBLAS sums a pixel's three products with
  fused multiply-adds in an order its kernel picks. For the luminance (`colour @ LUMA`, a
  matrix-vector product per row) that order followed the row's width: up to 16,384 float32
  columns one order, longer rows another, the last `width mod 8` columns a third. For the
  3 × 3 OKLab matrices it was one fused chain at every width on this machine, but still the
  kernel's choice: another BLAS, version or processor rounds differently, and a numba or GPU
  kernel could match it only by copying BLAS.
- **What is written out.** `colour.luminance` (the tone shoulder in `by_luminance`, the
  relight of linear-light styles and `unit_luminance`), `colour.through_matrix` and OKLab both
  ways (each output channel one `weighted_channels` of a matrix row, on contiguous channel
  planes), the water table's mouth blends (class by class, in class order), the crown and
  layer transfers' 2 × 2 turns (`calibration.turned`, where the layer transfer had numpy's
  `einsum`) and the artwork borrow's Rec. 601 luma.
  The OKLab inverse matrices are written out as the float32 values LAPACK gave, so no library
  computes them at import.
- **Float32 stays.** The order makes the result exact and repeatable; float64 would not, by
  itself, and would cost twice the memory of every colour plane a band holds.
- **The 3 × 3 mean.** scipy's `uniform_filter` keeps a running sum along each row, so where a
  row's values span many orders of magnitude, a water cover's Gaussian tail say, a pixel's
  mean followed where its row or piece began. The rock tops' ramp and the coral specks now
  take `surfaces._mean3x3`: three taps summed in float64 in one order per axis and rounded
  to the input's type, which is scipy's result wherever its running sum was exact.
- **Porting it.** numba without `fastmath` keeps each multiply and add apart; its weights must
  be float32 constants, since a Python float promotes the product to float64. CUDA contracts
  `a * b + c` into a fused multiply-add by default (nvcc `--fmad=true`, NVVM `-fma=1`), so a
  GPU kernel compiles with `--fmad=false`, or writes the products as `__fmul_rn` and the sums
  as `__fadd_rn`, which are never contracted; with IEEE division and square root and no
  flush-to-zero, as without `--use_fast_math`. OKLab's cube root and cube are transcendental
  calls, outside what this fixes ("Not covered" below).
- **Tested.** `tests/mapgen/test_fixed_order_sums.py`: the sums against a one-scalar-at-a-time
  reference, bit for bit; pinned bits for colours whose fused orders round elsewhere; the
  tone shoulder and OKLab both ways at 33,000 columns, whole and in pieces of 32,768, 16,385
  and 7 on 1 and 4 threads; a painted band 16,500 pixels long and a relief band drawn whole
  and in pieces; a pass of all five layers over a band of the full-size sheet 16,500 columns
  wide, drawn whole and in pieces of 16,385 and 512 (`--draw-columns`) to the same bytes; the
  3 × 3 mean against scipy; and that no `@`, `dot`, `matmul`, `einsum`, `tensordot`, `inv`,
  `solve` or `cg` is left in `colour.py`, `render/`, `palette/`, `lighting/` and
  `terrain/sample.py` but the open sea's membrane, whose `@` is a sparse product (below). The
  two band tests fail with the old luminance. The pass does not: rounded to bytes, the old
  order moved about one pixel in 10^8 (11 of the full-size sheet's, "Column pieces" above).
  `tests/mapgen/test_fixed_solve.py`: `fixed_sum` against its order one Python float at a
  time, `jacobi_cg` against scipy's `cg`, and a membrane solved in processes at 1, 4 and the
  default BLAS threads to one digest, where scipy's `cg` gave three.

**What it moved** (2026-10-07, build 502094), against the pixel batch's baseline. Until
game-painted 20, relief and relief dark 7, and terrain and satellite 9, these sums were BLAS's:
they share the pixel batch's one bump, as no map was rendered between the two.

- The luminance alone moved no byte: none in the 2048 render (painted, lit, `@2x` and
  unlit), none in the full-size sheet's densest water window, 67 million pixels.
- All of it, in the 2048 render over every level: painted 517 lit, 503 `@2x` and 540 unlit
  pixels, the lit ones by up to 2 levels; relief 63, 61 and 90; relief dark 43, 43 and 37;
  terrain 12, 12 and 13; satellite 9, 9 and 12; the light's tiles none. Terrain and
  satellite move only through the borrow's luma.
- Three windows of the full-size sheet, unlit, 117 million pixels: painted 10,059, relief
  1,579, relief dark 921, terrain 375 and satellite 327, each by one level. Scaled by area,
  the whole sheet moves about 80,000 to 120,000 painted pixels (about 0.01%), 15,000 relief,
  9,000 relief dark and 3,000 to 5,000 each of terrain and satellite.
- Time: per call on one BLAS thread OKLab costs about a quarter more, its cube root
  dominating either way. Drawn in one process, alternating, two bands of 16,384 columns on 2
  threads took 13.4 s with the sums written out against 15.1 and 15.6 s with BLAS.

**The open sea's membrane.** `palette/water/open_sea.py` `membrane` solved its 579,602 cells
with scipy's `cg`, and the answer's bits followed OpenBLAS's thread count, which defaults to
the machine's cores (24 here, OpenBLAS's cap). Traced step by step at 1, 4 and 24 threads, the
first iteration's vectors were the same bits: the sparse product (scipy's own loop), the
Jacobi step and the updates are elementwise or single-threaded. What differed was every
reduction `cg` hands to BLAS: `np.linalg.norm(b)` (and so the stopping tolerance), the
residual's norm, `r . z` and `p . q`, because OpenBLAS splits a long dot product over its
threads and adds the parts in another grouping. From the second iteration on the steps
differed, and the answers ended up to 3e-13 m apart. numpy's own sums of the same products
were the same at every thread count.

- `terrain/solve.py` `jacobi_cg` is scipy's loop step for step, its stopping rule and its
  first guess included, with every dot product and norm a `fixed_sum`: the products added by
  halves, each level the second half onto the first, elementwise, an odd last term carried
  on. The order follows the length alone, so the answer is the same bits at any thread count
  and in any library; a numba or GPU port adds in the same pairs. `relax` in
  `terrain/harmonic.py` uses it too.
- Its answer lies within 1.4e-13 m of scipy's at 24 threads, nearer than scipy's own at 1 and
  24 threads (3.2e-13 m), and converges in the same 1,059 steps to the same tolerance. The
  bed is written into the float32 lattice, which rounds both answers alike: the heights,
  the ground and the water planes were the same bytes with either solver at 1 and at 24
  threads, so no map changes.
- It is faster: 4.5 s against scipy's 6.6 s at 24 threads and 4.9 s at one, best of three
  on the captured system, as it reuses its scratch arrays. The membrane is solved once a
  render, at any size.
- The other sparse solves, `spsolve` in `terrain/harmonic.py` (the seam band, the holes and the
  perched water: 159 systems a run, up to 55,000 cells), gave the same bits at 1, 4 and 24
  threads, every one of them.

Not covered:

- **Transcendental functions.** numpy's float32 `cbrt`, `power` and `exp` take the processor's
  vector paths, which differ between processors (AVX-512 against AVX2). They are elementwise
  and read no width, checked at every width and offset when the pieces came, but they are not
  the same on every machine.
- **Filters and rasters.** scipy's Gaussian filters sum each output pixel's taps directly, the
  same in a piece with its halo; how their C build rounds is the build's. The cliff, mesh and
  crown rasterisers transform vertices with float64 `@`; they write the raster caches, not the
  draw.

### Known limits

- More threads than 8 drew little faster, and relief slower: the bands wait on memory, not
  on cores. In pieces, 16 threads drew a full-width strip in the time 8 did.
- Pieces drew a band 1.2 to 1.35 times faster on one core than whole rows, short of the twice
  the performance plan had hoped for: much of a band's time is per-pixel arithmetic and
  gathers from the field, which a piece's size does not change.
- The decoded bands the threads need, and the pieces in flight, are memory the serial loop
  did not take; the thread count is cut to fit, and `--draw-threads` lowers it further.

## 41. Compiled kernels: the light, the sampler's gathers and the painters (2026-10-07)

The light bake spends its time in two loops: the horizon march, 32 directions on the ground
and 32 on the crowns at a few hundred steps each, and the sky view. The draw spends about a
fifth of its terrain time in the sampler's gathers. Each of them now also exists as a numba
kernel: the same arithmetic, compiled, a row at a time, with none of the temporary arrays
numpy makes for every step. So do the crown stamps and the water of every style, the
painters that took the most of the draw ("The painters", below), and, since 2026-10-08, the
max-Z scan the direct, top, mesh and Titan caches are rasterised with ("The raster passes",
below). The numpy code stays where it was, as the reference the kernels are proven against
and the path a machine without numba runs.

### The switch

- `mapgen.jit.kernels_on()` decides, at every call. `MAPGEN_KERNELS=numpy` selects the
  reference; unset, or any other value, the kernels wherever `numba` imports. numba is in the
  `gen` extra, pinned; without it the reference runs. `cuda` also runs the light's loops on
  the GPU ("On the GPU", below).
- The two paths write the same bytes, so nothing a render writes records which one ran.
- A kernel module (`lighting/kernels.py`, `lighting/spans/kernels.py`, `terrain/kernels.py`,
  `terrain/maxz/kernels.py`, `palette/water/kernels.py`, `palette/painted/kernels.py`) is
  imported only once the switch says kernels, so the reference never loads numba. A test
  holds that.
- The light's spawned processes inherit the switch with the environment.

### Why the bits are the same

- Per pixel, each kernel does the operations its numpy code does, on the same types and in
  the same order.
  - The march: `(sample - near) * scale`, the maximum, NaN when either side is (as
    `np.maximum` has it). The bilinear sample is
    `(a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy` with the same float32
    fractions.
  - The span march and its sky view (`lighting/spans/kernels.py`, light-and-crowns.md
    section 29, "Arches as spans") do the same per pixel for the ground, then each span
    sample's four-pixel minimum and maximum, its two tangents and the band's rules, in
    `spans.py`'s order. A row with no span in reach skips that work, which changes nothing.
  - The sky view adds a division and a square root. IEEE rounds both correctly, so they are
    as exact as an addition.
  - The gathers add the taps in numpy's order and round to float32 after each one, as
    numpy's in-place add does, float64 weights included. PCHIP's slopes and Hermite basis
    follow `pchip_slope` and `pchip_1d` term by term.
- Everything per step, the steps themselves, their offsets, fractions and weights, is worked
  out by the numpy code that did it before, in Python, and handed to the kernel. The
  transcendentals (the directions' sine and cosine, the horizon's arctangent and degrees) stay
  in numpy.
- No fastmath, so no fused multiply-add and no reassociation; numpy's error model, so a
  division by zero is infinite rather than an exception.
- So no kernel moves a last bit. The kernels that may move one, the transcendental ones the
  plan allowed with a measured list, are none.
- One difference exists and cannot show. Where `np.maximum` meets +0 and -0, numpy's vector
  loop and its scalar tail return different ones; the kernel keeps the first. Every reader of
  a horizon rounds it to a byte or compares it, and the two zeros are equal there.
- numba has no float16 arrays. A float16 source reaches a kernel as float32, which is what
  numpy's path reads too.
- The kernels read without bounds checks. The light's feed therefore refuses a halo its steps
  overrun, where numpy would fail on the shapes.

### Threads, processes and compiling

- The kernels release the GIL (`nogil`), so the draw's threads run the gathers side by side.
- A gather reads the block its taps cover, cut to the piece's columns (section 40, "Column
  pieces"), copied into one C-ordered array for the kernel.
- `cache=True`: numba keeps the compiled code beside the module, in `__pycache__`
  (`kernels.*.nbi`, `kernels.*.nbc`), keyed on the source file and the CPU. A light process
  loads it instead of compiling it again.
- The first run after a change to a kernel module compiles: each kernel once per argument
  types, about 0.3 s apiece, 1.1 s for the first because numba starts up with it. Every
  signature the light and the draw use took 2.7 s together, under the exclusive render lock.
  Loaded from disk they take 0.4 s, nearly all of it numba starting up, once per process.
  In the windowed gate, which drew every window twice, a first draw took 0 to 0.4 s longer
  than its second, except two water windows (1.4 s and 3.5 s); the reference's own pairs
  differ by up to 0.8 s.
- A change to `mapgen.jit`'s options does not invalidate that cache: delete the files, or
  touch the kernel module.
- Each signature's code is a file named by a digest of the signature's key
  (`jit.keyed_cache_files`, 2026-10-07). numba numbers them in the order it compiles them,
  and two processes compiling different signatures of one kernel at once can take the same
  number: the index then hands one signature the other's code, which reads its arguments as
  the wrong types, garbage or a `TypeError`. The test suite's workers do exactly that, and so
  can light processes or two renders on a cold cache. Measured with stock numba 0.68: eight
  processes compiling eight signatures at once left a cache that handed out the wrong code in
  3 of 7 rounds; named by key, in none of 6. A process that saves the index over another's
  only drops that one's entry, which is compiled again. Code files of an old kernel source
  are no longer overwritten by number; they stay until deleted, which is always safe.

### Measured (2026-10-07, build 502094)

Each pair ran under the exclusive render lock, the reference first, minutes apart, with
numba 0.68.0; other work on the machine averaged under two cores throughout. The code drew
each layer alone and in whole rows: these numbers predate the one pass and the column pieces
(section 40).

**One full-size light block.** A 32768 render's block: 4096 native pixels, 2048 at half
resolution plus the march's 330-pixel halo each side, synthetic relief (the march costs the
same whatever the heights are), one process.

| Part | numpy, s | Kernels, s | Faster |
| --- | --- | --- | --- |
| 32 ground horizons, with slabs | 77.9 | 5.7 | 13.6× |
| 32 crown horizons | 11.7 | 2.0 | 5.7× |
| The sky view | 1.73 | 0.12 | 14× |

The block's other work (normals, encoding, tiles) is unchanged, and the light at 32768 was
not rerun whole: the windowed gate below draws unlit.

**The 32768 sheet in windows** (G2: three windows of each of the five layers, 8 threads,
each window drawn twice). The draws took 162 s against 211 s, 23% less. Projected to the
whole sheet from the full-width window:

| Layer | numpy, s | Kernels, s |
| --- | --- | --- |
| terrain | 250 | 166 |
| satellite | 294 | 205 |
| relief | 323 | 235 |
| relief-dark | 313 | 223 |
| painted | 824 | 727 |
| All five | 2,004 | 1,555 |

The water windows gain most, 28% to 33% for the four plain layers. The run's peak commit
fell from 19.2 to 19.0 GB; three of the fifteen draws peaked 0.6 to 1.1 GB higher than
before, the others within 0.4 GB either way.

**The 2048 render** (G1, lit, five layers). 493 s either way, 525 CPU seconds against 589.
Nearly all of it is the rasters the run prepares first, which no kernel touches. The draws
of satellite, relief and relief-dark fell from about 3 s to about 1 s, painted from 15.2 to
13.1 s, and the light from 14.8 to 13.1 s. Terrain, the first layer drawn, rose from 3.0 to
7.7 s: that run started with no compiled code on disk and paid the compile there.

### Checked

- G1 at 2048 (all five layers, lit): all 1,125 tiles the same bytes as the reference and as
  the pixel-batch baseline, and the six sidecars the same apart from their timings, with the
  kernels and with `MAPGEN_KERNELS=numpy`.
- G2 at 32768: all 15 windows the same SHA-256 as the baseline, both ways, and each window's
  two draws the same.
- `tests/mapgen/test_kernels.py` compares every kernel with its reference byte for byte:
  five azimuths, crowns, the span march under both fades and its sky view, three sky
  spacings and a strided view; four source types, three tap kinds and two scales for the
  gathers; PCHIP on integer and float sources; float64 weights; a column piece against the
  same columns of whole rows.
- The full-size light block above came out the same bytes both ways.

### The painters (2026-10-07)

A profile of one pass over two bands of the full-size sheet (rows 17,408 to 17,920, every
column, all five layers, unlit, on one thread, after a pass that warmed it up) spread the
draw over many painters, none of them a tenth of it. The four that cost the most, and that
are arithmetic once their transcendentals are worked out, now run as kernels. Measured on
build 502094 by running every call of them both ways, back to back and alternating which
went first, on that strip and on two bands of the water window of section 40 (rows 18,432
to 18,944, columns 10,240 to 26,624), seconds over the pass:

| Painter | Layers | Strip, numpy → kernel | Water, numpy → kernel |
| --- | --- | --- | --- |
| `crown_stamp.stamp_crowns`: the crowns, tree by tree | painted | 2.71 → 0.43 | 3.02 → 0.48 |
| `shore.water_composite`: the water | terrain, satellite | 2.94 → 0.37 | 2.13 → 0.27 |
| `relief._water`: the water | relief, relief-dark | 1.66 → 0.44 | 0.80 → 0.27 |
| `optics.mix_underwater`: the colour under the water, and the mix | painted | 1.88 → 0.99 | 1.31 → 0.67 |
| The four | | 9.18 → 2.22 | 7.25 → 1.68 |
| The whole pass, all five layers | | 31.9 → 24.9 | 25.5 → 19.9 |

The pass figures are the pass as measured, both ways run, less the other way's four painters.
All 1,152 calls gave the same bits both ways. The pass is 22% shorter on one thread; on 8
threads the stamps also stop holding the GIL tree by tree. The 8-thread draw has not been
timed yet with no other run on the machine.

- `terrain/kernels.py`, `stamp`: `_stamp` for every tree of a band in turn. What `_stamp`
  works out per tree before it reads a texel (the crown's centre and the rows and columns it
  may reach, its mip level and texel, its yaw's cosine and sine) is worked out by numpy for
  all the band's trees at once (`crown_stamp._placements`), with the float64 operations
  `_stamp` does one tree at a time. The mips are read from one float64 atlas
  (`CrownSet.atlas()`, 53 MB on build 502094, which holds a float32 texel exactly), laid out
  again only when the set's `levels` is replaced.
- `palette/water/kernels.py`: `water_composite` and the relief's `_water` per pixel, the wet
  band, the stroke and the foam included, and the mix by the cover.
- `palette/painted/kernels.py`, `underwater`: `mix_underwater` on the pixels it mixes: the bed
  through the optics, the carpet, the sunk crowns, the open sea and the opaque water, then the
  mix. The band's own planes (`g`, the cover, the class optics' tint, body and deep colour, the
  crowns' colour) are read in place, without the gather the numpy painter makes; a band past
  `WET_MOST` is read as flat views.

Left in numpy, measured in the profile:

- `blend_regimes` (0.12 s), `composite_top` (0.13 s) and `composite_meshes` (0.22 s), together
  1.3% of the pass: about 5 ns a pixel, so a kernel would save a few tenths of a second.
- The colour spaces (`oklab`, `linear_from_oklab`, `linear_to_srgb`), 5.0 s: matrix products,
  now summed in one fixed order (section 40, "Fixed-order sums"), and cube roots and powers.
- The relief's shade and slope rock, the satellite's colours, the painted ground's rock and
  meshes, the crowns' light: each a twentieth of the pass or less.

**Why the bits are the same.** The rules above hold, and:

- Every transcendental stays in numpy: the crowns' cosine, sine and `log2`; every `exp` of the
  water (the fade, the bed's and the sunk crowns' transmission, the carpet's, the open sea's
  and the opaque water's); the relief stroke's power 1.5. They are worked out on the pixels
  the painter reads and handed to the kernel. The power is worked out only where the cover is
  neither 0 nor 1; there `4 c (1 - c)` is +0, and so is its power.
- numpy's cosine, sine and `log2` give an element of an array what they give it alone: the
  placements of all 97,689 trees of the paint store equal `_stamp`'s own, one tree at a time,
  at sheet sizes 2048, 8192, 32768 and 65536.
- The mix makes the numpy painter's choices: `shore.wet_mix` mixes only a band's wet pixels
  when under a third of it is wet and every pixel above, and the wet painters paint the whole
  band past `WET_MOST`. A dry pixel keeps the ground's value either way.
- A kernel takes float32 planes, colours and exposure only, and float64 pixel centres for
  the crowns. With any other the numpy painter runs, whose float types follow its inputs'.
- `np.clip` and `np.maximum` are reproduced with their NaN rules; the signed zero above can
  meet the crowns' dome and top, and cannot show there either.

**Compiling.** The painter kernels' signatures took 3.3 s together in a fresh process with
an empty cache (the stamps 1.0 s of it, numba starting up with them), and 0.44 s loaded from
disk. Their first test run, on parallel workers, left a cache that handed the painted water
another signature's code; that race is why each signature now has a file of its own
("Threads, processes and compiling", above).

**Checked.**

- G1 at 2048 (all five layers, lit), with the kernels and with `MAPGEN_KERNELS=numpy`: all
  1,125 tiles the same bytes as the base's numpy run, and the six sidecars the same apart from
  their timings.
- G2 at 32768: all 15 windows the same SHA-256 as the pixel-batch baseline, both ways, and
  each window's two draws the same, also with the kernels compiled from an empty cache while
  a G1 compiled them in another process.
- The A/B above: 1,152 calls on real bands of the full-size sheet, the same bits both ways.
- G1 at 8192, with the kernels and the light on the GPU against `MAPGEN_KERNELS=numpy`: all
  18,085 tiles the same bytes ("On the GPU" below, "Checked").
- `tests/mapgen/test_paint_kernels.py` compares each painter kernel with its reference byte
  for byte: trees of three species at every scale, yaw and lean over three sheet sizes and
  windows that cut them, mips replaced after a stamp; the plain styles' water with and
  without a stroke, the relief's tinted or not, the painted style's with each of the class
  optics, the carpet, the sunk crowns and the opaque water, each on bands from all wet to all
  dry, mixed both ways; and float64 planes, which run the reference.

### On the GPU (2026-10-07)

`mapgen renders --gpu` runs the light's horizon march and sky view as CUDA kernels, in each
light process. Everything else runs as above: numba's kernels where they exist, numpy
elsewhere. The CPU path stays the default and the reference, and the tiles are the same
bytes either way.

- **The switch.** `--gpu` sets `MAPGEN_KERNELS=cuda`, which the light's processes inherit;
  setting it by hand does the same. `jit.gpu_on()` says CUDA where the switch says `cuda`
  and numba's kernels are on. `--gpu` checks at once, in under a second, that numba and CuPy
  import and that a kernel compiles and loads on a device, and refuses with exit code 12
  (`jit.NO_GPU`) and the reason on stdout when one does not (section 20, "Refusals"): a run
  never finds out at its light. It was argparse's exit code 2 until 2026-10-07, which a
  wrapper could not tell from a bad command line. The reference and numba's path never import
  CuPy; a test holds that.
- **The log.** Nothing a run writes says where its light was marched: the light's
  `meta.json` and the sidecars are a numba run's, timings apart. So each light process counts
  its march and sky-view calls by where they ran (`gpu.ran`), each block hands its count back
  with its tiles, and a `--gpu` bake prints the sum once its block rows are in. At 2048, one
  block of 32 ground and 32 crown horizons and a sky view: `light: horizon and sky-view calls
  65 on NVIDIA GeForce RTX 3080; 0 ran on numba, the device out of memory` (measured before
  the crowns became spans, which numba marches; below, "Spans"). A run without `--gpu` prints
  no such line.
- **What it needs.** The `gpu` extra: CuPy (`cupy-cuda12x`) and NVRTC from
  `nvidia-cuda-nvrtc-cu12`, both pinned, on Windows or Linux on x86-64, and an NVIDIA
  driver. No CUDA toolkit. CuPy compiles `lighting/gpu.cu` once a process and keeps the
  compiled code on disk. The type gate reads CuPy through the stub in `typings/cupy/` and
  needs no `gpu` extra.
- **Why the bits are the same.** Each thread does for its pixel what `lighting/kernels.py`
  does for one element of a row: the same float32 operations, in the same order, from the
  offsets, fractions and scales numpy works out (the rules above). NVRTC compiles with
  `--fmad=false`, because its default fuses `a * b + c` into one rounding: a probe of
  100,000 such sums left 15,734 different without the option and none with it. Division,
  the square root and subnormals are IEEE's (`--prec-div`, `--prec-sqrt`, `--ftz=false`).
  The arctangent and the degrees stay in numpy, as every transcendental does.
- **What still differs, and cannot show.** A NaN the GPU makes carries CUDA's one bit
  pattern, where x86 keeps the payload of the NaN it came from. None arose in the tests, and
  every reader of a horizon rounds it to a byte, where the two agree.
- **Spans** take numba's span march: the CUDA march is the plain one, which a block with no
  span in its window runs.
- **Memory.** A call uploads its rasters and the steps, marches, reads the result back and
  hands the device memory back. One the device has no memory for runs numba's kernel, with
  the same bits. A light process with the GPU imports CuPy and opens a CUDA context: 0.65 GB
  more commit (0.33 GB working set) and 0.19 GB of device memory, so `light_workers()`
  counts `LIGHT_GPU_BYTES`, 0.7 GB, more a process.

**Measured** (build 502094, RTX 3080; the machine shared, its CPU about half busy, no render
lock: timings wait for the round's one exclusive run). One full-size light block, the bench of
"Measured" above, numba against CUDA, the same bits:

| Part | numba, s | CUDA, s |
| --- | --- | --- |
| 32 ground horizons, with slabs | 8.04 | 2.97 |
| 32 crown horizons | 2.80 | 2.81 |
| The sky view | 0.11 | 0.03 |

One march over that block, 219 steps, takes 3.2 ms in the kernel and 11.5 ms with its
transfers (3.4 ms to upload a raster, 3.9 ms to read the horizon back). numpy's arctangent and
degrees after it take 53 ms, so they are now most of a horizon's cost. The crowns' march is
short (its fade ends at 80 m), and there the transfers cost what numba's march does.

**The painters stay on the CPU.** A CUDA twin of `water_composite` gave the numba kernel's
bits, and took 1.36 ms against 1.81 ms for a 288 by 544 piece, and 60 to 93 ms against 73 to
101 ms for a whole band 32,768 wide: moving its planes to the device and back costs what the
numba kernel computes. The heaviest, `underwater`, is no better placed: on a piece from all wet
to a fifth wet, its arrays alone take 2.2 to 3.0 ms to move (14 to 23 MB up, 1.9 MB down),
against 3.0 to 7.1 ms for numba's whole call. So a GPU painter is worth at most about one
more draw thread, while the draw runs 8 that one GPU behind one bus would have to serve; and
the four painters with kernels are under a tenth of a pass (2.22 s of 24.9 s on one thread,
"The painters" above). A painter gains only when the band's planes stay on the device from
one step to the next, which is a different draw.

**Checked.**

- `tests/mapgen/test_gpu_kernels.py` compares the CUDA march and sky view with the reference
  byte for byte on the cases of `test_kernels.py`, plus a block whose width is not a whole
  number of thread blocks and a device out of memory. It also holds the switch, the flag and
  its refusal, the light's worker count, the count of where each call ran and the bake's
  line, and that CuPy loads only under `cuda`. On a machine without numba, CuPy or a device
  the kernel tests skip and say which.
- G1 at 2048 (all five layers, lit), with `--gpu` and without, side by side: all 1,125 tiles
  the same bytes as each other and as the pixel-batch baseline, and the six sidecars the same
  apart from their timings. Against that baseline both also add the light's `key`, which the
  kept light brought after it ("Kept light", section 29). The `--gpu` run's light process was
  seen on the device. At 2048 the light is one small block, and it took 12.4 s against 12.8 s:
  the march gains at full size.
- G1 at 8192 (2026-10-07), where the light is 2 × 2 blocks, baked a block row at a time as
  the bands come in (section 42): `MAPGEN_KERNELS=numpy` against `--gpu`, that is numba's
  painters and sampler and the CUDA light: all 18,085 tiles the same bytes, the light's 2,730
  among them, and the six sidecars the same apart from their timings. The `--gpu` run logged
  `light: horizon and sky-view calls 260 on NVIDIA GeForce RTX 3080; 0 ran on numba, the
  device out of memory`, 65 a block. 2048 bakes one block and G2 draws unlit, so this is the
  check over several blocks; the full-size light's 64 blocks are first checked by the full
  render after the round.

### The raster passes (2026-10-08)

The direct, top, mesh and Titan caches are `MaxZRaster`'s work, and until this date it ran on
one core: 2,175 s of a cold 32768 render on build 502094 (direct 1,605, meshes 359, Titan
115, top 96) while 31 of the 32 cores idled. Since the overhangs (light-and-crowns.md section
29, "Arches as spans") the direct pass made three passes over each band, its top, its
undersides and the floor under them, and each transformed every placement again: about two
thirds of its time. Three changes, and every cache is the same bytes.

**The scan and the fold on numba** (`terrain/maxz/raster.py`, its kernels in
`terrain/maxz/kernels.py`). Where the kernels are on, the raster passes rasterise into a
`KernelRaster` (`max_z_raster`); the field's cliff layer and the crown sprites keep numpy's
`MaxZRaster`.

- *The same samples.* A triangle is tested at the points of its bucket, `size + 1` square
  from the corner of its box, and a wide one tile by tile, with numpy's float operations in
  numpy's order. A Python float meets a float32 array as numpy casts a weak scalar, to
  float32 first, in the comparisons too.
- *The same folds.* numpy buffers its candidates and folds them once more than
  `RASTER_FLUSH` wait, checked after each `add` and after every 16 wide tiles. A fold keeps
  each texel's highest candidate, of two equal the later (a stable lexsort's last), and NaN
  over any (it sorts last, and then fails the `>`), and writes a texel only past what it
  holds. So of two equal heights one fold keeps the later source, two folds the earlier. The
  kernel counts its candidates as numpy buffers them and folds at the same points; between
  folds each candidate goes straight into the open fold, per texel its height, source and
  mark beside the list of texels marked, 11 bytes a texel while it is open. The order of the
  samples inside one `add` cannot show: one `add` has one source.
- *Fewer divisions.* A sample is in the triangle when `l1`, `l2` and `l3 = 1 - l1 - l2` are
  all at least `-1e-6`, `l1` and `l2` each a numerator over the triangle's `den`. IEEE
  division rounds correctly, so for a fixed `den` the quotient moves with the numerator
  alone: `n / den >= -1e-6` holds for exactly the numerators past one value. The kernel finds
  that value once a triangle, stepping from `-1e-6 * den` through the representable floats
  (`_threshold`, a step or two), and tests both numerators against it with an addition each.
  A sample that fails it fails numpy's test; one that passes is divided and tested as numpy
  tests it. On a dense band at 32768, a quarter of the samples get that far and 4% are in.
- *No buffers.* numpy's scan held a bucket's whole grid of samples in temporaries, the 16 GB
  peak of the Titan pass at full size.

**A placement's faces once a band** (`terrain/maxz/faces.py`, `rasters.band_placements`).
The direct band sorts each placement's triangles once for its three passes: those reaching
the band that face up, all of them where the winding is unknown, which the top and the floor
draw, and those facing down whose top is high enough over the rock's foot to be an
underside. They are kept as int32 row numbers. The transform, a 3×3 product per vertex in
numpy's own BLAS call, is done again for each pass, which costs less than holding a band's
vertices: holding them, with the faces as int64 triangles and twice the bands in flight, the
8192 preparation peaked at 12.7 GB against 6.3.

**Bands on threads** (`rasters_banded.in_band_order`, `RASTER_THREADS`). A band's planes
depend on the triangles that reach it alone, and its folds count its own candidates, so the
bands of the direct, top, mesh and Titan rasters are rasterised side by side, 16 at a time or
one a core, and written in order, no more than 16 waiting past the one being written. The
kernels release the GIL; the Python between them is a few percent of a band.

What did not pay: a row's samples tested into arrays first for the compiler to vectorise,
with and without the divisions, was a third slower than testing them one by one; a bucket is
at most 9 samples wide for most triangles, too short a row to vectorise.

#### Measured (2026-10-08, build 502094)

The raster preparation alone at each size (the bench's `rasters` mode: the renders command's
own functions in its order, the caches empty and numba's compiled code deleted first, so each
run compiles; about 3.5 s of the direct pass), under the exclusive render lock, master against
this code:

| Stage, s | 8192, before | 8192, after | 16384, before | 16384, after |
| --- | --- | --- | --- | --- |
| sweep and mesh decode | 36.0 | 42.2 | 37.7 | 36.2 |
| direct | 376.2 | 36.2 | 659.5 | 33.0 |
| top | 27.5 | 2.6 | 41.1 | 4.8 |
| meshes | 148.0 | 9.0 | 184.1 | 8.3 |
| Titan trees, at half size | 16.8 | 3.6 | 33.8 | 3.6 |
| the whole preparation | 612 | 102 | 963 | 93 |
| peak commit, GB | 6.68 | 6.66 | 7.61 | 6.86 |

The sweep and the decode are unchanged code; in the 8192 run the sweep had 0.8 of a core.
At 2048 (the G1 run, beside other work) the direct pass took 34.8 s against 401.2, the
meshes 15.9 against 178.8, the top 2.3 against 30.2 and the Titan trees 1.9 against 14.2:
eight bands, so eight threads at most, and a triangle no bigger than a sample costs its
setup whatever the size. Building G2's 32768 caches took 36.2 s for the direct pass, 14.5
for the top, 9.1 for the meshes and 2.6 for the Titan trees, against 1,605, 96, 359 and 115
in the cold full render of the base benchmark.

The full render at 32768, all five layers lit, cold (every cache empty, numba compiling),
against the base benchmark's: 1,638 s against 4,056, its preparation 351 s against 2,402.
The four caches took 118 s against 2,176: the direct pass 73.5, 34.5 of it the unchanged
sweep and mesh decode, the top 15.2, the meshes 21.9 and the Titan trees 7.9. The
preparation's peak commit fell from 16.1 GB, the Titan pass's temporaries, to 11.4, the
direct pass's 16 bands. The run's peak is the light's: 33.7 GB against 31.5, on 15 light
workers against 11, a count that follows the free memory (a base run on 16 peaked at 35.4).
The draw and the light, which this does not touch, took 21% less than in the base run,
which shared the machine with 5 to 7 cores of other work while they ran.

The direct pass reaches 8 to 12 of the 16 cores, and its bands spend about two fifths of
their time waiting. The scan is not why: 16 threads scanning a small raster ran 12 times as
fast as one. The transform between the kernel calls is part of it, numpy holding the GIL
for some of it (16 threads ran it 2.7 times as fast as one); the rest is not pinned down.
24 or 32 threads were slower than 16, one BLAS thread was slower, a shorter GIL switch
interval bought CPU and no time, and letting 64 bands run ahead of the writer instead of 17
bought nothing.

#### Checked

- Every file of the raster caches at 8192 and 16384, direct (all five planes), top, meshes,
  Titan trees, falls and rivers, the same bytes as master's, `meta.json` apart from its
  seconds.
- G1 at 2048, uncached, all five layers lit: all 1,131 files the same content as the
  baseline and all 1,125 tiles the same bytes; only the six sidecars differ in bytes, their
  timings.
- G2 at 32768 from caches this code built: all 15 windows the same SHA-256 as the baseline,
  drawn from master's caches.
- `tests/mapgen/test_raster_kernels.py`: the kernel raster against numpy's with folds forced
  every 37 and every 4,000 candidates, with and without a ceiling, at both sample offsets and
  an offset first row, wide, flat, tied and degenerate triangles, float64 vertices and a NaN
  height; ties across one fold and across two; instances and row subsets; the threshold
  against the division on 2,600 numerators each for seven `den`s from 1e-12 to 5e7; a
  placement's faces and extent under every facing and rise; whole direct bands (one and two
  sub-samples), top and mesh bands, both switch settings; a cache the same bytes on one
  thread and on four.

### Known limits

- The painters left in numpy above. A kernel for the colour spaces could add its sums in
  the fixed order, but its cube roots and powers would move last bits.
- On the GPU, each of a block's 64 marches uploads the block's rasters again, about a third
  of the call; kept on the device for the block they would cost one upload. numpy's
  arctangent after each march costs more than the whole call.
- Each light process opens its own CUDA context, 0.19 GB of device memory: 16 processes take
  3 GB of a 10 GB card before they march.
- The level sweep and the rock meshes' decode, 34 s at any size, are pure Python on one core,
  a third of the raster preparation at 32768 now. They read packages that do not depend on
  one another, so a pool of processes over them is the next step.
- The raster passes have no CUDA kernel; under `--gpu` they run numba's.
- A numba release is a new proof, which is why it is pinned: the bit tests in
  `tests/mapgen/test_kernels.py` and `tests/mapgen/test_paint_kernels.py` and a G1 against the
  reference come with an upgrade.

## 42. Cutting the bands as they settle (2026-10-07)

Until this date a run drew every layer's whole sheet, then baked the light from the whole
surface, then cut each layer's trees from its sheet: the light and each layer's cut were
stages of their own after the draw, and the sheets waited for them, 3.2 GB a layer at full
size in files of the run's scratch. Now each band goes on to its layers' trees as soon as it
is settled, and the light bakes a row of blocks as soon as the surface holds every row the
row reads. No sheet is held whole, there are no sheet files, and what is left after the draw
is the light's last rows, the lit trees' last bands and the coarse levels. The tiles and the
light are the same bytes.

### A band's way

- **The draw hands it on.** `render_layers` (section 40) takes a sink, `bands=`
  (`compose.BandSink`). Each band is drawn into rows of its own, made before its first piece,
  and handed to the sink once its pieces are in and it is settled: after its rows of the
  light's surface are written and its measurements merged, and in order. Without a sink the
  pass returns whole sheets as before, which the crops and the tests use.
- **The stream takes it** (`render/draw/stream.py` `RenderStream`). Without the light the band is
  the lit colour and goes to the layer's `tiles/` and `tiles@2x/`. With it, it goes to
  `unlit/` at once and waits for the default sun's terms of its rows (below); then it is
  relit (`light.relight_rows`, the arithmetic of section 29's baked copy, row by row) and goes
  to `tiles/` and `tiles@2x/`.
- **The cutter cuts it** (`tiles/cutter.py` `TileStream`, `tiles/levels.py`). Each sheet,
  the unlit and the lit of every layer, takes its rows in order on a lane: its tasks run one
  at a time in the order they came, and the lanes of all the sheets share a pool of 8
  threads. A level is resampled a strip at a time as soon as the source rows the strip reads
  are in, halo included, and the sheet drops the rows no level reads again. A level's rows
  are gathered into rows of tiles, and each row of tiles goes to the encode pool as soon as
  it is whole (section 17, "Cutting in parallel"). At 32768, `tiles@2x/` is cut from the
  16384 level, which a second sheet takes as its rows as they come.
- **The install waits for it.** After the draw and the light, each layer's trees are waited
  for, checked against their counts and renamed into place, `unlit/`, then `tiles/`, then
  `tiles@2x/`, as before.

### The light, a row of blocks at a time

- **When a row can bake.** The bake's blocks are those of section 29, 16 × 16 native tiles
  with a 150 m halo. Block row k reads the surface to the end of its rows and twice the
  horizon's reach past it (`bake.block_rows`): 660 rows at 32768, so the first of its 8 rows
  can bake once 4,756 rows are drawn. After each band the run queues every row whose reads
  are in (`LightingRun.drawn`; `lighting/bake.py` `LightBake.queue`). At 2048 the one block is
  the whole sheet, so the bake starts when the draw ends.
- **When a band is relit.** A lit band waits until every block row up to its rows is baked
  (`LightingRun.ready`). Its terms and land weight are then copied out of the scratch
  (`LightingRun.terms`), and the relight runs on the cutter's lanes.
- **After the draw** the rows not yet queued are queued, the waiting bands relit as their
  rows come in, the coarser levels baked from the native ones, and the pyramid renamed into
  place, as in section 29.
- **The same bytes.** A block reads and writes what it did; only when it runs changes. A
  band is relit a row at a time with the terms of its own rows, and the luminance reads no
  row width (section 40, "Fixed-order sums").

### A kept light, read while it matches

A kept light (section 29, "Kept light") is installed when its key, the whole surface's digest
included, is this run's, and that digest is known only once the draw is done; a lit band
cannot wait that long. So a run reads a kept bake while it draws:

- `light.kept/surface.json` lists the digest and place of each band of the surface the bake
  was made from (`Surface.puts`). A kept bake whose key differs from this run's at most in the
  surface is a candidate (`KeptLight.candidate`).
- While the bands this run draws hash the same, in order, each block row whose reads they
  cover takes the kept terms. A block's terms depend only on the rows it reads and on what
  casts on them, which the key matched, so they are the terms this run's bake would write.
- If every band matched, the kept bake is installed and nothing is baked. The first band
  drawn otherwise ends the reading: every row whose reads are in is queued on this run's own
  bake, and the bands already relit by the kept terms stand.
- A kept light from before this date has no `surface.json` and is baked again once.

### Memory and disk

Estimated from the sizes, not measured at full size:

- **The rows a sheet keeps.** A strip of the coarsest level, 8 of its 256 rows out of 1,024 of
  the sheet's, reads 388 rows past either edge, so a 32768 sheet keeps about 2,100 rows, 0.2 GB.
  A lit layer has three sheets: the unlit, the lit, and the 16384 one, about 0.15 GB.
- **The bands waiting for the light.** A block row's 4,096 rows, its 660 rows of reach and
  the bands drawn while it bakes: about 20 bands, 0.5 GB a layer at 32768.
- **Rows on their way.** `put` waits while the rows queued on the lanes and encoding pass
  2 GiB (`QUEUED_BYTES`), or while free memory is short of them and 4 GiB more.
- So a full-size run of five lit layers holds about 5 to 7 GB of rows, where it held 16 GB of
  sheet files, and a peak working set of 12.9 GB while a layer's three trees were cut. The
  draw still sets one sheet's bytes aside before it counts its threads (section 40, "How many
  threads").
- **Disk.** `sheets.cache/` is gone, 16 GB of scratch at full size for five layers, and the
  Maps tab's disk check no longer counts it (maps_contract.md §4.2).
- **Pools.** The draw's 8 threads, the lanes' 8, the light's processes and the encoders now
  run at once. The light's process count is taken from free memory when its first row is
  queued, by then with the draw under way.

### Progress

The stages keep their order (maps_contract.md §5.3): `draw` while the pass runs, `light` once
it has ended, starting at the share of blocks already baked, then each layer's `cut`, which
is now the wait for its trees and their rename. A layer's `pyramid zN:` lines are printed as
its trees are installed, as before.

### Checked

- Tests (`tests/mapgen/test_parallel_cut.py`, `test_render_stream.py`): every level of a
  2048 sheet taken in runs of 1 to 300 rows is a whole resize, at scales 2 to 128, and the
  sheet never kept more than 1,300 of its rows; the stream is `install_pyramid`'s bytes on 1
  and 3 encoders; at 1024 in rows of 256-px blocks, the light's first row is queued after the
  second band and every tree and light tile is a whole bake's and cut's; a kept bake is read
  while the bands match and baked again from the first that does not, to a fresh run's bytes;
  a failure on a lane or in an encoder is raised and leaves no shared block behind.
- The 2048 render of all five layers, lit, on the numpy reference path (build 502094): every
  one of its 1,125 tiles and light tiles is the same bytes as before this change, and all
  1,131 files have the same content, the light's `key` and so its surface digest included.
  The draw took 19.6 s and the light 13.8 s after it, and no layer then waited for its trees.
  The run peaked at 9.21 GB of commit, as before: at this size the preparation decides that.
- Three windows of the full-size sheet, every layer, unlit (section 40): all 15 draws are the
  same bytes as before. Those draws cut no tiles.
- The artwork's 1,708 tiles are the same bytes.
- A full run that kept its caches, then a restyle from them into a new folder: the restyle
  read the kept light as its bands matched, installed it and baked nothing, and its 1,131
  files have the content of the same pair before this change.

### Known limits

- Not timed at full size. The draw, the light and the encoders now contend for the cores,
  and the presets' stage seconds for the cut and the light (maps_contract.md §4.3) wait for
  that measurement.
- The bands waiting for the light are held in memory, not in files.
- At 2048 and below the light is one block: nothing of it overlaps the draw.

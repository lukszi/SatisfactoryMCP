# Live sun light and tree crowns

Sections 29 and 36 of the [design spec](../../DESIGN.md): the lighting pyramid the page relights, and
the tree crowns drawn on the painted layer and cast into it. A section number below
resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

## 29. Live sun light (2026-10-05)

A render drawn with the light stores its colour without light and adds a lighting pyramid,
and the page relights it in the browser for any sun. One light, the sun; the page picks where
it stands.

Since 2026-10-06 every render bakes the light unless told not to: `--light` is the default
of `python -m mapgen renders` and `--no-light` turns it off; in the Maps tab the "live sun"
box starts ticked and the `render` preset's `light` option defaults to true. `--unlit`, the
old opt-in, still means `--light`. While the light was opt-in, a plain command-line run drew
a map the page could not relight. Every render mode takes it: each layer, each size,
`--kernel-only`, and `--restyle`, which bakes the light again because the raster cache does
not keep it.

### The model

`light = amb · sky · SVF + (1 − amb) · sun · max(n·L, 0) · (1 − shadow · (1 − fill)) / sin(max(el, 35°))`,
divided by the same expression for flat ground in the open, so flat ground at any sun is 1.

- **Lambert** is the shipped hillshade's own term. Relit at 315° / 45° with shadows and sky
  off, unlit terrain reproduces the baked hillshade to 2 levels (a test holds it).
- **Cast shadows** come from 32 stored horizon angles per pixel, interpolated between the two
  directions either side of the sun. A blocker counts fully up to 40 m away and not at all
  past 150 m (`FADE_M`): without the fade a low sun shadows a third to a half of the land. A
  100 m fade was tried at 16:00 on the four shared crops and reads almost the same, so 150 m
  stays.
- **Soft edge** (`SHADOW_SOFT_DEG`, 6°). A horizon goes from lit to shadowed over 6° of
  angle. The edge is constant in angle, so on the ground it grows with the distance from the
  blocker to the end of its shadow, `soft · H / sin² el`: under the 16:00 sun (24°) about
  13 m for a 20 m wall and 3 m for a 5 m rock (a test holds that a taller blocker throws the
  wider one). At the old 2° the edge was a step at every pixel, and at 16:00 whole valleys
  turned into flat polygons with stair-stepped edges.
- **Fill** (`SHADOW_FILL`, 0.35). A cast shadow keeps 35% of the sun's Lambert term: the sky
  around the sun and the light bounced off sunlit ground, both from the sun's side. Without
  it shadowed ground is the ambient term alone, the same for every slope, and a valley in
  shadow reads as one flat grey shape at the floor. 0.2 and 0.35 were compared at 16:00 on
  the cliff-lakes and abyss crops; 0.35 keeps the rock's shape inside the shadow.
- **Tree crowns** cast into horizons of their own (below). Only a style that draws the
  crowns reads them (`shader_light(layer)["crowns"]`, the painted style today); every other
  style is shaded by the ground alone. Before, terrain, satellite and relief showed the
  shadows of trees they do not draw: near-black blocks in the forests and dashes in the
  desert, five times as frequent beside a crown as away from one.
- **Sky view** within 10 m darkens the foot of a cliff and the floor of a gully. Larger radii
  grey whole valleys.
- **Normalisation** by `sin(max(el, 35°))`: without the clamp a fifth to a third of the
  pixels blow out at 20°.
- **Shadow floor.** The light passes through a soft maximum with 0.36 at a knee of 0.1
  (`SHADOW_FLOOR`, `SHADOW_FLOOR_KNEE`), so the darkest light lands at 0.36 to 0.40 instead of
  the 0.2 the bare model reaches in a shadowed gully.
- **Water** stays unlit: the land weight (one minus the water cover) blends the light out,
  from the tree crowns over it too (section 36, "Crowns and the water").
- **Per style.** The painted style lights in linear light with its own ambient, sky and sun
  colours; the shader undoes and reapplies the luminance tone curve of section 31
  (`tone_knee`, `tone_white`). Terrain, satellite and both relief styles multiply into sRGB,
  as the hillshade always did: ambient `SHADE_FLOOR / (SHADE_FLOOR + SHADE_RANGE · sin 45°)`,
  white light, no curve (`palette/lightparams.py`). Relief drawn unlit keeps flat ground at its
  ramp colour, so its live light is this sRGB approximation, not its own OKLab shade. One
  lighting pyramid serves every style.
- **One copy, in float32.** The baked fallback (`lighting/model.py`) computes as the shader
  does: the normalisation is the float32 that the page's `uInvNorm` uniform holds, and one
  tone curve (`colour.tone`) serves the shader's reference, the painted draw and its
  calibration. A height raster's Lambert term is always `lighting/hillshade.py` `sun_dot`:
  the hillshade, the relief suns and `surface_direct`; flat ground's is its `FLAT_SUN_DOT`.
  Until game-painted 20, relief 7 and terrain and satellite 9 the normalisation and the relief
  suns ran in float64. At 2048 float32 flips the baked direct byte for about 2 in 65,536
  normals and moves 9 to 14 lit pixels a layer by one level, and 14 pixels of each relief
  style drawn without the light; the unlit sheets and the lighting pyramid do not change.

The default sun is game noon, 225° / 62.25°. The game turns its sun about one fixed tilted
axis (`AFGSkySphere`, pitch `30 + 15 h`); `lighting/sun.py` and the page's `sun.ts` both
compute that path. The cartographic 315° / 45° is a one-click preset, not the default: the
artwork has no light direction to inherit.

### What is written

| Where | What |
| --- | --- |
| `<renders>/light/tiles/{z}/{x}_{y}.nrm.webp` | Lossless RGBA: east and south normal as `(v + 1) / 2`, sky view, land weight. An opaque tile drops the alpha channel, which a reader takes as land |
| `<renders>/light/tiles/{z}/{x}_{y}.hz.webp` | An 8 × 8 grey atlas of 128 px cells at half resolution, `255 · sqrt(deg / 90)`, WebP q75: cells 0–31 the ground's horizons, cells 32–63 the crowns' where they stand above the ground's, else 0 |
| `<renders>/light/meta.json` | The light axis (model constants and their digest), `occluder_layers` (the layers that read the crown cells; empty without crowns), tile counts, timings |
| `<layer>/unlit/` | The unlit colour, 1x only |
| `<layer>/tiles/`, `tiles@2x/` | The colour lit by the default sun: what a page without WebGL, and every older reader, draws |

Coarser levels are computed from the coarser surface, not by averaging encoded tiles: normals
from the downsampled heights, sky view and horizons by mean.

### The stage

The one pass that draws every layer (section 40) hands its heights and land weight to a
`Surface` (two memory maps in the light's scratch, 5.4 GB at 32768), each band its own rows
from whichever thread drew it, once, whatever layers the pass draws: the surface the seabed
rule draws (below, "One capture"). After the pass,
`lighting/stage.py` cuts the sheet into blocks of 16 × 16 native tiles, each with a 150 m
halo, and a process pool computes per block: the ground's horizons and the crowns' at half
resolution, sky view, normals, the native tiles, and the light at the default sun for the
baked copy, once with the crowns and once without. A block whose core is all water skips the
horizon march. Each layer then queues `unlit/` from a copy of the sheet, is lit in place by
the default sun with its own term while that tree encodes (`render/light.py`: the crowns' only
for a style that draws them), and queues `tiles/` and `tiles@2x/`. All three go through one
encode pool and are renamed into place in that order (section 17, "Cutting in parallel").

**One capture (2026-10-07).** The light does not depend on which layers a run draws, or in
which order. Until this date it did, in two ways:

- The surface was the first layer's as that layer drew it. The painted layer keeps the
  render-only meshes standing in the water, and the other styles leave them to the seabed
  (section 27), so `--layer painted --layer terrain` baked the painted layer's sea meshes
  into the one light every layer reads: terrain was relit around rocks it draws as water.
- The crown occluder came with the painted layer only, so `--layer terrain` alone baked a
  `light/` without the crowns' horizons, which the painted layer reads.

Now the light takes the surface the seabed rule draws: a band composes the seabed's heights
and water for the light beside the painted layer's own, whether a layer that draws the
seabed is in the pass or not (`render/surface.py` `band_surfaces`), and the painted layer
drawn unlit always keeps the default sun on the meshes only it draws (section 36, "Coral
trees are no crowns"). The crown tops come from the paint
store whenever there is one: the painted ground's plane when that layer is drawn, else the
store's (`render/light.py` `crown_tops`). A run without a paint store still bakes no crown
cells. A full run draws terrain first and the painted layer, so its light and tiles are
unchanged.

Measured at 2048 against a full run of every layer, which the change leaves the same to the
bit. Before it, `--layer painted --layer terrain` differed from that run in the light (52 of
the 85 normal tiles, 63,702 pixels; 51 of the 85 horizon atlases), in 50 of the 85 painted
tiles (22,085 pixels, at most 55 levels; its `unlit/` in 13,073 pixels) and in 51 of the 85
terrain tiles (29,134 pixels, at most 137; its `unlit/` not at all). `--layer terrain`
differed in 68 of the 85 horizon atlases, the crown cells, and in nothing else. After it,
both runs' light and tiles match the full run's to the bit.

**Horizon cost.** The march takes bilinear samples up to 16 px and the nearest pixel beyond,
with in-place arithmetic: 0.14 µs per half-resolution pixel and direction, 6.3 times faster
than the all-bilinear reference, which it matches to a mean 0.10 to 0.17° and a 0.1 to 0.3%
difference in which pixels are shadowed at 25°. Measured again with the 6° edge on two
full-size windows from the 1 m field: all-bilinear is 5.2 times slower and changes the
horizon by a mean 0.15° (p99 2.4°), less than the q75 encoding does, and the shade by more
than 0.1 on 0.4% of the pixels; the nearest pixel stays. A rotated row sweep was built and
measured at 1.7 times slower than the reference: rotating the block grows it, and every
pixel of the rotated square is computed. One full-size block of the steepest 2 km box took
121 s on two threads of a shared machine, 69 s of it light terms and 52 s WebP encoding; 64
blocks project to 2.2 CPU-hours, about 8 minutes on 16 workers, before water blocks are
skipped. The research estimate for the ray march was 72 minutes.

**Strips, memory and workers (2026-10-06).** The v7 render's light took 2,903 s: 2,604 s for
the 64 full-size blocks in waves of 8 workers, at 3 to 3.5 GB each, and 298 s for the coarser
levels, which the parent computed one strip at a time while the pool waited. Four changes,
none of which moves a byte:

- **Strips.** The march and the sky view finish 64 rows (`horizon.STRIP_ROWS`) through every
  step before the next 64, so a step's arrays stay in cache. Before, each step was a pass
  over the whole block, about 700 a direction. The arithmetic per pixel and its order are
  unchanged.
- **A direction at a time.** A block encodes each horizon cell as it is marched
  (`stage._bake_horizons`): the atlas byte, the coarser levels' 2 × 2 mean, and the four
  float cells the default sun reads (`model.sun_cells`). The 64 cells are no longer stacked
  as 1 GB of floats with copies of it. The mean of one direction sums its four pixels as the
  stacked mean did, `(a + b) + (c + d)`, which a test holds.
- **Workers of its own.** `--light-workers` sets the bake's pool. By default it is one a
  core, at most 16 (`LIGHT_WORKER_CAP`), and no more than the free memory holds at 1.5 GB
  each (`LIGHT_WORKER_BYTES`, below), counted when the bake starts. On Windows the free
  memory is the lesser of the free RAM and the commit still available
  (`pools.free_ram_bytes`): a process that cannot commit fails with RAM to spare, which
  other processes' idle pools can bring about. `--workers N` sets it where `--light-workers`
  is not given, as it did before, and sizes the cutter's pool the same way.
- **A fed pool for the coarser levels.** The parent submits a strip's tiles, 4 to a task
  (`LEVEL_TASK_TILES`), and computes the next strips while they encode, up to `LEVEL_AHEAD`
  (4) strips ahead. Before, it waited for each strip, whose 1 to 8 tasks left most of the
  pool idle. The horizon bytes come from a 256-entry table of
  `encode_horizon(decode_linear(q))`, exact because both are elementwise.

Measured on synthetic terrain with crowns at the full-size spacing, 4096 px blocks, on the
16-core reference machine while other jobs ran:

| | Before | After |
| --- | --- | --- |
| One block alone | 111 to 131 s | 73 s |
| of which horizons, ground and crowns | 63 s | 32 s |
| of which sky view | 7.5 s | 2.9 s |
| of which WebP encoding | 36 s | 37 s |
| Peak working set / commit of a worker | 3.9 / 5.3 GB | 1.1 / 2.5 GB |
| A wave of 8 blocks / of 16 | 325 s (v7) / not run: 62 GB | 117 s / 184 s |
| Coarser levels from 8192 px, 8 / 16 workers | 126 / 121 s | 51 / 35 s |

So the 64 blocks of a full-size bake take about 940 s on 8 workers and 740 s on 16, and the
coarser levels, scaled from v7's 298 s by the ratio above, about 120 s and 90 s: the light at
32768 is about 1,060 s on 8 workers and 830 s on 16, against 2,903 s. These assume every
block marches, as v7's timing says it did. WebP encoding is now half a block's time.

A 2048 render of all five layers, `--workers 2`, with `--light-workers 2` after, ran before
and after: all 1,125 tiles match by SHA-256, the light pyramid's 170 among them, and the
sidecars differ in their timings only. Its light took 26.1 s before and 18.7 s after. A full-size block baked before and after writes the
same 256 tiles' 512 files, default-sun terms and coarser-level sources by SHA-256, and the
coarser levels from 8192 px the same 2,730 files.

**One BLAS thread a worker (2026-10-06).** numpy and scipy each load an OpenBLAS, and on the
32-thread reference machine each commits about 0.8 GB of thread buffers as it loads: 1.59 GB
of the 2.5 GB commit in the table above, about 25 GB over 16 workers, for a bake that makes
no BLAS call. The pool now starts its workers with `OPENBLAS_NUM_THREADS=1`
(`pools.one_blas_thread`, set while the pool lives and put back after; the parent keeps the
BLAS it loaded with), and a worker that imports both commits 0.04 GB. The same full-size
block bakes in 72.5 s at a peak of 1.08 GB working set and 0.98 GB commit, and writes the
same 512 files, terms and coarser-level sources by SHA-256; a full-size block under arches
peaks at 1.36 and 1.04 GB. `LIGHT_WORKER_BYTES` is 1.5 GB, the larger of those with room for
a block that has both. The cutter's encoders import no numpy, so their pool needs no such
setting (section 17).

**Normal tiles at WebP method 2 (2026-10-06).** The lossless `.nrm.webp` tiles are written
at effort 2 (`stage.NRM_METHOD`) instead of 4; the lossy horizon atlases keep 4. Lossless at
any effort, so the pixels are the same and only the file differs: on 200 z7 normal tiles of
renders-v7, re-encoded from their decoded pixels, 13.7 ms a tile instead of 35.0 (2.6×) for
5.7% more bytes, every tile the same RGBA pixels. A full-size block writes 256, so it saves
about 5 s of the 37 s its WebP encoding took; the normal tiles are about a third of the
light pyramid's bytes, which grows about 2%. The owner chose the trade on 2026-10-06.

**Step growth** (`STEP_GROWTH`, 1%). Past `FINE_M` (44 m) the march steps grow with the
distance. A plateau is sampled at the first step past its edge, so the horizon jumps from
step to step and a soft edge shows the jumps as bands, as wide as the gap. At 3% they read
as terraces in every penumbra at full size; at 1% they are 0.4 to 1.5 m and do not. 219
steps instead of 138, measured at 19% more for the ground's 32 directions on a full-size
window, because the bilinear steps near the receiver dominate. At 2048 every step is still
one pixel.

**Encoding.** q75 decodes within a mean 1.05° of the exact horizon (p99 6.4°), q90 within
0.48° (p99 2.9°) at 1.44 times the bytes. With the 6° edge the two relight the abyss crop at
16:00 the same to the eye, so q75 stays.

**Bytes.** The steepest 2 km box writes 78 KB of lighting per z7 tile, so a full map is at
most about 1.3 GB at z7 and less in practice, because open water compresses to almost
nothing. The crown cells are 0 wherever no crown stands above the ground's horizon; at 2048
they take the light pyramid from 18.8 to 24.2 MB. The unlit colour adds about half the
colour pyramid again.

### Scratch

While the run lasts, `light.cache/` holds 13.5 bytes a pixel, 14.5 GB at full
size: the surface (heights 4, land 1), the default-sun terms (3), and the bake's
half-resolution heights, land and sky view (1.5) and quarter-resolution horizons (4). With
a paint store the crown tops and cover add 5, 5.4 GB, whatever layers the run draws. The Maps tab's estimate counts them
as `presets.LIGHT_SCRATCH_BYTES` and `CROWN_SCRATCH_BYTES`, scaled by area; tests hold both to
what the stage allocates.

It is scratch for one run, not a cache. It carries no stamp, no flag keeps it (`--keep-direct`
keeps the raster caches only), and every lit run, `--restyle` and another layer set included,
bakes the light again. Nothing reads it after the run, so the question of storing it
compressed (section 39) is one of size and traffic while the run lasts, not of reuse.

| Planes | Written | Read |
| --- | --- | --- |
| `z`, `land` | By the first layer drawn, a band of rows at a time | By the bake, one block of 16 × 16 tiles at a time with its halo (`land` without one); `land` again by each layer's default-sun copy, 512 rows at a time |
| `occluder`, `occluder_cover` | By `crown_occluder`, 256 rows at a time | By the bake, a block with its halo |
| `terms` | By the bake's processes, a block each | By each layer's default-sun copy, 512 rows at a time |
| `zh`, `landh`, `svfh`, `hzq` | By the bake's processes, a block each | By each coarser level, a strip of rows at a time; each level replaces them with its own |

Until 2026-10-06 the crowns went to `crowns.npy` and `crown_cover.npy` and the bake copied
them into its occluder files, 10.7 GB in all at full size. `crown_occluder` now writes the
occluder files (`stage.occluder_planes`) and the bake reads them where they are. At full
size that is 5.4 GB less disk and 10.7 GB less traffic. A test bakes both ways to the same
bytes. A 2048 render of all five layers, `--workers 2`, ran before and after the change:
all 1,125 tiles match by SHA-256, the light pyramid's 170 among them, the sidecars differ
in their timings only, and the scratch at its peak went from 98.6 to 77.6 MB with every
other plane unchanged.

- **Where.** `--scratch-dir`, else `--cache-dir`, else beside the renders. The Maps tab does
  not pass it.
- **Traffic.** At 32768 the scratch takes about 22 GB of writes (27 GB with the old copy).
  Reads come to 18 GB for the bake's blocks (the halo makes a window 1.75 times its block's
  area), 8 GB for the coarser levels and 4.3 GB for each layer's default-sun copy, 47 GB in
  all for five layers once the page cache no longer holds them. At the 115 to 147 MB/s
  measured on the hard disk of section 39 that is up to 8 to 10 minutes of transfer a run,
  on the disk that also writes the tiles; an SSD does it in under a minute. These are counts
  of what each step touches, not a measurement of a full run.
- **Not compressed.** Measured on a 2048 preview with the painted layer (2026-10-06), the
  scratch at its peak is 77.6 MB and zstd level 1 with the shuffle of section 39, in 1024 px
  tiles, makes it 29.9 MB: 2.6 times. The planes the bake and the default-sun copies read
  most are dense: heights 1.8 times, terms 1.8, half-resolution heights 1.7, sky view 1.8,
  horizons 2.4. Level 19 gets the heights to 2.0, and the terms to 3.4 stored a channel at a
  time. Tiles of 256 or 2048 px move the ratio by less than 0.1. Only the land weight (25),
  the crown cover (6.9) and the crown tops (5.7, NaN away from trees) shrink much. The
  raster caches of section 39 are 89% zero, which is why they went 20 times smaller. Scaled
  to 32768 that is about 20 GB to 7 or 8 GB while the run lasts, at up to 2 s of CPU a GB to
  write and under 1 s to read. It would take a 2-D tiled store the bake's processes can
  write a block at a time, for space nothing keeps after the run; moving the scratch to an
  SSD with `--scratch-dir` takes all of the traffic off the hard disk instead.
- **The start of a run.** `lit.claim_scratch` empties a scratch a run left when it was
  killed, before the field is read. A render that has started drawing keeps its `z.npy`
  mapped until it ends, and Windows refuses to rename a mapped file, so a scratch in use is
  refused with a message instead. Elsewhere that check finds nothing. Before, the second run
  failed with `[Errno 22]` when it created its first plane, after the preparation; two runs
  started while both are still preparing still meet that way.
- **The end of a run.** `lit.light_run` closes the stage however the draw loop ends:
  finished, returned early, failed or interrupted. A run killed outright, as a job cancelled
  from the Maps tab is, leaves the scratch to the next lit run's start or, under the cache
  folder, to the tab's cache clear. So can a failure whose traceback still holds a plane
  mapped.

### Hooks

`bake_light` takes two optional rasters on the sheet's grid, both of which only cast:

- `occluder`: the crown tops in metres, NaN where empty, or `(top, cover)` with the covered
  share of each pixel as a byte. The crowns stand on the surface, each lifted by its cover
  (`horizon.crown_surface`), and cast into the crown cells under their own shorter fade
  (`OCCLUDER_FADE_M`, 25 to 80 m), received on the crown tops, so a crown is lit or shaded
  where the painted layer draws it. A crown cell keeps its horizon only where it stands
  above the ground's, which the shader's `max` makes exact and leaves the cells empty away
  from trees. `occluder_layers` names the layers that read them. The paint store's crown
  tops feed it (section 36; the mapgen README's "Tree shadows").
- `slabs = (ground, min_z, max_z)`: the surface without the floating geometry, and that
  geometry's underside and top. A slab extends a horizon only where its underside is below
  the horizon already reached, so an arch stops casting a curtain to the ground. On the arch
  crop at 16:00 the curtains go; the pipeline does not yet rasterise the arches' min-Z.

### The page

`litlayer.ts` draws a lit layer on one WebGL2 canvas in the base-map pane: per tile the unlit
colour, normals and horizons, relit by the shader with the arithmetic of `lighting/model.py`.
The z0 probe's `X-Map-Light` header carries the shader's numbers; the layer's `params.crowns`
switches the crown cells on, and a pyramid without `hz_cells` reads as the old 8 × 4 atlas
with no fill. A test holds the shader to the Python model's constants. Without WebGL2, or when the
context or the tiles fail, the layer falls back to the baked `tiles/` with a toast. Settings →
map holds the default sun (game noon, 09:00, 16:00 or map north-west) and the shadow and sky
switches. The sun button on the map opens a time-of-day slider on the game's path, the
presets, the switches and, under "advanced", a free compass with azimuth and elevation. The
button moves the sun for the visit; Settings keeps the default.

### Open

- The arches' min-Z raster, so `slabs` is fed by the pipeline.
- The sun in the fragment, so a link carries it.
- Faint diagonal bands at low sun from the q75 horizon encoding and the direction
  interpolation.
- A crown top takes the ground's horizon measured on the ground under it, not on its top, so
  in a valley a crown is shaded by terrain a little longer than it would be.
- Whether the satellite style draws the tree crowns, and so reads their shadows.
- A land weight for the crowns over water, read by the painted layer only, so they take the
  live light and a tree's shadow can fall on the water.
- Sun colour along the day, and whether the artwork style gets any light at all.

## 36. Tree crowns (2026-10-05)

The painted style drew trees as a soft canopy: one blurred disc per tree with a radius guessed
from the mesh name, under the rocks. Bamboo was guessed at 1.5 m and measures 4.4 m, and rock
hid most of the forest on cliff tops. Each tree is now drawn as its own crown: the species'
mesh seen from above, at its own position, yaw, scale and lean, in the colour its leaf texture
has. The Titan trees are static meshes, not foliage, and are left to the satellite additions.

### The paint input

`python -m mapgen paint` (generator version 3) adds three files and a `crowns` block to
`meta.json`:

| File | What |
| --- | --- |
| `crowns.rec.z` | One record per tree foliage instance, zlib: `x`, `y`, `z` (world cm), `yaw` (degrees), `scale`, `scale_z`, the trunk axis `axis_x`, `axis_y`, `axis_z` (a unit vector), `species` (uint16). 99,073 trees, 2.4 MB |
| `crowns.sprites.z` | One sprite per species, zlib: cover (uint8), crown top in cm above the pivot (uint16) and the material slot on top (uint8, 255 for none), at 0.125 m per texel. 53 sprites, 0.6 MB |
| `crown.i16.z` | The crown top on the heightfield's 1 m grid: world decimetres, `NODATA` where no crown stands. The tree-shadow builder's occluder. 10.1 MB |

`meta.json` `crowns.species[]` names each species, its mesh, its sprite's place in the stream,
its measured radius and top, its instance count, and each material slot with its kind
(`leaf`, `bark` or `skip`), linear colour and mask cover. `crowns.tilt_max_deg` records the
steepest lean (logs and hanging bulbs; 90% of every tree species stands within 15 degrees).

**A sprite** is the species' LOD 0, rasterised from above. Each section's material decides what
its triangles are. Imposters, billboards, lianas, ivy and `WorldGridMaterial` are dropped:
they are flat cards for distance, or hang on vertical faces. Every other triangle adds an
optical depth of `-ln(1 - opacity)` to the texels it covers (at most 3), and cover is
`1 - exp(-sum)`, so three half-clear leaf cards read denser than one. The top is the highest
triangle; the slot is that triangle's material.

**A material's colour** is the mean of its albedo texture (the instance's own parameter, else
its parent's) in linear light, under its mask, times its `Brightness` and `Saturation`
scalars. The mask is the first alpha that varies: the `ORMA` map's, then the albedo's, unless
the albedo parameter says its alpha is subsurface (`Albedo(RGB,SSS)`). Palms, bamboo and the
Snake Legs leaf planes carry no mask at all and draw as solid leaves. Nothing else in the
material graph is read, so a tint the shader applies at run time is not in the colour.

**The soft canopy** keeps its plane and its rule, `1 - exp(-crown area per m^2)`, but each
tree's radius is now its species' measured one (the disc that hides as much ground as the
sprite) times its own scale, snapped to eleven bins from 0.5 to 20 m.

### Drawing

`terrain/crown_stamp.py` stamps the crowns into each band of the render's own grid. Each tree's
sprite is turned by its yaw, scaled, and shifted along its trunk axis by the species' mean
crown height, so a leaning bamboo's crown stands off its base. The sprite is read through a
mip chain (2x2 means; the top channel takes the maximum) at the level whose texel is nearest
the output pixel, bilinearly, so a 1024 preview keeps each crown's area. Trees are laid
lowest top first, each over the ones below. A band returns cover, cover-weighted colour, a
dome height and the highest crown top in world cm; `stamp_crowns(...)["top_cm"]` is the crown
height raster on any render grid.

A pixel is placed on a sprite from its own centre on the sheet, so a crown draws the same
whichever band or window holds it (2026-10-07). Before, the offset was counted in float32
from the band's corner, up to 0.03 cm off along a full-size row, and a band starting
elsewhere moved the crowns: with the band halo widened from 8 to 16 rows (renders.md section
40), 0.48% of the crown values of a 2048 sheet and 0.46% of 12 full-size bands changed, the
cover by up to 3e-5 and the top by up to 0.17 cm.

Placing the pixels from their centres moved the cover of one full-width, full-size band by up
to 0.0024 and the dome by up to 5 cm. The top is the highest one among the crowns whose cover
reaches `COVER_TOP_MIN`, so where a cover crosses 0.25 the top passes to another crown, by
up to 17 m, at 53 pixels of that band, and there the crown flips between drawn and hidden.
Measured on build 502094: at 2048, 328 pixels in 55 of the painted layer's 85 tiles, by up
to 12 levels; in the full-size windows renders.md section 40 describes, 21,219 pixels
(0.02%), by up to 76. No other layer and no light tile moved.

`palette/painted/ground.py` composites the crowns that stand out of the water last, over water and
foam, under the highlight shoulder; a crown under the water's surface is drawn in the bed
instead ("Crowns and the water" below). The `crowns` block of `satellite-painted.json`:

| Key | Value | What |
| --- | --- | --- |
| `draw` | true | Off, the soft canopy is drawn as before |
| `canopy_kept` | 0.0 | How much of the soft canopy stays under the crowns. 0: the crowns replace it, so no tree is drawn twice |
| `darkening`, `chroma` | 0.85, 0.8 | Times the texture colour, and times the style's own chroma gain of 1.2 |
| `dome_gain`, `shade_clamp` | 0.35, [0.55, 1.2] | The light: the style's sky and sun over the shared sun's `sun_dot` on the crown's smoothed height (0.75 m) times 0.35, relative to flat ground and clamped |
| `hidden_below_m` | 0.5 | A crown whose top is more than this below the drawn surface is hidden: a tree under an overhang, or beside a higher rock |
| `waterline_m` | 0.1 | The height over which a crown's top passes from over the water to under it, centred on the water's surface |

### Measured

Extraction adds 28 to 56 s to the paint command (sprites, records and the 1 m top plane),
which took 65 to 108 s in all on a loaded machine; the store grows from 54 to 66 MB. Drawing a z7 crop of the painted layer with crowns took 1.8 s against 0.8 s without (forest,
1.1 Mpx), 2.3 against 1.1 s (Red Bamboo, 1.7 Mpx) and 2.5 against 1.8 s (Titan forest,
2.7 Mpx). Most of the sheet has no tree. A 1024 preview of the painted layer ran end to end.

### Known limits

- **Colours are the textures', moved by the canopy target, the red Kapok's species target
  and, for the blue palms, a target of their own.** Crowns of a target's own hue, and the red
  Kapok `SM_Kapok_03` by name, are calibrated (section 31, "Crowns"); every other crown keeps
  its texture mean: bamboo is a saturated pink-red, the tall mangroves' tops are their bark
  texture. The style's `chroma` of 0.8 is a taste call.
- **Blue palms are blue, on a target of their own.** `BluePalm_01` and `_02` (3,332 trees:
  1,747 in the Rocky Desert, 768 in the Savanna, 344 on the Spire Coast) have one leaf colour,
  the leaf half of `TX_BluePalm_01_Alb`: light blue with white midribs, linear (0.27, 0.40,
  0.46). Their instances `MI_BluePalm_03` and `_04` carry no vector parameter but the wind
  pivot, and the parent `MM_WindPlants` is cooked without its graph, so no tint is skipped
  that could be read. Drawn as that texture mean through the crown style, they came out
  near-white powder blue (#a3c3d3, L 0.80), where the game shows a saturated mid blue from
  above. They now take the named crown target #3d627d (section 31, "Crowns"). Over the Rocky
  Desert windows at 0.92 m/px, palm pixels draw #3a5e78 with lit tops #416783: ΔE 4.7 to the
  Rocky Desert area shot's #2e6695, and 5.5 to the river split's lit tops #3e5579. The wiki's
  Rocky Desert and Spire Coast shots show blue palms in both biomes, so they stay blue
  everywhere.
- A crown is lit by the fixed north-west sun of the painted style. The live sun shading takes
  the crown tops as its occluder (section 29); the crown domes are not yet in its normal
  pyramid.
- Lean moves a crown; it does not foreshorten it.
- `SM_Trunk_01` (6,933 logs and stumps) draws as small bark sprites.

### Crowns and the water (2026-10-06)

Through style version 12 a crown over a wet pixel was drawn at 60% opacity (`over_water`, so
a river under bamboo would read), whether or not the tree stood out of the water. The crowns
were already composited after the water; the fade was the fault. On the Spire Coast's sand
spit at (243, -2078) the crowns over water stand on ground a median 0.2 m under the drawn sea
(quartiles 0.1 to 0.4 m), so the water cover there is 1 and 40% of the sea showed through
every crown: faded and blue, as if drowned. Of the 99,073 trees, 6,490 stand on a wet texel of the field, 6,035 of them at the
ocean's level, and 475 have their top under the water's level.

Now each pixel of a crown is compared with the water's surface there, the drawn ground plus
the water's depth (`palette/painted/trees.py` `crown_layer`):

- **Out of the water**, its top more than half of `waterline_m` over the surface: drawn
  whole over the water, the foam and the shore line, exactly as over dry ground. The rocks
  and the render-only meshes already stand out of the water this way, by raising the drawn
  surface.
- **Under it**: composited into the bed after the coral carpet and before the open-sea term
  and the opaque area water, seen through the water above its own top with the class's
  optics (`palette/painted/optics.py` `underwater`), as the seabed coral is (section 31). Shallow
  under clear water it reads as a dark shape; under the swamp's murk it is gone within a
  few tens of centimetres.
- Between the two, `waterline_m` (0.1 m) cuts a crown where its top crosses the surface, so a
  swamp plant whose upper leaves break the surface shows only those.

**The swamp.** Its trees stand in water up to about 0.6 m deep. Of the 2,650 trees within
250 m of (2343, 291), 2,063 stand on wet texels, their tops a median 0.9 m over the water
and 146 under it, most of those `SM_UppochnerBulb` and `SM_SwampStump`. The tall trees, and
the stumps and bulbs that break the surface, are drawn over the swamp water; those under it
go into the bed, where the swamp's opaque water (tau 0.3 m) hides them. The game's swamp
water is murky, so a plant under it is not seen from above either.

A river under bamboo is now hidden by the crowns, as from above in the game; it shows where
the canopy is open. The Titan trees were already laid over the water and stand tens of
metres out of it; they keep their 0.8 opacity everywhere (section 30).

**The light.** The lighting stage leaves water unlit: its land weight is one minus the water
cover (section 29), and the one pyramid serves every layer, of which only the painted one
draws crowns. So in a render with the light a crown over water keeps the flat light it was
drawn with: no live shading, no shadow from its neighbours. No tree shadow falls on water,
neither on its surface nor on its bed, and a render without the light draws no cast shadow
at all. Lighting the crowns over water needs a crown land weight in the light pyramid that
only the painted layer reads.

**Measured** on six windows at 0.229 m/px, drawn through `render_layer` from inputs cut to
the window, with no rock geometry, open-sea bed, rivers or artwork detail, the same before
and after. Crown pixels are those at least half covered by a crown and by water; Delta E
is OKLab x100.

| Window | Centre (m) | Crown px over water | Mean before, after | Delta E mean (p95) |
| --- | --- | --- | --- | --- |
| Sand spit | (243, -2078) | 36,093 | #718575, #5f7c66 | 5.1 (10.7) |
| Spiral | (-339, -2275) | 101,927 | #6c8573, #5c7d63 | 4.6 (9.3) |
| Spire islets | (269, -1943) | 48,021 | #678176, #5a7966 | 4.7 (10.2) |
| Lake Forest | (235, -437) | 4,110 | #7f8378, #908d84 | 4.6 (7.6) |
| Swamp | (2343, 291) | 75,523 | #697961, #567b54 | 4.8 (11.3) |
| Dry forest | (-1590, 934) | 0 | | 0 |

Every pixel without a crown, and every crown over dry ground, is unchanged to the bit; the
dry forest has no changed pixel. Over the sea the crowns come out darker and more saturated
(OKLab L -0.032 to -0.037, chroma from about 0.042 to 0.064); over the lake, whose water is darker than
its trees, lighter (L +0.040). In the swamp 2,692 crown pixels are under the surface and draw
as its water.

### Coral trees are no crowns (2026-10-06)

From game-painted 6, the first style with crowns (renders-v5 and v6), the small coral trees
read as smooth domes lit from the north-west. Recipe 6 (renders-v4) drew them as what they
are: tiered plates, each a shallow bowl, with a dark line at every step.

**Drawn twice.** `SM_CoralTreeSmall_01` (473), `SM_CoralTreeSmall_02` (570) and
`CraterTree_02` (341) are foliage under `/Foliage/Coral/`. The paint store takes them as trees
(`TREE_MARKS` "/Coral/CoralTree" and "CraterTree"), and the render-only mesh pass takes the
same instances (`RENDER_ONLY_DIRS`, section 27). So each was drawn as its mesh, lit by its
own max-Z top, and then covered by an opaque crown: the platform material has no mask.

**The geometry.** The species' sprite is the LOD 0 top seen from above. `_01` is a plate
whose rim stands at 13.4 to 13.6 m and whose middle at 12.2 to 12.6 m, a 1.2 m dish over a
4 m radius, with the stem's knob above it. `_02` has a top plate with its rim at 16.4 m and
its middle at 15.6 m over lower plates at 11 to 12 m. Each plate is concave.

**Why a dome.** A crown is lit by `sun_dot` of its dome: the cover times the top, blurred by
0.75 m (`DOME_SIGMA_M`), times `dome_gain` 0.35. The blur pulls the sprite's edge down to
zero, from 13 m to 9 m over its last metre, and that edge outweighs the 0.8 to 1.2 m dish
shrunk by 0.35. Every coral tree came out lit as a hump.

**The rule.** A species the render-only mesh pass draws is no crown: `load_crowns` drops its
records (`terrain/crown_stamp.py` `meshed_species`, `is_render_only_foliage` on the species'
mesh). The paint store is unchanged and keeps generator version 3. Its crown-top plane still
holds the coral, so in a render with the light a coral tree still casts a tree shadow. The
coral trees then draw as recipe 6 drew them, in the calibrated colours: the coral mesh colour
#99868e lit by the mesh's own top, the seabed coral under water, and section 31's rule for a
coral speck standing in the sea, which the crown over it had hidden.

**In a render with the light.** The lighting pyramid's surface is the seabed rule's (section
27), whichever layer captures it (section 29, "One capture"). The rule leaves a render-only
mesh standing in the water to the seabed, so the pyramid holds those pixels as water, with
land weight 0, and leaves them unlit. Most coral stands in the sea, so in a lit run it drew
flat. The painted layer, drawn unlit, now keeps the default sun's Lambert term of its own top
on the meshes only it draws (`palette/painted/band.py` `painted_ndl`, with
`lighting/model.py` `surface_direct`, the shader's direct term without shadows). The pyramid
has those pixels as water, so the page leaves them as baked. Coral on dry land is in the
surface and is lit live. The pyramid is unchanged, so the light model stays at version 2.

**Measured** on the render archive's windows with the harness of "Crowns and the water", now
also given the window's render-only meshes from its base sweep. Coral-tree pixels are those
the coral crown covered (cover over 0.9, gone after) on a whole coral mesh pixel. Each cell
is the Pearson r of their luma against the north-west sun term of the true surface, then
against the crown dome's. The archive crops are the stored pyramid levels; v4's colours
differ, which r ignores.

| Window | Zoom | Pixels | v4 | v6 | Now |
| --- | --- | --- | --- | --- | --- |
| Spire islets (269, -1943) | z2 | 6,934 | 0.99, 0.35 | 0.39, 0.89 | 0.99, 0.36 |
| Spiral (-339, -2275) | z1 | 3,412 | 0.69, 0.27 | 0.54, 0.87 | 0.99, 0.48 |
| Specks lagoon (16, -2137) | z1 | 3,683 | 0.70, 0.24 | 0.50, 0.78 | 0.97, 0.44 |
| Carpet (-440, -2380) | z0 | 1,348 | 0.39, 0.05 | 0.68, 0.78 | 0.99, 0.57 |

In a full run's light, at the default sun, the coral standing in the sea followed its own
surface at r -0.08 to 0.16 before and at 0.70 to 0.95 now (375 to 6,228 pixels a window).
The caps' median is 1.3 to 1.8 Delta E from the #99868e target, against 6.8 to 7.1 as
crowns. With the coral crowns kept, the change matches master to the bit drawn without the
light and drawn with the painted layer capturing; drawn unlit after another layer it changes
mesh pixels only. Leaving the coral crowns out changes pixels under them only, and at most
13 a window at their faint edges. The carpet and every other crown are unchanged.

**Known limits.**

- In a lit run the coral standing in the sea keeps the noon light whatever sun the page
  picks, and takes no cast shadow.

**The paint store keeps the coral (measured 2026-10-07).** The 1,384 coral trees, of 99,073,
still write the crown-top and canopy planes. Without them the crown top loses 63,997 texels
and the canopy 289,406 (111,343 by a tenth of cover or more). Neither is a second drawing:

- The canopy plane is weighted by `canopy_kept`, 0.0 while game-painted draws crowns, so
  leaving the coral out of it changed no unlit pixel at 2048.
- The crown top is the light's crown occluder, which casts the coral's tree shadow. Leaving
  the coral out took that shadow away: 32 of the light pyramid's 85 horizon tiles changed,
  and 571 pixels of the lit painted pyramid at 2048, by at most 21 levels. A coral tree
  standing in the sea casts no other shadow.

So the paint store stays at generator version 3.

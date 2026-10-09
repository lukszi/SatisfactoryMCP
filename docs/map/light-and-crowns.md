# Live sun light and tree crowns

Sections 29 and 36 of the [design spec](../../DESIGN.md): the lighting pyramid the page relights, and
the tree crowns drawn on the painted layer and cast into it. A section number below
resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 29. Live sun light (2026-10-05)

A render drawn with the light stores its colour without light and adds a lighting pyramid,
and the page relights it in the browser for any sun. One light, the sun; the page picks where
it stands.

Every render bakes the light unless told not to: `--light` is the default of
`python -m mapgen renders` and `--no-light` turns it off (`--unlit`, the old opt-in, still
means `--light`); in the Maps tab the "live sun" box starts ticked and the `render` preset's
`light` option defaults to true. Every render mode takes it: each layer, each size,
`--kernel-only`, and `--restyle`, which installs the light an earlier run kept when it draws
the same surface (below, "Kept light").

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
  wider one). At 2° the edge is a step at every pixel, and at 16:00 whole valleys turn into
  flat polygons with stair-stepped edges.
- **Fill** (`SHADOW_FILL`, 0.35). A cast shadow keeps 35% of the sun's Lambert term: the sky
  around the sun and the light bounced off sunlit ground, both from the sun's side. Without
  it shadowed ground is the ambient term alone, the same for every slope, and a valley in
  shadow reads as one flat grey shape at the floor. 0.2 and 0.35 were compared at 16:00 on
  the cliff-lakes and abyss crops; 0.35 keeps the rock's shape inside the shadow.
- **Tree crowns and the Titan trees** cast into horizons of their own, each alone (below, "The
  trees in the light"). Only a style that draws the trees reads them
  (`shader_light(layer)["crowns"]`, the painted style today); every other style is shaded by
  the ground alone. Shared by every style, the crowns' shadows drew near-black blocks in the
  forests and dashes in the desert on layers that draw no trees, five times as frequent
  beside a crown as away from one.
- **Arches, rock overhangs and crowns are spans** (below, "Arches as spans"): each blocks only
  between its underside and its top, so its shadow falls where the sun's ray meets it and
  light passes beneath.
- **Sky view** within 10 m darkens the foot of a cliff and the floor of a gully. Larger radii
  grey whole valleys.
- **Ambient occlusion** darkens the sky view where a rock, a cliff or an arch meets the ground,
  at full resolution, and the trees' own a cell of the atlas (below, "Ambient occlusion").
- **Normalisation** by `sin(max(el, 35°))`: without the clamp a fifth to a third of the
  pixels blow out at 20°.
- **Shadow floor.** The light passes through a soft maximum with 0.36 at a knee of 0.1
  (`SHADOW_FLOOR`, `SHADOW_FLOOR_KNEE`), so the darkest light lands at 0.36 to 0.40 instead of
  the 0.2 the bare model reaches in a shadowed gully.
- **Water and the void** stay unlit: the land weight, the ground's share of each drawn pixel
  (below, "The land weight"), blends the light out, from the tree crowns over the water too
  (section 36, "Crowns and the water").
- **Per style.** The painted style lights in linear light with its own ambient, sky and sun
  colours; the shader undoes and reapplies the luminance tone curve of section 31
  (`tone_knee`, `tone_white`). Terrain and the relief multiply into sRGB,
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

### Arches as spans (2026-10-07)

An arch, a rock overhang and a tree crown stand over what is beneath them. Until light model 3
the march stood each of them on the ground as a column, so an arch cast a wall from its foot to
the end of its shadow, a crown a straight streak from its trunk, and an overhang a wedge. Now
each is a span with an underside and a top, and the sun's ray decides (`lighting/spans/march.py`).

**The march.** Along each of the 32 directions the ground blocks as before, but the ground is
the *solid* surface: the drawn one without what floats. A span sample blocks only the tangents
between its underside and its top as the receiver sees them:

- Where that reaches down to what already blocks, the sample merges into the horizon with the
  drawn top's exact tangent: an arch's foot, a rock's base, and a receiver on the span itself,
  which sees its own top as a plain march does.
- Otherwise it is a band above the horizon, and light passes beneath. One band is kept per
  direction: a sample that overlaps it, or leaves a gap narrower than the sun disc (6°,
  `BAND_GAP_DEG`), widens it; one apart from it replaces it when it is nearer the elevation the
  sun's path has in that direction (`path_elevation`; 45° where the path never comes). So two
  arches in line keep the sky between them. A band joins the horizon only where it reaches
  down to it; above it, however narrow the sky beneath, the shade reads the share of the sun
  disc each hides. Joining any band within a sun disc of the horizon flipped neighbouring
  receivers between lit and shaded.
- A sample stands for the stretch of ray from halfway back to the step before to halfway on to
  the next, and reads, of the four pixels around it, the span whose top is highest, reaching
  down over those it overlaps. A thin arch crossed between two samples still blocks, so its
  shadow is a line, not dots; one object's pixels are read whole; and two spans one above the
  other are never read as one: the lowest underside and the highest top of the four made a
  low slab beside a high deck one solid span, which drew a lattice of wall shadows on the
  maze and spire sites.
- Past 40 m a band's tangents fade by the fade weight, as the ground's do, so a far span
  shades alike whether it floats or reaches the horizon. Narrowing a band about an exact
  centre while a merged span faded drew dashed arcs in a far spire's shadow.
- **Sky view**: a band costs `sin(hi) − sin(lo)` of a direction's sky, not the whole wall
  under it. Beside an arch the median sky view rose from 0.55–0.67 to 0.78–0.83 on the
  prototype's three arch sites.
- **Normals** of the ground beside or beneath a span, a pixel below a neighbouring underside,
  come from the solid surface, so a span's edge draws no rim on it. The span's own pixels, and
  a rock beside it at its height, keep the drawn surface's.
- Where no span is in reach, the march and the sky view are the plain ones to the bit, and a
  strip of rows with none skips the span work. Both run as numba kernels equal to their numpy
  reference bit for bit (`spans/kernels.py`, section 41 of renders.md). `--gpu` runs both, and
  the rules after the march, on CUDA to the same bits (`spans/gpu.cu`, `spans/device.py`;
  renders.md section 41, "On the GPU").

**What casts as a span.**

- **Arches.** The top raster draws the arches apart (`terrain/top_raster.py`): their top
  (max-Z); their underside, the highest arch surface 25 cm or more below the top
  (`SAME_SURFACE_CM`), else the lowest (the same triangles rasterised upside down), so under
  a deck that crosses another it is the deck's own; and the boulders alone.
  Every pixel an arch covers is a span from its underside to the drawn top; the solid surface
  there is drawn with the boulders only.
- **Rock overhangs** (`terrain/overhangs.py`). Under each rock's top the direct pass finds the
  highest downward face that rises 1 m over its placement's lowest point (`UNDERSIDE_RISE_CM`,
  so a rock's own foot is no underside), and the highest upward face under the top, the floor.
  A rock floats where its underside clears both the floor and the ground by 2 m
  (`OVERHANG_CLEAR_M`): the cap of a mushroom rock, a ledge leaning out. A rock resting on
  another or sunk into it finds the other's top as its floor, and a mesh with an open bottom,
  or whose winding is unknown, has no underside: both stay columns. The solid surface sets a
  floating rock down on its floor, or the ground.
- **Crowns.** Each crown pixel is a span from its species' underside, a share of its top's
  height over the drawn surface, to its top, marched alone into the crown cells under the
  crowns' own fade (below, "The trees in the light"). A crown over open ground throws its own
  shape, offset by the sun, instead of a streak.
- **The Titan trees** are a slab `TITAN_SLAB_M` (12 m) deep under their canopy's top, marched
  alone into cells of their own under the ground's fade.

**The arches' holes.** Their meshes have open edges that left slits and specks in the raster,
drawn as dark lines through the deck. The top raster fills a hole the arch encloses whose
widest point is within 0.5 m of it (`FILL_HALF_WIDTH_M`), and a pixel the arch covers from
opposite sides within 0.46 m along a row, a column or a diagonal (`FILL_RADIUS_M`), three times
over (`terrain/archfill.py`); a filled pixel takes the mean top and underside of the covered
pixels around it. Holes wider than a metre, the ground framed by the arches, stay open. The fill
reads 7.3 m past a band's edges (`FILL_HALO_M`) on the band's own grid, so a slit the band edge
cuts closes, and where nothing is filled the top raster is the one it was. On a sheet coarser
than 0.46 m to the pixel nothing is filled.

**The default sun** is baked from the bands themselves: per cell, the horizon's soft edge plus
the share of the sun disc the bands above it hide (with the trees, the union of two bands:
the ground's, and the crowns' and Titan trees' marched together over the ground,
`span_bake.canopy_cells`), weighted between the sun's two directions as the horizon is, and
zoomed to the pixels as the horizon is (`span_bake.default_shade`). Only
cells a span was in reach of, grown by one, take it; every other pixel's term is the horizon's
as before. Zoomed horizons would draw one-pixel bright rings where a cell holding an arch as
horizon meets one holding it as a band.

**The atlas.** The page reads horizons, no bands yet. So a cell folds its band in at the
elevation the sun's path has in that direction, `el + 6° × (cover − ½)` where the band hides
some of the disc there (`span_bake.path_horizon`). For a sun on the game's path, the time-of-day
slider, the page shades a span where the bake does; a sun off the path keeps the path's
shadow there. The model block's `spans` records the rule and the 32 path elevations. Live bands
on the page (their underside and top per direction in a texture of their own, read per texel
by the shader) are a later step; nothing here needs to change for it.

A folded band sits inside the shader's 6° soft edge, which is about 10 steps of the atlas's
byte at the path's elevations, so the tile that holds one is stored at a higher quality
(below, "Horizon tiles at q95 where a band folds"), and a coarser level averages the shade it
reads, not the degrees (below, "Coarser levels").

**FXAA on the arches** (`render/draw/archaa.py`). Their silhouettes stair-step at the pixel. FXAA
3.11 at its quality preset (an end-of-edge search 12 px along, `FXAA_REACH`) runs on each band
of every layer's unlit and lit copy that an arch is near, and its answer is kept only inside the
arches' coverage grown 3 px (`MASK_DILATE_PX`): rocks, crowns, water and the ground keep their
bytes. The luma is summed in one fixed order (section 41 of renders.md). A band near an arch
waits for the next band's first 14 rows (`FXAA_HALO`), so it is filtered as the whole sheet
would be (`render/draw/stream.py`).

**On disk.** `top.cache` holds the boulders alone, the arches' underside and their coverage
beside the top; `direct.cache` holds the overhangs' underside and floor, NaN where none. Both
carry `planes: 2` in their stamp, so a cache from before is rebuilt, never read without them.
The light's scratch holds the captured spans in `slabs/`, one file per 256 px tile that has
any; they are digested with the surface they came with (below, "Kept light"). A block reads
them at half resolution, each cell the highest of its four pixels' spans, whole.

**Cost** (2026-10-07, shared mode, other jobs running; base is the code before spans):

| What | Base | Spans |
| --- | --- | --- |
| Light block, 4096 px, terrain only | 26.7 s | 25.9 s |
| Light block, 4096 px, crowns over 40% | 39–59 s, 1.03 GB | 105–120 s, 1.44 GB |
| Light block, 4096 px, crowns and 3% arches | | 107–127 s, 1.50 GB |
| Nine real 1024 px blocks (arch and forest sites), summed | 33 s | 111 s (2.4–5.7× each) |
| Direct raster, 16384, back to back | 308 s | 739 s |
| Top raster, 16384, back to back | 44 s | 66 s, before the underside's own pass |

The span march is most of it: a scalar loop over the pixels a span covers, against the plain
march's vectorised rows, and it visits only the columns holding a span
(`spans.SpanRuns`). Blocks of open terrain cost what they did. The full-size light, 830 s on
16 workers before, so lands at about two to three times that, by the share of the map under
crowns and arches; the direct raster, built once per cache, at about 2.4 times its 634 s.

**Limits.**

- One band per direction: a third arch in line, apart from the kept band by more than a sun
  disc, casts no shadow in that direction.
- One span per pixel: where two decks cross, the part of the lower one under the upper casts
  nothing; where a Titan tree's canopy stands over a crown, the crown casts nothing.
- Where several spans compete for a direction's one band, neighbouring cells can keep
  different ones: a faint speckle in the shadow, seen under the maze site's stacked arches.
- One underside a species: the share of its own top its leaves start at, whatever the tree's
  scale and the slope it stands on.
- An overhang over a ledge sets the ledge's top down as the floor only where it is the highest
  face under the overhang; an overhang over another overhang keeps the lower one a column.
- The page's horizons are exact for a sun on the game's path only, until the bands reach it.
- Holes in the arches wider than a metre stay open.

### What is written

| Where | What |
| --- | --- |
| `<renders>/light/tiles/{z}/{x}_{y}.nrm.webp` | Lossless RGBA: east and south normal as `(v + 1) / 2`, sky view, land weight. An opaque tile drops the alpha channel, which a reader takes as land. At the native level the normal carries the ground's detail where the run drew one (painted.md section 30, "The layers' own textures") |
| `<renders>/light/tiles/{z}/{x}_{y}.hz.webp` | A grey atlas of 97 cells of 128 px, 8 a row in 13 rows, at half resolution, each in a 16 px border of its own edge texels (`hz_gutter`, so 1280 × 2080 px): cells 0–31 the ground's horizons, cells 32–63 the crowns' alone and 64–95 the Titan trees' alone, always stored, `255 · sqrt(deg / 90)`, a span's band folded in at the sun path's elevation (above, "Arches as spans"); cell 96 the trees' ambient occlusion, `255 · o` (below, "The trees in the light"). WebP q95 where a band is folded into the tile or, on a coarser level, into any tile beneath it; WebP q90 elsewhere (below, "Horizon tiles at q95 where a band folds") |
| `<renders>/light/meta.json` | The light axis (model constants and their digest), `occluder_layers` (the layers that read the crown cells; empty without crowns), the `key` the bake was made under (below, "Kept light"), tile counts, timings |
| `<layer>/unlit/` | The unlit colour, 1x only, lossless WebP (PNG before 2026-10-08; the server takes the suffix from the sidecar's `layout`). On the painted layer, the ground without its trees (section 36, "Trees apart") |
| `<layer>/trees/` | Painted only: the crowns and the Titan trees as one layer, lossless WebP RGBA with straight alpha, 1x, sparse: a tile with no tree in it is not written (section 36, "Trees apart") |
| `<layer>/tiles/`, `tiles@2x/` | The colour lit by the default sun, trees and all: what a page without WebGL, and every older reader, draws |

Coarser levels are computed from the coarser surface, not by averaging encoded tiles: normals
from the downsampled heights, sky view by mean, and horizons by the mean of the shade the
page reads (below, "Coarser levels"). The tile format, the work files and the coarser levels
are `lighting/light_tiles.py`'s.

### The land weight (2026-10-07)

The land weight is the share of each drawn pixel that is the ground, and so the share the light
lights: `(1 - water cover) * (1 - void cover) * (1 - void rim)`. The void's cover and rim are
the ones every layer draws (`render/ground/void.py`), worked out once a piece for the surface the light
captures. The void is not water: its cover is not added to the water's, and the water's own
drawing does not change.

Before, the land weight was one minus the water cover, and 0 only where none of a pixel's
bilinear taps has data. The void's edge is softened over 2 m and its rim lies over the field's
last texels, but the light took those pixels as land; and where the data stops the drawn heights
drop to 0 m, so a pixel beside that edge was lit at a near-vertical normal. Every void edge and
every coast past the world's edge drew, lit, a staircase of the field's 1 m texels with a light
or dark rim, and a lone texel with data inside the void a dark square; unlit, both were a smooth
curve. Now the light fades out with the ground under the void's soft edge, so the lit edge is
the unlit one. The heights the light marches are unchanged: the 0 m plane under no data still
casts (Open).

**Measured** (2026-10-07, build 502094). On 27 full-size windows, 14 of them at void edges, the
unlit sheets are the same bytes and the land weight moves only within a few metres of the void.
At 2048, all five layers, with the lattice's softened edge of section 25 beside it: the lit
tiles of each layer move in 31 of 85 tiles, 34,148 to 36,901 pixels, at most 73 levels in
the painted layer and 121 to 124 in the others; the light's normal tiles move in 31 of 85,
25,223 pixels, and its lossy horizon atlases in 25.

### The stage

The one pass that draws every layer (section 40) hands its heights and land weight to a
`Surface` (two memory maps in the light's scratch, 5.4 GB at 32768), each band once, in
order, whatever layers the pass draws: the surface the seabed rule draws (below, "One
capture"). The surface is baked in blocks of 16 × 16 native tiles, each with a 150 m halo,
and a process pool computes per block (`lighting/stage.py` `bake_block`): the ground's
horizons and the crowns' at half resolution, sky view, normals, the native tiles, and the
light at the default sun for the baked copy, once for the ground alone and once with the
crowns and their canopy's own light (below, "The canopy's own light"). A block
whose core is all water skips the horizon march. A row of blocks is queued as soon as the
surface holds every row it reads, while the pass is still drawing (`lighting/bake.py`,
section 42). Each band of a layer goes to `unlit/` at once and, once the terms of its rows
are in, is lit by the default sun with its own term (`render/draw/light.py` `relight_rows`: the
crowns' only for a style that draws them) and goes to `tiles/` and `tiles@2x/`. The three
trees are renamed into place in that order (section 17, "Cutting in parallel").

**One capture (2026-10-07).** The light does not depend on which layers a run draws, or in
which order. It takes the surface the seabed rule draws: a band composes the seabed's heights
and water for the light beside the painted layer's own, whether a layer that draws the seabed
is in the pass or not (`render/ground/surface.py` `band_surfaces`), and the painted layer drawn unlit
always keeps the default sun on the meshes only it draws (section 36, "Coral trees are no
crowns"). The crown tops come from the paint store whenever there is one: the painted
ground's plane when that layer is drawn, else the store's (`render/draw/light.py` `crown_tops`). A
run without a paint store still bakes no crown cells. Before this date the surface was the
first layer's as that layer drew it, and the crowns came with the painted layer only: `--layer
painted --layer terrain` relit terrain around the sea meshes it draws as water, and `--layer
terrain` alone baked no crown horizons. Measured at 2048, both runs now match a full run of
every layer to the bit, light and tiles, and a full run, which draws terrain first, is
unchanged.

**Horizon cost.** The march takes bilinear samples up to 16 px and the nearest pixel beyond,
with in-place arithmetic: 0.14 µs per half-resolution pixel and direction, 6.3 times faster
than the all-bilinear reference, which it matches to a mean 0.10 to 0.17° and a 0.1 to 0.3%
difference in which pixels are shadowed at 25°. With the 6° edge on two full-size windows,
all-bilinear changes the horizon by a mean 0.15° (p99 2.4°), less than the q75 encoding
does, and the shade by more than 0.1 on 0.4% of the pixels; the nearest pixel stays. A rotated
row sweep measured 1.7 times slower than the reference: rotating the block grows it, and every
pixel of the rotated square is computed.

**Strips, memory and workers (2026-10-06).** The bake is shaped so that no step waits and no
worker holds more than it needs; none of it moves a byte:

- **Strips.** The march and the sky view finish 64 rows (`horizon.STRIP_ROWS`) through every
  step before the next 64, so a step's arrays stay in cache. The arithmetic per pixel and its
  order are those of a pass over the whole block.
- **A direction at a time.** A block encodes each horizon cell as it is marched
  (`stage._bake_horizons`): the atlas byte, the coarser levels' 2 × 2 refold (a ground cell
  with its crown cell, which comes next), and the four float cells the default sun reads
  (`model.sun_cells`), so the 64 cells are never stacked as 1 GB of floats. The refold of one
  direction is the stacked one's to the bit, which a test holds.
- **Workers of its own.** `--light-workers` sets the bake's pool. By default it is one a
  core, at most 16 (`LIGHT_WORKER_CAP`), and no more than the free memory holds at 2.0 GB
  each (`LIGHT_WORKER_BYTES`, below), counted when the bake starts. On Windows the free
  memory is the lesser of the free RAM and the commit still available
  (`pools.free_ram_bytes`): a process that cannot commit fails with RAM to spare, which
  other processes' idle pools can bring about. `--workers N` sets it where `--light-workers`
  is not given, and sizes the cutter's pool the same way.
- **A fed pool for the coarser levels.** The parent submits a strip's tiles, 4 to a task
  (`LEVEL_TASK_TILES`), and computes the next strips while they encode, up to `LEVEL_AHEAD`
  (4) strips ahead, so the pool is not left idle between strips. The horizon bytes come from a
  256-entry table of `encode_horizon(decode_linear(q))`, exact because both are elementwise.
- **One BLAS thread a worker.** numpy and scipy each load an OpenBLAS, and on a 32-thread
  machine each commits about 0.8 GB of thread buffers as it loads, about 25 GB over 16
  workers, for a bake that makes no BLAS call. The pool starts its workers with
  `OPENBLAS_NUM_THREADS=1` (`pools.one_blas_thread`, set while the pool lives and put back
  after; the parent keeps the BLAS it loaded with), and a worker that imports both commits
  0.04 GB. A full-size block peaks at 1.08 GB working set and 0.98 GB commit, one under arches
  at 1.36 and 1.04 GB. With the crowns and arches as spans (2026-10-07) a block under crowns
  peaks at 1.44 GB commit and one with crowns and arches at 1.50 GB; `LIGHT_WORKER_BYTES` is
  2.0 GB, the larger with room. The default sun's terms are made 512 rows at a time
  (`stage.TERM_ROWS`), to the same bytes, so the canopy's own light adds nothing measurable:
  before the spans, with crowns over half of it a block peaked at 1.13 and 1.09 GB, against
  1.14 and 1.02 before the canopy, and at 1.30 and 1.26 GB with a hole of no data across it
  (2026-10-07). The cutter's encoders import no numpy, so their pool needs no such setting
  (section 17).

Measured on synthetic terrain with crowns at the full-size spacing, 4096 px blocks, on the
16-core reference machine while other jobs ran: one block takes 73 s, 32 s of it the ground's
and the crowns' horizons, 2.9 s the sky view and 37 s WebP encoding; a wave of 8 blocks takes
117 s and of 16 184 s; the coarser levels from 8192 px take 51 s on 8 workers and 35 s on 16.
So the light at 32768 is about 1,060 s on 8 workers and 830 s on 16, assuming every block
marches.

**Normal tiles at WebP method 2 (2026-10-06).** The lossless `.nrm.webp` tiles are written
at effort 2 (`light_tiles.NRM_METHOD`) instead of 4; the lossy horizon atlases keep 4.
Lossless at any effort, so the pixels are the same and only the file differs: on 200 z7 normal tiles,
13.7 ms a tile instead of 35.0 (2.6×) for 5.7% more bytes. The normal tiles are about a third
of the light pyramid's bytes, which grows about 2%. The owner chose the trade on 2026-10-06.

**Step growth** (`STEP_GROWTH`, 1%). Past `FINE_M` (44 m) the march steps grow with the
distance. A plateau is sampled at the first step past its edge, so the horizon jumps from
step to step and a soft edge shows the jumps as bands, as wide as the gap. At 3% they read
as terraces in every penumbra at full size; at 1% they are 0.4 to 1.5 m and do not. 219
steps instead of 138, measured at 19% more for the ground's 32 directions on a full-size
window, because the bilinear steps near the receiver dominate. At 2048 every step is still
one pixel.

**Encoding.** q75 decodes within a mean 1.05° of the exact horizon (p99 6.4°), q90 within
0.48° (p99 2.9°) at 1.44 times the bytes. With the 6° edge the two relight the abyss crop at
16:00 the same to the eye, so q75 stayed, until the folded bands of the spans: a band sits
inside the soft edge, where those errors are the whole shade. Since 2026-10-08 a tile with a
folded band is q95 and every other one q90, each cell in a border of its own edge (below,
"Horizon tiles and coarser levels").

**Bytes.** The steepest 2 km box writes 78 KB of lighting per z7 tile, so a full map is at
most about 1.3 GB at z7 and less in practice, because open water compresses to almost
nothing. The crown cells are 0 wherever no crown stands above the ground's horizon; at 2048
they take the light pyramid from 18.8 to 24.2 MB. The unlit colour adds about half the
colour pyramid again. A full-size light was 2.38 GB under the spans with q75 horizons, and
is 4.06 GB with its folded tiles at q95 (below, "Horizon tiles and coarser levels").

### Scratch

While the run lasts, `light.cache/` holds 16.6 bytes a pixel, 17.8 GB at full
size: the surface (heights 4, land 1), the default-sun terms (4), and the bake's
half-resolution heights, land and sky view (1.5) and quarter-resolution horizons (6.1, the
atlas's 97 cells). With a paint store the crown tops, cover and underside add 6, 6.4 GB,
whatever layers the run draws. Until light version 5 the horizons took 4 bytes and the crowns 5.
With the layers' textures, at 16384 px and up, the ground's detail normal adds 2, 2.1 GB at full
size. The Maps tab's estimate counts them as `presets.LIGHT_SCRATCH_BYTES`,
`CROWN_SCRATCH_BYTES` and `DETAIL_SCRATCH_BYTES`, scaled by area; tests hold each to what the
stage allocates.

It is scratch for one run, not a cache: no flag keeps it, and nothing reads it after the run.
What a later run can use, the finished pyramid and the default-sun terms, is filed apart from
it (below, "Kept light"). So the question of storing it compressed (section 39) is one of
size and traffic while the run lasts, not of reuse.

| Planes | Written | Read |
| --- | --- | --- |
| `z`, `land` | By the draw pass, a band of rows at a time, each band hashed as it is stored | By the bake, one block of 16 × 16 tiles at a time with its halo (`land` without one); `land` again for each lit band, a band at a time |
| `occluder`, `occluder_cover` | By `crown_occluder`, 256 rows at a time, then hashed once | By the bake, a block with its halo |
| `slabs/` | With the surface, a band at a time: only the 256 px tiles that hold an arch or an overhang, hashed with it | By the bake, the tiles in a block's window, at half resolution |
| `terms` | By the bake's processes, a block each, then moved to the kept light | For each lit band, a band at a time, once its row of blocks is baked |
| `zh`, `landh`, `svfh`, `hzq` | By the bake's processes, a block each | By each coarser level, a strip of rows at a time; each level replaces them with its own |

`crown_occluder` writes the occluder files themselves (`stage.occluder_planes`), and the bake
reads them where they are; a test bakes from them and from a copy to the same bytes.

- **Where.** `--scratch-dir`, else `--cache-dir`, else beside the renders. The Maps tab does
  not pass it.
- **Traffic.** At 32768 the scratch takes about 22 GB of writes. Reads come to 18 GB for the
  bake's blocks (the halo makes a window 1.75 times its block's area), 8 GB for the coarser
  levels and 4.3 GB for each layer's default-sun copy, 47 GB in all for five layers once the
  page cache no longer holds them. At the 115 to 147 MB/s measured on the hard disk of section
  39 that is up to 8 to 10 minutes of transfer a run, on the disk that also writes the tiles;
  an SSD does it in under a minute. These are counts of what each step touches, not a
  measurement of a full run.
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
- **The start of a run.** `render/draw/light.py` `claim_scratch` empties a scratch a run left when
  it was killed, before the field is read. A render that has started drawing keeps its
  `z.npy` mapped until it ends, and Windows refuses to rename a mapped file, so a scratch in
  use is refused with exit code 11 and the reason on stdout (section 20, "Refusals").
  Elsewhere that check finds nothing. Two runs started while both are still preparing can
  still meet: the second fails when it creates its first plane.
- **The end of a run.** `render/draw/light.py` `light_run` closes the stage however the draw loop
  ends: finished, returned early, failed or interrupted, a crown occluder that fails
  included. A run killed outright, as a job cancelled from the Maps tab is, leaves the scratch
  to the next lit run's start or, under the cache folder, to the tab's cache clear. So can a
  failure whose traceback still holds a plane mapped.

### Kept light (2026-10-07)

A palette-only restyle draws the surface the render before it drew, so its bake would write
the same pyramid. The finished bake is kept beside the raster caches, and a run that draws
the same surface installs it instead of baking (`render/draw/kept_light.py`).

**The key.** After the draw pass the run digests what the bake reads (`stage.light_key`):

- the surface: each band's heights and land weight, and its spans' tiles, hashed as
  `Surface.put` stores them on the thread that drew the band and folded in row order, so
  neither the thread count nor the order the bands finish in matters;
- the crown tops and cover, hashed once `crown_occluder` has written them;
- the size, the light model's digest (`light_axis`, the default sun included), the layers
  that read the crown cells, and `stage.LIGHT_VERSION`.

The key and its digest go into `light/meta.json` as `key`; two bakes under one digest write
the same tiles and terms. `LIGHT_VERSION` stands for the bake's own code. A test pins the
bytes one small bake writes (its terms, sky view, land and horizon planes and its lossless
normal tiles), so a change that moves them fails until the version is bumped and the new
digest pinned.

**What is kept.** `light.kept/` sits in the raster caches' folder: `--cache-dir`, else beside
the renders.

| File | What |
| --- | --- |
| `tiles/` | The pyramid's tiles: a hard link to each installed one, or a copy where the cache and the renders are on different volumes |
| `terms.npy` | The default-sun terms, 4 bytes a pixel (4.3 GB at 32768), moved out of the scratch: a rename on one volume |
| `surface.json` | Where each band of the surface went and its digest, which a later run reads while it draws (section 42) |
| `meta.json` | The bake's `meta.json`, key included. It is removed first and written last, so a keep cut short is never read |

It goes with the raster caches: a run that keeps none (no `--keep-direct`, `--restyle` or
`--kernel-only`) deletes it at its end, and the Maps tab's cache clear takes it with the rest
of `_cache/<size>/`. A keep that fails, in a read-only folder say, leaves no `meta.json`, and
the run relights from its own scratch.

**Reuse.** When the kept `meta.json` carries the run's digest and its terms and tiles are
there, the run links the tiles into `<renders>/light/` through the usual staging and swap,
writes the kept `meta.json` there, and relights each layer from the kept terms. A run into
the folder that already holds that bake leaves it untouched. Anything else bakes and replaces
the kept light. A palette change keeps the key. A change to the drawn heights or to the water
or void cover the land weight comes from (a new field, build, raster cache or seabed rule)
changes the surface, so the light is baked again. The digest is known only once the draw is done,
so while it draws a run reads the kept terms as long as its bands match the kept bake's
(section 42, "A kept light, read while it matches").

**Cost.** The hashing reads what the run holds in memory already: each band on its draw
thread (SHA-256 runs at about 2.8 GB/s a core; the surface is 5.4 GB at 32768) and the crowns
once as they are written (5.4 GB, about 2 s). Linking a full-size pyramid's 43,690 files took
6.5 s on the hard disk and 9.4 s on the SSD of the reference machine, once to keep it and once
to install it, against about 1,140 s for the bake on the CPU (2026-10-08), 314 s of it after
the draw. The Maps tab budgets a restyle whose cache holds `light.kept/meta.json` at
`LIGHT_KEPT_S`, 10 s at full size, in place of those 314 s (`LIGHT_STAGE_S`). Its
cache size counts the linked tiles in full, though they share their blocks with the render
they came from.

**Measured** at 2048, all five layers, against the pixel batch's baseline (2026-10-07). A full
run writes the same 1,125 tiles to the byte, the light's 170 among them, and the same
sidecars but for the light's new `key`. A full run that kept its caches, then a restyle from
them into a new folder, as the Maps tab runs one: the restyle installed the kept light, baked
nothing, and wrote the same 1,125 tiles to the byte. A plain full run and that pair read the
same surface digest, drawn on 4 threads and on 8. Timed alone on the reference machine, the
same pair before the change and after it: the full run took 482.5 s and 477.0 s, so the
hashing and the keep cost nothing measurable; the restyle 208.2 s, of which 12.9 s was the
bake on 2 workers, and 191.7 s. Every tile and sidecar of the two restyles is the same but
the light's `key`. At full size the bake a restyle skips is about 1,140 s on the CPU and
770 s with `--gpu`, most of it beside the draw (renders.md section 41, "The whole render,
timed").

### Edges of the light (2026-10-07)

**No data.** The capture stores NaN where the draw has no height at all, the void the
painters draw there (`render/ground/surface.py` `_light_planes`; the land weight there was 0
already). The bake takes it as open (`lighting/spans/holes.py`):

- a half-resolution pixel averages only its pixels that have a height;
- for the march and the sky view a hole stands at `OPEN_M`, 10 km down, so nothing in it
  blocks the sun or the sky;
- a pixel of a hole takes the horizons, the spans' bands, the sky view and the height of the
  nearest pixel that has one, in its block and the ring past it, so the planes the page and
  the default sun interpolate hold no ramp toward a hole; the spans' receivers and solid
  surface open the same way (`stage._opened_spans`);
- a block opens the holes anywhere in the window it marches, its halo included
  (`stage._no_data`, 2026-10-08). It opened them only when its own core held one, so a hole
  just past a block's edge stayed NaN, a march that crossed it came out NaN, and the atlas's
  byte cast stored that as 0, open sky: lit staircase wedges along the 4096 px block grid of
  a full-size light, seen with an east sun at (-434, 1110 to 1380) and (1440, -3490). A NaN
  horizon is now refused (`horizon.encode_horizon`), never stored.

Before, a hole was captured as a plane at 0 m, the height the sampler gives a pixel with
nothing under it. Wherever the ground beside a hole lies lower, that plane stood as a wall
and shaded the rim of the pits, the chasm and the southern and eastern coasts (sweep class
7, auto #2). A test holds that a flat floor at -50 m beside a hole bakes the bytes of the
same floor with no hole, where the 0 m plane shaded it.

**Normals beside a hole.** A pixel beside a hole takes the slope toward its side that has
a height, and a hole stands flat (`horizon.normals`). Before, the slope ran down to the
0 m plane and drew a lit or dark line along every rim.

**Block seams.** Each block marches and takes the sky view one half-resolution pixel past
its edge, and brings the sky view and the default sun's horizon to full resolution from
those, at pixel centres (`light_tiles.upsampled`); the per-cell shade beside a span (above,
"Arches as spans") comes up the same way. Before, the sky view was clamped at a block's
edge and the default sun's horizon stretched corner to corner across the block (`zoom`
without grid mode), so the light stepped along the 4096 px grid of a full-size render
(auto #8). A bake in blocks now writes the bytes of a bake in one block, which a test
holds; on the test's ridges the old crowned direct term differed by up to 86 levels between
the two.

**Not the light's: landscape holes.** The lit crease lines at landscape holes (auto #6,
three lines of about 200 m) are steps in the drawn surface, which the normals draw
faithfully. The ground lattice (`terrain/fill.py` `terrain_lattice`) takes the landscape's
own heights where the landscape has a sample and the decimetre field in its holes, and the
two disagree: at (-1144.3, 2409.7) the landscape stands at -50.281 m and the field beside
it at -50.1 m, and 6.6 cm of that survives the interpolation as a one-pixel step. The other
two lines are seams between provenance codes 4 and 5. They want the lattice blended across
the hole's edge.

### The canopy's own light (2026-10-07)

The painted layer lays the tree crowns and the Titan trees over the ground, and its baked
light was the ground's: a crown took the Lambert term, sky view and horizon of the ground
beneath it, so a ravine under the Titan forest showed through the canopy (sweep class 6,
auto #5). The crown occluder's canopy now has a light of its own, baked at the default sun
into the painted layer's terms (`lighting/spans/canopy.py` `block_canopy` and `canopy_rows`):

- **Where:** the occluder's covered share of each pixel, where its top stands above the drawn
  surface (a crown under it the painter hides). The Titan trees join the paint store's crowns
  in the occluder where the painted layer draws them (`render/draw/light.py` `titan_crowns`):
  their top where it stands higher, and the larger cover. So they cast into the crown cells
  as the crowns do.
- **Its slope:** the occluder's top smoothed over its own pixels by `DOME_SIGMA_M` (0.75 m),
  with 0.35 of its relief (`CANOPY_RELIEF`), as the painted crowns' own lit domes took it
  until they were lit by their sprites' normals (section 36); flat where no canopy is. The crown
  plane steps by metres between neighbouring trees, and at its full relief a forest drew as a
  mottle of dark crescents.
- **Its horizon:** the crowns' horizon received on the canopy top, whole: the crowns' span
  march with its band folded in at the sun path's elevation (`span_bake.horizon_cells`, the
  cell's `whole`), not the larger of it and the ground's horizon measured under the canopy.
- **Its sky:** the sky view of the crowns stood on the surface.
- The painted layer's direct term and sky view are the ground's blended toward the canopy's
  by its share. The terms gain a fourth byte, the painted sky view (`stage.TERMS`), and
  `relight_rows` reads the third and fourth for a style that draws the crowns.
- **Drawn unlit, the Titan trees stood flat** (`palette/painted/trees.py` `titan_over`), as
  the crowns did. Before, the unlit colour kept the style's own north-west hillshade on
  them, under the light. Painted style 20, round 2's one bump. Since 2026-10-08 the trunks
  stand flat and the canopy, like every crown, keeps its sprites' normals at the default sun
  (section 36, "Drawing").

Only the baked copy has it. The page's shader still lights the canopy with the ground's
normal and sky view and the larger of the two horizons, until a tile carries the canopy's
share, slope and sky: the same follow-up as the arches' live light.

These changes are light model 3 and `LIGHT_VERSION` 2.

**Measured** at 2048 against round 2's baseline (2026-10-07, build 502094). The unlit trees
of terrain, satellite and both relief styles keep every pixel; painted's moves 89,570, the
Titan trees drawn flat. The lit trees of the four styles that draw no crowns move 129,058 to
151,071 pixels each, by up to 137 levels beside the void's coasts and pits and by a few along
shadow edges across the map, where the default sun is now taken at pixel centres. Painted's
moves 1,110,430, by up to 78, over its forests. The full-size windows drawn unlit (section 40)
move only in painted, under the Titan trees: 4.29 million of the strip's 33.6 million pixels
and 11.7 million of the water window's 67.1 million, by up to 94. In full-size windows lit
by the bake at 0.229 m/px the void's
rim at (761.7, 2332.7) loses its wall shadow, the Titan forest at (1769.7, -10.0) its ravine,
and the sky view's step at the block edge of row 12288 goes.

### Horizon tiles and coarser levels (2026-10-08)

The page's shadows of arches, overhangs and crowns came out blotchy and blocky, with bright
bars along tile edges and thin shadows broken into dashes from z6 out; the baked copy had
none of it. Three causes, all in the horizon tiles, none in the shader's arithmetic.

**Horizon tiles at q95 where a band folds.** A folded band is stored inside the 6° soft
edge at the path's elevation (above, "The atlas"), about 10 steps of the byte there, and WebP
q75 moved those bytes by enough to swing the shade from lit to shaded: under the arches of
a full-size render the page's shade was off by 0.48 at the 95th percentile. A tile that
holds a folded band is now stored at q95 (`light_tiles.HZ_FOLDED_QUALITY`), a coarser tile
when any tile beneath it holds one (`light_tiles.folded_tiles`, `BlockDone.folded`), and
every other tile at q90 (`HZ_QUALITY`), whose shadows are the terrain's own soft horizon.
The light's `meta.json` counts the folded atlases as `tiles.hz_folded`. At 2048 a tile is
0.9 km across and 79 of the 85 hold a fold.

**Measured at full size** (2026-10-08, the v8 surface, build 502094; each encoding of the
same atlases). 12,856 of the 21,845 tiles hold a fold, 9,360 of the 16,384 at z7: arches,
overhangs and, mostly, crowns. The normal tiles stay 0.88 GB.

| Horizon tiles | z7 | z0–z6 | All | Against q75 |
| --- | --- | --- | --- | --- |
| q75, no border (before) | 0.97 GB | 0.56 GB | 1.53 GB | |
| q75, border | 1.06 GB | 0.59 GB | 1.66 GB | +0.12 GB |
| q90, border | 1.66 GB | 0.90 GB | 2.56 GB | +1.03 GB |
| q95 where folded, else q90 (chosen) | 2.07 GB | 1.10 GB | 3.17 GB | +1.64 GB |
| lossless where folded, else q90 | 3.17 GB | 1.51 GB | 4.68 GB | +3.15 GB |
| lossless | 3.40 GB | 1.57 GB | 4.97 GB | +3.44 GB |

On 480 folded z7 tiles, the page's shade inside the soft edge is off by, at the 95th
percentile: q75 0.50, q90 0.27, q95 0.18, q100 0.10, lossless 0. q95 takes 0.64 of the
lossless bytes and q100 0.82, which put lossless and q100 over the 2.5 GB the owner allowed
the light to grow; q95 was chosen on 2026-10-08. The light took 1,035 s on 16 workers, the
native levels 908 s.

**The v8 light at q95 was transcoded, not baked again.** It was baked with the folded tiles
lossless (`LIGHT_VERSION` 3), and a lossless atlas decodes to the exact bytes the bake
encoded, so each was encoded again with `light_tiles.hz_webp` at q95; the q90 and normal
tiles were copied, and `meta.json` was rewritten as the bake writes it: `light_version` 4,
the key's digest, `tiles.bytes`, `tiles.hz_bytes` and `tiles.hz_folded`. A version 3 bake so
transcoded is a fresh version 4 bake of the same surface byte for byte, its `meta.json` the
same text apart from the bake's two timings (a 2048 surface of ridges, a hole and crowns in
one corner: 170 tiles, 15 of them folded).

**The cells' border.** The atlas packs its 64 cells edge to edge, and a lossy codec smears
each cell's neighbour two or three texels into it, which the shader's clamp to the cell
cannot undo: a seam at every tile edge on cliffs, arches and canopy. Each cell now sits in a
16 px border of its own edge texels (`model.HZ_GUTTER_PX`, `light_tiles.hz_atlas`), and the
shader steps over it (`hz_gutter` in the model block; `litlayer.ts` `hz()`), so a cell's
edge is coded beside copies of itself. A pyramid without `hz_gutter` reads as before. On
synthetic cells unlike their neighbours, as a tile's east edge is unlike its west, q75 left
edge errors of up to 25 levels without the border and 7 with it (a test holds the ratio);
the border costs 3% of the bytes at q75.

**Coarser levels.** Each coarser level took the mean of four horizons in degrees. Where a
thin shadow at the top of the soft edge meets lit ground, that mean falls below the edge
and the shadow is gone, so a line of shadow turned into dashes. A coarser texel now takes
the mean of the four shades at its direction's path elevation and folds it back as a band is
folded, `el + 6° × (shade − ½)`; a texel all lit or all shaded keeps the mean of its degrees
(`lighting/refold.py`). A crown cell averages the larger of the two horizons, the one the
page shades with when it draws the crowns, and is kept only where it stands above the
ground's, so both styles read the mean of their own shade, exact for a sun on the path and
to half a stored step (0.03 of shade). It is a pure float32 function of the 2 × 2 texels
below; `refold.cu` is its CUDA twin under `--gpu`, the same bits (a test holds it, and skips
without a device).

**No data past a block's edge** is opened too (above, "Edges of the light").

All of it changes the light's bytes, so `LIGHT_VERSION` is 4 (3 stored the folded tiles
lossless and was never shipped) and a kept light is baked again; the pinned bake's digest now
covers its horizon tiles as well.

### The trees in the light (2026-10-09)

Light version 5. Until it, every crown was cast from half its lift to its top, the Titan
trees joined the crowns in one plane and cast as crowns, and a crown cell was stored only
where the crowns' horizon, with the terrain marched in it, stood above the ground's.

**Each species' underside** (`lighting/undersides.py`). A species' crown starts where
`LEAF_LOW_SHARE` (5%) of its leaf area seen from above lies lower, as a share of its crown
top: its LOD 0 read at the start of a run from the install, the paint store's material kinds
saying which triangles are leaves (bark for a species with none). The lowest few percent are
left out because a palm's or a bush's lowest card hangs near the ground and would make the
whole crown a column. A species whose mesh cannot be read keeps `CROWN_UNDERSIDE`, 0.5. On
build 502094, of the 53 species:

| Species | Underside | Species | Underside |
| --- | --- | --- | --- |
| `SM_GreenTree_01`, `_02` | 0.74, 0.76 | `DioTree_01`, `_02`, `_03` | 0.17, 0.32, 0.34 |
| `SM_Kapok_01`, `_03` | 0.75 | `SM_PurpleTree_01`, `_02` | 0.30 |
| `SM_AncientPineTree_01`, `_02` | 0.71, 0.54 | `CatPalm_01`, `SM_DypsisPalm_01` | 0.30 |
| `SM_Bamboo_01`, `_02` | 0.49, 0.55 | `SM_SnailBottom_*`, `SM_Uppochner_02` | 0.01 to 0.08 |
| `BluePalm_01`, `_02` | 0.58, 0.71 | stumps, bulbs, pollen trees, `SM_Trunk_01` | 0 |
| `SM_Mangrove_Tall_01` | 0.45 | `SM_Yucca_01`, `_02` | 0.51, 0.33 |

The paint store keeps one crown-top plane, the highest crown at each 1 m texel. Each tree is
placed again as `crown_sprites.stamp_tops` placed it, and a texel takes the underside of the
tree whose sprite stands highest there (`stamp_undersides`): 33 s for the store's 99,073 trees
and 494 million sprite texels, once a run, before the draw. The sheet takes the mean underside
over the crowns a pixel covers (`occluders.sheet_crowns`), and a pixel whose top is a Titan
tree's is marked as one (`render/draw/light.py` `titan_crowns`); `occluder_under` is the
light's third occluder plane, a byte a pixel, digested in the kept light's key.

**The Titan trees** are a slab `TITAN_SLAB_M` (12 m) deep under their canopy's top, the
rendered-look study's value, never below the ground. Before, half the lift of a canopy 46 to
86 m up was a slab 23 to 43 m deep. They cast under the ground's fade (40 to 150 m) instead
of the crowns' (25 to 80 m), which cut a canopy 60 m up off its shadow from 25 m out at a
low sun.

**Cells of their own, always stored.** The atlas grows to 97 cells in 13 rows (above, "What
is written"): the ground's 32 as before, the crowns' 32, the Titan trees' 32 and the trees'
ambient occlusion. The crowns and the Titan trees are each marched alone: over open ground
(`OPEN_M`), so no terrain stands in their cells, their tops standing on the ground where a
sample reaches down to the horizon (`spans/bake.py` `tree_surfaces`), received on the canopy
top. Every cell is stored, 0 where no tree is in reach. The page shades with the larger of
the cells it reads, so tree shadows alone are exactly the trees', and both on is the picture
of before at the native level but where a terrain horizon and a tree's band each hide part of
the sun disc: there the larger of the two shades a little less than their union did. The
default sun's crowned terms still read the union: for its two directions the crowns and the
Titan trees are marched together over the ground (`canopy_cells`), as the crown cells were
before, and that march gives the canopy's own light its horizon.

A coarser level refolds a tree cell on its own, except where the larger of it and the
ground's shades more on average than the ground's: there the cell takes the refold of that
larger horizon (`refold.refold`, and its CUDA twin), so with both switches on the page's
maximum is the mean of the larger and exact for a sun on the path, and with tree shadows
alone a 2 x 2 of texels where the trees raise nothing reads the trees' own mean. Where both
shade, tree shadows alone keep some of the terrain's at the coarser levels.

**Ambient occlusion** (`lighting/occlusion.py`). At three scales, boxes of half-width 1, 3 and
8 m (`AO_SCALES_M`, weights 0.3, 0.4, 0.3), a pixel is occluded by how far the mean height of
its box stands above it, against 1.5 times the half-width (`AO_DEPTH`); the weighted sum times
0.55 (`AO_STRENGTH`) is taken off its sky light, the rendered-look study's numbers with boxes
for its blurs. The heights are summed as integers in steps of 1/1024 m, exact whichever
window a block reads, so a block's occlusion is the same bits in any block layout, and
`occlusion.cu` is its twin under `--gpu`. A scale under half a pixel is left out: a 2048 sheet
keeps the two larger.

- **The ground's**: the occluding heights are the solid surface, what floats left out, so an
  arch or an overhang darkens nothing beneath or beside it (its sky view already lets the sky
  in beneath it), and the receivers are the drawn surface. It is folded into the normal
  tiles' sky view, which every style reads, and the default sun's sky terms: rocks, cliff
  feet and gullies darken by up to 0.55 of their sky light, a terraced rock at every step.
- **The trees'**: the same over the canopy top, crowns and Titan trees stood on the ground,
  against the ground's, as a factor on what the ground's leaves (`relative_occlusion`): the
  atlas's cell 96, `255 · o`, at half resolution, the coarser levels its mean. The page takes
  it off the sky light only while it draws the trees; the painted layer's baked sky term
  takes it, and the canopy's own sky the canopy top's occlusion.

**Measured** on three full-size windows of 2048 × 2048 px drawn by the renders command and
baked with each code (2026-10-09, master a22775a5 against this branch; `cache32k-master`'s
rasters, `inputs-stories`):

| Window | Crowned direct term (default sun) moved | Ground sky term moved | Painted sky term moved |
| --- | --- | --- | --- |
| Titan forest (1769.7, -10.0) | 108,002 px (2.6%), by -3 to 2 at p1 / p99 | 1,604,960 px, by up to -32 at p1 | 2,652,472 px, by up to -126 at p1 |
| The study's forest spot (81.2, -790.8) | 86,259 px (2.1%), by 0 to 2 | 1,900,466 px, by up to -29 | 2,635,088 px, by up to -125 |
| Rib bones (2981.2, -2745.6) | 6,853 px (0.2%), by 0 | 1,874,372 px, by up to -41 | 1,984,242 px, by up to -68 |

The default sun's shadows hardly move. At noon a Titan canopy wider than about 30 m blocks
every ray that passes beneath its edge from below, however deep its slab, so its shadow is
its footprint moved by the sun, and the straight-edged diagonal bands beside the canopies of
the study's forest spot, the shadows' sides swept along the sun's azimuth with the half-
resolution canopy's steps along them, are the same before and after. A 12 m slab lets the sun
in beneath a canopy at a low sun and under its narrower parts, where the page's light moves.
The sky terms carry the occlusion: under and beside the forests' crowns the painted layer's
sky term falls by up to half, and the ground's by up to a sixth beside rocks, cliffs and the
rib bones' feet; the arches' sky stays as it was.

At 2048 all three layers against master's run (`inputs-stories`, uncached): the unlit sheets
are the same bytes; the lit tiles move in 71 of each layer's 85, 1,235,545 pixels of
painted's by up to 56 levels, 1,099,503 of terrain's by up to 48 and 1,010,703 of
relief-dark's by up to 42; the normal tiles in 79 of 85, 1,965,962 pixels by up to 109 (the
sky view); every horizon atlas, its crown cells now stored everywhere. The pyramid takes 41.2
MB instead of 40.4, its horizon atlases 23.6 instead of 23.2, 78 of them folded instead of
79. The full size was not measured.

### Hooks

`bake_light` takes an optional `occluder` on the sheet's grid, which only casts: the crown tops
in metres, NaN where empty, or `(top, cover)` with the covered share of each pixel as a byte,
or `(top, cover, under)` with each pixel's underside byte (`occluders.UNDER_SCALE`: a crown's
underside as a share of its lift, 254 for all of it, or `UNDER_TITAN`, 255, for a Titan
tree). The trees stand on the surface, each lifted by its cover (`horizon.crown_surface`), and
are spans from their underside to that top (above, "Arches as spans"; below, "The trees in
the light"); without an underside plane every crown's is half its lift. The crowns cast into
the crown cells under their own shorter fade (`OCCLUDER_FADE_M`, 25 to 80 m), the Titan trees
into theirs under the ground's, each alone and received on the canopy top, so a tree is lit or
shaded where the painted layer draws it. `occluder_layers` names the layers that read them.
The paint store's crown tops feed it (section 36; the mapgen README's "Horizons and tree
shadows"). The arches and the overhangs come with the surface itself, in its `slabs/`.

### The page

`litlayer.ts` draws a lit layer on one WebGL2 canvas in the base-map pane: per tile the unlit
colour, normals and horizons, relit by the shader with the arithmetic of `lighting/model.py`,
and on a layer drawn apart at its trees the trees as a fourth texture (`littiles.ts`, below).
The z0 probe's `X-Map-Light` header carries the shader's numbers and the layer's `parts`; the
layer's `params.crowns` switches the crown cells on, and a pyramid without `hz_cells` reads as
the old 8 × 4 atlas with no fill; one without `hz_gutter` reads its cells edge to edge. A test
holds the shader to the Python model's constants. Without WebGL2, or when the
context or the tiles fail, the layer falls back to the baked `tiles/` with a toast. Settings →
map holds the default sun (game noon, 09:00, 16:00 or map north-west) and five switches:

| Setting | Label | Uniform | Off |
| --- | --- | --- | --- |
| `mapShade` | shade | `uLightOn` | the light is 1 everywhere: the unlit colour as it is |
| `mapTrees` | trees | `uTreesOn` | the trees are not drawn, and neither their shadows nor their sky occlusion is read: the light of a world without trees. Only a layer whose probe has `parts.trees` can switch them; on any other they stay on |
| `sunShadows` | terrain shadows | `uGroundSh` | the ground's horizon cells are not read: rocks, cliffs, arches and overhangs |
| `sunTreeShadows` | tree shadows | `uCrownSh` | the crown cells are not read; only a layer with `params.crowns` has the switch |
| `sunSky` | sky light | `uSkyOn` | the sky view is 1 |

The shader takes the horizon as `max(uGroundSh · ground, crown, Titan)`, the tree terms only
while `uCrownSh · uTreesOn`, the Titan term only where the pyramid has its own cells
(`model.titan_cell`), and scales the shadow by `max(uGroundSh, uCrownSh · uTreesOn)`, so all
off casts none and all on is the picture from before the switches. The trees' sky occlusion
(`model.ao_cell`, linear) takes its share off the sky view while the trees are on.

**The trees on the page.** A tile's trees are fetched only while the trees are on, as straight
alpha premultiplied by the browser on decoding (`createImageBitmap`'s `premultiplyAlpha`), so a
bilinear read never pulls in the colour the lossless WebP leaves free under alpha 0; a tile the
sparse tree does not hold answers 204 and draws one shared transparent texture. The shader
lights the ground and the trees with the same light, the ground weighted by its land weight
and the trees by 1, so a crown over the water now takes the live light and its neighbours'
shadows; it lays the trees over the ground at their alpha in linear light, before the tone
curve. With both on that is the unlit colour of before, up to the 8-bit rounding of the two
stores (section 36, "Trees apart"). The baked `tiles/` still keep a crown over the water at the
light it was drawn with. A layer drawn before its trees came apart keeps them in its colour:
its trees row shows greyed, and its trees are always on.

The sun button on the map opens a time-of-day slider on
the game's path, the presets, the three shadow and sky switches, a "hillshade only" button
(both shadows and the sky off, the shade on, the sun where it is) and, under "advanced", a free
compass with azimuth and elevation. The button moves the sun for the visit; Settings keeps the
default. The shade is a checkbox under the base-map radios instead ([page.md](page.md) §18),
because with it off the sun button has nothing to move: the button greys out and says why.

The controls show only on a base map whose probe carries `X-Map-Light`. Where the layer is
drawn with its baked light instead, without WebGL2 or after a failure, they show greyed out
with the reason as their tooltip.

The trees row sits under the base-map radios beside the shade. While the trees are off the
sun panel's tree-shadow switch greys out and says why.

**Tree shadows without terrain shadows.** Until light version 5 the bake stored a crown cell
only where the crowns' horizon stood above the ground's, and the crown march took the
terrain with it, so with terrain shadows off a tree's shadow inside a terrain shadow was
missing and the sun panel said so. From light version 5 the crown and Titan tree cells hold
the trees alone and are always stored (below, "The trees in the light"), so tree shadows
alone are exactly the trees'. A page reads the Titan tree cells and the trees' ambient
occlusion cell by the model block's `titan_cell` and `ao_cell`. On a pyramid without them,
with terrain shadows off, a tree's shadow that falls where a terrain shadow would also fall
reads a crown cell of 0 and is missing, and the sun panel says so in a line under the
switches while that mix is set.

### Open

- The spans' bands on the page, so a sun off the game's path shades them too.
- The sun in the fragment, so a link carries it, and the shade, trees and shadow switches with
  it.
- With the shade off the page still fetches the normal and horizon tiles it no longer reads,
  four fifths or more of a tile's bytes; skipping them needs the tile cache to load each kind
  apart, as it loads the trees.
- Faint diagonal bands at low sun from the direction interpolation, and from the q90
  encoding on tiles without a folded band.
- On the page a crown still takes the ground's normal and sky view, and the ground's horizon
  measured under it where that is higher; the baked copy lights it by its own top (above,
  "The canopy's own light").
- The lattice seams at landscape holes (above, "Edges of the light").
- The baked copy keeps a crown over the water at the light it was drawn with; the page lights
  it (above, "The trees on the page"). A tree's shadow still falls on no water.
- The horizon march reads the 0 m the heights drop to under no data as ground, so a void edge
  beside low ground casts a wall's shadow on it ("The land weight" fades the light at the
  edge itself, not the shadow it throws further in).
- Sun colour along the day, and whether the artwork style gets any light at all.

## 36. Tree crowns (2026-10-05)

Each tree is drawn as its own crown: the species' mesh seen from above, at its own position,
yaw, scale and lean, in the colour its leaf texture has. A soft canopy of one blurred disc per
tree, with a radius guessed from the mesh name, cannot do that (bamboo guessed at 1.5 m
measures 4.4 m), and rock hid most of the forest on cliff tops. The Titan trees are static
meshes, not foliage, and are drawn by section 30.

### The paint input

`python -m mapgen paint` writes three files and a `crowns` block in `meta.json`:

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

**The soft canopy** keeps its plane and its rule, `1 - exp(-crown area per m^2)`, with each
tree's radius its species' measured one (the disc that hides as much ground as the sprite)
times its own scale, snapped to eleven bins from 0.5 to 20 m.

### Drawing

Since 2026-10-08 every crown is drawn from its species' crown sprite ("Crown sprites" below):
leaves, holes and branches in their own colour, lit by their own normals. The paint store's
sprites (one flat colour per material slot) only feed the crown-top plane now.

**The atlas.** A painted run reads the crown sprite cache of the installed build, and builds
it first where it is missing, for another build, or short of a species of the paint store
(`render/run/sprites.py`, `--sprites-dir`, default `data/local/crown-sprites/`). It decodes it
once into one float32 atlas (`terrain/crown_atlas.py`): per texel the linear colour, the
normal and the crown top, each times alpha, then alpha; a tile per species and mip with its
one-texel gutter. Because every channel is times alpha, a bilinear read between a crown's edge
and its gutter fades all of them together, and the gutter's alpha of 0 adds nothing. A crown's
top is that read over its alpha, the alpha-weighted top.

**The stamp** (`terrain/crown_stamp.py`). Each tree takes the mip whose texel, times the tree's
scale, is nearest the output pixel (log2 of their ratio, rounded), is turned by its yaw,
scaled, and shifted along its trunk axis by the species' mean crown height, so a leaning
bamboo's crown stands off its base. Trees are laid lowest top first, each over the ones below.
A band returns cover, colour and normal times cover, composited front over back, and the
highest crown top in world cm where the cover reaches `COVER_TOP_MIN` (0.25).

What a tree needs is worked out on the host in float64 (`crown_placements`): its tile, its
stamp centre, its pose as float32 (turn, the tile's corner and texel in the tree's scaled
cm, its top's rise, base and opacity) and the rows and columns it can reach, bounded by its
tile's farthest covered texel plus a texel's diagonal, the bilinear tent's reach. Per pixel,
the pixel's centre less the stamp's centre is taken in float64 and rounded to float32 once;
the rest is float32 in a fixed order. Three implementations give the same bits: the numpy
reference (`_stamp`), numba's (`terrain/kernels.py` `stamp`, the default), and CUDA under
`--gpu` (`render/gpu/crowns.py`, kernel `stamp_crowns` in `render/gpu/texels.cu`, the trees
binned into 16-pixel cells as the sprite stamp bins its sprites, the atlas uploaded once a
process). Tests hold the two kernels to the reference bit for bit.

A pixel is placed on a sprite from its own centre on the sheet, so a crown draws the same
whichever band or window holds it (2026-10-07); counted from the band's corner, a crown moved
with the band that held it.

**The light.** Each pixel's sun term is the normal of its crowns, averaged by alpha, each
sprite normal turned by its tree's yaw (`trees.crown_sun`), against flat ground: drawn lit, the
style's north-west sun; drawn unlit, as in every run with the light, the light's default sun
over its own flat ground. A crown drawn unlit so keeps the leaf-level relief the light cannot
draw (the canopy's own light takes the crown tops smoothed by 0.75 m, section 29), as the
meshes only the painted style draws keep the default sun (`band.painted_ndl`); an upright
crown reads flat either way. The airbrushed dome that lit a crown before, its cover times its
top blurred by 0.75 m and lit at 0.35 of its relief, is gone, and with it `dome_gain`.

`palette/painted/band.py` composites the crowns that stand out of the water last, over water
and foam, under the highlight shoulder (`trees.over_crowns`), or keeps them apart ("Trees
apart" below); a crown under the water's surface is drawn in the bed instead ("Crowns and the
water" below). The `crowns` block of
`satellite-painted.json`:

| Key | Value | What |
| --- | --- | --- |
| `draw` | true | Off, the soft canopy is drawn instead |
| `canopy_kept` | 0.0 | How much of the soft canopy stays under the crowns. 0: the crowns replace it, so no tree is drawn twice |
| `darkening`, `chroma` | 0.85, 0.8 | Times the sprite's colour, and times the style's own chroma gain of 1.2 |
| `shade_clamp` | [0.55, 1.2] | The light: the style's sky and sun over the crown normals' sun term, relative to flat ground and clamped |
| `hidden_below_m` | 0.5 | A crown whose top is more than this below the drawn surface is hidden: a tree under an overhang, or beside a higher rock |
| `waterline_m` | 0.1 | The height over which a crown's top passes from over the water to under it, centred on the water's surface |

**The colours** are the sprites' texels, moved by section 31's crown calibration as before:
the species and named crown targets move a species' tiles, the canopy target each pixel. The
ancient pines' sprite reads its needles' mask from the packed `ORMA` blue, so they draw olive
needles where the paint store's flat colour, the whole card's mean, drew them mustard. The
canopy target's op is measured on the sprites: the median green crown is the Kapok's, greyer
in its sprite than its flat colour was, so the op lifts lightness by 0.063 and chroma 1.54
times, where it lifted 0.037 and 1.14; a crown already more saturated than the Kapok, the
green trees' game view, draws greener than before. That is the op to the 1.0 screenshot
#558653; the derived canopy, measured on the paint store's flat slot colours, asked ×1.83 and
is not taken since 2026-10-09 (calibration.md section 31, "The second review").

**Measured** (2026-10-08, build 502094, the gate inputs' paint store, against master
a22775a5's own runs):

- At 2048, painted and lit: the light's 170 tiles keep their content, the light is not
  touched. Of the painted tiles 578,950 of the native level's 4.19 million pixels change
  (13.8%, 48 of 64 tiles, by up to 123), and 26 to 35% of each coarser level; `unlit/` the
  same.
- At 32768, 1024-pixel windows drawn unlit and lit by the style's own sun: 772,689 of
  1,048,576 pixels change at the Titan forest (1769.7, -10.0), 719,346 in the Northern Forest
  (81.2, -790.8), 648,659 in the Red Jungle (-1036, 237) and 166,457 on the Spire Coast
  (269, -1943), by up to 110 to 154 levels. The ancient pines' crown cores there move from
  #8e8826 to #707c21.
- The Titan canopy's sprites against the raster's leaves over 600 m round the Titan forest:
  the footprints overlap by 0.89 (cover over a half on 42% of the pixels against the leaves'
  47%, the cards' holes), and the sprites' top stands a median 1.2 m under the raster's.
- On those windows the CUDA stamps give numba's bits on the real atlas and paint store.

### Known limits

- **Colours are the sprites', moved by the canopy target, the red Kapok's species target
  and, for the blue palms, a target of their own.** Crowns of a target's own hue, and the red
  Kapok `SM_Kapok_03` by name, are calibrated (section 31, "Crowns"); every other crown keeps
  its sprite's colours: bamboo is a saturated pink-red. The style's `chroma` of 0.8 is a
  taste call.
- **Blue palms are blue, on a target of their own.** `BluePalm_01` and `_02` (3,332 trees:
  1,747 in the Rocky Desert, 768 in the Savanna, 344 on the Spire Coast) have one leaf colour,
  the leaf half of `TX_BluePalm_01_Alb`: light blue with white midribs, linear (0.27, 0.40,
  0.46). Their instances `MI_BluePalm_03` and `_04` carry no vector parameter but the wind
  pivot, and the parent `MM_WindPlants` is cooked without its graph, so no tint is skipped
  that could be read. That texture mean draws near-white powder blue (#a3c3d3, L 0.80), where
  the game shows a saturated mid blue from above, so they take the named crown target #3d627d
  (section 31, "Crowns"). Over the Rocky Desert windows at 0.92 m/px, palm pixels draw
  #3a5e78 with lit tops #416783: ΔE 4.7 to the Rocky Desert area shot's #2e6695, and 5.5 to
  the river split's lit tops #3e5579. The wiki's Rocky Desert and Spire Coast shots show blue
  palms in both biomes, so they stay blue everywhere.
- Drawn unlit, a crown keeps its sprite normals' shading at the default sun whatever sun the
  page picks, and the light's canopy term, lit by the smoothed crown tops, comes on top of
  it: on a sloped canopy the slope is lit twice, mildly, at the canopy's 0.35 of its relief.
  The page's normal pyramid has the ground's normals only; a tree normal tile would let the
  page light the sprites' normals itself.
- A crown's normal is turned by its tree's yaw; lean and an uneven scale do not tilt it.
- Lean moves a crown; it does not foreshorten it.
- `SM_Trunk_01` (6,933 logs and stumps) draws as small bark sprites.

### Crowns and the water (2026-10-06)

Of the 99,073 trees, 6,490 stand on a wet texel of the field, 6,035 of them at the ocean's
level, and 475 have their top under the water's level. A fade over every wet pixel draws them
wrong: on the Spire Coast's sand spit at (243, -2078) the crowns over water stand on ground a
median 0.2 m under the drawn sea (quartiles 0.1 to 0.4 m), so the water cover there is 1 and a
60% crown shows 40% of the sea through it, faded and blue, as if drowned. So each pixel of a
crown is compared with the water's surface there, the drawn ground plus the water's depth
(`palette/painted/trees.py` `crown_layer`):

- **Out of the water**, its top more than half of `waterline_m` over the surface: drawn
  whole over the water, the foam and the shore line, exactly as over dry ground. The rocks
  and the render-only meshes stand out of the water the same way, by raising the drawn
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

A river under bamboo is hidden by the crowns, as from above in the game; it shows where the
canopy is open. The Titan trees are laid over the water and stand tens of metres out of it,
opaque where their canopy's leaves are (section 30).

**The light.** The lighting stage leaves water unlit: its land weight falls with the water
cover (section 29, "The land weight"), and the one pyramid serves every layer, of which only the painted one
draws crowns. So in the baked copy a crown over water keeps the flat light it was drawn with:
no shading, no shadow from its neighbours. On the page, a render with its trees apart lights
the trees at a land weight of 1, so a crown over the water takes the live light and its
neighbours' shadows ("Trees apart" below); one drawn before keeps the flat light. No tree
shadow falls on water, neither on its surface nor on its bed, and a render without the light
draws no cast shadow at all.

### Coral trees are no crowns (2026-10-06)

The small coral trees are tiered plates, each a shallow bowl, with a dark line at every step.
Drawn as crowns they read as smooth domes lit from the north-west.

**Drawn twice.** `SM_CoralTreeSmall_01` (473), `SM_CoralTreeSmall_02` (570) and
`CraterTree_02` (341) are foliage under `/Foliage/Coral/`. The paint store takes them as trees
(`TREE_MARKS` "/Coral/CoralTree" and "CraterTree"), and the render-only mesh pass takes the
same instances (`RENDER_ONLY_DIRS`, section 27). Drawn as both, each is its mesh, lit by its
own max-Z top, covered by an opaque crown: the platform material has no mask.

**The geometry.** The species' sprite is the LOD 0 top seen from above. `_01` is a plate
whose rim stands at 13.4 to 13.6 m and whose middle at 12.2 to 12.6 m, a 1.2 m dish over a
4 m radius, with the stem's knob above it. `_02` has a top plate with its rim at 16.4 m and
its middle at 15.6 m over lower plates at 11 to 12 m. Each plate is concave.

**Why a dome.** A crown is lit by `sun_dot` of its dome: the cover times the top, blurred by
0.75 m (`DOME_SIGMA_M`), times `dome_gain` 0.35. The blur pulls the sprite's edge down to
zero, from 13 m to 9 m over its last metre, and that edge outweighs the 0.8 to 1.2 m dish
shrunk by 0.35. Every coral tree comes out lit as a hump.

**The rule.** A species the render-only mesh pass draws is no crown: `load_crowns` drops its
records (`terrain/crown_stamp.py` `meshed_species`, `is_render_only_foliage` on the species'
mesh). The paint store still holds them: its crown-top plane holds the coral, so in a render
with the light a coral tree still casts a tree shadow, and its canopy plane counts them. The
coral trees draw as their meshes, in the calibrated colours: the coral mesh colour #917c75 lit
by the mesh's own top, the seabed coral under water, and section 31's rule for a coral speck
standing in the sea.

**In a render with the light.** The lighting pyramid's surface is the seabed rule's (section
27), whichever layer captures it (section 29, "One capture"). The rule leaves a render-only
mesh standing in the water to the seabed, so the pyramid holds those pixels as water, with
land weight 0, and leaves them unlit. The painted layer, drawn unlit, keeps the default sun's
Lambert term of its own top on the meshes only it draws (`palette/painted/band.py`
`painted_ndl`, with `lighting/model.py` `surface_direct`, the shader's direct term without
shadows). The pyramid has those pixels as water, so the page leaves them as baked. Coral on
dry land is in the surface and is lit live.

**Measured** on the render archive's windows, with the windows' render-only meshes from their
base sweep: the luma of coral-tree pixels follows the north-west sun term of the true surface
at a Pearson r of 0.97 to 0.99 and the crown dome's at 0.36 to 0.57; drawn as crowns it was the
other way round. In a full run's light, at the default sun, the coral standing in the sea
follows its own surface at r 0.70 to 0.95 (375 to 6,228 pixels a window). The caps' median is
1.3 to 1.8 Delta E from the then target #99868e, against 6.8 to 7.1 as crowns.

**Known limits.**

- In a lit run the coral standing in the sea keeps the noon light whatever sun the page
  picks, and takes no cast shadow.

### Trees apart (2026-10-08)

The page can switch the trees off (section 29, "The page"): to see the ground under a forest
or under the Titan trees when building, or for the look. The light needs nothing new for it:
the ground's cells, normals, sky view and land weight are the ground's without the trees, and
the trees' shadows and sky occlusion are cells of their own. What it needs is the colour
without the trees, and the trees apart from it.

**The split** (`palette/painted/band.py` `painted_parts`). The painter lays the crowns that
stand out of the water and then the Titan trees over the finished pixel last, each
`out · (1 − a) + c · a` in linear light, before the tone curve. The pixel before them is the
ground `G`; the layers fold into one (`trees.folded_trees`): `A = 1 − Π(1 − aᵢ)` and
`C = P / A`, `P` the colours laid in turn premultiplied, a crown's `a` its alpha times the
share of it out of the water. `G · (1 − A) + C · A` is the picture, which is still laid as
before, so `tiles/` and `tiles@2x/` keep their bytes.

- `unlit/` takes `tone(G)`, toned in the picture's own float type, so a pixel no tree covers
  keeps its bytes;
- `trees/` takes `tone(C)` as sRGB, with `A` as a byte.

The page undoes the tone and the sRGB curve of each, as it does the unlit colour's, and lays
the trees over the ground; with the light on, both take it. A test holds that the two stores,
laid again, give the picture within one level (`tests/mapgen/test_trees_apart.py`).

**Left in the ground.**

- A crown under the water's surface is drawn into the bed ("Crowns and the water"): optics,
  not an alpha, so with the trees off the swamp's sunk plants still show.
- The coral trees are render-only meshes ("Coral trees are no crowns").
- A crown hidden under an overhang (`hidden_below_m`) is in neither, as before.
- The void and the falls are drawn over the picture and over the ground; on the ground no
  crown hides a fall's foam, and the trees take no alpha where the void covers them
  (`render/draw/painting.py` `draw_band`). The arches' FXAA runs on the ground and the lit
  picture; the trees are not filtered.

**The draw and the cut.** A run with the light draws the painted layer apart
(`render_layers(split=...)`, only unlit and only into a band sink): each piece hands its
ground and its trees as bytes beside the picture (`painting.TreeSplit`), and the stream sends
the ground to `unlit/` and the trees to `trees/` at once, with no light to wait for, while the
picture waits for its light as before (`render/draw/stream.py`). The tiles are lossless WebP
(`tiles/formats.py`): the ground at effort 2, as the normal tiles; the trees at 4, RGBA with
straight alpha and `exact=False`, so the colour under alpha 0 is free to compress. A coarser
level of the trees is Pillow's Lanczos on RGBA, which resamples premultiplied, so no colour
from under alpha 0 bleeds into an edge (a test pins it). A trees tile whose alpha is all 0 is
not written: the tree is sparse, and its record counts the tiles written per level, with its
`layout`, `alpha` and `sparse`. The layer's sidecar names it in its light block as
`trees_dir` and `trees_tiles`; the server answers a tile on its grid that is not there with
204 (maps_contract.md section 8.1).

**Measured** at 2048, the painted layer drawn on master's surface before the split and after
it (2026-10-08, build 502094):

- `tiles/` (85 tiles), `tiles@2x/` (21) and the light's 170 tiles are the same bytes.
- `trees/` holds 68 of the 85 tiles, 1.75 MB; 13.3% of the native level's pixels hold a tree.
- `unlit/`, the ground, is 4.49 MB as WebP. The picture it was is 5.53 MB as PNG and 4.60 MB
  as WebP; terrain's and relief-dark's unlit trees come out 23% and 20% under their PNG.
- The ground and the trees laid again as the page lays them, the shade off, give the old
  unlit colour within one level on 99.77% of the native level. Of the 9,728 pixels off by
  more (at most 81 levels), 8,935 lie within reach of an arch's FXAA, which filtered the
  picture's crowns with the arch but leaves the trees layer unfiltered; most of the rest lie
  under a waterfall's mist, which the picture fades by the crowns' cover in sRGB and the page
  by their alpha in linear light.
  The ground differs from the old unlit on 6,009 pixels no tree covers, the same FXAA and
  crowns whose alpha rounds to 0.

At 2048 a crown is one to three pixels, so nearly every tile holds one. At full size the
trees are estimated at 140 to 165 MB, from 48 forest tiles of the v8 render coded the same
way, against the quarter the ground's WebP takes off the 526 MB of the painted layer's PNG.

**Limits.**

- The Titan trees go with the trees; they cannot be hidden alone.
- A coarser level of the picture is not its coarser ground and trees laid over each other: they
  differ by a covariance term at the trees' edges, which does not show.
- The page relights a crown with the ground's normal; the canopy's own normal and sky view
  would be a sparse tile beside the trees (section 29, "Open").

### Crown sprites (2026-10-08)

The rendered look draws each crown as the tree really looks from above: leaves, holes and
branches in colour, with a normal the light can shade and an alpha the ground shows
through. The crown sprites are that art, one per species, every species covered, and since
2026-10-08 every crown is drawn from them ("Drawing" above), the Titan canopy too
(section 30).

`python -m mapgen crown-sprites` takes the species from the paint store's `crowns` block and
the Titan canopy's meshes from a sweep of the levels, with their placements; reads each
mesh, its textures and its billboard from the install, and writes `data/local/crown-sprites/`
in about 90 s on the CPU, a third of it the sweep. On build 502094 that is 55 species: 8 from
the game's own view from above, 47 rasterised from their meshes, the two Titan canopy meshes
among them; and 218 Titan canopy placements. A painted run builds the cache itself where it
finds none current (`--sprites-dir`).

**Two sources.** A tree mesh carries its far-distance stand-in as a material slot, of one of
three kinds, told apart by the master material (`gamedata/vegetation/billboards.py`):

| Kind | Master | Views | Species |
| --- | --- | --- | --- |
| octahedral | `MM_OctaBillboardMat` | 3 x 3 frames: eight round the tree, the bottom-right one from straight above | 12 (11 bake folders: `SM_GreenTree_02`'s atlas sits in `SM_GreenTree_01`'s) |
| impostor | `Imposter_Master` | 4 x 4 to 8 x 8 frames round the tree at one elevation, no top view | 12, among them `DioTree_01` and `_03`, `SM_AncientPineTree_02`, `SM_SnakeLegs_Tree_02`, the Dypsis and cat palms |
| SpeedTree | `MM_SpeedTreeImposter` | side views, and a frame from above that the mesh does not use | the three Kapoks |

The rest have no billboard at all, the yuccas, Purple and Amber trees among them. The
Kapoks' SpeedTree atlas does hold a crown from above, bottom left, but of another tree: it is
the billboard `SM_Kapok_01` shares with a sibling (`MI_Kapok_01_02_Imposter`), and placed at
its best it overlaps the mesh's footprint by 0.50, with whole limbs on one side only. It is
not used. Every species is rasterised from its mesh (below); one whose octahedral top view
is usable takes that view instead. A view is usable when its texel spans at most `ATLAS_TEXEL_MAX_M` 0.2 m
on the ground and its footprint overlaps the raster's by `ATLAS_IOU_MIN` 0.6. Four atlases
are coarser: `DioTree_02` 0.23 m, `SM_SnakeLegs_Tree_01` 0.25 m, `SM_Mangrove_Tall_01` 0.34 m
and `SM_AncientPineTree_01` 0.39 m a texel, the cook having dropped their top mips. The
finest sheet's pixel is 0.229 m, and a view at its own pixel would be blurred twice by the
stamp's resampling, so those four keep the raster.

**The top view on the mesh** (`sprites/align.py`). Read off all twelve octahedral atlases
against the raster, three conventions place a frame with no fitting:

- the frame is centred on the mesh's pivot (within one atlas texel on every atlas, though
  `SM_GreenTree_02`'s bounds centre stands 11 m off it);
- turned by `np.rot90(frame, 3)`, its rows run along +Y and its columns along +X, as the
  sprite grid's do; its normal's red is +X and green +Y;
- its width is one of two measures of the mesh: twice the bounds' top over the pivot
  (`height`: the green trees, bamboo, the funnel tree, `DioTree_02`, the tall mangrove,
  Snake Legs), or the diameter of the sphere round the bounds' centre that holds every vertex
  (`sphere`: the blue palms, the small mangroves, the ancient pine). Each matches the width a
  free fit finds to 3%, and the generator's setting is not in the cook, so the rule whose
  footprint overlaps the raster's more is taken.

The frame is read onto the sprite grid at 4 x 4 samples a texel, colour and normal weighted
by alpha. Its colour is the render target's linear albedo. The billboard material's own
`Brightness` (up to 2.2) and `Desaturation` make up for how a flat card is shaded and are not
applied, so the view's albedo is the mesh's. The render target kept only each normal
component's positive half: z, which faces the camera, is whole, and a negative x or y reads
0. What the unit length leaves over goes back on the axes that read 0, shared as the raster's
own normal leans there, evenly where it does not. The crown top is the raster's, carried to
the view's texels the raster leaves bare from the nearest it covers.

**The mesh raster** (`sprites/raster.py`). LOD 0 is read with its first UV set, its tangent
basis and its vertex colours (`staticmesh.lod0_surface`: the UVs vertex-major after the packed
basis, half or full float; the basis two vectors a vertex, tangent then normal, four int8 over
127, the normal's fourth the bitangent's sign; a colour an `FColor`, stored BGRA). Each
material slot is a leaf, a bark, or skipped as the paint store's sprites skip it. A slot's albedo is its
texture in linear light times the instance's `Brightness` and `Saturation`; its texture is
read at the size whose texel spans about one sample on that slot's triangles, and a leaf at
256 texels at least, since a smaller mip blurs the cut-out into a gradient. A leaf's mask is
the first channel that is a cut-out, nearly every texel within 32 of 0 or 255: the packed
`ORMA` map's blue first, where the wind-plant master keeps it on every leaf card of the
build; then its alpha; then the albedo's, unless that holds subsurface. A bark is never cut.

Each 0.125 m texel is sampled 4 x 4 times. A sample takes the highest triangle over it whose
mask passes there, bilinear, from a third of 255 up; a tie keeps the triangle first in the
mesh. A triangle whose UVs stay inside the texture holds its edges, so a card's border never
reads the far side of its atlas; one that tiles wraps. The hit is then shaded
(`sprites/shade.py`, below): its colour and its normal. A texel's alpha is its share of
samples hit, its colour and normal their means, its top the highest. Triangles edge-on from
above draw nothing.

**The material at a hit.** What the material instances expose beyond the albedo, and what
the shading makes of it:

- **Normal maps.** A slot's `Normal` (else `Grass Normal`, else `Baked Normal`) is read at
  the albedo's size, red and green as x and y over 127.5, z the unit length's remainder. The
  hit's interpolated tangent `T` and normal `N` give the bitangent `B = cross(N, T) * sign`,
  and the normal is `x T + y B + z N`, normalised: the engine's tangent space. On a card seen
  from its back `N` is turned over and `T` and `B` are kept, as the engine's two-sided
  foliage does. Checked against the game's own render: flipping green lowers the normals'
  agreement with the octahedral views on nine of the twelve (`SM_GreenTree_01` 0.87 to 0.80,
  `BluePalm_01` 0.88 to 0.83) and leaves the other three level, so the stored convention is
  the engine's. The maps add leaf-level relief that the views, read at 0.03 to 0.39 m a
  texel and rebuilt from their halves, mostly blur away: agreement moves by -0.02 to +0.01.
- **Spherical normals.** The Dio leaves (`MI_DioTree_LeafMap_01` and `_03`) bend their
  normals 0.976 of the way toward the direction from a pivot, the master's
  `Spherical Normals Influence`. The pivot is taken as the instance's
  `1.2 Style wind Crown Pivot` (`DioTree_02`: 10 m over the pivot); that the bend is about it
  is an assumption, which the game's view of `DioTree_02` supports: agreement 0.803 without
  the bend, 0.812 with it. No other tree material sets it.
- **Moss.** Five bark instances carry a `Moss Albedo` with a `Moss Color Tint`, a `Fall
  Off` and a `Contrast` (the three Kapoks', the purple trees' and the dead swamp trees').
  Where a bark's normal turns up it wears the moss texture's mean colour times the tint:
  none below z `1 - Fall Off`, all from `Fall Off / Contrast` above it (the Kapok: from 0.61
  to 0.71). How the master ramps it in is not in the cook, so that ramp is a stand-in; the
  moss's own texture detail is not drawn. The green trees' and the Snake Legs' bark set a
  tint and a ramp but take their moss texture from the master, which the cook keeps without
  its defaults, so they wear none.
- **Vertex colours** are not colour. Every wind-plant mesh that has them keeps wind data in
  them: red tracks the height (r 0.92 to 0.98 wherever it varies), green takes 4 to 78
  distinct values (a branch index, by the master's `BranchID` parameters), alpha mostly two
  or three levels. They are read and left out.
- **Tints.** No tree material sets a hue, a colour variation or a per-instance tint; the
  only colour scalars are `Brightness` and `Saturation`, applied to the albedo, and the
  coral's and crater tree's `Emissive Color`, a glow that is no albedo and is left out. The
  graph a master would apply them in is not in the cook either, so anything it does with
  wind data, distance or the time of day cannot be recovered.

The per-sample work is kernel-shaped, in two passes: the fill (`sprites/fill.py`) and the
shading (`sprites/shade.py`), each numpy as the reference and a CUDA twin in `raster.cu`
under `--gpu`, the fill a thread a sample over 32 x 32-sample bins of triangles in index
order, the shading a thread a hit. Each twin does the same float32 operations in the same
order (renders.md section 41), with no transcendental function: the textures are linear and
the normal maps decoded before either pass. A build gives the same bytes either way; the
twins' tests skip on a machine without a device. Triangle setup, the tables and the
gathering into texels stay on the CPU.

**Validated** on the eight species and four more whose atlas exists, raster against atlas:

| Species | Footprint overlap | Area, raster / view (m²) | Luminance, view / raster | Normal agreement (mean cos) |
| --- | --- | --- | --- | --- |
| `SM_GreenTree_01` | 0.77 | 703 / 700 | 0.96 | 0.87 |
| `SM_GreenTree_02` | 0.76 | 210 / 206 | 0.91 | 0.70 |
| `SM_Mangrove_01` | 0.85 | 56 / 53 | 0.97 | 0.95 |
| `SM_Mangrove_02` | 0.76 | 33 / 33 | 0.86 | 0.92 |
| `SM_Mangrove_Tall_01` | 0.76 | 498 / 507 | 1.16 | 0.93 |
| `SM_AncientPineTree_01` | 0.74 | 437 / 396 | 0.94 | 0.82 |
| `DioTree_02` | 0.63 | 122 / 140 | 1.26 | 0.81 |
| `SM_SnakeLegs_Tree_01` | 0.71 | 195 / 190 | 0.85 | 0.59 |
| `SM_Bamboo_01` | 0.62 | 26 / 35 | 0.76 | 0.70 |
| `BluePalm_01` | 0.86 | 5.4 / 5.5 | 0.96 | 0.88 |
| `BluePalm_02` | 0.73 | 24 / 24 | 0.94 | 0.86 |
| `FunnelTree_01` | 0.84 | 43 / 45 | 0.98 | 0.93 |

The overlap is of two leaf-level alphas, so a one-texel shift of a leaf costs it; the areas
agree within 6% on nine of the twelve, and the hue is the same on all. The raster's colour,
read from the leaf textures, comes within 10% of the game's own render in luminance on seven
of the twelve and within 26% on all.

**The cache** (`sprites/store.py`) is laid out for one stamp kernel: `atlas.npz` holds three
planes over one atlas 2048 wide, `colour` (RGBA8: sRGB colour and alpha), `normal` (two bytes:
x and y as `n * 127 + 128`, so an upright normal reads back exactly upright, z the remainder
up) and `top` (uint16, the crown top in cm over the pivot), and `records`, one row per species
and level: its rectangle, its corner in mesh cm, its texel (12.5 cm doubling down the chain),
its highest top, and how far its farthest covered texel reaches from the pivot, so a stamp
bounds a tree under any yaw before it reads a texel. `first` and `levels` give each species'
first row and its count; `titan` the Titan canopy's placements as paint store records, their
species the atlas's. A level's texel `(r, c)` centres on `(x0 + (c + 0.5) t, y0 + (r +
0.5) t)`, rows along +Y, the paint store sprites' convention, so a stamp turns and scales it
by the tree's record. Each species carries its mip chain down to 4 texels:
alpha and alpha-weighted colour averaged, normals summed by alpha, the top as its weighted
mean. A one-texel gutter round each rectangle holds its edge colour at alpha 0, so a bilinear
read never meets a neighbour or fades to black. `meta.json` is the stamp (the build, reader
`crown_sprites` 1, the layout `format` 2, the texel), the record's dtype, per species its
mesh, source, instance count and what was measured (a Titan canopy mesh marked `titan`), the
species no sprite was made of, and the count of Titan placements. It is written after the
atlas and removed first, so a write cut short is a miss. 13 MB, an atlas of 2048 x 2454.
Format 1, before the Titan canopy and the exact upright normal, is read as a miss.

**Known limits.**

- The moss ramp and the spherical normals' pivot are stand-ins for a graph the cook leaves
  out ("The material at a hit").
- The game's view keeps whatever the material graph does at run time; the raster reads the
  textures and the instance parameters only, so a species' colour can move by the source it
  takes. Both are albedo, and the species targets of section 31 still calibrate them.
- `SM_Bamboo_01`'s view covers a third more than its raster and draws a quarter darker.
- The coral trees and `CraterTree_02` get sprites, though the render-only mesh pass draws
  them ("Coral trees are no crowns").
- The paint store's own sprites read a leaf's mask from the `ORMA` alpha or the albedo's, so
  the Kapok's (the albedo's subsurface) covers most of each card and the ancient pines' and
  Snake Legs' have none; the crown sprites read the blue. The draw no longer reads them; the
  crown-top plane, the light's occluder, still comes from them.

**The paint store keeps the coral (measured 2026-10-07).** The 1,384 coral trees, of 99,073,
still write the crown-top and canopy planes, and neither is a second drawing. The canopy plane
is weighted by `canopy_kept`, 0.0 while game-painted draws crowns, so leaving the coral out of
it changed no unlit pixel at 2048. The crown top is the light's crown occluder, which casts the
coral's tree shadow: without the coral, 32 of the light pyramid's 85 horizon tiles and 571
pixels of the lit painted pyramid at 2048 changed, by at most 21 levels. So the paint store
stays at generator version 3.

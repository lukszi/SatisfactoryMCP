# Space, and the map drawn on it

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §7 is the coordinate frame, the region
layer and the selector language every spatial and planning tool shares; §17 and §18 are the web
map's base layers and the mode model that lets a reader pick one; §19 is the water channel every
one of those layers draws. Section numbers are continuous with the rest of the spec;
[DESIGN.md](../DESIGN.md) indexes it.

The dated sections below name the generator files as they were when each section was written.
Since 2026-10-05 the map generators are one package, `tools/mapgen/`, run as
`python -m mapgen <command>`, and the old entry scripts are thin shims that keep their paths.
Its [README](../tools/mapgen/README.md) has the full package map. The names used below now
live here:

| Named below | Now |
| --- | --- |
| `tools/gen_world_heightmap.py` (`--caves`, `--rocks`) | `mapgen heightmap` (`caves`, `rocks`): `heightmap.py`, `gamedata/sweep.py`, `gamedata/mesh.py`, `gamedata/caves.py`, `gamedata/rocks.py` |
| `tools/gen_map_renders.py` | `mapgen renders`: `pipeline.py`, with `terrain/`, `palette/`, `lighting/` and `tiles/` |
| `tools/gen_map_image.py` | `mapgen artwork`: `artwork.py`; the frame and slice reader in `gamedata/frame.py` |
| `tools/gen_paint_layers.py` | `mapgen paint`: `gamedata/paint.py` |
| `tools/check_map_fill.py` | `mapgen check-fill`: `check_fill.py` |
| `tools/map_fill.py` | `terrain/fill.py` |
| `tools/map_shore.py` | `palette/shore.py` for the optics, `terrain/rasters.py` for the render-only meshes, `cache.py` for their cache |
| `tools/map_painted.py` | `palette/painted.py` |
| `tools/palettes/*.json` | `palette/palettes/*.json`, byte for byte, so no digest moved |

---

## 7. Spatial model


**Slugs are latent shards.** A shard pool counted only from crafted Power Shards
understates what a player can overclock with. On the reference save the Dimensional
Depot holds **93 Blue, 58 Yellow and 39 Purple slugs — 404 shards — against 22 already
crafted**, a 19x understatement.

The 1/2/5 ratios are *derived*, never listed: `GameData.slug_yields()` reads every
single-ingredient part recipe that produces a Power Shard, which is exactly
`Power Shard (1)`, `(2)` and `(5)`. Restricting to one ingredient also excludes
`Synthetic Power Shard`, which makes shards from Time Crystal, Dark Matter Crystal,
Quartz and Photonic Matter — a production chain, not something lying in a crate.

`craftable` is reported **apart from** `free`, because crafting is a manual step:
folding it in would produce a number the player reads as available now. The
affordability check uses both — "SHORT by 158, but 404 more are craftable from slugs you
already hold".

Slugs are found wherever `stock()` looks: carried, in crates, or in the Depot. The output
also names *where*, since "is that the Depot?" is otherwise a question the player has to
ask.

### 7.1 Coordinate frame — settled

| axis | meaning |
|---|---|
| **−Y** | **north** |
| +X | east |
| +Z | up |
| scale | 1 m = 100 cm exactly |

Established four independent ways, strongest first: the wiki `Crash_Site` table gives 118 pods with a
Region column *and* save coordinates — `Northern Forest` mean Y = −81,078 vs `Southern Forest`
+205,386; joining those rows to `crashSites.py` matches **117/118 to sub-centimetre**, which also proves
the wiki's coordinates *are* raw save coordinates and pins cm-per-metre at 100.

Content extents (2,688 static objects): X −298,838…406,564; Y −314,104…304,196; Z −16,827…46,942.

Biome grid `[WIKI]`: `GRID_CELL = 102400`, `GRID_X0 = -319600` (west edge of X0),
`GRID_Y0_SOUTH = 302800` (Y0 is southernmost). Emit the cell (`"X3Y4"`) on every node/site response —
it's exact and needs no interpolation.

**Compass bearing = `atan2(x - ox, -(y - oy))`.** The negated Y is the thing every naive implementation
gets wrong.

### 7.2 Regions: exact geometry, advisory names

**Decision: separate the two concerns.** All *computation* uses exact geometry — grid cells, cones,
radii, clusters. Region *names* are a separate, independently queryable, explicitly approximate dataset
used only for human-readable labelling and name lookup. A name never feeds a calculation.

**Layer 1 — exact geometry (authoritative, zero maintenance).**
Biome grid cell from §7.1's closed-form transform; cone/hemisphere direction tests; radius queries;
200 m single-linkage clustering. All derived, all exact, nothing hand-authored.

**Layer 2 — `data/region_names.json` (advisory). BUILT, and re-derived from the game.**
19 regions as a 256 m label raster with a matching confidence raster, plus a 64 m pair carried
alongside for lookups (`accuracy_m: 64`). Exposed as `label_for(x, y)`, `label_for_node(node)`,
`filter_nodes(nodes, name)` and `resolve(name)`. The optimizer and site ranking never consult it.

Regenerate with `uv run --extra gen python tools/gen_region_names.py`, which reads the game's own
`FGMapAreaTexture` through `core.gameassets.maparea`. It used to rasterise
`data/satisfactory_regions.json`, a hand trace of a wiki image; that file is **deleted** and so is
the CC BY-SA obligation it carried. What the re-derivation bought:

1. **The boundaries are the game's.** 4096² palette indices at 1.83 m to the texel, `mColorToArea`
   resolving each index to one `UFGMapArea` asset. Not a reading of a picture of the boundaries.
2. **The names are the game's too**, out of each area asset's `mDisplayName` — a string-table key
   `World_Data` / `Locations/<Something>`. Nineteen keys, nineteen labels, the identity apart from
   two plurals. It supplies three facts nothing else could: `Area_Savanna_1/2` are **Rocky Desert**
   (the game has a Savanna asset and no Savanna region), `Area_crater_1/2` are **Blue Crater** and
   **Crater Lakes**, and `Area_RedJungle_1/2` are **Red Jungle** and **Jungle Spires**. Which is why
   an index is resolved by `PublicExportHash` and not by package name: thirty-five assets share
   eighteen names.
3. **No Man's Land is a label, not a blank.** 43% of the raster is `Area_NoMansLand` — the outer
   coast and the ocean — and the game has an object for it with its own display name, so it is
   emitted. **287 of the 768 painted cells**, the largest region on the layer. Void is now the
   narrower thing: the game names no region *and* no known static object within 1 km. The land mask
   (2,688 objects) decides only that, never a name.
4. **The confidence letters are measurements.** `interior` = one area covers the whole cell;
   `boundary` = an exact boundary runs through it; `unnamed` = No Man's Land; `void` = no name.
   At 256 m: **197 interior / 284 boundary / 287 unnamed / 132 void**. `verified` is gone with the
   48-entry override table it described — those were nodes read off the wiki image by eye.
5. **Bboxes still come from the published raster**, so `bbox AND raster` holds by construction —
   the defect that made the shipped boxes disagree with their own grid for 11 of 21 regions.

Two grids because a majority downsample is lossy and how lossy was measured, on 5,054 known static
world objects looked up in the grid against the raster itself: 256 m mislabels **14.13%**, 128 m
8.33%, **64 m 5.30%**, 32 m 2.61%. 64 m costs 14,400 characters and is carried; 32 m costs 57,600
for twice the accuracy and was refused. The 256 m pair is what `/api/regions` serves, so the payload
and the frontend did not move.

The corners are **measured, not stated in the asset** — see the biome-raster section below — and
every run re-measures them and refuses to write if the pin stops holding (1.9692 against 1.333 on
build 495413).

What the change cost, measured against the retired trace before it was deleted: **454 of the 900
cells changed name**, 287 of them to No Man's Land. The largest single move is Spire Coast, where the
wiki drew one coastal ring across the whole north and the game draws a 1.6 km² strip, giving the rest
to Rocky Desert (40 cells), Desert Canyons (11), Dune Desert (10) and Swamp (1). Three names went:
**Western Beaches** and **Snaketree Forest**, which the game does not use, and **Eastern Dune Forest**,
which it does use — the asset exists and states that name — but puts no ground under.

That is also why the planner suite stopped planning over `region:Spire Coast`. Those reference plans
were hand-verified over the 51 nodes that selector used to return and it now returns 18, so
`tests/conftest.py` states the field as a bounding box instead: a region name is advisory by design
and a regression suite must not stand on one.

Also: 200 m single-linkage recovers the real oil fields, but one cluster merges 6 well satellites with a
standalone node 85 m away — so **node kind must never be inferred from one cluster member**.

### 7.2a Node lookup — one tool, three views

`search_resource_nodes` answers three different questions through its `show` parameter,
rather than splitting into separate tools that share 90% of their body:

| show | ranks by | answers |
|---|---|---|
| `fields` (default) | yield | "where is there a lot of iron" — 200 m clusters |
| `nodes` | yield | "which individual nodes", with ids reusable as selectors |
| `nearest` | **distance** | "what is closest" — requires `near` |

`near` takes a coordinate in metres, `me` for the player pawn, or **the name of a labelled
factory**. The last is the reason the view has this shape: "the nearest free coal to the
coal powerplant" is the question actually asked, and hand-copying a centroid out of another
tool's output is how the wrong coordinate gets used. Supplying `near` in any view adds a
distance column headed with the origin's name, so the number is never ambiguous.

`show="nearest"` without `near` is an **error**, not a silent fall back to yield order:
answering a different question than the one asked is worse than refusing.

### 7.2b Map deep links

`show_on_map(at=)` leads with a link to this project's own web map, on the host and port in
`config.web_url()` (`SATISFACTORY_WEB_PORT`, default 8712; frontend_vision.md §10.2). Below
it, it builds a satisfactory-calculator.com interactive-map link centred on
any place the shared resolver takes ([selectors.md](selectors.md)), plus one kind of its
own — `resource:<name>`, the centroid of every node of that resource — with the relevant
overlays switched on.

Fragment format, read off a working link the player supplied:

```
#4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;oilImpure;...
 ^zoom ^x    ^y     ^group    ^sublayers, semicolon-separated
```

**Coordinates are save centimetres.** Corroborated rather than stated by the site: the
supplied coordinate falls inside the measured content bbox (§7.1) and resolves to the
northern oil region, which is what its oil layers show. Every other tool quotes metres, so
the conversion lives in `maplink.map_url` and nowhere else; a metre value passed by mistake
lands 1/100th of the way across the map, near the origin, which looks plausible and is
wrong.

**Every layer token was read from the page, not inferred.** `WebFetch` gets **403** from
this host, but `curl` from the user's own machine returns the 1.5 MB page with the
identifiers in it. That distinction earned its keep — inferring from the single oil example
got **two of fourteen wrong**:

| guessed | actual | why the guess failed |
|---|---|---|
| `nitrogenWell*` | **`nitrogenGasWell*`** | the stem is the item name, not the resource word |
| `geyser` (bare) | **`geyser{Impure,Normal,Pure}`** | our node table gives geysers no purity; this map does |

Both would have opened the map at the right place with the overlay **silently missing** —
the failure mode hardest to notice, and the reason a guess was not good enough here.

Structure, all read from the page: nodes are `<stem><Purity>`; wells are
`<stem>Well<Purity>` and exist only for `oil`, `nitrogenGas` and `water`; nitrogen and
water are **well-only**, so a bare node token does not exist for them; oil is both.
Collectibles are single tokens — `greenSlugs`, `yellowSlugs`, `purpleSlugs`, `hardDrives`,
`mercerSpheres`, `somersloops`. There are 51 layer *groups*, of which `gameLayer` carries
the resource markers.

### 7.2c A miner is never valid on a liquid node

`search_resource_nodes` reported **every oil node at double its real rate** — a pure node
read 480 m³/min where an Oil Extractor gives 240.

`node_rate` picks the best extractor for the node's kind, filtered by `mAllowedResources`.
That field is only populated when `mOnlyAllowCertainResources` is **True**, which is
`False` on every miner — so miners looked unrestricted, and Miner Mk.3 (base 240) out-bid
the Oil Extractor (base 120) on crude.

`mAllowedResourceForms` is the field that actually encodes it, and it was **already parsed
and simply never consulted**:

| building | `mAllowedResourceForms` | `mOnlyAllowCertainResources` |
|---|---|---|
| Miner Mk1/2/3 | `RF_SOLID` | False → `mAllowedResources` empty |
| Oil / Water Extractor | `RF_LIQUID` | True |
| Resource Well Extractor | `RF_LIQUID, RF_GAS` | True |

Corrected: oil reads 60/120/240 by purity, confirmed **three independent ways**.

1. Our building model, `extract_rate(purity, clock)`.
2. The dump's own cycle fields — `mItemsPerCycle / mExtractCycleTime × 60`, litres to m³
   for fluids — which reproduces every parsed `base_extract_rate` exactly, including the
   Oil Extractor's 2000 L/s → 120 m³/min.
3. The wiki's Crude Oil "Resource acquisition" table (supplied by the user; the site is
   behind a Cloudflare 403 to automated fetches):

| node purity | m³/min at 100% | m³/min at 250% |
|---|---|---|
| Impure | 60 | 150 |
| Normal | 120 | 300 |
| Pure | 240 | 600 |

All three agree cell-for-cell, and `node_rate` now joins them; before the fix it
disagreed with all three by exactly 2×. Tests pin both the derivation and the published
table, including the **250% column** — that is the figure a plan is actually built
against, and a pure node overclocked is 600 m³/min, not 1,200.

**Only the node search was affected.** `extractor_processes` builds its columns from
`building.extract_rate(purity, clock)` with the actual building, so the LP and
`plan_layout` were always right — which is how the discrepancy was spotted: the same world
read 480 in one tool and 240 in the other.

### 7.2d The point inspector

A right-click on the map asks `/api/inspect`, which calls `place.describe`, the function
`describe_location` calls. One answer, several parts, each labelled for what it is: the
region with its confidence; the terrain reading and the sampled ground and built
elevations within `radius_m` (200 m by default); the grid cell and direction from the map
centre; the belts and pipes whose drawn lines pass within `radius_m`; the five nearest
nodes, each with its `spoiler` flag; up to three per-resource fields with a member within
500 m; and up to five remaining pickups within 500 m, pedestals left out. With no readable
save the node table still answers the geography, the nodes and the fields; `conduits` is
null and `pickups` empty, and `save_error` says why. `stale` carries the node and
collectible tables' ages when either is behind the save.

### 7.3 Source selectors

**Decision: one selector language, used by every spatial and planning tool.** `plan_factory` takes no
`direction` parameter; it takes `sources`, a list of selectors that say which nodes may feed the plan.
Implemented in `spatial/select.py`. The terms themselves are tabulated once, next to the machine
selectors and the place grammar they share a spelling with, in [selectors.md](selectors.md).

**Locations union; filters intersect.** So `["north", "resource:Crude Oil"]` is "crude oil in the
northern half", and `["region:Spire Coast", "near:120,-2020@1500"]` is the union of two areas.

Two rules that matter more than the syntax:

- **A failed location selector returns nothing, not the whole map.** A typo'd region name that quietly
  widened the scope to the entire map would answer a completely different question and produce a
  confidently wrong plan. Filters with no location *are* a legitimate whole-map query, so the two cases
  are tracked separately (`location_attempted` vs `location_seen`).
- **Every unmatched selector is reported** in the response's notes, never silently dropped.

Underlying primitives, all exact: `grid_cell(x, y)`; `distance_m` (XY only — Z spans just 0.64 km and
matters for pipe head, not proximity); `in_direction(dir, origin, half_angle)`; `cluster(nodes, 200 m)`
with fields named by *content* rather than biome.

The earlier plan of unioning a cone with a curated `NORTH_REGIONS` set is dropped. It existed to patch a
too-narrow cone, and it silently dropped the best-purity northern field because that field's biome wasn't
in the hand-written list. Named regions are now first-class selectors instead, so no curated direction
list is needed.

> **Aggregates must be scoped.** "1,740 m³/min free in the north" is true and useless: free capacity
> within 1,000 m of the user's generator farm is **0**, and the free nodes are 1,515–2,215 m away across
> two separate fields. Always report free capacity per cluster with a distance, never as a bare total.

Altitude has a **sign** that matters: a field 268 m above the refineries feeds them without pumps.
Report `dz` relative to the consumer, not absolute altitude.

---

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
province by renumbering. See [§20 of `parked.md`](parked.md) for what v3 did and did not fix.

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

Cutting 5,461 tiles at `optimize=True` is minutes of one core doing nothing but deflate. The
**resampling stays serial** — level z is one Lanczos downscale of the whole sheet, in the
parent, exactly as before — and only the per-tile encode is spread over processes, with the
level published once into a `shared_memory` block every worker maps and one task per row of
tiles. Because no worker resamples anything, no worker can disagree about a filter tap at a
strip boundary, which is why this is byte-identical rather than merely equivalent.

`--check-parallel` proves it rather than asserting it: one level cut both ways, SHA-256 of
every tile compared name for name. On the reference machine, z5 of the artwork sheet, 1,024
tiles: **5.43 s serial against 1.34 s on 16 workers, 4.05×, byte_identical true**, recorded
in the sidecar.

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

### Measured on the reference machine

| | terrain | satellite |
|---|---|---|
| draw 16384² | 65 s | 74 s |
| cut both trees, 16 workers | 46 s | 42 s |
| `tiles/` z0..z6 | 5,461 tiles, 237.5 MB | 5,461 tiles, 229.2 MB |
| `tiles@2x/` z0..z5 | 1,365 tiles, 237.2 MB | 1,365 tiles, 228.6 MB |

227 s for both layers end to end, against 91 s for both at 8192² with no @2x tree and a
serial cutter. Banded at 256 rows with an **8**-row halo — up from 4, because the cubic
stencil reaches two texels either side instead of one and the water blur's three sigma is
five pixels at this resolution rather than two — so no pixel is computed from a one-sided
gradient or a truncated kernel.

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

Measured when this first shipped at 8192²: 12 s to draw terrain and 15 s satellite, 32 s each
to cut, 91 s for both layers end to end, 60.1 and 60.2 MB of PNG per pyramid over 1,365
tiles. The 16384² numbers that replaced them are in the table above.

## 18. One base map at a time, and the page says which (2026-07-31)

> Since 2026-10-05 the modes are the map types of the registry rather than these four names,
> `mode=` holds a type id (`artwork` still reads as `map`), and a shared default chosen in
> Settings can open a render. [maps_contract.md](maps_contract.md) §6.3 has the current rules;
> the rest of this section stands.

Three pyramids on the server were three pictures the page could not ask for: the client
addressed the artwork alias and nothing else. What it now has is the Google Earth split —
**modes**, which are one question with one answer, kept apart from **overlays**, which are
thirty-five independent yes/nos.

**The four modes are `artwork`, `terrain`, `satellite`, `plain`**, as radios in their own
section at the top of the layer control, above a rule and above every checkbox. `plain` is a
real mode rather than the absence of one: it is no base imagery, which is the shipped state,
what a fresh clone with no generated renders looks like, and — since it is the mode the biome
tint is designed for — a thing a reader may prefer.

**A mode is one `L.TileLayer` and a mode switch swaps it.** Nothing else moves: not the CRS,
not the three panes, not one data overlay, not the region blend's rule. That is the serving
design of §17 arriving on the client exactly as intended — same frame, same tile size, same
grid, so the client changes one path segment. The one per-mode difference that survives is
depth: `maxNativeZoom` comes from that layer's own `X-Map-Tile-Max-Z`, so the renders stop at
z6 (z5 when the client is fetching @2x tiles, which are one level shallower) and Leaflet
upscales past it while the artwork runs to its own z7 if it was `--enhance`d.

**Measured, at 1600×1000 on the reference world:** the frame is unmoved by a switch — map
rect `[0, 40, 1600, 960]`, pane transform `translate3d(0,0,0)`, scale bar `500 m`, `z=-3
c=0,0` — identical across all four; and the network log for a switch contains that mode's
tiles and no other layer's.

### The rule that replaced the auto-untick

A render arriving used to *untick* the region box, once, as an event. With four modes that
stops being expressible: "arriving" now happens on every switch, so the same heuristic would
throw away a choice the reader had made in between. So it is stated as a rule about states:
**the region tint defaults OFF under any imagery mode and ON under plain** — which is the map
the old heuristic left you on, said as a rule — **and a reader's own tick of that box wins for
the rest of the session.** The default is what the page does when it has not been told, not
what it does instead of being told.

Programmatic ticks are told from real ones by a flag, because they cannot be told apart
afterwards: Leaflet fires `overlayadd` from the layer's own `add` event, so `map.addLayer` and
a click arrive identically.

### Resolution order, and what a link pins

`#world=…&save=…&mode=…&z=…&c=…` — subject, then picture, then viewport. An absent or unknown
`mode` resolves **artwork → plain**, and so does a mode this machine has never generated: a
link to someone else's terrain render should land on a map rather than on an error. Terrain
and satellite are never chosen *for* you even when they are the only pictures on disk, because
they are interpretations of this world rather than the map of it and the page should not have
an opinion about which. The fragment omits `mode` entirely until the probes have answered, so
a pan in the first fifty milliseconds cannot pin a mode nobody chose.

### A mode that is not there stays on screen

An ungenerated pyramid greys its row out rather than removing it, with the generator named in
the row's `title` — the same sentence `/api/maptiles`' GET 404 carries, repeated in the client
because the page probes with **HEAD** and HEAD answers 204 with no body on purpose. Asking for
the message would mean asking for the error the server went out of its way not to raise. A
pyramid whose tiles turn out not to *draw* is treated as the same thing plus a toast: the mode
greys out with the reason in place of the generator, and the page falls back to `plain` rather
than to another render, because silently substituting a different picture of the same world is
the one answer that could be mistaken for success.

The artwork's single-image `/api/mapimage` fallback survives as a detail of the artwork mode
rather than a stage of a loader: probed only when its pyramid does not answer, and drawn as the
`imageOverlay` it always was.

### The mode radios have a sibling: the floor picker

The same split, one level in. "Which storey of this factory am I looking at" is one question
with one answer, so it is radios in its own folded section above the modes, drawn by
`layercontrol.ts` and decided by `floors.ts` through `onFloorPick` — exactly the seam
`onModePick` is, for exactly the reason. It differs where the rows differ: a mode is a word and
a floor is a word plus a measurement, and a mezzanine has to read as subordinate to the storey
it is a ledge on. The section exists only while the page is slicing something, and
`floor=<platform>/<band>` joins the fragment between `save` and `mode` — subject, then how much
of the subject, then picture, then viewport. See [§16b](parked.md#16b-built-floor-wise-factory-view-2026-07-31--only-think-about-that-idea)
for what a floor is and how it is recovered.

---

## 19. The water channel, rebuilt out of the game's own water (2026-07-30)

The heightmap's fifth stage used to be a flatness detector over the 2048 px interface raster.
It found **20.4%** of the sheet as water against the artwork's 39.0% — 46.7% recall overall,
**35.8% over Spire Coast** — and invented plateau lakes on flat mesas. The failures were
structural rather than tunable: the raster's quantisation step is 3.9 m and the water it was
asked to find is 2.1 m deep, a river is a metre of water in a groove it cannot resolve at all,
and over the fill province the "terrain" the detector compared against **is** the water
surface, so the depth it needed was identically zero.

**Two sources, each asked only what it knows.** The four `SlicedMap` BC1 slices are the game's
own drawing of its own world and its water is drawn blue, so `B - R >= 25` gives the plan
shape: bimodal with nothing between the modes, 3 of the 626 static resource nodes called water
(0.48%), registration measured at exactly (0, 0) sheet pixels by a ±2 px sweep. The 849 cooked
water actors give the level: **837 carry a world AABB** — `BoxComponent.BoxExtent` (130),
`BrushBodySetup.AggGeom` (270), `InstancedStaticMeshComponent.CachedBounds` (222) and, for the
plane-backed blueprints whose cooked instance names no mesh, `WaterPlane`'s own
`ExtendedBounds` (215) — each taken to world space through the composed `AttachParent` chain
eight corners at a time, because 486 of them are rotated. **A box's top is the surface**: the
save's 23 water extractors all sit inside a volume and stand on its box top to within
**0.005 cm**.

The channel goes from **9.589 km² to 18.248 km²**, against the artwork's 18.288. Spire Coast
recall goes from 35.8% to **99.85%**, the invented plateau lakes are gone, and the ocean sits
at −16.994 m, which is where all 31 ocean-spline boxes put it.

### The level is per texel, not per body

The literal recipe was one median per drawn body. It was rejected by measurement: the ocean
and every river running into it are **one connected shape** in the artwork, spanning 141 m of
box top, and a single median over that invents up to **157 m of depth** across 0.06 km². The
level is therefore the *highest* box top standing over each texel — a box top is a surface, so
where several overlap in plan the highest is the one visible from above. That is not rough:
0.017% of neighbouring wet texel pairs step past 0.5 m, and those are river mouths, where a
step is what is really there. The body median survives as the fallback for the **17 texels**
of drawn water no box stands over at all.

### `waterq.u8.z`, and the one arithmetic nothing may do

A level and a **depth** are different claims. The level comes from a box and is good to
centimetres wherever there is water; the depth is that level minus the ground, and over the
fill layer the ground is a 3.9 m raster that routinely rounds *above* a sea surface 17 m down.
So the field gained a fourth raster — `0` dry, `1` water with a depth measured against 1 m
terrain, `2` water whose level is known and whose depth is not — and 7.39 km² of the 18.25
takes value 2.

**Nothing may decide submersion with `water_m > z_m` any more.** That test reads the open
ocean as dry, and it is what `Reading.submerged` used to do; it now asks the quality byte and
falls back to the comparison only for a field written before that byte existed. `Reading.
water_depth_m` is `None` where the depth is unknown rather than `max(…, 0)`, and the inspector
prints the reason instead of a plausible `0 m deep`.

The same rule binds `tools/gen_map_renders.py`, which reads the rasters directly, and
**2026-07-31 it does**: submersion is the coverage of `waterq.u8.z != 0` sampled onto the
output grid, the depth feather applies only to the share whose depth was measured, and a
level-only texel is drawn at full alpha. Measured on the shipped field, the comparison it
replaced read **3.572 km² of ocean out of 18.248 as dry** — that is what used to be missing
from both renders.

Level-only water is also tinted at the **deep** end of the ramp rather than the shallow one,
and that is a measurement rather than a preference: 95.2% of it stands over the fill province
and 98% of its surface levels lie in a 0.7 m band around the ocean's own −16.99 m. It is the
ocean. Running the ramp on `water_m − z_m` there would paint it the pale green of an
ankle-deep sheet, because the number being subtracted is a 3.9 m raster's rounding error.

One thing the channel still cannot fix: beyond the landscape's own extent the field has no
height *and* no water volume, so those texels stay the page's `--sea`. Against bright water
that edge is visible in the corners of the frame. It is the render saying nothing, which is
the intended behaviour, and filling it would mean inventing ocean.

### Four gates, each aimed at a specific silent failure

Dry-node false positives over 1% mean the colour classifier drifted or the sheet moved.
Spire Coast recall under 95%, measured against the artwork over the `Spire Coast` cells of
`data/region_names.json` — the game's own map areas, so still not this pipeline marking its own
homework, and a sharper stencil than the wiki trace it replaced — means the region that exposed
the old detector is being missed again. That stencil is a good deal smaller than it was, so the
0.48% / 99.85% / 0.000 m / 0.0001% below are the last run's numbers over the old one and the next
run re-measures them. An ocean
level more than 0.5 m from the median ocean-spline box top means the level is coming from the
wrong volumes. And artwork water standing over no box at all, past 1%, means the mask and the
volumes have stopped describing the same world, which is what a misregistration looks like
from here. The run refuses to write if any of them fails. On build 495413 they measure
0.48%, 99.85%, 0.000 m and 0.0001%.

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
a memory-mapped scratch file so both layers draw from one rasterisation.

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

Neither regime helps the third province. `fill` is the interface raster — 3.66 m cells
quantised to 3.9 m in Z — so it draws the ocean shelf and the map's edge as terraces: flat
plateaus with blocky outlines that no kernel can un-terrace, because those steps are real in
the data and are not in the world. They are low-passed at the raster's own cell size,
normalised over the province so no landscape measurement is dragged into a 3.9 m answer,
faded by its own weight so the province boundary is not itself drawn, and **clamped to one
quantisation step** — an artifact is at most one step tall, so a larger correction is not
de-terracing, it is a blur erasing the 300 m scarp at the map's edge by half of itself. It
fires on 13.96% of the field and clamps on 2.3% of the province.

The design called for marching squares over each terrace and a spline along the polyline.
Smoothing every level's indicator with one kernel and summing them is, by the linearity of a
convolution, **the same array** as smoothing the level field itself — so the two are one
operation, and the one that is here is the one that does not need polylines extracted from
56 million texels.

**Superseded by section 26.** The 3.9 m step assumes an 8-bit raster. The raster is float16,
with steps of 0.24–0.49 m, so this removed a quantisation that does not exist; the blocks
on screen were nearest-neighbour 3.66 m cells. Recipe 5 re-reads the raster instead.

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

## 21. The side panel: factory health and power circuits (2026-09-26)

Two tabs over the map, fed by two routes that call the same domain code the MCP tools do.

**Factories** is `/api/factories/health`: `factory_health`'s sweep over every named factory,
one row each — `assess` for the states and the worst machines, `build_view` for the measured
MW, `LabelStore.review` for a label whose anchors have shrunk or gone. Sorted by how many
machines sit in an actionable state (`health.ACTIONABLE`: dead node, no recipe, blocked,
starved, stalled), then by uptime. The map marks the same set on the machines themselves: a
thick outline for every actionable machine, red and hollow when stopped, yellow and solid when
blocked (docs/save-projection.md §6.2d). The panel's header names the same set, read from
`actionable_states`.
A row flies to the factory's box and outlines it; a machine under it flies to that machine.
Clicking a factory label on the map selects its row.

**Power** is `/api/power/circuits`: `power_report`'s world ledger at the top, then one row per
**circuit**, where a circuit is a connected component of the save's power edges and its
figures are `PowerLedger` run over only the records standing on it. Nothing new is computed;
the split is the only addition, and it has three limits worth knowing before trusting it:

- **Switches join.** The save carries no `mCircuitID` and no switch state, so an open power
  switch or priority switch still joins the two sides here. Two circuits the game keeps apart
  may read as one. This errs the same way `health._lit` does.
- **Batteries are not in the ledger.** `PowerLedger` counts generators and consumers; a
  circuit running on Power Storage reads as generation 0 with draw, which is also what a
  circuit with no source looks like.
- **Unwired records are in no ledger.** A machine or generator on no power edge draws from and
  feeds nothing, so `power_report` leaves it out of both sides and the circuit rows sum to the
  world line. `off_grid` carries its count and rated MW. One rule decides both the count and
  the list: the wire is tested before anything else, so a paused machine on no wire is counted
  (at no MW, and in `off_grid.paused`) and listed. A circuit's `consumers` counts its paused
  machines the same way, and its ledger says how many in `paused`. The machines are listed on their own,
  beside the machines wired to a circuit no generator stands on — `assess`'s two lists over
  every machine in the world — and generators on no wire have a third list.
- **Two generator classes need help from the save side.** A standing Biomass Burner is
  `Build_GeneratorBiomass_C` in the save and `Build_GeneratorBiomass_Automated_C` in the dump,
  joined by `BUILDING_CLASS_ALIASES`. The HUB's built-in burner has no entry at all, so it is
  listed in `unmodellable`, on the world and on its circuit, and its output is not counted.

**Around the panel (2026-09-27).** Popups and flights keep clear of the overlays: every
auto-pan and every fly-to is padded by what the side panel, the layer control and the trace
card cover (`overlayPad` in `map.ts`), and the whole-world view fits the map sheet into what
is left. Below 700 px the panel is a bottom sheet, the panel and the layer control start
folded, and opening one of the panel, the layer control or a trace folds the others.

- **`show=label:<name>`** in the fragment selects that factory once factory health has loaded:
  the panel row, the outline, the flight and the machines, belts and pipes layers.
  `show_on_map` writes it when its place is a factory label.
- **Layer ticks persist** per browser, except the pickup rows (the fragment owns those) and
  the region tint (the base-map mode decides it).
- **Machine rows name their factory.** `/api/machines` sends `factory`, the label whose
  anchors hold the machine, so a machine popup can link to that factory's dashboard page.
- **The marker key** lives at the bottom of the layer control and folds with it.

## 22. Terrain z: three surfaces, a mapped cache, and the site's height (2026-10-05)

The game ships its terrain in the cooked `LandscapeComponent`s: 2289 components of 128x128
uint16 samples, 1 m apart, 7.8 mm vertical. `tools/gen_world_heightmap.py` has always fused
them with the rock meshes and the 2048 px interface raster into `height.i16.z`. Generator v4
writes two more planes beside it and changes nothing in the existing five.

| Surface | File | What it is |
| --- | --- | --- |
| `ground` | `height.i16.z` | The fused field: landscape, rock max-Z, fill. The default everywhere. |
| `terrain` | `terrain.u16.z` | The bare sculpted landscape, raw uint16 on its own grid (`meta.json` `terrain_grid`), 0 = hole. Rocks and cliffs are meshes and are not in it. |
| `top` | `top.i16.z` | `ground` max-folded with the 1,076 arch placements and 41,351 foliage boulders, from their collision trimeshes. |

**Ground stays the default** because it wins on every independent ground-truth set; `terrain`
alone puts a factory under a mesa, and folding arches into `ground` puts it on a roof.

### Reading a point

`Field.z(x_cm, y_cm, surface=..., hint_z_cm=None)` reads bilinear over the four vertices
around the point. Two guards keep a blend from inventing ground: a no-data vertex, or four
vertices spanning more than 2 m (a cliff edge), hand the answer to the heaviest valid vertex.
On landscape texels the ground reading is replaced by the terrain plane's value when the two
agree within 0.1 m, which is the decimetre rounding of the same sample.

The `terrain` plane is read differently: as the engine's own surface. UE Landscape renders
and collides each 1 m quad as two flat triangles split on the diagonal from vertex
(r, c) to (r+1, c+1); with `tx`, `ty` the position inside the quad, `tx >= ty` reads
triangle (r,c)-(r,c+1)-(r+1,c+1) and the rest (r,c)-(r+1,c)-(r+1,c+1). The landscape is one
continuous heightfield, so the 2 m step guard does not apply to it: a steep quad is a steep
triangle in game. Measured on 130 power poles standing on bare landscape, the triangles
match the saved z to a median under 0.1 mm (81.5% within 1 mm, the reading's own rounding);
bilinear 0.6 mm, the other diagonal 1.3 mm, bicubic 1.3 mm. Over all 2,910 landscape truth
points the gain is under 1 mm, because the remaining ~1.7 cm is where objects keep their
pivot, not terrain. The cooked collision is the same 1 m grid at mip 0 (2,289 components,
`CollisionSizeQuads` 127, scale 1.0), and no Nanite, virtual-texture or displacement data
exists, so nothing finer than 1 m horizontal and 7.8 mm vertical is in the game files.

The rock and top rasters are sampled at the vertex, where the reader puts every value.
Generator v4 sampled them at the texel centre, half a metre east and south of where they
were read. On the save truth, v5 moves the bilinear `ground` median from 0.088 m to 0.052 m
on cliff (prov 4) and from 0.161 m to 0.097 m on cliff direct (prov 5); landscape is unchanged
and the roof tail (p90 about 50-90 m) does not move, because that is which surface, not where.

Every reading carries `terrain_z_m` (the bare landscape under the point) and `ambiguous`:
ground more than 2 m above terrain, or rock over a landscape hole, so the answer may be a
rock top or a roof. A field without a terrain plane calls every cliff texel ambiguous.

A **hint** picks the surface: among the surfaces with data, the one nearest the hint,
preferring one at or below hint + 2 m, because a thing rests on a surface below it.

### The cache

Each plane decodes once into `cache/<plane>.npy` beside the `.z` files and is memory-mapped
after. The stamp beside it holds the source's size, mtime, sha256 and the generator version;
a changed mtime with an unchanged sha256 still maps. Any failure to write decodes in memory.
The int16 decoder sums in int16, which is exact under wraparound and drops the decode peak
from 1.24 GB to 227 MB.

### The site's z

Both servers install `siting.TerrainGround` as P5's ground-height provider
(docs/planner-p5_contract.md §6) when they start. Through it:

1. A z typed as `x,y,z`, or the player's own z for `me`, wins.
2. A pad dragged on the page arrives with a null z, and `check` fills it with the pad's
   median on `ground`.
3. Chat's `site_plan` and `site_at` go through `siting.settle_z`, which reads the same
   installed field and can pick another surface by hint: the median z of what already stands
   on the pad. The reply carries the reading in words: surface, pad min and max, bare
   terrain, ambiguous share, provenance and its accuracy, coarse, water.
4. No data under the pad, or no field on the machine: `z = None`, and chat says why. Save
   objects are never interpolated into a terrain answer; that measured 100x worse.

The stored record stays `check`'s canonical one: the reading is for the reply, not the plan
file. A stored z is kept when a pad is resized in place; a moved pad is read again.

### Measured (build 502094, 5,956 save ground-truth points)

| Lookup | All: median | All: p95 | Within 1 m | Landscape: median |
| --- | --- | --- | --- | --- |
| nearest vertex (`at`, before) | 0.084 m | 83.3 m | 81.7% | 0.058 m |
| bilinear `ground` with refine | 0.055 m | 83.2 m | 81.9% | 0.024 m |
| `terrain` alone | 0.119 m | 122.5 m | 61.9% | 0.024 m |
| hint = truth z (oracle) | 0.045 m | 43.3 m | 88.5% | 0.024 m |
| bilinear `ground`, generator v5 | 0.041 m | 83.0 m | 82.1% | 0.024 m |

The tail is which surface is meant, never resolution. No one- or two-valued plane can hold
a cave floor; section 23 flags where one is missing, and section 24 reads every surface on rock
off the rocks' own collision meshes.


## 23. The cave flag (2026-10-05)

The field holds one ground per (x, y), so a point in a cave reads the surface above it: the
218 save points more than 3 m under every surface (22 caves, two of them hold 140) are off
by a median of 91 m. The cave flag does not fix that. It makes sure no answer presents the
surface as a cave point's height.

### The masks

`tools/gen_world_heightmap.py --caves` sweeps the world once (6 s on build 502094) and writes
`data/local/caves/`: `caves.npz` (330 kB) and `meta.json`. It is its own directory, so a field
can be regenerated or swapped without it. It reads the field at `--field` (default
`data/local/heightmap/`), refuses to replace an existing directory without `--force`, and is
never committed.

| Signal | What it answers | Stored as |
| --- | --- | --- |
| Cave sound volumes: `FGAmbientVolume` whose `mAmbientSettings` names a cave, 207 actors, 804 brush convex hulls | 3D: is this point inside a cave volume | hull planes, per-hull slices, boxes |
| Cave decoration: foliage under `/Caves/` and `/CaveFloor/` (not `SM_NonCave_*`), 245,833 instances more than 3 m under the ground | 2D: does a cave lie under this (x, y) | bit 1 of an 8 m cell mask, buffered 2 cells (16 m) |

Bit 2 of the mask is every cell a hull's plan touches, so a point read runs the 3D test only
there. 2.37 km² of the map is flagged.

### Reading

`Reading.cave` is `none`, `below` or `inside`, beside `ambiguous` and never folded into it: a
rock shelf is ambiguous, a cave is not a shelf.

| Case | `cave` |
| --- | --- |
| No flagged cell | `none` |
| Flagged cell, no hint, or a hint at the surface | `below` |
| Hint inside a hull, or over a flagged cell more than 3 m under every surface | `inside` |

Without a hint a read is never `inside`. `Area.cave_pct` is the share of a pad over flagged
cells. The masks load lazily on the first cave question and are re-read when `meta.json`
changes (checked at most once a second). No masks means `none` everywhere.

### Where it shows

One line, from `caves.note`. Under `inside` the surface is named as the surface, never as the
answer: "in a cave: ground height unknown here (the surface above is 233 m)". Under `below`:
"a cave lies under this point: the height given is the surface, not the cave floor". No
ceiling is ever printed; nothing measured supports one.

- `describe_location`: a `cave=` field. With `at=me` the player's z is the hint, and under
  `inside` `terrain_m=unknown`.
- `whereami`: a `cave=` field, the player's z as hint.
- The inspector: `terrain_cave` and `terrain_cave_note`. A map click has no z, so it shows
  `below` at most.
- Siting (`settle_z`, `site_plan`): the hint is the typed or player z, else the median z of
  what stands on the pad. `inside` gives `z = None` with the note as the reason. Otherwise
  `cave_pct` rides along and the terrain line says how much of the pad has a cave under it.
  The page's site preview shows the same share.

### Measured (build 502094, save ground truth)

On 218 cave points (more than 3 m under every surface) and 4,885 open-ground points (within
1 m of the ground), field `data/local/heightmap/`:

| | Cave points flagged | Open ground flagged |
| --- | --- | --- |
| With the true z as hint, `inside` | 95.0% (207) | 0.1% (5) |
| No hint, `below` | 95.0% | 9.4% |
| Sound volumes alone, 3D | 81.7% | 0.1% |
| Decoration alone, 2D | 85.3% | 7.0% |

The 9.4% of open ground flagged `below` mostly stands over a real cave; the wording says so
rather than calling it a fault. Eleven cave points are missed, six of them mushrooms
(`BP_Shroom_01_C`). The measuring scripts are kept out of the repository with the truth set.
Ceilings and the underground map are not part of this; they wait for cave building to be real.
Section 24 adds cave floors where a hint exists.


## 24. Rock heights from the collision surface (2026-10-05)

Two surfaces, two jobs. The map draws the visible rock (the Nanite surface, `gen_map_renders`).
Every programmatic height reads the **collision** surface, the one the player and the build gun
stand on: the siting z, `describe_location`, `whereami`, the inspector. The two differ by more
than 0.5 m on 12.5% of rock-top, and by more than that at cliff edges, where a 1 m raster of
either smears.

### The collision pack

`tools/gen_world_heightmap.py --rocks` adds `rocks.npz` and `rocks.json` to the field at
`--field` (default `data/local/heightmap/`) and touches nothing else there; a full run writes
them with the planes. It refuses a field cut from another build, and an existing pack without
`--force`. On build 502094: 24 s, 15.4 MB.

| What | Count | Collision taken from |
| --- | --- | --- |
| Rock meshes with `CTF_UseComplexAsSimple` | 110 | the cooked Chaos trimesh: render LOD `LODForCollision` (1-3), triangle for triangle on every one |
| Rock meshes without | 21 | `AggGeom` simple elements: convex hulls, boxes, spheres and capsules, as closed hulls wound outward; elements with `NoCollision` are skipped |
| `BP_CaveFloor_C` spline components | 173 (61 actors) | each component's own cooked trimesh, already bent along its spline |

Placements follow the cliff pass: the same sweep, the same owner, name, bounds and oversize
culls. What the cliff pass drops is kept under a kind instead: `rock` (19,857, the `ground`
set), `rock, simple collision` (575), `arch` (1,076), `foliage boulder` (41,351), `cave floor`
(173). Two meshes ship no collision at all (`ArcMerge1`, `SM_Rubble_Small_01`, 12 placements).
Each mesh is stored once in mesh-local cm with its winding (+1 outward, -1 inward, 0 an open
shell); each placement as a row-vector matrix, an origin, a kind and a world box. The sidecar
records the counts.

### The index

`rocks.RockIndex` cuts the pack into 64 m world tiles the first time a point in one is asked
about: the placements whose box overlaps, transformed to world, kept per triangle where the
triangle's plan overlaps the tile, flagged up-facing from the winding and the placement's
handedness (an open shell counts both ways), and binned into 1 m cells. Tiles live in an LRU
of 64 MB, about 35 dense cliff tiles. `hits(x, y)` intersects the vertical line with the cell's
triangles and returns every surface, highest first; two hits within 1 cm of one kind are one.

### Reading a point

The planes keep every job they had; the pack only answers on rock.

- **Landscape** (no cliff vertex in the point's quad, no hint, `ground`): the fast path of
  section 22, untouched.
- **Rock, `top`, or any hint:** `Field.collision` reads the hits. `ground` is the highest of the
  landscape (on the engine's triangles) and the up-facing `rock` hits; `top` the highest of
  every up-facing hit; every other up-facing hit is a `floor`. Without a hint the answer is
  `ground`, and `ambiguous` keeps its meaning: more than 2 m over the landscape.
- A **hint** picks with `Surfaces.pick` as before, over the floors too, so a shelf, a ledge
  under an overhang or a cave floor can answer. `Reading.surface` is then `floor`.
- No pack, a pack from another build, or no hit and no landscape: the planes answer as before.

**Caves.** The flag of section 23 is computed exactly as before, on the planes. Under `inside`,
the hint's pick is kept only when it is a collision surface no more than 3 m under the hint and
no more than 2 m above it; the reading is then `cave_floor` and its line is
`in a cave: floor -9.2 m, the rock collision just under the given height`. Otherwise the height
stays unknown. No ceiling is printed. Pads keep their 1 m statistics; only the point reader
and the siting point z changed.

### Measured (build 502094, 5,956 save ground-truth points)

Median absolute error and share within 1 m, before (v5 planes) and after (planes plus pack).
Rock is a point with a cliff vertex in its quad; "on top" within 2 m of either answer; an edge
has more than 2 m of collision relief within 0.5 m; a cave point lies more than 3 m under
every plane.

| Population | n | No hint | Hint = truth | Hint = truth + 1.5 m |
| --- | --- | --- | --- | --- |
| All | 5,956 | 0.041 → 0.047 m, 82.1 → 82.0% | 0.033 → 0.032 m, 88.5 → 96.8% | 88.5 → 96.6% |
| Landscape | 3,204 | 0.023 → 0.023 m, 95.1 → 95.1% | 95.1 → 98.0% | 95.1 → 97.9% |
| Rock | 2,752 | 0.084 → 0.105 m, 66.9 → 66.9% | 0.050 → 0.044 m, 80.9 → 95.3% | 80.8 → 95.1% |
| Rock top, open | 1,796 | 0.035 → 0.048 m, 99.2 → 99.2% | 99.2 → 99.2% | 99.2 → 99.2% |
| Rock top, edge | 76 | 0.100 → 0.052 m, 77.6 → 77.6% | 0.059 → 0.034 m, 86.8 → 100% | 86.8 → 98.7% |
| Cave | 219 | 91 m either way | 76 m → 0.092 m, 0 → 87.2% | 0 → 85.4% |

- **Hints are where the pack pays.** The p90 with a hint falls from 2.8 m to 0.26 m; the tail of
  section 22 was which surface, and the floors hold the missing ones.
- **Caves:** with the true z as hint, 87% of cave points get a floor and 84% are within 1 m;
  3.7% get a floor more than 1 m off (mostly mushrooms, which stand on foliage the pack does
  not hold). The 63 points inside a sound volume but less than 3 m under the planes (cave
  mouths) all get a floor within 1 m, against 32% before.
- **The open rock-top median rises by 13 mm.** Most truth objects are placed by designers on the
  visible surface, and collision is LOD 1-3 of it. Physics-settled pickups should rest on
  collision, but on the 8 of them where the two surfaces differ by more than 0.2 m, 7 sit within
  5 cm of the visible surface (median 1.8 cm, collision 23 cm). Creature drops go the other way
  (5 of 6 closer to collision). Too few to overturn the decision; worth an in-game check.

Timings on the reference machine, `Field.z` end to end: landscape 27 µs median (unchanged);
rock warm 62 µs median, 75 µs p95 (24 µs before); `hits` alone 26 µs. A tile's first touch
costs 22 ms median, 37 ms p95 in a dense cliff area (1.9 MB a tile), 9 ms median over scattered
points. Loading the pack takes 0.1 s and 25 MB.

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

### Cost

A full run of both layers took 30.5 min wall time on 2026-10-05: the rock pass about
14 min, the arch-and-boulder pass 2 min, then about 6 min to draw and 2 min to cut each
layer. It wrote 1,656 MB of tiles (terrain 843 MB, satellite 812 MB), against 1,943 MB for
recipe 3: smoother ground compresses better.

### Output and switching

`--renders-name renders-v2` writes to `data/local/renders-v2/<layer>/` and leaves
`data/local/renders/` alone. The server reads only `data/local/renders/`, so switching is a
rename while the server is stopped: move `renders` to `renders-v1`, then `renders-v2` to
`renders`. The tile URLs carry a build tag derived from the sidecar, so browsers fetch the new
tiles without a cache flush.

## 26. Rebuilt base data and a PCHIP sampler: recipe 5 (2026-10-05)

Recipe 4 drew its gentle ground from `terrain.u16.z`. Recipe 5 rebuilds the rest of the
lattice the kernel reads, and swaps the kernel. Rocks, arches and boulders are drawn exactly as
in section 25. Build 502094.

### What changed

`terrain/fill.py` (then `tools/map_fill.py`) builds the lattice once per run, in about 13 s, before any band is drawn:

| Texels | Source | Share of the field |
| --- | --- | --- |
| Landscape | `terrain.u16.z`, unchanged | 45.46% |
| Cliff province | the field's own heights, copied unchanged | 21.06% |
| Fill | the float16 interface raster: Gaussian with sigma 1 texel, then cubic, then +1.0 m | 13.64% |
| Fill within 48 m of the landscape | as above, plus the landscape's residual carried in by a harmonic solve and a cosine taper | 0.32% |
| Interior holes | biharmonic fill, or harmonic where the biharmonic leaves its border's range by more than 2 m | 0.16% (26 holes, 11 harmonic) |
| No data out to the field's edge | left empty: the page's sea colour | 19.37% |

- **The de-terracing is gone.** `FILL_VERTICAL_M = 3.9` assumes an 8-bit raster. The raster
  is float16, with steps of 0.24–0.49 m, so there was nothing to de-terrace.
- **Rock is never a constraint.** A hole next to a rock, or the ground under a rock with no
  landscape sample, is filled from the ground around it. The rock's own heights stay in the
  cliff province and are composited by coverage, as before.
- **The open sea stays the page's colour.** The prototype drew the sea past the data as water
  over an invented bed. That was declined: nothing is drawn where the field has no data.
- **Water over the fill province** is drawn as before: level known, depth not, full alpha and
  the deep tint. The raster stores the water surface there, so the ground under it is not a
  sea bed and is not read as one.
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

### Known limits

- The 1 m staircase along the water edge is unchanged, because the water quality plane is 1 m.
- The rebuilt lattice is not stored. The server's height lookups still read the field as
  generated.

### Cost and output

A full run of both layers took about 37 min wall time on 2026-10-05. The lattice rebuild took
13 s, the geometry sweep and decode 35 s, the rock pass 13 min and the arch-and-boulder pass 2 min. Drawing and cutting took
11 min for terrain and 10 min for satellite. Tile output was 1,640 MB: terrain 835 MB and
satellite 805 MB, against 1,656 MB for recipe 4.

The seam trace's share of a hard switch is 1.22, against 1.18 for recipe 4. It is above 1
on both because the smoothed lift is not a convex blend. The seam's own p99 curvature fell
from 3,638 to 3,554, and the pure-kernel p99 fell from 2.11 to 1.24.

`--renders-name renders-v3` writes to `data/local/renders-v3/<layer>/`. Switching works as in
section 25.

## 27. A crisp shore, render-only meshes and the game-painted satellite: recipe 6 (2026-10-05)

Recipe 6 fixes the north beach in every style and adds a third style. Build 502094.

### The ocean shore

Recipe 5 took the ocean's plan shape from `waterq.u8.z`, which inherits the artwork's water
mask: 3.66 m BC1 blocks copied nearest-neighbour to 1 m. Its 0.9 m depth feather then ran on a
beach with a median slope of 0.067, smearing the edge over about 13 m. Nothing finer than the
1 m landscape exists in the game files, so the coast is now drawn from it.

- **Where.** `ocean_reach` in `palette/shore.py` (then `tools/map_shore.py`): measured water whose level is within 0.5 m
  of `OCEAN_LEVEL_M`, and every texel within 48 m of it that is not level-only water. Rivers,
  lakes and all level-only water keep recipe 5's rule, unchanged to the byte. Level-only
  water stands over the fill, whose raster holds the surface (about -16.3 m off the
  landscape frame) rather than a bed; read as ground it drew a dry strip around the frame. Applying the crossing to all measured
  water would newly wet about 1 M texels of river and lake bank, because their box tops are
  flat while their banks are not.
- **Coverage.** `cover = clip((L - z) / (|grad z| * px) + 0.5, 0, 1)`, with `z` the final
  drawn surface (terrain, rocks, arches, render-only meshes). The edge is one antialiased
  pixel at every zoom. The two rules are blended by the reach plane's bilinear coverage.
- **Optics.** The edge itself is one pixel, but the water fades in with depth, so the shore
  reads as a short physical transition rather than a line. Over the sea the water's opacity is
  `a0 + (1 - a0)(1 - exp(-d / c))` over ground darkened by `wet_darken`; the deep tint still
  uses `WATER_DEPTH_FULL_M`. Terrain: `a0` 0.30, `c` 0.6 m. Satellite: 0.15 and 1.1 m. Wet
  darkening 0.82. (A first cut used 0.62 and 0.38, which read as too sharp a border.)
- **Wet sand and foam**, per style in `shore`: `wet_band` multiplies the ground within `m`
  metres above the waterline (measured across the ground, `height / slope`) towards a darker,
  cooler tint, fading quadratically; `foam` lays a faint line over water shallower than
  `max_depth_m` and within `width_m` of the line, so a flat sandbar gets a line and not a
  sheet. Satellite: 3 m band, foam 0.25. Painted: 3 m band, foam 0.4 (0.12 m deep, 1 m wide).
  Terrain: neither.
- **The stroke.** A dark line where the crossing passes through a pixel is a style constant,
  `shore.stroke`, and is 0 (off) in every palette.
- **The level** is one constant, `OCEAN_LEVEL_M = -17.0`, the water boxes' and ocean tiles'
  own level. The artwork, the paint's Sand/WetSand crossover and the lowest land plants agree
  on about -17.4 instead. At -17.0 the spiral sandbars near (-339, -2275) read as shallows;
  whether they are dry in game decides the constant.
- **One pass.** Each painter now returns its ground and `water_composite` lays water over it
  once; outside the reach it is exactly recipe 5's `water_over`. The prototype drew the painter
  twice.

### Render-only meshes

Coral trees, big, plateau and small shells, `CliffPillar_03` and rubble are drawn by the
artwork as land and are absent from the heightfield. `mesh_items` (now in `terrain/rasters.py`) takes the statics
under `/Foliage/Coral/` and `/UnderWater/` plus `CliffPillar_03` from the same placement sweep,
and the foliage instances of the same directories and of `/Rubble/` and `SeaRock` (minus the
top-layer boulders) through a new `extra_foliage` harvest of `sweep_levels`. Each mesh is read
at its finest source, falling back to its collision hull. On this build: 57,294 coral, 4,223
shell and 38,009 rock instances.

They are rasterised into `meshes.cache/` beside `direct.cache/` and `top.cache/` (same size and
build stamp, plus the reader version), with a class plane: coral, shell or rock. They are
composited with the top layer's raise-only lift, and only where the mesh top stands within
0.6 m of the water surface or above it, so seabed coral roots do not speckle the sea.

**The heightfield is unchanged.** `CliffPillar_03` stays excluded there because it is passable
in game: the map draws what the artwork draws, and height lookups keep reading the walkable
ground. The provenance input is `render_meshes`, reader version 1.

### The paint input

`python -m mapgen paint` (preset `paint`, then `tools/gen_paint_layers.py`) writes `data/local/paint/` once per game build,
in about 25 s, 54 MB:

| File | What |
| --- | --- |
| `w.<Layer>.u8.z` | One weight plane per paint layer on the heightfield's 1 m grid, from each `LandscapeComponent`'s `WeightmapLayerAllocations` and its BGRA weightmap textures (17 layers on 2,289 components) |
| `canopy.u8.z` | Tree crown cover, `1 - exp(-crown area per m^2)`, from 99,045 tree foliage instances with a crown radius per species |
| `pigment.rgb.u8.z` | The `PigmentMap` texture, 1024 px, placed on the render frame |
| `meta.json` | `generator_version`, `cl`, the game pin, file sha256s and `digest`; each layer's linear albedo (texture mean times `FG_Landscape_Inst` vector), the rock and canopy albedos, and the component origins |

The layer-to-texture pairing is by name (`LAYERS` in the tool), because the cooked material
graph that wires them is stripped. Extracted planes reproduce the prototype's painted raster to
0.5% in linear light.

### The game-painted style

Layer `painted`, style `satellite-painted`, palette `palette/palettes/satellite-painted.json`,
in `palette/painted.py` (both under `tools/mapgen/src/mapgen/`). Once per run, on the 1 m grid (about 80 s):

1. Paint weights times layer albedo times 0.95, normalised by total weight; Puddles lerped on
   top. WetSand's OKLab lightness is set to 0.9 of Sand's: as shipped it is lighter than the
   sand it wets.
2. Times `(0.85 + 0.15 * PigmentMap)`.
3. **Component seams.** A component painted solid with one layer meets its neighbour in a
   straight 127 m line. Where the jump across a component edge is a step (above 0.03 in
   square-root linear), the colour is blended towards a 6 m blur of itself. 154,401 edge
   texels qualify on this build.
4. Off the landscape: the median paint of the biome, blurred 44 m, faded in over 6 m.
5. **Biome tint.** Biomes painted with the same Grass layer merged. Each biome gets a small
   OKLab `(a, b)` offset, 0.35 of its hue offset in the satellite-biome palette, blurred 44 m.
6. Rock colour on a 4 m grid: the game's cliff albedo, taking 0.3 of the lightness and 0.5 of
   the chroma of the ground around it (25 m blur), plus 0.02 L. Grey rock read as mud at the
   prototype's settings; this reads as stone.

Per pixel: canopy over the ground (0.85 times cover, Forest_Far albedo times 0.85); rock where
a rock, arch or boulder raises the surface (by its lift, not its coverage, so a buried mesh is
never coloured); the render-only meshes in their own colours (coral #b08a9c, shell #d6ccba,
coral whose top is under water the seabed blue #5f8899, rock class takes the rock colour); OKLab chroma times 1.2; then an altitude lift of 0.03 L
along a dry-land ramp. That ramp follows the recommended construction: heights over dry land
only (`waterq` dry, p1 to p99.5), position `0.35 * linear + 0.65 * equalised`, applied as an
even OKLab step. Light is sky plus sun (ambient 0.40), equal to 1 on flat ground, times
exposure 1.12 and the artwork borrow with its dark ink damped to 0.25. A highlight shoulder
at 0.72, then sRGB.

**Water** is Beer-Lambert, calibrated against Spire Coast screenshots:
`bed * T + W (1 - T) + 0.02 sky`, `T = exp(-k d)` with `k` = (4.08, 3.53, 3.53) per metre and
`W` #577f7e, then blended towards the open sea #354e68 by `1 - exp(-d / 12 m)`. The bed is the
ground colour times exposure times 0.8 (wet). Coral and shells are part of the bed: they are
composited into the ground colour first, and the depth `d` is measured to the drawn surface,
which over a mesh is the mesh top, so a shallow reef stays visible. The fit is six shallow
patches at 1.5 to 4.2 Delta E, with depths matched to the heightfield's range rather than
measured.

### Measured

On crops of the z7 grid, through `render_layer` itself (a `window` argument draws part of the
sheet): with recipe 6 switched off the refactored painters reproduce the shipped recipe 5
tiles to mean 0.0 and p99 0 per channel (max 1 to 4). Drawing an 8 Mpx crop took 2.8 s for
terrain, 3.0 s for satellite and 4.1 s for painted, against 2.35 and 2.56 s for recipe 5 and
3.86 and 4.07 s for the two-pass prototype.

### Cost and output

A full run of all three layers took 60 min wall time on 2026-10-05, against about 37 min for
two layers of recipe 5: lattice 11 s, paint preparation 78 s, sweep and decode 47 s, rock pass
10.5 min, arch-and-boulder pass 1.7 min, render-only mesh pass 6.4 min (42.6 M texels). Drawing
and cutting took 11 min for terrain, 13 min for satellite and 15 min for painted. The
render-only mesh cache is 5.4 GB at 32768 and is deleted with the others unless
`--keep-direct`. Tile output was 2,523 MB: terrain 863 MB, satellite 838 MB and painted
822 MB. `--renders-name renders-v4` writes to `data/local/renders-v4/<layer>/`; the registry
adopts the painted layer as `game-painted-r6-502094`.

### Known limits

- Lakes and rivers keep recipe 5's edge. Their banks need sloped surfaces from the river
  splines, which is a separate item.
- A river whose level is within 0.5 m of the sea and that reaches within 48 m of it is drawn
  by the ocean rule.
- Pigment strength (0.15) makes the central Red Bamboo ground strongly red, as the prototype
  did.

## 28. Relief styles, tones and the palette-only restyle (2026-10-05)

Two styles were added to the three drawn ones, from the colour study's directions A (muted
cartographic) and C (dark). Terrain and satellite stay selectable unchanged. Build 502094.

| Layer | Style | Tone | Palette |
|---|---|---|---|
| `relief` | `relief-muted` | light | `palette/palettes/relief-muted.json` |
| `relief-dark` | `relief-night` | dark | `palette/palettes/relief-night.json` |

### What the relief painter does

`palette/relief.py`, one painter for both palettes, all mixing in OKLab:

1. **Ramp.** Height to `t` over dry land only (`waterq` dry, p1 to p99.5): `0.35·linear +
   0.65·equalised` for the light style, `0.4/0.6` for the dark one. The stops are OKLCh; they are
   joined in OKLab, lightly smoothed, and re-spaced so equal steps of `t` are equal OKLab steps
   (largest step within 5% of the mean, checked by a test).
2. **Biome tints** (light only): per biome `(dL, C, h, w)`, blurred 24 biome texels (44 m) like
   the satellite's biome colours. Crater, Red Bamboo, Maze Canyons and Abyss Cliffs were pulled to
   C ≤ 0.036 and away from pink and lavender hues, which read as a fault on the gentle and arch
   crops.
3. **Rock**: smoothstep over 32–52° slope, towards `L + dl` at the rock hue.
4. **Shade**, measured from flat ground's n·L (sin 45°), so flat ground keeps its ramp colour
   exactly. Light: three suns (NW 0.5, W 0.25, N 0.25), `L += 0.30·d`, chroma ×
   `(1 + 0.35·min(d, 0))`. Dark: the NW sun, `L × clip(1 + 0.95·d, 0.42, 1.30)`. Both push the
   shadow side towards a cool hue and the sunlit side a little warm.
5. **Borrow**: the artwork's high pass rides on L only, as the cube root of the luminance
   multiplier the other styles use, with its dark (ink) side damped to 0.25.
6. **Water**: a 1 m tint plane built once per run, `1 − exp(−depth/τ)` for measured water and
   0.85 for level-only water, blurred (12 m light, 6 m dark) and normalised by the wet mask, so a
   shore takes the colour of the water beside it and no tint step follows the data edge. Over the
   sea the opacity rises from 0.45 at the line with a 0.8 m depth fade; the light style strokes
   the shoreline. No data stays the page's sea colour, as in every style.

The dark ramp's top two stops were `(.480 .040 40)` and `(.530 .038 285)`, which made high ground
mauve-grey mud (92% on the gentle crop in the study). They are now `(.480 .055 70)` and
`(.530 .045 85)`, ending warm; the two lowest stops moved from teal to green so lowland no longer
matches the shallow water.

### Measured

On z7 crops through `render_layer` itself (rock, arch and mesh rasters for all but the arch crop,
which is drawn from the lattice alone). Mud is CIELAB C* < 12 with 25 < L* < 60, near-white
L* > 78 with C* < 4, as in the colour study.

| Crop | relief: mud / near-white | relief dark: mud |
|---|---|---|
| gentle | 0.2% / 15.4% | 5.4% |
| arch | 2.8% / 0.1% | 4.7% |
| boulders | 9.2% / 0% | 1.8% |
| coast | 1.4% / 0% | 0% |
| desert lake | 1.1% / 0% | 6.6% |

The gentle crop is the central plateau, near the top of the ramp; its near-white share is down
from 64% on today's terrain. Drawing a 1.3 Mpx crop takes 1.2 to 2 s per style, about as long as
terrain.

### The palette-only restyle

A full render spends most of its time on geometry that does not depend on the palette: the sweep,
the rock pass, the arch-and-boulder pass and the render-only mesh pass. `--restyle` draws only
from the caches a render kept with `--cache-dir` and `--keep-direct`, and exits 9 when one is
missing or was cut for another size, sub-sampling or build, so a palette change never turns into
a full render. The caches are kept afterwards. The job preset is `restyle` on a render
(maps_contract.md §4).

Measured at `--size 1024` on 2026-10-05, with other renders running on the machine: the run that
built the caches (terrain only) took 8 min 41 s, most of it the sweep, the rock pass and the mesh
pass; a restyle drawing `relief` and `relief-dark` from those caches took 1 min 40 s, nearly all
of it the fixed preparation (field, lattice, artwork sheet, biome raster, the relief grounds at
about 20 s); a restyle at a size with no cache refused in 3.6 s with exit 9. At full size the
draw and cut dominate instead: from the recipe 5 stage times one layer is about 8.5 min against
about 37 min for a full two-layer render. The full-size figure is an estimate, not a run.

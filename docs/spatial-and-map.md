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
| `tools/gen_world_heightmap.py` (`--caves`, `--rocks`) | `mapgen heightmap` (`caves`, `rocks`): `heightmap.py`, `gamedata/sweep.py`, `gamedata/mesh.py`, `gamedata/water.py`, `gamedata/caves.py`, `gamedata/rocks.py`, `terrain/field.py`, `terrain/validate.py`, `terrain/sidecar.py` |
| `tools/gen_map_renders.py` | `mapgen renders`: `pipeline.py`, with `terrain/`, `palette/`, `lighting/` and `tiles/` |
| `tools/gen_map_image.py` | `mapgen artwork`: `artwork.py`, `gamedata/artwork_sheet.py`, `enhance/`, `tiles/artwork_output.py`; the frame in `gamedata/frame.py` |
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

### The artwork command, piece by piece (2026-10-05)

`mapgen artwork` (`tools/gen_map_image.py`) is split by concern. `artwork.py` holds only the
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
ground. The provenance input is `render_meshes`, reader version 1 (2 since section 35).

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
exposure 1.12 and the artwork borrow with its dark ink damped to 0.25. The gain and shoulder
of section 31, then sRGB.

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

- Lakes keep recipe 5's edge. Since recipe 7 (section 34), rivers get sloped banks from
  their splines.
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

## 29. Live sun light (2026-10-05)

A render drawn with `--unlit` (the Maps tab's "live sun" box) stores its colour without light
and adds a lighting pyramid, and the page relights it in the browser for any sun. One light,
the sun; the page picks where it stands.

### The model

`light = amb · sky · SVF + (1 − amb) · sun · max(n·L, 0) · (1 − shadow) / sin(max(el, 35°))`,
divided by the same expression for flat ground in the open, so flat ground at any sun is 1.

- **Lambert** is the shipped hillshade's own term. Relit at 315° / 45° with shadows and sky
  off, unlit terrain reproduces the baked hillshade to 2 levels (a test holds it).
- **Cast shadows** come from 32 stored horizon angles per pixel, interpolated between the two
  directions either side of the sun, with a 2° soft edge. A blocker counts fully up to 40 m
  away and not at all past 150 m (`FADE_M`): without the fade a low sun shadows a third to a
  half of the land. A 100 m fade was tried at 16:00 on the four shared crops and reads almost
  the same, so 150 m stays.
- **Sky view** within 10 m darkens the foot of a cliff and the floor of a gully. Larger radii
  grey whole valleys.
- **Normalisation** by `sin(max(el, 35°))`: without the clamp a fifth to a third of the
  pixels blow out at 20°.
- **Shadow floor.** The light passes through a soft maximum with 0.36 at a knee of 0.1
  (`SHADOW_FLOOR`, `SHADOW_FLOOR_KNEE`), so the darkest light lands at 0.36 to 0.40 instead of
  the 0.2 the bare model reaches in a shadowed gully.
- **Water** stays unlit: the land weight (one minus the water cover) blends the light out.
- **Per style.** The painted style lights in linear light with its own ambient, sky and sun
  colours; the shader undoes and reapplies the luminance tone curve of section 31
  (`tone_knee`, `tone_white`). Terrain, satellite and both relief styles multiply into sRGB,
  as the hillshade always did: ambient `SHADE_FLOOR / (SHADE_FLOOR + SHADE_RANGE · sin 45°)`,
  white light, no curve (`palette/lightparams.py`). Relief drawn unlit keeps flat ground at its
  ramp colour, so its live light is this sRGB approximation, not its own OKLab shade. One
  lighting pyramid serves every style.

The default sun is game noon, 225° / 62.25°. The game turns its sun about one fixed tilted
axis (`AFGSkySphere`, pitch `30 + 15 h`); `lighting/sun.py` and the page's `sun.ts` both
compute that path. The cartographic 315° / 45° is a one-click preset, not the default: the
artwork has no light direction to inherit.

### What is written

| Where | What |
| --- | --- |
| `<renders>/light/tiles/{z}/{x}_{y}.nrm.webp` | Lossless RGBA: east and south normal as `(v + 1) / 2`, sky view, land weight. An opaque tile drops the alpha channel, which a reader takes as land |
| `<renders>/light/tiles/{z}/{x}_{y}.hz.webp` | 32 horizons at half resolution, an 8 × 4 grey atlas of 128 px cells, `255 · sqrt(deg / 90)`, WebP q75 |
| `<renders>/light/meta.json` | The light axis (model constants and their digest), tile counts, timings |
| `<layer>/unlit/` | The unlit colour, 1x only |
| `<layer>/tiles/`, `tiles@2x/` | The colour lit by the default sun: what a page without WebGL, and every older reader, draws |

Coarser levels are computed from the coarser surface, not by averaging encoded tiles: normals
from the downsampled heights, sky view and horizons by mean.

### The stage

`render_layer` hands the first layer's drawn heights and land weight to a `Surface` (two
memory maps beside the raster caches, 5.4 GB at 32768). After that layer is drawn,
`lighting/stage.py` cuts the sheet into blocks of 16 × 16 native tiles, each with a 150 m
halo, and a process pool computes per block: horizons at half resolution, sky view, normals,
the native tiles, and the light at the default sun for the baked copy. A block whose core is
all water skips the horizon march. Each layer then installs `unlit/`, is lit in place by the
default sun, and installs `tiles/` and `tiles@2x/` as before (`tiles/lit.py`).

**Horizon cost.** The march takes bilinear samples up to 16 px and the nearest pixel beyond,
with in-place arithmetic: 0.14 µs per half-resolution pixel and direction, 6.3 times faster
than the all-bilinear reference, which it matches to a mean 0.10 to 0.17° and a 0.1 to 0.3%
difference in which pixels are shadowed at 25°. A rotated row sweep was built and measured at
1.7 times slower than the reference: rotating the block grows it, and every pixel of the
rotated square is computed. One full-size block of the steepest 2 km box took 121 s on two
threads of a shared machine, 69 s of it light terms and 52 s WebP encoding; 64 blocks project
to 2.2 CPU-hours, about 8 minutes on 16 workers, before water blocks are skipped. The
research estimate for the ray march was 72 minutes.

**Bytes.** The steepest 2 km box writes 78 KB of lighting per z7 tile, so a full map is at
most about 1.3 GB at z7 and less in practice, because open water compresses to almost
nothing. The unlit colour adds about half the colour pyramid again.

### Hooks

`bake_light` takes two optional rasters on the sheet's grid, both of which only cast:

- `occluder`: absolute heights in metres, NaN where empty. It blocks under its own shorter
  fade (`OCCLUDER_FADE_M`, 25 to 80 m) and lifts the receivers to its top, so a crown is lit
  or shaded where the painted layer draws it. The paint store's crown tops feed it
  (section 36; the mapgen README's "Tree shadows").
- `slabs = (ground, min_z, max_z)`: the surface without the floating geometry, and that
  geometry's underside and top. A slab extends a horizon only where its underside is below
  the horizon already reached, so an arch stops casting a curtain to the ground. On the arch
  crop at 16:00 the curtains go; the pipeline does not yet rasterise the arches' min-Z.

### The page

`litlayer.ts` draws a lit layer on one WebGL2 canvas in the base-map pane: per tile the unlit
colour, normals and horizons, relit by the shader with the arithmetic of `lighting/model.py`.
The z0 probe's `X-Map-Light` header carries the shader's numbers. Without WebGL2, or when the
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
- Sun colour along the day, and whether the artwork style gets any light at all.

## 30. The game's own surface colours on the painted layer (2026-10-05)

Four additions to the game-painted style of section 27, each read from the install rather than
chosen. Style `satellite-painted` version 2, paint generator version 2, two new readers.
Build 502094. Every number below was measured on crops of the z7 grid, not on a full sheet.

### The baked ground colour

Every landscape HLOD cell of `Persistent_Level.umap` ships an unlit BaseColor of its 508 m
square: a 1024 px virtual texture of 128 px BC1 tiles with a 4 px border, in Morton order.
`gamedata/bake.py` finds each cell's mip-0 chunk by its size and bulk flags, box-filters it to
1 m and places it where the landscape component of the same section lands. `python -m mapgen
paint` writes it as `bake.rgb.u8.z` (47 MB). On this build 141 of 148 cells decode; the seven
small edge cells (512 and 256 px) have another layout and are left out, and the paint covers
them. The bake covers 63.7% of the grid and 97% of the painted land. Black texels are its own
holes and count as no bake.

Beside it the store keeps `layers_bake_fit`: each paint layer's albedo refitted to the bake by
a non-negative least squares per channel, over 400,000 texels sampled every fourth texel where
the layers' weights sum above one half. A layer dominant on fewer than 200 sampled texels keeps
its name-matched albedo; on this build that is DesertRock and PurpleForest.

With `"ground": "bake"` in the palette the painted ground is the bake wherever it has one,
faded into the paint mix over `have_blur_m` (weight `clip(2 * blur(have) - 1) * have`), and the
paint mix elsewhere uses the refitted table. The name-matched table's corrections (the 0.95
darkening, WetSand's lightness fix) and the PigmentMap do not apply in that mode, and the biome
tint touches only ground off the bake. The prototype measured the albedo against the bake at a
median Delta E of 8.9 on the dune and 10.3 on the wet-sand beach with the old table, 1.0 and 1.2
with the refit, and 0 with the bake. Wet sand stops reading as shallow water. The ground comes
out darker (0.69 to 0.96 of the old luminance); retuning exposure and colour targets is the
colour calibration's job, not this one's.

### Rock surfaces

**Families.** The sweep now records each placement's first `OverrideMaterials` entry. A rock
wears that material, or its mesh's own first one, and the material's parent chain is walked to
one of the `Cliff_<Layer>` instances (`gamedata/rockfamily.py`). Rocks on this build: grass
4,737, plain cliff 4,344, forest 1,415, sand 1,019, red jungle 582, red grass 115, wet sand 42,
and 8,772 others (desert rock, boulders, arches) with no family.

**The plane.** The direct pass already rasterises every rock with the max-Z rasteriser, which
keeps the winning triangle's source id; the source is now the placement's family, written as
`direct.family.u8` beside the direct cache. The stamping therefore rides the rasteriser and
costs no pass of its own. The direct cache's stamp gains `families` (the `rock_families` reader
version), so a cache from before this change is rebuilt once.

**Colour.** The paint store records each family's `Color Tint`, the nearest one up the chain
(linear 0.624, 0.545, 0.471 for every family on this build), and its top layer, the mean of the
family root's `Far Albedo` texture or else its `Albedo`: forest and grass from their far
textures, red grass from `TX_GrassRed_01_Alb`, sand from `TX_Sand_BC`. Plain cliff, wet sand and
red jungle have none. Per pixel, rock is multiplied by its family's tint and then blended to the
top layer by an up-facing ramp on the drawn surface's normal, `nz` from 0.60 to 0.85, boxed over
3 pixels. That ramp is a guess: the `CliffTopMaterial` function is not decoded.

**Trees over rock.** Rock used to hide the canopy: 86 to 97% of tree cover above 0.5 in the
forest and ivy crops. The store's `crown.i16.z` holds, per 1 m texel, the highest crown top over
it in decimetres: each tree's base height plus `max(4 m, 1.2 * radius)`, within its crown
radius. Since generator version 3 the measured crown tops of section 36 fill it instead. The canopy is laid over rock wherever the drawn surface is no higher than that top, so
a tree on a ledge shows and a tree below a cliff stays hidden. It is a per-pixel comparison
against a 1 m plane; no tree is stamped at render time, which in the prototype extrapolated to
about 7 minutes for a full sheet.

### The Titan trees

73 trunks (`SM_TitanTree_01`, Nanite) and 218 leaf meshes (`SM_TitanTree_Leaves_01` and `_02`)
are StaticMeshActors the sweep already lists. `titan_items` picks them and the mesh rasteriser
draws them at twice the render's pixel into `titan.cache` (stamp: half the size, the build, the
`titan_trees` reader). The painter samples that raster bilinearly, lights the crowns by their
own relief, and lays them over the finished pixel, water included, at `titan_trees.opacity`
(0.8), leaves sRGB (77, 90, 48), trunks (99, 88, 81). They cover about 0.9 km² of ground that
was mostly drawn bare.

Players build under these trees, so they are a style toggle. `--no-titan-trees` renders with
opacity 0, skips the raster and records its own style digest; the Maps tab's generate form has
a "Titan trees (game-painted)" checkbox for it, the preset option `titan_trees`.

### Inland water

Under a few centimetres of water the Beer-Lambert model lets nearly all of the bed through, so
shallow pools and lake rims vanished: 0.81 km² of water is under 1 m deep. Off the ocean reach
the transmitted share is now multiplied by `1 - water.inland_floor` (0.35), so inland water
always keeps that much of its body colour. The sea is unchanged.

### Provenance

| Axis | Change |
| --- | --- |
| data: `paint` | generator version 2: `bake.rgb.u8.z`, `crown.i16.z`, `layers_bake_fit`, `rock_families`, `bake` stats |
| data: `rock_families` | reader version 1, painted layer only |
| data: `titan_trees` | reader version 1, painted layer only; absent when the trees are off |
| style | `satellite-painted` version 2 |

### Measured

The paint command took 68 s (25 s before): the bake read and decode 19 s, the refit, the crown
plane and the family colours most of the rest. The store is 104 MB. At render time the family
lookup took 1.7 s. Preparing the painted ground took 195 to 227 s with other jobs running on the
machine, against 112 to 124 s for the prototype on an idle one. Drawing a crop through
`render_layer` took no longer than before: 1.0 to 5.8 s per crop with everything on, against
1.6 to 7.4 s for the shipped recipe 6 on the same crops. The Titan raster for a 1600 px crop
of the forest took 32 s at the 2x pixel; the whole map is estimated at 1.5 to 2.5 min and has
not been measured.

### Known limits

- Nothing here has been compared with an in-game top-down view.
- The up-facing ramp is a guess, and grass tops come out a little light.
- The Titan crowns over water let the water's blue through at 0.8 opacity.
- Murky water (a floor of about 0.55 for swamps) needs the per-body class plane, which is not
  built.

## 31. Colour calibration of the game-painted style (2026-10-05)

The painted style is calibrated against in-game screenshots of the Spire Coast, the Dune
Desert, the Western Beaches and the Eastern Dune Forest. Style `satellite-painted` version 2. Code:
`palette/painted.py`. Numbers: the `tone` and `calibration` blocks of
`palette/palettes/satellite-painted.json`.

### The ground albedo source

`PaintedGround(..., bake=GroundBake(linear, have))` takes the game's baked ground colour (the
landscape HLOD BaseColor) as an optional input. `linear` is linear RGB, `(rows, cols, 3)`
float32, on the paint store's 1 m grid. `have` is a bool plane of the same shape.
`GroundBake.from_srgb(rgb, have)` builds one from 8-bit sRGB and drops the bake's black
holes. Without one, a palette with `"ground": "bake"` reads the paint store's own bake
(section 30) through the same feather, block by block; a store without a bake draws from the
paint table.

One function picks the source: `ground_albedo(paint, have, bake, feather_m)` returns the paint
mix unchanged when `bake` is None. Otherwise the bake replaces the paint mix where `have` is
set, feathered over `have_blur_m` inside its own edge. `PaintedGround.albedo_source` records
`"bake"` or `"paint"`. Under the bake the biome tint is skipped; elsewhere the paint table,
pigment and biome tint stand as in section 27. Every later step works on whichever albedo
arrived.

### Tone

- **Gain.** The ground's exposure is multiplied by `tone.gain` = 1.6, for both lit land and
  the bed under water. On land layers the bake fitted best at ×1.6 to ×2.1 linear. The water
  body, sky and deep colours are not scaled. The Beer-Lambert fit assumed a bed of the
  displayed dry sand times 0.8, which the gain now delivers: over the Sand target the ramp is
  0.25 m #8a9b92, 0.5 m #6d8c88 and 1 m #5a8182, against the fit's #8d9c93, #6f8e89 and
  #5d8483.
- **Shoulder.** It replaces the per-channel highlight shoulder. On luminance, the curve is
  the identity below `knee` 0.6. Above it, a Reinhard curve takes `white` 1.6 to 1, scaled
  to join the identity with slope 1.

### Per-layer colour transfer

The targets are display sRGB colours, at map exposure:

| Target | Colour |
| --- | --- |
| Sand | #d5cbb6 |
| WetSand | #b8a083 |
| SandRipples (the Dune Desert) | #d07756 |
| Grass | #83986e |
| Forest and the canopy | #558653 |

Each target is taken back through the flat-ground pipeline into ground OKLab: the inverse
shoulder, divided by exposure times gain and by the flat sky-and-sun light, then half the
altitude lift and the chroma gain undone. For each layer, the median colour of its pure
texels (at least 0.7 of the weight) is measured on the albedo as it arrived. The step from
that median to the target has three parts: a lightness offset, a chroma scale (clipped to
0.25 to 4) and a hue turn. Each texel moves by its layers' steps, mixed by their normalised
weights. Because the transfer measures its own source, the same targets work on the bake
and on the paint table.

### Other colours

- **Desert rock.** In Area_DuneDesert, Area_DesertCanyons and Area_RockyDesert (mask blurred
  25 m), the rock's chroma and hue are set to the target #ae8271. Its lightness keeps its
  variation around the target. Everywhere else, rock keeps its section 27 colour at the old
  exposure (`rock_keeps_exposure`). The Spire Coast rock in the references is mossy dark
  grey and was not sampled.
- **Meshes.** Coral-tree caps are #99868e, replacing the pink placeholder, and shells stay
  #d6ccba; both are display targets, so the gain does not brighten them. Seabed coral
  #5f8899 is a bed colour, as in the water fit, so it is also divided by the 0.8 wet factor.
  It is still composited into the bed before Beer-Lambert, with the depth measured to the
  coral top, so shallow reefs stay visible: #628b9b at the surface, #5a8286 at 0.5 m.

### Measured

Crops of the z7 grid through `render_layer`, with a scratch extraction of the bake as the
`GroundBake`. Each value is the median of the material's pixels: for layers, texels with at
least 0.7 of the weight, more than 1 m above the water and with no rock, mesh or canopy
cover. Values are ΔE (OKLab ×100) to the target, and to the screenshot reference in
brackets:

| Material | Crop centre (m) | Before | After, bake | After, no bake |
| --- | --- | --- | --- | --- |
| Dry sand | forest (-1295, 826) | 10.9 (15.0) | 0.3 (4.2) | 1.2 (3.5) |
| Dunes | Dune Desert (2700, -1700) | 7.3 (8.2) | 0.2 (4.6) | 1.4 (3.7) |
| Canopy | forest (-1295, 826) | 13.6 (16.6) | 1.4 (2.7) | 2.2 (2.4) |
| Desert rock | desert lake (3125, -674) | 1.8 (4.7) | 0.4 (3.1) | 0.5 (3.0) |
| Coral-tree cap | Spire Coast (-339, -2275) | 5.6 (3.4) | 3.2 (6.5) | 3.2 (6.5) |

The distance to the reference is the deliberate discount for the game's tonemap and grade,
which the targets remove. The coral caps sit 3.2 below target because they are domes lit by
their slope; their chroma and hue are within 0.5. Wet sand and grass had no dry, pure patches
in these crops. On the Spire Coast the wet sand is under water, as the references show.

### Known limits

- The SandRock, SandCracks and DesertRock layers have no target. Under the bake they take
  the bake's own salmon after the gain.
- Rock outside the deserts is uncalibrated.
- Every target comes from tonemapped perspective screenshots, not a top-down capture.

## 32. The seabed coral carpet (2026-10-05)

In the Spire Coast shallows the game shows patches of blue on the seabed: blue fans with pale
rims under turquoise water (owner screenshots and the wiki's Spire Coast shot). The painted
style drew none of it. Build 502094.

### What it is

Checked one candidate at a time against the game files:

- **Not a paint layer.** The `CoralRock` layer (`TX_SeaRocks_01_Alb`, linear albedo 0.171,
  0.173, 0.235) covers 0.62 km² of seabed at weight above 30, most of it on the Spire Coast.
  It is already drawn, and the HLOD BaseColor bake over the spiral sandbars' seabed, which
  carries it, is brown-grey, not blue.
- **Not the layer's grass type.** `LandscapeGrassType` `CoralRock` spawns crater grass, Grass_03,
  lichen and pebbles at runtime, culled at 150 m, so it never reaches a map view.
- **Not decals or spline meshes.** None of the 105 `DecalActor`s stands in the Spire Coast box
  (1 m grid rows 700 to 2400, columns 2500 to 4800), and the 19 spline meshes over its water
  are all `SM_RiverPlane`.
- **Placed foliage: `SM_CraterGrass_01`.** 112,525 instances map-wide. 48,145 have their origin
  under water, and 47,118 of those stand on `CoralRock` paint. Median origin depth 1.41 m. They
  form the clustered patches in the channels, where the screenshots show the carpet. The mesh is
  a 1.3 x 1.0 m rosette of upright cards, 0.53 m tall. Its albedo
  (`TX_CraterBush_01_Alb1`) is purple-pink with blue-white rims; the master material
  (`MM_Grass_Master`, SSS 0.8) is stripped, and through the water the game draws it blue.

Other coral foliage under water (barnacles, crater coral roots, coral formations) is grey-green,
pink or beige in its textures and stays with the render-only meshes of section 27.

### The input

`python -m mapgen paint` harvests the carpet in the same level walk as the canopy trees
(`gamedata/carpet.py`) and writes two more planes into the paint store, so they carry the paint
input's digest and build. Paint generator version 2.

| File | What |
| --- | --- |
| `carpet.u8.z` | Share of each 1 m texel under a rosette's plan, 0..255. The footprint is the convex hull of the mesh's plan view, sampled at 10 cm, times the instance's XY scale: 0.93 m² at scale 1. The blades are upright cards, so the triangles' own plan area (0.04 m²) would draw specks. |
| `carpet_top.i16.z` | The highest rosette top over the texel, decimetres, no-data elsewhere: origin z plus the mesh top times the Z scale. |

`meta.json` gains a `carpet` block: instances per mesh, the decode route, the footprint area and
the covered texels (166,056 on this build). Every instance is kept, wet or dry; the renderer
decides where water covers them.

### Drawing it

Style `satellite-painted` version 2, palette key `carpet`. In `palette/painted.py`:

1. **Patches.** The cover is blurred by `blur_m` (1.25 m) and mapped through
   `1 - exp(-gain * share)` with `gain` 3, so a cluster of rosettes reads as one patch with a
   mottled edge. Top no-data texels take the highest top within the blur.
2. **In the bed, under the water.** Only where the pixel is under water. The carpet replaces the
   bed by its cover, and is seen through the water above its own top: depth
   `level - top`, with `level = z + depth`. Then the same Beer-Lambert as the bed, before the
   open-sea term: `carpet * T + W (1 - T) + sky`.
3. **`depth_scale` 0.2.** The water's fitted `k` saturates by about 1 m, and the median carpet
   top is 0.8 m down (origins at 1.4 m). With the bed's own `k` the carpet would vanish, yet the
   screenshots show it clearly through the channels. The carpet's depth is scaled by 0.2; the
   water fit's depths were matched, not measured, so this is the weaker number of the two.
4. **Colour** `#6c9ebe` (linear-light carpet albedo at map exposure). Drawn over the channels it
   comes out at median `#5e8a9c`, against the calibration target `#5f8899` (the reference's
   `#6493a6` at map exposure).

`strength` 0 switches it off; a version 1 paint store has no carpet planes and draws none.

### Measured

On five 1280 px crops of the z7 grid through `render_layer`: the carpet changes 5.6% of the
pixels of a dense Spire Coast channel by more than 40 (summed over RGB), and nothing on the
spiral sandbars, where no crater grass grows. Its cost was not measured separately: one
7500² blur and one dilation at load, two plane samples per band.

### Known limits

- The blue is matched to screenshots, not read from the material: the grass master material is
  stripped, so in-game tint and subsurface scattering cannot be checked.
- Rosettes above the water line (in the Crater biome) are not drawn; they are ordinary land
  foliage, which the understory item covers.
- The pale rims the screenshots show inside the patches are not drawn.
## 33. Water by class in the game-painted style (2026-10-05)

Recipe 6 drew every water texel with one set of Beer-Lambert optics, the ones calibrated on
the Spire Coast sea. Inland that read wrong: rivers came out sea-blue, the swamp a clear blue
sheet, and sulfur ponds like the sea. The game-painted style now gives each water body a
class and each class its own optics. The ocean keeps its calibrated values unchanged.

### The class plane

`python -m mapgen paint` also writes `water_bodies.json` into the paint store: every water
actor with a box (class, world box, the materials its export subtree assigns) and every
root `StaticMeshComponent` whose mesh is under `/HotSpring/`. On build 502094 that is 839
actors and 101 terraces, and it doubles the paint run to about 50 s because the water boxes
read mesh bounds. `PAINT_GENERATOR_VERSION` is 3 (with sections 30 to 32 and 36).

`gamedata/waterbodies.py` `classify` turns that into a uint8 plane on the 1 m grid, once per
render (about 5 s):

1. Each actor's class comes from its first known material (`MATERIAL_CLASS`):
   `MI_SLW_River_*` river, `MM_Lake_01` lake, `MI_Lake_Blue_01` blue lake,
   `MI_Lake_Turquoise_01` turquoise, `MI_WaterSwamp_Muddy` swamp, `MI_Lake_Caves_01` cave,
   `SulfurPond_Inst` sulfur, `MM_OceanMaster` ocean. 527 actors (the `FGWaterVolume`
   brushes, translucent water, lake and ocean spline tools) assign none.
2. A lake box at most 150 m on a side with a hot-spring terrace inside it (within 1 m of its z
   range) is a hot spring.
3. Boxes paint wet texels whose level lies within 1 m of the box's z range, largest box
   first, so a pond inside a big box keeps its own class.
4. Unclaimed wet texels within 1 m of the ocean level are ocean. That includes the level-only
   water around the frame at about -16.3 m.
5. The rest of an inland body (8-connected) takes the body's majority class when that class
   covers at least a quarter of it.
6. What is left is swamp in `Area_Swamp` and lake everywhere else.

Build 502094: ocean 15.97 M texels, lake 1.20 M, swamp 0.62 M, river 0.38 M, turquoise 53 k,
hot spring 15 k, sulfur 9.7 k, cave 5.3 k, blue lake 5.2 k; 312 bodies claimed by
material, 0.28 M texels by the biome fallback.

The renderer samples the plane bilinearly with the dry taps dropped
(`terrain/sample.py` `ClassMix`), so a shore pixel takes its water's class rather than half
of nothing. A pixel whose wet taps agree takes its class's row exactly. A band holding only
ocean and dry texels takes the recipe 6 path unchanged, and so does a render from a paint store
without `water_bodies.json`; the sidecar's `paint.water_classes` says which happened.

### The optics

Per class, in `water_classes` of `satellite-painted.json`: absorption `k_per_m`, the body
colour `body`, the `deep` colour and its `deep_tau_m`, a `turbidity` and a `bed_tint`.

```
T     = (1 - turbidity) * exp(-k d)
under = bed * bed_tint * T + body * (1 - T) + 0.02 sky
under = lerp(under, deep, 1 - exp(-d / deep_tau))
```

With turbidity 0 and a white bed tint this is the ocean's formula. Turbidity is an opacity
floor: murky water hides its bed even at the edge. The bed tint stands for a stained bed,
the sulfur pond's orange rim.

| Class | Body | k (r, g, b) /m | Turbidity | Source |
|---|---|---|---|---|
| river | #4f7d78 | 2.4, 1.6, 1.6 | 0 | Hue from the wiki's Rocky Desert river; clear, so the bed shows |
| lake | #56745b | 3.0, 2.2, 2.6 | 0.1 | Wiki Lake Forest and the crash-site pond: jade green |
| lake_blue | #4a8494 | 3.4, 1.0, 0.75 | 0 | `MI_Lake_Blue_01` absorption (0.52, 0.15, 0.11) |
| turquoise | #448882 | 3.5, 1.0, 1.1 | 0.05 | `MI_Lake_Turquoise_01` deep colour and tint |
| swamp | #7e6e6a | 4.0, 4.5, 5.0 | 0.45 | Wiki Swamp: opaque mauve-brown mud |
| cave | #2f4a47 | 3.0, 2.5, 2.5 | 0 | Dark; no reference |
| sulfur | #67a395 | 3.0, 2.0, 2.0 | 0.3 | `SulfurPond_Inst`: deep (0.17, 1, 0.89) cyan, shallow (1, 0.26, 0) orange as the bed tint |
| hot_spring | #68a098 | 2.5, 1.6, 1.5 | 0.2 | Milky turquoise; no reference |

Targets follow the Spire Coast calibration (section 27): the reference colour times 0.85
linear for map exposure, OKLab L times 0.95 and chroma times 0.9. Against the references at
assumed depths, Delta E (OKLab x100): swamp 3.7 at 1 m and 5.2 at 3 m, lake 1.9 at 2 m and
4.5 at 4 m. Hue alone (the a, b distance) is under 1 for both. The river references are a
dusk shot at a grazing angle and a stream over white sand. They fix only the hue (a, b
distance 1 to 3), not the lightness.

### Known limits

- No top-down screenshot has checked any class. The sulfur pond, hot spring and cave optics
  come from material parameters, not from pictures.
- Classes change at box edges. Where the water channel is itself built from boxes, as in the
  Red Bamboo terrace lakes near (420, 560), a chain of pools reads as a mosaic of classes.
- The hot-spring rule finds terraces in lake boxes near the sulfur ponds and in the Red Bamboo
  terraces. Whether those pools are milky in game is unchecked.
- The satellite and terrain styles still draw one water colour.
## 34. Rivers from the game's own splines: recipe 7 (2026-10-05)

Recipe 7 draws every river as a continuous ribbon at its own height. Build 502094.

### What the game ships

- **The river actor is `BP_River_PROT_C`** (130 of them). There is no `WaterBodyRiver`; the one
  `FGRiverSpline` in the world has no components.
- Each river is a `SplineComponent` plus a chain of `SplineMeshComponent` sections (1,241
  in all) that bend the flat `SM_RiverPlane` along a cubic Hermite curve. The plane's mesh
  bounds are 1000 cm long, 500 cm either side of the centre and 0 cm thick.
- **So the water surface is the plane:** a centreline height and a half width along the curve,
  `500 cm * StartScale.X` to `500 cm * EndScale.X`. The scale's Y is 1 everywhere. 7 sections
  carry a pitch or roll; they are read as flat across.
- **There is no depth in the asset.** The depth along the spline is the plane minus the
  ground under it, measured per pixel at render time.
- The `SplineComponent`'s own scale curve has a Z of 46 to 78 on the river sampled. It does
  not reach the spline meshes and its meaning is unknown, so it is not read.
- The heightfield's water levelled each river on the **actor's boxes**. Their union is one
  AABB around the whole river, rotated and 2.5 m tall, so its top is the highest point of the
  river plus up to 2.5 m. Inside the ribbons the field's river water stood 1.8 m above the
  plane at the median and 49 m at p90.

Measured: 25.6 km of centreline, half width p10/p50/p90 3.4 / 15 / 30 m (max 113 m, at lake
mouths). Plane footprint 0.92 km². 0.39 km² of it stands above the 1 m ground, and of that
0.06 km² was not water in the field. Where it shows, the river is 0.91 m deep at the median;
15% of it is under 0.3 m.

### The reader

`gamedata/rivers.py`, run inside the shared level sweep (`sweep_levels` keeps each river's
sections beside the water boxes). The render caches both as `rivers.cache/rivers.json`,
keyed on the build and the reader version `river_splines`, so a run whose raster caches hit
skips the sweep. A standalone walk takes 9 s.

### The ribbon on the 1 m grid

`ribbon_planes` samples each section every 0.5 m and records three planes. Each texel takes
the nearest centreline piece, measured exactly rather than to the nearest sample.

| Plane | What it holds |
| --- | --- |
| `level_m` | the plane's height |
| `half_m` | its half width |
| `u` | distance over half width, 1 at the plane's edge |

- The planes are filled 8 m past the edge (`RIBBON_REACH_M`), and NaN beyond.
- **Open ends are square.** At an end no other section continues, the plane is cut across the
  tangent instead of rounding off.
- **Where two planes overlap, the higher shows**, as seen from above. Past both edges, the
  nearer one in half widths wins.
- The whole map takes about 1 s.

### Reconciling with the field's water

`palette/rivers.py`, once per run, on copies of the field's planes. The field on disk is not
changed.

- **A wet texel came from a river box** when its level equals that box's top within 5 cm and
  no other surface box stands as high: 0.358 km².
- **Under another surface box** (a lake the river AABB overhangs), the texel takes that box's
  level, or goes dry where measured ground stands above it: 0.27 km². The exception is
  inside the ribbon where the plane runs more than 1 m above that box. There the box is a
  lake's AABB reaching over the river's valley, so it is not used.
- **Anywhere else where the ribbon speaks**, the texel is dropped and the ribbon draws the
  river: 0.088 km².
- **The ribbon speaks** inside its reach, except where the plane stands more than 8 m over
  the ground (`RIVER_MAX_DEPTH_M`) or over no ground. Those are wide sections hanging over a
  waterfall pit or a lake below. Drawn there, they paint fans of water in the air.

### Drawing

Per band, through `RiverWater.over`.

- **Coverage.** The plane is sampled bilinearly. The river covers a pixel where the plane
  crosses the drawn surface: the ocean's one-pixel crossing rule (`shore_terms`) with a level
  per pixel instead of -17 m. The banks are where the game's plane meets the terrain, not
  where a mask ends.
- **Presence fades** to 0 over 1.5 m inside the plane's edge, over 1.5 m before the 8 m depth
  cut, where the plane hangs 1 to 2 m over other water (`RIVER_OVER_WATER_M`), and on
  texels beside a jump of more than 0.5 m between neighbours. Such a jump is two planes
  meeting, and any sampler draws a line along it.
- **Where a river meets other water, the higher surface shows**; a tie of 5 cm goes to the
  river. At a mouth the river plane dips under the lake or the sea, and the hand-over happens
  where the two levels agree, so the colour is continuous.
- **Optics.** River pixels get the shore optics: the opacity fade and wet darkening in
  terrain and satellite, and the wet band on the banks in every style. Painted water is
  Beer-Lambert, so the bed shows in the shallows.
- **The pale-path fix.** In the prototype a river 0.2 to 0.9 m deep was mostly the riverbed
  paint seen through recipe 5's 0.9 m alpha feather. Now the optics read a river at least
  `shore.river.min_depth_m` (0.6 m) deep once `bank_m` (2.5 m) in from its waterline, a
  taste setting per palette. At the waterline the true depth is used, so the bank stays soft.
  The palettes gained the block, so the three style versions went up by one.
- **Without rivers** (`--kernel-only`) the water terms are exactly recipe 6's.

### Cost

- `RiverWater` takes 4 to 7 s per run and about 1 GB of temporary 1 m planes.
- Drawing a crop costs 5 to 20% more than recipe 6.
- When the cache misses, the sweep is the shared one; the rivers add little to it.

### Known limits

- Nothing has been compared against an in-game top-down view of a river. The minimum
  optical depth and the colour are a taste call.
- Where the 8 m rule cuts a river plane, the field's river-box water stays at the box level,
  as in recipe 6. One waterfall pool near (-1170, -240) keeps a dark rectangle.
- Steps of more than 0.5 m per metre break the ribbon for a few metres. Those are
  waterfalls, which are a separate item.
- A section bent more tightly than its half width draws the fan its mesh would.
## 35. Waterfalls and the first small-mesh batch (2026-10-05)

Two additions to the satellite and game-painted styles. The heightfield is unchanged, and the
recipe number stays 6. A render records them as two provenance inputs, `waterfalls` (reader
version 1) and `render_meshes` (now reader version 2), plus each style's palette digest.
Build 502094.

### Where the falls come from

Every waterfall on the map is a `BP_WaterFallTool_02` actor: 191 of them, 185 inside the map
frame. The tool hangs a vertical curtain of `SM_Waterfall_Side_Module` instances, each 2 m wide
and 10 m tall, from a lip. It lays `SM_Waterfall_Top_Module` instances (8.05 m of rushing
water) upstream of the lip, and on 148 of the falls it puts `SM_SplashModule_Mid` discs where
the water lands. The modules are instanced components whose mesh and attachment come from the
class template, so the reader composes them onto the actor's root itself.
`gamedata/waterfalls.py` turns one actor into one record:

| Field | What |
| --- | --- |
| `x`, `y`, `z` | the middle of the lip, in metres |
| `along`, `out` | the lip's direction and the direction the water falls (the actor's local -Y) |
| `width_m` | the curtain's span along the lip |
| `height_m` | the curtain's length, which often runs on far below the ground |
| `top_len_m`, `splash` | the top modules' length, and each splash disc as `[x, y, z, radius]` |

The level sweep reads them in the same pass that harvests the render-only meshes
(`sweep_levels(read_actor=...)`), so this adds no second pass. A render caches the records in
`falls.cache/falls.json` beside the raster caches, stamped with the build and the reader
version, and deletes them with the caches unless `--keep-direct` is given. The 8
`SM_WaterfallMesh_01` and the one `Waterfall_Top_01` are backdrop meshes outside the playable
area and are not read.

### Which falls are drawn

`palette/falls.py` prepares the records against the field once per run. The drop is measured
to the lowest surface within 11 m out from the lip, never below the curtain's own end. A fall
is left out when:

- its lip is less than 1 m above the ocean level, or under standing water. These are the ocean
  pouring off the edge of the world: 102 of the 185 in-frame records.
- the field has no ground under the lip, or less than half of the 30 points out from it.
- the ground under the lip is more than 4 m above it, which means the fall is inside a cave or a
  rock: 40 records.
- it drops less than 2 m.

One more record fails the last two tests, which leaves 42 drawable falls on this build.

### How they are drawn

The falls are drawn on the finished band in sRGB, after the sea fill, and only ever towards the
foam colour. A pixel is never darkened. Each fall has three terms:

- **The streak** is a band across the full width of the lip. It runs from the top modules
  upstream (faint, 0.45) to `spread` metres out, where `spread = 0.05 * drop`, clamped to
  2–9 m, and fades to 0.55 at its end. Two cosines across the lip break it into strands. It
  shows only where the drawn ground stands below the lip plus 3 m, so a rock roof over the
  fall hides it.
- **The pool** is a filled capsule along the lip at the landing (the splash discs' median
  offset, or half the spread), with a radius of `0.08 * drop`, clamped to 3–10 m, and a
  slightly brighter rim. It shows only where the ground is at least 40% of the drop below the
  lip, reaching full strength at 80%.
- **The mist** is a soft halo 1.8 radii wide around the pool, at 0.28, in a cooler grey.

The edges are softened over the larger of one pixel and 0.6 m. A fall narrower than a pixel
therefore covers only part of it, so the falls stay small at preview sizes and on the coarse
tiles. All the numbers are in each palette's `falls` block. The terrain style has none and
draws no falls. The drawn ground, not the water surface, decides what hides the streak: the
water boxes around a fall often stand above its lip.

### The small-mesh batch

The first slice of the satellite-gaps study's small-mesh list, drawn through the existing
render-only path (section 27):

| Meshes | How | Instances | Class |
| --- | --- | --- | --- |
| Hot-spring terraces, `/World/Environment/HotSpring/` | static meshes, added to `RENDER_ONLY_DIRS` | 101 | `terrace`, game-painted colour #ccbea8 |
| `SmoothRock_03`, `SnakeStone_01` | foliage instances, matched by exact name in `RENDER_ONLY_FOLIAGE_MESHES` | 1,710 and 730 | `rock` |

The 31 `Hotspring_Blob_01` meshes inside geyser nodes are components of the node actor, so the
sweep does not reach them. The satellite style draws the new meshes as relief only, like the
other render-only meshes.

### Known limits

- Nothing here has been checked against an in-game top-down view. The streak widths and the
  pool sizes were chosen by looking at crops.
- The waterfall material bends the curtain outwards in the vertex shader (`Curvature Amount`,
  `WPO Strenght`). The record keeps the straight curtain the instances describe.
- Where a rock overhangs part of a pool, the pool stops at the rock's edge. That is correct,
  but it can draw a straight cut across the foam.
- A 277 m wide fall, such as the one at (1784, 559), draws a long bright bar. At z3 and below
  it is one of the brightest features in its area.
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

`terrain/crowns.py` stamps the crowns into each band of the render's own grid. Each tree's
sprite is turned by its yaw, scaled, and shifted along its trunk axis by the species' mean
crown height, so a leaning bamboo's crown stands off its base. The sprite is read through a
mip chain (2x2 means; the top channel takes the maximum) at the level whose texel is nearest
the output pixel, bilinearly, so a 1024 preview keeps each crown's area. Trees are laid
lowest top first, each over the ones below. A band returns cover, cover-weighted colour, a
dome height and the highest crown top in world cm; `crown_band(...)["top_cm"]` is the crown
height raster on any render grid.

`palette/painted.py` composites them last, over water and foam, under the highlight
shoulder. The `crowns` block of `satellite-painted.json`:

| Key | Value | What |
| --- | --- | --- |
| `draw` | true | Off, the soft canopy is drawn as before |
| `canopy_kept` | 0.0 | How much of the soft canopy stays under the crowns. 0: the crowns replace it, so no tree is drawn twice |
| `darkening`, `chroma` | 0.85, 0.8 | Times the texture colour, and times the style's own chroma gain of 1.2 |
| `dome_gain`, `shade_clamp` | 0.35, [0.55, 1.2] | The light: the style's sky and sun over the shared sun's `sun_dot` on the crown's smoothed height (0.75 m) times 0.35, relative to flat ground and clamped |
| `hidden_below_m` | 0.5 | A crown whose top is more than this below the drawn surface is hidden: a tree under an overhang, or beside a higher rock |
| `over_water` | 0.6 | Crown opacity over water, so a river under bamboo still reads |

### Measured

Extraction adds 28 to 56 s to the paint command (sprites, records and the 1 m top plane),
which took 65 to 108 s in all on a loaded machine; the store grows from 54 to 66 MB. Drawing a z7 crop of the painted layer with crowns took 1.8 s against 0.8 s without (forest,
1.1 Mpx), 2.3 against 1.1 s (Red Bamboo, 1.7 Mpx) and 2.5 against 1.8 s (Titan forest,
2.7 Mpx). Most of the sheet has no tree. A 1024 preview of the painted layer ran end to end.

### Known limits

- **No colour has been checked against the game.** The texture means are the raw albedo:
  bamboo is a saturated pink-red, the tall mangroves' tops are their bark texture. The style's
  `chroma` of 0.8 is a taste call.
- A crown is lit by the fixed north-west sun of the painted style. The live sun shading takes
  the crown tops as its occluder (section 29); the crown domes are not yet in its normal
  pyramid.
- Lean moves a crown; it does not foreshorten it.
- `SM_Trunk_01` (6,933 logs and stumps) draws as small bark sprites.

## 37. Where sections 28 to 36 meet (2026-10-05)

Sections 28 to 32 and 33 to 36 were built side by side. Where two of them touch the same
pixel, these rules decide.

| Where | Rule |
| --- | --- |
| Inland water opacity | Section 31's `inland_floor` (0.35) and a water class's `turbidity` (section 33) both say how much body colour inland water keeps. The larger applies, never both, so the swamp (0.45) keeps its own and the lake (0.1) gets the floor. The ocean row has turbidity 0 and draws exactly as before. |
| River ribbons | A pixel's share of ribbon water (section 34) takes the `river` class's optics, whatever the class plane says under it. The class plane was built from the field's water, which the ribbon partly replaces. |
| Crown tops | One producer: the measured tops of section 36 write `crown.i16.z`. Section 30's estimate from the radius is gone. Section 30's trees-over-rock reads the same plane. |
| Canopy over rock | With crowns drawn the soft canopy is off (`canopy_kept` 0), so section 30's rule draws nothing and the crowns' own "hidden under a higher surface" test decides. |
| Crowns and Titan trees | Crowns are composited first, the Titan raster last: the Titan trees stand taller. |
| Tree shadows | The lighting stage's occluder (section 29) is the crown-top plane on the sheet's grid. It blocks under `OCCLUDER_FADE_M` and receives on the crown top. Only a run that draws the painted layer has it. |
| Versions | Paint generator version 3. Styles: terrain 3, satellite 3, game-painted 4. Recipe 7. Readers: `render_meshes` 2, `river_splines`, `waterfalls`, `rock_families` and `titan_trees` 1. |
| Caches | The river cache is a raster cache; the falls cache sits beside it. `tiles/extras.py` loads meshes, falls, Titan trees and rivers for a run. |

### Known limits

- A palette-only `--restyle` checks the raster caches it needs, not the river and falls
  caches. A restyle without them sweeps the game again rather than refusing.
- The crown domes are not in the lighting stage's normal pyramid.
- No combination has been compared against an in-game top-down view.

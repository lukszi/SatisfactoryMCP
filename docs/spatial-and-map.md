# Space, and the map drawn on it

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §7 is the coordinate frame, the region
layer and the selector language every spatial and planning tool shares; §17 to §41 are the web
map, from its base layers and the page that picks one to the heightfield, the drawn renders,
their styles, water, light and caches. Those live in [docs/map/](map/) under the numbers they
were given, and the [index below](#sections-17-to-41-the-map) says which file holds each.
Section numbers are continuous with the rest of the spec; [DESIGN.md](../DESIGN.md) indexes it.

The dated sections name the generator files as they were when each section was written. Since
2026-10-05 the map generators are one package, `tools/mapgen/`, run as
`python -m mapgen <command>`; the old entry scripts are thin shims that keep their paths. Its
[README](../tools/mapgen/README.md) names the command behind each old script and has the package
map.

## Sections 17 to 41: the map

One file per part of the pipeline. A reference to `spatial-and-map.md` section NN, in a
document or in a docstring, resolves through this table. §37 stays here, because it is where
the others meet.

| § | Section | File |
| --- | --- | --- |
| 17 | [Two more base layers, drawn rather than found (2026-07-31)](map/renders.md#17-two-more-base-layers-drawn-rather-than-found-2026-07-31) | renders.md |
| 18 | [One base map at a time, and the page says which (2026-07-31)](map/page.md#18-one-base-map-at-a-time-and-the-page-says-which-2026-07-31) | page.md |
| 19 | [The water channel, rebuilt out of the game's own water (2026-07-30)](map/heightfield.md#19-the-water-channel-rebuilt-out-of-the-games-own-water-2026-07-30) | heightfield.md |
| 20 | [The two-regime sampler, and a smoothed z7 (2026-07-31)](map/renders.md#20-the-two-regime-sampler-and-a-smoothed-z7-2026-07-31) | renders.md |
| 21 | [The side panel: factory health and power circuits (2026-09-26)](map/page.md#21-the-side-panel-factory-health-and-power-circuits-2026-09-26) | page.md |
| 22 | [Terrain z: three surfaces, a mapped cache, and the site's height (2026-10-05)](map/heightfield.md#22-terrain-z-three-surfaces-a-mapped-cache-and-the-sites-height-2026-10-05) | heightfield.md |
| 23 | [The cave flag (2026-10-05)](map/heightfield.md#23-the-cave-flag-2026-10-05) | heightfield.md |
| 24 | [Rock heights from the collision surface (2026-10-05)](map/heightfield.md#24-rock-heights-from-the-collision-surface-2026-10-05) | heightfield.md |
| 25 | [Renders after the terrain work: recipe 4 (2026-10-05)](map/renders.md#25-renders-after-the-terrain-work-recipe-4-2026-10-05) | renders.md |
| 26 | [Rebuilt base data and a PCHIP sampler: recipe 5 (2026-10-05)](map/renders.md#26-rebuilt-base-data-and-a-pchip-sampler-recipe-5-2026-10-05) | renders.md |
| 27 | [A crisp shore, render-only meshes and the game-painted satellite: recipe 6 (2026-10-05)](map/painted.md#27-a-crisp-shore-render-only-meshes-and-the-game-painted-satellite-recipe-6-2026-10-05) | painted.md |
| 28 | [Relief styles, tones and the palette-only restyle (2026-10-05)](map/painted.md#28-relief-styles-tones-and-the-palette-only-restyle-2026-10-05) | painted.md |
| 29 | [Live sun light (2026-10-05)](map/light-and-crowns.md#29-live-sun-light-2026-10-05) | light-and-crowns.md |
| 30 | [The game's own surface colours on the painted layer (2026-10-05)](map/painted.md#30-the-games-own-surface-colours-on-the-painted-layer-2026-10-05) | painted.md |
| 31 | [Colour calibration of the game-painted style (2026-10-05)](map/calibration.md#31-colour-calibration-of-the-game-painted-style-2026-10-05) | calibration.md |
| 32 | [The seabed coral carpet (2026-10-05)](map/painted.md#32-the-seabed-coral-carpet-2026-10-05) | painted.md |
| 33 | [Water by class in the game-painted style (2026-10-05)](map/water.md#33-water-by-class-in-the-game-painted-style-2026-10-05) | water.md |
| 34 | [Rivers from the game's own splines: recipe 7 (2026-10-05)](map/water.md#34-rivers-from-the-games-own-splines-recipe-7-2026-10-05) | water.md |
| 35 | [Waterfalls and the first small-mesh batch (2026-10-05)](map/water.md#35-waterfalls-and-the-first-small-mesh-batch-2026-10-05) | water.md |
| 36 | [Tree crowns (2026-10-05)](map/light-and-crowns.md#36-tree-crowns-2026-10-05) | light-and-crowns.md |
| 37 | [Where sections 28 to 36 meet (2026-10-05)](#37-where-sections-28-to-36-meet-2026-10-05) | this file |
| 38 | [Perched water: a box top that is not the surface (2026-10-05)](map/water.md#38-perched-water-a-box-top-that-is-not-the-surface-2026-10-05) | water.md |
| 39 | [Compressed raster caches: the zstd band store (2026-10-06)](map/renders.md#39-compressed-raster-caches-the-zstd-band-store-2026-10-06) | renders.md |
| 40 | [Drawing a layer's bands on threads (2026-10-06)](map/renders.md#40-drawing-a-layers-bands-on-threads-2026-10-06) | renders.md |
| 41 | [Compiled kernels: the light's march and the sampler's gathers (2026-10-07)](map/renders.md#41-compiled-kernels-the-lights-march-and-the-samplers-gathers-2026-10-07) | renders.md |

---

## 7. Spatial model

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
`tests/support/reference_world.py` states the field as a bounding box instead: a region name is
advisory by design and a regression suite must not stand on one.

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
`config.web_url()` (`SATISFACTORY_WEB_PORT`, default 8712; frontend_vision.md §13.2). Below
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
Implemented in `spatial/nodes/selectors.py`. The terms themselves are tabulated once, next to the machine
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

## 37. Where sections 28 to 36 meet (2026-10-05)

Sections 28 to 32 and 33 to 36 were built side by side. Where two of them touch the same
pixel, these rules decide.

| Where | Rule |
| --- | --- |
| Inland water opacity | Section 31's `inland_floor` (0.35) and a water class's `turbidity` (section 33) both say how much body colour inland water keeps. The larger applies, never both, so the swamp (0.45) keeps its own and the lake (0.1) gets the floor. The ocean row has turbidity 0 and draws exactly as before. |
| Swamp water | Section 31's opaque swamp colour is applied last, over whatever the swamp class's optics (section 33) drew, so its 0.3 m tau decides everywhere but the very edge. It goes only on the swamp class's share of a pixel's water: ocean inside `Area_Swamp` keeps the sea. Which texels the swamp class claims is section 33's rule. |
| River ribbons | A pixel's share of ribbon water (section 34) takes the `river` class's optics, whatever the class plane says under it. The class plane was built from the field's water, which the ribbon partly replaces. |
| Crown tops | One producer: the measured tops of section 36 write `crown.i16.z`. Section 30's estimate from the radius is gone. Section 30's trees-over-rock reads the same plane. |
| Canopy over rock | With crowns drawn the soft canopy is off (`canopy_kept` 0), so section 30's rule draws nothing and the crowns' own "hidden under a higher surface" test decides. |
| Canopy targets | Section 31's species targets (the red Kapok) move their species' crowns first, wherever they grow; then the canopy target moves the crowns of section 36, the step taken by the crowns near the target's hue, so a crown a species target moved is gated by its new colour. The soft canopy they used to colour stays off. Its named crown targets (the blue palms) move the crowns of their own hue wherever they grow, gated apart from the canopy target. |
| Rock family and rock target | Section 31's rock targets are set first; section 30's family tint goes on relative to the families' median, so a common tint leaves rock on target. A family with a target of its own (desert rock, section 31's "Rock by mesh family") takes it over the area's rock. A render-only rock wears its own family from the mesh cache's family plane, tint, target and top layer, as a cliff does; one with no family takes the area's rock. |
| Coral, carpet and water | Section 32's carpet and section 31's seabed coral are both bed colours under the water. A coral speck standing in water is drawn as that water with the coral as its bed. |
| Crowns and Titan trees | Crowns are composited first, the Titan raster last: the Titan trees stand taller. |
| Coral trees | One producer: the render-only mesh pass of section 27. Section 36's crowns leave out every species that pass draws; the crown-top plane still holds them, so they still cast tree shadows (section 36, "Coral trees are no crowns"). |
| Meshes standing in the water, lit | The lighting pyramid's surface is the seabed rule's, whichever layer captures it (section 29, "One capture"): the render-only meshes standing in the water are water in it, and the painted layer, drawn unlit, keeps the default sun's light of their own top on them. |
| Crowns and water | A crown standing out of the water is composited after the water, the foam and the shore line, whole; one under the surface goes into the bed after section 32's carpet and before the open-sea term and section 31's opaque water, so the class optics, the open sea and the swamp's murk all apply to it (section 36, "Crowns and the water"). |
| Tree shadows | The lighting stage's occluder (section 29) is the crown-top plane on the sheet's grid, with each pixel's covered share. It casts into crown horizons of their own under `OCCLUDER_FADE_M`, received on the crown top, and only the painted layer, which draws the crowns, reads them; terrain, satellite and relief are shaded by the ground alone. Every lit run with a paint store has it, whatever layers it draws. |
| Perched water | Section 38 re-levels the water the river reconcile left, so a ribbon stands in for its box wherever the spline speaks and the membrane only where none does. Every style, the water classes and the relief tint read that result, not the field's box levels. Water below a drop inside a box is re-levelled before the rest of its body, so the class plane sees the basin under the wide fall at the swamp's level and the swamp box claims it. |
| Holes and the open sea | Section 38's holes are filled after the re-levelling and never where the river reconcile dropped water; `WaterSurfaces.grades` carries them, and the open sea (row below) hands those grades to every style. Section 33's open sea is found on that same drawn water, so a box at the sea's level stops at the sea's reach. |
| Caches | The river cache is a raster cache; the falls cache sits beside it. `render/extras.py` loads meshes, falls, Titan trees and rivers for a run. |
| Open sea, void and pits | Section 26's open sea is laid into the lattice and the water planes after the rivers and section 38 have drawn theirs, and before any style draws. Every style, the water classes and the relief tint read that one bed, and the renderer's wet and measured planes come from those planes' grades, so water a later stage re-wets is drawn. |

The style, reader and recipe versions current today are in
`src/satisfactory_mcp/core/gameassets/versions.py`, and the ones a render was drawn with are
in its sidecar.

### Known limits

- A palette-only `--restyle` checks the raster caches it needs, not the river and falls
  caches. A restyle without them sweeps the game again rather than refusing.
- The crown domes are not in the lighting stage's normal pyramid.
- No combination has been compared against an in-game top-down view.

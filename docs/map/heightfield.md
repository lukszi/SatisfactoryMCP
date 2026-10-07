# The heightfield: water, terrain z, caves and rocks

Sections 19, 22, 23 and 24 of the [design spec](../../DESIGN.md): the water channel the heightmap
generator writes beside the 1 m field, and how the server reads a height, a cave and a
rock from it. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

Numbers 19 to 24 also name sections of parked.md, residency.md and plumbing.md; in these
files they are the map's ([the document set](../../DESIGN.md#the-document-set)).

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
Since 2026-10-05 the renders subtract a drawn bed instead, continued from the measured one
beside it (section 26); only `--kernel-only` and level-only water away from the ocean's level
keep the deep tint.

One thing the channel still cannot fix: beyond the landscape's own extent the field has no
height *and* no water volume, so those texels stay no-data in the field. The renders used to
paint them the page's `--sea`, and against bright water that edge drew straight lines across
the sea. Since 2026-10-05 the renders draw them as the artwork does instead: its water as
the open sea over a drawn bed, the rest as the void (section 26, "The open sea, the void and
the pits"). The field itself is unchanged.

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

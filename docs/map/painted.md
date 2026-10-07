# The painted and relief styles

Sections 27, 28, 30 and 32 of the [design spec](../../DESIGN.md): the game-painted style, the relief
styles, the game's own surface colours and the seabed carpet. A section number below
resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 27. A crisp shore, render-only meshes and the game-painted satellite: recipe 6 (2026-10-05)

Recipe 6 fixes the north beach in every style and adds a third style. Build 502094.

### The ocean shore

The ocean's plan shape from `waterq.u8.z` inherits the artwork's water mask: 3.66 m BC1 blocks
copied nearest-neighbour to 1 m. A 0.9 m depth feather on a beach with a median slope of 0.067
smears that edge over about 13 m. Nothing finer than the 1 m landscape exists in the game
files, so the coast is drawn from it.

- **Where.** `ocean_reach` in `palette/water/shore.py`: measured water whose level is within
  0.5 m of `OCEAN_LEVEL_M`, and every texel within 48 m of it that is not level-only water.
  Rivers, lakes and all level-only water keep recipe 5's rule. Level-only water stands over
  the fill, whose raster holds the surface (about -16.3 m off the landscape frame) rather than
  a bed; read as ground it drew a dry strip around the frame. Applying the crossing to all
  measured water would newly wet about 1 M texels of river and lake bank, because their box
  tops are flat while their banks are not.
- **Coverage.** `cover = clip((L - z) / (|grad z| * px) + 0.5, 0, 1)`, with `z` the final
  drawn surface (terrain, rocks, arches, render-only meshes). The edge is one antialiased
  pixel at every zoom. The two rules are blended by the reach plane's bilinear coverage.
- **Optics.** The edge itself is one pixel, but the water fades in with depth, so the shore
  reads as a short physical transition rather than a line. Over the sea the water's opacity is
  `a0 + (1 - a0)(1 - exp(-d / c))` over ground darkened by `wet_darken`; the deep tint still
  uses `WATER_DEPTH_FULL_M`. Terrain: `a0` 0.30, `c` 0.6 m. Satellite: 0.15 and 1.1 m. Wet
  darkening 0.82.
- **Wet sand and foam**, per style in `shore`: `wet_band` multiplies the ground within `m`
  metres above the waterline (measured across the ground, `height / slope`) towards a darker
  tint, fading quadratically; `foam` lays a faint line over water shallower than
  `max_depth_m` and within `width_m` of the line, so a flat sandbar gets a line and not a
  sheet. Satellite: 3 m band, foam 0.25. Painted: 3 m band, foam 0.4 (0.12 m deep, 1 m wide).
  Terrain: neither. The painted band's tint is a mild warm (0.90, 0.88, 0.86), because the
  wet sand layer carries the wet colour by rule (section 31, "Wet sand by rule"); a cool tint
  pulled the band towards mauve.
- **The stroke.** A dark line where the crossing passes through a pixel is a style constant,
  `shore.stroke`, and is 0 (off) in every palette.
- **The level** is one constant, `OCEAN_LEVEL_M = -17.0`, the water boxes' and ocean tiles'
  own level. The artwork, the paint's Sand/WetSand crossover and the lowest land plants agree
  on about -17.4 instead. At -17.0 the spiral sandbars near (-339, -2275) read as shallows;
  whether they are dry in game decides the constant.
- **One pass.** Each painter returns its ground and `water_composite` lays water over it
  once; outside the reach it is exactly recipe 5's `water_over`.

### Render-only meshes

Coral trees, big, plateau and small shells, `CliffPillar_03` and rubble are drawn by the
artwork as land and are absent from the heightfield. `mesh_items` (`terrain/render_meshes.py`)
takes the statics under `/Foliage/Coral/` and `/UnderWater/` plus `CliffPillar_03` from the
same placement sweep, and the foliage instances of the same directories and of `/Rubble/` and
`SeaRock` (minus the top-layer boulders) through the `extra_foliage` harvest of
`sweep_levels`. Each mesh is read at its finest source, falling back to its collision hull. On
this build: 57,294 coral, 4,223 shell and 38,009 rock instances. The coral trees are tree
foliage too, and only this pass draws them (section 36, "Coral trees are no crowns").

They are rasterised into `meshes.cache/` beside `direct.cache/` and `top.cache/` (same size and
build stamp, plus the reader version), with a class plane: coral, shell or rock. They are
composited with the top layer's raise-only lift, and only where the mesh top stands within
0.6 m of the water surface or above it, so seabed coral roots do not speckle the sea.

The terrain, satellite and relief styles draw ground and water only, so there a mesh never
breaks the water's surface: under water, coral, shells and terraces are left to the seabed,
and a rock is drawn only where its top stands above the surface. Most coral the 0.6 m rule
keeps stands well clear of the water (median 6 m), so each would be a one- or two-pixel island
in the lagoons. The game-painted style keeps the rule above and colours the meshes itself.

**The heightfield is unchanged.** `CliffPillar_03` stays excluded there because it is passable
in game: the map draws what the artwork draws, and height lookups keep reading the walkable
ground. The provenance input is `render_meshes`, whose reader version moved with the falls of
section 35, with the rocks' family plane (section 31, "The Spire Coast rock from its own
material") and with `CliffPillar_03` read from its own package rather than `Mesh_Old`.

### The paint input

`python -m mapgen paint` writes `data/local/paint/` once per game build, about 113 MB in about
2 minutes. Paint generator version 3 writes:

| File | What | Section |
| --- | --- | --- |
| `w.<Layer>.u8.z` | One weight plane per paint layer on the heightfield's 1 m grid, from each `LandscapeComponent`'s `WeightmapLayerAllocations` and its BGRA weightmap textures (17 layers on 2,289 components) | 27 |
| `pigment.rgb.u8.z` | The `PigmentMap` texture, 1024 px, placed on the render frame | 27 |
| `bake.rgb.u8.z` | The landscape HLOD's baked BaseColor on the 1 m grid | 30 |
| `canopy.u8.z` | The soft canopy's cover, `1 - exp(-crown area per m^2)`, from each tree's measured radius | 36 |
| `crowns.rec.z`, `crowns.sprites.z`, `crown.i16.z` | One record per tree, one sprite per species, and the crown top on the 1 m grid | 36 |
| `carpet.u8.z`, `carpet_top.i16.z` | The seabed coral carpet's cover and top | 32 |
| `water_bodies.json` | Every water actor's box and materials, and the hot-spring terraces | 33 |
| `meta.json` | `generator_version`, `cl`, the game pin, file sha256s and `digest`; each layer's linear albedo (texture mean times `FG_Landscape_Inst` vector) and its refit to the bake, the rock families' tints and top layers, the crowns, the carpet and the component origins | |

The layer-to-texture pairing is by name (`LAYERS` in the command), because the cooked material
graph that wires them is stripped. Extracted planes reproduce the prototype's painted raster to
0.5% in linear light.

### The game-painted style

Layer `painted`, style `satellite-painted`, palette `palette/palettes/satellite-painted.json`.
`palette/painted/ground.py` builds the ground once per run from the paint store, on the 1 m grid
(about 80 s), and `palette/painted/band.py` draws each band over it (both under
`tools/mapgen/src/mapgen/`). With `"ground": "bake"`, the palette's setting, the bake is the
ground wherever it has one (section 30); elsewhere the paint mix is:

1. Paint weights times layer albedo times 0.95, normalised by total weight; Puddles lerped on
   top. WetSand's OKLab lightness is set to 0.9 of Sand's: as shipped it is lighter than the
   sand it wets. (In the game the wet sand is the sand multiplied by `WetSand_Color`, about
   (0.42, 0.38, 0.34), which the name-matched texture mean misses; section 31 derives the wet
   sand target from the sand target.)
2. Times `(0.85 + 0.15 * PigmentMap)`.
3. **Component seams.** A component painted solid with one layer meets its neighbour in a
   straight 127 m line. Where the jump across a component edge is a step (above 0.03 in
   square-root linear), the colour is blended towards a 6 m blur of itself. 154,401 edge
   texels qualify on this build.
4. Off the landscape: the median paint of the biome, blurred 44 m, faded in over 6 m.
5. **Biome tint.** Biomes painted with the same Grass layer merged. Each biome gets a small
   OKLab `(a, b)` offset, 0.35 of its hue offset in the satellite-biome palette, blurred 44 m.
6. Rock colour on a 4 m grid: the game's cliff albedo, taking 0.3 of the lightness and 0.5 of
   the chroma of the ground around it (25 m blur), plus 0.02 L. Grey rock read as mud; this
   reads as stone.

Per pixel: canopy over the ground (0.85 times cover, Forest_Far albedo times 0.85); rock where
a rock, arch or boulder raises the surface (by its lift, not its coverage, so a buried mesh is
never coloured); the render-only meshes in their own colours (section 31, "Other colours"),
rock in its own family; OKLab chroma times 1.2; then an altitude lift of 0.03 L along a
dry-land ramp. That ramp follows the recommended construction: heights over dry land only
(`waterq` dry, p1 to p99.5), position `0.35 * linear + 0.65 * equalised`, applied as an even
OKLab step. Light is sky plus sun (ambient 0.40), equal to 1 on flat ground, times exposure
1.12 and the artwork borrow with its dark ink damped to 0.25. The gain and shoulder of section
31, then sRGB.

**Water** is Beer-Lambert, calibrated against Spire Coast screenshots:
`bed * T + W (1 - T) + 0.02 sky`, `T = exp(-k d)` with `k` = (4.08, 3.53, 3.53) per metre and
`W` #577f7e, then blended towards the open sea #3c597d by `1 - exp(-d / 12 m)` (section 33,
"Sea deep and swamp water"). The bed is the ground colour times exposure times 0.8 (wet).
Coral and shells are part of the bed: they are composited into the ground colour first, and
the depth `d` is measured to the drawn surface, which over a mesh is the mesh top, so a shallow
reef stays visible. The fit is six shallow patches at 1.5 to 4.2 Delta E, with depths matched
to the heightfield's range rather than measured. Inland water takes its class's optics
(section 33).

### Known limits

- Lakes keep recipe 5's edge; rivers get sloped banks from their splines (section 34).
- A river whose level is within 0.5 m of the sea and that reaches within 48 m of it is drawn
  by the ocean rule.
- Pigment strength (0.15) makes the central Red Bamboo ground strongly red where the paint
  mix draws.

## 28. Relief styles, tones and the palette-only restyle (2026-10-05)

Two styles beside the three drawn ones, from the colour study's directions A (muted
cartographic) and C (dark). Build 502094.

| Layer | Style | Tone | Palette |
|---|---|---|---|
| `relief` | `relief-muted` | light | `palette/palettes/relief-muted.json` |
| `relief-dark` | `relief-night` | dark | `palette/palettes/relief-night.json` |

**Palette files are checked when they load** (`palette/schema.py`). Each file and each of its
blocks has one TypedDict, and `checked` refuses a missing key, a stray one, a value of the
wrong type, or a calibration that names a rock family the game does not have, with a
`PaletteError` naming the place in the file, before anything is drawn. Adding or removing a
key in `palettes/*.json` needs the matching field in `schema.py`. A palette's digest is its
canonical JSON, so any edit, its `about` strings included, changes the digest every render of
that style records.

### What the relief painter does

`palette/relief.py`, one painter for both palettes, all mixing in OKLab:

1. **Ramp.** Height to `t` over dry land only (`waterq` dry, p1 to p99.5): `0.35·linear +
   0.65·equalised` for the light style, `0.4/0.6` for the dark one. The stops are OKLCh; they are
   joined in OKLab, lightly smoothed, and re-spaced so equal steps of `t` are equal OKLab steps
   (largest step within 5% of the mean, checked by a test).
2. **Biome tints** (light only): per biome `(dL, C, h, w)`, blurred 24 biome texels (44 m) like
   the satellite's biome colours. Crater, Red Bamboo, Maze Canyons and Abyss Cliffs are held to
   C ≤ 0.036 and away from pink and lavender hues, which read as a fault on the gentle and arch
   crops.
3. **Rock**: smoothstep over 32–52° slope, towards `L + dl` at the rock hue.
4. **Shade**, measured from flat ground's n·L (sin 45°), so flat ground keeps its ramp colour
   exactly. Light: three suns (NW 0.5, W 0.25, N 0.25), `L += 0.30·d`, chroma ×
   `(1 + 0.35·min(d, 0))`. Dark: the NW sun, `L × clip(1 + 0.95·d, 0.42, 1.30)`. Both push the
   shadow side towards a cool hue and the sunlit side a little warm.
5. **Borrow**: the artwork's high pass rides on L only, as the cube root of the luminance
   multiplier the other styles use, with its dark (ink) side damped to 0.25.
6. **Water**: a 1 m tint plane built once per run, `1 − exp(−depth/τ)` for measured water,
   blurred (12 m light, 6 m dark) and normalised by the wet mask, so a shore takes the colour
   of the water beside it and no tint step follows the data edge. The depth is read off the
   ground the run draws, so the open sea's bed (section 26) tints the open sea; level-only
   water away from the ocean's level stays at 0.85. Over the sea the opacity rises from 0.45 at
   the line with a 0.8 m depth fade; the light style strokes the shoreline at 0.2, as more
   inked a dark rim round every island and rock in the water. The void is drawn as in every
   style (section 26).

The dark ramp runs from `(.360 .056 148)` to `(.600 .048 85)`: green at the bottom, so lowland
does not match the shallow water, and warm at the top, where cooler stops made high ground
mauve-grey mud. Its water is darker and bluer, shallow `(.300 .066 226)` and deep
`(.215 .046 244)`, so the shallows do not read as lowland. On the 2048 preview the land's
median L is 0.44 and water 0.5 to 4 m deep 0.30, and 8% of the land is darker than that water.

### Measured

On z7 crops through `render_layer` itself (rock, arch and mesh rasters for all but the arch
crop, which is drawn from the lattice alone), the light style. Mud is CIELAB C* < 12 with
25 < L* < 60, near-white L* > 78 with C* < 4, as in the colour study.

| Crop | Mud | Near-white |
|---|---|---|
| gentle | 0.2% | 15.4% |
| arch | 2.8% | 0.1% |
| boulders | 9.2% | 0% |
| coast | 1.4% | 0% |
| desert lake | 1.1% | 0% |

The gentle crop is the central plateau, near the top of the ramp; the terrain layer draws 64%
of it near-white. Drawing a 1.3 Mpx crop takes 1.2 to 2 s per style, about as long as terrain.
The dark style's ramp has not been re-measured since its contrast change.

### The palette-only restyle

A full render spends most of its time on geometry that does not depend on the palette: the sweep,
the rock pass, the arch-and-boulder pass and the render-only mesh pass. `--restyle` draws only
from the caches a render kept with `--cache-dir` and `--keep-direct`, and exits 9 when one is
missing or was cut for another size, sub-sampling or build, so a palette change never turns into
a full render. The caches are kept afterwards. The job preset is `restyle` on a render
(maps_contract.md §4). The kept caches are a zstd band store, about 0.9 GB at full size
(section 39); a raw cache kept before the band store is still drawn from. A lit restyle
installs the light the render kept instead of baking it again (section 29, "Kept light"), and
sweeps the game again for the rivers and falls when their caches are missing (section 37's
known limits).

## 30. The game's own surface colours on the painted layer (2026-10-05)

Four additions to the game-painted style of section 27, each read from the install rather than
chosen. Build 502094. Every number below was measured on crops of the z7 grid, not on a full
sheet.

### The baked ground colour

Every landscape HLOD cell of `Persistent_Level.umap` ships an unlit BaseColor of its 508 m
square: a 1024 px virtual texture of 128 px BC1 tiles with a 4 px border, in Morton order.
`gamedata/ground/bake.py` finds each cell's mip-0 chunk by its size and bulk flags, box-filters
it to 1 m and places it where the landscape component of the same section lands. `python -m
mapgen paint` writes it as `bake.rgb.u8.z` (47 MB). On this build 141 of 148 cells decode; the
seven small edge cells (512 and 256 px) have another layout and are left out, and the paint
covers them. The bake covers 63.7% of the grid and 97% of the painted land. Black texels are
its own holes and count as no bake.

A hole the bake encloses on every side is ground the game hides: a crater's pit, the abyss
pits, a cave's mouth. The paint under it is never seen and is one solid layer per landscape
component, so it would draw as squares. So the paint is dropped there and the biome fallback
(the area's median ground, blurred over `fallback_blur_m`) draws instead;
`paint.hidden_ground_texels` in the sidecar counts them (238,054 on build 502094).

Beside it the store keeps `layers_bake_fit`: each paint layer's albedo refitted to the bake by
a non-negative least squares per channel, over 400,000 texels sampled every fourth texel where
the layers' weights sum above one half. A layer dominant on fewer than 200 sampled texels keeps
its name-matched albedo; on this build that is DesertRock and PurpleForest.

With `"ground": "bake"` in the palette the painted ground is the bake wherever it has one,
faded into the paint mix over `have_blur_m` (weight `clip(2 * blur(have) - 1) * have`), and the
paint mix elsewhere uses the refitted table. The name-matched table's corrections (the 0.95
darkening, WetSand's lightness fix) and the PigmentMap do not apply in that mode, and the biome
tint touches only ground off the bake. Against the bake the name-matched table is a median
Delta E of 8.9 off on the dune and 10.3 on the wet-sand beach, the refit 1.0 and 1.2: wet sand
stops reading as shallow water. The bake is darker than the name-matched mix (0.69 to 0.96 of
its luminance); section 31's gain answers for that.

On a sheet coarser than the 1 m grid (4096 px and below) the painted layer samples its ground
over each pixel's footprint, every texel it covers weighted by its share
(`terrain.sample.taps_footprint`). One bilinear sample per 3.66 m pixel drew the bake's
stippled blends as speckle and its 1 to 2 m trails as dotted lines, in the Rocky Desert most of
all. A pixel no wider than a texel keeps the bilinear taps, so 8192 px and up, the full-size
render included, draw exactly as before.

### Crude oil stamps (2026-10-06)

Every crude oil node (`BP_ResourceNode_C` with `Desc_LiquidOil_C`) leaves a stamp in its
cell's bake: the same blot about 20 m across, either near black (sRGB about 45, 45, 38) or a
pale rainbow speckle. The paint layers under it are the ground around it, sand on the Spire
Coast islets, and the artwork draws nothing there. Drawn as ground, the 1 m bake stretched
over 4.4 pixels of the full-size sheet, so each stamp drew as a black, blurred, blocky blot. On
build 502094, 26 of the 30 nodes stand on bake; the other four lie in its holes. None of the
541 other nodes, wells and geysers on bake carries a stamp. A stamp reaches at most 10.2 m from
its node, and past 9 m the bake is back to its usual distance from the paint mix.

The painted ground patches each stamp before the bake is blended (`palette/painted/albedo.py`
`patch_stamps`). Within 11 m of a crude oil node (`STAMP_INNER_M`) the bake takes the paint
mix, scaled to the bake by the median per-texel ratio of bake to paint on the ring out to 15 m
(`STAMP_OUTER_M`). Across that ring it hands back to the bake by a smoothstep.

The ratio is the leading paint layer's own where that layer leads at least 20 of the ring's
texels (`STAMP_LAYER_MIN`), else the whole ring's (2026-10-07). The game paints wet sand
under and around most oil puddles. On the west coast node at (-2450, 906) the ring is mostly
dry sand, whose ratio (1.01, 1.28, 1.27) turned the wet sand under the stamp grey-green, a
grey disc inside the brown wet sand that reaches past the stamp; with the wet sand's own ratio
the patch is the wet sand around it.

The grey and blue-grey discs at some ore nodes, such as the copper node at (3817, -2683) and
the stone node at (-371, 2015), are no stamp: they are the `Cliff_LayerInfo` and gravel the
landscape paints under the node, in the paint layers and the bake alike, and the node's own
mesh, which covers them in game, is not drawn. They are kept as game data. The nodes come
from `data/world_resource_nodes.json` (`gamedata/nodes.py` `oil_nodes`). The bake keeps its
weight there, so the biome tint stays off, as it is around the node. 10,011 texels are replaced
and 8,635 blended; the sidecar records `paint.bake_stamps_patched`. In the Spire Coast window at
(269, -1943) no pixel within 11 m of its three nodes is darker than sRGB luma 70.

### Rock surfaces

**Families.** The sweep records each placement's first `OverrideMaterials` entry. A rock
wears that material, or its mesh's own first one, and the material's parent chain is walked to
one of the `Cliff_<Layer>` instances (`gamedata/rocks/families.py`). Rocks on this build: grass
4,737, plain cliff 4,344, forest 1,415, sand 1,019, red jungle 582, red grass 115, wet sand 42,
and 8,772 others (desert rock, boulders, arches). Desert rock is a family of its own (section
31, "Rock by mesh family").

**The plane.** The direct pass rasterises every rock with the max-Z rasteriser, which keeps the
winning triangle's source id; the source is the placement's family, written as
`direct.family.u8` beside the direct cache. The stamping rides the rasteriser and costs no pass
of its own. The direct cache's stamp carries `families` (the `rock_families` reader version),
so a cache cut by another version is rebuilt.

**Colour.** The paint store records each family's `Color Tint`, the nearest one up the chain
(linear 0.624, 0.545, 0.471 for every family on this build), and its top layer, the mean of the
family root's `Far Albedo` texture or else its `Albedo`: forest and grass from their far
textures, red grass from `TX_GrassRed_01_Alb`, sand from `TX_Sand_BC`. Plain cliff, wet sand and
red jungle have none. Per pixel, rock is multiplied by its family's tint relative to the
median tint of all families, then blended to the top layer by an up-facing ramp on the drawn
surface's normal, `nz` from 0.60 to 0.85, boxed over 3 pixels. That ramp is a guess: the
`CliffTopMaterial` function is not decoded. The top lies in patches inside that ramp, and the
forest top wears a display target in place of its texture's mean (section 31, "Moss in
patches"); the sand and grass tops wear their paint layers' targets (section 31, "A top made
of a paint layer").

An arch or boulder of the top pass lifted over a cliff is not that cliff, but the family plane
under it is the cliff's: the direct pass alone stamps it. So a rock pixel takes the area's
rock, with no family tint or top, by the overlay's lift over the surface below it (the band's
`top_weight`, full from `MESH_FULL_LIFT_M`). The seventh render drew a root beam over the
Northern Forest's coast at (-122, -1580) with the sand family's top as a cream stripe and the
forest family's moss further on, and arches of the Titan Forest at (1400, -560) with their
cliffs' moss.

The rock targets of section 31 are measured on rock that already wears the common tint, so
only a family's departure from it is applied (`palette/painted/surfaces.py` `family_tables`).
With one tint for every family, as on this build, rock stays on its target. A render-only rock
(`CliffPillar_03`, sea rocks, rubble, rock piles) never takes the family or top layer of a
cliff whose footprint it happens to overlap; it wears its own (section 31, "The Spire Coast
rock from its own material").

**Trees over rock.** Rock hid 86 to 97% of the tree cover above 0.5 in the forest and ivy
crops. The soft canopy is laid over rock wherever the drawn surface is no higher than the
store's crown top over that texel (`crown.i16.z`, section 36), so a tree on a ledge shows and a
tree below a cliff stays hidden. It is a per-pixel comparison against a 1 m plane. With crowns
drawn the soft canopy is off, and the crowns' own "hidden under a higher surface" test decides
(section 37).

### The Titan trees

73 trunks (`SM_TitanTree_01`, Nanite) and 218 leaf meshes (`SM_TitanTree_Leaves_01` and `_02`)
are StaticMeshActors the sweep already lists. `titan_items` picks them and the mesh rasteriser
draws them at twice the render's pixel into `titan.cache` (stamp: half the size, the build, the
`titan_trees` reader). The painter samples that raster bilinearly, lights the crowns by their
own relief, and lays them over the finished pixel, water included, at `titan_trees.opacity`
(0.8), leaves sRGB (77, 90, 48), trunks (99, 88, 81). They cover about 0.9 km² of ground that
was mostly drawn bare. They are exposed like everything else, by `exposure` times `tone.gain`.

Players build under these trees, so they are a style toggle. `--no-titan-trees` renders with
opacity 0, skips the raster and records its own style digest; the Maps tab's generate form has
a "Titan trees (game-painted)" checkbox for it, the preset option `titan_trees`.

### Inland water

Under a few centimetres of water the Beer-Lambert model lets nearly all of the bed through, so
shallow pools and lake rims vanished: 0.81 km² of water is under 1 m deep. Off the ocean reach
the transmitted share is multiplied by `1 - water.inland_floor` (0.35), so inland water
always keeps that much of its body colour. The sea is unchanged. A water class's own
`turbidity` takes over where it is larger (section 37).

### Known limits

- Nothing here has been compared with an in-game top-down view.
- The up-facing ramp is a guess.
- The Titan crowns over water let the water's blue through at 0.8 opacity.

## 32. The seabed coral carpet (2026-10-05)

In the Spire Coast shallows the game shows patches of blue on the seabed: blue fans with pale
rims under turquoise water (in-game screenshots and the wiki's Spire Coast shot). Build 502094.

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
(`gamedata/vegetation/carpet.py`) and writes two planes into the paint store, so they carry the
paint input's digest and build.

| File | What |
| --- | --- |
| `carpet.u8.z` | Share of each 1 m texel under a rosette's plan, 0..255. The footprint is the convex hull of the mesh's plan view, sampled at 10 cm, times the instance's XY scale: 0.93 m² at scale 1. The blades are upright cards, so the triangles' own plan area (0.04 m²) would draw specks. |
| `carpet_top.i16.z` | The highest rosette top over the texel, decimetres, no-data elsewhere: origin z plus the mesh top times the Z scale. |

`meta.json` gains a `carpet` block: instances per mesh, the decode route, the footprint area and
the covered texels (166,056 on this build). Every instance is kept, wet or dry; the renderer
decides where water covers them.

### Drawing it

Palette key `carpet`, in `palette/painted/optics.py`:

1. **Patches.** The cover is blurred by `blur_m` (3 m) and mapped through
   `1 - exp(-gain * share)` with `gain` 8, so a cluster of rosettes reads as one patch with a
   soft edge and a lone rosette as a faint tint (0.12 at most). Top no-data texels take the
   highest top within the blur. The rosettes cover 0.3% of the grid and 2.4% of the Spire
   Coast, so a smaller blur (1.25 m, gain 3) draws one blue dot each, a stipple, not a carpet.
   Measured on the Spire Coast box, a 3 m blur leaves the cluster share at 0.13 (median) to
   0.37 (90th percentile) where there is any, which the gain maps to 0.65 to 0.95.
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

`strength` 0 switches it off; a paint store without carpet planes draws none.

### Measured

On five 1280 px crops of the z7 grid through `render_layer`: the carpet changes 5.6% of the
pixels of a dense Spire Coast channel by more than 40 (summed over RGB), and nothing on the
spiral sandbars, where no crater grass grows. Its cost: one 7500² blur and one dilation at
load, two plane samples per band.

### Known limits

- The blue is matched to screenshots, not read from the material: the grass master material is
  stripped, so in-game tint and subsurface scattering cannot be checked.
- Rosettes above the water line (in the Crater biome) are not drawn; they are ordinary land
  foliage, which the understory item covers.
- The pale rims the screenshots show inside the patches are not drawn.

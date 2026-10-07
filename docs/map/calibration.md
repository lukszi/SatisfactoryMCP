# Colour calibration of the game-painted style

Section 31 of the [design spec](../../DESIGN.md): the targets the game-painted style is calibrated to,
and how they were measured. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 31. Colour calibration of the game-painted style (2026-10-05)

The painted style is calibrated against in-game screenshots: first the Spire Coast, the Dune
Desert, the Western Beaches and the Eastern Dune Forest, then a second pass over the biomes
those left out (see "Area targets"). Code: `palette/painted/ground.py` builds the ground once
per run and `band.py` draws each band, with `calibration.py`, `trees.py` (crowns), `optics.py`
(water) and `surfaces.py` (rock and meshes). Numbers: the `tone` and `calibration` blocks of
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
  displayed dry sand times 0.8, which the gain delivers: over the Sand target the ramp is
  0.25 m #8a9b92, 0.5 m #6d8c88 and 1 m #5a8182, against the fit's #8d9c93, #6f8e89 and
  #5d8483.
- **Shoulder.** On luminance, the curve is the identity below `knee` 0.6. Above it, a
  Reinhard curve takes `white` 1.6 to 1, scaled to join the identity with slope 1.

### Per-layer colour transfer

The targets are display sRGB colours, at map exposure. Each is tagged by its source: a
screenshot measured by the method below, or derived by a rule from another target.

| Target | Colour | Source |
| --- | --- | --- |
| Sand | #d5cbb6 | screenshot |
| WetSand | #a29583: the Sand target at OKLab L ×0.80, C ×1.0, h −10 | derived (rule) |
| SandRipples (the Dune Desert) | #ca784f | screenshot |
| Grass | #83986e | screenshot |
| Forest and the canopy | #558653 | screenshot |
| Rock outside every area entry | #85816c | screenshot |

With a paint store from generator 4 on, a render takes the keys the palette's
`calibration.derived_keys` lists from the game install instead ("Targets derived from the game
install" below), these six among them.

#### Wet sand by rule (2026-10-06)

In the game the wet sand is the sand multiplied by `WetSand_Color`: the bake's Sand times
(0.422, 0.378, 0.336) matches the bake's WetSand within 1 to 11%. Five daylight screenshots of
an exposed wet band against the dry sand beside it pool to L ×0.80, C ×0.98 and a hue turn of
−13° (Steam [3771679183](https://steamcommunity.com/sharedfiles/filedetails/?id=3771679183),
the north beach; [3806590944](https://steamcommunity.com/sharedfiles/filedetails/?id=3806590944);
[3427095273](https://steamcommunity.com/sharedfiles/filedetails/?id=3427095273);
[3438470146](https://steamcommunity.com/sharedfiles/filedetails/?id=3438470146); and
[Western Beaches](https://satisfactory.wiki.gg/images/Western_Beaches.png)). Warm light gives
−3 to −13°. Under a blue-cast sky the glossy band reflects the sky and turns −30 to −50°, so a
single shot of it can read a low-chroma mauve; that is the sky, not the sand.

`calibration.derived` holds the rule: WetSand is the Sand target with OKLab lightness ×0.80,
chroma ×1.0 and hue −10°. `with_derived` (`palette/painted/calibration.py`) adds it to every
scope that has a Sand target and no WetSand target of its own: #a29583 from the global
#d5cbb6, and #987b61 inside the desert entry from its #c4ab8b. A target written out in a scope
wins over the rule. The derived targets then go through the per-layer transfer like any other.

The band the shore draws (section 27, `wet_band`) multiplies the ground near the waterline on
top of that, by a mild warm (0.90, 0.88, 0.86); a cool tint pulled the band towards mauve.
Measured on windows of the full-size grid (see "The land rules, measured" below), dry ground
painted WetSand lands at L ×0.72 to ×0.78 of the dry sand 10 to 60 m up the beach, C ×0.82 to
×0.94 and h −3 to −9°. (On the ocean coast at (-2515, 645), whose dry sand is near grey, it
reads C ×1.32 and h −15°.) A stronger band would sink it further below ×0.80 within the first
metre. On a beach painted Sand down to the drawn sea, as at the north beach (-58, -1507), the
band alone draws the wet line, at L ×0.97 in the first metre. The paint has no WetSand above
the drawn sea there: its Sand/WetSand crossover sits near -17.4 m and the sea at -17.0 m
(section 27).

#### SandRipples

The Dune Desert's target is the pooled median of four 1.0 daylight Steam shots, measured as
the area targets below are: [3486291454](https://steamcommunity.com/sharedfiles/filedetails/?id=3486291454)
(rippled sand seen almost straight down, high sun),
[3352353206](https://steamcommunity.com/sharedfiles/filedetails/?id=3352353206),
[3360666296](https://steamcommunity.com/sharedfiles/filedetails/?id=3360666296) and
[3590923329](https://steamcommunity.com/sharedfiles/filedetails/?id=3590923329). They range
from h 44 to h 49 and pool to L 0.653, C 0.117, h 46.9, which is #ca784f. The game's own bake
for the layer is red-orange too (#b36957). A golden-hour shot under a pink sky
([Dune Desert](https://satisfactory.wiki.gg/images/Dune_Desert_Area.png), probably Early
Access) reads redder, h 44.5, and is not used; a daylight shot of a rippled flat by a lake
reads h 54 and is left out as an outlier.

#### The transfer

Each target is taken back through the flat-ground pipeline into ground OKLab: the inverse
shoulder, divided by exposure times gain and by the flat sky-and-sun light, then half the
altitude lift and the chroma gain undone. For each layer, the median colour of its pure
texels (at least 0.7 of the weight) is measured on the albedo as it arrived. The step from
that median to the target has three parts: a lightness offset, a chroma scale (clipped to
0.25 to 4) and a hue turn. Each texel moves by its layers' steps, mixed by their normalised
weights. Because the transfer measures its own source, the same targets work on the bake
and on the paint table.

### Area targets

`calibration.areas` is a list of entries. Each one names map areas and gives targets that hold
only there. An area is named by its stem (`Area_crater`, both craters) or by one asset
(`Area_crater_1`, the Blue Crater; `Area_RedJungle_2`, the Jungle Spires). The membership is
blurred over `area_blur_m` (25 m) on the 4 m rock grid. An unknown family in
`calibration.families` or `tops` stops the run when the palette loads (section 28, "Palette
files are checked"). An entry can carry:

- **`layers`.** The layer's weight is split by the area share. The area's part moves to the
  entry's target, the rest to the global target if there is one. Each source median is
  measured on its own side, on pure texels as above.
- **`rock`.** As the desert rock below.
- **`canopy`** and **`meshes`** (coral, shell). The colour becomes a plane on the rock grid:
  the global colour outside, the entry's inside. The canopy targets also move the tree crowns
  (see "Crowns" below). No entry carries a canopy target today: the forests take the global
  one, and the Red Jungle's crimson is keyed by species.
- **`water`** with **`water_class`.** An opaque display colour for the water of one class of
  section 33 (`swamp` for the Swamp). Under that class's share of a pixel's water it replaces
  the Beer-Lambert result by `1 - exp(-depth / water.opaque_tau_m)`, with the tau 0.3 m, so
  only the edge shows the bed. Ocean inside the area keeps the sea. A store without a class
  plane gives it the water off the ocean's reach.

No two entries scope the same material to the same area; a test checks this.

**Offshore pieces.** The game's area map gives stretches of open sea, with their islands, to
areas they do not touch: the Rocky Desert's id covers the sea north of the Spire Coast
(around (743, -2527)) and the islands off the west coast (-2655, 1506). Scoped as they stand,
the Spire Coast's outer islands would wear the desert's red-brown rock and tan sand, with a
seam through one rock mass at the area edge. So before any target is scoped, the area map on
the 4 m rock grid is rehomed (`palette/painted/calibration.py` `rehome_offshore`, from
`PaintedGround._coarse_areas`). An area's pieces are its 8-connected components; the one
holding the most land is its own. Any other piece with land under half its cells takes the
named area it shares the longest border with, else No Man's Land. Sea is water within
`OCEAN_LEVEL_BAND_M` (0.5 m) of the ocean level, any grade, and the void off the landscape; a
lake or swamp is land, so the second crater and swamp keep their targets. This holds for every
area entry. On build 502094 it moves 3.74 km² (sidecar `paint.offshore_cells_rehomed`, in 4 m
cells): Rocky Desert to Spire Coast 2.47 km², Rocky Desert to none 1.09, Red Jungle to none
0.13, Grass Fields to none 0.05. The Spire Coast has no rock target of its own, so the islands
it takes wear the default rock and their families.

**Rock.** In each entry with a `rock` target, and with the default `rock` target everywhere
else, the rock's chroma and hue are set to the target. The lightness moves by the step from
the median to the target, so it keeps its variation. Every rock is on a display target, so
the plain exposure (`rock_keeps_exposure`) applies only to a palette without a default.

The targets use the method above: the median sRGB over the top 60% of L in each box, then
L ×0.95 (capped at 0.86) and chroma ×0.9 in OKLab. Only medium- or high-confidence
references were used. A target is a screenshot measured so, or derived by a rule:

| Material | Areas | Target | Source | References |
| --- | --- | --- | --- | --- |
| Rock | Dune Desert, Desert Canyons, Rocky Desert (not Savanna) | #ae8271 | screenshot | first pass |
| Rock, desert rock family | wherever desert rock stands (see "Rock by mesh family" below) | #ae8271 | screenshot | the desert rock target above. On the desert spires: [Spires Base](https://images.steamusercontent.com/ugc/23177420036648851/71D9249104B80F4E0B96989BE624C9583A34D636/) and [Funicular base](https://images.steamusercontent.com/ugc/9849253882402225725/30B60C5F97C3A2CDF7BA66D4554AB06E246919FD/) pool to #af6f58 (ΔE 4.9), and a Dune Desert mesa wall, [Desert for Dessert](https://steamcommunity.com/sharedfiles/filedetails/?id=3360666296), reads #925e4b |
| Rock | Grass Fields, Northern Forest, Western Dune Forest | #7e7868 | screenshot | up-facing lit rock: [store shot](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/526870/ss_b1104309f1c22c85de6ad6c401e6d889411c14d2.1920x1080.jpg), [Random mode 1](https://satisfactory.wiki.gg/images/Random_Game_Mode_-_Resource_Node_Example_1.png), [cave entrance](https://satisfactory.wiki.gg/images/Entrance_Of_A_Cave.webp), [Northern Forest U8](https://satisfactory.wiki.gg/images/Comparison_2_-_Northern_Forest_-_U8.png) |
| Rock and Cliff layer | Red Jungle, Jungle Spires, Red Bamboo Fields | #877e6e | screenshot | [Jungle Spires](https://satisfactory.wiki.gg/images/Jungle_Spires.png), [Red Jungle 2021](https://steamcommunity.com/sharedfiles/filedetails/?id=2627451942) |
| Rock, default | everywhere else, the Spire Coast included | #85816c | screenshot | [Abyss Cliffs](https://satisfactory.wiki.gg/images/Abyss_Cliffs.png), [Lake Forest](https://satisfactory.wiki.gg/images/Lake_Forest.png), the store shot. Lit up-facing bare rock on the Spire Coast pools to #877b71, ΔE 2.1 from it (see "The Spire Coast rock from its own material") |
| Top layer, forest family | the up-facing faces of every `_Forest` cliff and rock, in patches | #505936 | screenshot | moss and grass on lit Spire Coast tops, six boxes pooled: [Can't beat that view](https://images.steamusercontent.com/ugc/12186521166813992372/550A8BFD145E1EC2AFAF479D7DE6B99D87CB37FD/) (the arch top and a shelf), [a Spire pillar](https://images.steamusercontent.com/ugc/54708874924571662/8337E85F52B3538ED4EC6B96D2B0281B9DC53240/), [a leaning pillar from above](https://images.steamusercontent.com/ugc/14675271398369972649/33698D99D1A66B96456520E8149422C870255F1D/), [the oil platform](https://images.steamusercontent.com/ugc/16557712698482623084/7042B540721BDD03CD7F72A2D5CD82452986D6E4/) and an unpublished 1.0 shot (see "Moss in patches") |
| Sand | the deserts and Savanna | #c4ab8b | screenshot | [Somersloop](https://satisfactory.wiki.gg/images/Somersloop_at_Rocky_Desert.jpg), [six iron nodes](https://satisfactory.wiki.gg/images/Rocky_desert_six_Iron_nodes.jpg), [Desert Canyons](https://satisfactory.wiki.gg/images/Desert_Canyons.png) |
| WetSand | the deserts and Savanna | #987b61 | derived (rule) | the entry's Sand at L ×0.80, C ×1.0, h −10 ("Wet sand by rule" above) |
| Gravel | the deserts and Savanna | #8f8373 | screenshot | Somersloop, and the gravel-to-sand ratio in Desert Canyons |
| Grass | Grass Fields | #9dad70 | screenshot | the v1.1 top-down HUB shots: [front](https://satisfactory.wiki.gg/images/HUB_Front_Overhead.png), [rear](https://satisfactory.wiki.gg/images/HUB_Rear_Overhead.png), [burners](https://satisfactory.wiki.gg/images/HUB_Biomass_Burners_Overhead.png), [freighter](https://satisfactory.wiki.gg/images/HUB_FICSIT_Freighter_Overhead.png) |
| Crowns, red Kapok (`SM_Kapok_03`) | everywhere, by species | #7c4955 | screenshot | [Red Jungle from above](https://steamcommunity.com/sharedfiles/filedetails/?id=3776654401), [Red Jungle 2021](https://steamcommunity.com/sharedfiles/filedetails/?id=2627451942); two 1.0 sources agree (ΔE 1.8 and 1.9) (see "Crowns") |
| Crowns, blue palms | everywhere, by hue | #3d627d | screenshot | [six iron nodes](https://satisfactory.wiki.gg/images/Rocky_desert_six_Iron_nodes.jpg), high-angle daylight: the blue leaf pixels (hue 190 to 290, chroma at least 0.04) of five crowns pooled, then the method above. The [Rocky Desert area](https://satisfactory.wiki.gg/images/Rocky_Desert_Area.png) crowns agree on hue (242) but are seen from below; the [river split](https://satisfactory.wiki.gg/images/Rocky_Desert_river_split.png) crowns are backlit. A 1.0 shot gives #556b85 (ΔE 4.4); not derivable, as a third of the leaf is midribs and the fronds are sparse |
| CoralRock layer | Blue Crater, Crater Lakes | #6c7386 | screenshot | [crater ground at noon](https://steamcommunity.com/sharedfiles/filedetails/?id=3372405479) |
| Shell meshes (the pale plates) | Blue Crater | #747b85 | screenshot | [Blue Crater aerial](https://steamcommunity.com/sharedfiles/filedetails/?id=3579556500), [Blue Crater](https://satisfactory.wiki.gg/images/Blue_Crater.png), the noon crater shot |
| Water, opaque | Swamp | #302627 | screenshot | 1.0 views from above, see section 33, "Sea deep and swamp water" |

Two readings in these references:

- Rock outside the deserts is a near-neutral warm grey (C 0.02 to 0.03), not the green the
  ground tint gave it. Lit tops that face up read warm (h 80 to 100); vertical and hazy faces
  read blue-grey from sky light, which a top-down map does not show.
- The Rocky Desert rock in its own references is grey, not the Dune Desert red-brown, so
  Savanna is kept out of the desert rock.

### Rock by mesh family (2026-10-06)

Rock takes its colour from the mesh it is before the area it stands in. `calibration.families`
names a target per rock family (`gamedata/rocks/families.py` `FAMILIES`), and a family named there
wears its target wherever it stands, over any area entry's rock. One is named: the desert rock
family, with the desert rock target #ae8271.

**Why.** The desert spires of the Spire Coast, the mesas at x 1,450 to 2,250 and y -3,200 to
-1,650, are desert rock. Every rock there is an `SM_DesertRock_*` mesh in its own
`MI_DesertRock_*` material, the same set as the Dune Desert's. Their parent `MI_DesertRock`
carries a `Rock Base Color` of linear (0.890, 0.549, 0.384), sRGB #f2c4a7, a salmon. The Spire
Coast's grey pillars are cliff meshes: `CliffPillar`, `CliffFormation` and `CliffCone` in
`_Forest` and `_WetSand` instances of the Cliff master, whose `Color Tint` is the near-neutral
#cfc3b6. The game's area map gives the mesas to the Spire Coast, and the western one to an
offshore piece of the Rocky Desert that the rehoming above moves to the Spire Coast. Coloured
by area they would wear the Spire Coast's grey; the references show them red-brown (the table
above).

**The family.** `MI_DesertRock` (in `Rock/DesertRock/Material/`) roots the family `desert`,
code 8 in the family plane. On build 502094, 5,907 placements are desert rock, all in the Dune
Desert and the desert spires. The family has no `Color Tint` or top layer to read
(`TARGET_ONLY`), so the paint store does not record it. The family plane carries the code, so a
direct cache cut before it is rasterised again (the `rock_families` reader version), and a
`--restyle` refuses one until then.

**Drawing.** `PaintedGround.attach_families` takes the direct pass's family plane and reads its
code at each 4 m cell of the rock grid (`surfaces.family_cells`). A family's rock grid takes
its target's chroma and hue. Its lightness moves by the step from the median of the family's
own cells to the target, as an area target's does (`family_targets`). Per pixel, rock of that
family takes its family's rock in place of the area's (`rock_surface`). The sidecar records
`paint.rock_family_targets`: on build 502094, 247,002 cells and a lightness step of -0.059.
The offshore rehoming stays: the islands it moved to the Spire Coast, at (743, -2527) and the
islets at (269, -1943), are cliff pillars and stay grey.

**Measured** on windows of the full-size grid (0.229 m to the pixel), the median of rock
pixels as drawn: the western, middle and eastern mesas draw #b88b79, #b78a78 and #ac8170, L 0.64
to 0.68, C 0.06, h 43, on the target, lit and lifted by their height. The Dune Desert's desert
rock draws #aa7f6e. The Spire Coast islets, the spiral and the Northern Forest keep their
cliff families' colours.

### The Spire Coast rock from its own material (2026-10-06)

The Spire Coast has no rock target of its own. Measured on Steam shots of the coast in
daylight, its rock falls in three pools: lit up-facing bare rock #877b71, moss and grass on the
tops #505936, and shaded faces #3a3935. The default rock #85816c is ΔE 2.1 from the lit bare
rock, so the coast takes the default. A single box on a backlit vertical face in shade, which
once gave the coast a charcoal target, measures the third pool.

**The pillars' own material.** The grey pillars are `Cliff` meshes in `_Forest`, `_WetSand`
and plain instances of the Cliff master, with no tint of their own: in the lagoon box (x -600
to 1,100, y -2,900 to -1,600) 371 of 435 placements are `_Forest`. `Cliff_Forest`'s static
switches turn on "Blend Material on Top", `UseForestGrass` and "Use Far Albedo", with
`TX_Forest_Far_01_Alb` as the far albedo: a forest-floor top layer on the up-facing faces,
which the heightfield cliffs draw (section 30, "Rock surfaces"). `Cliff_WetSand` switches its
top layer on too, but overrides no texture, so its top is the cooked master's default, which
is not readable; it stays bare.

**Render-only rocks.** `CliffPillar_03` is passable in game, so it stays out of the heightfield
and is drawn by the render-only mesh pass (section 27). That pass records each rock's family
as the direct pass does: the placement's override material, else the mesh's own, walked up to
a `Cliff_<Layer>` root (`gamedata/rocks/families.py` `worn_family`). `rasterise_mesh_band`
carries it above the class in each instance's source code (`MESH_FAMILY_SHIFT`), and the cache
holds it as a family plane, `meshes.family.u8`, beside the class plane. `mesh_surface` draws a
rock pixel through `rock_surface` with that family, so it takes the family's tint and its top
layer on the up-facing faces exactly as a cliff does; a rock with no family keeps the area's
rock. On build 502094 the 376 `CliffPillar_03` placements are 137 plain cliff, 128 grass, 57
forest, 21 red jungle, 25 sand, 5 red grass and 3 with no family; all 32 lagoon stacks are
forest. Those counts read the mesh from its own package in `Rock/Cliff/Mesh/` (render meshes
reader version 4, rock families reader version 3), not the unused copy in `Mesh_Old`, which
has another shape and no material of its own.

### The land rules, measured (2026-10-06)

Windows of the full-size grid (0.229 m to the pixel, 240 to 300 m across) drawn in-process
through `render_layer`, with the fifth render's raster caches, the paint store, and no artwork
detail and no open sea. Values are the median of the pixels named, display sRGB, and ΔE is
OKLab ×100. The rock tops are measured in "Moss in patches".

| Window | Pixels | Drawn | Against the reference |
| --- | --- | --- | --- |
| Lagoon stacks (203, -2515) | render-only rock, steep faces | #616053 | lit bare rock #877b71: 10.6 |
| | cliff rock, steep faces | #89856f | #877b71: 3.1 |
| Spiral (-339, -2275) | cliff rock, steep faces | #6f6e5c | #877b71: 6.0 |
| Spire spit (243, -2078) | dry WetSand 0 to 4 m from the water | #8f8372 to #948878 | the rule, L ×0.80, h −10: L ×0.72 to 0.74, h −6 to −8 |
| Spire shore (269, -1943) | dry WetSand 0 to 4 m | #958877 to #998d7c | L ×0.74 to 0.76, h −6 to −8 |
| | dry WetSand 4 to 60 m | #9d917f | L ×0.78, C ×0.91, h −5 |
| North beach (-58, -1507) | Sand 0 to 1 m from the water | #c2a886 | L ×0.97 of the dry sand #cab08d; no WetSand above the drawn sea |
| Western Dune Forest (-1239, 1305) | crowns | #548552 | #558653: 0.3 |
| Jungle Spires (-1696, 936) | crowns | #558552 | #558653: 0.3 |
| Red Jungle (-1036, 237) | red Kapok crowns | #7b4854 | #7c4955: 0.3 |
| | other crowns (bamboo) | #c7616e | their own texture colour, as in the Red Bamboo Fields |
| Red trees in the Jungle Spires (-650, 700) | red Kapok crowns | #7b4854 | #7c4955: 0.3 |

The canopy's global step is +0.037 in lightness and ×1.141 in chroma over 26,000 trees; the
red Kapok's own is −0.035 and ×0.646 over its 1,324 trees. The wet sand's derived targets reach
the bed under shallow water too, as the bed is the ground seen through the water.

### Moss in patches (2026-10-06)

On the Spire Coast's lit tops the screenshots show moss and grass on 14 to 66% of the face, in
patches with bare rock between them, pooled #505936. So the top layer lies in patches inside
section 30's up-facing ramp, and the forest top wears that colour. A solid lid in the forest
texture's mean draws #58713d on flat ground, ΔE 7.5 from it.

**No mask from the game.** Where the top shows is decided inside `CliffTopMaterial`, the
Cliff master's top-layer function. It is cooked into the master, so its mask cannot be read
(section 30, "Rock surfaces"). The patches are a rule drawn by the style and tuned to the
screenshots' share, not game data.

**The mask.** `palette/painted/surfaces.py` `top_cover` multiplies section 30's up-facing ramp
by a patch mask from `rock_top.patches`. The mask's noise is `patch_noise`: value noise on a
lattice in world metres from the frame's corner, each lattice point valued by a 64-bit hash
of its indices and the seed, blended by smoothstep. Three octaves of 16, 6 and 2.5 m weigh
0.4, 0.35 and 0.25 (`octaves_m`, `seed` 5). Each pixel samples the noise at its own centre,
so a point of the world draws the same in every band, tile, window and sheet size, and no
texture is stored. Flatness shapes it: the noise is lowered by up to `flat_gain` 0.1, all of
it at `nz` 0.85 and none from 0.97 up (`flat`), so flatter faces carry more moss. The mask is
that noise against `level` 0.5, through an edge `soft` 0.06 wide. On flat faces that is moss
on 49% of the rock, 26 to 72% in 30 m boxes (10th to 90th percentile); at `nz` 0.85 it is
21%, 6 to 40% in 30 m boxes. The mask applies wherever the top does: on the heightfield
cliffs by the direct pass's family plane, and on the render-only rocks by their own family
(`mesh_surface`).

**The forest top.** `calibration.tops` names a display target for a family's top, made a
ground colour as the other display targets are (`top_targets`, applied by `family_tables`).
Only the forest family has one, #505936, from the Spire Coast boxes of the target table
above. The grass, red grass and sand tops keep their textures' means, in the same patches.

**Measured** as in "The land rules, measured", on windows 280 m across. The pixels are
forest-family rock facing up (`nz` above 0.85), clear of water, crowns and canopy, with the
heightfield cliffs (rock weight at least 0.99, no mesh) and the render-only rocks counted
apart. The moss share is the share of them with a top weight of at least 0.5; "moss" is the
median of those at least 0.9 moss.

| Window | Pixels | Moss share | All tops | Moss | Moss ΔE to #505936 |
| --- | --- | --- | --- | --- | --- |
| Lagoon stacks (203, -2515) | cliff tops, 13,253 | 0.523 | #707157 | #4e5734 | 0.7 |
| | render-only rock tops, 6,184 | 0.384 | #7c7861 | #4e5734 | 0.7 |
| Spiral (-339, -2275) | cliff tops, 35,848 | 0.343 | #837f69 | #4f5835 | 0.4 |
| Lake Forest (298, -557) | cliff tops, 123,793 | 0.353 | #7f7b65 | #535c38 | 1.1 |
| | render-only rock tops, 3,070 | 0.308 | #86826c | #555e3b | 1.7 |
| Spire cliffs (357, -1670) | cliff tops, 189,822 | 0.452 | #7f795f | #515a37 | 0.4 |
| | render-only rock tops, 68,450 | 0.514 | #767259 | #535c39 | 1.1 |

In 30 m boxes the cliff tops carry 14 to 72% moss in the Lake Forest (10th to 90th
percentile of 15 boxes) and 26 to 67% at the Spire cliffs (25 boxes), against the
screenshots' 14 to 66%. A window's share is below the flat 49% because most of its tops are
short of `nz` 0.97. Between the patches the tops show the area's rock as drawn: #85816c in
the Lake Forest, the default target, #86826d to #8e8a74 in the two Spire Coast windows, and
#9a8472 at the Spire cliffs, which stand on the Desert Canyons' edge.

**Cost.** On one full-width band of the 32768 sheet the mask adds 0.43 s where 2.5% of the
pixels carry a top, and 1.0 s where 10 to 26% do: 55 to 133 s over the sheet's 128 bands.

### Other colours

- **Meshes.** Coral-tree caps are #99868e, a display target, so the gain does not brighten
  it. Seabed coral #5f8899 is a bed colour, as in the water fit, so it is also divided by the
  0.8 wet factor. It is composited into the bed before Beer-Lambert, with the depth measured
  to the coral top, so shallow reefs stay visible: #628b9b at the surface, #5a8286 at 0.5 m.
- **Which coral is under water.** Coral takes the seabed colour by the pixel's water cover,
  never by `depth_m > 0`, which off the ocean's reach is the depth fraction of dry land.
- **Coral specks.** The mesh raster takes one sample per pixel, so coral narrower than a
  pixel standing in the sea fills a whole 3.66 m preview pixel and drew as a pink dot. A
  coral pixel whose eight neighbours are at least 60% water (`SPECK_WATER`) is drawn as that
  water, at their mean depth, with the coral as its bed. Coral wider than a pixel keeps its
  cap colour, and so does coral on land: the Spire Coast's coral trees stand a median 23 m
  above the sea and are the caps the #99868e target was measured on. At 2048 px this sinks
  823 coral pixels of the sheet, 260 of them in the Spire Coast crop at (16, -2137). The
  specks left in the Spire lagoon at 2048 are coral pieces 2 to 75 px across standing a median
  6.5 m out of the water, and boulders and ground a median 0.33 m over the sea's level: game
  data, kept.
- **Shells** (`SM_BigShell_01`, `PlateauShell`, `SmallShell`: everything the `Shell` class
  takes) wear their own material's colour: the mean of their BaseColor textures in linear
  light is 0.08 to 0.11 neutral grey, kept as the albedo sRGB (88, 85, 83). Their materials
  also carry a cyan emissive, not drawn. The Blue Crater keeps its #747b85 target.

### Crowns

With crowns drawn the soft canopy is off (`canopy_kept` 0), so the canopy targets move the
crowns instead (`palette/painted/trees.py` `crown_ops`, applied in `crown_layer`):

- **Scopes.** Each area entry with a `canopy` target is one, holding the trees where its area
  share is at least 0.5; the global `canopy` target holds the trees no entry does.
- **Source.** The per-channel weighted median of the scope's crown colours as drawn: the
  texture mean times `darkening`, at the style's `chroma`. A tree counts by the ground its
  crown hides, its species' sprite cover times its scale squared.
- **Step.** As for the layers: a lightness step, a chroma scale and a hue turn to the
  target, taken back through the flat light without the altitude lift a crown does not get
  (`display_to_crown`). Per pixel the ops are mixed by the area weights on the 4 m grid.
- **Hue gate.** A target was measured on canopy of one hue. A crown takes all of its scope's
  step within 20 degrees of the target's hue and none past 40 (`HUE_GATE_DEG`), scaled by its
  chroma from none at grey to all at 0.02 (`CANOPY_GREY`); the source median is taken over the
  gated crowns only. Pink bamboo, coral trees and the yellow pines of the Northern and Lake
  Forests keep their texture colours; the blue palms take a target of their own (below).
  Without the gate one step over every species drew the Red Bamboo orange: a per-channel
  median over mixed hues has almost no chroma, so the global scale came out x1.7.
- **Named crown targets.** `calibration.crowns` names targets for crowns that no canopy target
  was measured on. Each is one more scope over every tree on the map, gated by its own hue, so
  it moves the crowns of that hue wherever they grow. Its gate opens only past the grey line:
  none under chroma 0.02, all from 0.025 (`TARGET_GREY`). The canopy gate keeps its ramp from
  0 (`CANOPY_GREY`), which the Orange palms at 0.020 sit on. So the near-grey balloon tree
  `SM_BalloonTree_02_T` (chroma 0.012, the palms' hue) keeps its colour, as do its crown edges
  against the purple tree (0.016 to 0.019). Each op is gated on the crown's colour before any
  op; the canopy and palm gates do not overlap. The blue palms' target is #3d627d: 3,332
  trees, step -0.285 and ×1.51 (sidecar `crowns@blue_palm`).
- **Species targets.** `calibration.species` names a target per tree species, for a colour
  that belongs to the tree rather than to the place. `species_targets` (`trees.py`) returns
  every species' sprite mips with each named species moved, every texel, from its own colour
  as drawn onto its target, and `PaintedGround` takes those mips before the scopes are
  measured, so the hue gates that follow see the moved colour. One is named: the red Kapok
  `SM_Kapok_03`, on #7c4955, 1,324 trees, step -0.035 and ×0.646 (sidecar
  `species@SM_Kapok_03`).

**Why by species.** The red Kapok grows outside the Red Jungle too: 68% of its crowns stand
there, but also 32% of the Rocky Desert's, 22% of the Red Bamboo Fields' and 19% of the Jungle
Spires'. Keyed by area, the red Kapok in the Jungle Spires around (-650, 700) falls to the
green target's scope, which its hue gate refuses, and draws its texture mean #994946, ΔE 6
from the crimson. Its crimson is real (two 1.0 sources at ΔE 1.8 and 1.9; not sub-surface
light, which reads coral, and not haze, which reads mauve from afar). Other red-leaved species
are not named: the research names only the red Kapok, so the pink bamboo, the mangroves' bark,
the cat palms, the Dypsis palms and the Diospyros keep their texture colours, the bamboo in the
Red Jungle as in the Red Bamboo Fields. The Western Dune Forest and the Jungle Spires take the
global canopy target: their crowns are 86 to 90% `SM_Kapok_01`, they have no grading volume
of their own, and 1.0 Kapok canopy ranges h 127 to 152. The Early Access wiki images that once
gave them targets of their own predate Lumen.

### Measured

Crops of the z7 grid through `render_layer`, with a scratch extraction of the bake as the
`GroundBake`. Each value is the median of the material's pixels: for layers, texels with at
least 0.7 of the weight, more than 1 m above the water and with no rock, mesh or canopy
cover. Values are ΔE (OKLab ×100) to the target, and to the screenshot reference in
brackets:

| Material | Crop centre (m) | Uncalibrated | Bake | No bake |
| --- | --- | --- | --- | --- |
| Dry sand | forest (-1295, 826) | 10.9 (15.0) | 0.3 (4.2) | 1.2 (3.5) |
| Dunes | Dune Desert (2700, -1700) | 7.3 (8.2) | 0.2 (4.6) | 1.4 (3.7) |
| Canopy | forest (-1295, 826) | 13.6 (16.6) | 1.4 (2.7) | 2.2 (2.4) |
| Desert rock | desert lake (3125, -674) | 1.8 (4.7) | 0.4 (3.1) | 0.5 (3.0) |
| Coral-tree cap | Spire Coast (-339, -2275) | 5.6 (3.4) | 3.2 (6.5) | 3.2 (6.5) |

The distance to the reference is the deliberate discount for the game's tonemap and grade,
which the targets remove. The coral caps sit 3.2 below target, lit by their slope; their
chroma and hue are within 0.5. They are not domes: a coral tree is a stack of shallow bowls
(section 36, "Coral trees are no crowns"). Wet sand and grass had no dry, pure patches in
these crops. On the Spire Coast the wet sand is under water, as the references show.

The area targets were measured flat-lit on the paint table (no bake): each material's pure
texels pushed through the flat-ground pipeline, the median in OKLab against the target. Rock
lands within 0.1 to 1.2 ΔE of its targets, Grass Fields grass 2.0, desert sand 2.2 and gravel
1.7, the Cliff layer 1.4, the craters' CoralRock 1.3 and the shell plates 0.0. What is left on
the layers is mostly the biome tint, which is added after the transfer.

### Targets derived from the game install (2026-10-07)

`python -m mapgen calibrate` derives a display colour for every calibration key from the
game's own data, through a model of the game's camera, and writes them to
`targets.derived.json` beside the paint store. A palette names the keys a render takes from
there in `calibration.derived_keys`; every other key keeps its screenshot target. Code:
`palette/painted/derive/` (`camera.py`, `scene.py`, `rules.py`, `targets.py`, `palette.py`),
`commands/calibrate.py`, and the readers in `gamedata/level/curves.py` and `lighting.py`.

**Inputs.** The paint store, from generator 4 on, keeps three more blocks in its `meta.json`,
read by the same level walk:

| Block | What | Read from |
| --- | --- | --- |
| `lighting` | the sun's colour and lux at noon, its pitch, the sky's luminance factor, the auto-exposure | `Persistent_Level`: `BP_Sky_Sphere_C.mSunLightColorCurve` and `mSunIntensity` at 12:00, `LightSource_0`'s `RelativeRotation` pitch, `SkyAtmosphereComponent.SkyLuminanceFactor`, `GlobalPostProcess`'s `AutoExposureBias`, `LowPercent`, `HighPercent`, `MinBrightness` and `MaxBrightness` |
| `atmosphere_volumes` | every `FGAtmosphereVolume` that overrides the sun or the grade: its `mPriority`, each enabled curve at noon, and its brush seen from above (the convex hull of its `BodySetup`'s convex vertices, world metres) | the levels the walk reads |
| `mesh_materials` | the shell materials' mean linear colour under their masks | `MI_Bigshell_01`, `PlateauShell_Inst`, `SmallShell_Inst` |

Build 502094: the sun (1.000, 0.928, 0.714) at 3.14 lux, 59.49° up; the sky factor (1.127,
1.127, 1.300); bias +1.5 EV over the 75 to 95% band. Three volumes: the Dune Desert
(priority 3: sun (1.000, 0.880, 0.658) and a warm grade), its Southern Oasis (priority 4) and
a cave test volume (priority 7). A volume's sun is 4 lux; the model leaves the brighter sun to
the auto-exposure and takes only its colour and grade. The command also reads the install's
area map and the heightfield's water, to scope the area entries on the painter's 4 m grid,
rehomed offshore as in "Area targets" above.

**The camera model.** A flat, lit patch of albedo `a` is measured as

    measured = discount(OKLab(filmic(grade(E · a · I))))
    I = sun lux / π · sun colour · (sin(elevation) · T_atm + sky · SkyLuminanceFactor)
    E = 0.18 · 2^bias / mean luminance of the 75–95% band of the lit bake

- `filmic` is UE5's default film curve (slope 0.88, toe 0.55, shoulder 0.26, black 0, white
  0.04), with its blue correction 0.6 and gamut expansion 1, in AP1.
- `T_atm` and `sky` are the engine's default SkyAtmosphere, single scattering, integrated
  numerically: 0.931, 0.848, 0.730 and 0.024, 0.044, 0.082 at 59.5°.
- `grade` is a volume's `ColorCorrectAll` gains only (shadows under luma 0.09, highlights from
  0.5). Its gamma, contrast and saturation are 1 on this build; the command names any that
  is not.
- `discount` is the screenshot method's own measurement (L ×0.95 capped at 0.86, C ×0.9), so
  a derived colour and a screenshot target are measured alike.
- `E` is 2.8067 on build 502094, over 2.24 million lit bake texels at 4 m.

Every product is written out in a fixed order. The model has no fitted parameter.

**The light of a key.** Each sample a key is measured on (a texel, or a tree) takes the
highest-priority volume whose hull holds it, else the level's light; the key takes the light
most of its samples took (at most 20,000 samples, drawn with a fixed seed). A key with no
place, such as a texture mean, takes the level's light. The desert rock takes the Dune
Desert's: most of its placements stand inside that volume.

**The rules.**

| Kind | Keys | Albedo |
| --- | --- | --- |
| bake median | layers, derived layers, area layers | the OKLab median of the bake over the layer's pure texels (at least 0.7 of the blend, at least 50) in the key's scope: inside an area entry's areas, or outside every entry that targets the layer |
| paint table | an untargeted layer with too few pure texels | the layer's texture mean times its material vector |
| cliff texture × tint | `rock`, an area's rock | the mean of `Cliff_Macro_Alb_02` and `Cliff_Detail_Alb` times the cliff family's `Color Tint` |
| sand-rock | `families.desert`, an area rock wearing its target | the DesertRock paint-table entry, `TX_SandRock_Alb_01` times `Sand Rock BaseColor`; `MI_DesertRock` has no albedo texture of its own |
| top texture | `tops` | the family's far albedo mean |
| crowns | `canopy`, `crowns`, `species` | the weighted median of the trees' top-down sprite colours (cover × scale²); the canopy takes only crowns within 20 to 40° of the forest floor texture's hue and leaves out the keyed species and the blue palms |
| material texture | `meshes.coral`, an area's shells | the materials' base-colour means under their masks |
| none | water, seabed coral | the game's water absorbs but does not scatter, so its colour is the sky, the clouds and the fog, which the cooked assets do not expose |

**Results on build 502094.** ΔE is OKLab ×100 against the screenshot target; `*` marks the
keys `derived_keys` lists.

| Key | Derived | Screenshot | ΔE | Light |
| --- | --- | --- | ---: | --- |
| * layers.Sand | #d7c1a2 | #d5cbb6 | 3.0 | global |
| layers.SandRipples | #d98d64 | #ca784f | 6.0 | Dune Desert |
| * layers.Grass | #7f8d4e | #83986e | 4.4 | global |
| * layers.Forest | #484d26 | (the canopy's #558653) | 17.3 | global |
| * derived.WetSand | #b38c61 | #a29583 | 4.7 | global |
| * canopy | #5e8136 | #558653 | 3.2 | global |
| * rock | #897758 | #85816c | 3.2 | global |
| * families.desert | #c38761 | #ae8271 | 4.5 | Dune Desert |
| * tops.forest | #54662b | #505936 | 4.7 | global |
| * meshes.coral | #9f7e75 | #99868e | 3.6 | global |
| crowns.blue_palm | #bec7c1 | #3d627d | 34.6 | global |
| species.SM_Kapok_03 | #ad4a37 | #7c4955 | 10.3 | global |
| desert entry: Sand | #d7c3a5 | #c4ab8b | 7.2 | global |
| desert entry: WetSand | #b48d61 | #987b61 | 7.1 | global |
| * desert entry: Gravel | #9e896f | #8f8373 | 3.1 | global |
| * desert rock entry | #c38761 | #ae8271 | 4.5 | Dune Desert |
| * Grass Fields etc.: rock | #897758 | #7e7868 | 2.6 | global |
| Grass Fields: Grass | #7d8f50 | #9dad70 | 10.0 | global |
| * Red Jungle: Cliff | #7c6f5b | #877e6e | 4.9 | global |
| * Red Jungle: rock | #897758 | #877e6e | 3.0 | global |
| crater: CoralRock | #82838e | #6c7386 | 5.8 | global |
| Blue Crater: shell | #7b705b | #747b85 | 6.0 | global |

The layers no key targets get a derived colour too: GrassRed #c09872, Puddles #624f37,
RedJungle #ae6451, SandCracks #d1845c (Dune Desert light), SandPebbles #bfaa85, SandRock
#bd7551 (Dune Desert light) and Soil #896e4e, all from the bake; DesertRock and PurpleForest
have no pure texels and fall back to their paint-table entries.

**Which keys take the derived colour.** Those within ΔE 5 of their screenshot target, the
desert rock under the Dune Desert's light, the Forest floor (it had no screenshot target, only
the canopy's as a stand-in), and the seven layers with no target at all. The rest keep their
screenshot: the blue palms (the leaf texture is a pale blue-grey and the model has no foliage
sky term), the red Kapok (no subsurface light), every water colour, and the keys between 5 and
10 until the in-game checks their verdicts list are done. A render with derived colours
carries the merged palette's digest, so a different install's derivation is a different style.

**The file and the run.** `targets.derived.json` holds each key's colour with its rule, light,
light shares, albedo, assets and sample count, and a stamp: the paint store's digest, the
digest of the area map on the 4 m grid, the calibration block's digest without its `about`
and `derived_keys`, and the model version. A render reads the file while its stamp holds and
derives in its own run otherwise, about 6 s; a store from before generator 4 keeps every
screenshot target and the sidecar says why (`sources.paint.derived_targets`). The command
reads the install and writes nothing but the file; `--check` prints the table and writes
nothing.

**Tests.** `tests/fixtures/calibration_screenshots.json` keeps the screenshot targets with
their sources. A scored key must lie within ΔE 8 of its target, or within its own tolerance,
its measured distance plus 1 (Grass Fields grass 11, the red Kapok 11.5); the blue palms are
not scored. The integration test checks them on a real store with `E` within 2% of 2.81.

**Limits.**

- Not checked against an engine render: the film curve and the exposure follow UE's source,
  and E = 2.81 against a free fit of 2.87 may be partly luck.
- Noon only, and single scattering only. The volumes are voted by their hull in plan at the
  ground; the game blends them by camera position and `mBlendDistance`.
- No local exposure: metering on the view a screenshot sees would bring the Grass Fields
  grass from ΔE 9.9 to 2.5 but the crater's CoralRock from 5.8 to 17.0, and the view's
  footprint would be a free parameter.
- SandCracks (L 0.685) stays lighter than the screenshot SandRipples (L 0.653), though the bake
  has it darker; deriving SandRipples too (L 0.712) would restore the bake's order.
- The Dune Desert volume's hull also covers the western mesas and 40% of the Spire Coast.

### Known limits

- No screenshot target, for want of a clean reference: SandRock (Dune Desert and Spire
  Coast), SandPebbles, SandCracks, DesertRock, Soil (forest floor, swamp mud, Titan Forest),
  Puddles, the Red Jungle and Red Bamboo ground (RedJungle_LayerInfo, also in Crater Lakes),
  red grass, the Forest floor, the jungle floor sand, the swamp canopy, the Abyss Cliffs Cliff
  layer, gravel outside the deserts, and the moss on the Titan Forest formations. The layers
  among them take their derived colour where the store has daylight ("Targets derived from
  the game install"); without it, Forest_LayerInfo takes the canopy target, a vivid treetop
  green, and the others keep the bake's colour.
- The Grass Fields grass target comes from one place, seen in four v1.1 shots; the biome
  is inferred from the flowers. Grass elsewhere keeps the global target.
- The patches of the top layer are a rule, not the game's mask, which is cooked into
  `CliffTopMaterial` ("Moss in patches"). Only the forest top has a colour measured from
  screenshots; the grass, red grass and sand tops keep their texture means, in the same
  patches. The `_WetSand` instances' top layer is the cooked master's default and is not
  drawn; whether it shows in game is unchecked.
- The red Kapok is crimson wherever it grows, the Rocky Desert and the Red Bamboo Fields
  included, by the Red Jungle's references. Its crowns there were not measured from above.
- On a beach whose paint has no WetSand above the drawn sea, as at the north beach, the
  shore's band is the only wet cue and reads L ×0.97 in its first metre, against ×0.80 in
  game.
- The rocks on the North Beach lagoon islands, and the cliff foot and boulders along the north
  beach around (128, -1500), sit inside the main piece of `Area_DesertCanyons`, so the
  offshore rehoming leaves them, and they keep the desert rock, though the wiki and a 2022
  editor view show them grey. None of them is desert rock, so the desert family does not touch
  them: the cliff foot is cliff meshes (`CliffPillar_01` to `_07`, `CliffFlat_02` and `_03`,
  `CliffFormation_04` and `_05`) in plain, `_Forest` and `_Grass` instances, and the boulders
  are foliage `SM_Boulder_04` and `SM_Boulder_02` in their own materials. It is the desert
  entry's area target that reddens them. For the cliff foot to follow its own meshes, that
  entry's rock would have to skip the cliff families, which take the default #85816c instead.
  That would also grey the 561 cliff placements of the Desert Canyons and the 1,363 of the
  Rocky Desert, so it is left undone. The boulders wear the same materials everywhere, with no
  override, so following their mesh would give them one colour across the map, the Dune Desert
  included.
- Boulders, arches, and rubble and rock piles whose material roots no family keep the area's
  rock. On the mesas, which the area map gives to the Spire Coast, a few of them stay grey
  (the default rock) on the red-brown rock.
- The Red Bamboo Fields and Red Jungle lakes keep the sea fit: their reference water
  reflects a purple sky, which is not the swamp's look.
- Every target comes from tonemapped perspective screenshots; only the Grass Fields grass
  is from a top-down view.

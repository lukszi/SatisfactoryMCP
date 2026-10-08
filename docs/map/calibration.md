# Colour calibration of the game-painted style

Section 31 of the [design spec](../../DESIGN.md): the targets the game-painted style is calibrated to,
and how they were measured. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 31. Colour calibration of the game-painted style (2026-10-05)

The painted style is calibrated against in-game screenshots: first the Spire Coast, the Dune
Desert, the Western Beaches and the Eastern Dune Forest, then a second pass over the biomes
those left out (see "Area targets"). Code: `palette/painted/ground.py` builds the ground once
per run and `band.py` draws each band, with `calibration.py`, `transfer.py` (the layers onto
their targets), `trees.py` (crowns), `optics.py` (water) and `surfaces.py` (rock and meshes). Numbers: the `tone` and `calibration` blocks of
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
`calibration.derived_keys` lists from the game install instead, where the derive gate passes
them ("Targets derived from the game install" below), these six among them.

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

**The blend at a layer's edge** (2026-10-07, `palette/painted/transfer.py`). A texel at a
layer's edge carries part of that layer's weight but much of the colour of the layer beside
it, and every layer's step acted on all of that colour. Two things followed:

- A step that scaled chroma up multiplied the neighbour's colour. In the Red Jungle and the
  Red Bamboo Fields the `Cliff_LayerInfo` source is near grey (chroma 0.005) and its target
  scaled chroma x3.95 with a 31° turn: every edge with the red `RedJungle_LayerInfo` drew a
  fire-red rim, and the bake's grey noise turned into coloured confetti.
- The bake draws a forest patch's edge sharper than the weight maps blur it, so a texel with
  the sand's colour can carry half a forest floor's weight. The forest floor's step (L +0.09,
  x1.81) then lifted that sand, and the sand's own (L -0.03, chroma x0.41) reached it only by
  half: a bright orange halo round every forest patch on sand.

So `layer_op` never widens chroma: a step whose scale would pass one turns the hue only and
adds the chroma it lacks as a shift, `target - turn(source)`, and the source still lands on
its target. Around a source under chroma 0.02 (`GREY_SOURCE_CHROMA`), which has no hue to
turn, the step is that shift alone. A narrowing step is unchanged. On build 502094 four steps
widened: the forest floor (x1.81), the desert gravel (x1.27, grey source), Grass Fields grass
(x1.05) and the red areas' cliff layer (x3.95, grey source); their pure texels keep their
spread instead of growing it.

Where calibrated layers of two paint layers share a texel, `layer_transfer` splits the summed
weight of its two largest again by where its colour lies between their source medians in
OKLab, `u`, 0 at one and 1 at the other, clipped (`by_colour`). The colour decides by
`4 w_i w_j / (w_i + w_j)^2`: all of it where the two weights are even, none where one of them
fades out, so the split stays continuous where a layer's weight ends. The sand-coloured texel
under half a forest floor's weight is then sand, and a colour halfway is half of each. It
then adds, for each two of them,
`w_i w_j / W (A_i - A_j)(s_i - s_j)`, with `A` an op's matrix, `s` its source median and `W`
their summed share, so a mix lands on `sum w t` plus the blended matrix applied to its
departure from `sum w s`: between the targets. A texel of one calibrated layer beside
uncalibrated ones, or of two scopes of one layer (`Sand_LayerInfo@0` and `Sand_LayerInfo`),
is moved by its weights alone, as before.

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
| Rock | Dune Desert (Desert Canyons and Rocky Desert until 2026-10-08) | #ae8271 | screenshot | first pass |
| Rock, desert rock family | wherever desert rock stands (see "Rock by mesh family" below) | #ae8271 | screenshot | the desert rock target above. On the desert spires: [Spires Base](https://images.steamusercontent.com/ugc/23177420036648851/71D9249104B80F4E0B96989BE624C9583A34D636/) and [Funicular base](https://images.steamusercontent.com/ugc/9849253882402225725/30B60C5F97C3A2CDF7BA66D4554AB06E246919FD/) pool to #af6f58 (ΔE 4.9), and a Dune Desert mesa wall, [Desert for Dessert](https://steamcommunity.com/sharedfiles/filedetails/?id=3360666296), reads #925e4b |
| Rock | Grass Fields, Northern Forest, Western Dune Forest | #7e7868 | screenshot | up-facing lit rock: [store shot](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/526870/ss_b1104309f1c22c85de6ad6c401e6d889411c14d2.1920x1080.jpg), [Random mode 1](https://satisfactory.wiki.gg/images/Random_Game_Mode_-_Resource_Node_Example_1.png), [cave entrance](https://satisfactory.wiki.gg/images/Entrance_Of_A_Cave.webp), [Northern Forest U8](https://satisfactory.wiki.gg/images/Comparison_2_-_Northern_Forest_-_U8.png) |
| Rock and Cliff layer | Red Jungle, Jungle Spires, Red Bamboo Fields | #877e6e | screenshot | [Jungle Spires](https://satisfactory.wiki.gg/images/Jungle_Spires.png), [Red Jungle 2021](https://steamcommunity.com/sharedfiles/filedetails/?id=2627451942) |
| Rock, default | everywhere else, the Spire Coast included | #85816c | screenshot | [Abyss Cliffs](https://satisfactory.wiki.gg/images/Abyss_Cliffs.png), [Lake Forest](https://satisfactory.wiki.gg/images/Lake_Forest.png), the store shot. Lit up-facing bare rock on the Spire Coast pools to #877b71, ΔE 2.1 from it (see "The Spire Coast rock from its own material") |
| Top layer, forest family | the up-facing faces of every `_Forest` cliff and rock, where the cliff master's mask puts the top | #505936 | screenshot | moss and grass on lit Spire Coast tops, six boxes pooled: [Can't beat that view](https://images.steamusercontent.com/ugc/12186521166813992372/550A8BFD145E1EC2AFAF479D7DE6B99D87CB37FD/) (the arch top and a shelf), [a Spire pillar](https://images.steamusercontent.com/ugc/54708874924571662/8337E85F52B3538ED4EC6B96D2B0281B9DC53240/), [a leaning pillar from above](https://images.steamusercontent.com/ugc/14675271398369972649/33698D99D1A66B96456520E8149422C870255F1D/), [the oil platform](https://images.steamusercontent.com/ugc/16557712698482623084/7042B540721BDD03CD7F72A2D5CD82452986D6E4/) and an unpublished 1.0 shot (see "The top layer's colours") |
| Rock, arches and boulders | the top pass, everywhere | #8a8671 | derived (bake) | the default rock at 1.088 times its luminance, the median arch and boulder body over the cliffs' in the game's baked distant view, per area (painted.md section 30, "Rock textures") |
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
  Savanna is kept out of the desert rock. Since 2026-10-08 the Rocky Desert and the Desert
  Canyons are too ("Rock from the cliff material").

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
OKLab ×100. The rock tops were measured with the patches of 2026-10-06.

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

### The top layer's colours (2026-10-06; the mask, 2026-10-09)

On the Spire Coast's lit tops the screenshots show moss and grass on 14 to 66% of the face,
pooled #505936, and the forest top wears that colour. From 2026-10-06 the top lay in value
noise patches inside an up-facing ramp, a rule tuned to the screenshots' share. Since
2026-10-09 it lies where the cliff master's own slope mask puts it, read from its compiled
shader, and the look's normal maps break its edge (painted.md section 30, "Rock textures"):
the game's baked distant view has the coastal tops mossy over the whole up-facing face, the
screenshots' bare share being the mesh normal maps' own relief.

**The forest top.** `calibration.tops` names a display target for a family's top, made a
ground colour as the other display targets are (`top_targets`, applied by `family_tables`).
Only the forest family has one, #505936, from the Spire Coast boxes of the target table
above.

**A top made of a paint layer** (2026-10-07, `surfaces.layer_tops`). The sand, grass and red
grass tops are textures of the landscape's own tiles, `Tiles/<Layer>/`, so the folder names
the paint layer they are. Such a top wears that layer's display target, scoped by area as the
layer is, on the rock grid: the sand top #d5cbb6, and #c4ab8b in the deserts and the Savanna;
the grass top #83986e, and #9dad70 in the Grass Fields. A layer with no target leaves its top
the texture's mean, as red grass is. A texture mean is brighter than the bake the gain was
fitted on: the sand top's (linear 0.56, 0.45, 0.33) drew near white (#fce1c1 to #ffe7ca) in
patches over the desert and beach rock of the Rocky Desert, the Dune Desert's edge and the
Northern Forest's sand cliffs.

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
- **Hot-spring terraces** are #ccbea8 on the map, a display target (`calibration.meshes`).
  Kept as an albedo, the exposure and gain drew them near white (#fbe6c8) as islets in the
  Rocky Desert lakes and in the Southern Forest's spring at (895, 2215).

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
  `SM_BalloonTree_02_T` (chroma 0.012, the palms' hue) keeps its colour. The blue palms'
  target is #3d627d: 3,332 trees, step -0.285 and ×1.51 (sidecar `crowns@blue_palm`).
- **By species, not by pixel** (2026-10-07, `named_targets`). The gate reads each species'
  own colour, and the species' mips move by the share it opens, before the canopy op is
  measured. Gated per pixel, a palm under a sparser, taller crown failed it: `AmberTree_01`'s
  branch cards (opacity 0.18; one at scale 2.4 at (-2212, -1374) reaches 34 m) lie over the
  palms at cover 0.3 to 0.5,
  their mix has chroma 0.006 to 0.01, so the gate closed and the palm drew its pale texture
  colour #acccd9, grey under the amber. Moved in its mips, the palm is blue before any crown
  is laid over it, and the amber over it darkens it instead.
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
there in `calibration.derived_keys`, and takes each only where the derive gate passes it
("The derive gate" below); every other key keeps its screenshot target. Code:
`palette/painted/derive/` (`camera.py`, `scene.py`, `rules.py`, `gate.py`, `targets.py`,
`palette.py`), `commands/calibrate.py`, and the readers in `gamedata/level/curves.py` and
`lighting.py`.

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
| cliff body texture | `rock`, an area's rock | the mean of `Cliff_Sediment_Alb`, the body albedo every cliff instance streams; the family's `Color Tint` is recorded and not applied ("Rock from the cliff material" below) |
| sand-rock | `families.desert`, an area rock wearing its target | the DesertRock paint-table entry, `TX_SandRock_Alb_01` times `Sand Rock BaseColor`; `MI_DesertRock` has no albedo texture of its own |
| top texture | `tops` | the family's far albedo mean |
| crowns | `canopy`, `crowns`, `species` | the weighted median of the trees' top-down sprite colours (cover × scale²); the canopy takes only crowns within 20 to 40° of the forest floor texture's hue and leaves out the keyed species and the blue palms |
| material texture | `meshes.coral`, an area's shells | the materials' base-colour means under their masks |
| none | water, seabed coral | the game's water absorbs but does not scatter, so its colour is the sky, the clouds and the fog, which the cooked assets do not expose |

**Results on build 502094** (model version 2, 2026-10-08). ΔE is OKLab ×100 against the
screenshot target, and "Chroma and hue" the part of it in a and b against the gate's allowance;
`*` marks the keys `derived_keys` lists and a render takes, `x` those it lists and the derive
gate declines, so they keep their screenshot.

| Key | Derived | Screenshot | ΔE | Chroma and hue | Light |
| --- | --- | --- | ---: | ---: | --- |
| * layers.Sand | #d7c1a2 | #d5cbb6 | 3.0 | 1.9 / 2.0 | global |
| layers.SandRipples | #d98d64 | #ca784f | 6.0 | 1.0 / 7.8 | Dune Desert |
| * layers.Grass | #7f8d4e | #83986e | 4.4 | 2.7 / 4.3 | global |
| * layers.Forest | #484d26 | (the canopy's #558653) | 17.3 | 5.0 / 6.2 | global |
| x derived.WetSand | #b38c61 | #a29583 | 4.7 | 4.6 / 2.0 | global |
| * canopy | #5e8136 | #558653 | 3.2 | 2.9 / 6.2 | global |
| x rock | #7b6f5a | #85816c | 5.5 | 1.0 / 2.1 | global |
| * families.desert | #c38761 | #ae8271 | 4.5 | 3.3 / 4.0 | Dune Desert |
| * tops.forest | #54662b | #505936 | 4.7 | 3.2 / 3.6 | global |
| x meshes.coral | #9f7e75 | #99868e | 3.6 | 3.1 / 1.7 | global |
| crowns.blue_palm | #bec7c1 | #3d627d | 34.6 | 6.1 / 4.1 | global |
| species.SM_Kapok_03 | #ad4a37 | #7c4955 | 10.3 | 7.9 / 4.7 | global |
| desert entry: Sand | #d7c3a5 | #c4ab8b | 7.2 | 0.7 / 3.5 | global |
| desert entry: WetSand | #b48d61 | #987b61 | 7.1 | 2.5 / 3.5 | global |
| * desert entry: Gravel | #9e896f | #8f8373 | 3.1 | 1.7 / 1.8 | global |
| * desert rock entry | #c38761 | #ae8271 | 4.5 | 3.3 / 4.0 | Dune Desert |
| * Grass Fields etc.: rock | #7b6f5a | #7e7868 | 2.9 | 1.1 / 1.7 | global |
| Grass Fields: Grass | #7d8f50 | #9dad70 | 10.0 | 0.5 / 5.7 | global |
| * Red Jungle: Cliff | #7c6f5b | #877e6e | 4.9 | 0.8 / 1.7 | global |
| x Red Jungle: rock | #7b6f5a | #877e6e | 5.0 | 0.9 / 1.7 | global |
| crater: CoralRock | #82838e | #6c7386 | 5.8 | 1.5 / 2.0 | global |
| Blue Crater: shell | #7b705b | #747b85 | 6.0 | 5.1 / 1.3 | global |

Model version 1 derived the three rock keys #897758: ΔE 3.2, 2.6 and 3.0, but chroma and hue
2.2 / 2.1, 2.6 / 1.7 and 2.4 / 1.7. The gate declines all three; "Rock from the cliff
material" below says where that colour came from.

The layers no key targets get a derived colour too: GrassRed #c09872, Puddles #624f37,
RedJungle #ae6451, SandCracks #d1845c (Dune Desert light), SandPebbles #bfaa85, SandRock
#bd7551 (Dune Desert light) and Soil #896e4e, all from the bake; DesertRock and PurpleForest
have no pure texels and fall back to their paint-table entries.

**Which keys take the derived colour.** `derived_keys` lists the desert rock under the Dune
Desert's light, the Forest floor, the seven layers with no target at all, and the keys that
were within ΔE 5 of their screenshot target when they were listed. Of those, a render takes the
ones the derive gate passes. Not listed: the blue palms (the leaf texture is a pale blue-grey and
the model has no foliage sky term), the red Kapok (no subsurface light), every water colour,
and the keys between 5 and 10 until the in-game checks their verdicts list are done. A render
with derived colours carries the merged palette's digest, so a different install's derivation
is a different style.

**The derive gate** (2026-10-08, `derive/gate.py`). A listed key takes its derived colour only
when it agrees with its screenshot target in lightness, chroma and hue:

- ΔE (OKLab ×100) at most 5, as the listing rule above;
- and the a-and-b part of the difference, the chroma step and the hue step together, at most
  2/3 of the target's chroma, the target counted at chroma 0.02 at least, so a grey target
  keeps an allowance (×100: 1.3 for a grey, 2.1 for the default rock #85816c).

ΔE alone let a hue shift through on a near lightness: model version 1's rock was 3.2 from
#85816c with nearly all of it in hue and chroma. The share 2/3 sits between the worst key the
gate keeps on this build (the sand and the desert gravel, 0.63 of their allowance) and model
version 1's rock (0.71). `calibrate` prints both parts and marks a declined key `x` with why; a
render records it in its sidecar (`sources.paint.derived_targets.declined`) and keeps the
screenshot. A key with
no screenshot target of its own is not gated, and `derived_ungated` names keys that pass
regardless: the Forest floor, whose target is the canopy's as a stand-in.

On build 502094 the gate declines four listed keys. The default rock and the Red Jungle's rock
(ΔE 5.5 and 5.0, lightness only: the cliff body is darker than the lit tops the screenshots
pool) keep #85816c and #877e6e, which are as grey. The wet sand (chroma 0.075 against 0.030)
and the coral caps (hue 45° off) keep their screenshot colours, which model version 1 also
replaced.

**The file and the run.** `targets.derived.json` holds each key's colour with its rule, light,
light shares, albedo, assets and sample count, and a stamp: the paint store's digest, the
digest of the area map on the 4 m grid, the calibration block's digest without its `about`,
`derived_keys` and `derived_ungated`, and the model version. A render reads the file while its
stamp holds and derives in its own run otherwise, about 6 s; a store from before generator 4
keeps every
screenshot target and the sidecar says why (`sources.paint.derived_targets`). The command
reads the install and writes nothing but the file; `--check` prints the table and writes
nothing.

**Tests.** `tests/fixtures/calibration_screenshots.json` keeps the screenshot targets with
their sources. A scored key must lie within ΔE 8 of its target, or within its own tolerance,
its measured distance plus 1 (Grass Fields grass 11, the red Kapok 11.5); the blue palms are
not scored. The integration test checks them on a real store with `E` within 2% of 2.81, and
that the three rock keys derive the cliff body within the gate's chroma-and-hue allowance.

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

### Rock from the cliff material (2026-10-08)

Model version 1 derived every non-desert rock #897758 (chroma 0.050, hue 81°), close to the
dirt paths, and the painter set every rock's chroma and hue to it. Its albedo was the mean of
`Cliff_Macro_Alb_02` and `Cliff_Detail_Alb` times the cliff family's `Color Tint`. Neither
texture is the cliffs': `Cliff_Macro_Alb_02` is referenced only by `MM_Arc_Emissive`, and
`Cliff_Detail_Alb` by the arch master `MM_Arc_01` and the hot springs. Read on build 502094:

- **One master, one tint.** Every cliff and cliff-family rock wears an instance of
  `Rock/Material/Rock_WA`: 143 instances under `World/Environment/`. `Cliff` sets
  `Color Tint` (0.624, 0.545, 0.471) and the static switch `Use Color Tint`; every family
  root (`Cliff_Forest`, `_Grass`, `_RedGrass`, `_Sand`, `_WetSand`, `_RedJungle`) and 138 of
  the 139 instances under them inherit both. One overrides the tint: `CliffCone_01_WetSand`,
  (0.475, 0.404, 0.341). `Boulder_WA_Grass`, `Pebbles01` and `Ribrock_02_Inst` do not set the
  switch; the master's own default is grey (0.870).
- **What it samples.** The master's cached expression data references, for colour,
  `Cliff_Sediment_Alb` (linear mean 0.096, 0.090, 0.086: dark grey with tan veins),
  `TX_FlatRock_01_Alb` (streamed only where "No Sand/Grass" is off), the top layers' textures
  and `PigmentMap`. Each mesh's `Reflection Map` is not colour: red edges, green cavity, blue
  occlusion. The cliff instances stream `Cliff_Sediment_Alb`; `ROCK_TEXTURES` now names it,
  so the next `mapgen paint` also records it as the store's rock albedo.
- **Per area.** The static switch `PigmentMap` is on for the `Cliff_Grass`, `Cliff_RedGrass`
  and `Cliff_Sand` trees (62 instances) and off for the plain, forest, wet-sand and red-jungle
  cliffs. The pigment map is white over most of the map. Nothing varies per placement:
  `bHasPerInstanceRandom` and `bHasPerInstanceCustomData` are false on `Rock_WA`.
- **How the tint combines.** The graph is cooked out, so the parameter names are what is left:
  `Color Tint` behind `Use Color Tint`, and `Mask Intensity` (0.4 on `Cliff`, 1.0 by default)
  the only scalar that could be its strength; no lerp, overlay or strength parameter is named.
  The game's own bake decides it. The persistent level ships 572 mesh HLOD cells
  (`Persistent_Level_HLOD0_256m_1023m_L<n>_X<x>_Y<y>_Material_BaseColor`, 512 px virtual
  textures in the bake's tile layout, the 256 m cell centred on (256x, 256y) m), each the
  BaseColor of its merged proxies baked from their materials. A vector multiply survives that
  bake: the sand tops read (0.509, 0.342, 0.227), `TX_Sand_BC` times the master's `BaseColor`
  (0.949, 0.780, 0.737). The cliff body reads (0.084 to 0.095, 0.082 to 0.091, 0.078 to 0.084),
  chroma 0.003 to 0.006, the same in all 17 non-desert areas: the body texture's colour. A
  plain multiply by the tint would read (0.060, 0.049, 0.040), chroma 0.04. Whatever the tint
  does, a masked lerp would fit its names, it does not reach the colour seen from afar, so the
  rule records it and does not apply it.

The rule's colour is the texture's under the warm noon sun: #7b6f5a, chroma 0.034 at hue 82°,
against the grey targets' 0.025 to 0.031 at 82 to 98°.

**Where rock is tan in the game.** In the HLOD bake, tan is the desert rock family
(terracotta, the Dune Desert and the desert spires: `families.desert`), the sand tops of the
`_Sand` family (4.0% of the Rocky Desert's atlas texels, 5.4% of the Savanna's, 2.2% of the
Western Dune Forest's, 2.0% of the Desert Canyons'), and a lighter warm grey (linear 0.16 to
0.18, 0.14 to 0.17, 0.12 to 0.15) on the arches (`MM_Arc_01`) and the rocks of other masters,
which have no family and wear the area's rock.

**Footage** (Coffee Stain's videos, by YouTube id): the 1.0 launch trailer (Jt4XOPiPJHs, 0:46)
and the console trailer (TURoNOy6eC0, 0:56) show the Grass Fields arch grey; the 1.2 trailer
(aoHCYhlYjlc, 0:24) and the console trailer (0:19) the desert rock terracotta. The Update 8
showcase (ZywFe6eKNvk, an editor build, 0:14:28 and 0:15:20) shows grey cliffs and grey arches
standing in sand beside red mesas. The rule and the families put both in place: the cliffs
grey, the desert family terracotta.

**The desert entry follows the game.** The desert rock entry (#ae8271) named the Desert
Canyons and the Rocky Desert too, and so coloured every rock there salmon, the 561 and 1,363
cliff-family placements included, though the HLOD bake has those cliffs grey with sand tops, as
the showcase frames do. It now names the Dune Desert only. The two areas' rock wears the
default (#85816c on this build, the derived rock being declined by the gate), and their sand
tops the desert sand target #c4ab8b where the cliff master's mask puts them; the desert rock
family wears its own target wherever it stands, so the mesas stay terracotta. The derived key
is `areas[DuneDesert].rock`, the same rule and the same #c38761 as before.

The HLOD bake itself could become a source: rasterised top-down at 1 m it gives the game's own
colour of every merged mesh, trees included, though its proxies are coarse.

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
- The top layer's mask is the cliff master's, read with the look's normal maps where the game
  reads each mesh's own (painted.md section 30, "Rock textures"). Only the forest top has a
  colour measured from screenshots; the sand and grass tops wear their layers' targets and
  the red grass top its texture's mean. The `_WetSand` instances' top layer is the cooked
  master's default and is not drawn; whether it shows in game is unchecked.
- The red Kapok is crimson wherever it grows, the Rocky Desert and the Red Bamboo Fields
  included, by the Red Jungle's references. Its crowns there were not measured from above.
- No crown target for the orange palms (`Orange_palm1`, `_2`, chroma 0.020) or the screw palm
  (`SM_ScrewPalm_01`, 0.017): their texture means are pale grey-green and grey-teal, and the
  canopy gate moves the orange palms all the way and the screw palm by 0.62, so they draw a
  pale mint #a7bcac and a blue-grey #768c85. Like the blue palm's, their in-game crowns are
  likely darker than the mean; no reference has been measured.
- `AmberTree_01`'s crown is branch cards at opacity 0.18, so it draws as a tan haze tens of
  metres wide over the ground and the palms under it; its look from above is unchecked.
- Render-only meshes take one colour per class: the coral class's cap target #99868e also
  colours `CraterCoralRoots` (the Blue Crater's pillars), `SM_NetFungi_01` and the barnacles,
  and the shell class's grey the `SmallShell` plates on the Desert Canyons' cliffs. Neither
  was measured on those meshes.
- On a beach whose paint has no WetSand above the drawn sea, as at the north beach, the
  shore's band is the only wet cue and reads L ×0.97 in its first metre, against ×0.80 in
  game.
- The rocks on the North Beach lagoon islands, and the cliff foot and boulders along the north
  beach around (128, -1500), sit inside the main piece of `Area_DesertCanyons`. They were
  salmon while the desert rock entry named that area, and are grey since it names the Dune
  Desert only ("Rock from the cliff material"), as the wiki, a 2022 editor view and the game's
  HLOD bake show them. None of them is desert rock: the cliff foot is cliff meshes
  (`CliffPillar_01` to `_07`, `CliffFlat_02` and `_03`, `CliffFormation_04` and `_05`) in plain,
  `_Forest` and `_Grass` instances, and the boulders are foliage `SM_Boulder_04` and
  `SM_Boulder_02` in their own materials. The boulders wear the same materials everywhere, with
  no override, so following their mesh would give them one colour across the map, the Dune
  Desert included.
- Boulders, arches, and rubble and rock piles whose material roots no family keep the area's
  rock. On the mesas, which the area map gives to the Spire Coast, a few of them stay grey
  (the default rock) on the red-brown rock.
- The Red Bamboo Fields and Red Jungle lakes keep the sea fit: their reference water
  reflects a purple sky, which is not the swamp's look.
- Every target comes from tonemapped perspective screenshots; only the Grass Fields grass
  is from a top-down view.

# Colour calibration of the game-painted style

Section 31 of the [design spec](../../DESIGN.md): the targets the game-painted style is calibrated to,
and how they were measured. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

## 31. Colour calibration of the game-painted style (2026-10-05)

The painted style is calibrated against in-game screenshots: first the Spire Coast, the Dune
Desert, the Western Beaches and the Eastern Dune Forest, then a second pass over the biomes
those left out (see "Area targets"). Style `satellite-painted` version 3; the crowns, the
gated swamp water and the mesh colours below are version 6, the blue palms' own crown target
version 9, the desert rock family's target and the daylight dune target version 11, and the
three land rules (the Spire Coast rock from its own material, wet sand by rule, the red Kapok
by species) version 17, and the moss in patches version 18. Code:
`palette/painted/ground.py`, `calibration.py`, `trees.py` (crowns), `optics.py` (water)
and `surfaces.py` (rock and meshes). Numbers: the `tone`
and `calibration` blocks of `palette/palettes/satellite-painted.json`.

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

WetSand was #b8a083, from one Spire Coast waterline box that probably mixed in dry sand, then
#b1a09e through game-painted 16: the Western Beaches wet band and the 1.0 crash-beach store
shot both read a low-chroma mauve grey, so that target kept the old lightness (L 0.72) and
took their hue and chroma. Since 17 it is a rule (below).

#### Wet sand by rule (2026-10-06)

In the game the wet sand is the sand multiplied by `WetSand_Color`: the bake's Sand times
(0.422, 0.378, 0.336) matches the bake's WetSand within 1 to 11%. Five daylight screenshots of
an exposed wet band against the dry sand beside it pool to L ×0.80, C ×0.98 and a hue turn of
−13° (Steam [3771679183](https://steamcommunity.com/sharedfiles/filedetails/?id=3771679183),
the north beach; [3806590944](https://steamcommunity.com/sharedfiles/filedetails/?id=3806590944);
[3427095273](https://steamcommunity.com/sharedfiles/filedetails/?id=3427095273);
[3438470146](https://steamcommunity.com/sharedfiles/filedetails/?id=3438470146); and
[Western Beaches](https://satisfactory.wiki.gg/images/Western_Beaches.png)). Warm light gives
−3 to −13°. Under a blue-cast sky the glossy band reflects the sky and turns −30 to −50°, which
is where the mauve of #b1a09e came from.

`calibration.derived` holds the rule: WetSand is the Sand target with OKLab lightness ×0.80,
chroma ×1.0 and hue −10°. `with_derived` (`palette/painted/calibration.py`) adds it to every scope that
has a Sand target and no WetSand target of its own: #a29583 from the global #d5cbb6, and
#987b61 inside the desert entry from its #c4ab8b. A target written out in a scope wins over the
rule. The derived targets then go through the per-layer transfer like any other.

The band the shore draws (section 27, `wet_band`) multiplies the ground near the waterline on
top of that. Its tint was a cool (0.78, 0.80, 0.85), which pulled towards mauve; it is now a
mild warm (0.90, 0.88, 0.86). Measured on windows of the full-size grid (see "The land rules,
measured" below), dry ground painted WetSand lands at L ×0.72 to ×0.78 of the dry sand 10 to
60 m up the beach, C ×0.82 to ×0.94 and h −3 to −9°, against ×0.75 to ×0.87, C ×0.53 to ×0.62
and h −42 to −77° before. (On the ocean coast at (-2515, 645), whose dry sand is near grey, it
reads C ×1.32 and h −15°.) A stronger band would sink it further below ×0.80 within the first
metre: the cool tint took 0.03 to 0.04 of L there, the mild one takes 0.02. On a beach painted
Sand down to the drawn sea, as at the north beach (-58, -1507), the band alone draws the wet
line, at L ×0.97 in the first metre (×0.95 before). The paint has no WetSand above the drawn
sea there: its Sand/WetSand crossover sits near -17.4 m and the sea at -17.0 m (section 27).

SandRipples was #d07756 through style version 10. It came from one golden-hour shot,
[Dune Desert](https://satisfactory.wiki.gg/images/Dune_Desert_Area.png) (low sun, pink sky,
probably Early Access), which measures h 44.5, and a further -3° hue turn took it to h 40.9.
The low sun and the turn both pushed it towards red, so the dunes drew a little salmon. Since
version 11 the target is the pooled median of four 1.0 daylight Steam shots, measured as the
area targets below are: [3486291454](https://steamcommunity.com/sharedfiles/filedetails/?id=3486291454)
(rippled sand seen almost straight down, high sun),
[3352353206](https://steamcommunity.com/sharedfiles/filedetails/?id=3352353206),
[3360666296](https://steamcommunity.com/sharedfiles/filedetails/?id=3360666296) and
[3590923329](https://steamcommunity.com/sharedfiles/filedetails/?id=3590923329). They range
from h 44 to h 49 and pool to L 0.653, C 0.117, h 46.9, which is #ca784f. That is ΔE 1.4 from
the old value: the same lightness and chroma, turned from salmon towards orange. The game's
own bake for the layer is red-orange too (#b36957). One more daylight shot of a rippled flat
by a lake reads h 54 and is left out as an outlier. On a 300 m window of the full-size grid
at (3025, -1826), the median of the ground clear of rock with at least 0.7 SandRipples goes
from #d07958 (h 41) to #ca7a52 (h 47).

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
blurred over `area_blur_m` (25 m) on the 4 m rock grid. An entry can carry:

- **`layers`.** The layer's weight is split by the area share. The area's part moves to the
  entry's target, the rest to the global target if there is one. Each source median is
  measured on its own side, on pure texels as above.
- **`rock`.** As the desert rock below.
- **`canopy`** and **`meshes`** (coral, shell). The colour becomes a plane on the rock grid:
  the global colour outside, the entry's inside. The canopy targets also move the tree crowns
  (see "Crowns" below). Since game-painted 17 no entry carries a canopy target: the Western
  Dune Forest and the Jungle Spires take the global one, and the Red Jungle's crimson is keyed
  by species.
- **`water`** with **`water_class`.** An opaque display colour for the water of one class of
  section 33 (`swamp` for the Swamp). Under that class's share of a pixel's water it replaces
  the Beer-Lambert result by `1 - exp(-depth / water.opaque_tau_m)`, with the tau 0.3 m, so
  only the edge shows the bed. Ocean inside the area keeps the sea; through style version 5 the
  colour went on every water texel of the area, sea included. A store without a class plane
  gives it the water off the ocean's reach.

No two entries scope the same material to the same area; a test checks this.

**Offshore pieces.** The game's area map gives stretches of open sea, with their islands, to
areas they do not touch: the Rocky Desert's id covers the sea north of the Spire Coast
(around (743, -2527)) and the islands off the west coast (-2655, 1506). Its rock and sand
targets followed, so the Spire Coast's outer islands drew red-brown rock (#b18574 against
#51524d) and tan sand, with a seam through one rock mass at the area edge. Before any target
is scoped, the area map on the 4 m rock grid is rehomed (`palette/painted/calibration.py`
`rehome_offshore`, from `PaintedGround._coarse_areas`). An area's pieces are its 8-connected
components; the one holding the most land is its own. Any other piece with land under half
its cells takes the named area it shares the longest border with, else No Man's Land. Sea is
water within `OCEAN_LEVEL_BAND_M` (0.5 m) of the ocean level, any grade, and the void off the
landscape; a lake or swamp is land, so the second crater and swamp keep their targets. This
holds for every area entry. On build 502094 it moves 3.74 km² (sidecar
`paint.offshore_cells_rehomed`, in 4 m cells): Rocky Desert to Spire Coast 2.47 km², Rocky
Desert to none 1.09, Red Jungle to none 0.13, Grass Fields to none 0.05. At 2048 px the
Spire Coast crop's red rocks go from #af8774 to #575951 (ΔE 22.7 to 2.4 against #51524d);
dunes and the desert lake do not change. Since game-painted 17 the Spire Coast has no rock
target of its own, so the islands it takes wear the default rock and their families.

**Rock.** In each entry with a `rock` target, and with the default `rock` target everywhere
else, the rock's chroma and hue are set to the target. The lightness moves by the step from
the median to the target, so it keeps its variation. Every rock is now on a display target,
so the old exposure (`rock_keeps_exposure`) no longer applies to any area; the flag stays
for a palette without a default.

The targets use the method above: the median sRGB over the top 60% of L in each box, then
L ×0.95 (capped at 0.86) and chroma ×0.9 in OKLab. Only medium- or high-confidence
references were used. A target is a screenshot measured so, or derived by a rule:

| Material | Areas | Target | Source | References |
| --- | --- | --- | --- | --- |
| Rock | Dune Desert, Desert Canyons, Rocky Desert (not Savanna) | #ae8271 | screenshot | first pass |
| Rock, desert rock family | wherever desert rock stands (see "Rock by mesh family" below) | #ae8271 | screenshot | the desert rock target above. On the desert spires: [Spires Base](https://images.steamusercontent.com/ugc/23177420036648851/71D9249104B80F4E0B96989BE624C9583A34D636/) and [Funicular base](https://images.steamusercontent.com/ugc/9849253882402225725/30B60C5F97C3A2CDF7BA66D4554AB06E246919FD/) pool to #af6f58 (ΔE 4.9), and a Dune Desert mesa wall, [Desert for Dessert](https://steamcommunity.com/sharedfiles/filedetails/?id=3360666296), reads #925e4b |
| Rock | Grass Fields, Northern Forest, Western Dune Forest | #7e7868 | screenshot | up-facing lit rock: [store shot](https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/526870/ss_b1104309f1c22c85de6ad6c401e6d889411c14d2.1920x1080.jpg), [Random mode 1](https://satisfactory.wiki.gg/images/Random_Game_Mode_-_Resource_Node_Example_1.png), [cave entrance](https://satisfactory.wiki.gg/images/Entrance_Of_A_Cave.webp), [Northern Forest U8](https://satisfactory.wiki.gg/images/Comparison_2_-_Northern_Forest_-_U8.png) |
| Rock and Cliff layer | Red Jungle, Jungle Spires, Red Bamboo Fields | #877e6e | screenshot | [Jungle Spires](https://satisfactory.wiki.gg/images/Jungle_Spires.png), [Red Jungle 2021](https://steamcommunity.com/sharedfiles/filedetails/?id=2627451942) |
| Rock, default | everywhere else, the Spire Coast included since 17 | #85816c | screenshot | [Abyss Cliffs](https://satisfactory.wiki.gg/images/Abyss_Cliffs.png), [Lake Forest](https://satisfactory.wiki.gg/images/Lake_Forest.png), the store shot. Lit up-facing bare rock on the Spire Coast pools to #877b71, ΔE 2.1 from it (see "The Spire Coast rock from its own material") |
| Top layer, forest family | the up-facing faces of every `_Forest` cliff and rock, in patches | #505936 | screenshot | moss and grass on lit Spire Coast tops, six boxes pooled: [Can't beat that view](https://images.steamusercontent.com/ugc/12186521166813992372/550A8BFD145E1EC2AFAF479D7DE6B99D87CB37FD/) (the arch top and a shelf), [a Spire pillar](https://images.steamusercontent.com/ugc/54708874924571662/8337E85F52B3538ED4EC6B96D2B0281B9DC53240/), [a leaning pillar from above](https://images.steamusercontent.com/ugc/14675271398369972649/33698D99D1A66B96456520E8149422C870255F1D/), [the oil platform](https://images.steamusercontent.com/ugc/16557712698482623084/7042B540721BDD03CD7F72A2D5CD82452986D6E4/) and an unpublished 1.0 shot. Through game-painted 17 it was the far texture's mean (see "Moss in patches") |
| Sand | the deserts and Savanna | #c4ab8b | screenshot | [Somersloop](https://satisfactory.wiki.gg/images/Somersloop_at_Rocky_Desert.jpg), [six iron nodes](https://satisfactory.wiki.gg/images/Rocky_desert_six_Iron_nodes.jpg), [Desert Canyons](https://satisfactory.wiki.gg/images/Desert_Canyons.png) |
| WetSand | the deserts and Savanna | #987b61 | derived (rule) | the entry's Sand at L ×0.80, C ×1.0, h −10 ("Wet sand by rule" above) |
| Gravel | the deserts and Savanna | #8f8373 | screenshot | Somersloop, and the gravel-to-sand ratio in Desert Canyons |
| Grass | Grass Fields | #9dad70 | screenshot | the v1.1 top-down HUB shots: [front](https://satisfactory.wiki.gg/images/HUB_Front_Overhead.png), [rear](https://satisfactory.wiki.gg/images/HUB_Rear_Overhead.png), [burners](https://satisfactory.wiki.gg/images/HUB_Biomass_Burners_Overhead.png), [freighter](https://satisfactory.wiki.gg/images/HUB_FICSIT_Freighter_Overhead.png) |
| Crowns, red Kapok (`SM_Kapok_03`) | everywhere, by species | #7c4955 | screenshot | [Red Jungle from above](https://steamcommunity.com/sharedfiles/filedetails/?id=3776654401), [Red Jungle 2021](https://steamcommunity.com/sharedfiles/filedetails/?id=2627451942); two 1.0 sources agree (ΔE 1.8 and 1.9). Through game-painted 16 it was the Red Jungle's canopy target (see "Crowns") |
| Crowns, blue palms | everywhere, by hue | #3d627d | screenshot | [six iron nodes](https://satisfactory.wiki.gg/images/Rocky_desert_six_Iron_nodes.jpg), high-angle daylight: the blue leaf pixels (hue 190 to 290, chroma at least 0.04) of five crowns pooled, then the method above. The [Rocky Desert area](https://satisfactory.wiki.gg/images/Rocky_Desert_Area.png) crowns agree on hue (242) but are seen from below; the [river split](https://satisfactory.wiki.gg/images/Rocky_Desert_river_split.png) crowns are backlit. A 1.0 shot gives #556b85 (ΔE 4.4); not derivable, as a third of the leaf is midribs and the fronds are sparse |
| CoralRock layer | Blue Crater, Crater Lakes | #6c7386 | screenshot | [crater ground at noon](https://steamcommunity.com/sharedfiles/filedetails/?id=3372405479) |
| Shell meshes (the pale plates) | Blue Crater | #747b85 | screenshot | [Blue Crater aerial](https://steamcommunity.com/sharedfiles/filedetails/?id=3579556500), [Blue Crater](https://satisfactory.wiki.gg/images/Blue_Crater.png), the noon crater shot |
| Water, opaque | Swamp | #302627 | screenshot | 1.0 views from above, see section 33, "Sea deep and swamp water". It was #7e7372 through style version 15, measured through the swamp's fog on [Swamp](https://satisfactory.wiki.gg/images/Swamp.png), [Swamp 2024](https://steamcommunity.com/sharedfiles/filedetails/?id=3202456199) and [Swamp 1.0](https://steamcommunity.com/sharedfiles/filedetails/?id=3344959083) |

Removed in game-painted 17: the Spire Coast rock #51524d (one backlit, shaded vertical face in
an unpublished 1.0 shot, see "The Spire Coast rock from its own material"), the Western Dune
Forest canopy #7c9573 and the Jungle Spires canopy #6c7f5b (Early Access wiki images, see
"Crowns"), and the global WetSand #b1a09e (now the rule).

Two readings in these references:

- Rock outside the deserts is a near-neutral warm grey (C 0.02 to 0.03), not the green the
  ground tint gave it. Lit tops that face up read warm (h 80 to 100); vertical and hazy faces
  read blue-grey from sky light, which a top-down map does not show.
- The Rocky Desert rock in its own references is grey, not the Dune Desert red-brown, so
  Savanna is kept out of the desert rock.

### Rock by mesh family (2026-10-06)

Rock takes its colour from the mesh it is before the area it stands in. `calibration.families`
names a target per rock family (`gamedata/rocks/families.py` `FAMILIES`), and a family named there
wears its target wherever it stands, over any area entry's rock. Style version 11 names one:
the desert rock family, with the desert rock target #ae8271.

**Why.** The desert spires of the Spire Coast, the mesas at x 1,450 to 2,250 and y -3,200 to
-1,650, are desert rock. Every rock there is an `SM_DesertRock_*` mesh in its own
`MI_DesertRock_*` material, the same set as the Dune Desert's. Their parent `MI_DesertRock`
carries a `Rock Base Color` of linear (0.890, 0.549, 0.384), sRGB #f2c4a7, a salmon. The Spire
Coast's grey pillars are cliff meshes: `CliffPillar`, `CliffFormation` and `CliffCone` in
`_Forest` and `_WetSand` instances of the Cliff master, whose `Color Tint` is the near-neutral
#cfc3b6. The game's area map gives the mesas to the Spire Coast, and the western one to an
offshore piece of the Rocky Desert that the rehoming above moves to the Spire Coast. Coloured
by area, they drew the Spire Coast's charcoal (#5a5b56 at the western mesa in the fifth
render). The references show them red-brown (the table above).

**The family.** `MI_DesertRock` (in `Rock/DesertRock/Material/`) roots the family `desert`,
code 8 in the family plane. On build 502094, 5,907 placements are desert rock, all in the Dune
Desert and the desert spires. The family has no `Color Tint` or top layer to read
(`TARGET_ONLY`), so the paint store and its generator are unchanged. The family plane needs
the new code, so the `rock_families` reader is version 2. A version 1 direct cache, with desert
rock at code 0, is rasterised again on the next run, and a `--restyle` refuses it until then.

**Drawing.** `PaintedGround.attach_families` takes the direct pass's family plane and reads its
code at each 4 m cell of the rock grid (`surfaces.family_cells`). A family's rock grid takes
its target's chroma and hue. Its lightness moves by the step from the median of the family's
own cells to the target, as an area target's does (`family_targets`). Per pixel, rock of that
family takes its family's rock in place of the area's (`rock_surface`). The sidecar records
`paint.rock_family_targets`: on build 502094, 247,002 cells and a lightness step of -0.059.
The offshore rehoming stays: the islands it moved to the Spire Coast, at (743, -2527) and the
islets at (269, -1943), are cliff pillars and stay grey.

**Measured.** Windows of the full-size grid (0.229 m to the pixel) were drawn in-process
before and after, with the fifth render's raster caches and the family plane rasterised again
for each window by the direct pass's own code. It matches the cached heights on all but 0.03%
of the rock pixels. Values are the median of rock pixels (any direct coverage) as drawn,
display sRGB:

| Window | Rock there | Before | After |
| --- | --- | --- | --- |
| Western mesa, x 1,500 to 1,720, y -3,050 to -2,750 | desert | #565752 | #b88b79 |
| Middle mesa, x 1,750 to 2,150, y -2,550 to -2,150 | desert | #575853 | #b78a78 |
| Eastern mesa, x 2,000 to 2,250, y -2,100 to -1,850 | desert | #5f605a | #ac8170 |
| Dune Desert, x 2,450 to 2,800, y -2,950 to -2,550 | desert | #aa7e6d | #aa7f6e |
| Spire Coast islets at (269, -1943) | forest | #607350 | the same |
| The spiral at (-339, -2275) | forest, wet sand | #5f7250, #567d7f | the same |
| North beach at (128, -1500) | plain cliff, forest, grass | #987f6c, #706f54, #7f9662 | #99806c, #716f54, #7f9662 |
| Northern Forest at (271, -1044) | grass, plain cliff | #768759, #7d875d | the same, no pixel changed |

The mesas come out L 0.64 to 0.68, C 0.06, h 43: on the target, lit and lifted by their
height. In the Dune Desert the desert rock moves within 1 level except at the window's west
edge, where the area blend used to grey it. The Spire Coast windows change by at most 1 level
on under 0.2% of their rock pixels, and the north beach by 1 level on a quarter of them: an
area's rock median is measured over ground the new SandRipples target also moves. Away from
rock, boulders and meshes (3 px clear of any), no pixel changes in any window except on the
SandRipples layer in the Dune Desert.

### The Spire Coast rock from its own material (2026-10-06)

Through game-painted 16 the Spire Coast had a rock target of its own, #51524d. It came from
one box in an unpublished 1.0 shot: a vertical cliff face at the frame's edge, backlit and in
shade. Measured on Steam shots of the coast in daylight, the rock falls in three pools: lit
up-facing bare rock #877b71, moss and grass on the tops #505936, and shaded faces #3a3935.
The default rock #85816c is ΔE 2.1 from the lit bare rock; #51524d was 15.6 from it. So the
entry is gone, and the Spire Coast's rock falls back to the default.

**The pillars' own material.** The grey pillars are `Cliff` meshes in `_Forest`, `_WetSand`
and plain instances of the Cliff master, with no tint of their own: in the lagoon box (x -600
to 1,100, y -2,900 to -1,600) 371 of 435 placements are `_Forest`. `Cliff_Forest`'s static
switches turn on "Blend Material on Top", `UseForestGrass` and "Use Far Albedo", with
`TX_Forest_Far_01_Alb` as the far albedo: a forest-floor top layer on the up-facing faces,
which the heightfield cliffs already draw (section 30, "Rock surfaces"). `Cliff_WetSand`
switches its top layer on too, but overrides no texture, so its top is the cooked master's
default, which is not readable; it stays bare. Through the camera model of the colour study
the far albedo gives #54662b; the style draws it #58713d on flat ground, ΔE 3.6 from that.

**Render-only rocks.** `CliffPillar_03` is passable in game, so it stays out of the
heightfield and is drawn by the render-only mesh pass (section 27). Through game-painted 16
its 32 lagoon sea stacks drew in the area's rock, flat #51524d, with no family and no top
layer. Now the mesh pass records each rock's family as the direct pass does: the placement's
override material, else the mesh's own, walked up to a `Cliff_<Layer>` root
(`gamedata/rocks/families.py` `worn_family`). `rasterise_mesh_band` carries it above the class in
each instance's source code (`MESH_FAMILY_SHIFT`), and the cache gains a family plane,
`meshes.family.u8`, beside the class plane (render meshes reader version 3, so an older mesh
cache is rasterised again once). `mesh_surface` draws a rock pixel through `rock_surface` with
that family, so it takes the family's tint and its top layer on the up-facing faces exactly as
a cliff does; a rock with no family keeps the area's rock. On build 502094 the 376
`CliffPillar_03` placements are 137 plain cliff, 128 grass, 57 forest, 21 red jungle, 7 sand,
5 red grass and 21 with no family; all 32 lagoon stacks are forest.

### The land rules, measured (2026-10-06)

Windows of the full-size grid (0.229 m to the pixel, 240 to 300 m across) were drawn
in-process through `render_layer`, with the fifth render's raster caches, the paint store, and
no artwork detail and no open sea, once with game-painted 16 and once with 17. The
render-only rocks' family plane was rasterised for each window by `mesh_items` and
`rasterise_mesh_band` from the level sweep's placements; where both have a rock, the heights
agree within 10 cm on 99.4% of the pixels (96.5% in the Red Jungle). Values are the median of
the pixels named, display sRGB, and ΔE is OKLab ×100.

| Window | Pixels | Before | After | ΔE to the reference, before → after |
| --- | --- | --- | --- | --- |
| Lagoon stacks (203, -2515) | render-only rock facing up | #51524d | #59723e | moss #54662b (model): 9.1 → 3.9 |
| | render-only rock, steep faces | #383b39 | #616053 | lit bare rock #877b71: 24.3 → 10.6 |
| | cliff rock facing up (forest top) | #58703d | #59723d | the stacks' tops: 0.1 apart |
| | cliff rock, steep faces | #53554e | #89856f | #877b71: 14.6 → 3.1 |
| Spiral (-339, -2275) | cliff rock, steep faces | #494d44 | #6f6e5c | #877b71: 17.8 → 6.0 |
| | render-only rock, all | #595a51 | #666558 | #877b71: 12.8 → 8.8 |
| Spire spit (243, -2078) | dry WetSand 0 to 4 m from the water | #96888a to #a19291 | #8f8372 to #948878 | the rule, L ×0.80, h −10: L ×0.75 to 0.79, h −62 to −77 → L ×0.72 to 0.74, h −6 to −8 |
| Spire shore (269, -1943) | dry WetSand 0 to 4 m | #9d8f8f to #a89796 | #958877 to #998d7c | L ×0.78 to 0.81, h −58 to −62 → L ×0.74 to 0.76, h −6 to −8 |
| | dry WetSand 4 to 60 m | #aa9a95 | #9d917f | L ×0.82, C ×0.62, h −42 → L ×0.78, C ×0.91, h −5 |
| North beach (-58, -1507) | Sand 0 to 1 m from the water | #baa486 | #c2a886 | L ×0.95 → ×0.97 of the dry sand #cab08d; no WetSand above the drawn sea |
| Western Dune Forest (-1239, 1305) | crowns | #79916f | #548552 | #558653: 6.8 → 0.3 |
| Jungle Spires (-1696, 936) | crowns | #6c7f5b | #558552 | #558653: 3.9 → 0.3 |
| Red Jungle (-1036, 237) | red Kapok crowns | #7b4854 | #7b4854 | #7c4955: 0.3 → 0.3 |
| | other crowns (bamboo) | #a1657e | #c7616e | their own texture colour, as in the Red Bamboo Fields |
| Red trees in the Jungle Spires (-650, 700) | red Kapok crowns | #994946 | #7b4854 | #7c4955: 6.0 → 0.3 |
| Dune Desert (3025, -1826), dry and inland | everything | | | 38,941 of 1.7 M pixels change, by one level at most |

The canopy's global step hardly moves when the two forests join its scope: 20,540 trees before
and 26,000 after, a lightness step of +0.037 and a chroma scale of ×1.141 both times. The red
Kapok's own step is −0.035 and ×0.646 over its 1,324 trees, as the Red Jungle's was. The wet
sand's derived targets reach the bed under shallow water too: the sea 0.3 to 1 m deep goes
0.001 to 0.010 darker in L, its hue within 2° (#5f8182 to #5d7e7f at the north beach).

### Moss in patches (2026-10-06)

Through game-painted 17 the top layer covered every up-facing face of a family that has one,
at the full weight of section 30's ramp, in its texture's mean. So every `_Forest` cliff and
pillar wore a solid green lid, #58713d on flat ground. On the Spire Coast's lit tops the
screenshots show moss and grass on 14 to 66% of the face, in patches with bare rock between
them, and darker: the boxes pool to #505936, ΔE 7.5 from #58713d. Since game-painted 18 the
top lies in patches, and the forest top wears that colour.

**No mask from the game.** Where the top shows is decided inside `CliffTopMaterial`, the
Cliff master's top-layer function. It is cooked into the master, so its mask cannot be read
(section 30, "Rock surfaces"). The patches are a rule drawn by the style and tuned to the
screenshots' share, not game data.

**The mask.** `palette/painted/surfaces.py` `top_cover` multiplies section 30's up-facing ramp by a
patch mask from `rock_top.patches`. The mask's noise is `patch_noise`: value noise on a
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

**Measured.** As in "The land rules, measured": windows of the full-size grid, 280 m across,
drawn in-process once with game-painted 17 and once with 18. The pixels are forest-family
rock facing up (`nz` above 0.85), clear of water, crowns and canopy, with the heightfield
cliffs (rock weight at least 0.99, no mesh) and the render-only rocks counted apart. The moss
share is the share of them with a top weight of at least 0.5. Colours are medians in display
sRGB, over all of them ("all tops") and over those at least 0.9 moss ("moss"); ΔE is OKLab
×100.

| Window | Pixels | Moss share | All tops | Moss | Moss ΔE to #505936 |
| --- | --- | --- | --- | --- | --- |
| Lagoon stacks (203, -2515) | cliff tops, 13,253 | 0.998 → 0.523 | #57703c → #707157 | #566f3b → #4e5734 | 6.9 → 0.7 |
| | render-only rock tops, 6,184 | 0.999 → 0.384 | #58713d → #7c7861 | #58713d → #4e5734 | 7.4 → 0.7 |
| Spiral (-339, -2275) | cliff tops, 35,848 | 0.999 → 0.343 | #57703c → #837f69 | #57703c → #4f5835 | 7.2 → 0.4 |
| Lake Forest (298, -557) | cliff tops, 123,793 | 0.999 → 0.353 | #5b7440 → #7f7b65 | #5b7440 → #535c38 | 8.4 → 1.1 |
| | render-only rock tops, 3,070 | 0.999 → 0.308 | #5d7642 → #86826c | #5c7541 → #555e3b | 8.7 → 1.7 |
| Spire cliffs (357, -1670) | cliff tops, 189,822 | 0.998 → 0.452 | #59723e → #7f795f | #59723e → #515a37 | 7.8 → 0.4 |
| | render-only rock tops, 68,450 | 1.000 → 0.514 | #5c7541 → #767259 | #5c7541 → #535c39 | 8.7 → 1.1 |

In 30 m boxes the cliff tops carry 14 to 72% moss in the Lake Forest (10th to 90th
percentile of 15 boxes) and 26 to 67% at the Spire cliffs (25 boxes), against the
screenshots' 14 to 66%. A window's share is below the flat 49% because most of its tops are
short of `nz` 0.97. Between the patches the tops show the area's rock as drawn: #85816c in
the Lake Forest, the default target, #86826d to #8e8a74 in the two Spire Coast windows, and
#9a8472 at the Spire cliffs, which stand on the Desert Canyons' edge. Steep
faces (`nz` below 0.6) move by at most 3 levels, where the ramp's 3-pixel box reaches them.
The spiral's render-only rocks have a single up-facing forest pixel and are left out.

**Cost.** On one full-width band of the 32768 sheet the mask adds 0.43 s where 2.5% of the
pixels carry a top, and 1.0 s where 10 to 26% do: 55 to 133 s over the sheet's 128 bands.

### Other colours

- **Meshes.** Coral-tree caps are #99868e, replacing the pink placeholder; a display target,
  so the gain does not brighten it. Seabed coral #5f8899 is a bed colour, as in the water
  fit, so it is also divided by the 0.8 wet factor. It is still composited into the bed
  before Beer-Lambert, with the depth measured to the coral top, so shallow reefs stay
  visible: #628b9b at the surface, #5a8286 at 0.5 m.
- **Which coral is under water.** Coral takes the seabed colour by the pixel's water cover.
  Through style version 5 it was `depth_m > 0`, which off the ocean's reach is the 40 m depth
  fraction of dry land, so 4,023 px of coral on dry land drew seabed blue.
- **Coral specks.** The mesh raster takes one sample per pixel, so coral narrower than a
  pixel standing in the sea fills a whole 3.66 m preview pixel and drew as a pink dot. A
  coral pixel whose eight neighbours are at least 60% water (`SPECK_WATER`) is drawn as that
  water, at their mean depth, with the coral as its bed. Coral wider than a pixel keeps its
  cap colour, and so does coral on land: the Spire Coast's coral trees stand a median 23 m
  above the sea and are the caps the #99868e target was measured on. At 2048 px this sinks
  823 coral pixels of the sheet, 260 of them in the Spire Coast crop at (16, -2137), whose
  median goes from mauve #898189 to the water's #56787d. The specks left in the Spire lagoon
  at 2048 are coral pieces 2 to 75 px across standing a median 6.5 m out of the water, and
  boulders and ground a median 0.33 m over the sea's level: game data, kept.
- **Shells** (`SM_BigShell_01`, `PlateauShell`, `SmallShell`: everything the `Shell` class
  takes) wear their own material's colour: the mean of their BaseColor textures in linear
  light is 0.08 to 0.11 neutral grey, kept as the albedo sRGB (88, 85, 83). The #d6ccba cream
  was a placeholder. Their materials also carry a cyan emissive, not drawn. The Blue Crater
  keeps its #747b85 target.

### Crowns

With crowns drawn the soft canopy is off (`canopy_kept` 0), so through style version 5 the
canopy targets coloured nothing and the crowns drew their texture means: the Red Jungle a
saturated red against its #7c4955. The targets now move the crowns (`palette/painted/trees.py`
`crown_ops`, applied in `crown_layer`):

- **Scopes.** Each area entry with a `canopy` target is one, holding the trees where its area
  share is at least 0.5; the global `canopy` target holds the trees no entry does.
- **Source.** The per-channel weighted median of the scope's crown colours as drawn: the
  texture mean times `darkening`, at the style's `chroma`. A tree counts by the ground its
  crown hides, its species' sprite cover times its scale squared.
- **Step.** As for the layers: a lightness step, a chroma scale and a hue turn to the
  target, taken back through the flat light without the altitude lift a crown does not get
  (`display_to_crown`). Per pixel the ops are mixed by the area weights on the 4 m grid, as
  the canopy colour planes were.
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
- **Species targets** (game-painted 17). `calibration.species` names a target per tree
  species, for a colour that belongs to the tree rather than to the place. Before the scopes
  are measured, each named species' crowns move from the species' own colour as drawn onto
  its target, every texel of its sprite's mips (`palette/painted/trees.py` `species_targets`), so the
  hue gates that follow see the moved colour. One is named: the red Kapok `SM_Kapok_03`, on
  #7c4955, 1,324 trees, step -0.035 and ×0.646 (sidecar `species@SM_Kapok_03`).

**Why by species.** Through game-painted 16 the crimson was the canopy target of
`Area_RedJungle_1`, and the Western Dune Forest (#7c9573) and the Jungle Spires (#6c7f5b) had
canopy targets of their own. Those two came from Early Access wiki images (uploaded 2023-03,
before Lumen). Their crowns are 86 to 90% `SM_Kapok_01` and they have no grading volume of
their own, so they now take the global #558653; 1.0 Kapok canopy ranges h 127 to 152. The red
Kapok grows outside the Red Jungle too: 68% of its crowns there, but also 32% of the Rocky
Desert's, 22% of the Red Bamboo Fields' and 19% of the Jungle Spires'. Keyed by area, the red
Kapok in the Jungle Spires around (-650, 700) fell to the green target's scope, which its hue
gate refused, and drew its texture mean #994946, ΔE 6 from the crimson. Its crimson is real
(two 1.0 sources at ΔE 1.8 and 1.9; not sub-surface light, which reads coral, and not haze,
which reads mauve from afar). Other red-leaved species are not named: the research names
only the red Kapok, so the pink bamboo, the mangroves' bark, the cat palms, the Dypsis palms
and the Diospyros keep their texture colours. Inside the Red Jungle the bamboo was moved by
the area's crimson step through game-painted 16 and now draws its texture colour, as it does
in the Red Bamboo Fields.

Measured at 2048 px on build 502094, game-painted 6: the median of crown pixels at least 95%
covered and dry, per area, Delta E (OKLab x100) to the target before and after. Game-painted
17 is measured in "The land rules, measured" above.

| Scope | Trees measured | Step (dL, chroma) | Area | Before | After |
| --- | --- | --- | --- | --- | --- |
| Red Jungle #7c4955 | 10,312 | -0.035, x0.65 | Red Jungle | 6.2 | 0.4 |
| Jungle Spires #6c7f5b | 2,170 | +0.037, x0.71 | Jungle Spires | 5.7 | 1.0 |
| Western Dune Forest #7c9573 | 3,070 | +0.094, x0.71 | Western Dune Forest | 12.1 | 1.0 |
| Global #558653 | 20,540 | +0.037, x1.14 | Swamp | 5.2 | 0.3 |
| | | | Titan Forest | 8.5 | 2.6 |
| | | | Spire Coast | 6.5 | 3.6 |
| | | | Northern Forest (pines, gated out) | 6.9 | 7.2 |

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
which the targets remove. The coral caps sat 3.2 below target, lit by their slope; their
chroma and hue are within 0.5. They are not domes: a coral tree is a stack of shallow bowls,
and from game-painted 6 to 14 its crown covered the mesh (section 36, "Coral trees are no
crowns"). Wet sand and grass had no dry, pure patches in these crops. On the Spire Coast the
wet sand is under water, as the references show.

The area targets were measured on the paint table (no bake), flat-lit: each material's pure
texels pushed through the flat-ground pipeline, the median in OKLab against the target.
Layers are texels with at least 0.7 of the weight and no canopy; rock is the rock grid
under cliff provenance; canopy is texels at least 0.8 covered, which is 85% canopy and 15%
ground; swamp water is one texel at 1.4 m. ΔE before is version 2, after is version 3:

| Material | Areas | Before | After |
| --- | --- | --- | --- |
| Rock | Grass Fields, Northern Forest, Western Dune Forest | 4.6 | 0.2 |
| Rock | Red Jungle, Jungle Spires, Red Bamboo Fields | 1.5 | 0.1 |
| Rock | Spire Coast | 20.8 | 1.4 |
| Rock, default | Abyss Cliffs, Lake Forest | 1.8 | 1.1 |
| Rock, default | Titan Forest | 1.9 | 1.2 |
| Rock, default | Savanna | 2.5 | 0.6 |
| Grass | Grass Fields | 7.2 | 2.0 |
| Canopy | Western Dune Forest | 7.6 | 1.1 |
| Canopy | Jungle Spires | 2.2 | 3.2 |
| Canopy | Red Jungle | 16.1 | 2.7 |
| Sand | the deserts and Savanna | 9.0 | 2.2 |
| Gravel | the deserts and Savanna | 2.5 | 1.7 |
| Cliff layer | Red Jungle, Jungle Spires, Red Bamboo Fields | 4.8 | 1.4 |
| CoralRock layer | the craters | 13.6 | 1.3 |
| Shell plates | Blue Crater | 27.2 | 0.0 |
| WetSand | everywhere | 4.0 | 0.5 |
| Swamp water | Swamp, 1.4 m | 5.7 | 0.1 |

The first-pass targets stay where they were: desert rock 0.5 to 0.1, Sand outside the
deserts 0.5 to 0.7, Grass outside Grass Fields 0.6, the dunes 1.3 and the Spire Coast
canopy 0.6. The Jungle Spires canopy was already close in hue and chroma and moves mostly
in lightness; its remaining 3.2 is the 15% of ground under the canopy. What is left on
the layers is mostly the biome tint, which is added after the transfer. Game-painted 17
dropped the Spire Coast rock and the two forest canopy rows and turned the WetSand row into
the rule ("Area targets" above).

### Known limits

- No target, for want of a clean reference: SandRock (Dune Desert and Spire Coast),
  SandPebbles, SandCracks, DesertRock, Soil (forest floor, swamp mud, Titan Forest),
  Puddles, the Red Jungle and Red Bamboo ground (RedJungle_LayerInfo, also in Crater Lakes),
  red grass, the jungle floor sand, the swamp canopy, the Abyss Cliffs Cliff layer, gravel
  outside the deserts, and the moss on the Titan Forest formations.
- Forest_LayerInfo still takes the canopy target, which makes the bare forest floor a
  vivid treetop green. The two references for the floor disagree by 35° in hue, so it has
  no target of its own yet.
- The Grass Fields grass target comes from one place, seen in four v1.1 shots; the biome
  is inferred from the flowers. Grass elsewhere keeps the Eastern Dune Forest target.
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
  (the default rock since game-painted 17, charcoal before) on the red-brown rock.
- The Red Bamboo Fields and Red Jungle lakes keep the sea fit: their reference water
  reflects a purple sky, which is not the swamp's look.
- Every target comes from tonemapped perspective screenshots; only the Grass Fields grass
  is from a top-down view.

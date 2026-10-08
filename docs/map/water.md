# Water on the renders: classes, rivers, falls and perched water

Sections 33, 34, 35 and 38 of the [design spec](../../DESIGN.md): how the renders class, level and draw
water. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

## 33. Water by class in the game-painted style (2026-10-05)

Inland water is not the sea. Drawn with the optics calibrated on the Spire Coast sea, rivers
come out sea-blue, the swamp a clear blue sheet, and sulfur ponds like the sea. So the
game-painted style gives each water body a class and each class its own optics.

### The class plane

`python -m mapgen paint` also writes `water_bodies.json` into the paint store: every water
actor with a box (class, world box, the materials its export subtree assigns) and every
root `StaticMeshComponent` whose mesh is under `/HotSpring/`. On build 502094 that is 839
actors and 101 terraces.

`gamedata/water/bodies.py` `classify` turns that into a uint8 plane on the 1 m grid, once per
render (about 5 s):

1. Each actor's class comes from its first known material (`MATERIAL_CLASS`):
   `MI_SLW_River_*` river, `MM_Lake_01` and `MI_Lake_Turquoise_01` lake (see "Lake colours"
   below), `MI_Lake_Blue_01` blue lake, `MI_WaterSwamp_Muddy` swamp, `MI_Lake_Caves_01` cave,
   `SulfurPond_Inst` sulfur, `MM_OceanMaster` ocean. 527 actors assign none. Of those, an
   actor whose class is in `ACTOR_CLASS` takes that: the 26 `BP_TranslucentWater_C` are
   translucent (see "Translucent water" below). The other 501 (the `FGWaterVolume` brushes,
   the falls, the lake and ocean spline tools) have no class of their own.
2. A hot-spring terrace standing in a lake box at most 150 m on a side (within 1 m of its z
   range) tints that lake around itself only; see "Hot springs" below.
3. Boxes paint wet texels whose level lies within 1 m of the box's z range, largest box
   first, so a pond inside a big box keeps its own class. No box but the ocean's claims the
   open sea: water within 1 m of the ocean level that the map's edge reaches through
   channels at least 96 m wide (a 48 m opening on a 4 m grid, kept where it comes within
   three radii of the edge). The swamp's `MI_WaterSwamp_Muddy` boxes are 230 m squares at
   the sea's level and reach past its coast, to x 3250 against the swamp area's 3040; without
   that rule they paint the open sea in straight steps. A lagoon behind a narrower mouth keeps
   its box. A box whose top lies under the sea's level claims no water at the sea's level (see
   "Lake boxes under the sea" below).
4. Unclaimed wet texels within 1 m of the ocean level are ocean. That includes the level-only
   water around the frame at about -16.3 m.
5. River boxes are settled body by body (`_settle_rivers`). See "River boxes" below.
6. The rest of an inland body (8-connected) takes the body's majority class when that class
   covers at least a quarter of it.
7. What is left is swamp in `Area_Swamp` and lake everywhere else.
8. Where swamp meets the ocean inside one body, the plane blends the two
   (`feather_mouths`; see "Swamp mouths" below).
9. The terraces tint their lakes (`tint_springs`).
10. Dry texels within 8 m of classed water take the class of the nearest (`fill_dry`,
    `DRY_FILL_M`); see "Dry texels beside water" below.

Build 502094, when the plane was first built: ocean 16.23 M texels, lake 1.20 M, river 0.38 M,
swamp 0.35 M, the turquoise lakes 53 k (lake since, below), hot spring 15 k, sulfur 9.7 k, cave
5.3 k, blue lake 5.2 k; 312 bodies claimed by material, 0.28 M texels by the biome fallback.
Translucent water has since taken 288 k texels from the lake, and the river boxes give back
what they claim outside their own bodies. The open sea is 14.92 M texels; it took 264,581 texels from the
swamp and 16 from a river, and nothing from any other class. Seeding it from the texels with no
ground as well would add 2 k, so the edge alone does.

The renderer samples the plane bilinearly with the dry taps dropped
(`terrain/sample.py` `ClassMix`), so a shore pixel takes its water's class rather than half
of nothing. A pixel whose wet taps agree takes its class's row exactly. A band holding only
ocean and dry texels takes the ocean path unchanged, and so does a render from a paint store
without `water_bodies.json`; the sidecar's `paint.water_classes` says which happened.

### River boxes (2026-10-06)

A `BP_River_PROT_C` box is one AABB around a whole river (section 34). Its rectangle reaches
into the lakes and the sea the river feeds, at their level, and painted like any other box it
leaves a straight-edged block of river colour in them, as at (-370, -868), (-1033, -366) and
(12, -70). The ribbons draw the river row wherever they speak, so the rectangle adds nothing
but the seam.

The boxes still paint in size order, then each river's texels are judged by the body they
lie in:

- A body is wet texels 8-connected through neighbours whose levels differ by at most 0.5 m
  (`level_bodies`, `BODY_STEP_M`). The unclaimed water at the ocean level outside the open
  sea counts, as ocean.
- Where the river holds more of the body than every other class together, and at least a
  quarter of it, the whole body is river, except what boxes smaller than its river box
  claimed. A small pond inside it keeps its class.
- Anywhere else the river's texels go back to what the other boxes painted under it, or to
  unclaimed. The sidecar's `river_box_texels_given_back` counts them.

Measured on nine 2.5 km tiles of build 502094's reconciled water, straight river edges (runs
of 8 m or more) inside one body fall from 2,544 texels to 39: two lake boxes smaller than the
river box, which keep their class. The rest lie on level steps between two bodies. The river's
texels go back to lake, ocean, swamp and hot spring. On the field's own planes
(`--kernel-only`) the river keeps 0.31 M of 0.38 M texels.

### Lake boxes under the sea (2026-10-06)

The field's level at a texel is the highest box top over it (section 19), stored in
decimetres. Four lake boxes stand under the sea's level. Two of them reach over water whose
level is the sea's: the Rocky Desert box at -17.046 m, 5 cm under the sea's -16.994 m
(x -1,100 to -870, y -1,475 to -1,116), and a box at -17.7 m (x -1,958 to -1,678, y -558 to
-329). The sea's surface stands over both, so the field holds the sea's level there. A channel
narrower than the open sea's 96 m is not open sea, so step 3 would let the lake boxes claim it,
and in the Rocky Desert they draw a dark green rectangle in the sea.

A box whose top lies under the ocean level (`OCEAN_LEVEL_M`) claims no texel whose level is
within half a decimetre of it (`SEA_ROUNDING_M`, the field's rounding): that water is the
sea's. A box under the sea whose water reads its own level keeps it, such as the pond at
-17.55 m near (3,825, -2,604). Boxes at or above the sea's level, the swamp's at -16.666 m
among them, are unaffected. On the field's own planes this turns 46,704 lake texels to ocean,
exactly the two boxes' sea-level texels.

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

Each source is tagged: **screenshot** (measured on in-game pictures), **game data** (read
from the material), **fit** (fitted to screenshots through a model) or **none**.

| Class | Body | Deep | k (r, g, b) /m | Turbidity | Source |
|---|---|---|---|---|---|
| ocean (the `water` block) | #5fa9a9 | #3c597d | 4.59, 3.97, 7.27 | 0 | fit: body, k and a deep tau of 3.9 m on the 1.0 sea screenshots by depth band (see "The sea by depth band"). Deep: screenshot, the 1.0 open sea from above (see "Sea deep and swamp water") |
| river | #4f7d78 | #2e5054 | 2.4, 1.6, 1.6 | 0 | screenshot: hue from the wiki's Rocky Desert river; clear, so the bed shows |
| lake | #56745b | #375a58 | 3.0, 2.2, 2.6 | 0.1 | screenshot: body jade near the shore; deep teal from 1.0 top-down screenshots (see "Lake colours") |
| lake_blue | #4a8494 | #22485a | 3.4, 1.0, 0.75 | 0 | game data: `MI_Lake_Blue_01` absorption (0.52, 0.15, 0.11) |
| swamp | #302627 | #302627 | 4.0, 4.5, 5.0 | 0.45 | screenshot: opaque near-black from above in 1.0 (see "Sea deep and swamp water") |
| cave | #2f4a47 | #1c2e2d | 3.0, 2.5, 2.5 | 0 | none: dark |
| sulfur | #67a395 | #3f8a7e | 3.0, 2.0, 2.0 | 0.3 | game data: `SulfurPond_Inst`, deep (0.17, 1, 0.89) cyan, shallow (1, 0.26, 0) orange as the bed tint |
| hot_spring | #68a098 | #46827d | 2.5, 1.6, 1.5 | 0.2 | none: milky turquoise |
| translucent | #708973 | #708973 | 1.4, 1.4, 1.4 | 0 | screenshot: the Blue Crater from above in 1.0 and 1.1, pooled #718a75. k: game data, the translucent material's opacity ramp (see "Translucent water") |

Targets follow the Spire Coast calibration (section 27): the reference colour times 0.85
linear for map exposure, OKLab L times 0.95 and chroma times 0.9. The river references are a
dusk shot at a grazing angle and a stream over white sand. They fix only the hue (a, b
distance 1 to 3), not the lightness.

### Lake colours (2026-10-06)

The lake body is jade, calibrated on the wiki's Lake Forest shot, and that jade is the Lake
Forest fog: the `Atmosphere_LakeForest` volume over the 131 m lakes has a noon inscattering of
#4a7354, Delta E 1.9 from the lake body #56745b, and starts at 0 m. The game's lake material
(`MM_Lake_01`, single-layer water) absorbs red most and green least and scatters nothing, so it
has no body colour of its own: shallow water reads green over its bed, and deep water reads
dark, lit mostly by the sky's reflection. In 1.0 screenshots from above, deep lake water reads
teal at hue 185 to 199 (the biggest Crater Lakes crater near top-down: #4b6c67), and near-shore
water green at hue 146 to 165.

So the body stays jade, which is right near the shore, and the deep colour is the teal #375a58
(hue 191), at `deep_tau_m` 6 m. Drawn through the painted layer's water over each window's
baked bed, the median Rocky Desert lake texel reads #607763 (hue 149) under 1 m, #4e6d5e (hue
163) at 2 to 4 m and #385b58 (hue 189) at 15 m and deeper; the Lake Forest, Crater Lakes and
mesa windows agree within 6 degrees of hue at each depth, but for the mesa's shallows over red
sand (hue 140 under 1 m).

The 14 `MI_Lake_Turquoise_01` lakes on the Desert Spires mesas are lakes: the instance's cooked
shader maps are hash-identical to `MM_Lake_01`'s and no actor sets anything the shader reads,
so in game they render exactly as it does. As lakes, these boxes fall under step 2's
hot-spring rule: the 230.3 m lake by the crash site at (1908, -2368) is a 114 m box holding
three hot-spring terraces near (1918, -2428), whose tint reaches 20 m around each of them.

### Sea deep and swamp water (2026-10-06)

The open sea's deep colour and the swamp's water come from 1.0 screenshots seen from above.

**One sky term.** Seen from above, deep water in the game is lit mostly by the sky it
reflects: its materials absorb and scatter little. One sky term for every class, (0.026,
0.047, 0.085) of the sun's light, put through the camera model with each material's own
absorption over its bed, fits the 1.0 screenshots of the sea, lakes, rivers and swamp to a
median Delta E of 5.2, most of it from depths that had to be guessed. Drawn alone it is
#3c597d. A test that needs no depth, the distance to the nearest point of each class's
colour-against-depth curve, gives the sea 2.8. A reflection per material, by its Fresnel term,
fits worse (13.2). The palette's own sky term, 0.02 of #96bee6, is about a fifteenth of the
fitted one. Source: fit.

**The sea.** Its deep colour is #3c597d; the body, the absorption and `deep_tau_m` are fitted
to the readings below ("The sea by depth band"). The full row the sky fit gives (k 1.69, 0.50, 0.36 per metre, 2.17 times
the game's absorption, with body and deep both #3c597d) would also draw the 1.0 lagoons blue,
at hue 228, where the screenshots read teal, so it is not taken. Source: screenshot, measured
by the method of section 31:

| Shot | What, light | Reading |
| --- | --- | --- |
| [3557686632](https://steamcommunity.com/sharedfiles/filedetails/?id=3557686632) | 1.0, open sea from a high rock, clear, about 50 degrees down | deep #2b557c (h 248) |
| [3735753140](https://steamcommunity.com/sharedfiles/filedetails/?id=3735753140) | 1.1, island fuel plant, clear, about 45 degrees | deep #385a85 (h 254), lagoon #3b6887 |
| [3755331272](https://steamcommunity.com/sharedfiles/filedetails/?id=3755331272) | 1.2.3, sand-bar islands, clear | channel #4b6f88, far deep #4a5d7e |
| [3552323039](https://steamcommunity.com/sharedfiles/filedetails/?id=3552323039) | lagoon over sand and coral, high angle, clear | bed showing #6ca0a7, open #3d6f7a |
| [3771500868](https://steamcommunity.com/sharedfiles/filedetails/?id=3771500868) | Spire Coast lagoon, high oblique, clear | #78969e (h 216) |

**The sea by depth band (2026-10-09).** The first sea row, k (4.08, 3.53, 3.53), body
#577f7e and a 12 m tau, was fitted to Spire Coast patches at depths matched by guess. Against
the readings above it drew the shallows too dark (L 0.57 to 0.59 against 0.65 to 0.67) and
the lagoons too teal (h 210 to 228 at 3 to 8 m against h 214 to 240). No reading has a known
depth, so each is matched to the nearest colour the row draws inside the band its shot shows:
the bed showing and the Spire lagoon 0.2 to 1.5 m, the lagoon, channel and open water 2 to
8 m, the deep 20 to 60 m, over the wet sand target as bed (both rules, #a68c72 and #a4947f).
k, the body and the tau are free, the deep colour stays #3c597d. The fit: k (4.59, 3.97, 7.27)
per metre, body #5fa9a9, tau 3.9 m; every reading within ΔE 2.5, mean 1.4, where the first
row's mean was 3.4 (shallows 9.3 and 6.9). It draws #739aa2 at 0.25 m, #5b9da3 at 1 m,
#4c7e90 at 4 m, #426884 at 8 m and #3d5c7e at 15 m. The body stands in for what the model
leaves out, the sky's reflection off the surface and the light the water scatters; a row with
the game's own absorption (0.78, 0.23, 0.166) lets the bed through to 2 m and draws the
shallows tan (h 75 to 96 at 0.25 to 0.5 m), which only the Update 8 editor footage shows.
On the 2048 sheet this row and the day's other colour changes (section 31: coral caps, wet
sand; section 27: area tints) move 1.23 million of the finest level's 4.19 million pixels, by
up to 55 levels: 0.84 million of them sea, 0.04 million inland water, 0.35 million land, all
but 75,000 of those by 8 levels or less.

**The swamp.** `MI_WaterSwamp_Muddy` absorbs about 20 times what the sea does, so swamp water
is opaque within 0.1 m and near-black from above. The opaque target is #302627. Source:
screenshot. A 1.0 video from a tower, looking down
([UD_1Rz7xryg](https://www.youtube.com/watch?v=UD_1Rz7xryg&t=170)), reads #332d31, and
cleared pools in 1.2 ([3743733077](https://steamcommunity.com/sharedfiles/filedetails/?id=3743733077))
read #2c1f1d; the two pool to #302627.

Shots through more of the swamp's fog read mauve: `Atmosphere_Swamp` inscatters #9977c1 (hue
304) at noon, at a density of 0.1 against the global 0.02 (source: game data), and lifts and
tints the wiki and Early Access shots (#9f8a88, #857475, #7b7472). Further off in the same video
the water reads #4f494e. The fogged #4d4a4e is the fallback if the near-black ever reads as a
hole.

The swamp class row takes the same colour as its body and its deep colour, so swamp-class
water outside `Area_Swamp`'s blur, which section 31's opaque colour does not reach, draws
near-black too: #322a2d, where the sky's 0.02 reflection lifts it a little. A body of
#302627 less that reflection (#2a180e) would land on the target exactly, but over so dark a
body a crown sunk 0.3 m in the swamp showed more than in the sea, which murky water must not
do (`tests/mapgen/test_crowns_over_water.py`). Which water takes the opaque colour is
section 37's rule ("Swamp water").

**Measured.** Drawn by the painted layer's own water code (`band_water_terms`, `class_optics`,
`underwater`, the tone, then `with_void`) at 1 m on the field's own planes, with the open sea
laid by `open_sea` from the artwork and the class plane from `classify`. The bed is the bake,
unlit and uncalibrated, so only the absolute colour of the shallows is approximate. Medians of
fully wet pixels clear of the void, display sRGB: the ocean and west coast window at
(-2515, 645) draws #5c7f81 at 0.5 to 1 m, #547b81 at 2 to 4 m, #4a6c7f at 8 to 15 m and #3d5b7d
at 40 m, the open sea (the optics read no deeper). Where the open sea fades into the void the
colour runs evenly to the page's navy, #3f5c7d at the sea's edge to #233340. Swamp water in the
swamp at (2343, 291) and at (2554, 608) draws #3e3434 at 0.1 to 0.3 m and #302627 from 1 m.

Against the dark colours of the map (Delta E, OKLab x100), the swamp water (L 0.28, hue 11) is
16.8 from a pit's black (L 0.11) and 13.7 from its grey lit edge, and 20 from the open sea at
40 m (L 0.46). It is 6.2 from the page's navy past the world's edge, and 4.1 from the void
where the sea fades into it (#1c2b37): the same lightness, the opposite hue. It meets only the
sea and the land: in these windows the only no-data beside swamp water lies under rock, near
(3000, 1150). The fogged #4d4a4e would sit 0.8 from a pit's lit edge and 2.5 from the void's
lit edge.

### Translucent water (2026-10-06)

The Blue Crater's lake is not `MI_Lake_Blue_01`. It is a `BP_TranslucentWater_C`, which
assigns no material of its own: the blueprint draws `TranslucentWater_Inst`. By the biome
fallback all 26 of these actors would be lakes, the Blue Crater a dark green #4b6a5d at its
depth. So they have a class of their own, `translucent`, by actor class (step 1).

On the field's own planes 14 of the 26 hold water, 288,398 texels. The Blue Crater is 110,272
of them, over a flat floor: depth p10, p50 and p90 2.1, 3.9 and 4.0 m. The others are a 454 m
lake in the Savanna at (533, 968), median depth 5.8 m; two lakes in the Western Dune Forest at
(-1271, 1705) and (-772, 1631), 4.0 and 12.3 m; a shallow lake in the Southern Forest at
(481, 2150); and smaller lakes and ponds in the Southern Forest, Titan Forest, Red Jungle,
Grass Fields, Western Dune Forest and No Man's Land.

**The row.** Body and deep colour are both #708973, the colour that draws the pooled target
#718a75 at the Blue Crater's 3.9 m once the sky's 0.02 reflection is added. At that depth the
bed adds under 0.2 Delta E, whether it is the bake's WetSand (#816652) or WetSand's calibrated
target (#a29583 then, #a4947f since 2026-10-09; section 31's rule). Source: screenshot, measured by the method of section 31:

| Shot | What, light | Reading |
| --- | --- | --- |
| [wiki Blue_Crater.png](https://satisfactory.wiki.gg/images/Blue_Crater.png) | 1.0 biome table, high angle, cyan-green grade | water over the floor #87a183 (h 141) |
| [3731866458](https://steamcommunity.com/sharedfiles/filedetails/?id=3731866458) | 1.1, Blue Crater rocket-fuel plant, hazy cyan | #5c7368 (h 163) |

The absorption is one number, 1.4 per metre, on every channel. A translucent material blends
its surface over what lies behind by an opacity, the same for every colour.
`TranslucentWater_Inst` sets `BaseOpacity` 0.9 at `Water Depth` 133 cm (`Depth Power` 1.5),
and 1.4 per metre with the inland floor of 0.35 leaves a tenth of the bed at 1.3 m. Source:
game data, read as an opacity ramp from the parameter names; the shader graph was not
decoded. Turbidity is 0, so the inland floor applies.

**Measured** as in "Sea deep and swamp water", the median Blue Crater texel draws #848571 under
0.5 m, #758a76 at 1 to 2 m and #718a76 from 3 m, Delta E 0.1 from the target at 3.7 to 4.1 m.
The other translucent water draws the same past 2 m, #718a76 to #708973 at every depth.

**The two that set their own colours.** Two actors set the blueprint's colour variables. Slot
2499 at (-1271, 1705), z 2.5 m, sets the primary colour to (0.386, 0.524, 0.583) and the
secondary to (0.376, 0.612, 0.740). Slot 2500 at (-772, 1631), z -54.7 m, sets the primary to
(0.616, 0.807, 0.759), the secondary to (0.561, 0.875, 0.805) and the deep colour to (0.315,
0.811, 0.922). The blueprint's defaults are primary (0.1, 0.131, 0.23), secondary (0.531, 0.655,
0.627), deep (0.712, 0.776, 0.885) and shallow (1, 0.932, 0.795). The default secondary equals
the instance's `WaterColor B`, but the primary and the deep colour differ from its
`WaterColor A` (0.205, 0.471, 0.683) and `DeepWater` (0, 0, 0). So how the blueprint hands its
variables to the material is not known without decoding its construction script, and the paint
store does not record them. Both lakes draw with the class row. By their numbers they read
bluer (2499) and turquoise (2500) in game.

**The sulfur ponds** are three `BP_Water_C` with `SulfurPond_Inst`. Its parent, `Lake_Inst`, is
the older translucent material with the same colour parameters (`WaterColor A` and `B`,
`DeepWater`, `ShallowWater`). None of them is a `BP_TranslucentWater_C`, and a known material
decides before the actor class does, so they keep the sulfur row.

### Swamp mouths (2026-10-06)

The class plane names one class per texel, and the renderer samples it bilinearly. Where swamp
meets the ocean, the class rows and the swamp's share of section 31's opaque colour would both
switch within one texel, a step of Delta E 26.7. The meeting lines lie on the open sea's 48 m
arcs (step 3) and on the swamp boxes' straight edges.

`feather_mouths` blends the two. It finds the texels where swamp and ocean are 8-neighbours at
levels within 0.5 m (`BODY_STEP_M`). Within 30 m of that line (`MOUTH_FEATHER_M`) each swamp
and ocean texel of the same body takes a swamp share: a smoothstep from 1 to 0, a half at the
line. The distance is counted in steps through water of the texel's own class, the 8- and
4-neighbourhood in turn, so the blend never reaches water behind a spit; the octagon it draws
reaches up to 33 m. Water at another level, such as a pool above a fall, is another body and
keeps its edge.

Plane values from `MOUTH_BLEND` (10) on are the blend, in 64 steps (`MOUTH_STEPS`).
`class_shares` gives each plane value its share of each class. `water_table` mixes the swamp
and ocean rows by that share, and the swamp's opaque share is the same share, so the class
optics and the opaque colour fade together. The sidecar's `mouth_blend_texels` counts the blend.

**Measured** on the field's own planes and drawn as above: 78,231 texels blend, all on the
swamp's sea coast, in 0.5 s on the full grid. Across the line, between neighbours whose depths
agree within 0.25 m, the largest step within 2 m of it is 3.3 to 4.1 Delta E on 1 m texels (p99
1.8) and 1.0 to 1.1 on 0.25 m pixels (p99 0.4), against 28 without the blend. The largest
steps left are the water's own: a texel 30.6 m deep beside one 1.1 m deep, and the swamp's
waterline. No texel farther than 33 m from a line changes class, and no drawn colour farther
than 60 m from one changes at all.

**Over the void (2026-10-07).** The swamp's boxes reach past its coast into No Man's Land,
where the artwork's sea runs on over no data to the void's edge, as at (3031, 1140). The open
sea's 48 m opening does not reach between the rocks there, so the boxes claimed that water,
and the blend carried the near-black swamp on into the void's sea. Ocean over no data
(`void`, the field's height `NODATA`) now takes no swamp: where swamp meets it, the blend lies
on the swamp's side alone, a smoothstep from none at the line to all of it 30 m in. On the
run's planes the blend shrinks from 90,125 to 85,168 texels; nothing else moves.

### Hot springs (2026-10-07)

A hot-spring terrace used to turn the whole lake box holding it into a hot spring: a 150 m
lake drew milky turquoise for one terrace, as at (1915, -2440) and (-950, -690). Now the lake
stays a lake and each terrace (`spring_terraces`: one standing in a lake box at most
`HOT_SPRING_BOX_MAX_M` on a side, within 1 m of its z range) tints the lake water around its
root (`tint_springs`): all of it within 8 m, along a smoothstep to none at 20 m
(`SPRING_REACH_M`), where the water's level lies in the box's range. The terrace meshes reach
20.7 m from their roots at the median of the 101 (p10 6.2 m, p90 40.1 m), measured on the
full-size mesh raster, and the mesh is drawn over its own footprint, so the tint shows as a
ring around the terrace and on its submerged steps.

Plane values from `SPRING_BLEND` (74) on blend the hot spring into the lake in 32 steps
(`SPRING_STEPS`), as the mouth blends do swamp into ocean. On the run's planes 5,505 texels
take the tint, where 18,170 drew as hot spring before; the sidecar's `spring_tinted_texels`
counts them.

### Dry texels beside water (2026-10-07)

Within the ocean's reach the coast is where the drawn surface crosses the sea's level
(section 27), so water is drawn over texels the class plane leaves dry: the artwork's
3.66 m blocks of dry ground inside the swamp's lagoons, at the swamp's level but under the
sea's crossing. The sampler gives a pixel with no wet tap the ocean's row, so those blocks
drew as teal squares in the near-black swamp, as at (2322, 265) and (2725, 280).

`fill_dry` gives every dry texel within 8 m (`DRY_FILL_M`) of classed water the class of the
nearest, a step at a time through the 4-neighbours in a fixed order. Drawn water there takes
the water's class around it, and the opaque swamp colour with it. A shore pixel whose dry taps
take the same class as its wet ones is drawn as before. On the run's planes 1.50 million dry
texels are filled, and the sidecar's `dry_texels_filled` counts them.

### Box seams (2026-10-07)

The field levels each wet texel at the highest box top over it (section 19), so where two
boxes meet inside one sheet of water the level steps along the box edge: a lake box over a
river's junction at (-1585, -480) stands 0.3 m over the river around it, and the depth, and
with it the tone, jumps along a straight line in every style. A box edge cuts the water's
extent too, where the lower box's level falls under the ground.

`palette/water/seams.py` feathers these steps in the level the renders draw (the open sea's,
or the surfaces' without it):

- **A seam** is a step of more than 0.15 m and at most 1 m between 4-neighbours
  (`BOX_SEAMS`) where the level runs on flat, within half a decimetre, to the next texel on
  both sides: a step between two box tops, not a sloped surface.
- **The feather.** Within 12 m of a seam, through the water, the level is a screened membrane
  joining the two sides of each seam and neighbours on one flat sheet, held to the level as
  it was over 4 m: a step becomes a ramp about 8 m long, and a slope or a fall is never
  smoothed over. No texel moves by more than half a metre, and past 12 m nothing changes.
- The membrane's sums are fixed in order (`terrain.solve.jacobi_cg`), so the level is the same
  bits on any machine.

The class plane and the relief styles' tint are still read off the levels as they were: the
feathered level would join bodies across 0.5 to 1 m seams and move 16,504 texels from river to
lake. On the run's planes 32,995 texels sit beside a seam and 377,942 texels move, by up to
0.5 m (p99 0.45 m), in 1.6 s.

### Shallow inland water (2026-10-07)

The painted style draws water by Beer-Lambert over its bed, and inland field water is covered
by the depth feather of `surface.water_alpha`, full only at 0.9 m. A pool 0.3 m deep was
therefore a third water and that third mostly its bed: hot springs, desert ponds and the
shallows of the Titan Forest and Red Bamboo lakes drew as dry ground, where the relief
styles show them, as at (-1405, -585), (2370, -2500) and (680, -170).

The painted palette's `shore.inland` sets a floor for inland field water, the share that is
neither the ocean's reach nor a river's:

- **Cover** (`edge_m`, 0.25 m): it covers its pixel fully once 0.25 m deep
  (`shore.inland_cover`), so the waterline still follows the ground.
- **Depth** (`min_depth_m`, 1.0 m): the optics read it at least 1 m deep (`optical_depth`), so
  every wet texel takes at least the tint of a metre of its class's water. A river keeps its
  own floor, 0.6 m once 2.5 m in from its bank (section 34).

### What the water fixes moved (2026-10-07)

Build 502094, against round 2's baseline; this section's rules with section 34's hand-over
and section 35's falls:

- **The 2048 render**, every level: painted 98,909 lit, 94,135 `@2x` and 98,452 unlit pixels
  (by up to 155 levels); relief 37,906, 36,321 and 35,881 (165); relief dark 34,225, 32,767 and
  35,186 (144); satellite 36,559, 35,073 and 36,331 (109); terrain 38,344, 36,581 and 37,645
  (200). Of the finest painted level's 45,200, 44,758 lie within 30 m of the water as drawn
  before; the rest along the river ribbons.
- **The light**: 6.6 million pixels of its horizon tiles move, 438,750 by more than 8 levels,
  and 16,056 of its normal tiles. The horizons read only the heights, which follow the water:
  the seabed rule keeps a render-only mesh or leaves it to the bed by the water level
  (section 27), and water the river reconcile now drops leaves the meshes in a channel standing.
- **Three windows of the full-size sheet**, unlit: painted 4.69 million of 117 million pixels
  (by up to 150 levels), relief 1.46 million (62), relief dark 1.34 million (145), satellite
  1.46 million (66), terrain 1.40 million (113), every one within 30 m of water drawn before
  or after.
- **The river reconcile** (section 34): water from river boxes 0.358 to 0.372 km², dropped to
  the ribbons 0.128 to 0.210 km², re-levelled 0.271 to 0.178 km², taken back by lower bodies
  0.041 to 0.026 km²; section 38 then re-levels 478 bodies where it did 455, and fills 31,619
  texels of holes where it did 36,892.

### Known limits

- The lake, the sea's deep colour and the swamp have been checked against screenshots from
  above; no in-game top-down check over a known depth has been made. A look from about 50 m
  over the open sea at (-3000, 645) and over the swamp at (2343, 291) would settle the two
  rows above. The sulfur pond, hot spring and cave optics come from material parameters,
  not from pictures.
- The sky fit also gives rows for the river and the blue lake. They are not applied.
- Translucent water draws one colour past 2 m. The target was measured at the Blue Crater's
  4 m only; the deeper translucent lakes (the Western Dune Forest one at (-772, 1631) runs to
  22 m) are unchecked. The two actors that set their own colours draw with the class row (see
  "Translucent water").
- Classes change at box edges. Where the water channel is itself built from boxes, as in the
  Red Bamboo terrace lakes near (420, 560), a chain of pools reads as a mosaic of classes.
- Two bodies more than 0.5 m apart in level are judged apart. Where the field levelled them
  on two boxes, they meet along a box edge, and the class changes along that straight line:
  0.64 k texels of river edge on build 502094.
- Where the swamp's lagoons open onto the sea, swamp turns to ocean along the edge of the
  opening: a line of 48 m arcs across one sheet of water, with nothing in the game to place
  it better. The blend of "Swamp mouths" takes the step off it (the p99 step per metre at the
  line goes from 27 to 1.8), but the dark water still follows the arcs, as a soft band 60 m
  wide.
- The blends join only swamp and ocean, and a terrace's tint and its lake. Any other two
  classes meeting inside one body still change within a texel.
- A box less than half a decimetre under the sea cannot be told from it by level. Two ponds
  inside the Rocky Desert's -17.046 m box, near (-989, -1,434) and (-978, -1,262), and two
  puddles beside them (6,993 texels in all) read the sea's level and draw as sea; the sea's
  box stands over them too.
- The hot-spring rule finds terraces in lake boxes near the sulfur ponds, in the Red Bamboo
  terraces and in the mesa lake by the crash site at (1908, -2368). Whether the water around
  a terrace is milky in game is unchecked, and the tint's reach is one number for every
  terrace, where the meshes range from 6 to 56 m.
- A seam over 1 m, such as a box edge across a lake whose two boxes stand 1.5 m apart, is
  left as a step: the rule cannot tell it from a fall.
- The terrain style still draws one water colour.

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
- The heightfield's water levels each river on the **actor's boxes**. Their union is one
  AABB around the whole river, rotated and 2.5 m tall, so its top is the highest point of the
  river plus up to 2.5 m. Inside the ribbons the field's river water stands 1.8 m above the
  plane at the median and 49 m at p90.

Measured: 25.6 km of centreline, half width p10/p50/p90 3.4 / 15 / 30 m (max 113 m, at lake
mouths). Plane footprint 0.92 km². 0.39 km² of it stands above the 1 m ground, and of that
0.06 km² was not water in the field. Where it shows, the river is 0.91 m deep at the median;
15% of it is under 0.3 m.

### The reader

`gamedata/water/rivers.py`, run inside the shared level sweep (`sweep_levels` keeps each river's
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
  nearer one in half widths wins. A plane standing more than 8 m over the ground
  (`RIVER_MAX_DEPTH_M`), or over none, gives way to one that does not: the short river over a
  fall's lip spreads its plane out over the basin 50 m below, and would hide the river in it.
- The whole map takes about 1 s.

### Reconciling with the field's water

`palette/water/rivers.py`, once per run, on copies of the field's planes. The field on disk is not
changed.

- **A river's own volumes are its box** (2026-10-07). A classless `FGWaterVolume` lying at
  least 90% inside a river's box, its top within 1.5 m of the box's, is the river's physics
  volume, not a lake's (`river_volumes`; 56 of the 270). Read as a lake's, its top kept the
  river box's water standing over the ribbon in a straight-edged block, as at the junction at
  (-1585, -480) and below the fall at (-1891, 320). A lake's own visible box, where it stands
  at the volume's top, still holds that lake's level.
- **A wet texel came from a river box** when its level equals that box's top within 5 cm and
  no other surface box stands as high: 0.358 km², 0.372 km² with the volumes.
- **Under another surface box** (a lake the river AABB overhangs), the texel takes that box's
  level, or goes dry where measured ground stands above it: 0.27 km². The exception is
  inside the ribbon where the plane runs more than 1 m above that box. There the box is a
  lake's AABB reaching over the river's valley, so it is not used.
- **Anywhere else where the ribbon speaks**, the texel is dropped and the ribbon draws the
  river: 0.088 km². Beside a step of more than 0.5 m left in the drawn plane, a fall, the
  ribbon draws nothing, so there the water stays at the higher plane's level and opens no
  crack at the lip (2026-10-07).
- **The ribbon speaks** inside its reach, except where the plane stands more than 8 m over
  the ground (`RIVER_MAX_DEPTH_M`) or over no ground. Those are wide sections hanging over a
  waterfall pit or a lake below. Drawn there, they paint fans of water in the air.
- Then a lower body takes back the box tops over its own bed, and water standing more than
  8 m over the river is taken out: section 38, "Boxes over lower water".

### Drawing

Per band, through `RiverWater.over`.

- **Coverage.** The plane is sampled bilinearly. The river covers a pixel where the plane
  crosses the drawn surface: the ocean's one-pixel crossing rule (`shore_terms`) with a level
  per pixel instead of -17 m. The banks are where the game's plane meets the terrain, not
  where a mask ends.
- **Presence fades** to 0 over 1.5 m inside the plane's edge, over 1.5 m before the 8 m depth
  cut, where the plane hangs 1 to 2 m over other water (`RIVER_OVER_WATER_M`), and on
  texels beside a jump of more than 0.5 m between neighbours (`RIVER_STEP_M`). Such a jump is
  two planes meeting, and any sampler draws a line along it. Over other water the plane fades
  in over 12 m from its edges and its open ends instead (`RIVER_MOUTH_FADE_M`, the other
  water's edge blended over 2 m), so a river ending in a lake leaves no square end.
- **Joints are one surface** (2026-10-07). Where two sections meet with a step of 0.5 to 2 m
  between their planes, the drawn plane is feathered across the joint over 8 m either side
  (`RIVER_JOINTS`, the membrane of section 33's "Box seams", through a surface sloping at most
  0.25 m a texel), and the river is drawn across it. The field's reconcile above reads the
  planes as they were. A step over 2 m is a fall and keeps its gap.
- **Where a river meets other water, the higher surface shows**, handed over along a ramp
  (2026-10-07): the river's share of the water runs from none where its plane stands 0.55 m
  under the other surface to all of it 0.45 m over (`RIVER_HANDOVER_M`, 1 m, centred on the
  5 cm tie). A plane meandering a few decimetres about a lake's level, as at (-1115, 1630),
  drew panels of river and lake colour wherever it crossed the tie, and a mouth a line; both
  now change tone along the ramp. Past the other water's last wet texel, away from the sea,
  the blur of its edge is no water to give way to (section 38, "Boxes over lower water").
- **Water a box leaves in the channel gives way at its edge** (2026-10-07). Where a lake's box
  keeps the field's water standing in a river's channel, its edge is the box's straight edge,
  and deeper than the river it drew a darker block, as at the junction at (-1585, -480). On the
  plane, other water gives way to the river at its edge whatever the levels, and holds its
  own only 12 m in (`RiverWater.yields`, over `RIVER_MOUTH_FADE_M`).
- **Optics.** River pixels get the shore optics: the opacity fade and wet darkening in
  terrain, and the wet band on the banks in every style that sets one. Painted water is
  Beer-Lambert, so the bed shows in the shallows.
- **No pale path.** A river 0.2 to 0.9 m deep seen through the 0.9 m alpha feather is mostly
  its bed paint. So the optics read a river at least `shore.river.min_depth_m` (0.6 m) deep
  once `bank_m` (2.5 m) in from its waterline, a taste setting per palette. At the waterline
  the true depth is used, so the bank stays soft.
- **Without rivers** (`--kernel-only`) the water terms are exactly recipe 6's.

### Cost

- `RiverWater` takes 8 to 11 s per run and about 1 GB of temporary 1 m planes; the joints'
  feather and the two distance planes over other water add about 4 s.
- Drawing a crop costs 5 to 20% more than recipe 6.
- When the cache misses, the sweep is the shared one; the rivers add little to it.

### Known limits

- Nothing has been compared against an in-game top-down view of a river. The minimum
  optical depth and the colour are a taste call.
- Where the 8 m rule cuts a river plane and no other plane speaks, the field's river-box
  water stays at the box level, for section 38 to re-level.
- Steps of more than 2 m per metre break the ribbon for a few metres. Those are
  waterfalls, which are a separate item. The water kept beside the lip (2,975 texels in 300
  runs) still steps by the fall's height, so the lip reads as a line under a metre wide, as
  at (-999, 1575); that fall is not drawn, because the falls are prepared on the field's own
  water, whose river volume stands over its lip.
- The river's colour model is parked: the ramp makes the hand-over continuous, but a river
  in a lake of another class still draws a band of mixed colour along its course.
- A section bent more tightly than its half width draws the fan its mesh would.

## 35. Waterfalls and the first small-mesh batch (2026-10-05)

Two additions to the satellite and game-painted styles. The heightfield is unchanged, and the
recipe number stays 6. A render records them as two provenance inputs, `waterfalls` and
`render_meshes`, plus each style's palette digest. Build 502094.

### Where the falls come from

Every waterfall on the map is a `BP_WaterFallTool_02` actor: 191 of them, 185 inside the map
frame. The tool hangs a vertical curtain of `SM_Waterfall_Side_Module` instances, each 2 m wide
and 10 m tall, from a lip. It lays `SM_Waterfall_Top_Module` instances (8.05 m of rushing
water) upstream of the lip, and on 148 of the falls it puts `SM_SplashModule_Mid` discs where
the water lands. The modules are instanced components whose mesh and attachment come from the
class template, so the reader composes them onto the actor's root itself.
`gamedata/water/falls.py` turns one actor into one record:

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
version, and deletes them with the caches unless `--keep-direct` is given. The records are
sorted by their lip's `x`, `y` and `z` as soon as they are swept, and drawn and digested in
that order whether they came from the sweep or the cache: overlapping falls blend one after
another, so the order moves pixels. Until 2026-10-07 a run that swept them drew them in the
sweep's order and only the cache was sorted. Drawn on the field's surface, the two orders
differ in 22 pixels of the satellite and of the painted layer at full size, each by one level
(4 at 16384 and at 8192, none at 2048), so a palette-only restyle did not equal the full run
it followed, and the two recorded different `waterfalls.digest`s for the same 191 records.
Both now record the cache's, `sha256:7badef35…` on build 502094, where a full run recorded
`sha256:bf3ae0ac…`; no 2048 tile moved. At 8192 a restyle from the cache a full run kept
(the light it kept installed, the numba kernels against that run's numpy) gives all 18,085
tiles the same bytes as that run, light included. The 8
`SM_WaterfallMesh_01` and the one `Waterfall_Top_01` are backdrop meshes outside the playable
area and are not read.

### Which falls are drawn

`palette/water/falls.py` prepares the records against the field once per run. The drop is measured
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

**No white box (2026-10-07).** Softened over a pixel, a streak across a 32 m lip drew as a
hard white rectangle, as at (740, 285) and (2040, -1880). Its sides now fade along a
smoothstep over half the lip's half width, and its far end over half the spread (`soft`,
0.5), so the foam thins out towards the ends of the lip and the end of its run.

**Under the trees (2026-10-07).** The falls are drawn after everything else, so a crown or
the Titan canopy over a lip drew the foam on top of the trees, as at (1805, 480) and
(2278, 775). In the painted style the foam and the mist now go under what the crowns and the
Titan trees cover of the pixel (`trees.canopy_cover`), worked out only for a band a fall
comes near.

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
- The 102 falls off the edge of the world stay out. The artwork draws them as curtains on
  the void's edge, and since section 26's void follows the artwork they could be drawn
  there; whether they should is still open.

## 38. Perched water: a box top that is not the surface (2026-10-05)

The field levels each wet texel at the highest water-box top over it (section 19). Three
kinds of box break that rule:

- **A sloped river.** `BP_River_PROT_C` and some `FGWaterVolume` boxes span a whole reach,
  so the box top is the river's upstream end. `BP_River_PROT_2` at (314, -1489) runs north
  down a desert channel from about +18 m to the sea at -17 m. Its box spans -22.8 to
  +22.0 m, so the field levels the whole channel at +22 m, 39 m above the bed at the mouth.
- **A box over another body.** Where one body's axis-aligned box covers part of a lower
  body, the higher top wins. At (831, -353) a 131 m box sits over a lake at 58 m.
- **A box over its own fall.** At (1784, 559) the 94.8 m lake's `FGWaterVolume` reaches to
  x 1863, over the basin 113 m below, which the swamp's boxes cover at -16.7 m. The ground
  falls 100 m between two neighbouring texels at the lip.

Drawn as the field has it, that is tens of metres of water. In the painted style it is opaque
Beer-Lambert water blended almost fully to the open-sea colour, in a flat shape with the
artwork mask's 3.66 m block edges, because the depth feather never fades out on a 40 m
depth.

`palette/water/perched.py` re-levels these bodies before drawing. The field is not changed. It
runs on the water the rivers left (section 34), and the painted style's water classes
(section 33) and the relief styles' water tint are taken from its result, so every consumer
draws one surface. Part of recipe 7.

- **Bodies.** Measured water, split into connected texels of one level. The ocean level
  (within 0.5 m of -17 m) is exempt.
- **Banks.** Dry ground at its height, and other water at its level. Dry ground the water
  encloses is not a bank: the artwork draws deep water too dark for its blue test.
- **The test.** A body is perched when its level stands more than 2 m above any bank, and
  more than 25% of the banks 6 to 24 m from it stand more than 2 m below the level, which is
  where still water at that level would run to. The ring test is what separates this from
  a lake drawn smaller than its water: there the dry ground below the level is a thin rim
  and the banks rise beyond it.
- **The surface.** Each shoreline texel takes the highest level its neighbours allow:
  `min(level, bank)`, never below its own ground. A harmonic membrane spans the shoreline,
  so a river's surface runs downhill with its banks. A texel keeps the box level within
  2 m of the membrane, takes the membrane at 4 m, and is handed over linearly between.
  The result is never above the box top.

Depth is then measured as for any other water, so the colour is a river's and the edge is
the usual depth feather, which follows the ground rather than the mask blocks.

### Water below a drop (2026-10-06)

A body judged as one would let the big lake's high banks outvote the basin's ring at the wide
fall, and the basin would draw at the lake's level, 113 m deep; one membrane would span the
drop and end in a straight hand-over line.

- **The cut.** `_below_drops` cuts each body where the ground falls more than 8 m
  (`LIP_DROP_M`) between eight-way neighbours. A part with no ground within 8 m of the level
  is water below a drop.
- **The test.** That water is judged first, and is perched when its own ring or the whole
  body's spills.
- **The surface.** Its membrane spans only shoreline more than 2 m below the level and within
  8 m of its own ground: beside the water it joins, or failing that its own low banks (the
  plateau at (-1101, -296)). The drop and the cliffs hold nothing up. The basin under the
  wide fall sits at -17.4 to -16.7 m, the swamp's level.
- **The rest of the body.** Once re-levelled, that water leaves the body and bounds the rest
  at its new level. A body with no re-levelled water below a drop is judged as one.

8 m was measured, not derived: at 10 m the plateau stays joined through one 10.0 m step, and
at 5 m 494 texels draw deeper than at 8 m and 1,454 shallower. The falls records (section 35)
are not read.

### Measured (build 502094)

On a render's planes, after the river reconcile and the lower bodies' take-back of "Boxes over
lower water", 42,550 texels in 455 bodies are re-levelled, in about 4 s. Lakes whose banks
stand above their level, the ocean, and level-only water are byte-identical.

### Holes in a lake

The artwork's blue test also reads a lake's deep middle as dry, and the water under an arch
or bridge it draws across a lake. The field keeps those texels dry, so drawn as it stands a
lake shows its bed there: dark-middle blobs, and in the cliff-ringed lakes near (1980, -1890) a
straight strip 18 m wide and 220 m long under the arch, and the spokes of the star-shaped rock
beside it. `wet_holes` fills them after the re-levelling, on the same bodies:

- **Inside.** The measured water at the body's level, and other inland measured water
  within 2 m of it, closed over gaps up to 24 m wide (`HOLE_BRIDGE_M` 12), with what that
  encloses.
- **Below.** Dry ground standing below the surface of the body's nearest texel.
- **Not where it spills.** Nothing within 12 m of ground outside that shape standing more
  than 2 m below the surface and running on past 12 m from the water, where still water
  would run to. The rounded ends of a bridged gap are not a spill.
- **Deep, but not a drop.** A connected part reaching the body, more than 2 m deep at its
  deepest and nowhere deeper than 15 m (`HOLE_DEPTH_MAX_M`). A sandbar awash stays as the
  artwork drew it. Without the cap 172 parts (6,700 texels) go deeper, up to 181 m: the
  wide fall at (1791, 553), cliff feet at (470, -563) and (-1916, 345), and the drop east
  of the arch lake. A real lake middle deeper than 15 m would stay dry too.

A hole takes the surface of its body's nearest texel and the measured grade; where two
bodies close over one gap, the lower surface. Water the river reconcile dropped stays
dropped, the ocean is never a body, and every texel that was water is byte-identical. Every
style draws the result: the open sea (section 26) is laid on these planes, and the renderer's
wet and measured planes, the class plane (section 33) and the relief tint read them.

Measured on the field's own planes: 54,733 texels in 394 bodies, about 4.5 s and 760 MB
at peak (the re-levelling peaks at 1.1 GB). Median depth 2.0 m, 99th percentile 9.2 m.
The largest are the arch lake at (1979, -1889) with 5,062 texels, (3596, -2139),
(2245, 741), (3149, -606) and the arch lake's east arm at (2026, -1904). In a render, on
the water the river reconcile leaves: 38,048 texels in 306 bodies.

### Boxes over lower water (2026-10-06)

At (-1077, -294), below the fall out of the crater lake at (-1033, -366), the basin drew as an
axis-aligned rectangle about 60 by 130 m: a thin sand rim along straight, stair-stepped edges,
a dark strip down its west side, and the river around it.

- **The cause.** The basin floor stands at 100 m and the river below the fall runs over it at
  101 to 105 m. The field levels a texel at the highest box top over it (section 19), and
  three boxes reach over the basin from above: the lake's two `FGWaterVolume`s (153.7 and
  153.9 m) and the short river over the lip (154.4 m). Outside them the lower river's own
  box gives 111.4 m. The river reconcile (section 34) dropped the water outside, where the
  ribbon speaks, but re-levelled the water inside to the lake's 153.9 m, because no river
  plane ran above that. This section then re-levelled that piece below a drop to about
  101 m, a membrane that kept 142.9 m in the hand-over column along its west edge: the
  rectangle, its dark strip, and the field's own water edge inside the river.
- **The rim.** Past a field water's last wet texel its edge is still blurred, and there the
  missing measured share reads as 40 m deep, so the river gave way to the blur and drew
  ground: a sand line along every edge of field water inside a ribbon.
- **The class.** A higher body's box over lower water, wherever its top is taken as that
  water's level: the field's rule, the reconcile's re-levelling to the highest other box,
  and this section's membrane over a box piece, which spans from the lower water up to the
  higher banks and drew navy blocks in lakes, as at (-274, -785) and (451, -547).

Four rules, all in the renderer. The field is not changed.

- **A lower body takes its water back** (`gamedata.water.channel.lower_bodies`, in the river
  reconcile after the river boxes' water). Each surface box with a level of its own, lowest
  first, floods from the wet texels at its top every measured texel joined to them, under
  the box, whose ground stands below the top and whose level stands more than 2 m above it
  (`LOWER_BODY_STEP_M`). A river's AABB has no level of its own, and boxes at the ocean's
  level are left to section 26. 41,100 texels.
- **Water over the river** (`palette.water.rivers._over_the_river`). In a river's valley, where the
  ribbon speaks and the ground stands at most 8 m over its plane (`RIVER_MAX_DEPTH_M`), water
  more than 8 m above the plane is a higher body's box over the river. It takes the plane's
  level and goes where the ribbon draws it or the ground stands above the plane, so the
  ribbon's own gaps (its steps, the reach past its edge) stay water. 41,400 texels. A lake
  whose bed stands more than 8 m over a plane running under it, as the crater lake does
  over the river's fan, keeps its level.
- **A hanging plane gives way** (section 34): the lip's river plane, 50 m over the basin,
  does not hide the river in it.
- **The blur is no water** (section 34, "Drawing"): past the other water's last wet texel,
  away from the sea's reach, the river draws.

Measured in process on the whole field (water stages only, 3.4 GB at peak, the same 4 s for
the reconcile), as straight runs of at least 16 m along one grid line of the drawn water,
counted where they lie on a surface box's edge, inland: 33 runs (855 texels) in the drawn
water, field and ribbons together, against 89 (2,378) without these rules, and 44 runs (1,216
texels) on the field's planes alone. 83,423 texels of the water planes every style draws
change, in 42 places merged at 100 m; the swamp, the Blue Crater, the sulfur pools and the
ocean coast draw byte-identical.

### Known limits

- Where a river spline speaks, its ribbon (section 34) has already taken the box's water
  back before this runs, so the spline's own surface replaces the membrane there. The
  membrane is left for boxes without a spline.
- A box piece over another body passes the ring test only when enough of its ring is below
  it. Where the lower body's own water reaches the piece over its bed, it is the lower
  body's before this runs ("Boxes over lower water").
- Left after "Boxes over lower water": a 93 m straight line at x -1369 near (-1265, -323), a
  0.5 m step between the lower river's box and its ribbon; the river strips at (-1126, 218)
  under a 201.6 m volume standing 2.8 m over the ribbon, inside the 8 m rule; faint lines in
  the arch lakes near (1764, -2122); and the wide fall's channel at (1850, 313), whose lower
  water is the swamp at the ocean's level.
- The arch lake near (1980, -1890) ends in a straight line at y -1733, the edge of its
  `FGWaterVolume`. South of it the only box is the ocean spline's at -17 m, so the field's
  water rule (section 19) drops the artwork's water there as ground standing out of the
  sea. That is the field's level rule, not a hole, and this section leaves it.
- A faint straight line remains beside the wide fall, 48 m from the swamp's edge, in
  terrain, satellite, relief and relief dark; painted shows no step. The shore rule's ocean
  reach is found on the field's own planes, before the re-levelling. Finding it on the
  re-levelled planes would also change the drawing around the older perched bodies.
- About 50 texels at the fall's foot keep 94.8 m: corner-joined pieces with no shoreline of
  their own, smaller than a pixel at 2048.
- A sloped reach below a drop that joins lower water takes that water's level along its
  whole length, so its upper end would draw dry. No such case shows on this build.
- This section leaves level-only water alone. At the ocean's level it draws on section 26's
  open-sea bed; away from it, it keeps the deep tint.

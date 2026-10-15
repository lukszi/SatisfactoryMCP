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
colour `body`, the `deep` colour and its `deep_tau_m`, a `turbidity` and a `bed_tint`. The
sea's row is the `water` block's, built from the game's own absorption (see "The sea's physics
row" below).

```
T     = (1 - turbidity) * exp(-k d)
under = bed * 0.8 * bed_tint * T + body * (1 - T) + 0.02 sky
under = lerp(under, deep, 1 - exp(-d / deep_tau))
```

The 0.8 is the wet bed (`bed_wet`). Turbidity is an opacity floor: murky water hides its bed
even at the edge. The bed tint stands for a stained bed, the sulfur pond's orange rim. The sea
takes the same formula with turbidity 0 and, in place of `0.8 * bed_tint`, the scale on what
is seen behind its surface ("What is behind the sea's surface" below).

Each source is tagged: **screenshot** (measured on in-game pictures), **game data** (read
from the material), **fit** (fitted to screenshots through a model) or **none**.

| Class | Body | Deep | k (r, g, b) /m | Turbidity | Source |
|---|---|---|---|---|---|
| ocean (the `water` block) | #385478 | #3c597d | 1.695, 0.498, 0.363 | 0 | game data times a fit: k is 2.17 times `MM_OceanMaster`'s absorption; body and deep are the sky term #3c597d, the body less the surface's reflection; deep tau 100 m; the bed at the material's `Ocean SBW` 1.3 times (0.8, 0.94, 1.3) (see "The sea's physics row") |
| river | #4f7d78 | #415d64 | 2.5, 1.198, 1.697 | 0 | game data times a fit: k is 5 times `MI_SLW_River_Base_01`'s absorption (`absorption_scale` in the row); body the wiki's Rocky Desert river; deep the readings' hue fit, tau 2 m; no inland floor under a ribbon (see "The river's row, teal in the shallows"). 2026-10-14 only: the sea's sky term #385478, #3c597d at 2.17 times the absorption. Before: #4f7d78, #2e5054, k 2.4, 1.6, 1.6 |
| lake | #56745b | #375a58 | 3.0, 2.2, 2.6 | 0.1 | screenshot: body jade near the shore; deep teal from 1.0 top-down screenshots (see "Lake colours") |
| lake_blue | #4a8494 | #22485a | 3.4, 1.0, 0.75 | 0 | game data: `MI_Lake_Blue_01` absorption (0.52, 0.15, 0.11) |
| swamp | #302627 | #302627 | 4.0, 4.5, 5.0 | 0.45 | screenshot: opaque near-black from above in 1.0 (see "Sea deep and swamp water") |
| cave | #2f4a47 | #1c2e2d | 3.0, 2.5, 2.5 | 0 | none: dark |
| sulfur | #67a395 | #3f8a7e | 3.0, 2.0, 2.0 | 0.3 | game data: `SulfurPond_Inst`, deep (0.17, 1, 0.89) cyan, shallow (1, 0.26, 0) orange as the bed tint |
| hot_spring | #68a098 | #46827d | 2.5, 1.6, 1.5 | 0.2 | none: milky turquoise |
| translucent | #8fa685 | #375a58 | 1.4, 1.4, 1.4 | 0 | screenshot: the Blue Crater from above in 1.0 and 1.1, pooled #718a75 at its 3.9 m; deep the lake's teal. k: game data, the translucent material's opacity ramp (see "Translucent water") |

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

**The sea.** Its deep colour is #3c597d. From 2026-10-09 to 2026-10-14 the body, the
absorption and `deep_tau_m` were fitted to the readings below ("The sea by depth band"). Since
2026-10-14 the sea draws the full row the sky fit gives (k 1.69, 0.50, 0.36 per metre, 2.17
times the game's absorption, with body and deep both #3c597d; "The sea's physics row"), and
sees its bed at the material's scale ("What is behind the sea's surface"). Source:
screenshot, measured by the method of section 31:

| Shot | What, light | Reading |
| --- | --- | --- |
| [3557686632](https://steamcommunity.com/sharedfiles/filedetails/?id=3557686632) | 1.0, open sea from a high rock, clear, about 50 degrees down | deep #2b557c (h 248) |
| [3735753140](https://steamcommunity.com/sharedfiles/filedetails/?id=3735753140) | 1.1, island fuel plant, clear, about 45 degrees | deep #385a85 (h 254), lagoon #3b6887 |
| [3755331272](https://steamcommunity.com/sharedfiles/filedetails/?id=3755331272) | 1.2.3, sand-bar islands, clear | channel #4b6f88, far deep #4a5d7e |
| [3552323039](https://steamcommunity.com/sharedfiles/filedetails/?id=3552323039) | lagoon over sand and coral, high angle, clear | bed showing #6ca0a7, open #3d6f7a |
| [3771500868](https://steamcommunity.com/sharedfiles/filedetails/?id=3771500868) | Spire Coast lagoon, high oblique, clear | #78969e (h 216) |

**The sea by depth band (2026-10-09, replaced 2026-10-14).** The first sea row, k (4.08, 3.53, 3.53), body
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

**Measured** (2026-10-06, on the sea row of that day). Drawn by the painted layer's own water code (`band_water_terms`, `class_optics`,
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

### The sea's physics row (2026-10-14)

The sea is drawn with the game's own absorption and the sky term of "Sea deep and swamp
water", where the depth-band row fitted its own absorption and body to the screenshots.

**The absorption** is the ocean material's. `MM_OceanMaster` is a base material, a
single-layer water whose `Ocean Base Color` is black: it absorbs and scatters nothing back.
Its parameter defaults are kept in the cooked material's cached expression data, after the
export's tagged properties: `Ocean Absorption Color` (0.5208, 0.1531, 0.1116), `Ocean
Murkyness` 1.5 and `Ocean SBW` 1.3 ("What is behind the sea's surface"). The first two's
product, (0.781, 0.230, 0.167), is read as the absorption per metre. `gamedata/water/ocean.py`
reads them from the install when a render prepares
(`read_ocean_absorption`, reader `ocean_absorption` 2), and the painted sidecar records them
under `sea_absorption`. Where the install's material does not read, the sea takes the
palette's `k_per_m`, the same product on build 502094, and the sidecar says so. Source: game
data. That the shader multiplies the two, and in those units, is read from the parameter
names; the graph was not decoded.

**The scale.** The sky fit puts each material's own absorption through the camera model over
its bed, under one sky term for every class (median Delta E 5.2 over the sea, lake, river and
swamp shots). The sea's absorption lands at 2.17 times the material's: k (1.695, 0.498, 0.363)
per metre. `absorption_scale` holds the factor. Source: fit; the fit's own files are gone, so
only these numbers are kept.

**The row.**

```
k     = absorption_scale * absorption
body  = sky_term - surface_r * sky
deep  = sky_term
under = bed * behind * exp(-k d) + body * (1 - exp(-k d)) + surface_r * sky
under = lerp(under, deep, 1 - exp(-d / 100 m))
```

- At no depth the sea shows its bed, at the scale behind its surface, and the surface's
  reflection, as every water does.
- Deep water draws the sky term #3c597d itself: the body is the sky term less the 0.02
  reflection the painter adds over every water, so body and reflection sum to the deep
  colour. The sky term is what the open sea shows; the reflection is part of it.
- Each channel runs once from the bed to the sky term, never back.
- `deep_tau_m` 100 m. A pure Beer-Lambert row has no deep blend, but the class rows mix
  linearly where classes meet (a river's ribbon fading into the sea, the swamp's mouths, a
  shore pixel's taps), and an infinite tau would mix into nothing. At 100 m the blend adds
  0.01 per metre to the blue's 0.363, under 3 %.

Over the wet sand target (#a4947f), with that day's wet bed of 0.8, it drew #7f8478 at 0.25 m
(hue 123), #6e8078 at 0.5 m (169), #567879 at 1 m (198), #426d7a at 2 m (219), #3d667b at 3 m (233), #3c617c at 4 m
(241), #3c5a7d at 8 m and #3c597d from 15 m. Against the screenshot readings of "Sea deep and
swamp water", matched by depth band as in "The sea by depth band": mean Delta E 3.2, max 8.5,
where the depth-band row reached 1.3 and 2.4. The lagoon, channel and deep readings sit within
2.6; the two shots of bed showing through (#6ca0a7, #78969e) miss by 8.5 and 6.1, since the
bed shows through clear water at 0.2 to 1.5 m as sand, not as teal.

Tests: `tests/mapgen/test_sea_row.py` (the row by depth on the numpy reference, the table, the
install's read). The underwater kernel and its reference are unchanged: the row is data.

**Measured** on a 2048 render of the painted layer (build 502094), against the same level of
the full-size sheet before: the channel between the western islands near (-2560, 760) draws
#3d6374 (hue 228) where it drew #518996 (hue 214), the shelf west of them #3c5a79 where it
drew #4a7c8e, and the open sea keeps #3c597d. In the clearer water the seabed carpet's patches
(section 32) show light blue, as in the northern lagoons near (-100, -2020); the carpet's
colour and strength were set under the teal row.

### What is behind the sea's surface (2026-10-14)

The physics row of the day before drew the shallows over sand grey-green and missed the two
readings where the bed shows through (#6ca0a7 by ΔE 8.5, #78969e by 6.1), and the seabed
carpet, tuned under the teal row, showed through the clearer water as light blue blotches.

**The scale behind the surface.** `MM_OceanMaster` has a third parameter for what is seen
from above through it: `Ocean SBW` 1.3, among the same cached defaults
(`read_ocean_absorption`, reader `ocean_absorption` 2). A single-layer water material scales
the scene behind its surface by `ColorScaleBehindWater`; SBW is read as that ("scene behind
water"), from its name. Source: game data. Everything the sea shows of what lies under it is
scaled: the bed, the coral, the carpet and the sunk crowns.

```
behind = SBW * behind_tint = 1.3 * (0.8, 0.94, 1.3) = (1.04, 1.22, 1.69)
under  = bed * behind * exp(-k d) + body * (1 - exp(-k d)) + surface_r * sky
```

**The wet bed.** The 0.8 of the wet bed stays in the tint's red: the 1.0 Spire Coast shot
([3771500868](https://steamcommunity.com/sharedfiles/filedetails/?id=3771500868)) reads the
water at a sandy beach's edge at the dry sand's lightness and grey (L 0.60 to 0.62, C 0.01,
against the dry sand's L 0.61), which the row meets within 3 L at 0.1 m. At SBW alone (1.3
on every channel) the edge drew 6 to 9 L over the beach, a white rim.

**The tint.** With the game's absorption the bed through the water turns green before blue:
its blue keeps at most 1.37 times its green, so sand, whose blue is 0.6 of its green, never
reads cyan, where the screenshots show the sandy shallows turquoise. The tint's green and blue
are a fit, by the method of "The sea by depth band", to the eight readings and four samples of
the Spire Coast shot over sand at map exposure (#6f8888, #5d878a, #4e727e, #2d5b6f, boxes
clear of reflections), with the red held at the wet bed's 0.8: green 0.94, blue 1.3. Source:
fit. It may stand for the material's caustics (`CausticsSize` 600, not drawn), which light
the bed under the shallows.

**The absorption's scale and the deep blend** do not move. Over the readings and samples the
fit is flat in `absorption_scale` from 1.5 to 3 and in `deep_tau_m` from 8 to 100 m (mean ΔE
within 0.05): a reading's depth is matched by band, so another scale only moves its match.
The 2.17 has a reading of its own: light reaching the bed goes down through the water and back
up, and a 45° sun refracts to 32° in water, so the path is 1 + 1/cos 32° = 2.18 times the
depth.

**No larger reflection.** A row in which most of the sky term is the surface's reflection, not
the water's body, fits the readings as well (mean 1.48 at a 0.75 share), but light added over
the surface brightens whatever is under it: nothing in the shallows could read darker than
L 0.42, and the crater-grass patches in the same Spire Coast shot read L 0.23 to 0.32. The
reflection stays 0.02 of the sky.

**Score.** Against the eight readings, matched by depth band: mean ΔE 1.54, max 2.6 (the row
before: 3.2 and 8.6; the depth-band row: 1.3 and 2.5). The three deep readings alone cost 2.4,
1.3 and 2.3, the sky term's own miss. The four Spire Coast samples: mean 1.4 (the row before:
4.0). Over the wet sand target (#a68868) the row draws #909288 at 0.25 m, #5d8385 at 1 m,
#447583 at 2 m, #3c6480 at 4 m and #3c5b7e at 8 m; over pale sand (#d4c2a2) #b6ccca at
0.25 m, #70b2bb at 1 m and #4996ab at 2 m, the turquoise of the game's lagoons.

**Measured** on windows of the full-size sheet drawn through the renders command's own
preparation, 240 m across at (-100, -2020), (400, -2100), (-430, -2200) and (-2560, 760): fully
wet lagoon water 1 to 1.6 m deep draws #537f88 to #5d8385 (L 0.57 to 0.58), and a crater-grass
patch at that depth #38445c to #39475e (L 0.39), where the Spire Coast shot reads the patches
L 0.23 to 0.32 against water L 0.42 to 0.58.

Tests: `tests/mapgen/test_sea_row.py` (the scale behind the surface, the readings by depth
band, the install's read) and `tests/mapgen/test_carpet.py` (the patches darker than the water
over sand around them).

### The river's physics row (2026-10-14)

Rivers are drawn as the sea is ("The sea's physics row"): the game's own absorption over a wet
bed, under the one sky term. Shallow water shows its bed; deep stretches and pools turn blue.

**What the game data says.** `MI_SLW_River_Base_01`, the river material, is an instance of
`MM_SLW_River_01`. The instance sets `WaterAbsorptionColour` (1.0, 0.479, 0.679) and
`WaterMurkiness` 0.5 (the base's defaults are (1.0, 0.391, 0.402) and 1.0). Read as for the
ocean, their product (0.5, 0.240, 0.339) is the absorption per metre. It takes red first and
keeps green longest, so a metre of river water passes (0.61, 0.79, 0.71): green-grey, not
teal. `gamedata/water/river_material.py` reads them when a render prepares
(`read_river_absorption`, reader `river_absorption` 1). It follows the parent chain for a
parameter the instance does not set, down to the base material's cached defaults, as the
ocean reader does. The painted sidecar records them under `river_absorption`. Where the
install's material does not read, the river takes the palette's `k_per_m`, which holds the
same numbers on build 502094. Source: game data; the shader graph was not decoded.

Three more parameters were looked at and left out:

- **Scattering.** The instance scatters a little, mostly blue: `WaterScatteringColour`
  (0.075, 0.080, 0.333) at `Scattering` 0.5. Added to the body at half strength, it lifts deep
  water to #486d9c and misses the readings by more (mean ΔE 7.4).
- **`WaterColor`.** The base keeps a `WaterColor` of (0.025, 0.057, 0.063), dark teal #2c4347
  alone. Added to the sky term at a quarter, half and full strength, it scores mean 5.2, 5.6
  and 6.5 against the 4.8 below.
- **No scale behind the surface.** The river material has no parameter like the ocean's
  `Ocean SBW`.

**The row.** k is `absorption_scale` (2.17, the sea's) times the game's absorption, so
(1.085, 0.520, 0.737) per metre. Body and deep are the sky term, as the sea's: body #385478,
deep #3c597d, `deep_tau_m` 100 m, turbidity 0, bed tint 1. Source: game data times the sea's
fit. The bed is the wet bed (0.8), not the sea's scale behind the surface.

**No inland floor under a ribbon.** The inland floor (`water.inland_floor`, 0.35 of the body
even at the waterline) is for field water, "the share that is neither the ocean's reach nor a
river's" ("Shallow inland water" above). The depth floor already took only that share; the
opacity floor took everything but the ocean, so a ribbon's waterline drew a third body. Under
the teal row this read as a tint. Under a blue body it drew a blue film over a bed 0.1 m down.
Both floors now take the same share (`shore.inland_share`). The numpy reference and the
compiled kernel read the floor from the same plane, so they stay bit-identical. The river keeps
its own depth floor of 0.6 m once 2.5 m in from its bank (section 34).

**Score.** Six river readings, measured by the method of section 31. The three streams are
matched at 0.2 to 1.0 m, the other three at 1.5 to 5 m, as in "The sea by depth band". Each is
scored over three beds (#a68c72, #a4947f, #7d7560) and given the best of them.

| Shot | What, light | Reading |
| --- | --- | --- |
| [3796948748](https://steamcommunity.com/sharedfiles/filedetails/?id=3796948748) | 1.x, forest stream, oblique | mid #628164, lit #6a7b61 |
| [3462159095](https://steamcommunity.com/sharedfiles/filedetails/?id=3462159095) | 1.x, savanna stream, photo mode | #667d4b |
| [wiki Rocky_Desert_Area.png](https://satisfactory.wiki.gg/images/Rocky_Desert_Area.png) | 1.0, desert stream, graded; white-balanced on its sand | #417983 |
| ZywFe6eKNvk frame 663 | U8 editor, desert river mouth | #3f5860 |
| ZywFe6eKNvk frame 1045 | U8 editor, Red Bamboo river | #3e5156 |

The deep pool in 3462159095 reads dark green (#22381e) under the trees it reflects. It is not
scored: no row draws a reflection.

Both rows are scored with this section's floors.

| Row | Misses (ΔE, OKLab x100) | Mean | Max | Shallow | Deep | Hue only (a, b) |
| --- | --- | --- | --- | --- | --- | --- |
| teal (before) | 3.1 2.7 5.9 2.3 6.4 8.7 | 4.84 | 8.7 | 3.9 | 5.8 | 3.03 |
| physics (now) | 4.6 3.7 7.0 3.0 4.1 6.2 | 4.77 | 7.0 | 5.1 | 4.4 | 3.06 |

The physics row is as close as the teal row overall. It is closer on the deep readings and
further off on the green streams. Those read green from above as well as through: grass and
canopy on the banks are part of their light. Over the wet sand target (#a68868) it draws
#77756d at the 0.6 m floor, #677072 at 1 m, #4e6778 at 2 m (hue 235), #43627a at 3 m,
#3d5c7c at 5 m and #3c597d from 8 m. Over pale sand (#d4c2a2) it draws #949f8d, #7e9589 at
1 m and #598183 at 2 m.

**What the readings alone would pick.** With k held at the game's, a deep colour fitted
freely to the six readings lands at slate #3d4e56 (hue 228, mean ΔE 2.87). At the sky term's
lightness, fitted on hue alone, it lands at #415d64 (hue 215, hue-only mean 2.45). No reading
looks down on a river deeper than about 5 m. The readings are oblique, at dusk or from the
editor, and fix the hue at 1.5 to 5 m, which the physics row passes through (hue 213 at 1 m,
242 at 3 m). The sea's blue beyond that comes from the sky term, which was fitted from above
over the sea, lakes, rivers and swamp ("Sea deep and swamp water"), not from a river shot.
Confidence: about even between blue and slate at depth.

**How much turns blue.** Ribbon depths on build 502094 (0.36 M texels): median 0.8 m, 37% deeper
than 1 m, 15% deeper than 2 m, 3% deeper than 4 m. Most rivers read as their bed under a grey
veil. Blue shows in the pools and deep runs, such as the pool at (-1180, -280) (median 3.1 m)
and the desert river at (2080, -1900).

Tests: `tests/mapgen/test_river_row.py` (the row from the install's read, the bed at the
waterline, no floor under a ribbon, monotone channels, the readings by depth band, the other
classes untouched).

### The river's row, teal in the shallows (2026-10-15)

The physics row above drew the wrong thing on the map. Most ribbons are shallow (median
0.8 m), and the narrow ones are shallower still near their banks, where the 0.6 m floor has
not ramped in. At 2.17 times the game's absorption a few decimetres pass most of the bed, so
the red forest's rivers (around (-550, 120)) drew their pale bed under a grey veil: #7f7d73
on the map where the teal row drew #617f76. Deep runs went the other way, to the sea's
blue. A short river between two lakes (around (-950, 1580)) drew mid-blue between two teal
lakes, and the mouth boxes, where the ribbon's share of the lake steps, showed as bands. The
geometry of those boxes did not change; the bands are the gap between the two rows.

**The row now.** The game's absorption keeps its hue (red first, green last) at 5 times, not
the sea's 2.17: k (2.5, 1.198, 1.697), the row's own `absorption_scale`. The body is the
wiki's teal river #4f7d78 again. The deep colour is the readings' hue fit #415d64 (hue 215,
"What the readings alone would pick"), with a 2 m tau. Source: fit to the six readings above
under a constraint: a shallow ribbon over a pale bed reads teal. The readings alone are flat
in the scale from 4 to 8; 5 gives the teal row's chroma at 0.3 m.

| Row | Mean ΔE | Max | Forest bed #95897c at 0.3 m, 0.6 m, 3 m, 30 m | Gap to the lake, 2 to 15 m |
| --- | --- | --- | --- | --- |
| teal (to 2026-10-13) | 4.84 | 8.7 | hue 166, 183, 193; deep #2f5255 | |
| sky term (2026-10-14) | 4.77 | 7.0 | chroma 0.5 (grey), hue 211, 243; deep #3c597d | 5.1 to 7.2 |
| now | 3.79 | 5.9 | hue 173, 189, 206; deep #415d64 | 2.1 to 3.5 |

Deep water (hue 215) is a little bluer than the teal row's #2e5054 and no longer the sea's
blue. The deep colour stays the open question below.

Tests: `tests/mapgen/test_river_row.py` (a shallow narrow river reads teal, deep water slate
teal, the river within ΔE 4 of the lake from 2 m, the readings below 3.85 mean).

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

**The row.** Body and deep colour were both #708973, the colour that draws the pooled target
#718a75 at the Blue Crater's 3.9 m once the sky's 0.02 reflection is added (since 2026-10-14
a body and the lake's deep teal draw it: "Translucent water by depth"). At that depth the bed
adds under 0.2 Delta E, whether it is the bake's WetSand (#816652) or WetSand's calibrated
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

### Translucent water by depth (2026-10-14)

On the west coast at (-1420, 2325) a basin about 58 m under the sea holds a lake of its own,
fed by two falls over its northern lip: 6,869 texels at -75.9 m over a floor at -80 m (depth
p10, p50 and p90 2.6, 4.0 and 5.4 m). It is translucent water, and it drew as one flat
rectangle of #708973, for two reasons:

- **The row.** Body and deep colour were the same, so translucent water drew one colour past
  2 m. Now the body is #8fa685 and the deep colour the lake's teal #375a58 at the lake's
  `deep_tau_m` 6 m. The body is solved, as the old row was, to draw the pooled target #718a75
  over WetSand at the Blue Crater's 3.9 m (Delta E under 0.6), so the Blue Crater keeps its
  colour. Through the row: #889b81 to 1 m, #7f967d at 2 m, #718a75 at 3.9 m, #647e6d at 6 m,
  #4b6961 at 12 m and the teal from about 30 m. The game's own map draws the basin pale over
  its shallow western end and darker where it is deep, and other lakes reach the same teal.
- **The crossing.** The basin lies inside the ocean's 48 m reach, where the waterline is the
  drawn surface crossing a level (`crossing_level`, "The swamp's own level" below). Water
  outside the sea's band took the sea's -17.0 m, so the basin's water was measured from the
  sea: 62 m deep, the deep colour everywhere within 48 m of the coast and the true 4 m only
  past it, along a straight-edged line across the lake. Water whose own level lies under the
  band now crosses at its own level, as water within the band does; water above the band
  still takes the sea's. On the field's own planes the basin is the only such water in the
  reach (6,964 texels, the rest a few texels at its falls).

Drawn unlit over each window's baked bed, the median translucent texel by depth. Before, every
one read #708973 to #718a75 past 2 m (the basin #708973 at every depth); after:

| Lake | 1-2 m | 3-4 m | 5-6 m | 8-12 m | 12-20 m |
| --- | --- | --- | --- | --- | --- |
| West-coast basin (-1420, 2325) | #80967c | #738c76 | #698270 | #577366 | |
| Western Dune Forest (-772, 1631) | #82977d | #738c76 | #66806e | #4f6c62 | #4a6860 |
| Savanna (533, 968) | #83987d | #728b75 | #67806f | #577266 | |
| Blue Crater (1558, 2110) | #82987e | #708974 | #698370 | | |

The basin's rock walls and its northern lip, which the sea's crossing had drawn as water down
to -17.0 m, are drawn as ground again.

Tests: `tests/mapgen/test_translucent_row.py` (the Blue Crater's target, darker and more teal
with depth, the lake's deep colour, only the swamp one colour at every depth) and
`tests/mapgen/test_painted.py` (a basin below the sea is as deep as its own water).

### Dry hollows under the sea (2026-10-14)

Within the ocean's 48 m reach the crossing rule draws every pixel whose ground lies under
-17.0 m as sea, whatever the water plane says. That is meant for the coast, where the plane's
3.66 m artwork blocks leave dry texels under the sea beside the water. It also drowned the
hollows the sea does not fill: the sunken ground around the west-coast basin, behind its rock
walls and past the straight edge of the ocean's box near (-1643, 2436), and the like at
(-1674, 3170), (-1620, 3474), (2934, 1141) and on the north edge near (1466, -3273). The game's
own map draws them black or as rock, not water. 80,170 dry texels more than 5 m under the sea
were drawn as sea, 25,045 of them in one patch at (-1643, 2436).

**The rule (replaced, see "Open sea is not a hollow" below).** `dry_hollows`
(`palette/water/shore.py`) took out of the reach each part of the dry ground under the sea
that is more than 4 m (`HOLLOW_CLEAR_M`, since removed) from any water and
sinks more than 5 m (`HOLLOW_DEPTH_M`) under the sea somewhere in it. Such a part keeps
recipe 5's rule, which draws dry texels as ground. Water is never taken out: the basin's lake
and the ocean's own patches standing over the hollow keep the crossing. A beach's dry strip
lies within 4 m of the water, and a shallow dip inland never reaches 5 m down, so both stay.
The hollows join through the dry ground only, not through the sea, because the hollow at
(-1643, 2436) touches the open sea under the -17 m contour along its box's edge.

**Moved.** On the field, 66,163 texels in 85 hollows leave the reach (11,879,716 to
11,813,553); none is wet and none lacks a height. By depth under the sea: 1,918 to 1 m, 2,151
from 1 to 2 m, 6,007 from 2 to 5 m, 13,192 from 5 to 10 m, 23,423 from 10 to 50 m and 19,472
deeper. Of the 80,170 dry texels more than 5 m down, 55,799 are ground now; 24,371 are still
drawn as sea, all within 4 m of water: strips beside a box's edge, between the ocean's patches
over the hollow, and around the arch's footprint near (-2805, 602). Drawn on windows of the
painted sheet, the rock walls and islands that the sea had covered stand out as the game's map
draws them. Two coast windows, (-2560, 760) and (-100, -2020), 240 m across, do not change by
a byte; a 400 m window at (-2202, 243) changes 991 pixels, all in one pocket at (-2134, 232),
and a 300 m window over the arch 195.

Test: `tests/mapgen/test_painted.py` (a hollow behind a wall leaves the reach; its lake, the
sea, a beach's strip and a shallow dip keep it).

**Open sea is not a hollow (2026-10-15).** The rule above drew open sea as land. Past the
straight edge at x -1740 off the south-west coast the landscape ends: the floor is cliff and
fill texels 25 to 145 m down, then no data, and the ocean's raster covers only patches of it.
Nothing above the sea stands between that floor and the ocean, so the sea fills it, and the
game's sea surface lies over it; render v12 drew its rocks as dry land, with a straight edge
and a brown stripe of cliff along the box. The same held for every large "hollow" above: of
the 66,163 texels, 65,120 join measured ocean through ground under the sea.

Now a hollow is a part of the dry ground under the sea (8-connected) that sinks 5 m under it
somewhere and touches no measured ocean texel, so ground above the sea walls it off. The 4 m
clearance is gone: the coast's dry strips touch the ocean and stay in the reach by that
alone. Water at the ocean's level standing over a walled pit still makes it sea; a lake at
its own level does not. On the field 1,286 texels in 31 hollows leave the reach (11,878,430
stay), the largest 792 at (1476, -3112) and 186 at (-1629, 3063); the window x -1841..-1606,
y 2344..2578 has exactly the reach it had before this rule.

Test: `tests/mapgen/test_painted.py` (open sea past the box's edge keeps the reach; walled
off and without the ocean's patch, the same drop is a hollow).

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

**Past the swamp (2026-10-14).** Over data too the boxes claimed sea-level water between the
abyss rocks that the open sea's opening does not reach, so the near-black swamp drew over the
sea there in No Man's Land, as at (3031, 1140). A swamp box now claims no water within the
ocean's band outside the areas the biome table calls swamp (`BIOME_CLASS`); that water is
ocean. On build 502094 28,547 texels go back to the ocean, and the blend shrinks from 84,204
to 64,434 texels. Inside `Area_Swamp` nothing changes.

**The swamp's own level (2026-10-14).** The ocean's crossing rule drew every waterline in its
48 m reach at -17.0 m, and the swamp's water counts as ocean there, being within the 0.5 m band.
But the swamp stands at -16.7 m (and -16.8 m in places), so its islands were drawn out to the
-17.0 m contour, wider than the water the field measures, and where flat ground sat at exactly
-17.0 m the waterline and its foam lay across it in blocks, the field's 1 m texels: grey
squares on the island at (2503, 791). `crossing_level` (`palette/water/shore.py`) now gives
each pixel its own water's level where that lies within the band, and -17.0 m elsewhere; dry
ground far from water samples no level and keeps the ocean's. The open sea reads exactly
-17.0 m (7.98 M measured texels), so its coast does not move; 0.96 M measured texels lie in
the band off it, nearly all swamp. The swamp's islands shrink to the field's wet outline,
which is what the game's own map draws there. It shares the water-edge batch's version bump,
as no map was rendered between them.

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

### Water edges (2026-10-14)

The wet plane is the artwork's mask, in its 3.66 m blocks, and field water was drawn only
inside it: the depth feather cut it where the ground rises above the level, but where the
mask stopped first, short of the shore or in open water, the water ended on the mask's
stairs, as across the canyon lake at (1800, -1772) and along the bank at (-1140, 1630).

`surface.water_edge` builds, once a pass on the 1 m grid, the edge every style draws the
field's water to (`WaterPlanes.edge`):

- **Reach.** Dry texels within 3 m (`WATER_REACH_M`) of measured water take the highest level
  beside them and count as measured, so the depth feather cuts the water where its surface
  meets the drawn ground, at full resolution. Not under a river's ribbon, which draws its own
  water there.
- **Outline.** The mask is smoothed by a Gaussian of 1.5 m (`WATER_SOFT_SIGMA_M`): past it the
  water fades out to where the smoothed mask reads 0.04 (`WATER_SOFT_LOW`), 2.6 m past a
  straight edge, and inside a body the cover follows the smoothed outline, so a block's
  corner is rounded. A wet texel with no texel within 2 m reading 0.9 (`WATER_THICK`) is a
  thin channel and keeps its full cover.

The level, the class and the meshes' seabed rule still read the mask as it was. The edge
costs about 3 s a pass and about 1 GB at its peak.

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

- A dry hollow is judged on the heightfield alone: a wall above the sea counts only where the
  field's ground or cliff texels stand above -17.0 m, and a gap one texel wide under the sea
  joins a pit to the ocean. Whether the 31 walled hollows left ("Dry hollows under the sea")
  hold water in game is unchecked.
- The lake, the sea's deep colour and the swamp have been checked against screenshots from
  above; no in-game top-down check over a known depth has been made. A look from about 50 m
  over the open sea at (-3000, 645) and over the swamp at (2343, 291) would settle the two
  rows above. The sulfur pond, hot spring and cave optics come from material parameters,
  not from pictures.
- The sky fit also gives a row for the blue lake. It is not applied.
- The river's deep colour is the readings' hue fit #415d64. No river reading looks down on
  water deeper than about 5 m ("The river's physics row"). A daylight look straight down on
  the pool at (-1180, -280) would settle it.
- The material's absorption is read as per metre from two parameter names, and `Ocean SBW`
  as the scale behind the surface from its name; the shader graph that uses them was not
  decoded. The cool tint on the sea's bed is a fit, and the material's caustics, which may be
  what it stands for, are not drawn ("What is behind the sea's surface").
- The sea's edge over pale sand draws a light grey-cyan fringe, 3 L above the dry sand beside
  it, where the one 1.0 shot with a beach in it reads the edge water at the dry sand's
  lightness. A daylight shot looking straight down at a sandy beach's waterline, and one over
  a crater-grass patch in a lagoon, would settle the edge and the patches' darkness.
- Translucent water was measured at the Blue Crater's 4 m only. How it darkens past that is
  the lake's deep colour and tau, not a reading; the deeper translucent lakes (the Western
  Dune Forest one at (-772, 1631) runs to 22 m) are unchecked against pictures. The two actors
  that set their own colours draw with the class row (see "Translucent water").
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
- Where the mask stops in open water more than 3 m from any shore, the water fades out
  along the smoothed outline instead of a straight line, still where the mask put it.
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
- **Jagged joints** (2026-10-14). On a sloped bend the nearest centreline piece flips between
  two sections texel by texel, a checkerboard of 0.5 to 2 m steps the joints' feather does not
  take, since the level runs on flat on neither side. The ribbon drew nothing beside each step,
  and the field's water kept at the lip filled the holes in blocks: the pixel-block squares at
  the junction at (-1585, -480). Within 2 texels of such a step (`RIVER_JAG_TEXELS`), and
  clear of a fall, the drawn plane is smoothed by a Gaussian of 1.5 m (`RIVER_JAG_SIGMA_M`):
  49,417 texels on build 502094.
- **Where a river meets other water, the higher surface shows**, handed over along a ramp
  (2026-10-07): the river's share of the water runs from none where its plane stands 0.55 m
  under the other surface to all of it 0.45 m over (`RIVER_HANDOVER_M`, 1 m, centred on the
  5 cm tie). A plane meandering a few decimetres about a lake's level, as at (-1115, 1630),
  drew panels of river and lake colour wherever it crossed the tie, and a mouth a line; both
  now change tone along the ramp. Past the other water's last wet texel, away from the sea,
  the blur of its edge is no water to give way to (section 38, "Boxes over lower water").
  The river covers what the other water leaves of a pixel (2026-10-14): at the edge of a
  patch of field water in a river, the two used to cover a pixel's share each, and the ground
  showed between them as an outline round every patch, as at (-1585, -480) and (1352,
  -1735).
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
are read only after it, to hold up the water a fall pours from ("Held at a lip").

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

### Held at a lip (2026-10-14)

At (2040, -1880) the arch lake's east arm drew as a flat sand box with straight sides, the
"waterfall box" of the sweep. No foam is drawn there; the box was the lake itself.

- **The cause.** The arm's `FGWaterVolume`, at 228.7 m, runs on 3 m past the cliff its fall
  pours over, and the artwork leaves the arm's shallows round the island dry, at 224 to
  225 m. Its shoreline therefore stands below the level all round: the cliff top, the dry
  shallows, and the 18 m strip under the arch, which is dry too until the holes are filled.
  The membrane spanned that shoreline and laid the arm on its own bed, 0 to 1 m deep, which
  draws as sand. The strip under the arch then took the arm's sunk surface (two bodies over
  one gap, the lower one), a 6 m step along its west edge against the lake at 228.7 m.
- **The rule.** `palette/water/lips.py`, between the re-levelling and the holes. A fall's lip
  (section 35's record) is the water's surface where it pours, so water the re-levelling
  lowered below a lip is raised back to it: the connected part of lowered water found under
  the fall's top modules (10 to 100% of their run upstream of the lip, across 80% of its
  width), on the lip's upstream side. Never above the box top, never the ocean's falls.
  Past the lip the membrane runs on down the cliff.
- **What it moves.** On build 502094, in a render, 12 lips hold water and 3,391 texels rise:
  the arm, 2,581 texels at the lip (2075, -1855), now at 226.5 m and 1.5 to 4.5 m deep; the
  lake below the fall at (-1340, -306), 427; the canyon pool at (2114, -1914), 198; nine
  others under 50. The strip under the arch now takes 226.5 m.

The lip is placed out over the void, here 12 m east of the cliff top, so the water feeding
it is looked for along the top modules' run, not just behind the lip.

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
- The fall at (2075, -1855) still draws no foam: section 35 reads the field's box levels,
  and its lip stands 2.2 m under the arm's box. Reading the drawn water instead would draw
  38 more falls, most at real steps in rivers, but in the canyon basin near (2131, -1828)
  the wide falls' pools draw as straight-sided bars over the lower lake.
- A dry patch of about 10 by 20 m in the arm's south corner near (2040, -1830) stays
  sand: it is within 12 m of the cliff, where the holes rule reads a spill.
- This section leaves level-only water alone. At the ocean's level it draws on section 26's
  open-sea bed; away from it, it keeps the deep tint.

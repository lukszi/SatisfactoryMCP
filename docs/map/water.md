# Water on the renders: classes, rivers, falls and perched water

Sections 33, 34, 35 and 38 of the [design spec](../../DESIGN.md): how the renders class, level and draw
water. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

## 33. Water by class in the game-painted style (2026-10-05)

Recipe 6 drew every water texel with one set of Beer-Lambert optics, the ones calibrated on
the Spire Coast sea. Inland that read wrong: rivers came out sea-blue, the swamp a clear blue
sheet, and sulfur ponds like the sea. The game-painted style now gives each water body a
class and each class its own optics. The ocean kept its calibrated values; only its deep
colour has moved since (see "Sea deep and swamp water" below).

### The class plane

`python -m mapgen paint` also writes `water_bodies.json` into the paint store: every water
actor with a box (class, world box, the materials its export subtree assigns) and every
root `StaticMeshComponent` whose mesh is under `/HotSpring/`. On build 502094 that is 839
actors and 101 terraces, and it doubles the paint run to about 50 s because the water boxes
read mesh bounds. `PAINT_GENERATOR_VERSION` is 3 (with sections 30 to 32 and 36).

`gamedata/water/bodies.py` `classify` turns that into a uint8 plane on the 1 m grid, once per
render (about 5 s):

1. Each actor's class comes from its first known material (`MATERIAL_CLASS`):
   `MI_SLW_River_*` river, `MM_Lake_01` and `MI_Lake_Turquoise_01` lake (see "Lake colours"
   below), `MI_Lake_Blue_01` blue lake, `MI_WaterSwamp_Muddy` swamp, `MI_Lake_Caves_01` cave,
   `SulfurPond_Inst` sulfur, `MM_OceanMaster` ocean. 527 actors assign none. Of those, an
   actor whose class is in `ACTOR_CLASS` takes that: the 26 `BP_TranslucentWater_C` are
   translucent (see "Translucent water" below). The other 501 (the `FGWaterVolume` brushes,
   the falls, the lake and ocean spline tools) have no class of their own.
2. A lake box at most 150 m on a side with a hot-spring terrace inside it (within 1 m of its z
   range) is a hot spring.
3. Boxes paint wet texels whose level lies within 1 m of the box's z range, largest box
   first, so a pond inside a big box keeps its own class. No box but the ocean's claims the
   open sea: water within 1 m of the ocean level that the map's edge reaches through
   channels at least 96 m wide (a 48 m opening on a 4 m grid, kept where it comes within
   three radii of the edge). The swamp's `MI_WaterSwamp_Muddy` boxes are 230 m squares at
   the sea's level and reach past its coast, to x 3250 against the swamp area's 3040; they
   painted the open sea mauve in straight steps. A lagoon behind a narrower mouth keeps its
   box. A box whose top lies under the sea's level claims no water at the sea's level (see
   "Lake boxes under the sea" below).
4. Unclaimed wet texels within 1 m of the ocean level are ocean. That includes the level-only
   water around the frame at about -16.3 m.
5. River boxes are settled body by body (`_settle_rivers`). See "River boxes" below.
6. The rest of an inland body (8-connected) takes the body's majority class when that class
   covers at least a quarter of it.
7. What is left is swamp in `Area_Swamp` and lake everywhere else.
8. Where swamp meets the ocean inside one body, the plane blends the two
   (`feather_mouths`; see "Swamp mouths" below).

Build 502094: ocean 16.23 M texels, lake 1.20 M, swamp 0.35 M, river 0.38 M, turquoise 53 k,
hot spring 15 k, sulfur 9.7 k, cave 5.3 k, blue lake 5.2 k; 312 bodies claimed by
material, 0.28 M texels by the biome fallback. Translucent water has since taken 288 k
texels from the lake (see "Translucent water" below). Turquoise has since merged into lake; about 2 k
of its texels went to hot spring instead (see "Lake colours" below). The open sea is 14.92 M texels; it took
264,581 texels from the swamp and 16 from a river, and nothing from any other class.
Seeding it from the texels with no ground as well would add 2 k, so the edge alone does.

The renderer samples the plane bilinearly with the dry taps dropped
(`terrain/sample.py` `ClassMix`), so a shore pixel takes its water's class rather than half
of nothing. A pixel whose wet taps agree takes its class's row exactly. A band holding only
ocean and dry texels takes the recipe 6 path unchanged, and so does a render from a paint store
without `water_bodies.json`; the sidecar's `paint.water_classes` says which happened.

### River boxes (2026-10-06)

A `BP_River_PROT_C` box is one AABB around a whole river (section 34). Its rectangle reaches
into the lakes and the sea the river feeds, at their level. Painted like any other box, it
left a straight-edged block of river colour in them, in the game-painted style only: at
(-370, -868), (-1033, -366) and (12, -70) among others. Since recipe 7 the ribbons draw the
river row wherever they speak, so the rectangle added nothing but the seam.

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

Measured on build 502094's reconciled water, on nine 2.5 km tiles with 400 m margins (close
to a render's counts, not equal to them):

| | Before | After |
|---|---|---|
| River texels | 0.277 M | 0.167 M |
| Straight river edges, runs of 8 m or more (texels) | 3,024 | 681 |
| The same inside one body | 2,544 | 39 |

The river's texels went to lake (64 k), ocean (40 k), swamp and turquoise (2.8 k each) and
hot spring (0.8 k); 0.7 k of lake turned river. The 39 are two lake boxes smaller than the
river box, which keep their class. The rest lie on level steps between two bodies. On the
field's own planes (`--kernel-only`) the river keeps 0.31 M of 0.38 M texels. On a 3.3 km
tile the classification takes 0.53 s against 0.29 s, at the same peak memory. The other
styles draw no class plane and are unchanged; style `satellite-painted` is version 9.

### Lake boxes under the sea (2026-10-06)

The field's level at a texel is the highest box top over it (section 19), stored in
decimetres. Four lake boxes stand under the sea's level. Two of them reach over water whose
level is the sea's: the Rocky Desert box at -17.046 m, 5 cm under the sea's -16.994 m
(x -1,100 to -870, y -1,475 to -1,116), and a box at -17.7 m (x -1,958 to -1,678, y -558 to
-329). The sea's surface stands over both, so the field holds the sea's level there; under the
-17.7 m box, water at the box's own surface would read -17.7. A channel narrower than the open
sea's 96 m is not open sea, so step 3 let the lake boxes claim it. In the Rocky Desert it drew a
dark green rectangle in the sea, with straight edges at x -870 and y -1,475.

A box whose top lies under the ocean level (`OCEAN_LEVEL_M`) now claims no texel whose level
is within half a decimetre of it (`SEA_ROUNDING_M`, the field's rounding): that water is the
sea's. A box under the sea whose water reads its own level keeps it, such as the pond at
-17.55 m near (3,825, -2,604). Boxes at or above the sea's level, the swamp's at -16.666 m
among them, are unchanged.

On the field's own planes this turns 46,704 lake texels to ocean, exactly the two boxes'
sea-level texels, and changes nothing else. Straight class edges inside one body (runs of 8 m
or more) go from 1,310 to 1,042 map-wide, and from 199 to 0 in the Rocky Desert window. In a
render of that window the step across the box's old edge goes from 0.062 to 0.010 OKLab.
Style `satellite-painted` is version 10.

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
| ocean (the `water` block) | #577f7e | #3c597d | 4.08, 3.53, 3.53 | 0 | fit: body and k on Spire Coast screenshots (section 27). Deep: screenshot, the 1.0 open sea from above (see "Sea deep and swamp water") |
| river | #4f7d78 | #2e5054 | 2.4, 1.6, 1.6 | 0 | screenshot: hue from the wiki's Rocky Desert river; clear, so the bed shows |
| lake | #56745b | #375a58 | 3.0, 2.2, 2.6 | 0.1 | screenshot: body jade near the shore; deep teal from 1.0 top-down screenshots (see "Lake colours") |
| lake_blue | #4a8494 | #22485a | 3.4, 1.0, 0.75 | 0 | game data: `MI_Lake_Blue_01` absorption (0.52, 0.15, 0.11) |
| swamp | #302627 | #302627 | 4.0, 4.5, 5.0 | 0.45 | screenshot: opaque near-black from above in 1.0 (see "Sea deep and swamp water") |
| cave | #2f4a47 | #1c2e2d | 3.0, 2.5, 2.5 | 0 | none: dark |
| sulfur | #67a395 | #3f8a7e | 3.0, 2.0, 2.0 | 0.3 | game data: `SulfurPond_Inst`, deep (0.17, 1, 0.89) cyan, shallow (1, 0.26, 0) orange as the bed tint |
| hot_spring | #68a098 | #46827d | 2.5, 1.6, 1.5 | 0.2 | none: milky turquoise |
| translucent | #708973 | #708973 | 1.4, 1.4, 1.4 | 0 | screenshot: the Blue Crater from above in 1.0 and 1.1, pooled #718a75. k: game data, the translucent material's opacity ramp (see "Translucent water") |

Targets follow the Spire Coast calibration (section 27): the reference colour times 0.85
linear for map exposure, OKLab L times 0.95 and chroma times 0.9. Against the references at
assumed depths, Delta E (OKLab x100): the old mauve swamp row 3.7 at 1 m and 5.2 at 3 m, lake
1.9 at 2 m and 4.5 at 4 m (with the old jade deep colour). Hue alone (the a, b distance) was
under 1 for both.
The river references are a dusk shot at a grazing angle and a stream over white sand. They fix
only the hue (a, b distance 1 to 3), not the lightness.

### Lake colours (2026-10-06)

The lake row was calibrated jade throughout, on the wiki's Lake Forest shot. That jade was the
Lake Forest fog: the `Atmosphere_LakeForest` volume over the 131 m lakes has a noon
inscattering of #4a7354, Delta E 1.9 from the lake body #56745b, and starts at 0 m. The game's
lake material (`MM_Lake_01`, single-layer water) absorbs red most and green least and scatters
nothing, so it has no body colour of its own: shallow water reads green over its bed, and deep
water reads dark, lit mostly by the sky's reflection. In 1.0 screenshots from above, deep lake
water reads teal at hue 185 to 199 (the biggest Crater Lakes crater near top-down: #4b6c67),
and near-shore water green at hue 146 to 165.

The body stays the jade, which is right near the shore. The deep colour moves from #2c4e3c
(hue 160) to the teal #375a58 (hue 191); absorption, `deep_tau_m` (6 m) and turbidity are
unchanged. Drawn through the painted layer's water (tone and exposure included) over each
window's baked bed, at its depth on the field's own planes, the median Rocky Desert lake texel
reads:

| Depth | Before | After |
|---|---|---|
| 0-1 m | #5f7661 (h147) | #607763 (h149) |
| 1-2 m | #53715c (h154) | #547260 (h158) |
| 2-4 m | #4b6a55 (h155) | #4e6d5e (h163) |
| 4-8 m | #405f4c (h157) | #45655b (h173) |
| 8-15 m | #3b5a47 (h157) | #41625a (h178) |
| 15 m and deeper | #2e4f3d (h159) | #385b58 (h189) |

The Lake Forest, Crater Lakes and mesa windows agree within 6 degrees of hue at each depth,
but for the mesa's shallows over red sand (hue 140 under 1 m). A shallow lake changes little;
a deep one turns from dark green to teal.

The 14 `MI_Lake_Turquoise_01` lakes on the Desert Spires mesas are lakes too. The instance's
cooked shader maps are hash-identical to `MM_Lake_01`'s, its deep colour, tint and depth
switch are not parameters of the master, and no actor sets anything the shader reads: in game
they render exactly as `MM_Lake_01`. 1.0 screenshots of the big mesa lake at (1950, -1890)
read green shallows (#526e46) and dark deep water, Delta E 3.2 from the lake body. The
turquoise class drew it cyan (#44827c at 1 to 2 m); it now draws as a lake (#54725f at 1 to
2 m, #47675c at 4 to 8 m). The turquoise class is gone.

As lakes, these boxes also fall under step 2's hot-spring rule. The 230.3 m lake by the crash
site at (1908, -2368) is a 114 m box holding three hot-spring terraces near (1918, -2428), so
it draws as a hot spring (#5d968f at 2 to 4 m; 2.1 k texels on the field's own planes).

### Sea deep and swamp water (2026-10-06)

The open sea's deep colour and the swamp's water now come from 1.0 screenshots seen from
above. Style `satellite-painted` is version 16.

**One sky term.** Seen from above, deep water in the game is lit mostly by the sky it
reflects: its materials absorb and scatter little. One sky term for every class, (0.026,
0.047, 0.085) of the sun's light, put through the camera model with each material's own
absorption over its bed, fits the 1.0 screenshots of the sea, lakes, rivers and swamp to a
median Delta E of 5.2, most of it from depths that had to be guessed. Drawn alone it is
#3c597d. A test that needs no depth, the distance to the nearest point of each class's
colour-against-depth curve, gives the sea 2.8 against 3.9 for the old row. A reflection per
material, by its Fresnel term, fits worse (13.2). The palette's own sky term, 0.02 of
#96bee6, is about a fifteenth of the fitted one. Source: fit.

**The sea.** Only the deep colour moves, from #354e68 to #3c597d; the body, the absorption and
`deep_tau_m` stay. The full row the fit gives (k 1.69, 0.50, 0.36 per metre, 2.17 times the
game's absorption, with body and deep both #3c597d) would also draw the 1.0 lagoons blue, at
hue 228, where the screenshots read teal, so it is not taken. Source: screenshot, measured by
the method of section 31:

| Shot | What, light | Reading |
| --- | --- | --- |
| [3557686632](https://steamcommunity.com/sharedfiles/filedetails/?id=3557686632) | 1.0, open sea from a high rock, clear, about 50 degrees down | deep #2b557c (h 248) |
| [3735753140](https://steamcommunity.com/sharedfiles/filedetails/?id=3735753140) | 1.1, island fuel plant, clear, about 45 degrees | deep #385a85 (h 254), lagoon #3b6887 |
| [3755331272](https://steamcommunity.com/sharedfiles/filedetails/?id=3755331272) | 1.2.3, sand-bar islands, clear | channel #4b6f88, far deep #4a5d7e |
| [3552323039](https://steamcommunity.com/sharedfiles/filedetails/?id=3552323039) | lagoon over sand and coral, high angle, clear | bed showing #6ca0a7, open #3d6f7a |
| [3771500868](https://steamcommunity.com/sharedfiles/filedetails/?id=3771500868) | Spire Coast lagoon, high oblique, clear | #78969e (h 216) |

**The swamp.** `MI_WaterSwamp_Muddy` absorbs about 20 times what the sea does, so swamp water
is opaque within 0.1 m and near-black from above. The opaque target goes from #7e7372 to
#302627. Source: screenshot. A 1.0 video from a tower, looking down
([UD_1Rz7xryg](https://www.youtube.com/watch?v=UD_1Rz7xryg&t=170)), reads #332d31, and
cleared pools in 1.2 ([3743733077](https://steamcommunity.com/sharedfiles/filedetails/?id=3743733077))
read #2c1f1d; the two pool to #302627.

The old mauve was the swamp's fog. `Atmosphere_Swamp` inscatters #9977c1 (hue 304) at noon,
at a density of 0.1 against the global 0.02 (source: game data). The wiki and Early Access
shots the old target came from (#9f8a88, #857475, #7b7472) are lifted and tinted by it.
Further off in the same video, through more of the fog, the water reads #4f494e. The fogged
#4d4a4e is the fallback if the near-black ever reads as a hole.

The swamp class row takes the same colour as its body and its deep colour, so swamp-class
water outside `Area_Swamp`'s blur, which section 31's opaque colour does not reach, draws
near-black too: #322a2d, where the sky's 0.02 reflection lifts it a little. A body of
#302627 less that reflection (#2a180e) would land on the target exactly, but over so dark a
body a crown sunk 0.3 m in the swamp showed more than in the sea, which murky water must not
do (`tests/mapgen/test_crowns_over_water.py`). Absorption, `deep_tau_m`, turbidity and the rule
for which water takes the opaque colour (section 37, "Swamp water") are unchanged.

**Measured.** Drawn by the painted layer's own water code (`band_water`, `class_optics`,
`underwater`, the tone, then `with_void`) at 1 m on the field's own planes, with the open sea
laid by `open_sea` from the artwork and the class plane from `classify`. The bed is the bake,
unlit and uncalibrated, so only the absolute colour of the shallows is approximate. Medians
of fully wet pixels clear of the void, display sRGB, on the ocean and west coast window at
(-2515, 645):

| Depth, as the optics read it | Before | After | Delta E |
| --- | --- | --- | --- |
| 0.5 to 1 m | #5b7f80 | #5c7f81 | 0.2 |
| 1 to 2 m | #577d80 | #577e82 | 0.4 |
| 2 to 4 m | #54797d | #547b81 | 0.7 |
| 4 to 8 m | #4f7279 | #507580 | 1.2 |
| 8 to 15 m | #476774 | #4a6c7f | 2.1 |
| 15 to 30 m | #3c586c | #41607e | 3.5 |
| 40 m, the open sea (the optics read no deeper) | #375169 | #3d5b7d | 4.2 |

The two-colour ocean at (3326, -216), the sea off the swamp at (2554, 608) and the Spire
lagoon at (16, -2137) agree within two levels per channel. Under 1 m no channel moves by
more than one level, and a lagoon 1 to 2 m deep moves by 0.4. Where the open sea fades into
the void, the colour still runs evenly to the page's navy: the median of each tenth of the
void's cover goes from #3f5c7d at the sea's edge to #233340 (before, #3b566a to #23323f).
The step between
neighbouring pixels in the fade is unchanged in the two-colour window: median 0.32, p99 25.8
against 25.5, the p99 from the lit rim and the land.

Swamp water in the swamp at (2343, 291) and at (2554, 608):

| Depth | Before | After |
| --- | --- | --- |
| 0.1 to 0.3 m | #7d706e | #3e3434 |
| 0.3 to 1 m | #7e7271 | #32292a |
| 1 to 2 m | #7e7372 | #302627 |

Against the dark colours of the map (Delta E, OKLab x100), the new swamp water (L 0.28, hue
11) is 16.8 from a pit's black (L 0.11) and 13.7 from its grey lit edge, and 20 from the open
sea at 40 m (L 0.46). It is 6.2 from the page's navy past the world's edge, and 4.1 from the
void where the sea fades into it (#1c2b37): the same lightness, the opposite hue. It meets
only the sea and the land: in these windows the only no-data beside swamp water lies under
rock, near (3000, 1150). The fogged #4d4a4e would sit 0.8 from a pit's lit edge and 2.5
from the void's lit edge.

### Translucent water (2026-10-06)

The Blue Crater's lake is not `MI_Lake_Blue_01`. It is a `BP_TranslucentWater_C`, which
assigns no material of its own: the blueprint draws `TranslucentWater_Inst`. The map classed
all 26 of these actors by the biome fallback, as lakes, and drew the Blue Crater #4b6a5d at
its depth. They now have a class of their own, `translucent`, by actor class (step 1). Style
`satellite-painted` is version 17, with "Swamp mouths" below.

On the field's own planes 14 of the 26 hold water, 288,398 texels, all of which the biome
fallback had drawn as lake (it fills 1,234 texels now, against 289,632). The Blue Crater is
110,272 of them, over a flat floor: depth p10, p50 and p90 2.1, 3.9 and 4.0 m. The others
are a 454 m lake in the Savanna at (533, 968), median depth 5.8 m; two lakes in the Western
Dune Forest at (-1271, 1705) and (-772, 1631), 4.0 and 12.3 m; a shallow lake in the
Southern Forest at (481, 2150); and smaller lakes and ponds in the Southern Forest, Titan
Forest, Red Jungle, Grass Fields, Western Dune Forest and No Man's Land.

**The row.** Body and deep colour are both #708973, the colour that draws the pooled target
#718a75 at the Blue Crater's 3.9 m once the sky's 0.02 reflection is added. At that depth the
bed adds under 0.2 Delta E, whether it is the bake's WetSand (#816652) or WetSand's calibrated
target (#b1a09e). Source: screenshot, measured by the method of section 31:

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

**Measured.** Drawn as in "Sea deep and swamp water" (the painted layer's water code at 1 m on
the field's own planes, over the bake), the median Blue Crater texel by depth:

| Depth | Before (lake) | After |
| --- | --- | --- |
| 0 to 0.5 m | #6d7763 | #848571 |
| 0.5 to 1 m | #5d7561 | #7c8874 |
| 1 to 2 m | #53725f | #758a76 |
| 2 to 3 m | #4f6e5e | #728a76 |
| 3 to 3.7 m | #4c6b5d | #718a76 |
| 3.7 to 4.1 m (38,874 of 53,687 texels drawn) | #4b6a5d | #718a76, Delta E 0.1 from the target |

A render of the window had read #486652. The other translucent water draws the same past 2 m,
#718a76 to #708973 at every depth: the Savanna lake at 4 to 8 m goes from #46665c to #718a75,
the deep Western Dune Forest lake at 8 to 15 m from #3d5e59 to #708974.

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
met the ocean, the class rows and the swamp's share of section 31's opaque colour both
switched within one texel. The meeting lines lie on the open sea's 48 m arcs (step 3) and on
the swamp boxes' straight edges. The step across them was Delta E 5.8 with the old mauve swamp
and 26.7 with the near-black one.

`feather_mouths` now blends the two. It finds the texels where swamp and ocean are
8-neighbours at levels within 0.5 m (`BODY_STEP_M`). Within 30 m of that line
(`MOUTH_FEATHER_M`) each swamp and ocean texel of the same body takes a swamp share: a
smoothstep from 1 to 0, a half at the line. The distance is counted in steps through water of
the texel's own class, the 8- and 4-neighbourhood in turn, so the blend never reaches water
behind a spit; the octagon it draws reaches up to 33 m. Water at another level, such as a pool
above a fall, is another body and keeps its edge.

Plane values from `MOUTH_BLEND` (10) on are the blend, in 64 steps (`MOUTH_STEPS`).
`class_shares` gives each plane value its share of each class. `water_table` mixes the swamp
and ocean rows by that share, and the swamp's opaque share is the same share, so the class
optics and the opaque colour fade together. The sidecar's `mouth_blend_texels` counts the blend.

**Measured** on the field's own planes and drawn as above. 78,231 texels blend, all on the
swamp's sea coast, in 0.5 s on the full grid. The step between neighbouring texels across the
line, counting only neighbours whose depths agree within 0.25 m:

| Where | Before, max / p99 | After, max / p99 |
| --- | --- | --- |
| (2931, 964), 1 m texels, within 2 m of the line | 28.3 / 27.0 | 3.3 / 1.8 |
| (2931, 316), the same | 27.9 / 27.3 | 4.1 / 1.8 |
| (2931, 964), 0.25 m pixels, within 2 m of the line | 8.0 / 7.4 | 1.0 / 0.4 |
| (2931, 316), the same | 7.9 / 7.5 | 1.1 / 0.4 |
| (2931, 964), 1 m texels, within 40 m of the line | 28.3 / 17.8 | 7.5 / 2.3 |
| (2931, 316), the same | 27.9 / 11.3 | 7.3 / 2.3 |

The largest steps left are the water's own: a texel 30.6 m deep beside one 1.1 m deep, and the
swamp's waterline. No texel farther than 33 m from a line changed class, and no drawn colour
farther than 60 m from one changed at all, at both mouths and over the whole coast at
(2554, 608). The swamp window at (2343, 291) holds no meeting line and is unchanged.

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
- The blend joins only swamp and ocean. Any other two classes meeting inside one body still
  change within a texel.
- A box less than half a decimetre under the sea cannot be told from it by level. Two ponds
  inside the Rocky Desert's -17.046 m box, near (-989, -1,434) and (-978, -1,262), and two
  puddles beside them (6,993 texels in all) read the sea's level and draw as sea; the sea's
  box stands over them too.
- The hot-spring rule finds terraces in lake boxes near the sulfur ponds, in the Red Bamboo
  terraces and in the mesa lake by the crash site at (1908, -2368). Whether those pools are
  milky in game is unchecked.
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
  fall's lip spreads its plane out over the basin 50 m below, and hid the river in it.
- The whole map takes about 1 s.

### Reconciling with the field's water

`palette/water/rivers.py`, once per run, on copies of the field's planes. The field on disk is not
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
  texels beside a jump of more than 0.5 m between neighbours. Such a jump is two planes
  meeting, and any sampler draws a line along it.
- **Where a river meets other water, the higher surface shows**; a tie of 5 cm goes to the
  river. At a mouth the river plane dips under the lake or the sea, and the hand-over happens
  where the two levels agree, so the colour is continuous. Past the other water's last wet
  texel, away from the sea, the blur of its edge is no water to give way to (section 38,
  "Boxes over lower water").
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
- Where the 8 m rule cuts a river plane and no other plane speaks, the field's river-box
  water stays at the box level, as in recipe 6, for section 38 to re-level. The waterfall
  pool near (-1170, -240) no longer keeps its dark rectangle (section 38, "Boxes over lower
  water").
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
version, and deletes them with the caches unless `--keep-direct` is given. The 8
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
  +22.0 m, so the whole channel was levelled at +22 m, 39 m above the bed at the mouth.
- **A box over another body.** Where one body's axis-aligned box covers part of a lower
  body, the higher top wins. At (831, -353) a 131 m box sits over a lake at 58 m.
- **A box over its own fall.** At (1784, 559) the 94.8 m lake's `FGWaterVolume` reaches to
  x 1863, over the basin 113 m below, which the swamp's boxes cover at -16.7 m. The ground
  falls 100 m between two neighbouring texels at the lip.

The renderer then drew tens of metres of water. In the painted style that is opaque
Beer-Lambert water blended almost fully to the open-sea colour, in a flat shape with the
artwork mask's 3.66 m block edges, because the depth feather never fades out on a 40 m
depth. Every recipe since 2 has drawn it.

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

The test reads a body as one, so at the wide fall the big lake's high banks outvoted the
basin's ring, and the basin drew at the lake's level, 113 m deep. Where such a body was
perched, one membrane spanned the drop and ended in a straight hand-over line.

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
  at its new level. A body with no re-levelled water below a drop is judged byte for byte as
  before.

8 m was measured, not derived: at 10 m the plateau stays joined through one 10.0 m step, and
at 5 m 494 texels draw deeper than at 8 m and 1,454 shallower. The falls records (section 35)
are not read.

### Measured (build 502094)

591 bodies stand more than 2 m above a bank; 380 pass the ring test. 168,925 texels
(0.17 km²) are re-levelled, in about 4 s. Their median depth goes from 18.3 m to 1.1 m, and
the 90th percentile from 75.8 m to 8.2 m. Merged at 100 m, 38 places lose more than 5 m of
drawn depth. The largest are at (-822, 201), (-612, 772), (624, -505), (-375, -895),
(-1227, -278) and (-146, 961). Lakes whose banks stand above their level, the ocean, and
level-only water are byte-identical.

Water below a drop, measured on a render's planes after the river reconcile: 467 bodies stand
more than 2 m above a bank, up from 390, and 57 are cut at a drop. 233 bodies are perched,
up from 186, 47 of them below a drop with 32,791 texels; 115,440 texels are re-levelled, up
from 94,144. Of the 34,149 texels that move, the median drawn depth goes from 53.9 m to
1.4 m and the 90th percentile from 112.9 m to 3.4 m. 239 texels end more than 2 m deeper
and over 5 m deep, most of them box pieces that now take the level of the lake they sit in.
Bodies with no cut, sloped rivers and the ocean are byte-identical.

### Holes in a lake

The artwork's blue test also reads a lake's deep middle as dry, and the water under an arch
or bridge it draws across a lake. The field keeps those texels dry, so the renderer drew
the lake bed there: dark-middle blobs, and in the cliff-ringed lakes near (1980, -1890) a
straight strip 18 m wide and 220 m long under the arch, and the spokes of the star-shaped
rock beside it. `wet_holes` fills them after the re-levelling, on the same bodies:

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

At (-1077, -294), below the fall out of the crater lake at (-1033, -366), every style drew
the basin as an axis-aligned rectangle about 60 by 130 m: a thin sand rim along straight,
stair-stepped edges, a dark strip down its west side, and the river around it. v4 drew the
same rectangle in navy.

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

Four changes, all in the renderer. The field is not changed.

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
- **A hanging plane gives way** (section 34): the lip's river plane, 50 m over the basin, no
  longer hides the river in it.
- **The blur is no water** (section 34, "Drawing"): past the other water's last wet texel,
  away from the sea's reach, the river draws.

Measured in process on the whole field (water stages only, 3.4 GB at peak, the same 4 s for
the reconcile), with straight runs of at least 16 m along one grid line of the drawn water,
field and ribbons together, counted where they lie on a surface box's edge, away from the
sea:

| | before | after |
| --- | --- | --- |
| Inland runs on box edges, drawn water (the rim counted before) | 89 runs, 2,378 texels | 33 runs, 855 texels |
| Every straight run of 16 m and more, drawn water | 955 runs, 38,909 texels | 689 runs, 32,299 texels |
| Inland runs on box edges, the field's planes alone | 101 runs, 2,837 texels | 44 runs, 1,216 texels |
| Texels this section re-levels | 115,440 in 233 bodies | 42,550 in 455 bodies |

83,423 texels of the water planes every style draws change, in 42 places merged at 100 m. The swamp,
Blue Crater, the lake navy blocks, the sulfur pools and the ocean coast draw byte-identical;
the crater lakes, the desert river at (341, -1542) and Spire Coast change only where a box
strip or a rim was. Of the ten worst boxes, these went to no run: the basin at (-1144, -287),
the river under the 235.5 m box at (-764, 668), the lake under the 131.3 m volume at
(451, -547), the navy block at (-274, -785) and the lake at (-45, -470).

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

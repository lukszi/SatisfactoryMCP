# The map's palette

Every colour on the map page is declared by the module that draws with it, through
`declareColours(owner, {...})` in `palette.ts`. In dev mode that module audits the whole table
at boot: every pair of colours from two DIFFERENT owners closer than dE 15 is an error unless
`DISCHARGED` in `palette.ts` lists it with the distance it was measured at and the reason it is
safe. This page is the table those declarations add up to, and why each value is where it is;
the code carries one line per colour and points here. Module paths are relative to
`src/satisfactory_mcp/interfaces/web/frontend/src/map/`.

## Rules

- **dE is CIE76** (plain Euclidean distance in CIE Lab, D65). Every number on this page and in
  `palette.ts` is in that unit; CIEDE2000 gives different numbers for the same pairs.
- **Compared across owners, never within one.** A ramp inside one owner is deliberate: the belt
  tiers, the pipe tiers, the storage pair, wire and pole, and the biome grounds all step by about
  dE 15 to 17 (the "house step"). Sharing an owner says "these are meant to look related".
- **A mark is not a fill.** Most discharged pairs are a small stroked or filled mark against a
  large flat area or a thin line, which the eye does not confuse even close in colour.
- **Game tints do not move.** The ore dots use the in-game item tints, so where one collides the
  other side moves, and where neither can the pair is discharged.
- **Tone variants.** Some colours have a `dark` twin picked through `byMapTone()` in `map-tone.ts`
  when the base map's picture is dark; both are declared and both are audited.

## Cased lines

dE is about a swatch, and a thin line is not one. A 1.5 px stroke at 0.85 opacity is
antialiased across two or three pixels, so what a reader sees is the colour composited over the
ground at roughly 0.6: a dE 26.7 swatch is worth about dE 15 of contrast. No single colour is far
from both grounds the map is drawn over either: the game's artwork is a pale warm tan with cyan
water (its six commonest tones, binned to a 16-level cube, are `#486878`, `#888878`, `#a8a898`,
`#988878`, `#4898a8` and `#989888`) while `plain` is near-black.

So a thin line is drawn as a dark, wider CASING under a lighter CORE: whichever way the ground
goes, one half separates from it. The power wires are always cased; the belts are cased on a
light base. The casing's extra width is in screen pixels (`WIRE_CASING_PX`, `BELT_CASING_PX`,
carried on the path as `_widen`) and is re-added at every zoom, so the rim stays a rim.

## The table

Nearest neighbour is the closest colour of any OTHER owner, as the audit computes it today.
"Discharged" means the pair is listed in `DISCHARGED` in `palette.ts`; "standing" means it is on
the debt list there, `STANDING`, which prints one warning at boot.

### `markers` — node dots and the player (`drawn/markers.ts`)

| key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- |
| `Desc_OreIron_C` | `#c8b6a6` | iron ore node | routes/chevrons 10.1, discharged | game tint |
| `Desc_OreCopper_C` | `#e08a4b` | copper ore node | pickups/tape_pickup 15.0 | game tint |
| `Desc_Stone_C` | `#cfcfcf` | limestone node | routes/belt fast 13.2, discharged | game tint |
| `Desc_Coal_C` | `#4c4c4c` | coal node | regions/A 6.3, discharged with seven other grounds | game tint, near-black because coal is |
| `Desc_OreGold_C` | `#e3c74a` | caterium node | pickups/power_slug_yellow 6.5, standing | game tint |
| `Desc_Sulfur_C` | `#e8e35c` | sulfur node | pickups/power_slug_yellow 8.8, standing | game tint |
| `Desc_RawQuartz_C` | `#e59ce0` | raw quartz node | power/wires 22.8 | game tint |
| `Desc_OreBauxite_C` | `#b06a4a` | bauxite node | pickups/tape_pickup 18.4 | game tint; pipes run where bauxite is refined |
| `Desc_OreUranium_C` | `#7ce07c` | uranium node | placements/generators 21.7 | game tint |
| `Desc_LiquidOil_C` | `#6b4bb0` | crude oil node | placements/storage fluid 16.2 | game tint |
| `Desc_NitrogenGas_C` | `#6ec5e0` | nitrogen node | pickups/power_slug_blue 4.6, standing | game tint |
| `Desc_Water_C` | `#3f8fd0` | water node | placements/machines 8.6, discharged | game tint |
| `Desc_SAM_C` | `#b04bd0` | SAM node | pickups/power_slug_purple 8.0, standing | game tint |
| `Desc_Geyser_C` | `#d97b4f` | geyser node (a placement target, not an item) | pickups/tape_pickup 15.3 | |
| `coal dark` | `#8c8f96` | coal node on a dark base | routes/belt slow 6.1, discharged | near-black coal vanishes on a dark base |
| `locked casing` | `#262040` | dark ring under a locked node's hollow dot | pickups/pickup collected 11.5, discharged | must read on every base |
| `player` | `#f5f0e8` | where the player last stood | routes/chevrons 17.0 | the value nothing else spends |

### `pickups` — what lies on the ground (`drawn/pickups.ts`)

| key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- |
| `somersloop` | `#d84378` | pickup | placements/stopped 28.3 | the rose the page's reds leave free |
| `mercer_sphere` | `#b06ae0` | pickup | markers/Desc_SAM_C 13.5, standing | |
| `hard_drive` | `#5468d4` | pickup | markers/Desc_LiquidOil_C 16.3 | indigo: blue enough to be a drive, clear of the machine blue |
| `loot_cache` | `#d8b46e` | pickup | markers/Desc_OreGold_C 25.2 | |
| `crashed_drop_pod` | `#838d3f` | pickup | regions/E 28.1 | the drab olive no network or ground spends |
| `power_slug_blue` | `#5cc8e8` | pickup | markers/Desc_NitrogenGas_C 4.6, standing | |
| `power_slug_yellow` | `#e8d55c` | pickup | markers/Desc_OreGold_C 6.5, standing | |
| `power_slug_purple` | `#c85ce8` | pickup | markers/Desc_SAM_C 8.0, standing | |
| `mushroom` | `#a8c86e` | pickup | markers/Desc_OreUranium_C 24.4 | |
| `tape_pickup` | `#e09a6e` | pickup | markers/Desc_OreCopper_C 15.0 | |
| `pickup fallback` | `#7fd1b9` | a category the table does not name | placements/generators 24.0 | a stand-in that reaches the screen is still audited |
| `pickup collected` | `#2a3147` | the X over a collected pickup, light base | markers/locked casing 11.5, discharged | two crossed strokes, never a ring |
| `pickup collected dark` | `#9aa0a8` | the same X on a dark base | routes/belts 5.7, discharged | two crossed strokes, never a run or a disc |

A pickup is the same filled disc as a node dot, on the same ground, so the two are their own
owners and the audit measures every pickup against every node. Node colours are game tints, so
where a pair is too close the pickup moves. Five pairs stand on the debt list until it does: the
three power slugs and the mercer sphere against nitrogen, caterium, sulfur and SAM, the closest
`power_slug_blue` against `Desc_NitrogenGas_C` at 4.6. The two X marks are a different kind of
mark from a disc or a ring, and are discharged.

### `placements` — `drawn/placements.ts`

| key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- |
| `foundations` | `#545470` | the concrete, one 8 m tile per piece | pickups/pickup collected 16.5 | a large area at full strength over the biome fill, so it must clear the grounds: slate violet is the cool direction they leave open; dE 15.3 from the nearest artwork tone, which REGION_BLEND does not soften |
| `machines` | `#4aa3df` | machine footprints | markers/Desc_Water_C 8.6, discharged | the page's oldest colour; the only machine on a water node is an extractor, drawn ultramarine |
| `extractors` | `#19039c` | extractor footprints | markers/Desc_LiquidOil_C 36.2 | not amber, which the page spends on a pending removal |
| `generators` | `#a3f5b4` | generator footprints | crates/crates 21.5 | not tan, which vanished on sand |
| `blocked` | `#ffd000` | thick outline: output full | markers/Desc_OreGold_C 23.4 | CSS twin is `--blocked`; docs/save-projection.md §6.2d |
| `stopped` | `#d9534f` | thick outline: stopped and needs action | markers/Desc_Geyser_C 23.1 | |
| `storage` | `#6a78c8` | a solid container, filled box | pickups/hard_drive 18.0 | must clear the other filled boxes and the selection pink (dE 64.1) |
| `storage fluid` | `#253496` | a fluid buffer, one value step below | markers/Desc_LiquidOil_C 16.2 | |

### `routes` — the belts and the pipes (`drawn/belts.ts`, `drawn/pipes.ts`)

| key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- |
| `belts` | `#93a5b4` | Mk3 belts and unknown tiers, and the layer swatch | pickups/pickup collected dark 5.7, discharged | mid steel: has to read over dark concrete AND pale sand; never re-measured, everything else moves around it |
| `belt slow` | `#7f8f9d` | Mk1 and Mk2 belts | markers/coal dark 6.1, discharged | one value step below the middle |
| `belt fast` | `#a7b9c7` | Mk4 and Mk5 belts | pickups/pickup collected dark 10.0, discharged | one value step above; dE 15.6 slow to fast is the house step |
| `lift fill` | `#0e1116` | the hole inside a conveyor lift's ring, and the belt casing | power/casing dark 3.5, discharged | near-black, because a hole can always get darker |
| `pipes` | `#7d221a` | pipes of unknown tier, and the layer swatch | markers/Desc_OreBauxite_C 27.4 | oxide: amber is the pending removal's family and mid-rust is the bauxite dot's neighbourhood; dE 49.0 from the nearest artwork tone |
| `pipe mk1` | `#690e06` | Mk1 pipes | regions/L 32.6 | one value step below the middle |
| `pipe mk2` | `#91362e` | Mk2 pipes | markers/Desc_OreBauxite_C 21.4 | one value step above; dE 15.1 Mk1 to Mk2 |
| `chevrons` | `#e8cbb4` | direction marks on a pipe, at 0.7 opacity | markers/Desc_OreIron_C 10.1, discharged | composited over the pipe tones it is dE 41.3 to 50.6 from the pipe under it, and 14.3 to 17.0 from the iron dot |

### `power` — `drawn/power-wires.ts`

| key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- |
| `casing` | `#1c1550` | the dark rim under every wire and pole, light base | markers/locked casing 19.3 | violet, not black: a near-black casing lands in the map's tightest grey cluster; dE 48.4 to 54.3 from the nearest artwork tone |
| `casing dark` | `#08060f` | the same rim on a dark base | routes/lift fill 3.5, discharged | on a dark base the indigo is only dE 18 from the ground |
| `wires` | `#b8b0f8` | the wire core, and the layer swatch | markers/Desc_RawQuartz_C 22.8 | violet is this layer's free hue; the belts it runs beside are dE 33.2 to 36.5 away; dE 39.1 to 40.2 from the nearest artwork tone |
| `poles` | `#d8c8f8` | pole discs and tower rings | routes/belt fast 23.6 | one value step above the wire (dE 16.0): lighter buys the world view, where a pole says a base is here |

### `regions` — `map/regions.ts`

One muted ground per biome letter (the legend letter `data/region_names.json` assigns, which is
alphabetical by name). These are the BLENDED values, painted at full opacity, because the
transparency is applied once at the pane (`REGION_BLEND`).

| key | hex | region | nearest cross-owner neighbour |
| --- | --- | --- | --- |
| `A` | `#3e3e3c` | Abyss Cliffs | markers/Desc_Coal_C 6.3, discharged |
| `B` | `#284e5a` | Blue Crater | markers/Desc_Coal_C 14.9, discharged |
| `C` | `#2e5348` | Crater Lakes | markers/Desc_Coal_C 16.4 |
| `D` | `#654e37` | Desert Canyons | markers/Desc_Coal_C 18.6 |
| `E` | `#726443` | Dune Desert | markers/Desc_Coal_C 23.3 |
| `F` | `#3b5a3b` | Grass Fields | markers/Desc_Coal_C 23.6 |
| `G` | `#294834` | Jungle Spires | markers/Desc_Coal_C 19.3 |
| `H` | `#32544d` | Lake Forest | markers/Desc_Coal_C 14.3, discharged |
| `I` | `#594a37` | Maze Canyons | markers/Desc_Coal_C 14.1, discharged |
| `J` | `#8a8478` | No Man's Land | markers/coal dark 12.1, discharged |
| `K` | `#2e4637` | Northern Forest | markers/Desc_Coal_C 15.2 |
| `L` | `#65423b` | Red Bamboo Fields | markers/Desc_Coal_C 17.7 |
| `M` | `#4e3937` | Red Jungle | markers/Desc_Coal_C 11.9, discharged |
| `N` | `#5c5b4e` | Rocky Desert | markers/Desc_Coal_C 10.0, discharged |
| `O` | `#335041` | Southern Forest | markers/Desc_Coal_C 15.6 |
| `P` | `#295258` | Spire Coast | markers/Desc_Coal_C 14.8, discharged |
| `Q` | `#374232` | Swamp | markers/Desc_Coal_C 12.9, discharged |
| `R` | `#294233` | Titan Forest | markers/Desc_Coal_C 16.3 |
| `S` | `#585d40` | Western Dune Forest | markers/Desc_Coal_C 18.7 |

No Man's Land is the outer coast and the ocean, the largest thing on the layer, and the one
ground that must NOT read as a biome: bare, pale and desaturated, one step brighter than any
other. Its nearest grounds are Rocky Desert at dE 17.1 (it borders it along most of the west
coast), Dune Desert at 18.4 and Western Dune Forest at 20.6. The alternatives were worse against
that same border: the render's own no-man's-land tone (`#7c7a6c`) is 12.6 away, a warm sand
(`#807a68`) 13.3, anything darker collapses onto it (`#5a5750` is 3.9), and a cool grey
(`#46484a`) is 5.1 from Abyss Cliffs.

Coal against the grounds is the one collision hue cannot fix: the nineteen grounds cover the
whole dark-neutral range, so every near-black that clears one lands on another, and a coal that
is not near-black is not coal. The marks are different kinds instead: a 3 to 6 px stroked disc
against a 256 m flat fill faded to 0.45 wherever there is imagery.

### The rest

| owner | key | hex | role | nearest cross-owner neighbour | constraint |
| --- | --- | --- | --- | --- | --- |
| `crates` | `crates` | `#3fcc94` | crate glyphs (`drawn/crates.ts`) | placements/generators 21.5 | a 13 px glyph found on open terrain at world zoom: dE 50.2 from the nearest ground |
| `plans` | `plans` | `#4ec22e` | a sited plan's dashed outline (`drawn/plan-sitings.ts`) | markers/Desc_OreUranium_C 25.0 | green, which nothing built spends; dE 105.5 from the concrete and 110.7 from the machine blue it is laid over |
| `map-highlight` | `highlight` | `#ff4fd8` | the one selection outline or ring (`map/map-highlight.ts`) | pickups/power_slug_purple 24.4 | |

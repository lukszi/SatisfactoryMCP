# The map page: one base map at a time, and the side panel

Sections 18 and 21 of the [design spec](../../DESIGN.md): how the page picks a base map, and what its
side panel shows. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-42-the-map).

Numbers 19 to 24 also name sections of parked.md, residency.md and plumbing.md; in these
files they are the map's ([the document set](../../DESIGN.md#the-document-set)).

## 18. One base map at a time, and the page says which (2026-07-31)

The page keeps the Google Earth split: **modes**, which are one question with one answer, apart
from **overlays**, which are independent yes/nos. The modes are radios in their own section at
the top of the layer control, above a rule and above every checkbox. Which modes there are,
and which one the page opens on, is the map registry's: every ready map type ticked "in
switcher", the shared default first, and plain ([maps_contract.md](../maps_contract.md) §6.3).
`plain` is a real mode rather than the absence of one: no base imagery, what a fresh clone with
no generated renders shows, and the mode the biome tint is designed for.

**A mode is one `L.TileLayer` and a mode switch swaps it.** Nothing else moves: not the CRS,
not the panes, not one data overlay, not the region blend's rule. That is the serving design
of §17 on the client: same frame, same tile size, same grid, so the client changes one path
segment. The one per-mode difference is depth: `maxNativeZoom` comes from that layer's own
`X-Map-Tile-Max-Z`, or `X-Map-Tile-2x-Max-Z` when the client fetches @2x tiles, and Leaflet
upscales past it.

### The rule that replaced the auto-untick

A render "arrives" on every switch between modes, so a heuristic that unticks the region box
when one arrives would throw away a choice the reader made in between. So it is a rule about
states: **the region tint defaults OFF under any imagery mode and ON under plain, and a
reader's own tick of that box wins for the rest of the session.** The default is what the page
does when it has not been told, not what it does instead of being told.

Programmatic ticks are told from real ones by a flag, because they cannot be told apart
afterwards: Leaflet fires `overlayadd` from the layer's own `add` event, so `map.addLayer` and
a click arrive identically.

### Resolution order, and what a link pins

`#world=…&save=…&mode=…&z=…&c=…`: subject, then picture, then viewport. A `mode` this machine
cannot draw falls back as maps_contract.md §6.3 says, so a link to someone else's render lands
on a map rather than on an error. A drawn render is never chosen *for* the reader unless it is
the shared default, even when it is the only picture on disk: it is an interpretation of this
world rather than the map of it. The fragment omits `mode` until the probes have answered, so
a pan in the first fifty milliseconds cannot pin a mode nobody chose.

### A mode that is not there stays on screen

An ungenerated pyramid greys its row out rather than removing it, with the generator named in
the row's `title`: the same sentence `/api/maptiles`' GET 404 carries, repeated in the client
because the page probes with **HEAD** and HEAD answers 204 with no body on purpose. A pyramid
whose tiles turn out not to *draw* is treated the same way plus a toast: the mode greys out
with the reason in place of the generator, and the page falls back to `plain` rather than to
another render, because silently substituting a different picture of the same world is the
one answer that could be mistaken for success.

The artwork's single-image `/api/mapimage` fallback is a detail of the artwork mode: probed
only when its pyramid does not answer, and drawn as an `imageOverlay`.

### Under the radios: what the picked map is made of (2026-10-08)

A base map drawn with live light has parts that can be turned off without changing which
picture it is. They are checkboxes under the radios, inside the mode section and folded with
it (`layercontrol/part-picker.ts`, kept there through `radio-section.ts`'s `tail`). Today there
is one, **shade**: off, the map shows its flat colour with no light at all. Each part is a
Settings → map switch, so it is remembered like the sun's switches. The rows show only while the
picked mode has a light, and greyed, with the reason as their tooltip, while that light is drawn
baked: without WebGL2, or after the live light failed. A part is a switch in the lit layer's
shader, not a Leaflet layer stacked over it: the shade is the light itself
([light-and-crowns.md](light-and-crowns.md) §29, "The page").

### The mode radios have a sibling: the floor picker

The same split, one level in. "Which storey of this factory am I looking at" is one question
with one answer, so it is radios in its own folded section above the modes, drawn by
`layercontrol/floor-picker.ts` and decided by `floors/floors.ts` through `onFloorPick`, the
seam `onModePick` is, for the same reason. It differs where the rows differ: a mode is a word
and a floor is a word plus a measurement, and a mezzanine has to read as subordinate to the
storey it is a ledge on. The section exists only while the page is slicing something, and
`floor=<platform>/<band>` joins the fragment between `save` and `mode`: subject, then how much
of the subject, then picture, then viewport. See [§16b](../parked.md#16b-built-floor-wise-factory-view-2026-07-31--only-think-about-that-idea)
for what a floor is and how it is recovered.

## 21. The side panel: factory health and power circuits (2026-09-26)

Two tabs over the map, fed by two routes that call the same domain code the MCP tools do.

**Factories** is `/api/factories/health`: `factory_health`'s sweep over every named factory,
one row each — `assess` for the states and the worst machines, `build_view` for the measured
MW, `LabelStore.review` for a label whose anchors have shrunk or gone. Sorted by how many
machines sit in an actionable state (`health.ACTIONABLE`: dead node, no recipe, blocked,
starved, stalled), then by uptime. The map marks the same set on the machines themselves: a
thick outline for every actionable machine, red and hollow when stopped, yellow and solid when
blocked (docs/save-projection.md §6.2d). The panel's header names the same set, read from
`actionable_states`.
A row flies to the factory's box and outlines it; a machine under it flies to that machine.
Clicking a factory label on the map selects its row.

**Power** is `/api/power/circuits`: `power_report`'s world ledger at the top, then one row per
**circuit**, where a circuit is a connected component of the save's power edges and its
figures are `PowerLedger` run over only the records standing on it. Nothing new is computed;
the split is the only addition, and it has three limits worth knowing before trusting it:

- **Switches join.** The save carries no `mCircuitID` and no switch state, so an open power
  switch or priority switch still joins the two sides here. Two circuits the game keeps apart
  may read as one. This errs the same way `health._lit` does.
- **Batteries are not in the ledger.** `PowerLedger` counts generators and consumers; a
  circuit running on Power Storage reads as generation 0 with draw, which is also what a
  circuit with no source looks like.
- **Unwired records are in no ledger.** A machine or generator on no power edge draws from and
  feeds nothing, so `power_report` leaves it out of both sides and the circuit rows sum to the
  world line. `off_grid` carries its count and rated MW. One rule decides both the count and
  the list: the wire is tested before anything else, so a paused machine on no wire is counted
  (at no MW, and in `off_grid.paused`) and listed. A circuit's `consumers` counts its paused
  machines the same way, and its ledger says how many in `paused`. The machines are listed on their own,
  beside the machines wired to a circuit no generator stands on — `assess`'s two lists over
  every machine in the world — and generators on no wire have a third list.
- **Two generator classes need help from the save side.** A standing Biomass Burner is
  `Build_GeneratorBiomass_C` in the save and `Build_GeneratorBiomass_Automated_C` in the dump,
  joined by `BUILDING_CLASS_ALIASES`. The HUB's built-in burner has no entry at all, so it is
  listed in `unmodellable`, on the world and on its circuit, and its output is not counted.

**Around the panel (2026-09-27).** Popups and flights keep clear of the overlays: every
auto-pan and every fly-to is padded by what the side panel, the layer control and the trace
card cover (`overlayPad` in `map.ts`), and the whole-world view fits the map sheet into what
is left. Below 700 px the panel is a bottom sheet, the panel and the layer control start
folded, and opening one of the panel, the layer control or a trace folds the others.

- **`show=label:<name>`** in the fragment selects that factory once factory health has loaded:
  the panel row, the outline, the flight and the machines, belts and pipes layers.
  `show_on_map` writes it when its place is a factory label.
- **Layer ticks persist** per browser, except the pickup rows (the fragment owns those) and
  the region tint (the base-map mode decides it).
- **Machine rows name their factory.** `/api/machines` sends `factory`, the label whose
  anchors hold the machine, so a machine popup can link to that factory's dashboard page.
- **The marker key** lives at the bottom of the layer control and folds with it.

# The map page: one base map at a time, and the side panel

Sections 18 and 21 of the [design spec](../../DESIGN.md): how the page picks a base map, and what its
side panel shows. A section number below resolves through the [map's index](../spatial-and-map.md#sections-17-to-40-the-map).

Numbers 19 to 24 also name sections of parked.md, residency.md and plumbing.md; in these
files they are the map's ([the document set](../../DESIGN.md#the-document-set)).

## 18. One base map at a time, and the page says which (2026-07-31)

> Since 2026-10-05 the modes are the map types of the registry rather than these four names,
> `mode=` holds a type id (`artwork` still reads as `map`), and a shared default chosen in
> Settings can open a render. [maps_contract.md](../maps_contract.md) §6.3 has the current rules;
> the rest of this section stands.

Three pyramids on the server were three pictures the page could not ask for: the client
addressed the artwork alias and nothing else. What it now has is the Google Earth split —
**modes**, which are one question with one answer, kept apart from **overlays**, which are
thirty-five independent yes/nos.

**The four modes are `artwork`, `terrain`, `satellite`, `plain`**, as radios in their own
section at the top of the layer control, above a rule and above every checkbox. `plain` is a
real mode rather than the absence of one: it is no base imagery, which is the shipped state,
what a fresh clone with no generated renders looks like, and — since it is the mode the biome
tint is designed for — a thing a reader may prefer.

**A mode is one `L.TileLayer` and a mode switch swaps it.** Nothing else moves: not the CRS,
not the three panes, not one data overlay, not the region blend's rule. That is the serving
design of §17 arriving on the client exactly as intended — same frame, same tile size, same
grid, so the client changes one path segment. The one per-mode difference that survives is
depth: `maxNativeZoom` comes from that layer's own `X-Map-Tile-Max-Z`, so the renders stop at
z6 (z5 when the client is fetching @2x tiles, which are one level shallower) and Leaflet
upscales past it while the artwork runs to its own z7 if it was `--enhance`d.

**Measured, at 1600×1000 on the reference world:** the frame is unmoved by a switch — map
rect `[0, 40, 1600, 960]`, pane transform `translate3d(0,0,0)`, scale bar `500 m`, `z=-3
c=0,0` — identical across all four; and the network log for a switch contains that mode's
tiles and no other layer's.

### The rule that replaced the auto-untick

A render arriving used to *untick* the region box, once, as an event. With four modes that
stops being expressible: "arriving" now happens on every switch, so the same heuristic would
throw away a choice the reader had made in between. So it is stated as a rule about states:
**the region tint defaults OFF under any imagery mode and ON under plain** — which is the map
the old heuristic left you on, said as a rule — **and a reader's own tick of that box wins for
the rest of the session.** The default is what the page does when it has not been told, not
what it does instead of being told.

Programmatic ticks are told from real ones by a flag, because they cannot be told apart
afterwards: Leaflet fires `overlayadd` from the layer's own `add` event, so `map.addLayer` and
a click arrive identically.

### Resolution order, and what a link pins

`#world=…&save=…&mode=…&z=…&c=…` — subject, then picture, then viewport. An absent or unknown
`mode` resolves **artwork → plain**, and so does a mode this machine has never generated: a
link to someone else's terrain render should land on a map rather than on an error. Terrain
and satellite are never chosen *for* you even when they are the only pictures on disk, because
they are interpretations of this world rather than the map of it and the page should not have
an opinion about which. The fragment omits `mode` entirely until the probes have answered, so
a pan in the first fifty milliseconds cannot pin a mode nobody chose.

### A mode that is not there stays on screen

An ungenerated pyramid greys its row out rather than removing it, with the generator named in
the row's `title` — the same sentence `/api/maptiles`' GET 404 carries, repeated in the client
because the page probes with **HEAD** and HEAD answers 204 with no body on purpose. Asking for
the message would mean asking for the error the server went out of its way not to raise. A
pyramid whose tiles turn out not to *draw* is treated as the same thing plus a toast: the mode
greys out with the reason in place of the generator, and the page falls back to `plain` rather
than to another render, because silently substituting a different picture of the same world is
the one answer that could be mistaken for success.

The artwork's single-image `/api/mapimage` fallback survives as a detail of the artwork mode
rather than a stage of a loader: probed only when its pyramid does not answer, and drawn as the
`imageOverlay` it always was.

### The mode radios have a sibling: the floor picker

The same split, one level in. "Which storey of this factory am I looking at" is one question
with one answer, so it is radios in its own folded section above the modes, drawn by
`layercontrol/floor-picker.ts` and decided by `floors/floors.ts` through `onFloorPick` —
exactly the seam `onModePick` is, for exactly the reason. It differs where the rows differ: a
mode is a word and a floor is a word plus a measurement, and a mezzanine has to read as
subordinate to the storey it is a ledge on. The section exists only while the page is slicing
something, and `floor=<platform>/<band>` joins the fragment between `save` and `mode` —
subject, then how much of the subject, then picture, then viewport. See [§16b](../parked.md#16b-built-floor-wise-factory-view-2026-07-31--only-think-about-that-idea)
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

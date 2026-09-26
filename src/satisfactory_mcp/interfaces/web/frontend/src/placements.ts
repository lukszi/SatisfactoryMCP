/* What the player built and where it stands: the floor plan, the machines on it, and the
 * containers among them.
 *
 * Together because they are the same drawing problem -- a rotated footprint at a measured size
 * -- and because all three fall back to a stated stand-in when the docs dump carries no
 * clearance for a class. The difference is only where the size comes from: a foundation takes
 * the grid's own tile, a machine and a container take their own w_m and l_m. The storage layer
 * at the bottom is the one that is not only a placement, because a container's CONTENTS are
 * the point of drawing it.
 */

import { CONTENTS_POPUP_PX, code, contentsRows, count, popup } from "./dom";
import type { Row } from "./dom";
import { refreshFloors } from "./floors";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { footprintCorners } from "./map";
import { raiseNodeDots } from "./markers";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";

import type {
  MachinesResponse,
  StorageResponse,
  StorageRow,
  StructuresResponse,
} from "./api-shapes";

/* The player's floor plan: one 8 m tile per placed foundation, ramp, wall or catwalk.
 *
 * NO PER-CLASS SIZE. None of these eighteen classes has clearance data, and they all snap to
 * the same grid, whose edge the server reports as `tile_m` -- so a wall paints the tile it
 * stands on rather than its own thin volume, straddling two tiles and fringing a walled
 * platform by half a tile, which is invisible at any zoom where the platform is legible.
 *
 * Stroked in its own fill colour, the trick the biome cells already use: no stroke at all
 * leaves hairline seams between neighbouring tiles at low zoom, and a stroke in any other
 * colour draws an 8 m grid.
 */
/* Concrete. A slab is poured ON the biome fill, both are large areas at full strength wherever
 * there is no imagery, and no warrant talks that pair apart -- so this colour has to clear the
 * grounds, and slate violet is the one cool direction they leave open (a neutral grey dies on
 * the coal dot at one end and No Man's Land at the other, and the teal side is Blue Crater and
 * Spire Coast). Measured: dE 18.1 from the nearest ground (Blue Crater), 17.9 from its nearest
 * cross-owner neighbour anywhere (that same coal dot), 24.7 from the nearest belt tone, and
 * 15.3 from the nearest of the six artwork tones binned in power.ts -- the one comparison
 * REGION_BLEND does not soften, since the render is what shows through the fade. */
var STRUCTURE_COLOUR = declareColours("placements", { foundations: "#545470" }).foundations;

export function drawStructures(data: StructuresResponse): void {
  // First of the built band, because the concrete is what everything else in it stands on
  // or runs over -- the legend reads a base bottom-up, exactly as the player laid it.
  var group = layer("foundations", true, STRUCTURE_COLOUR, [BAND.built, 0, "foundations"]);
  // No `|| 8`: `tile_m` is the server's FOUNDATION_M constant and is always sent, and a
  // fallback here would be the second copy of the number this field exists to prevent.
  var half = data.tile_m / 2;
  data.structures.forEach(function (s, row) {
    // No position guard: `iter_structures` DROPS a row whose x, y or z will not read as a
    // number, so a piece that arrives here has all three and `StructureRow` declares that.
    var piece = L.polygon(footprintCorners(s.x_m, s.y_m, half, half, s.yaw), {
      color: STRUCTURE_COLOUR,
      weight: 1,
      opacity: 0.9,
      fillColor: STRUCTURE_COLOUR,
      fillOpacity: 0.9,
      interactive: false,
      pane: "foundations",
    });
    // The only name a lightweight buildable has is its place in this list, so that is what the
    // floor filter joins on -- `deck_rows` in `/api/floors` indexes exactly this. The index
    // comes from the payload rather than a counter, so the two sides stay lined up whatever
    // this loop does with a row.
    piece._floor = { row: row, x_m: s.x_m, y_m: s.y_m, z_m: s.z_m };
    piece.addTo(group);
  });
}

/* Static, and early in the wave: the concrete is what floor mode measures a platform's extent
 * from, so the opening flight cannot happen until these pieces are on the map. */
registerFetch<StructuresResponse>({
  wave: "static",
  rank: 20,
  path: "/api/structures",
  label: "structures",
  clears: ["foundations"],
  refilters: true,
  draw: drawStructures,
});

/* The square a machine gets when the docs dump carries no clearance for its class -- both
 * biomass burners, here. Everything else is drawn at its own `w_m`/`l_m` and turned by its own
 * `yaw`, so a Manufacturer (18x20 m) reads as the eight-times-larger thing it is beside a
 * Constructor (8x10 m). */
var MACHINE_FALLBACK_M = 6;

/* Blue, amber, red -- the page's oldest three colours, and the ones every measured warrant
 * since has had to get out of the way of. Not themselves measured against anything; the
 * audit's one finding against them, the water node dot at dE 8.6 from this blue, is DISCHARGED
 * in palette.ts because the only machine that ever stands in the dot's square metre is a water
 * extractor, drawn in this table's amber at dE 101.3, under a dot raiseNodeDots() keeps on
 * top. */
var KIND_COLOUR: Record<string, string> = declareColours("placements", {
  machines: "#4aa3df",
  extractors: "#e0a33f",
  generators: "#d9534f",
});

/* The three layers /api/machines answers with, and the one row shape all three carry.
 * Spelled as a tuple rather than inferred, so `data[kind]` is a PlacementRow[] rather than
 * an index into an object with a string. */
const MACHINE_KINDS = ["machines", "extractors", "generators"] as const;

/* Where each of the three sits in the built band, spelled rather than taken from the loop's
 * own index: the order these are FETCHED and drawn in is one payload's field order, and the
 * order they are LISTED in is an editorial choice about a legend. They agree today. Reading
 * the second off the first would make the day they stop agreeing a silent one. */
const MACHINE_SLOT: Record<(typeof MACHINE_KINDS)[number], number> = {
  machines: 40,
  extractors: 50,
  generators: 60,
};

/* Thick outline = the server's `actionable`: generator red when stopped, signal yellow when
 * blocked. Grammar and the yellow's measured distances: docs/save-projection.md §6.2d. */
export var BLOCKED = "blocked";
var BLOCKED_COLOUR = declareColours("placements", { blocked: "#ffd000" }).blocked;
var STOPPED_COLOUR = KIND_COLOUR.generators;
document.documentElement.style.setProperty("--blocked", BLOCKED_COLOUR);

export function stateTone(state: string, actionable: boolean): string {
  return state === BLOCKED ? "blocked" : actionable ? "bad" : "";
}

export function drawMachines(data: MachinesResponse): void {
  MACHINE_KINDS.forEach(function (kind) {
    var group = layer(kind, kind !== "machines", KIND_COLOUR[kind], [
      BAND.built,
      MACHINE_SLOT[kind],
      kind,
    ]);
    data[kind].forEach(function (m) {
      // One guard, on x only. The row type says `y_m` can be null too, and the assertion below
      // is that claim not being acted on: if the projection ever sends half a position it
      // should be visible rather than silently skipped.
      if (m.x_m === null) return;
      var w = (m.w_m || MACHINE_FALLBACK_M) / 2;
      var l = (m.l_m || MACHINE_FALLBACK_M) / 2;
      // Read off `state` and not off `paused`, though the two agree: `paused` is first in
      // health.STATES, so one field decides the whole mark and the two can never disagree
      // about the same rectangle.
      var blocked = m.state === BLOCKED;
      var stopped = m.actionable && !blocked;
      var idle = stopped || m.state === "paused";
      var piece = L.polygon(footprintCorners(m.x_m, m.y_m!, w, l, m.yaw), {
        color: blocked ? BLOCKED_COLOUR : stopped ? STOPPED_COLOUR : KIND_COLOUR[kind],
        fillColor: KIND_COLOUR[kind],
        weight: m.actionable ? 3 : 1,
        fillOpacity: idle ? 0.15 : 0.65,
        dashArray: m.state === "paused" ? "2,2" : undefined,
      }).bindPopup(
        popup([
          ["building", m.name],
          ["recipe", m.recipe_name || m.recipe],
          ["clock", m.clock === null ? null : Math.round(m.clock * 100) + "%"],
          // The state replaces the old "paused: yes" row rather than joining it: they would
          // be the same claim twice, and this one can also say why a machine nobody paused
          // is standing still.
          ["state", blocked ? "blocked — output full, runs again once emptied" : m.state],
          ["needs action", m.actionable ? "yes" : null],
          // The only measured number in this whole project -- the fraction of the machine's
          // own ~300 s window it spent producing. Absent, not "0%", for a building that
          // carries no monitor: 46 of this world's 570 do not.
          ["uptime", m.uptime === null ? null : Math.round(m.uptime * 100) + "%"],
          // All three sides of the clearance box: a Refinery being 15 m tall is why a floor
          // view can say it comes through the ceiling, and the reader looking at that ghost
          // should find the number here.
          ["footprint", m.w_m && m.l_m ? m.w_m + " x " + m.l_m + " m" : null],
          ["height", m.h_m ? m.h_m + " m" : null],
          // Degrees about world Z, positive turning +X towards +Y. Absent, not "0", when the
          // projection carries no facing at all.
          ["facing", m.yaw === null || m.yaw === undefined ? null : Math.round(m.yaw) + "°"],
          ["at", m.x_m + ", " + m.y_m + " m"],
          ["instance", code(m.instance_leaf)],
        ])
      );
      // What the floor filter joins a machine by, and what it needs to know to tell whether
      // one on a lower deck comes up through this floor. See floors.ts.
      piece._floor = {
        id: m.instance_leaf,
        z_m: m.z_m === null ? undefined : m.z_m,
        h_m: m.h_m,
      };
      piece.addTo(group);
    });
  });
  raiseNodeDots();
}

/* The live wave's first entry, and the only one on the page with a post-draw hook. A save
 * write changes what is BUILT, so it changes the decomposition, and the ids a band lists are
 * what the floor filter runs on: without `after`, a machine placed since the view was opened
 * would be drawn by /api/machines, listed by no band, and silently missing from every floor. */
registerFetch<MachinesResponse>({
  wave: "live",
  rank: 10,
  path: "/api/machines",
  label: "machines",
  clears: ["machines", "extractors", "generators"],
  refilters: true,
  draw: drawMachines,
  after: refreshFloors,
});


/* Storage: the boxes, and what is in them.
 *
 * ONE LAYER, TWO KINDS, told apart by a value step in one hue -- see STORAGE below. A fluid
 * buffer and a storage container are both boxes the player put things in, so they belong to one
 * checkbox; a second hue would make the legend claim these are two networks.
 *
 * OFF BY DEFAULT, and NOT part of the reveal a factory label triggers -- see FACTORY_LAYERS in
 * labels.ts.
 */

/* Storage, measured. A container is drawn as a filled footprint box, so what it has to
 * separate from is the other filled boxes -- the three machine kinds, the belt attachments and
 * the concrete it stands on -- and magenta is what the page has left, blue, amber, red, steel
 * and rust all being spent.
 *
 * In CIE Lab it is dE 42.4 from its nearest filled box (the concrete; the generator red is
 * 51.8) and 48.5 from the nearest biome ground, which are the two comparisons that decide
 * whether a box reads. Its nearest neighbour ANYWHERE is the raw-quartz node dot at dE 27.4 --
 * a small disc on open terrain rather than a rectangle inside a factory, so the two are never
 * asked to be told apart in the same square metre, which is the axis the DISCHARGED warrants in
 * palette.ts turn on. The alternatives measured beside it were worse on one of the two: a
 * lighter magenta (#c76bb0) lands dE 17.7 from that same quartz dot, a violet (#8c72c4) dE 19.1
 * from the crude-oil dot and only 37.3 from the machine blue, and a sea green dE 10.5 from the
 * pickup teal.
 *
 * The fluid buffers are one value step down the same hue, at the house step of dE 16.7 against
 * the belts' 15.6 and the pipes' 15.1. It moves AWAY from everything, ending dE 33.7 from its
 * nearest colour on the page (the wire casing, with the crude-oil dot and the concrete both at
 * 34.0) and 37.6 from the nearest ground.
 */
var STORAGE = declareColours("placements", {
  storage: "#ad4f96",
  "storage fluid": "#7f3169",
});
var STORAGE_COLOUR = STORAGE.storage;
var STORAGE_FLUID_COLOUR = STORAGE["storage fluid"];

/* A container the docs dump carries no clearance for: the HUB's own box, the Blueprint
 * Designer's, and the Dimensional Depot uploader. The server sends null rather than a number
 * invented there, because an invented one would arrive indistinguishable from a measurement.
 * Four metres is half a foundation tile: small enough not to overstate an uploader, big enough
 * to be clickable at the zoom the layer is read at. */
var STORAGE_FALLBACK_M = 4;

/* What is in one container, as popup rows.
 *
 * TWO KINDS, AND ONLY ONE OF THEM GETS A GRID. A solid container holds stacks of things a
 * reader would recognise by their pictures, which is what `contentsRows` draws; a fluid buffer
 * holds ONE fluid and a level, so a grid of a single tile would be a picture claiming to be a
 * set, with nowhere to put the reading -- what a reader wants off a tank is "1,441 m³ of 2,400
 * — 60% full", and a corner badge cannot say a denominator.
 */
function storageContents(s: StorageRow): Row[] {
  if (s.kind === "fluid") {
    var stored = s.stored_m3;
    if (stored === null || stored === undefined) return [["contents", "not recorded"]];
    var level = count(Math.round(stored * 10) / 10) + " m³";
    // The capacity is what turns a level into a reading, and it comes from the docs dump
    // rather than the save -- so where the dump is silent the row says the level alone
    // instead of inventing a denominator.
    if (s.capacity_m3) {
      level += " of " + count(s.capacity_m3) + " — " + Math.round((s.fill || 0) * 100) + "% full";
    }
    return [
      ["fluid", s.fluid_name || (s.fluid ? null : "empty")],
      ["level", level],
    ];
  }
  // ...and a solid container is an inventory, drawn as the same grid a crate gets out of the
  // same helper. `/api/storage` sends the whole box, so `more` arrives as 0; it still feeds the
  // grid's "+N" tile against any server that truncates.
  return contentsRows(s.items || [], s.more || 0);
}

/* One container's whole card: what it is, what is in it, and where it stands.
 *
 * The contents come FIRST, above the placement rows every other popup on this page leads with,
 * because they are the reason this layer exists -- a reader who clicks a box is asking what is
 * in it, not where it is, and where it is was answered by the click.
 */
function storagePopup(s: StorageRow): Row[] {
  var rows: Row[] = [["storage", s.name]];
  storageContents(s).forEach(function (row) {
    rows.push(row);
  });
  // The guard is the discriminator: `slots` exists on the solid row and not on the fluid one,
  // so asking without it says "a tank with no slots" rather than "a tank has no slots".
  rows.push(["slots", s.kind === "solid" && s.slots ? s.slots + " slots" : null]);
  rows.push(["footprint", s.w_m && s.l_m ? s.w_m + " x " + s.l_m + " m" : null]);
  rows.push(["facing", s.yaw === null || s.yaw === undefined ? null : Math.round(s.yaw) + "°"]);
  rows.push(["at", s.x_m + ", " + s.y_m + " m"]);
  rows.push(["instance", code(s.instance_leaf)]);
  return rows;
}

export function drawStorage(data: StorageResponse): void {
  // Off at the whole-world zoom, like the machines and the routes: 151 boxes across 7 km is a
  // scatter of specks. Last of the built band, because the row is off by default and the
  // bottom of the list is where a reader who wants it goes looking.
  var group = layer("storage", false, STORAGE_COLOUR, [BAND.built, 70, "storage"]);
  data.storage.forEach(function (s) {
    if (s.x_m === null || s.y_m === null) return;
    var colour = s.kind === "fluid" ? STORAGE_FLUID_COLOUR : STORAGE_COLOUR;
    var w = (s.w_m || STORAGE_FALLBACK_M) / 2;
    var l = (s.l_m || STORAGE_FALLBACK_M) / 2;
    var box = L.polygon(footprintCorners(s.x_m, s.y_m, w, l, s.yaw), {
      color: colour,
      weight: 1,
      fillColor: colour,
      // An unfilled container is drawn hollow, the same device the machines use for `paused`:
      // an empty box is a place with room in it.
      fillOpacity: s.kind === "fluid" ? (s.fill ? 0.7 : 0.15) : s.total ? 0.7 : 0.15,
    })
      // Wider than the page's other cards, because this one lists item names against counts
      // and a name is not broken across lines. See CONTENTS_POPUP_PX in dom.ts.
      .bindPopup(popup(storagePopup(s)), { maxWidth: CONTENTS_POPUP_PX });
    // WHERE it stands, and no instance id: `/api/floors` does not decompose storage, so no
    // band lists this box and a mark carrying an id would be a join that always misses.
    box._floor = { x_m: s.x_m, y_m: s.y_m, z_m: s.z_m === null ? undefined : s.z_m };
    box.addTo(group);
  });
  raiseNodeDots();
}

/* Static rather than live, which is a claim about the CONTAINER and not about its contents: the
 * boxes move when the player builds. What is in them changes on every autosave and is not
 * refetched until the next switch, the same bargain the machines' clock speeds make. */
registerFetch<StorageResponse>({
  wave: "static",
  rank: 60,
  path: "/api/storage",
  label: "storage",
  clears: ["storage"],
  refilters: true,
  draw: drawStorage,
});

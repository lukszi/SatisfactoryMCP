/* The crates on the ground: what a pioneer dropped, and what is still in it.
 *
 * A CRATE IS A SITUATION, NOT INFRASTRUCTURE. These actors do not exist until somebody dies
 * or dismantles something with a full inventory, and they DELETE THEMSELVES the moment they
 * are emptied -- so every crate on this map is live information by construction, which is not
 * true of a storage box. It is also not the same drawing problem as `placements.ts` next
 * door: a crate is not a buildable, the docs dump carries no clearance for a `BP_Crate_C`,
 * and a 2 m prop at world zoom is a hundredth of a pixel -- so it is a mark at a fixed pixel
 * size rather than a footprint.
 */

import { code, popup } from "../../kit/dom";
import { count } from "../../kit/format";
import { CONTENTS_POPUP_PX, contentsRows } from "./inventory-grid";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";

import type { Row } from "../../kit/dom";

/* The absences are as load-bearing as the fields: no w_m/l_m, and NO OWNER -- `mCrateType` is
 * the actor's only saved property, so a co-op world's crate cannot say whose it is. */
import type { CrateRow, CratesResponse } from "../../api/shapes";

// Spring green, far from every ground so a small glyph is found at world zoom
// (docs/frontend_palette.md).
var CRATE_COLOUR = declareColours("crates", { crates: "#3fcc94" }).crates;

/* The glyph's box, in screen PIXELS, for the reason power-wires.ts gives for its poles: the
 * question a crate mark answers is "is there one here", not "does this fit", and a true-size
 * 2 m prop is a fraction of a pixel at the world view. About the size of the floor connectors'
 * arrows, the page's other fixed glyph that has to be CLICKED rather than merely seen. */
var CRATE_PX = 13;

/* Three kinds, and the glyph tells apart the one distinction a reader scans a map for.
 *
 *   death      a filled box.   Somebody died here and their pockets are still on the ground.
 *   dismantle  a hollow box.   Overflow from dismantling with a full inventory.
 *   none       a DASHED box.   The crate predates the game's own death/dismantle property.
 *
 * The third is dashed rather than drawn as one of the other two, and that is the whole care
 * this table needs. `mCrateType` arrived in build 433351 and most crates on disk are older
 * than it, so a `none` crate MIGHT be a death -- drawing it hollow would quietly file it as
 * "not a death", which is the one fact the save withheld. A dashed outline is this page's
 * word for "this is not a reading", as it is on a machine placements.ts cannot vouch for.
 * A fourth kind a later extractor learns is drawn as `none`, for the same reason.
 */
function crateGlyph(kind: string): string {
  var death = kind === "death";
  var told = death || kind === "dismantle";
  var box = CRATE_PX;
  return (
    '<svg width="' + box + '" height="' + box + '" viewBox="0 0 ' + box + " " + box + '" ' +
    'aria-hidden="true" focusable="false">' +
    // Inset by 2 so the 1.5 px stroke has room and the glyph's outer edge is its stated size
    // rather than its stated size plus a stroke.
    '<rect x="2" y="2" width="' + (box - 4) + '" height="' + (box - 4) + '" rx="1" ' +
    'fill="' + CRATE_COLOUR + '" fill-opacity="' + (death ? 0.55 : 0) + '" ' +
    'stroke="' + CRATE_COLOUR + '" stroke-width="1.5"' +
    (told ? "" : ' stroke-dasharray="2.2 1.6"') +
    "/>" +
    // The strap, which is what makes this read as a crate rather than as one more small
    // square on a map that already has hundreds of them.
    '<path d="M2 ' + box / 2 + " H" + (box - 2) + '" stroke="' + CRATE_COLOUR + '" ' +
    'stroke-width="1.2" stroke-opacity="0.9"/>' +
    "</svg>"
  );
}

/* What the title row calls one. A crate that cannot say which kind it is is called a CRATE:
 * the save is not unreadable and the crate is not a mystery, it simply predates the property
 * that would have said, and the sentence under the title -- the server's own -- explains why
 * there is nothing more to call it. */
export function crateLabel(kind: string): string {
  if (kind === "death") return "death crate";
  if (kind === "dismantle") return "dismantle crate";
  return "crate";
}

/* One crate's whole card. Contents first, on the storage popup's terms exactly: a reader who
 * clicks a crate is asking what is in it, and where it is was answered by the click.
 *
 * NO OWNER ROW. `mCrateType` is the actor's only saved property -- no player, no timestamp,
 * no cause -- so in a co-op world nothing here can say whose death this was, and a row that
 * guessed would arrive looking exactly like a row that knew. */
function cratePopup(c: CrateRow): Row[] {
  var rows: Row[] = [[crateLabel(c.kind), c.kind_text || c.kind]];
  /* The same inventory grid a storage box gets, out of the same helper: "what is in it" is
   * one question wherever it is asked. The server sends every kind, so `more` arrives as 0 and
   * contentsRows' "+N" tile is the net under any server that truncates again. */
  contentsRows(c.items || [], c.more || 0).forEach(function (row) {
    rows.push(row);
  });
  // Only when the list did not already show all of it: repeating "4 kinds, 24 items" over a
  // card that lists four kinds is a row that says nothing.
  rows.push([
    "in all",
    c.more ? c.item_kinds + " kinds, " + count(c.total) + " items" : null,
  ]);
  rows.push(["slots", c.slots ? c.slots + " slots" : null]);
  rows.push(["at", c.x_m === null ? null : c.x_m + ", " + c.y_m + " m"]);
  rows.push(["elevation", c.z_m === null ? null : c.z_m + " m"]);
  rows.push(["id", code(c.instance_leaf)]);
  return rows;
}

export function drawCrates(data: CratesResponse): void {
  /* ON BY DEFAULT, alone among the built band's recent arrivals.
   *
   * The page's default-off layers are off because they smear at the whole-world zoom, and
   * none of that arithmetic applies here: a couple of crates per world cannot crowd anything
   * at any zoom. What decides it is the other half -- "where did I die" is asked precisely
   * once, in a hurry, and an answer behind a checkbox nobody has noticed is not an answer.
   *
   * Last of the built band, after the containers: a crate is the inventory nobody built. */
  var group = clearedLayer("crates", { on: true, colour: CRATE_COLOUR, rank: [BAND.built, 80, "crates"] });

  data.crates.forEach(function (c) {
    // A crate whose position would not read is still SENT -- the projection knows it exists.
    // Skipping it is this page's call, and the same one every drawing module here makes.
    if (c.x_m === null || c.y_m === null) return;
    L.marker(latLngOf([c.x_m, c.y_m]), {
      /* A divIcon rather than a path, for the reason floors/glyphs.ts's arrows are one: a
       * crate is a SHAPE at a fixed pixel size, and the canvas renderer this page draws paths
       * on offers a fixed-size circle and nothing else. A square drawn as a polygon would be
       * in world metres and would vanish at world zoom.
       *
       * It therefore lives in the marker pane, above the shared canvas, so a crate always
       * takes the click from whatever it is lying on -- which is right: it is the smaller and
       * rarer of any two things at one spot, and a mark that cannot be clicked is a layer
       * with no content at all. */
      icon: L.divIcon({
        className: "crate-mark",
        html: crateGlyph(c.kind),
        iconSize: [CRATE_PX, CRATE_PX],
        iconAnchor: [CRATE_PX / 2, CRATE_PX / 2],
      }),
      // What the mark says before it is clicked: a reader hovering one is finding out whether
      // it is the death one.
      title: crateLabel(c.kind),
      alt: crateLabel(c.kind),
    })
      // The wider card the storage popup takes; see CONTENTS_POPUP_PX in inventory-grid.ts.
      .bindPopup(popup(cratePopup(c)), { maxWidth: CONTENTS_POPUP_PX })
      .addTo(group);
  });
}

/* THE LIVE WAVE, and it is the wave the data picks rather than the one its neighbours use. A
 * crate is created by dying and destroyed by being emptied, and both happen between one
 * autosave and the next, so a crate layer refetched only on a world switch would be showing a
 * reader where they died two sessions ago.
 *
 * Rank 25, ahead of the summary, because the summary is the fetch that clears the dimmed map
 * and says the switch has finished -- nothing should be issued after it.
 *
 * `refilters: false`: a crate is on none of the floor filter's layers. It is not decomposed
 * by /api/floors, it has no instance a band could list, and a crate outside a factory is the
 * ordinary case -- so it stays visible in floor mode rather than being filtered to nothing. */
registerFetch<CratesResponse>({
  wave: "live",
  rank: 25,
  path: "/api/crates",
  label: "crates",
  clears: ["crates"],
  refilters: false,
  draw: drawCrates,
});

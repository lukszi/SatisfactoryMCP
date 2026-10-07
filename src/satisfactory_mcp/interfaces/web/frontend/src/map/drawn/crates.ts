/* The crates on the ground: what a pioneer dropped, and what is still in it, as marks at a
 * fixed pixel size. See frontend/README.md, "Crates". */

import { code, popup } from "../../kit/dom";
import { count } from "../../kit/format";
import { CONTENTS_POPUP_PX, contentsRows } from "./inventory-grid";
import { L } from "../leaflet";
import { BAND, clearedLayer } from "../layers";
import { latLngOf } from "../map";
import { declareColours } from "../palette";
import { registerFetch } from "../../app/registry";

import type { Row } from "../../kit/dom";
import type { CrateRow, CratesResponse } from "../../api/shapes";

// Spring green, far from every ground so a small glyph is found at world zoom
// (docs/frontend_palette.md).
const CRATE_COLOUR = declareColours("crates", { crates: "#3fcc94" }).crates;

/* The glyph's box, in screen pixels. */
const CRATE_PX = 13;

/* A death crate is a filled box, a dismantle crate a hollow one, and any other a dashed one:
 * a crate that predates the kind might be a death. */
function crateGlyph(kind: string): string {
  const death = kind === "death";
  const told = death || kind === "dismantle";
  const box = CRATE_PX;
  return (
    '<svg width="' + box + '" height="' + box + '" viewBox="0 0 ' + box + " " + box + '" ' +
    'aria-hidden="true" focusable="false">' +
    // Inset by 2 so the 1.5 px stroke stays inside the stated size.
    '<rect x="2" y="2" width="' + (box - 4) + '" height="' + (box - 4) + '" rx="1" ' +
    'fill="' + CRATE_COLOUR + '" fill-opacity="' + (death ? 0.55 : 0) + '" ' +
    'stroke="' + CRATE_COLOUR + '" stroke-width="1.5"' +
    (told ? "" : ' stroke-dasharray="2.2 1.6"') +
    "/>" +
    '<path d="M2 ' + box / 2 + " H" + (box - 2) + '" stroke="' + CRATE_COLOUR + '" ' +
    'stroke-width="1.2" stroke-opacity="0.9"/>' +
    "</svg>"
  );
}

/** What the title row calls one; a crate of no known kind is a "crate". */
export function crateLabel(kind: string): string {
  if (kind === "death") return "death crate";
  if (kind === "dismantle") return "dismantle crate";
  return "crate";
}

/* One crate's card, contents first. No owner row: the save does not say whose it is. */
function cratePopup(c: CrateRow): Row[] {
  const rows: Row[] = [[crateLabel(c.kind), c.kind_text || c.kind]];
  contentsRows(c.items || [], c.more || 0).forEach(function (row) {
    rows.push(row);
  });
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
  const group = clearedLayer("crates", { on: true, colour: CRATE_COLOUR, rank: [BAND.built, 80, "crates"] });

  data.crates.forEach(function (c) {
    if (c.x_m === null || c.y_m === null) return;
    L.marker(latLngOf([c.x_m, c.y_m]), {
      icon: L.divIcon({
        className: "crate-mark",
        html: crateGlyph(c.kind),
        iconSize: [CRATE_PX, CRATE_PX],
        iconAnchor: [CRATE_PX / 2, CRATE_PX / 2],
      }),
      title: crateLabel(c.kind),
      alt: crateLabel(c.kind),
    })
      .bindPopup(popup(cratePopup(c)), { maxWidth: CONTENTS_POPUP_PX })
      .addTo(group);
  });
}

/* The live wave, ahead of the summary that ends a switch; no floor filter. */
registerFetch<CratesResponse>({
  wave: "live",
  rank: 25,
  path: "/api/crates",
  label: "crates",
  clears: ["crates"],
  refilters: false,
  draw: drawCrates,
});

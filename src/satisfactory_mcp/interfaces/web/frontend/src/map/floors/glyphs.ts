/* What floor mode adds to the drawn pieces: the ghost a machine from a lower deck becomes, and
 * the up/down arrow on whatever leaves the floor being looked at. */

import { code, popup } from "../../kit/dom";
import { metres } from "../../kit/format";
import { L } from "../leaflet";
import { latLngOf } from "../map";
import { bandAtHeight, bandOfId, isEndOnBand } from "./model";

import type { FloorBand, FloorDeck, FloorPlatform, FloorRun } from "../../api/shapes";
import type { Point3M } from "../geometry";
import type { Row } from "../../kit/dom";
import type { FloorMark } from "../leaflet-private";

/* The arrow's box in screen pixels: its size and its click target. In pixels so it stays
 * hittable zoomed out, and never 0x0, because the arrow itself is what opens the popup. */
const GLYPH_PX = 14;

/** A deck's height as the picker and the popups print it. */
export function deckHeightText(value: number | null): string {
  if (value === null) return "height not recorded";
  return (value >= 0 ? "+" : "") + metres(value, 1);
}

export function floorName(band: { ordinal: number }): string {
  return "Floor " + band.ordinal;
}

/* Restyle a machine that comes up through this floor rather than redraw it, so no second shape
 * can end up where the machine is not. Its own style and card are parked on the path so
 * unghosting is exact; see `_floorStyle` and `_floorCard` in leaflet-private.d.ts. */
export function ghost(piece: L.Path, rows: Row[]): void {
  if (!piece._floorStyle) {
    const was = piece.options;
    piece._floorStyle = {
      color: was.color,
      weight: was.weight,
      opacity: was.opacity,
      fillOpacity: was.fillOpacity,
      dashArray: was.dashArray,
    };
    const card = piece.getPopup();
    piece._floorCard = card ? (card.getContent() as string | HTMLElement) : null;
  }
  piece.setStyle({ weight: 1, opacity: 0.6, fillOpacity: 0.06, dashArray: "3,4" });
  piece.setPopupContent(popup(rows));
}

export function unghost(piece: L.Path): void {
  const was = piece._floorStyle;
  if (!was) return;
  const card = piece._floorCard;
  delete piece._floorStyle;
  delete piece._floorCard;
  // Leaflet's setStyle ignores an absent key, so a dash is cleared by spelling it empty.
  piece.setStyle({ dashArray: "" });
  piece.setStyle(was);
  if (card !== null && card !== undefined) piece.setPopupContent(card);
}

export function ghostRows(platform: FloorPlatform, band: FloorBand, mark: FloorMark): Row[] {
  const stands = mark.id === undefined ? null : bandOfId(platform, mark.id);
  const through = (mark.z_m || 0) + (mark.h_m || 0) - (band.top_m || 0);
  return [
    ["ghost", "not on this floor: it comes up through it"],
    ["stands on", stands ? floorName(stands) + ", " + deckHeightText(stands.top_m) : null],
    ["height", mark.h_m + " m above its own deck"],
    ["through this floor", Math.round(through * 10) / 10 + " m"],
    ["id", code(mark.id)],
  ];
}

function otherEnd(run: FloorRun, platform: number, band: FloorBand): FloorDeck | null {
  let found: FloorDeck | null = null;
  run.ends.forEach(function (end) {
    if (end && !isEndOnBand(end, platform, band)) found = end;
  });
  return found;
}

/* An arrow at a point, pointing the way it goes. Shared by a run and a wire, so a reader learns
 * one glyph and the popup says which. */
function glyphMarker(at: Point3M, up: boolean): L.Marker {
  return L.marker(latLngOf(at), {
    icon: L.divIcon({
      className: "floor-connector" + (up ? " floor-connector-up" : " floor-connector-down"),
      html: up ? "&#9650;" : "&#9660;",
      iconSize: [GLYPH_PX, GLYPH_PX],
      iconAnchor: [GLYPH_PX / 2, GLYPH_PX / 2],
    }),
    title: up ? "leads up a floor" : "leads down a floor",
  });
}

function connectorName(run: FloorRun): string {
  if (run.lift) return "conveyor lift";
  return run.kind === "pipe" ? "pipe riser" : "belt riser";
}

/* The arrow a belt or pipe connector gets on every floor it touches. The popup names the far
 * end as a FLOOR, because "it goes to floor 4" is the sentence a reader is after. */
export function connectorGlyph(run: FloorRun, platform: number, band: FloorBand, at: Point3M): L.Marker {
  const away = otherEnd(run, platform, band);
  const up = !!away && (away.top_m || 0) > (band.top_m || 0);
  const marker = glyphMarker(at, up);
  marker.bindPopup(
    popup([
      [connectorName(run), up ? "goes up from this floor" : "goes down from this floor"],
      ["to", away ? floorName(away) + ", " + deckHeightText(away.top_m) : "no deck: the ground"],
      ["rise", run.rise_m === null ? null : run.rise_m + " m"],
      // A lift is a class; a riser is a run climbing six metres or more, and many lifts are
      // belt-height jogs on one deck, so the two are not the same word.
      ["kind", run.lift ? "a conveyor lift, by class" : "a run that climbs a storey or more"],
      [run.kind === "pipe" ? "pipe row" : "chain", "#" + run.key],
    ])
  );
  return marker;
}

/* The arrow a wire gets when it leaves this floor. Up/down and the far floor are judged at the
 * ANCHORS, the points the filter kept it by, so the arrow never names another storey; the drawn
 * point and the rise are the wire's own ends. A far anchor on no band is the ground. */
export function wireGlyph(
  platform: FloorPlatform,
  here: Point3M,
  away: Point3M,
  hereAnchor: Point3M,
  awayAnchor: Point3M
): L.Marker {
  const up = awayAnchor[2] > hereAnchor[2];
  const lands = bandAtHeight(platform, awayAnchor[2]);
  const marker = glyphMarker(here, up);
  marker.bindPopup(
    popup([
      ["power line", up ? "goes up from this floor" : "goes down from this floor"],
      ["to", lands ? floorName(lands) + ", " + deckHeightText(lands.top_m) : "no deck: the ground"],
      ["rise", Math.round(Math.abs(away[2] - here[2]) * 10) / 10 + " m"],
      // A wire can leave a floor sideways as well as vertically.
      ["other end", away[0] + ", " + away[1] + " m"],
      ["shape", "a straight chord; a wire sags and the save records no sag"],
    ])
  );
  return marker;
}

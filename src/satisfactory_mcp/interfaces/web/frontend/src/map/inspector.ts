/* The right-click inspector: "what is here?", answered where the question is asked.
 *
 * Its own module because it is the one thing on this page that is not a layer: it draws
 * nothing, owns no group, appears in no checkbox, and answers about a point the player picked
 * rather than about anything the save contains. /api/inspect answers the three things a site
 * starts with -- the named region, the measured elevation, the nearest nodes -- so the only
 * work here is turning a latlng back into game coordinates and laying the answer out.
 */

import { get } from "../api/client";
import { code, dataButton, esc, FIND_AT_ATTR, FIND_ATTR, html, popup, traceButtons } from "../kit/dom";
import { pickupPlace } from "../dash/world/world-finds";
import { coords, count, formatNumber, metres, perMin, regionLine } from "../kit/format";
import { L } from "./leaflet";
import { gameXY, hashFor, map, MAP_SQUARE_M, NARROW } from "./map";
import { withQuery } from "../app/nav";
import { pinButtons } from "../chat/pins";
import { settingOn } from "../app/settings";
import { friendlyError } from "../kit/toast";
import { counted, gapText, WORDS } from "../kit/words";

import type { ConduitCount, Elevation, FoundField, InspectResponse, NearPickup } from "../api/shapes";
import type { PinTarget } from "../chat/pins";
import type { Row } from "../kit/dom";
import type { InspectedEvent } from "./leaflet-private";

function elevationRows(e: Elevation): Row[] {
  // The extracted heightfield goes first when there is one, because it is the only answer
  // measured AT the point rather than near it. Which layer of the field answered rides along
  // with it: a landscape texel is a metre good and a fill texel four, so quoting one number
  // for both would be the same overclaim as one median over nodes and foundations.
  const rows: Row[] = [];
  const measured = e.terrain_m !== null && e.terrain_m !== undefined;
  if (measured) {
    const acc = e.terrain_accuracy_m === null ? "" : " ±" + e.terrain_accuracy_m + " m";
    rows.push(["terrain", e.terrain_m + " m (" + e.terrain_source + acc + ")"]);
    if (e.terrain_cave_note) rows.push(["cave", e.terrain_cave_note]);
    if (e.terrain_ambiguous) {
      const bare =
        e.terrain_bare_m === null || e.terrain_bare_m === undefined
          ? "bare ground under it not known"
          : "bare ground " + e.terrain_bare_m + " m";
      rows.push(["under rock", "may be a rock top; " + bare]);
    }
    // Water is information, never a correction: gating terrain on it makes the terrain
    // worse, so it is shown beside the ground and never instead of it. The level and the
    // DEPTH are separate claims and the depth is the weaker one -- over the fill layer there
    // is no depth to state, and the server says so instead of sending a zero.
    if (e.terrain_water_m !== null && e.terrain_water_m !== undefined) {
      const depth =
        e.terrain_water_depth_m !== null && e.terrain_water_depth_m !== undefined
          ? e.terrain_water_depth_m + " m deep"
          : e.terrain_water_note || "depth not known here";
      rows.push(["water", e.terrain_water_m + " m surface, " + depth]);
    }
  } else if (e.terrain_note) {
    // A missing terrain is printed as the REASON it is missing, exactly as a missing fill is
    // below: one of the server's two notes tells the reader to run tools/gen_world_heightmap.py
    // and the other says this coordinate is open ocean or a cave mouth. Printing nothing reads
    // as the ground being unremarkable rather than as never having been looked at.
    rows.push(["terrain", e.terrain_note]);
  }
  // Unsurveyed ground gets one line, not three saying the same nothing -- and it is still
  // owed even when the note above explains the field's silence, because the two say different
  // things: why there is no texel, and that nothing is standing here either.
  if (!e.ground_count && !e.built_count) {
    if (measured) return rows;
    return rows.concat([["elevation", "nothing known within " + e.radius_m + " m"]]);
  }
  // Ground and built stay apart, exactly as the server sends them: a node rests on
  // terrain and a foundation is wherever the player put it, so one median labelled
  // "elevation" would be the platform's height on any developed site.
  rows.push([
    "ground",
    e.ground_count
      ? e.ground_m + " m (median of " + e.ground_count + ", spread " + e.ground_spread_m + " m)"
      : "no ground samples within " + e.radius_m + " m",
  ]);
  if (e.built_count) {
    rows.push(["built", e.built_m + " m (median of " + e.built_count + ")"]);
  }
  // A missing fill is printed as the REASON it is missing, never as 0: zero fill is a
  // real and different measurement, and a blank row reads as a bug in the map.
  rows.push(["fill", e.fill_m === null ? e.fill_note : metres(e.fill_m)]);
  return rows;
}

function elevationLine(e: Elevation): string {
  if (e.terrain_m !== null && e.terrain_m !== undefined) return metres(e.terrain_m);
  if (e.ground_count) return "about " + e.ground_m + " m, from nearby ground";
  return e.terrain_note || "not known here";
}

type NearNode = InspectResponse["nearest"][number];

function nearestTag(n: NearNode): string {
  if (n.occupied) return " (occupied)";
  return n.spoiler ? " (" + WORDS.locked + ")" : "";
}

function nearestText(n: NearNode): string {
  return n.resource_name + " " + n.purity + " · " + metres(n.distance_m) + nearestTag(n);
}

const PICKUPS_NEAR_M = 500;

function onSquare(x: number, y: number): boolean {
  return x >= MAP_SQUARE_M.x_min && x <= MAP_SQUARE_M.x_max && y >= MAP_SQUARE_M.y_min && y <= MAP_SQUARE_M.y_max;
}

function shown<T extends { spoiler: boolean }>(rows: T[]): T[] {
  const all = settingOn("spoilers");
  return rows.filter(function (r) {
    return all || !r.spoiler;
  });
}

function findButton(kind: string, spot: string, text: string, title: string): string {
  const attrs: Record<string, string> = {};
  attrs[FIND_ATTR] = kind;
  attrs[FIND_AT_ATTR] = spot;
  return dataButton(attrs, text, title);
}

function actions(x: number, y: number): string {
  const spot = x + "," + y;
  return [
    findButton("point", spot, "select point", "make this point the selection"),
    findButton("nodes", spot, "nodes near here", "ring the resource nodes near this point"),
    findButton("conduits", spot, "conduits here", "draw the belts and pipes near this point"),
    findButton("pickups", spot, "pickups near here", "ring the nearest pickups"),
    '<a href="' + esc(hashFor(withQuery("world/nodes", { near: spot }))) + '">open in World</a>',
  ].join(" ");
}

function conduitLine(c: ConduitCount): string {
  return counted(c.belt, "belt") + ", " + counted(c.pipe, "pipe") + " within " + formatNumber(c.radius_m, 0) + " m";
}

function fieldText(f: FoundField): string {
  return f.resources.join(" + ") + " " + WORDS.field + " · " + counted(f.size, "node") + " · " + perMin(f.free) + " " + WORDS.free;
}

function pickupText(p: NearPickup): string {
  return p.label + (p.distance_m === null ? "" : " · " + formatNumber(p.distance_m, 0) + " m");
}

function pickupsWithin(d: InspectResponse): string | null {
  if (d.pickups_within === null) return null;
  const n = d.pickups_within - (settingOn("spoilers") ? 0 : d.pickups_within_spoilers);
  return count(n) + " " + WORDS.remaining + " within " + PICKUPS_NEAR_M + " m";
}

function inspectHtml(d: InspectResponse, machine?: { leaf: string; name: string }): string {
  const rows: Row[] = [];
  if (machine) {
    rows.push(["machine", machine.name]);
    rows.push(["trace", traceButtons(machine.leaf)]);
  }
  const nearest = d.nearest;
  const pickups = shown(d.pickups);
  const fields = d.fields;
  const targets: PinTarget[] = [{ kind: "point", ref: { x_m: d.at.x_m, y_m: d.at.y_m }, text: "point" }];
  if (machine) targets.push({ kind: "machine", ref: { machine: machine.leaf }, text: "machine" });
  const near = nearest[0];
  if (near) {
    targets.push({ kind: "node", ref: { node: near.name }, text: "node " + near.resource_name });
    targets.push({ kind: "field", ref: { node: near.name }, text: "field" });
  }
  rows.push(["region", regionLine(d.region)]);
  rows.push(["elevation", elevationLine(d.elevation)]);
  rows.push(["grid", d.grid && onSquare(d.at.x_m, d.at.y_m) ? d.grid + (d.direction ? " · " + d.direction : "") : null]);
  if (nearest.length) rows.push(["nearest", nearestText(nearest[0]!)]);
  rows.push(["conduits", d.conduits ? conduitLine(d.conduits) : null]);
  rows.push(["pickups", pickupsWithin(d)]);
  // Shown in whole metres like every other position; the click copies the full-precision
  // selector for an MCP tool call.
  rows.push(["at", html(code(d.at.x_m + "," + d.at.y_m, coords(d.at.x_m, d.at.y_m)).html)]);
  rows.push(["", html('<span class="popup-acts">' + actions(d.at.x_m, d.at.y_m) + "</span>")]);
  rows.push([WORDS.pin, pinButtons(targets)]);
  const more: Row[] = elevationRows(d.elevation);
  /* Each nearest node carries the same `node:` selector its own dot's popup prints, because
   * the answer's next step is an MCP tool call naming one of these nodes and a resource plus
   * a distance cannot say WHICH one -- a world has dozens of impure copper nodes. */
  nearest.slice(0, 5).forEach(function (n, i) {
    more.push([i ? "" : "nodes", html(esc(nearestText(n)) + "<br>" + code("node:" + n.name).html)]);
  });
  fields.slice(0, 3).forEach(function (f, i) {
    more.push([i ? "" : "fields", html(esc(fieldText(f)) + "<br>" + code(f.selector).html)]);
  });
  pickups.slice(0, 5).forEach(function (p, i) {
    more.push([i ? "" : "pickups", html(esc(pickupText(p)) + "<br>" + code(pickupPlace(p)).html)]);
  });
  d.stale.forEach(function (t) {
    if (!t.behind && !t.moved && !t.unjoinable) return;
    more.push(["map data", t.notes.length ? t.notes.join(" ") : WORDS.mapDataBehind + (t.gap ? " (" + gapText(t.gap) + ")" : "")]);
  });
  // Said out loud rather than left to be inferred: with no save there is no built
  // population and no occupancy, so every node above reads as free whether it is or not.
  if (d.save_error) more.push(["save", d.save_error + "; nodes only, occupancy unknown"]);
  return popup(rows) + '<details class="popup-more"><summary>details</summary>' + popup(more) + "</details>";
}

function clearOfControls(): L.Point {
  const gap = 8;
  if (!NARROW.matches) return L.point(gap, gap);
  const frame = map.getContainer().getBoundingClientRect();
  let below = gap;
  map.getContainer().querySelectorAll(".leaflet-top.leaflet-right > *").forEach(function (control) {
    below = Math.max(below, control.getBoundingClientRect().bottom - frame.top + gap);
  });
  return L.point(gap, below);
}

/** The right-click handler, named rather than registered here: main.ts wires every map
 *  listener in one block, because Leaflet fires them in registration order. */
export function inspect(e: L.LeafletMouseEvent): void {
  // One right-click can reach this twice -- Leaflet fires at the layer under the cursor and
  // the event propagates to the map -- so the DOM event carries a mark.
  const dom = e.originalEvent as InspectedEvent | undefined;
  if (dom) {
    if (dom._inspected) return;
    dom._inspected = true;
  }
  const machine = dom ? dom._machine : undefined;
  // Rounded to a decimetre because the popup prints the same numbers it asked with, and a
  // coordinate you cannot retype is not a copyable coordinate.
  const at = gameXY(e.latlng);
  const x = Math.round(at[0] * 10) / 10;
  const y = Math.round(at[1] * 10) / 10;
  // Opened before the fetch, so the click has a visible effect on a slow answer and the
  // popup lands exactly where the pointer was rather than where the map has drifted to.
  const card = L.popup({ maxWidth: 340, autoPanPaddingTopLeft: clearOfControls() })
    .setLatLng(e.latlng)
    .setContent("inspecting " + x + ", " + y + " m&hellip;")
    .openOn(map);
  get<InspectResponse>(("/api/inspect?x_m=" + x + "&y_m=" + y) as `/api/inspect?${string}`)
    .then(function (d) {
      if (!map.hasLayer(card)) return;
      const body = document.createElement("div");
      body.innerHTML = inspectHtml(d, machine);
      body.querySelector("details")!.addEventListener("toggle", function () {
        card.update();
      });
      card.setContent(body);
    })
    .catch(function (err) {
      if (map.hasLayer(card)) card.setContent(popup([["inspect failed", friendlyError(err)]]));
    });
}

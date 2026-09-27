/* The right-click inspector: "what is here?", answered where the question is asked.
 *
 * Its own module because it is the one thing on this page that is not a layer: it draws
 * nothing, owns no group, appears in no checkbox, and answers about a point the player picked
 * rather than about anything the save contains. /api/inspect answers the three things a site
 * starts with -- the named region, the measured elevation, the nearest nodes -- so the only
 * work here is turning a latlng back into game coordinates and laying the answer out.
 */

import { get } from "./api";
import { code, esc, html, popup, traceButtons } from "./dom";
import { regionLine } from "./format";
import { L } from "./leaflet";
import { map } from "./map";
import { pinButtons } from "./pins";
import { friendly } from "./toast";
import { W } from "./words";

import type { Elevation, InspectResponse } from "./api-shapes";
import type { PinTarget } from "./pins";
import type { Row } from "./dom";
import type { InspectedEvent } from "./leaflet-private";

function elevationRows(e: Elevation): Row[] {
  // The extracted heightfield goes first when there is one, because it is the only answer
  // measured AT the point rather than near it. Which layer of the field answered rides along
  // with it: a landscape texel is a metre good and a fill texel four, so quoting one number
  // for both would be the same overclaim as one median over nodes and foundations.
  var rows: Row[] = [];
  var measured = e.terrain_m !== null && e.terrain_m !== undefined;
  if (measured) {
    var acc = e.terrain_accuracy_m === null ? "" : " ±" + e.terrain_accuracy_m + " m";
    rows.push(["terrain", e.terrain_m + " m (" + e.terrain_source + acc + ")"]);
    // Water is information, never a correction: gating terrain on it makes the terrain
    // worse, so it is shown beside the ground and never instead of it. The level and the
    // DEPTH are separate claims and the depth is the weaker one -- over the fill layer there
    // is no depth to state, and the server says so instead of sending a zero.
    if (e.terrain_water_m !== null && e.terrain_water_m !== undefined) {
      var depth =
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
  rows.push(["fill", e.fill_m === null ? e.fill_note : e.fill_m + " m"]);
  return rows;
}

function elevationLine(e: Elevation): string {
  if (e.terrain_m !== null && e.terrain_m !== undefined) return e.terrain_m + " m";
  if (e.ground_count) return "about " + e.ground_m + " m, from nearby ground";
  return e.terrain_note || "not known here";
}

function nearestText(n: InspectResponse["nearest"][number]): string {
  return n.resource_name + " " + n.purity + " · " + n.distance_m + " m" + (n.occupied ? " (occupied)" : "");
}

function inspectHtml(d: InspectResponse, machine?: { leaf: string; name: string }): string {
  var rows: Row[] = [];
  if (machine) {
    rows.push(["machine", machine.name]);
    rows.push(["trace", traceButtons(machine.leaf)]);
  }
  var targets: PinTarget[] = [{ kind: "point", ref: { x_m: d.at.x_m, y_m: d.at.y_m }, text: "point" }];
  if (machine) targets.push({ kind: "machine", ref: { machine: machine.leaf }, text: "machine" });
  var near = d.nearest[0];
  if (near) {
    targets.push({ kind: "node", ref: { node: near.name }, text: "node " + near.resource_name });
    targets.push({ kind: "field", ref: { node: near.name }, text: "field" });
  }
  rows.push(["region", regionLine(d.region)]);
  rows.push(["elevation", elevationLine(d.elevation)]);
  if (d.nearest.length) rows.push(["nearest", nearestText(d.nearest[0]!)]);
  // The one row built to be copied into an MCP tool call, so the unit -- the same " m"
  // every other coordinate row on the map ends with -- must ride along.
  rows.push(["at", html("<code>" + esc(d.at.x_m + ", " + d.at.y_m) + "</code> m")]);
  rows.push([W.pin, pinButtons(targets)]);
  var more: Row[] = elevationRows(d.elevation);
  /* Each nearest node carries the same `node:` selector its own dot's popup prints, because
   * the answer's next step is an MCP tool call naming one of these nodes and a resource plus
   * a distance cannot say WHICH one -- a world has dozens of impure copper nodes. */
  d.nearest.forEach(function (n, i) {
    more.push([i ? "" : "nodes", html(esc(nearestText(n)) + "<br>" + code("node:" + n.name).html)]);
  });
  // Said out loud rather than left to be inferred: with no save there is no built
  // population and no occupancy, so every node above reads as free whether it is or not.
  if (d.save_error) more.push(["save", d.save_error + "; nodes only, occupancy unknown"]);
  return popup(rows) + '<details class="popup-more"><summary>details</summary>' + popup(more) + "</details>";
}

/** The right-click handler, named rather than registered here: main.ts wires every map
 *  listener in one block, because Leaflet fires them in registration order. */
export function inspect(e: L.LeafletMouseEvent): void {
  // One right-click can reach this twice -- Leaflet fires at the layer under the cursor and
  // the event propagates to the map -- so the DOM event carries a mark.
  var dom = e.originalEvent as InspectedEvent | undefined;
  if (dom) {
    if (dom._inspected) return;
    dom._inspected = true;
  }
  var machine = dom ? dom._machine : undefined;
  // The inverse of the page's one coordinate rule: a point plotted at [-y, x] reads back as
  // x = lng, y = -lat. Rounded to a decimetre because the popup prints the same numbers it
  // asked with, and a coordinate you cannot retype is not a copyable coordinate.
  var x = Math.round(e.latlng.lng * 10) / 10;
  var y = Math.round(-e.latlng.lat * 10) / 10;
  // Opened before the fetch, so the click has a visible effect on a slow answer and the
  // popup lands exactly where the pointer was rather than where the map has drifted to.
  var card = L.popup({ maxWidth: 340 })
    .setLatLng(e.latlng)
    .setContent("inspecting " + x + ", " + y + " m&hellip;")
    .openOn(map);
  get<InspectResponse>(("/api/inspect?x_m=" + x + "&y_m=" + y) as `/api/inspect?${string}`)
    .then(function (d) {
      if (!map.hasLayer(card)) return;
      var body = document.createElement("div");
      body.innerHTML = inspectHtml(d, machine);
      body.querySelector("details")!.addEventListener("toggle", function () {
        card.update();
      });
      card.setContent(body);
    })
    .catch(function (err) {
      if (map.hasLayer(card)) card.setContent(popup([["inspect failed", friendly(err)]]));
    });
}

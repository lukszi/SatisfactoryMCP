/* Where a stored plan is to STAND: the pad it claims, drawn on the ground it claims it from.
 *
 * The only layer on this page that draws something that is not there. Everything else is a
 * reading of the save; a siting is the player's own statement, made through `site_plan`,
 * about a rectangle nobody has poured yet -- so it is drawn as an outline over the terrain
 * and the concrete rather than as a filled area that would hide the very things a reader
 * opened it to check: does the pad clear the belt, does it fit between the cliffs.
 *
 * The rectangle is the same one `siting.contains_cm` decides membership of on the server:
 * both halves rotate a centred WxD box by one yaw, and `footprintCorners` is the forward
 * direction of the transpose that function applies. They must not drift apart.
 */

import { code, popup } from "./dom";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { footprintCorners } from "./map";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";

import type { Row } from "./dom";
import type { PlanSiting, PlansResponse } from "./api-shapes";

/* Green, measured against every colour already declared on this page. Its nearest neighbour
 * anywhere is the uranium node dot at dE 25.0, then the mushroom pickup at 38.6 and the crate
 * green at 43.8 -- all small marks on open ground, none of them a rectangle a hundred metres
 * across. Against the things this outline is actually laid over it is far clear: dE 105.5
 * from the concrete it will replace, 110.7 from the machine blue standing on it, and 69.9
 * from the nearest biome ground.
 *
 * Green because the page spends none of it on anything built: blue, amber, tan, blue-violet,
 * slate and rust are all placements or networks, so an outline in a hue no built thing wears
 * cannot be mistaken for one. */
var PLAN_COLOUR = declareColours("plans", { plans: "#4ec22e" }).plans;

/* One pad's card. `source` is the row that decides whether the outline is worth trusting to
 * the metre: a footprint the player measured, against the square `plan_layout` budgeted from
 * the machine count. Said in words rather than passed through, because "layout" alone on a
 * card reads as a category and not as a caveat. */
function planPopup(p: PlanSiting): Row[] {
  var measured = p.source === "given";
  return [
    ["plan", p.name],
    ["footprint", p.width_m + " x " + p.depth_m + " m"],
    ["from", measured ? "measured" : "estimated from the machine count"],
    // Degrees about world Z, positive turning +X towards +Y: the same convention the machine
    // rectangles are drawn with, so a pad and the machines on it turn the same way.
    ["facing", Math.round(p.yaw_deg) + "°"],
    ["at", p.x_m + ", " + p.y_m + " m"],
    ["elevation", p.z_m === null ? null : p.z_m + " m"],
    // What the origin RESOLVED from when it was recorded -- "you", a factory name, or the
    // typed pair. A pad sited from a factory centroid moves meaning if that factory is
    // renamed, and this is the only place that says where the number came from.
    ["origin", p.origin_label || null],
    ["factory", p.factory || null],
    ["selector", code("plan:" + p.name)],
  ];
}

export function drawPlans(data: PlansResponse): void {
  /* CHROME, not the built band: a pad is an annotation about the world, like a factory label
   * and the player dot, and filing it under what the player built would make the legend claim
   * it is standing there. Last of that band, under the labels it belongs beside.
   *
   * On by default, and cheap to be: a world has a handful of sitings or none, so the row is
   * empty and silent until the evening somebody sites a plan -- which is the one evening this
   * layer is worth anything at all. */
  var group = layer("plan sitings", true, PLAN_COLOUR, [BAND.chrome, 50, "plan sitings"]);
  data.plans.forEach(function (p) {
    L.polygon(footprintCorners(p.x_m, p.y_m, p.width_m / 2, p.depth_m / 2, p.yaw_deg), {
      color: PLAN_COLOUR,
      weight: 2,
      // Unfilled on purpose. The interior is the ground being judged, and `fill: false` also
      // means the outline takes no click from the machines and foundations inside it -- only
      // its own edge is a pointer target.
      fill: false,
      // This page's word for "not a reading": the pad is a proposal, and the crate glyphs and
      // the paused machines already spell it this way.
      dashArray: "8,5",
    })
      .bindPopup(popup(planPopup(p)))
      .addTo(group);
  });
}

/* Static, because a siting changes when the player runs `site_plan` and never when the game
 * writes a save. The `notes` SSE event is what refetches it between world switches -- see
 * sse.ts, which names this path beside /api/factories for exactly that reason.
 *
 * `refilters: false`: a pad has no storey. It is a rectangle on the ground the factory would
 * stand on, so floor mode must leave it alone rather than filter it to nothing. */
registerFetch<PlansResponse>({
  wave: "static",
  rank: 80,
  path: "/api/plans",
  label: "plans",
  clears: ["plan sitings"],
  refilters: false,
  draw: drawPlans,
});

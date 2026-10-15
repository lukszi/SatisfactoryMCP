/* The site card's feedback lines: what the pad meets at the spot the newest preview measured.
 * docs/planner-p5_contract.md §4.2 says what each field means. */

import { loading } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count } from "../../kit/format";
import { outsideMap } from "./pad-drag";
import { counted } from "../../kit/words";

import type { SitePreviewResponse } from "../../api/shapes";
import type { Pad } from "./pad-drag";

export function wholeNumber(n: number): string {
  return Math.round(n).toLocaleString("en-GB");
}

export function feedbackLine(parent: HTMLElement, text: string, tone?: string): void {
  if (text) parent.appendChild(make("p", "site-line" + (tone ? " " + tone : ""), text));
}

function nodeDistanceLine(p: Pad, nodes: SitePreviewResponse["nodes"]): string {
  if (!nodes?.length) return "";
  const byDistance = nodes.map(function (node) {
    return { node: node, distanceM: Math.hypot(node.x_m - p.x_m, node.y_m - p.y_m) };
  });
  byDistance.sort(function (a, b) {
    return a.distanceM - b.distanceM;
  });
  const nearest = byDistance[0]!;
  const farthest = byDistance[byDistance.length - 1]!;
  return "nearest " + nearest.node.resource + " node " + wholeNumber(nearest.distanceM) + " m · farthest " + wholeNumber(farthest.distanceM) + " m";
}

// Only the first preview of a pad carries its nodes; later ones are measured against those.
let firstNodes: SitePreviewResponse["nodes"] = null;

function terrainLines(feedback: HTMLElement, t: NonNullable<SitePreviewResponse["terrain"]>): void {
  feedbackLine(feedback, "ground " + t.z_min_m + "…" + t.z_max_m + " m · slope " + (t.slope_mean_deg || 0) + "° (p90 " + (t.slope_p90_deg || 0) + "°) · rough " + (t.roughness_m || 0) + " m · " + (t.submerged_pct < 1 ? t.submerged_pct : Math.round(t.submerged_pct)) + " % under water");
  if (t.water_m === 0) feedbackLine(feedback, "water on the pad");
  else if (t.water_m !== null) feedbackLine(feedback, "water " + wholeNumber(t.water_m) + " m" + (t.water_below_m !== null ? ", " + wholeNumber(t.water_below_m) + " m below" : ""));
  if (t.cave_pct) feedbackLine(feedback, "a cave lies under " + Math.max(1, Math.round(t.cave_pct)) + " % of the pad: heights are the surface");
}

function trunksLine(feedback: HTMLElement, trunks: SitePreviewResponse["trunks"]): void {
  if (!trunks.length) return;
  let run = 0;
  let leg = 0;
  let pumps = 0;
  trunks.forEach(function (trunk) {
    run += trunk.run_m;
    leg += trunk.to_site_m;
    if (trunk.pumps) pumps++;
  });
  feedbackLine(feedback, counted(trunks.length, "trunk") + " · " + wholeNumber(run) + " m node to node + " + wholeNumber(leg) + " m to the pad · " + (pumps ? pumps + " need pumps" : "no pumps"));
}

/** Fills `feedback` from `data`, dimmed when it measured another pad than `p`. */
export function paintFeedback(feedback: HTMLElement, data: SitePreviewResponse | null, p: Pad | null): void {
  feedback.textContent = "";
  if (!data || !p) {
    loading(feedback, "the spot");
    return;
  }
  if (data.nodes) firstNodes = data.nodes;
  const stale = data.x_m !== p.x_m || data.y_m !== p.y_m || data.yaw_deg !== p.yaw_deg || data.w_m !== p.width_m || data.d_m !== p.depth_m;
  feedback.classList.toggle("plan-stale", stale);
  if (outsideMap(p)) {
    feedbackLine(feedback, "outside the map: a drop here is refused", "site-refused");
    return;
  }
  feedbackLine(feedback, data.region || "off any named region");
  if (!data.in_content) feedbackLine(feedback, "off the playable ground", "dash-muted");
  feedbackLine(feedback, data.z_m !== null ? "ground height " + wholeNumber(data.z_m) + " m" : data.z_note, data.z_m === null ? "dash-muted" : "");
  const terrain = data.terrain;
  if (!terrain) feedbackLine(feedback, data.terrain_note, "dash-muted");
  else if (terrain.z_min_m === null) feedbackLine(feedback, "no terrain data under the pad", "dash-muted");
  else terrainLines(feedback, terrain);
  feedbackLine(feedback, data.slabs.length ? "on " + data.slabs.join("; ") : "bare ground");
  if (data.failure) {
    feedbackLine(feedback, data.failure, "dash-muted");
    return;
  }
  feedbackLine(feedback, counted(data.on_pad, "machine") + " " + (data.on_pad === 1 ? "stands" : "stand") + " on the pad (plan: " + count(data.planned) + ")");
  feedbackLine(feedback, nodeDistanceLine(p, firstNodes));
  trunksLine(feedback, data.trunks);
  const built = data.built;
  feedbackLine(feedback, "here: " + built.where + " · " + built.figure, "site-built");
  if (data.sited) feedbackLine(feedback, "now: " + data.now.where + " · " + data.now.figure, "dash-muted");
  feedbackLine(feedback, data.basis, "dash-muted");
  if (built.stage_text) feedbackLine(feedback, "here " + built.stage_text);
  if (data.overlaps.length) feedbackLine(feedback, "overlaps the pad of " + data.overlaps.map(function (name) { return "“" + name + "”"; }).join(", "), "site-warn");
}

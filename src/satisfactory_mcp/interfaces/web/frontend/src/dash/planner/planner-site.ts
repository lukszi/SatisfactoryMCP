/* The planner's site tab: the map beside a card, the throttled preview while a pad moves, and
 * the one `site` op a drop pushes. docs/planner-p5_contract.md §3-§7 is the specification. */

import { get } from "../../api/client";
import { button, error, loading } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { count } from "../../kit/format";
import { L } from "../../map/leaflet";
import { map } from "../../map/map";
import { stageHeadroom } from "./planner-reads";
import { bench, changed } from "./planner-state";
import { applyOps } from "./planner-writes";
import { biomassQuery } from "../power-ledger";
import { onSetting, settingChoice } from "../../app/settings";
import {
  crossCancel,
  crossDrop,
  crosshairActive,
  crossTurn,
  normaliseYaw,
  outsideMap,
  padCorners,
  padGestureActive,
  samePad,
  setPad,
  showGhostPad,
  showNodeLines,
  startCrosshair,
  startPadEdit,
  stopPadEdit,
  useSnap,
  yawStep,
} from "./pad-drag";
import { state } from "../../app/state";
import { friendlyError } from "../../kit/toast";
import { counted, WORDS } from "../../kit/words";

import type { ApiUrl } from "../../api/client";
import type { SitePreviewResponse } from "../../api/shapes";
import type { Pad } from "./pad-drag";

var PREVIEW = "/api/plan/site-preview";
var GAP_MIN_MS = 120;
var GAP_MAX_MS = 400;
var GAP_PER_RTT = 3;
var COARSE = window.matchMedia("(pointer: coarse)");

interface Confirm {
  pad: Pad;
  text: string;
}

interface Ghost {
  key: string;
  pad: Pad;
  who: string;
}

var site = {
  key: "",
  rev: 0,
  stored: null as Pad | null,
  start: null as Pad | null,
  source: "",
  pad: null as Pad | null,
  lastPreview: null as SitePreviewResponse | null,
  error: "",
  note: "",
  confirm: null as Confirm | null,
  ghost: null as Ghost | null,
  sized: false,
  label: "map",
  framed: false,
};

var card = make("section", "dash-card site-card");
var feedback = make("div", "site-lines");
var inflight = false;
var pending: Pad | null = null;
var sentAt = 0;
var rtt = 40;
var pumpTimer = 0;

useSnap(function () {
  return settingChoice("siteSnap") || "fine";
});
var snapWas = settingChoice("siteSnap");
onSetting(function () {
  if (settingChoice("siteSnap") === snapWas) return;
  snapWas = settingChoice("siteSnap");
  if (siteTabShowing()) paint();
});

function padOf(raw: unknown): Pad | null {
  var siting = raw as { origin_m?: (number | null)[]; yaw_deg?: number; footprint_m?: number[] } | null;
  if (!siting || !siting.origin_m || siting.origin_m[0] === null || siting.origin_m[1] === null) return null;
  var footprint = siting.footprint_m || [0, 0];
  return {
    x_m: Number(siting.origin_m[0]),
    y_m: Number(siting.origin_m[1]),
    yaw_deg: Number(siting.yaw_deg || 0),
    width_m: Number(footprint[0] || 0),
    depth_m: Number(footprint[1] || 0),
  };
}

function footprintSource(raw: unknown): string {
  return String((raw as { footprint_source?: string }).footprint_source || "");
}

function wholeNumber(n: number): string {
  return Math.round(n).toLocaleString("en-GB");
}

function previewUrl(p: Pad | null, extra: string): ApiUrl {
  var query = "key=" + encodeURIComponent(site.key) + "&rev=" + site.rev + "&" + biomassQuery() + "&headroom=" + stageHeadroom();
  if (p) query += "&x_m=" + p.x_m + "&y_m=" + p.y_m + "&yaw_deg=" + p.yaw_deg + "&w_m=" + p.width_m + "&d_m=" + p.depth_m;
  return (PREVIEW + "?" + query + extra) as ApiUrl;
}

function fetchPreview(p: Pad | null, extra: string): Promise<SitePreviewResponse> {
  var sent = performance.now();
  return get<SitePreviewResponse>(previewUrl(p, extra)).then(function (data) {
    rtt = performance.now() - sent;
    return data;
  });
}

/* One preview in flight, the newest pad waiting, and a gap that grows with the round trip so a
 * slow server is not flooded while a pad is dragged. */
function pumpPreviews(): void {
  clearTimeout(pumpTimer);
  if (inflight || !pending) return;
  var gap = Math.min(GAP_MAX_MS, Math.max(GAP_MIN_MS, GAP_PER_RTT * rtt));
  var wait = sentAt + gap - performance.now();
  if (wait > 0) {
    pumpTimer = window.setTimeout(pumpPreviews, wait);
    return;
  }
  var p = pending;
  var key = site.key;
  pending = null;
  inflight = true;
  sentAt = performance.now();
  fetchPreview(p, "")
    .then(function (data) {
      if (key === site.key) {
        site.lastPreview = data;
        site.error = "";
        paintLines();
      }
    })
    .catch(function (reason) {
      if (key === site.key) site.error = friendlyError(reason);
    })
    .then(function () {
      inflight = false;
      pumpPreviews();
    });
}

function requestPreview(p: Pad): void {
  pending = p;
  pumpPreviews();
}

function movePad(p: Pad, preview: boolean): void {
  site.pad = p;
  setPad(p);
  if (preview) requestPreview(p);
}

function sitingValue(p: Pad): Record<string, unknown> {
  return {
    schema: 1,
    origin_m: [p.x_m, p.y_m, null],
    yaw_deg: p.yaw_deg,
    footprint_m: [p.width_m, p.depth_m],
    footprint_source: site.sized ? "given" : site.source || "layout",
    origin_label: site.label,
    when: "",
  };
}

/** Puts the pad back where the plan has it, or where it started. */
function revertPad(): void {
  site.confirm = null;
  site.label = "map";
  site.sized = false;
  var back = site.stored || site.start || site.pad;
  if (back) movePad(back, true);
  paint();
}

function saveSite(p: Pad): void {
  site.confirm = null;
  applyOps([{ op: "site", value: sitingValue(p) }]);
  site.label = "map";
  site.sized = false;
  paint();
}

/** A pad put down: refused off the map, held for a confirm when it would lose built machines. */
function commitPad(p: Pad, how: string): void {
  site.pad = p;
  site.note = "";
  if (site.stored && samePad(p, site.stored)) {
    paint();
    return;
  }
  if (outsideMap(p)) {
    site.note = "outside the map: the drop is refused";
    revertPad();
    return;
  }
  var key = site.key;
  fetchPreview(p, "&full=1")
    .then(function (data) {
      if (key !== site.key || !samePad(site.pad, p)) return;
      site.lastPreview = data;
      if (data.loses && how !== "fit") {
        site.confirm = { pad: p, text: data.loses.text };
        paint();
        return;
      }
      saveSite(p);
    })
    .catch(function (reason) {
      site.error = friendlyError(reason);
      paint();
    });
}

var dragHooks = {
  step: function (p: Pad) {
    site.pad = p;
    site.note = "";
    paintHead();
    paintFields();
    requestPreview(p);
  },
  commit: commitPad,
  cancel: function () {
    revertPad();
  },
};

/** Fits the pad, and chat's ghost when it has one, into the part of the map the card leaves. */
function framePad(p: Pad): void {
  if ((map as unknown as { _animatingZoom?: boolean })._animatingZoom) {
    map.once("zoomend", function () {
      framePad(p);
    });
    return;
  }
  var bounds = L.latLngBounds(padCorners(p)).pad(1.2);
  if (site.ghost && site.ghost.key === site.key) bounds.extend(L.latLngBounds(padCorners(site.ghost.pad)));
  var dash = document.getElementById("dash")!.getBoundingClientRect();
  var box = map.getContainer().getBoundingClientRect();
  var side = dash.left > box.left + 4;
  map.fitBounds(bounds, {
    paddingTopLeft: [16, 16],
    paddingBottomRight: side ? [box.right - dash.left + 16, 16] : [16, box.bottom - dash.top + 16],
    maxZoom: 1,
    animate: false,
  });
}

function beginSiting(): void {
  var key = site.key;
  var plan = bench.plan!;
  site.stored = padOf(plan.siting);
  site.source = plan.siting ? footprintSource(plan.siting) : "";
  site.error = "";
  fetchPreview(site.stored, "&first=1")
    .then(function (data) {
      if (key !== site.key) return;
      site.lastPreview = data;
      if (!site.stored) site.source = data.source;
      site.start = { x_m: data.x_m, y_m: data.y_m, yaw_deg: data.yaw_deg, width_m: data.w_m, depth_m: data.d_m };
      var p = site.stored || site.start;
      site.pad = p;
      startPadEdit(p, plan.name, dragHooks, COARSE.matches);
      showNodeLines(data.nodes || []);
      if (site.ghost && site.ghost.key === key) showGhostPad(site.ghost.pad);
      if (!site.framed) {
        site.framed = true;
        framePad(p);
      }
      paint();
    })
    .catch(function (reason) {
      if (key !== site.key) return;
      site.error = friendlyError(reason);
      paint();
    });
}

/* A new head moves the pad to what was stored, unless a gesture or a confirm holds it. */
function syncWithPlan(): void {
  var plan = bench.plan;
  if (!plan) return;
  if (site.key !== bench.key) {
    site.key = bench.key;
    site.rev = plan.rev;
    site.framed = false;
    site.confirm = null;
    site.note = "";
    site.lastPreview = null;
    beginSiting();
    return;
  }
  if (site.rev !== plan.rev && !padGestureActive() && !site.confirm) {
    site.rev = plan.rev;
    if (site.ghost && site.ghost.key === site.key && plan.siting && samePad(padOf(plan.siting), site.ghost.pad)) dropGhost();
    var stored = padOf(plan.siting);
    site.stored = stored;
    if (stored) site.source = footprintSource(plan.siting);
    var back = stored || site.start;
    if (back) movePad(back, true);
  }
}

/* ---------------------------------------------------------------- the card */

function feedbackLine(parent: HTMLElement, text: string, tone?: string): void {
  if (text) parent.appendChild(make("p", "site-line" + (tone ? " " + tone : ""), text));
}

function nodeDistanceLine(p: Pad, nodes: SitePreviewResponse["nodes"]): string {
  if (!nodes || !nodes.length) return "";
  var byDistance = nodes.map(function (node) {
    return { node: node, distanceM: Math.hypot(node.x_m - p.x_m, node.y_m - p.y_m) };
  });
  byDistance.sort(function (a, b) {
    return a.distanceM - b.distanceM;
  });
  var nearest = byDistance[0]!;
  var farthest = byDistance[byDistance.length - 1]!;
  return "nearest " + nearest.node.resource + " node " + wholeNumber(nearest.distanceM) + " m · farthest " + wholeNumber(farthest.distanceM) + " m";
}

// Only the first preview of a pad carries its nodes; later ones are measured against those.
var firstNodes: SitePreviewResponse["nodes"] = null;

function terrainLines(t: NonNullable<SitePreviewResponse["terrain"]>): void {
  feedbackLine(feedback, "ground " + t.z_min_m + "…" + t.z_max_m + " m · slope " + (t.slope_mean_deg || 0) + "° (p90 " + (t.slope_p90_deg || 0) + "°) · rough " + (t.roughness_m || 0) + " m · " + (t.submerged_pct < 1 ? t.submerged_pct : Math.round(t.submerged_pct)) + " % under water");
  if (t.water_m === 0) feedbackLine(feedback, "water on the pad");
  else if (t.water_m !== null) feedbackLine(feedback, "water " + wholeNumber(t.water_m) + " m" + (t.water_below_m !== null ? ", " + wholeNumber(t.water_below_m) + " m below" : ""));
  if (t.cave_pct) feedbackLine(feedback, "a cave lies under " + Math.max(1, Math.round(t.cave_pct)) + " % of the pad: heights are the surface");
}

function trunksLine(trunks: SitePreviewResponse["trunks"]): void {
  if (!trunks.length) return;
  var run = 0;
  var leg = 0;
  var pumps = 0;
  trunks.forEach(function (trunk) {
    run += trunk.run_m;
    leg += trunk.to_site_m;
    if (trunk.pumps) pumps++;
  });
  feedbackLine(feedback, counted(trunks.length, "trunk") + " · " + wholeNumber(run) + " m node to node + " + wholeNumber(leg) + " m to the pad · " + (pumps ? pumps + " need pumps" : "no pumps"));
}

function paintLines(): void {
  var data = site.lastPreview;
  var p = site.pad;
  feedback.textContent = "";
  if (!data || !p) {
    loading(feedback, "the spot");
    return;
  }
  if (data.nodes) firstNodes = data.nodes;
  var stale = data.x_m !== p.x_m || data.y_m !== p.y_m || data.yaw_deg !== p.yaw_deg || data.w_m !== p.width_m || data.d_m !== p.depth_m;
  feedback.classList.toggle("plan-stale", stale);
  if (outsideMap(p)) {
    feedbackLine(feedback, "outside the map: a drop here is refused", "site-refused");
    return;
  }
  feedbackLine(feedback, data.region || "off any named region");
  if (!data.in_content) feedbackLine(feedback, "off the playable ground", "dash-muted");
  feedbackLine(feedback, data.z_m !== null ? "ground height " + wholeNumber(data.z_m) + " m" : data.z_note, data.z_m === null ? "dash-muted" : "");
  var terrain = data.terrain;
  if (!terrain) feedbackLine(feedback, data.terrain_note, "dash-muted");
  else if (terrain.z_min_m === null) feedbackLine(feedback, "no terrain data under the pad", "dash-muted");
  else terrainLines(terrain);
  feedbackLine(feedback, data.slabs.length ? "on " + data.slabs.join("; ") : "bare ground");
  if (data.failure) {
    feedbackLine(feedback, data.failure, "dash-muted");
    return;
  }
  feedbackLine(feedback, counted(data.on_pad, "machine") + " " + (data.on_pad === 1 ? "stands" : "stand") + " on the pad (plan: " + count(data.planned) + ")");
  feedbackLine(feedback, nodeDistanceLine(p, firstNodes));
  trunksLine(data.trunks);
  var built = data.built;
  feedbackLine(feedback, "here: " + built.where + " · " + built.figure, "site-built");
  if (data.sited) feedbackLine(feedback, "now: " + data.now.where + " · " + data.now.figure, "dash-muted");
  feedbackLine(feedback, data.basis, "dash-muted");
  if (built.stage_text) feedbackLine(feedback, "here " + built.stage_text);
  if (data.overlaps.length) feedbackLine(feedback, "overlaps the pad of " + data.overlaps.map(function (name) { return "“" + name + "”"; }).join(", "), "site-warn");
}

function numberField(label: string, ctl: string, val: number, apply: (n: number) => void): HTMLElement {
  var wrap = make("label", "site-field");
  wrap.appendChild(make("span", "site-field-k", label));
  var input = make("input", "dash-number");
  input.type = "number";
  input.step = "any";
  input.value = String(Math.round(val * 100) / 100);
  input.setAttribute("data-ctl", ctl);
  input.onchange = function () {
    var n = Number(input.value);
    if (input.value.trim() === "" || !isFinite(n)) {
      input.value = String(val);
      return;
    }
    apply(n);
  };
  input.onkeydown = function (event) {
    if (event.key === "Enter") input.blur();
  };
  wrap.appendChild(input);
  return wrap;
}

var fields = make("div", "site-fields");

function applyTypedEdit(change: (p: Pad) => Pad): void {
  if (!site.pad || padGestureActive()) return;
  var next = change({ ...site.pad });
  movePad(next, true);
  commitPad(next, "typed");
}

function paintFields(): void {
  var p = site.pad;
  if (!p) return;
  var active = document.activeElement;
  if (active && fields.contains(active)) return;
  fields.textContent = "";
  fields.appendChild(numberField("x, m", "site-x", p.x_m, function (n) { applyTypedEdit(function (q) { q.x_m = n; return q; }); }));
  fields.appendChild(numberField("y, m", "site-y", p.y_m, function (n) { applyTypedEdit(function (q) { q.y_m = n; return q; }); }));
  fields.appendChild(numberField("yaw, °", "site-yaw", p.yaw_deg, function (n) { applyTypedEdit(function (q) { q.yaw_deg = normaliseYaw(n); return q; }); }));
  fields.appendChild(numberField("W, m", "site-w", p.width_m, function (n) { site.sized = true; applyTypedEdit(function (q) { q.width_m = n; return q; }); }));
  fields.appendChild(numberField("D, m", "site-d", p.depth_m, function (n) { site.sized = true; applyTypedEdit(function (q) { q.depth_m = n; return q; }); }));
}

function actions(parent: HTMLElement): void {
  var row = make("div", "site-actions");
  if (crosshairActive()) {
    var deg = yawStep() + "°";
    row.appendChild(button("⟲ " + deg, function () { crossTurn(-1); }, { label: "turn the pad " + deg + " anticlockwise" }));
    row.appendChild(button("⟳ " + deg, function () { crossTurn(1); }, { label: "turn the pad " + deg + " clockwise" }));
    var drop = button(WORDS.dropHere, function () { crossDrop(); paint(); });
    drop.classList.add("site-drop");
    drop.setAttribute("data-ctl", "site-drop");
    row.appendChild(drop);
    row.appendChild(button("cancel", function () { crossCancel(); paint(); }));
  } else {
    var move = button(COARSE.matches ? "move" : WORDS.moveByPanning, function () { startCrosshair(); paint(); }, { title: "pan the map under a fixed pad, then drop it" });
    move.setAttribute("data-ctl", "site-cross");
    move.disabled = bench.gone || !site.pad;
    row.appendChild(move);
    var fits = site.lastPreview && site.lastPreview.fits ? site.lastPreview.fits : [];
    fits.forEach(function (fit) {
      row.appendChild(button(WORDS.fitPad(fit.name), function () {
        var p = padOf(fit.value)!;
        site.label = fit.value.origin_label;
        site.sized = true;
        movePad(p, false);
        commitPad(p, "fit");
      }, { title: "centre the pad on what is built there, " + fit.machines + " machines, and size it to cover them" }));
    });
  }
  parent.appendChild(row);
}

function confirmLine(parent: HTMLElement): void {
  var asked = site.confirm;
  if (asked === null) return;
  var held = asked;
  var row = make("div", "site-confirm");
  row.setAttribute("role", "alert");
  row.appendChild(make("span", "", asked.text));
  var anyway = button("move anyway", function () { saveSite(held.pad); });
  anyway.setAttribute("data-ctl", "site-anyway");
  row.appendChild(anyway);
  row.appendChild(button("cancel", function () { revertPad(); }));
  parent.appendChild(row);
}

function ghostLine(parent: HTMLElement): void {
  var seen = site.ghost;
  if (seen === null || seen.key !== site.key) return;
  var ghost = seen;
  var row = make("div", "site-ghost-line");
  row.appendChild(make("span", "", ghost.who + " is looking at " + wholeNumber(ghost.pad.x_m) + ", " + wholeNumber(ghost.pad.y_m)));
  var use = button("use it", function () {
    site.label = "chat preview";
    site.sized = !site.stored || ghost.pad.width_m !== site.stored.width_m || ghost.pad.depth_m !== site.stored.depth_m;
    var p = ghost.pad;
    movePad(p, false);
    dropGhost();
    commitPad(p, "chat");
  }, { title: "move the pad there: one version, Ctrl+Z undoes it" });
  use.setAttribute("data-ctl", "site-use");
  row.appendChild(use);
  row.appendChild(button("dismiss", function () { dropGhost(); paint(); }));
  parent.appendChild(row);
}

var headline = make("span", "dash-sub");

function paintHead(): void {
  var p = site.pad;
  headline.textContent = p ? wholeNumber(p.x_m) + ", " + wholeNumber(p.y_m) + " · " + wholeNumber(p.yaw_deg) + "° · " + wholeNumber(p.width_m) + " × " + wholeNumber(p.depth_m) + " m" + (site.stored ? "" : " · not placed yet") : "";
}

function paint(): void {
  card.textContent = "";
  var head = make("div", "dash-title");
  head.appendChild(make("h2", "dash-h", WORDS.site));
  paintHead();
  head.appendChild(headline);
  card.appendChild(head);
  if (site.error) error(card, "the spot", site.error);
  if (site.note) feedbackLine(card, site.note, "site-refused");
  confirmLine(card);
  ghostLine(card);
  paintFields();
  card.appendChild(fields);
  actions(card);
  card.appendChild(feedback);
  paintLines();
  var deg = yawStep() + "°";
  var hint = COARSE.matches ? "move, pan the map under the pad, then " + WORDS.dropHere : "drag the square to move, the circle to turn; Shift moves freely; arrows nudge 8 m, Shift+arrows 1 m, [ and ] turn " + deg + ", Shift+[ and ] 15° freely; Esc puts it back";
  card.appendChild(make("p", "dash-note", hint + " · snap: " + (settingChoice("siteSnap") === "grid8" ? "8 m world grid and " + deg : "1 m and " + deg) + " (Settings)"));
}

/* ---------------------------------------------------------------- the split */

function siteTabShowing(): boolean {
  return document.body.classList.contains("dash-on") && state.dash.indexOf("planner/") === 0 && /\/site$/.test(state.dash) && bench.tab === "site";
}

export function syncSplit(): void {
  var on = siteTabShowing();
  if (document.body.classList.contains("site-on") === on) return;
  document.body.classList.toggle("site-on", on);
  if (!on) {
    stopPadEdit();
    showGhostPad(null);
    site.key = "";
    site.lastPreview = null;
  }
}

/** The site tab's body; the map beside it is the page's own map. */
export function renderSite(parent: HTMLElement): void {
  if (!bench.plan) return;
  document.body.classList.add("site-on");
  syncWithPlan();
  paint();
  parent.appendChild(card);
}

/* ---------------------------------------------------------------- chat's ghost */

function dropGhost(): void {
  site.ghost = null;
  showGhostPad(null);
}

/** A chat `site_plan(preview=True)`: a ghost pad with [use it] on that plan's site tab. */
export function showGhost(key: string, args: Record<string, unknown>, who: string): void {
  var arg = function (name: string): number {
    return Number(args[name]);
  };
  var p: Pad = { x_m: arg("x_m"), y_m: arg("y_m"), yaw_deg: arg("yaw_deg") || 0, width_m: arg("w_m"), depth_m: arg("d_m") };
  if (![p.x_m, p.y_m, p.width_m, p.depth_m].every(isFinite)) return;
  site.ghost = { key: key, pad: p, who: who };
  if (site.key === key) {
    showGhostPad(p);
    if (!padGestureActive()) framePad(site.pad || p);
  }
  changed();
}

window.addEventListener("hashchange", function () {
  setTimeout(syncSplit, 0);
});
new MutationObserver(syncSplit).observe(document.body, { attributes: true, attributeFilter: ["class"] });

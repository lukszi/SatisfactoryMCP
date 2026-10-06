/* The planner's site tab: the map beside a card, the throttled preview while a pad moves, and
 * the one `site` op a drop pushes. docs/planner-p5_contract.md §3-§7 is the specification. */

import { get } from "../../api/client";
import { button, error, loading } from "../../kit/dashkit";
import { count, make } from "../../kit/dom";
import { L } from "../../map/leaflet";
import { map } from "../../map/map";
import { bench, changed, gesture, stageHeadroom } from "./planner-core";
import { biomassQuery } from "../power-ledger";
import { choice, onSetting } from "../../app/settings";
import { busy, corners, crossCancel, crossDrop, crossing, crossOn, crossTurn, edit, ghost, nodes, outsideMap, same, setPad, stop, useSnap, yawStep } from "./pad-drag";
import { state } from "../../app/state";
import { friendly } from "../../kit/toast";
import { counted, W } from "../../kit/words";

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
  last: null as SitePreviewResponse | null,
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
  return choice("siteSnap") || "fine";
});
var snapWas = choice("siteSnap");
onSetting(function () {
  if (choice("siteSnap") === snapWas) return;
  snapWas = choice("siteSnap");
  if (showing()) paint();
});

function padOf(raw: unknown): Pad | null {
  var s = raw as { origin_m?: (number | null)[]; yaw_deg?: number; footprint_m?: number[] } | null;
  if (!s || !s.origin_m || s.origin_m[0] === null || s.origin_m[1] === null) return null;
  var fp = s.footprint_m || [0, 0];
  return { x: Number(s.origin_m[0]), y: Number(s.origin_m[1]), yaw: Number(s.yaw_deg || 0), w: Number(fp[0] || 0), d: Number(fp[1] || 0) };
}

function m(n: number): string {
  return Math.round(n).toLocaleString("en-GB");
}

function query(p: Pad | null, extra: string): ApiUrl {
  var q = "key=" + encodeURIComponent(site.key) + "&rev=" + site.rev + "&" + biomassQuery() + "&headroom=" + stageHeadroom();
  if (p) q += "&x_m=" + p.x + "&y_m=" + p.y + "&yaw_deg=" + p.yaw + "&w_m=" + p.w + "&d_m=" + p.d;
  return (PREVIEW + "?" + q + extra) as ApiUrl;
}

function fetchPreview(p: Pad | null, extra: string): Promise<SitePreviewResponse> {
  var t = performance.now();
  return get<SitePreviewResponse>(query(p, extra)).then(function (data) {
    rtt = performance.now() - t;
    return data;
  });
}

function pump(): void {
  clearTimeout(pumpTimer);
  if (inflight || !pending) return;
  var gap = Math.min(GAP_MAX_MS, Math.max(GAP_MIN_MS, GAP_PER_RTT * rtt));
  var wait = sentAt + gap - performance.now();
  if (wait > 0) {
    pumpTimer = window.setTimeout(pump, wait);
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
        site.last = data;
        site.error = "";
        paintLines();
      }
    })
    .catch(function (reason) {
      if (key === site.key) site.error = friendly(reason);
    })
    .then(function () {
      inflight = false;
      pump();
    });
}

function ask(p: Pad): void {
  pending = p;
  pump();
}

function value(p: Pad): Record<string, unknown> {
  return {
    schema: 1,
    origin_m: [p.x, p.y, null],
    yaw_deg: p.yaw,
    footprint_m: [p.w, p.d],
    footprint_source: site.sized ? "given" : site.source || "layout",
    origin_label: site.label,
    when: "",
  };
}

function restore(): void {
  site.confirm = null;
  site.label = "map";
  site.sized = false;
  var back = site.stored || site.start || site.pad;
  if (back) {
    site.pad = back;
    setPad(back);
    ask(back);
  }
  paint();
}

function push(p: Pad): void {
  site.confirm = null;
  gesture([{ op: "site", value: value(p) }]);
  site.label = "map";
  site.sized = false;
  paint();
}

function commit(p: Pad, how: string): void {
  site.pad = p;
  site.note = "";
  if (site.stored && same(p, site.stored)) {
    paint();
    return;
  }
  if (outsideMap(p)) {
    site.note = "outside the map: the drop is refused";
    var refused = site.note;
    restore();
    site.note = refused;
    paint();
    return;
  }
  var key = site.key;
  fetchPreview(p, "&full=1")
    .then(function (data) {
      if (key !== site.key || !same(site.pad, p)) return;
      site.last = data;
      if (data.loses && how !== "fit") {
        site.confirm = { pad: p, text: data.loses.text };
        paint();
        return;
      }
      push(p);
    })
    .catch(function (reason) {
      site.error = friendly(reason);
      paint();
    });
}

var hooks = {
  step: function (p: Pad) {
    site.pad = p;
    site.note = "";
    paintHead();
    paintFields();
    ask(p);
  },
  commit: commit,
  cancel: function () {
    restore();
  },
};

function frame(p: Pad): void {
  if ((map as unknown as { _animatingZoom?: boolean })._animatingZoom) {
    map.once("zoomend", function () {
      frame(p);
    });
    return;
  }
  var bounds = L.latLngBounds(corners(p)).pad(1.2);
  if (site.ghost && site.ghost.key === site.key) bounds.extend(L.latLngBounds(corners(site.ghost.pad)));
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

function start(): void {
  var key = site.key;
  var plan = bench.plan!;
  site.stored = padOf(plan.siting);
  site.source = plan.siting ? String((plan.siting as { footprint_source?: string }).footprint_source || "") : "";
  site.error = "";
  fetchPreview(site.stored, "&first=1")
    .then(function (data) {
      if (key !== site.key) return;
      site.last = data;
      if (!site.stored) site.source = data.source;
      site.start = { x: data.x_m, y: data.y_m, yaw: data.yaw_deg, w: data.w_m, d: data.d_m };
      var p = site.stored || site.start;
      site.pad = p;
      edit(p, plan.name, hooks, COARSE.matches);
      nodes(data.nodes || []);
      if (site.ghost && site.ghost.key === key) ghost(site.ghost.pad);
      if (!site.framed) {
        site.framed = true;
        frame(p);
      }
      paint();
    })
    .catch(function (reason) {
      if (key !== site.key) return;
      site.error = friendly(reason);
      paint();
    });
}

function sync(): void {
  var plan = bench.plan;
  if (!plan) return;
  if (site.key !== bench.key) {
    site.key = bench.key;
    site.rev = plan.rev;
    site.framed = false;
    site.confirm = null;
    site.note = "";
    site.last = null;
    start();
    return;
  }
  if (site.rev !== plan.rev && !busy() && !site.confirm) {
    site.rev = plan.rev;
    if (site.ghost && site.ghost.key === site.key && plan.siting && same(padOf(plan.siting), site.ghost.pad)) dropGhost();
    var stored = padOf(plan.siting);
    site.stored = stored;
    if (stored) site.source = String((plan.siting as { footprint_source?: string }).footprint_source || "");
    var back = stored || site.start;
    if (back) {
      site.pad = back;
      setPad(back);
      ask(back);
    }
  }
}

/* ---------------------------------------------------------------- the card */

function line(parent: HTMLElement, text: string, tone?: string): void {
  if (text) parent.appendChild(make("p", "site-line" + (tone ? " " + tone : ""), text));
}

function nearest(p: Pad, data: SitePreviewResponse | null): string {
  var list = (data && data.nodes) || (site.last && site.last.nodes) || null;
  if (!list || !list.length) return "";
  var d = list.map(function (n) {
    return { n: n, m: Math.hypot(n.x_m - p.x, n.y_m - p.y) };
  });
  d.sort(function (a, b) {
    return a.m - b.m;
  });
  return "nearest " + d[0]!.n.resource + " node " + m(d[0]!.m) + " m · farthest " + m(d[d.length - 1]!.m) + " m";
}

var firstNodes: SitePreviewResponse["nodes"] = null;

function paintLines(): void {
  var data = site.last;
  var p = site.pad;
  feedback.textContent = "";
  if (!data || !p) {
    loading(feedback, "the spot");
    return;
  }
  if (data.nodes) firstNodes = data.nodes;
  var stale = data.x_m !== p.x || data.y_m !== p.y || data.yaw_deg !== p.yaw || data.w_m !== p.w || data.d_m !== p.d;
  feedback.classList.toggle("plan-stale", stale);
  if (outsideMap(p)) {
    line(feedback, "outside the map: a drop here is refused", "site-refused");
    return;
  }
  line(feedback, data.region || "off any named region");
  if (!data.in_content) line(feedback, "off the playable ground", "dash-muted");
  line(feedback, data.z_m !== null ? "ground height " + m(data.z_m) + " m" : data.z_note, data.z_m === null ? "dash-muted" : "");
  var t = data.terrain;
  if (!t) line(feedback, data.terrain_note, "dash-muted");
  else if (t.z_min_m === null) line(feedback, "no terrain data under the pad", "dash-muted");
  else {
    line(feedback, "ground " + t.z_min_m + "…" + t.z_max_m + " m · slope " + (t.slope_mean_deg || 0) + "° (p90 " + (t.slope_p90_deg || 0) + "°) · rough " + (t.roughness_m || 0) + " m · " + (t.submerged_pct < 1 ? t.submerged_pct : Math.round(t.submerged_pct)) + " % under water");
    if (t.water_m === 0) line(feedback, "water on the pad");
    else if (t.water_m !== null) line(feedback, "water " + m(t.water_m) + " m" + (t.water_below_m !== null ? ", " + m(t.water_below_m) + " m below" : ""));
    if (t.cave_pct) line(feedback, "a cave lies under " + Math.max(1, Math.round(t.cave_pct)) + " % of the pad: heights are the surface");
  }
  line(feedback, data.slabs.length ? "on " + data.slabs.join("; ") : "bare ground");
  if (data.failure) {
    line(feedback, data.failure, "dash-muted");
    return;
  }
  line(feedback, counted(data.on_pad, "machine") + " " + (data.on_pad === 1 ? "stands" : "stand") + " on the pad (plan: " + count(data.planned) + ")");
  line(feedback, nearest(p, { nodes: firstNodes } as SitePreviewResponse));
  if (data.trunks.length) {
    var run = 0;
    var leg = 0;
    var pumps = 0;
    data.trunks.forEach(function (tr) {
      run += tr.run_m;
      leg += tr.to_site_m;
      if (tr.pumps) pumps++;
    });
    line(feedback, counted(data.trunks.length, "trunk") + " · " + m(run) + " m node to node + " + m(leg) + " m to the pad · " + (pumps ? pumps + " need pumps" : "no pumps"));
  }
  var b = data.built;
  line(feedback, "here: " + b.where + " · " + b.figure, "site-built");
  if (data.sited) line(feedback, "now: " + data.now.where + " · " + data.now.figure, "dash-muted");
  line(feedback, data.basis, "dash-muted");
  if (b.stage_text) line(feedback, "here " + b.stage_text);
  if (data.overlaps.length) line(feedback, "overlaps the pad of " + data.overlaps.map(function (n) { return "“" + n + "”"; }).join(", "), "site-warn");
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

function typed(change: (p: Pad) => Pad): void {
  if (!site.pad || busy()) return;
  var next = change({ x: site.pad.x, y: site.pad.y, yaw: site.pad.yaw, w: site.pad.w, d: site.pad.d });
  site.pad = next;
  setPad(next);
  ask(next);
  commit(next, "typed");
}

function paintFields(): void {
  var p = site.pad;
  if (!p) return;
  var active = document.activeElement;
  if (active && fields.contains(active)) return;
  fields.textContent = "";
  fields.appendChild(numberField("x, m", "site-x", p.x, function (n) { typed(function (q) { q.x = n; return q; }); }));
  fields.appendChild(numberField("y, m", "site-y", p.y, function (n) { typed(function (q) { q.y = n; return q; }); }));
  fields.appendChild(numberField("yaw, °", "site-yaw", p.yaw, function (n) { typed(function (q) { q.yaw = ((n % 360) + 360) % 360; return q; }); }));
  fields.appendChild(numberField("W, m", "site-w", p.w, function (n) { site.sized = true; typed(function (q) { q.w = n; return q; }); }));
  fields.appendChild(numberField("D, m", "site-d", p.d, function (n) { site.sized = true; typed(function (q) { q.d = n; return q; }); }));
}

function actions(parent: HTMLElement): void {
  var row = make("div", "site-actions");
  if (crossing()) {
    var deg = yawStep() + "°";
    row.appendChild(button("⟲ " + deg, function () { crossTurn(-1); }, { label: "turn the pad " + deg + " anticlockwise" }));
    row.appendChild(button("⟳ " + deg, function () { crossTurn(1); }, { label: "turn the pad " + deg + " clockwise" }));
    var drop = button(W.dropHere, function () { crossDrop(); paint(); });
    drop.classList.add("site-drop");
    drop.setAttribute("data-ctl", "site-drop");
    row.appendChild(drop);
    row.appendChild(button("cancel", function () { crossCancel(); paint(); }));
  } else {
    var move = button(COARSE.matches ? "move" : W.moveByPanning, function () { crossOn(); paint(); }, { title: "pan the map under a fixed pad, then drop it" });
    move.setAttribute("data-ctl", "site-cross");
    move.disabled = bench.gone || !site.pad;
    row.appendChild(move);
    var fits = site.last && site.last.fits ? site.last.fits : [];
    fits.forEach(function (f) {
      row.appendChild(button(W.fitPad(f.name), function () {
        var p = padOf(f.value)!;
        site.label = f.value.origin_label;
        site.sized = true;
        site.pad = p;
        setPad(p);
        commit(p, "fit");
      }, { title: "centre the pad on what is built there, " + f.machines + " machines, and size it to cover them" }));
    });
  }
  parent.appendChild(row);
}

function confirmLine(parent: HTMLElement): void {
  var c = site.confirm;
  if (c === null) return;
  var held = c;
  var row = make("div", "site-confirm");
  row.setAttribute("role", "alert");
  row.appendChild(make("span", "", c.text));
  var go = button("move anyway", function () { push(held.pad); });
  go.setAttribute("data-ctl", "site-anyway");
  row.appendChild(go);
  row.appendChild(button("cancel", function () { restore(); }));
  parent.appendChild(row);
}

function ghostLine(parent: HTMLElement): void {
  var seen = site.ghost;
  if (seen === null || seen.key !== site.key) return;
  var g = seen;
  var row = make("div", "site-ghost-line");
  row.appendChild(make("span", "", g.who + " is looking at " + m(g.pad.x) + ", " + m(g.pad.y)));
  var use = button("use it", function () {
    site.label = "chat preview";
    site.sized = !site.stored || g.pad.w !== site.stored.w || g.pad.d !== site.stored.d;
    var p = g.pad;
    site.pad = p;
    setPad(p);
    dropGhost();
    commit(p, "chat");
  }, { title: "move the pad there: one version, Ctrl+Z undoes it" });
  use.setAttribute("data-ctl", "site-use");
  row.appendChild(use);
  row.appendChild(button("dismiss", function () { dropGhost(); paint(); }));
  parent.appendChild(row);
}

var headline = make("span", "dash-sub");

function paintHead(): void {
  var p = site.pad;
  headline.textContent = p ? m(p.x) + ", " + m(p.y) + " · " + m(p.yaw) + "° · " + m(p.w) + " × " + m(p.d) + " m" + (site.stored ? "" : " · not placed yet") : "";
}

function paint(): void {
  card.textContent = "";
  var head = make("div", "dash-title");
  head.appendChild(make("h2", "dash-h", W.site));
  paintHead();
  head.appendChild(headline);
  card.appendChild(head);
  if (site.error) error(card, "the spot", site.error);
  if (site.note) line(card, site.note, "site-refused");
  confirmLine(card);
  ghostLine(card);
  paintFields();
  card.appendChild(fields);
  actions(card);
  card.appendChild(feedback);
  paintLines();
  var deg = yawStep() + "°";
  var hint = COARSE.matches ? "move, pan the map under the pad, then " + W.dropHere : "drag the square to move, the circle to turn; Shift moves freely; arrows nudge 8 m, Shift+arrows 1 m, [ and ] turn " + deg + ", Shift+[ and ] 15° freely; Esc puts it back";
  card.appendChild(make("p", "dash-note", hint + " · snap: " + (choice("siteSnap") === "grid8" ? "8 m world grid and " + deg : "1 m and " + deg) + " (Settings)"));
}

/* ---------------------------------------------------------------- the split */

function showing(): boolean {
  return document.body.classList.contains("dash-on") && state.dash.indexOf("planner/") === 0 && /\/site$/.test(state.dash) && bench.tab === "site";
}

export function syncSplit(): void {
  var on = showing();
  if (document.body.classList.contains("site-on") === on) return;
  document.body.classList.toggle("site-on", on);
  if (!on) {
    stop();
    ghost(null);
    site.key = "";
    site.last = null;
  }
}

/** The site tab's body; the map beside it is the page's own map. */
export function renderSite(parent: HTMLElement): void {
  if (!bench.plan) return;
  document.body.classList.add("site-on");
  sync();
  paint();
  parent.appendChild(card);
}

/* ---------------------------------------------------------------- chat's ghost */

function dropGhost(): void {
  site.ghost = null;
  ghost(null);
}

/** A chat `site_plan(preview=True)`: a ghost pad with [use it] on that plan's site tab. */
export function showGhost(key: string, args: Record<string, unknown>, who: string): void {
  var n = function (k: string): number {
    return Number(args[k]);
  };
  var p = { x: n("x_m"), y: n("y_m"), yaw: n("yaw_deg") || 0, w: n("w_m"), d: n("d_m") };
  if (![p.x, p.y, p.w, p.d].every(isFinite)) return;
  site.ghost = { key: key, pad: p, who: who };
  if (site.key === key) {
    ghost(p);
    if (!busy()) frame(site.pad || p);
  }
  changed();
}

window.addEventListener("hashchange", function () {
  setTimeout(syncSplit, 0);
});
new MutationObserver(syncSplit).observe(document.body, { attributes: true, attributeFilter: ["class"] });

/* The site tab: a small real map (map/mini-map.ts) of the pad, the nodes it draws from and the
 * straight trunk lines to it, beside the pad's facts and its trunks, all from one
 * /api/plan/site-preview read of the stored pad. Moving the pad is the place view, a link. */

import { get } from "../../../api/client";
import { goToMapThen } from "../../../app/nav";
import { count, formatNumber } from "../../../kit/format";
import { html, trusted, type Safe } from "../../../kit/html";
import { entry, list } from "../../../kit/ui/entry";
import { chip } from "../../../kit/ui/marks";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine } from "../../../kit/ui/text";
import { friendlyError } from "../../../kit/toast";
import { L } from "../../../map/leaflet";
import { nodeColour, PURITY_RADIUS } from "../../../map/drawn/markers";
import { footprintCorners, hashFor, latLngOf } from "../../../map/map";
import { showBox } from "../../../map/map-highlight";
import { miniMap, type MiniMap } from "../../../map/mini-map";
import { biomassQuery } from "../../power/readings";
import { stageHeadroom } from "../reads";
import { planDash } from "../routes";
import { padOf } from "../site";
import { bench, changed, itemClass } from "../state";
import { itemIcon, lookupOf } from "./parts";

import type { ApiUrl } from "../../../api/client";
import type { SitePreviewResponse } from "../../../api/shapes";
import type { BboxM } from "../../../map/geometry";
import type { Pad } from "../pad-drag";

const FIT_MAX_ZOOM = 1;

const read = { id: "", data: null as SitePreviewResponse | null, error: "" };
let mini: MiniMap | null = null;
let drawn = "";
const marks = L.featureGroup();

function ink(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function storedPad(): Pad | null {
  return bench.plan ? padOf(bench.plan.siting) : null;
}

function load(p: Pad): void {
  const plan = bench.plan!;
  const id = bench.key + ":" + plan.rev;
  if (read.id === id) return;
  Object.assign(read, { id, data: null, error: "" });
  const url = "/api/plan/site-preview?key=" + encodeURIComponent(bench.key) + "&rev=" + plan.rev + "&" + biomassQuery() + "&headroom=" + stageHeadroom() + "&x_m=" + p.x_m + "&y_m=" + p.y_m + "&yaw_deg=" + p.yaw_deg + "&w_m=" + p.width_m + "&d_m=" + p.depth_m + "&first=1";
  get<SitePreviewResponse>(url as ApiUrl)
    .then(function (data) {
      if (read.id !== id) return;
      read.data = data;
      changed();
    })
    .catch(function (reason) {
      if (read.id !== id) return;
      read.error = friendlyError(reason);
      changed();
    });
}

function where(p: Pad, d: SitePreviewResponse): string {
  const label = String((bench.plan!.siting as { origin_label?: string } | null)?.origin_label || "");
  const place = label || "x " + formatNumber(p.x_m, 0) + ", y " + formatNumber(p.y_m, 0);
  return d.region ? place + " · " + d.region : place;
}

function facts(p: Pad, d: SitePreviewResponse): Safe {
  const t = d.terrain;
  const row = (k: string, v: string | null) => html`<span class="k">${k}</span>${v === null ? html`<em class="na">–</em>` : html`<b>${v}</b>`}`;
  const measured = (d.source || "") === "given" ? "measured" : "estimate";
  const slope = t && t.slope_mean_deg !== null ? formatNumber(t.slope_mean_deg, 1) + "° mean · " + formatNumber(t.slope_p90_deg || 0, 1) + "° p90" : null;
  const over = d.overlaps.length ? html`<div class="nc"><span class="k">overlaps</span>${d.overlaps.map((n) => chip("“" + n + "”", "mid"))}</div>` : "";
  return html`<div class="card"><div class="kvs">${row("where", where(p, d))}${row("size", formatNumber(p.width_m, 0) + " × " + formatNumber(p.depth_m, 0) + " m · " + measured)}${row("facing", formatNumber(p.yaw_deg, 0) + "°")}${row("height", d.z_m === null ? null : formatNumber(d.z_m, 0) + " m")}${row("slope", slope)}${row("under water", t ? formatNumber(t.submerged_pct, 0) + "%" : null)}${row("on the pad", count(d.on_pad) + " of " + count(d.planned) + " machines")}</div>${over}<p class="pad-acts"><a href="${hashFor(planDash(bench.key, "place"))}">move the pad on the map</a></p></div>`;
}

function trunks(d: SitePreviewResponse): Safe {
  const look = lookupOf(bench.result);
  const rows = d.trunks.map((t) =>
    entry(
      { title: t.name },
      html`${itemIcon(look, t.name)}<div class="id"><span class="name">${t.name}</span><div class="ctx">${count(t.members)} ${t.members === 1 ? "node" : "nodes"} · ${t.carrier}</div></div><div class="metric"><b>${formatNumber(t.run_m, 0)}</b><span>m run</span></div><div class="metric"><b>${formatNumber(t.to_site_m, 0)}</b><span>m to pad</span></div>`
    )
  );
  return html`${dashH("trunks", rows.length)}${rows.length ? list("ptrunk", "stacked", rows) : emptyLine("no trunks", "no node of its inputs near the pad")}`;
}

const GLYPHS = {
  pad: '<rect class="lg-pad" x="1.5" y="3" width="9" height="6"/>',
  pure: '<circle class="lg-n" cx="6" cy="6" r="5"/>',
  normal: '<circle class="lg-n" cx="6" cy="6" r="3.8"/>',
  impure: '<circle class="lg-n" cx="6" cy="6" r="2.5"/>',
  trunk: '<line class="lg-t" x1="1" y1="11" x2="11" y2="1"/>',
};

function legend(): Safe {
  return html`${(Object.keys(GLYPHS) as (keyof typeof GLYPHS)[]).map((k) => html`<span class="lgi"><svg class="lg" viewBox="0 0 12 12" aria-hidden="true">${trusted(GLYPHS[k])}</svg>${k}</span>`)}`;
}

export function siteTab(): Safe {
  const p = storedPad();
  if (!p) return html`${dashH("site")}${emptyLine("no site")} <a href="${hashFor(planDash(bench.key, "place"))}">place the pad on the map</a>`;
  load(p);
  const d = read.data;
  const side = read.error ? emptyLine("the pad could not be read", read.error) : !d ? loadingLine("the pad") : html`${dashH("pad")}${facts(p, d)}${trunks(d)}`;
  return html`<div class="psite"><section class="wmap psite-map" aria-label="map of the pad"><div class="wmap-slot"></div><div class="wmap-leg">${legend()}</div></section><div class="psite-side">${side}</div></div>`;
}

function draw(m: L.Map, p: Pad, d: SitePreviewResponse | null): void {
  marks.clearLayers();
  const pts: L.LatLngTuple[] = footprintCorners(p.x_m, p.y_m, p.width_m / 2, p.depth_m / 2, p.yaw_deg);
  const centre = latLngOf([p.x_m, p.y_m]);
  const cool = ink("--ink-cool", "#cfd8e0");
  (d?.nodes || []).forEach(function (n) {
    const at = latLngOf([n.x_m, n.y_m]);
    L.polyline([at, centre], { color: cool, weight: 2, opacity: 0.7, interactive: false }).addTo(marks);
    const colour = nodeColour(itemClass(n.resource) || "");
    L.circleMarker(at, { radius: PURITY_RADIUS[n.purity] || 4, color: colour, weight: 1, fillColor: colour, fillOpacity: 0.85 })
      .bindTooltip(n.resource + " · " + n.purity, { direction: "top", className: "wmap-tip" })
      .addTo(marks);
    pts.push(at);
  });
  L.polygon(footprintCorners(p.x_m, p.y_m, p.width_m / 2, p.depth_m / 2, p.yaw_deg), { color: ink("--ink-hi", "#fff"), weight: 2, fillColor: ink("--ink-soft", "#b8bec6"), fillOpacity: 0.16 })
    .bindTooltip("pad · " + formatNumber(p.width_m, 0) + " × " + formatNumber(p.depth_m, 0) + " m", { direction: "top", className: "wmap-tip" })
    .addTo(marks);
  m.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: FIT_MAX_ZOOM, animate: false });
}

/** Puts the live map into its slot and draws the pad when it or the read changed. */
export function mountSite(root: HTMLElement): void {
  const slot = root.querySelector<HTMLElement>(".psite .wmap-slot");
  const p = storedPad();
  if (!slot || !p) return;
  if (!mini) {
    mini = miniMap("wmap-live");
    marks.addTo(mini.map);
  }
  slot.appendChild(mini.el);
  mini.map.invalidateSize({ animate: false });
  mini.sync();
  const key = read.id + "|" + JSON.stringify(p) + "|" + (read.data ? "1" : "0");
  if (key === drawn) return;
  drawn = key;
  draw(mini.map, p, read.data);
}

/** The header's ◎: the main map at the pad. */
export function showPad(): void {
  const p = storedPad();
  if (!p) return;
  const corners = footprintCorners(p.x_m, p.y_m, p.width_m / 2, p.depth_m / 2, p.yaw_deg);
  const xs = corners.map((c) => c[1]);
  const ys = corners.map((c) => -c[0]);
  const box: BboxM = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  goToMapThen(() => showBox(box, { layers: ["machines"] }));
}

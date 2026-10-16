/* The flows aspect: the production graph over the whole width, the balance per item under it.
 * A floor shown elsewhere is only noted: filtering the graph would cut edges mid-chain. */

import { perMin } from "../../kit/format";
import { cls, html, type Safe } from "../../kit/html";
import { group, list } from "../../kit/ui/entry";
import { chip } from "../../kit/ui/marks";
import { dashH, emptyLine, icon, na, note } from "../../kit/ui/text";
import { pending, q, read } from "./data";
import { aspectHref, setPick, type FCtx } from "./ctx";
import { graphElement, viewOf } from "./graph";

import type { FactoryGraphResponse } from "../../api/shapes";
import type { ProdNode } from "../prodgraph/model";

const VERDICTS = ["needs feeding", "surplus", "internal"];

function unitOf(c: FCtx, item: string): string {
  const edge = c.graph ? c.graph.edges.filter((e) => e.item_name === item)[0] : undefined;
  return edge && edge.unit === "m3" ? "m³/min" : "/min";
}

function iconOf(c: FCtx, item: string): string | null {
  if (!c.graph) return null;
  const edge = c.graph.edges.filter((e) => e.item_name === item && e.item_id)[0];
  return edge ? edge.item_id : null;
}

function signed(v: number): string {
  return (v > 0 ? "+" : v < 0 ? "−" : "") + perMin(Math.abs(v), false);
}

function balance(c: FCtx): Safe {
  const data = c.aspects.data;
  if (!data) return pending(c.aspects, "the balance", c.aspectsPath) || html``;
  if (!data.balance.length) return emptyLine("no item flows", "nothing here has a recipe set, or every machine is paused");
  const num = (v: number, k: string) => html`<div class="metric">${v ? html`<b>${perMin(v, false)}</b>` : na()}<span>${k}</span></div>`;
  const inner = VERDICTS.map((v) => {
    const rows = data.balance.filter((r) => r.verdict === v).sort((a, b) => Math.abs(b.net) - Math.abs(a.net) || a.item.localeCompare(b.item));
    if (!rows.length) return html``;
    return html`${group(v, rows.length)}${rows.map((r) => html`<div class="entry" data-item="${r.item}" tabindex="0">${icon(iconOf(c, r.item), r.item)}<div class="id"><span class="name">${r.item}</span></div>${num(r.made, "made")}${num(r.used, "used")}<div class="${cls("metric", v === "needs feeding" && "in")}"><b>${signed(r.net)}</b><span>${unitOf(c, r.item)}</span></div></div>`)}`;
  });
  return html`${dashH("balance per item", data.balance.length)}${list("fflow", "stacked", inner)}`;
}

export function flowsAspect(c: FCtx): Safe {
  const path = q("/api/factories/graph", { factory: c.name });
  const slot = read<FactoryGraphResponse>(path);
  const wait = pending(slot, "the graph", path);
  const scope = c.floor >= 0 ? html`<p class="fscope">${chip("floor " + c.floor)}graph and balance cover all floors</p>` : "";
  const body = slot.data && !slot.data.nodes.length ? emptyLine("nothing to draw") : html`<div class="fgslot"></div>`;
  return html`${scope}${wait || body}${note("nameplate rates at the saved clocks, split over each item's producers by share")}${balance(c)}`;
}

/** Puts the graph into its slot once the markup is mounted; a card click opens machines with
 *  its group picked. */
export function mountGraph(c: FCtx, root: HTMLElement): void {
  const slot = root.querySelector<HTMLElement>(".fgslot");
  if (!slot || !c.graph) return;
  const pick = function (node: ProdNode) {
    if (node.kind !== "process") return;
    const g = c.groups.filter((x) => x.building === node.building && (x.recipe || "") === (node.recipeName || ""))[0];
    if (g) setPick(c.name, "g" + g.index);
    location.hash = aspectHref(c, "machines");
  };
  slot.appendChild(graphElement("factory:" + c.name, viewOf(c.graph), pick));
}

/** Lights an item's lines, boxes, makers and users in the graph and its balance row. */
export function lightItem(root: HTMLElement, c: FCtx, item: string | null): void {
  const svg = root.querySelector<SVGSVGElement>(".fgraph svg.graph");
  root.querySelectorAll(".hl").forEach((el) => el.classList.remove("hl"));
  if (svg) svg.classList.toggle("has-hl", !!item);
  if (!item || !c.graph) return;
  const view = viewOf(c.graph);
  const ends: Record<string, boolean> = {};
  view.edges.forEach((e) => {
    if (e.itemName !== item) return;
    ends[e.source] = true;
    ends[e.target] = true;
  });
  const esc = item.replace(/["\\]/g, "\\$&");
  root.querySelectorAll('[data-item="' + esc + '"]').forEach((el) => el.classList.add("hl"));
  Object.keys(ends).forEach((id) => root.querySelector('[data-node="' + id.replace(/["\\]/g, "\\$&") + '"]')?.classList.add("hl"));
}

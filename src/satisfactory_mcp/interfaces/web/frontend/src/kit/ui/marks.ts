/* Small marks inside entries: chips, item pills, bars, the job bar and the slot tile. */

import { cls, html, type Hole, type Safe } from "../html";
import { clampPct, icon, type Tone } from "./text";

export function chip(text: Hole, tone?: Tone, title?: string): Safe {
  return html`<span class="${cls("chip", tone && "chip-" + tone)}"${title ? html` title="${title}"` : ""}>${text}</span>`;
}

/** Uppercase tag chips: REQUIRED, ALT. */
export function tagChip(text: Hole): Safe {
  return html`<span class="chip chip-muted tag">${text}</span>`;
}

export interface PillItem {
  name: string;
  icon?: string | null;
  rate?: string | null;
  href?: string;
}

/** Icon, rate and name on a well; the name links when href is given. */
export function pill(item: PillItem): Safe {
  const name = item.href ? html`<a class="nm" href="${item.href}">${item.name}</a>` : html`<span class="nm">${item.name}</span>`;
  return html`<span class="pill">${icon(item.icon, item.name)}${item.rate ? html`<b>${item.rate}</b>` : ""}${name}</span>`;
}

/** Inputs → outputs; byproducts after the first output with a "+". */
export function flow(inputs: PillItem[], outputs: PillItem[]): Safe {
  const outs = outputs.map(function (item, i) {
    return html`${i ? html`<span class="plus">+</span>` : ""}${pill(item)}`;
  });
  return html`<span class="pills">${inputs.map(pill)}${inputs.length ? html`<span class="arrow">→</span>` : ""}${outs}</span>`;
}

export type BarFill = "progress" | "util" | "ok" | "mid" | "blocked" | "bad";

export function bar(pct: number, fill?: BarFill, label?: string): Safe {
  return html`<div class="bar"${label ? html` role="img" aria-label="${label}"` : ""}><i class="f-${fill || "progress"}" style="width:${clampPct(pct).toFixed(1)}%"></i></div>`;
}

/** The state mix: segments in the given order, widths by share of the total. */
export function mixBar(parts: [BarFill, number][], label: string): Safe {
  const total = parts.reduce(function (sum, part) {
    return sum + part[1];
  }, 0);
  const segs = parts.map(function (part) {
    return total ? html`<i class="f-${part[0]}" style="width:${((100 * part[1]) / total).toFixed(2)}%"></i>` : "";
  });
  return html`<div class="bar mix" role="img" aria-label="${label}">${segs}</div>`;
}

/** The long-job bar: 2 px, determinate; null pct sweeps. */
export function jobBar(pct: number | null, label: string): Safe {
  if (pct === null) return html`<span class="jobbar sweep" role="progressbar" aria-label="${label}"><i></i></span>`;
  const shown = Math.floor(clampPct(pct));
  return html`<span class="jobbar" role="progressbar" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${shown}"><i style="width:${clampPct(pct).toFixed(1)}%"></i></span>`;
}

export interface Slot {
  name: string;
  icon?: string | null;
  value?: string;
  unit?: string;
  href?: string;
  on?: boolean;
  locked?: boolean;
  fill?: number;
}

/** A square slot: big icon, the held total under it, an optional fill bar. */
export function slotTile(slot: Slot): Safe {
  const value = slot.value ? html`<b>${slot.value}</b>${slot.unit ? html`<span class="u">${slot.unit}</span>` : ""}` : "";
  const body = html`${icon(slot.icon, slot.name)}<span class="sv">${value}</span>${slot.fill === undefined ? "" : bar(slot.fill, "util")}`;
  const names = cls("slot", slot.on && "on", slot.locked && "locked");
  return slot.href ? html`<a class="${names}" href="${slot.href}" title="${slot.name}">${body}</a>` : html`<span class="${names}" title="${slot.name}">${body}</span>`;
}

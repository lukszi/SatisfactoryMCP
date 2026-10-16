/* The entry skeleton (docs/frontend_kit.md): lists of entries with a fixed column template per
 * list, set by the list's `l-<name>` class in the stylesheet. */

import { cls, html, type Hole, type Safe } from "../html";
import type { Tone } from "./text";

export type EntryTone = "bad" | "blocked" | "mid" | "removing" | "";

export interface EntryOptions {
  tone?: EntryTone;
  href?: string;
  key?: string;
  open?: boolean;
  picked?: boolean;
  toggle?: boolean;
  focusable?: boolean;
  title?: string;
  extra?: string;
}

export function entry(o: EntryOptions, inner: Hole): Safe {
  const names = cls("entry", o.tone, o.open && "open", o.picked && "picked", o.extra);
  return html`<div class="${names}"${o.href ? html` data-href="${o.href}"` : ""}${o.key ? html` data-key="${o.key}"` : ""}${o.toggle ? html` data-toggle="1"` : ""}${o.focusable ? html` tabindex="0"` : ""}${o.title ? html` title="${o.title}"` : ""}>${inner}</div>`;
}

export type ListMode = "stacked" | "grid" | "comfy";

/** `name` picks the list's column template (`.l-<name> .entry`). */
export function list(name: string, mode: ListMode, entries: Hole, extra?: string): Safe {
  const modes = mode === "comfy" ? "stacked comfy" : mode;
  return html`<div class="${cls("list", modes, "l-" + name, extra)}">${entries}</div>`;
}

/** An in-list group label, "STARVED · 6". */
export function group(label: Hole, n?: number): Safe {
  return html`<div class="grp">${label}${n === undefined ? "" : html` · ${n}`}</div>`;
}

/** The identity cell: name (a link when href is given) and one muted context line. */
export function identity(name: string, context?: Hole, href?: string): Safe {
  const shown = href ? html`<a class="name" href="${href}" title="${name}">${name}</a>` : html`<span class="name" title="${name}">${name}</span>`;
  return html`<div class="id">${shown}${context ? html`<div class="ctx">${context}</div>` : ""}</div>`;
}

/** One name line: the name, then inline context that shrinks first. */
export function nameLine(name: Hole, context?: Hole): Safe {
  return html`<div class="nl"><span class="line1">${name}</span>${context ? html`<span class="ictx">${context}</span>` : ""}</div>`;
}

/** The scanned number, right-aligned in its column, unit in the meta role. */
export function metric(value: Hole, unit?: Hole, tone?: Tone | "none"): Safe {
  return html`<div class="${cls("metric", tone)}"><b>${value}</b>${unit ? html`<span>${unit}</span>` : ""}</div>`;
}

/** Secondary `value label` pairs. */
export function facts(pairs: [Hole, Hole][]): Safe {
  return html`<div class="facts">${pairs.map(function (pair) {
    return html`<span><em>${pair[0]}</em> ${pair[1]}</span>`;
  })}</div>`;
}

export function chips(inner: Hole): Safe {
  return html`<div class="chips">${inner}</div>`;
}

export function acts(inner: Hole, full?: boolean): Safe {
  return html`<div class="${cls("acts", full && "full")}">${inner}</div>`;
}

/** The expand chevron, keyed to the entry it opens. */
export function chevron(key: string, label: string, open: boolean): Safe {
  return html`<button class="chev" type="button" data-chev="${key}" aria-expanded="${String(open)}" aria-label="${(open ? "collapse " : "expand ") + label}">${open ? "▾" : "▸"}</button>`;
}

export function expandBody(inner: Hole): Safe {
  return html`<div class="expand">${inner}</div>`;
}

/** Wires chevrons and toggle entries inside `root`: a click opens or closes in place. */
export function wireExpand(root: HTMLElement, remember?: (key: string, open: boolean) => void): void {
  root.addEventListener("click", function (event) {
    const target = event.target as Element;
    const chev = target.closest("[data-chev]");
    const row = (chev || target.closest("[data-toggle]"))?.closest<HTMLElement>(".entry");
    if (!row || (!chev && target.closest("a, button, input, select, label"))) return;
    const open = !row.classList.contains("open");
    row.classList.toggle("open", open);
    const button = row.querySelector<HTMLButtonElement>("[data-chev]");
    if (button) {
      button.textContent = open ? "▾" : "▸";
      button.setAttribute("aria-expanded", String(open));
    }
    if (remember && row.dataset.key) remember(row.dataset.key, open);
  });
}

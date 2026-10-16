/* Headings, headline figures, notes and icons: the type roles of docs/frontend_kit.md. */

import { cls, html, type Hole, type Safe } from "../html";

export type Tone = "ok" | "bad" | "blocked" | "mid" | "muted" | "accent" | "id" | "remove";

/** A list or block heading, "name · n"; h2 for top-level blocks, h3 inside a section. */
export function dashH(text: Hole, n?: number | string, tag?: "h2" | "h3"): Safe {
  const count = n === undefined ? "" : html` <span class="n">· ${n}</span>`;
  return tag === "h3" ? html`<h3 class="dash-h">${text}${count}</h3>` : html`<h2 class="dash-h">${text}${count}</h2>`;
}

/** A headline figure: big number, short unit; `pct` draws the thin "x of y" bar. */
export function fig(value: Hole, unit: Hole, tone?: Tone, pct?: number): Safe {
  const track = pct === undefined ? "" : html`<span class="fig-bar"><i style="width:${clampPct(pct).toFixed(1)}%"></i></span>`;
  return html`<span class="${cls("fig", tone)}"><b>${value}</b><span class="k">${unit}</span>${track}</span>`;
}

export function figSub(text: Hole): Safe {
  return html`<span class="fig-sub">${text}</span>`;
}

/** A block heading carrying at most two figures; a link when href is given. */
export function blockHead(title: Hole, figs?: Hole, href?: string): Safe {
  return href ? html`<a class="bh" href="${href}">${dashH(title)}${figs}</a>` : html`<div class="bh">${dashH(title)}${figs}</div>`;
}

export interface PageHead {
  /** [label, href] of each level above the page. */
  crumbs: [string, string][];
  name: string;
  lead?: Hole;
  chips?: Hole;
  acts?: Hole;
  figs?: Hole;
  note?: Hole;
}

/** An object page's header card: breadcrumb, title with lead and chips, figures, one note. */
export function pageHead(o: PageHead): Safe {
  const crumbs = o.crumbs.map((c) => html`<a href="${c[1]}">${c[0]}</a><span class="sep">/</span>`);
  return html`<header class="phead"><nav class="crumbs" aria-label="breadcrumb">${crumbs}<span aria-current="page">${o.name}</span></nav>
    <div class="title">${o.lead}<h1>${o.name}</h1>${o.chips}${o.acts ? html`<span class="grow"></span><span class="acts">${o.acts}</span>` : ""}</div>${o.figs ? html`<div class="figs">${o.figs}</div>` : ""}${o.note ? note(o.note) : ""}</header>`;
}

/** Heading and count left; filters, then one sort select right. */
export function toolrow(head: Safe, tools?: Hole): Safe {
  return html`<div class="toolrow">${head}${tools ? html`<span class="tools">${tools}</span>` : ""}</div>`;
}

export function note(text: Hole): Safe {
  return html`<p class="note">${text}</p>`;
}

export function emptyLine(what: Hole, how?: Hole): Safe {
  return html`<p class="empty"><b>${what}</b>${how ? html` · ${how}` : ""}</p>`;
}

/** The missing-value mark. */
export function na(): Safe {
  return html`<em class="na">–</em>`;
}

export function monogram(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0]![0]! + words[1]![0]! : name.slice(0, 2)).toUpperCase();
}

/** A game icon from /api/icons, or the two-letter monogram when no class is known. */
export function icon(descClass: string | null | undefined, name: string, extra?: string): Safe {
  if (descClass) return html`<img class="${cls("ic", extra)}" src="/api/icons/${encodeURIComponent(descClass)}" alt="${name}" title="${name}" loading="lazy">`;
  return html`<i class="${cls("mono", extra)}" role="img" aria-label="${name}" title="${name}">${monogram(name)}</i>`;
}

/** Icons are optional per install: an icon that fails to load becomes its monogram. */
export function wireIconFallback(): void {
  document.addEventListener(
    "error",
    function (event) {
      const img = event.target as HTMLImageElement;
      if (img.tagName !== "IMG" || !img.classList.contains("ic")) return;
      const tile = document.createElement("i");
      tile.className = img.className.replace(/\bic\b/, "mono");
      tile.setAttribute("role", "img");
      tile.setAttribute("aria-label", img.alt);
      tile.title = img.alt;
      tile.textContent = monogram(img.alt);
      img.replaceWith(tile);
    },
    true
  );
}

export function clampPct(pct: number): number {
  return Math.max(0, Math.min(100, pct));
}

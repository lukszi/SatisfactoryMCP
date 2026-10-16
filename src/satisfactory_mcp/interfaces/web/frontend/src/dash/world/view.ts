/* What every World finder view returns, and the row parts they share: the entry that links to
 * its mark, the distance cell, "show more", the map key's glyphs. */

import { formatNumber } from "../../kit/format";
import { html, trusted, type Hole, type Safe } from "../../kit/html";
import { btn } from "../../kit/ui/controls";
import { entry, type EntryTone } from "../../kit/ui/entry";
import { hashFor } from "../../map/map";
import { worldDash, type WorldAddress } from "./address";

import type { Selection } from "../../app/selection";
import type { Mark, PointM } from "./model";

export interface Ctx {
  a: WorldAddress;
  /** The `near` the API is asked with; "" when there is nothing to be near. */
  near: string;
  /** Where distances the page measures are measured from. */
  from: PointM | null;
  /** The factory searched near, drawn as a square. */
  factory: { name: string; at: PointM } | null;
  /** Rows shown before "show more". */
  shown: number;
}

export interface View {
  title: string;
  n?: number;
  /** View-specific controls for the query card's second row. */
  tools?: Safe;
  body: Safe;
  note?: Hole;
  marks: Mark[];
  /** The results' identity: the map refits when it changes. */
  key: string;
  fitPlayer: boolean;
  legend: Safe;
  /** What picking a row tells the rest of the page (the chat's context). */
  picks: Record<string, Selection>;
}

/** A finder row: lights its mark on hover, picks it on click (again clears). */
export function row(c: Ctx, k: string, title: string, inner: Hole, tone?: EntryTone, spoiler?: boolean): Safe {
  const href = hashFor(worldDash({ ...c.a, sel: c.a.sel === k ? "" : k }));
  return entry({ key: k, href, picked: c.a.sel === k, focusable: true, title, tone, extra: spoiler ? "spoiler" : undefined }, inner);
}

export function away(m: number | null): string {
  if (m === null) return "–";
  return m < 1000 ? formatNumber(m, 0) + " m" : formatNumber(m / 1000, 1) + " km";
}

/** The distance column; `measured` marks one the page worked out, not the server. */
export function awayCell(m: number | null, measured?: boolean): Safe {
  const v = html`<em>${away(m)}</em>`;
  return html`<div class="facts"><span${measured ? html` class="measured" title="measured on the page from where the search is near"` : ""}>${v}</span></div>`;
}

export function moreBtn(c: Ctx, total: number): Safe {
  if (total <= c.shown) return html``;
  return html`<div class="wmore">${btn("show " + Math.min(total - c.shown, c.shown) + " more", { data: { more: "1" } })}<span class="note">${c.shown} of ${total}</span></div>`;
}

export type Glyph = "pure" | "normal" | "impure" | "tapped" | "hull" | "site" | "line" | "spot" | "pick" | "picked" | "factory" | "player";

const GLYPH: Record<Glyph, string> = {
  player: '<circle class="lg-pl" cx="6" cy="6" r="4"/>',
  pure: '<circle class="lg-f" cx="6" cy="6" r="5"/>',
  normal: '<circle class="lg-f" cx="6" cy="6" r="3.6"/>',
  impure: '<circle class="lg-f" cx="6" cy="6" r="2.4"/>',
  tapped: '<circle class="lg-r" cx="6" cy="6" r="3.6"/>',
  hull: '<rect class="lg-h" x="1" y="1" width="10" height="10" rx="3"/>',
  site: '<circle class="lg-h" cx="6" cy="6" r="5"/>',
  line: '<path class="lg-ln" d="M1 9 6 4 11 6"/>',
  spot: '<circle class="lg-r" cx="6" cy="6" r="4"/>',
  pick: '<circle class="lg-f" cx="6" cy="6" r="3.2"/>',
  picked: '<circle class="lg-r" cx="6" cy="6" r="3.2"/>',
  factory: '<rect class="lg-fac" x="2" y="2" width="8" height="8"/>',
};

/** The map key: glyphs and single words. */
export function legend(items: [Glyph, string][]): Safe {
  return html`${items.map(
    (it) => html`<span class="lgi"><svg class="lg" viewBox="0 0 12 12" aria-hidden="true">${trusted(GLYPH[it[0]])}</svg>${it[1]}</span>`
  )}`;
}

/** A query string without the empty parameters. */
export function qs(path: string, params: Record<string, string | undefined>): string {
  const pairs = Object.keys(params)
    .filter((k) => !!params[k])
    .map((k) => k + "=" + encodeURIComponent(params[k]!));
  return path + (pairs.length ? "?" + pairs.join("&") : "");
}

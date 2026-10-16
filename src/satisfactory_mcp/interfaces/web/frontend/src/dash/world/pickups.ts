/* World > pickups: collected of placed per kind, then what is left (nearest first) or what was
 * taken, from /api/collectibles. */

import { spoilerFlag } from "../../app/settings";
import { state } from "../../app/state";
import { count } from "../../kit/format";
import { html, type Safe } from "../../kit/html";
import { seg, selectBox } from "../../kit/ui/controls";
import { chips, list } from "../../kit/ui/entry";
import { bar, chip } from "../../kit/ui/marks";
import { dashH, emptyLine, icon } from "../../kit/ui/text";
import { hashFor } from "../../map/map";
import { lootLine, pickupName } from "../../map/drawn/pickups";
import { pending, read } from "../factories/data";
import { worldDash } from "./address";
import { pickupSelection } from "./finds";
import { madeRead, playerKnown } from "./here";
import { keyOf, PICKUP_ICON, pickups, type Mark, type Pickup } from "./model";
import { awayCell, legend, moreBtn, qs, row, type Ctx, type View } from "./view";

import type { Selection } from "../../app/selection";
import type { CensusRow, CollectiblesResponse } from "../../api/shapes";

function tools(c: Ctx, data: CollectiblesResponse | null): Safe {
  const modes = (["remaining", "collected"] as const).map((m) => ({ value: m, label: m, href: hashFor(worldDash({ ...c.a, mode: m, sel: "" })) }));
  const kinds: [string, string][] = [["", "every kind"] as [string, string]].concat((data?.census || []).map((r): [string, string] => [r.category, r.label]));
  return html`${seg("w-pmode", modes, c.a.mode, "which pickups to list")}${selectBox("w-pkind", kinds, c.a.kind, "kind")}`;
}

function kindTile(c: Ctx, r: CensusRow): Safe {
  const title = r.label + ": " + r.collected + " collected of " + r.placed + (r.standing === null ? "" : " · " + r.standing + " standing") + (r.never_streamed === null ? "" : " · " + r.never_streamed + " never streamed");
  const href = hashFor(worldDash({ ...c.a, kind: c.a.kind === r.category ? "" : r.category, sel: "" }));
  return html`<a class="${c.a.kind === r.category ? "wkind on" : "wkind"}" href="${href}" title="${title}">${icon(PICKUP_ICON[r.category], r.label)}<span class="num"><b>${r.collected}</b> <span class="muted">of ${r.placed}</span></span>${bar(r.placed ? (r.collected / r.placed) * 100 : 0, "util")}</a>`;
}

function pickEntry(c: Ctx, p: Pickup): Safe {
  const r = p.row;
  const name = pickupName(r.category);
  let mark: Safe | string = "";
  if (!r.collected) {
    if (r.observed === "never_streamed") mark = chip("never streamed", "muted", "placed where no save has had it loaded");
    else if (r.looted === true) mark = chip("looted", "muted", lootLine(r) || "");
    else if (r.looted === false) mark = chip("unlooted", "muted", lootLine(r) || "");
  }
  return row(c, keyOf.pick(r), name + " · " + r.x_m + ", " + r.y_m + " m", html`${icon(PICKUP_ICON[r.category], name)}<div class="id"><span class="name">${name}</span></div>${chips(mark)}${awayCell(p.distance_m, p.measured)}`);
}

function picks(rows: Pickup[]): Record<string, Selection> {
  const out: Record<string, Selection> = {};
  rows.forEach((p) => (out[keyOf.pick(p.row)] = pickupSelection(p.row)));
  return out;
}

function hiddenNote(data: CollectiblesResponse): Safe | string {
  if (!data.hidden_spoilers) return "";
  const href = hashFor(state.dash) + "&settings=spoilers";
  return html`${count(data.hidden_spoilers)} ${data.hidden_spoilers === 1 ? "kind" : "kinds"} hidden while spoilers are off · <a href="${href}">settings</a>`;
}

export function pickupsFinder(c: Ctx): View {
  const nearest = c.a.mode === "remaining" && !!c.near && playerKnown();
  const path = madeRead(qs("/api/collectibles", { mode: nearest ? "nearest" : c.a.mode, near: nearest ? c.near : "", group: c.a.kind, spoilers: spoilerFlag() }));
  const slot = read<CollectiblesResponse>(path);
  if (!slot.data) return { title: c.a.mode, tools: tools(c, null), body: pending(slot, "pickups", path) || emptyLine("no pickups"), marks: [], key: "", fitPlayer: false, legend: html``, picks: {} };
  const data = slot.data;
  const all = pickups(data.rows, c.from, c.a.sort, pickupName);
  const rows = all.slice(0, c.shown);
  const census = data.census.length ? html`${dashH("collected of placed", data.census.length, "h3")}<div class="wkinds">${data.census.map((r) => kindTile(c, r))}</div>` : "";
  const empty = c.a.mode === "collected" ? emptyLine("nothing collected yet") : emptyLine("no pickup left" + (c.a.kind ? " of this kind" : ""));
  const listing = rows.length ? html`${list("wpick", "stacked", rows.map((p) => pickEntry(c, p)))}${moreBtn(c, all.length)}` : empty;
  const where = data.where ? " · nearest to " + data.where : "";
  return {
    title: c.a.mode,
    n: all.length,
    tools: tools(c, data),
    body: html`${census}${dashH(c.a.mode, all.length, "h3")}${listing}`,
    note: html`${count(rows.length)} of ${count(all.length)} ${c.a.mode}${where}${data.hidden_spoilers ? html` · ${hiddenNote(data)}` : ""}`,
    marks: rows.map((p): Mark => ({ t: "pick", k: keyOf.pick(p.row), x: p.row.x_m, y: p.row.y_m, category: p.row.category, collected: p.row.collected, tip: pickupName(p.row.category) })),
    key: "picks|" + path + "|" + c.a.sort + "|" + rows.length,
    fitPlayer: nearest,
    legend: legend([["player", "player"], [c.a.mode === "collected" ? "picked" : "pick", c.a.mode === "collected" ? "collected" : "pickup"]]),
    picks: picks(rows),
  };
}

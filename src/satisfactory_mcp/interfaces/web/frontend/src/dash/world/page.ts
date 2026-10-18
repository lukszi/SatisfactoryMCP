/* The World page, `dash=world?show=nodes|conduits|pickups&…&sel=<key>`: the player as a strip,
 * the finder left and a live map of its results right (on top on phones). docs/frontend_kit.md,
 * "World". */

import "./world.css";
import { closeTo, go } from "../../app/nav";
import { select } from "../../app/selection";
import { settingChoice } from "../../app/settings";
import { state } from "../../app/state";
import { vitals } from "../../app/vitals";
import { coords, formatNumber } from "../../kit/format";
import { html, mount, trusted, type Safe } from "../../kit/html";
import { seg, selectBox, sortSelect } from "../../kit/ui/controls";
import { emptyLine, note } from "../../kit/ui/text";
import { notify, offer } from "../../kit/toast";
import { hashFor } from "../../map/map";
import { actorWord } from "../planner/state";
import { wireRetry } from "../factories/data";
import { requestRender } from "../actions";
import { conduitsFinder } from "./conduits";
import { resourceOptions } from "./finds";
import { here, playerAt, playerKnown } from "./here";
import { fromLegacy, GROUPS, sortsOf, SHOWS, worldAddress, worldDash, worldScrollKey, type WorldAddress } from "./address";
import { PAGE, type PointM } from "./model";
import { nodesFinder } from "./nodes";
import { pickupsFinder } from "./pickups";
import { away, type Ctx, type View } from "./view";
import { attachMap, lightMark, mapPanel } from "./world-map";

import type { ActivityEvent } from "../planner/state";

const GROUP_WORD: Record<string, string> = { none: "none", field: "field", region: "region", run: "runs", network: "networks" };
const SORT_WORD: Record<string, string> = { nearest: "nearest", rate: "rate", total: "total", rank: "best rank", length: "length", distance: "distance", kind: "kind" };
const EXTRA_WORD: Record<string, string> = { purity: "purity", status: "status", within: "within m", pure: "pure only", net: "network" };

/* "Show more" per query, for the page session. */
const shownBy: Record<string, number> = {};
let scrollTo = "";
let current: { a: WorldAddress; view: View } | null = null;

function factories(): { name: string; at: PointM }[] {
  const health = vitals().health;
  return (health ? health.factories : [])
    .map((r) => ({ name: r.name, at: r.centroid_m as PointM }))
    .sort((x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: "base", numeric: true }));
}

function pointOf(text: string): PointM | null {
  const m = /^(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)$/.exec(text.trim());
  return m ? [+m[1]!, +m[2]!] : null;
}

function ctxOf(a: WorldAddress, subject: string): Ctx {
  const factory = factories().filter((f) => f.name === a.near)[0] || null;
  const from = a.near === "me" ? playerAt() : factory ? factory.at : pointOf(a.near);
  return { a, near: a.near === "me" && !playerKnown() ? "" : a.near, from, factory, shown: shownBy[worldScrollKey(subject)] || PAGE };
}

export function worldTitle(subject: string): string {
  const show = worldAddress(subject).show;
  return show.charAt(0).toUpperCase() + show.slice(1);
}

/* ---------------------------------------------------------------- controls */

function dashOf(a: WorldAddress, patch: Partial<WorldAddress>): string {
  return worldDash({ ...a, sel: "", ...patch });
}

function dash(a: WorldAddress, patch: Partial<WorldAddress>): string {
  return hashFor(dashOf(a, patch));
}

function pair(label: string, inner: Safe): Safe {
  return html`<span class="qpair"><span class="k">${label}</span>${inner}</span>`;
}

function nearControl(a: WorldAddress): Safe {
  const list = factories();
  const isFactory = list.some((f) => f.name === a.near);
  const on = a.near === "me" ? "me" : isFactory ? "factory" : "other";
  const items = [{ value: "me", label: "me", href: dash(a, { near: "me" }) }];
  if (list.length && a.show !== "pickups") items.push({ value: "factory", label: "a factory", href: dash(a, { near: isFactory ? a.near : list[0]!.name }) });
  if (on === "other") items.push({ value: "other", label: pointOf(a.near) ? coords(pointOf(a.near)![0], pointOf(a.near)![1]) : a.near, href: dash(a, {}) });
  const pick = isFactory ? selectBox("w-nearfac", list.map((f): [string, string] => [f.name, f.name]), a.near, "factory") : "";
  return pair("near", html`${seg("w-near", items, on, "near what")}${pick}`);
}

function queryCard(a: WorldAddress, view: View): Safe {
  const shows = SHOWS.map((s) => ({ value: s, label: s, href: dash(a, { show: s, group: "", sort: "", kind: "", extra: {} }) }));
  const res = a.show === "nodes" ? selectBox("w-res", resourceOptions("any resource", a.res), a.res, "resource") : "";
  const groups = GROUPS[a.show].length > 1 ? pair("group", seg("w-group", GROUPS[a.show].map((g) => ({ value: g, label: GROUP_WORD[g] || g, href: dash(a, { group: g, sort: "" }) })), a.group, "group by")) : "";
  const sorts = sortsOf(a).map((s): [string, string] => [s, SORT_WORD[s] || s]);
  const extras = Object.keys(a.extra).map((k) => html`<button type="button" class="fchip" data-drop="${k}" aria-pressed="true" title="remove this filter">${EXTRA_WORD[k] || k}: ${a.extra[k]!} ×</button>`);
  const rankOff = a.show === "nodes" && a.group === "field" && !a.res ? html`<span class="note">best rank needs one resource</span>` : "";
  return html`<div class="card wctl"><div class="qrow">${seg("w-show", shows, a.show, "what to find")}${res}</div><div class="qrow">${a.show !== "pickups" || a.near !== "me" ? nearControl(a) : ""}${groups}${view.tools || ""}${sortSelect("w-sort", sorts, a.sort)}${rankOff}</div>${extras.length ? html`<div class="qrow">${extras}</div>` : ""}</div>`;
}

function strip(): Safe {
  const d = here.data;
  const glyph = trusted('<svg class="lg" viewBox="0 0 12 12" aria-hidden="true"><circle class="lg-pl" cx="6" cy="6" r="4"/></svg>');
  if (!d) return html`<p class="wstrip muted">${glyph}${here.failed ? "where the player is could not be read" : "reading where the player is…"}</p>`;
  const p = d.player;
  if (!p) return html`<p class="wstrip muted">${glyph}no player position in this save · a dedicated-server save holds no pawn</p>`;
  const place = [d.region ? d.region.name : "off the map", d.grid].filter(Boolean).join(" · ");
  const nearest = d.nearest_building ? html`<span><span class="muted">nearest</span> ${d.nearest_building.name} · ${away(d.nearest_building.distance_m)}</span>` : "";
  const pawns = d.pawns > 1 ? html`<span class="muted">the host's position, of ${d.pawns} players</span>` : "";
  return html`<p class="wstrip" aria-label="where the player is" title="${d.age_note}">${glyph}<span class="num">${coords(p.x_m, p.y_m)}</span><span class="muted">${formatNumber(p.z_m, 0)} m up</span><span>${place}</span>${nearest}${pawns}</p>`;
}

/* ---------------------------------------------------------------- linking */

function lightRow(root: HTMLElement, key: string | null): void {
  root.querySelectorAll(".wfind .entry.hl").forEach((el) => el.classList.remove("hl"));
  if (key) root.querySelectorAll('.wfind .entry[data-key="' + CSS.escape(key) + '"]').forEach((el) => el.classList.add("hl"));
}

function pick(a: WorldAddress, key: string, fromMap: boolean): void {
  const again = a.sel === key;
  if (!again && current?.view.picks[key]) select(current.view.picks[key]!);
  scrollTo = fromMap && !again ? key : "";
  go(worldDash({ ...a, sel: again ? "" : key }), "replace");
}

function wire(root: HTMLElement, a: WorldAddress, subject: string): void {
  wireRetry(root);
  root.addEventListener("change", function (event) {
    const t = event.target as HTMLSelectElement;
    const patch: Record<string, Partial<WorldAddress>> = {
      "w-res": { res: t.value },
      "w-nearfac": { near: t.value },
      "w-sort": { sort: t.value },
      "w-radius": { radius: t.value === "250" ? "" : t.value },
      "w-ckind": { kind: t.value },
      "w-pkind": { kind: t.value },
    };
    if (patch[t.id]) go(dashOf(a, patch[t.id]!), "replace");
  });
  root.addEventListener("click", function (event) {
    const t = event.target as Element;
    if (t.closest("[data-more]")) {
      const k = worldScrollKey(subject);
      shownBy[k] = (shownBy[k] || PAGE) * 2;
      requestRender();
      return;
    }
    const drop = t.closest<HTMLElement>("[data-drop]");
    if (drop) {
      const extra = { ...a.extra };
      delete extra[drop.dataset.drop || ""];
      go(dashOf(a, { extra }), "replace");
      return;
    }
    if (t.closest("a, button, input, select, label")) return;
    const row = t.closest<HTMLElement>(".wfind .entry[data-key]");
    if (row) pick(a, row.dataset.key || "", false);
  });
  root.addEventListener("keydown", function (event) {
    const t = event.target as HTMLElement;
    if (event.key === "Enter" && t.matches(".wfind .entry[data-key]")) pick(a, t.dataset.key || "", false);
  });
  function hover(event: Event): void {
    const row = (event.target as Element).closest<HTMLElement>(".wfind .entry[data-key]");
    const key = row ? row.dataset.key || null : null;
    lightRow(root, key);
    lightMark(key);
  }
  root.addEventListener("mouseover", hover);
  root.addEventListener("focusin", hover);
}

/* ---------------------------------------------------------------- page */

function finder(c: Ctx): View {
  if (c.a.show === "conduits") return conduitsFinder(c);
  if (c.a.show === "pickups") return pickupsFinder(c);
  return nodesFinder(c);
}

export function renderWorld(body: HTMLElement, subject: string): void {
  const a = worldAddress(subject);
  const c = ctxOf(a, subject);
  const view = finder(c);
  current = { a, view };
  const root = document.createElement("div");
  root.className = "wpage";
  const nothing = !c.near && a.near === "me" && a.show === "conduits" ? emptyLine("no player position to search near", "pick a factory instead") : "";
  mount(
    root,
    html`${strip()}<div class="wsplit"><section class="wfind" aria-label="finder">${queryCard(a, view)}${nothing || view.body}${view.note ? note(view.note) : ""}</section>${mapPanel(view.legend)}</div>`
  );
  body.appendChild(root);
  wire(root, a, subject);
  const marks = c.factory ? view.marks.concat([{ t: "factory", x: c.factory.at[0], y: c.factory.at[1], text: c.factory.name }]) : view.marks;
  attachMap(root, { key: view.key + "|" + (c.factory ? c.factory.name : ""), marks, player: playerAt(), fitPlayer: view.fitPlayer, sel: a.sel }, {
    pick: (key) => pick(a, key, true),
    hover: (key) => {
      lightRow(root, key);
      lightMark(key);
    },
  });
  if (scrollTo) {
    const key = scrollTo;
    scrollTo = "";
    requestAnimationFrame(() => root.querySelector('.wfind .entry[data-key="' + CSS.escape(key) + '"]')?.scrollIntoView({ block: "nearest" }));
  }
}

function worldShown(): boolean {
  return state.dash === "world" || state.dash.indexOf("world?") === 0;
}

/* Esc clears the pick, unless a field or a dialog has the key. */
document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape" || !worldShown() || document.querySelector("dialog[open]")) return;
  const t = event.target as Element;
  if (t.closest && t.closest("input, select, textarea")) return;
  const a = worldAddress(state.dash.slice(5));
  if (a.sel) closeTo(worldDash({ ...a, sel: "" }));
});

function typingInField(): boolean {
  const active = document.activeElement;
  return !!active && /^(INPUT|TEXTAREA)$/.test(active.tagName);
}

/** Where a chat's find lands: the old view and parameters it names, as this page's address. */
export function findDash(args: { view?: unknown; params?: Record<string, string> }): string {
  return typeof args.view === "string" ? fromLegacy(args.view, args.params || {}) : "world";
}

/* A find chat ran: follow it, or offer to, as the follow setting says; never mid-typing. */
export function onFindActivity(entry: ActivityEvent): void {
  if (entry.kind !== "world.find" || entry.world !== state.world || entry.actor.kind === "page") return;
  const mode = settingChoice("follow");
  const args = (entry.args || {}) as { view?: string; params?: Record<string, string> };
  if (mode === "off" || typeof args.view !== "string") return;
  const there = findDash(args);
  if (state.dash === there) return;
  const who = actorWord(entry.actor);
  if (mode === "toasts" || typingInField()) {
    offer(who + " " + entry.text, "open", () => go(there));
    return;
  }
  notify(who + " " + entry.text + " (Settings, follow chat)");
  go(there);
}

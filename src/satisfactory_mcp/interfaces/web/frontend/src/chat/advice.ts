/* Advisors: the "worth a look" block on a factory's page in the kit, snooze and restore in
 * its ⋯, and the store behind them. See docs/advisors_contract.md §8. */

import { get, latest, send } from "../api/client";
import { askBarOwnerId, onAsks, openAsk, renderAskBar, settleAskFocus } from "./asks";
import { html, mount, type Safe } from "../kit/html";
import { openMenu, type MenuItem } from "../kit/overlay/menu";
import { btn, moreBtn } from "../kit/ui/controls";
import { acts, entry, expandBody, list, nameLine } from "../kit/ui/entry";
import { chip } from "../kit/ui/marks";
import { dashH, emptyLine, toolrow, type Tone } from "../kit/ui/text";
import { hashFor } from "../map/map";
import { createListeners } from "../app/listeners";
import { goToMapThen } from "../app/nav";
import { showBox, showMachine } from "../map/map-highlight";
import { registerFetch } from "../app/registry";
import { onSetting, spoilerQuery } from "../app/settings";
import { state } from "../app/state";
import { fail, friendlyError, notify } from "../kit/toast";
import { startTrace } from "../map/tools/trace";
import { ADVICE_WORD, WORDS } from "../kit/words";

import type { ApiError, ApiPath, StatusError } from "../api/client";
import type { AdviceResponse, AdviceRestored, AdviceRow, AdviceStaleResponse } from "../api/shapes";

const VISIBLE = 5;
const PER_KIND = 3;
const SNOOZES: [number | null, string][] = [
  [0.5, "30 min"],
  [1, "1 h"],
  [4, "4 h"],
  [10, "10 h"],
  [null, "until it gets worse"],
];
const PATH: ApiPath = "/api/advice";
const HIDE: ApiPath = "/api/advice/hidden";
const RESTORE: ApiPath = "/api/advice/hidden/{adv_id}";
const SEEN_KEY = "advice.seen";
const ASK_PREFIX = "ask:adv-";
const TRACED = ["unconnected", "starved", "underclock", "box_empty"];

const view = {
  data: null as AdviceResponse | null,
  loadError: "",
  world: "",
  newIds: {} as Record<string, boolean>,
  expandedKeys: {} as Record<string, boolean>,
  showAllActive: false,
  showHidden: false,
  writingKeys: {} as Record<string, boolean>,
};
const adviceListeners = createListeners();

export function onAdvice(listener: () => void): void {
  adviceListeners.on(listener);
}

function notifyAdviceListeners(): void {
  adviceListeners.emit();
}

function remembered(): Record<string, boolean> | null {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : null;
  } catch (ignored) {
    return null;
  }
}

function remember(ids: string[]): void {
  const all: Record<string, boolean> = {};
  ids.forEach(function (id) {
    all[id] = true;
  });
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(all));
  } catch (ignored) {
    /* storage is a convenience */
  }
}

/** The active advisories not shown before: against the previous reply in this world, else
 *  against the ids this browser stored on its last visit. */
function newSinceLastVisit(previous: AdviceResponse | null, body: AdviceResponse, seen: Record<string, boolean> | null): string[] {
  const fresh = body.active.filter(function (advisory) {
    if (previous) {
      return !previous.active.concat(previous.hidden).some(function (known) {
        return known.id === advisory.id;
      });
    }
    return !!seen && !seen[advisory.id];
  });
  return fresh.map(function (advisory) {
    return advisory.id;
  });
}

function adoptAdvice(body: AdviceResponse): void {
  const ids = body.active.concat(body.hidden).map(function (advisory) {
    return advisory.id;
  });
  const seen = remembered();
  const sameWorld = !!view.data && view.world === state.world;
  if (!sameWorld) view.newIds = {};
  newSinceLastVisit(sameWorld ? view.data : null, body, seen).forEach(function (id) {
    view.newIds[id] = true;
  });
  remember(ids);
  view.data = body;
  view.loadError = "";
  view.world = state.world;
  notifyAdviceListeners();
}

export function refetchAdvice(): void {
  const ticket = latest("advice");
  get<AdviceResponse>(`${PATH}?${spoilerQuery()}`)
    .then(function (body) {
      if (ticket.fresh()) adoptAdvice(body);
    })
    .catch(function (reason) {
      if (!ticket.fresh()) return;
      view.loadError = friendlyError(reason);
      notifyAdviceListeners();
    });
}

registerFetch<AdviceResponse>({
  wave: "live",
  rank: 45,
  path: PATH,
  query: spoilerQuery,
  label: "advisories",
  clears: [],
  refilters: false,
  draw: adoptAdvice,
  failed: function () {
    view.loadError = "the save could not be read";
    notifyAdviceListeners();
  },
});

onSetting(refetchAdvice);

let askBarWasOurs = false;
onAsks(function () {
  const ours = askBarOwnerId().indexOf(ASK_PREFIX) === 0;
  if (ours || askBarWasOurs) notifyAdviceListeners();
  askBarWasOurs = ours;
});

/* -------------------------------------------------------------------- writes */

function reportWriteFailure(reason: unknown, what: string): void {
  const err = reason as StatusError;
  if (err?.status === 409) {
    const body = err.body as AdviceStaleResponse | undefined;
    fail(body?.error || "that advisory changed since you read it");
  } else fail(what + ": " + friendlyError(reason));
  refetchAdvice();
}

function playTimeText(hours: number): string {
  return hours < 1 ? Math.max(1, Math.round(hours * 60)) + " min" : Math.round(hours * 10) / 10 + " h";
}

/** Hides an advisory for `hours` of play, or until it gets worse when `hours` is null. */
function snoozeOrDismiss(advisory: AdviceRow, hours: number | null): void {
  if (view.writingKeys[advisory.key]) return;
  view.writingKeys[advisory.key] = true;
  const body = hours === null ? { key: advisory.key, mode: "dismiss", rev: advisory.rev } : { key: advisory.key, mode: "snooze", hours: hours, rev: advisory.rev };
  notifyAdviceListeners();
  send<AdviceRow & ApiError>("POST", HIDE, body, undefined, spoilerQuery())
    .then(function () {
      notify(hours === null ? "hidden until it gets worse" : "snoozed for " + playTimeText(hours) + " of play");
      refetchAdvice();
    })
    .catch(function (reason) {
      reportWriteFailure(reason, "not hidden");
    })
    .then(function () {
      delete view.writingKeys[advisory.key];
      notifyAdviceListeners();
    });
}

function restore(advisory: AdviceRow): void {
  if (view.writingKeys[advisory.key]) return;
  view.writingKeys[advisory.key] = true;
  notifyAdviceListeners();
  send<AdviceRestored>("DELETE", RESTORE, { rev: advisory.rev }, advisory.id)
    .then(function () {
      notify("restored " + ADVICE_WORD[advisory.kind] + ": " + advisory.text);
      refetchAdvice();
    })
    .catch(function (reason) {
      reportWriteFailure(reason, "not restored");
    })
    .then(function () {
      delete view.writingKeys[advisory.key];
      notifyAdviceListeners();
    });
}

/* ----------------------------------------------------------------- the rows */

function goToSpots(advisory: AdviceRow): void {
  const one = advisory.machines.length === 1 ? advisory.machines[0]! : null;
  const layers = advisory.reveal;
  goToMapThen(function () {
    if (one?.instance && one.x_m !== null && one.y_m !== null) {
      showMachine(one.instance, one.name, one.x_m, one.y_m, { layers: layers });
      return;
    }
    const bbox = advisory.bbox_m;
    if (!bbox) return;
    const pad = 30;
    showBox([bbox[0]! - pad, bbox[1]! - pad, bbox[2]! + pad, bbox[3]! + pad], { layers: layers });
  });
}

/** The ⋯ items: where it leads, ask, then the ways to hide it (or restore a hidden one). */
function menuOf(advisory: AdviceRow): MenuItem[] {
  const items: MenuItem[] = [];
  const seed = advisory.seed;
  if (seed && TRACED.indexOf(advisory.kind) >= 0) items.push({ label: "trace what feeds it", act: () => goToMapThen(() => startTrace(seed, "up")) });
  if (advisory.kind === "power" || advisory.kind === "headroom") items.push({ label: "open power", href: hashFor("power") });
  if (advisory.plan) items.push({ label: "open plan", href: hashFor("planner/" + advisory.plan) });
  if (advisory.state !== "active") return items.concat({ label: "restore", act: () => restore(advisory) });
  items.push({ label: WORDS.askChat, act: () => openAsk({ kind: "advice", label: advisory.text, ref: advisory.key }, "adv-" + advisory.id) });
  return items.concat(SNOOZES.map((s) => ({ label: s[0] === null ? "hide until it gets worse" : "snooze " + s[1], act: () => snoozeOrDismiss(advisory, s[0]) })));
}

function hiddenWords(advisory: AdviceRow): string {
  const who = advisory.by === "chat" ? " by chat" : "";
  const data = view.data;
  if (advisory.state === "snoozed" && advisory.until_play_s !== null && data) {
    const left = Math.max(0, advisory.until_play_s - data.play_s) / 3600;
    return "snoozed" + who + ", " + playTimeText(left) + " of play left";
  }
  return "hidden" + who + " until it gets worse";
}

function rowView(advisory: AdviceRow): Safe {
  const open = !!view.expandedKeys[advisory.key];
  const word = ADVICE_WORD[advisory.kind] || advisory.kind;
  const marks = html`${chip(word, advisory.tone as Tone)}${view.newIds[advisory.id] && advisory.state === "active" ? chip("new", "muted", "not on this page before") : ""}${advisory.back ? chip("back", "muted", "back: worse than when it was hidden") : ""}`;
  const facts = advisory.lines.slice();
  if (!facts.length) facts.push("no more to it than the line above");
  const map = advisory.machines.length
    ? html`<button type="button" class="btn icon" data-advmap="${advisory.key}" title="show it on the map" aria-label="${"show " + word + " " + advisory.subject + " on the map"}">◎</button>`
    : html`<span class="btn-gap" aria-hidden="true"></span>`;
  const chev = html`<button class="chev" type="button" data-advx="${advisory.key}" aria-expanded="${String(open)}" aria-label="${(open ? "hide" : "show") + " the evidence"}">${open ? "▾" : "▸"}</button>`;
  return entry(
    { open, title: advisory.text, extra: advisory.state === "active" ? "" : "adv-hidden" },
    html`<div class="id">${nameLine(html`${marks} <span class="name">${advisory.text}</span>`, advisory.state === "active" ? "" : hiddenWords(advisory))}</div>${acts(html`${map}<span data-adv="${advisory.key}">${moreBtn(word, [])}</span>${chev}`)}${expandBody(html`${facts.map((line) => html`<p>${line}</p>`)}`)}`
  );
}

/** At most VISIBLE rows and PER_KIND of each kind, unless every row was asked for. */
function shownRows(rows: AdviceRow[]): { shown: AdviceRow[]; rest: AdviceRow[] } {
  if (view.showAllActive) return { shown: rows, rest: [] };
  const perKind: Record<string, number> = {};
  const shown: AdviceRow[] = [];
  const rest: AdviceRow[] = [];
  rows.forEach(function (advisory) {
    perKind[advisory.kind] = (perKind[advisory.kind] || 0) + 1;
    if (perKind[advisory.kind]! <= PER_KIND && shown.length < VISIBLE) shown.push(advisory);
    else rest.push(advisory);
  });
  return { shown: shown, rest: rest };
}

function moreWords(rest: AdviceRow[]): string {
  const kinds: string[] = [];
  const perKind: Record<string, number> = {};
  rest.forEach(function (advisory) {
    if (!perKind[advisory.kind]) kinds.push(advisory.kind);
    perKind[advisory.kind] = (perKind[advisory.kind] || 0) + 1;
  });
  return kinds.map((kind) => (perKind[kind]! > 1 ? perKind[kind] + " " : "") + ADVICE_WORD[kind]).join(", ");
}

function textLink(text: string, act: string, expanded: boolean): Safe {
  return html`<button type="button" class="advlink" data-advact="${act}" aria-expanded="${String(expanded)}">${text}</button>`;
}

export interface AdviceFilter {
  factory: string;
}

function activeBlock(active: AdviceRow[], filter?: AdviceFilter): Safe {
  if (!active.length) return emptyLine(filter ? "nothing worth a look in this factory" : "nothing worth a look in this save");
  const cut = shownRows(active);
  const more = cut.rest.length
    ? textLink("+" + cut.rest.length + " more (" + moreWords(cut.rest) + ")", "all", false)
    : view.showAllActive && active.length > VISIBLE
      ? textLink("show fewer", "fewer", true)
      : "";
  return html`${list("adv", "stacked", cut.shown.map(rowView), "nolead")}${more}`;
}

function wireAdvice(card: HTMLElement, rows: AdviceRow[]): void {
  const byKey = (key: string | undefined) => rows.filter((r) => r.key === key)[0];
  card.addEventListener("click", function (event) {
    const target = event.target as Element;
    const toggle = target.closest<HTMLElement>("[data-advx]");
    const map = target.closest<HTMLElement>("[data-advmap]");
    const more = target.closest<HTMLElement>("[data-adv] .more");
    const act = target.closest<HTMLElement>("[data-advact]")?.dataset.advact;
    if (toggle) {
      event.stopPropagation();
      view.expandedKeys[toggle.dataset.advx!] = !view.expandedKeys[toggle.dataset.advx!];
    } else if (map) {
      const advisory = byKey(map.dataset.advmap);
      if (advisory) goToSpots(advisory);
      return;
    } else if (more) {
      const advisory = byKey(more.closest<HTMLElement>("[data-adv]")!.dataset.adv);
      if (advisory) openMenu(more, menuOf(advisory));
      return;
    } else if (act === "all" || act === "fewer") view.showAllActive = act === "all";
    else if (act === "hidden") view.showHidden = !view.showHidden;
    else if (act === "retry") refetchAdvice();
    else return;
    notifyAdviceListeners();
  });
}

/** The "worth a look" block: one line per advisory, the evidence in its expand, ⋯ for the
 *  rest; hidden ones folded under the list. */
export function adviceCard(parent: HTMLElement, filter?: AdviceFilter): void {
  const card = document.createElement("section");
  card.className = "advice";
  card.setAttribute("aria-label", WORDS.worthALook);
  parent.appendChild(card);
  const data = view.world === state.world ? view.data : null;
  if (!data) {
    const head = toolrow(dashH(WORDS.worthALook));
    mount(card, view.loadError ? html`${head}${emptyLine("the advisories did not load", view.loadError)} ${btn("try again", { data: { advact: "retry" } })}` : html`${head}<p class="note" aria-busy="true">loading advisories…</p>`);
    wireAdvice(card, []);
    return;
  }
  const inScope = (advisory: AdviceRow) => !filter || (advisory.subject_kind === "factory" && advisory.subject === filter.factory);
  const active = data.active.filter(inScope);
  const hidden = data.hidden.filter(inScope);
  const fold = hidden.length ? textLink(hidden.length + " hidden · " + (view.showHidden ? "close" : "show"), "hidden", view.showHidden) : "";
  const hiddenList = hidden.length && view.showHidden ? list("adv", "stacked", hidden.map(rowView), "nolead") : "";
  mount(card, html`${toolrow(dashH(WORDS.worthALook, active.length), fold)}<div class="adv-ask"></div>${activeBlock(active, filter)}${hiddenList}`);
  if (askBarOwnerId().indexOf(ASK_PREFIX) === 0) renderAskBar(card.querySelector<HTMLElement>(".adv-ask")!);
  wireAdvice(card, active.concat(hidden));
  settleAskFocus(card);
}

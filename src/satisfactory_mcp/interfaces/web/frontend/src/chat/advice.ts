/* Advisors: the "worth a look" card on the Overview and on a factory's page, the snooze split
 * button, restore, and the store behind them. See docs/advisors_contract.md §8. */

import { get, latest, send } from "../api/client";
import { askButton, askBarOwnerId, onAsks, renderAskBar, settleAskFocus } from "./asks";
import { button, chip, empty, error, link, loading } from "../kit/dashkit";
import { make } from "../kit/dom";
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

var VISIBLE = 5;
var PER_KIND = 3;
var SNOOZES: [number | null, string][] = [
  [0.5, "30 min"],
  [1, "1 h"],
  [4, "4 h"],
  [10, "10 h"],
  [null, "until it gets worse"],
];
var PATH: ApiPath = "/api/advice";
var HIDE: ApiPath = "/api/advice/hidden";
var RESTORE: ApiPath = "/api/advice/hidden/{adv_id}";
var SEEN_KEY = "advice.seen";
var ASK_PREFIX = "ask:adv-";
var TRACED = ["unconnected", "starved", "underclock", "box_empty"];

var view = {
  data: null as AdviceResponse | null,
  loadError: "",
  world: "",
  newIds: {} as Record<string, boolean>,
  expandedKeys: {} as Record<string, boolean>,
  showAllActive: false,
  showHidden: false,
  openMenuKey: "",
  focusMenuOnRender: false,
  writingKeys: {} as Record<string, boolean>,
};
var adviceListeners = createListeners();

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

var askBarWasOurs = false;
onAsks(function () {
  const ours = askBarOwnerId().indexOf(ASK_PREFIX) === 0;
  if (ours || askBarWasOurs) notifyAdviceListeners();
  askBarWasOurs = ours;
});

/* -------------------------------------------------------------------- writes */

function reportWriteFailure(reason: unknown, what: string): void {
  const err = reason as StatusError;
  if (err && err.status === 409) {
    const body = err.body as AdviceStaleResponse | undefined;
    fail((body && body.error) || "that advisory changed since you read it");
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
  view.openMenuKey = "";
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

/* ---------------------------------------------------------------- the menu */

function closeMenu(focusBack: boolean): void {
  const key = view.openMenuKey;
  if (!key) return;
  view.openMenuKey = "";
  notifyAdviceListeners();
  if (!focusBack) return;
  const opener = document.querySelector<HTMLElement>('[data-ctl="adv-more:' + CSS.escape(key) + '"]');
  if (opener) opener.focus({ preventScroll: true });
}

document.addEventListener("click", function (event) {
  if (!view.openMenuKey) return;
  const target = event.target as HTMLElement | null;
  if (target && target.closest && target.closest(".advice-menu, .advice-split")) return;
  closeMenu(false);
});

function handleMenuKey(menu: HTMLElement, event: KeyboardEvent): void {
  const items = Array.prototype.slice.call(menu.querySelectorAll("[role=menuitem]")) as HTMLElement[];
  const at = items.indexOf(document.activeElement as HTMLElement);
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeMenu(true);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = items[(at + step + items.length) % items.length];
    if (next) next.focus();
  } else if (event.key === "Tab") closeMenu(false);
}

function snoozeSplit(advisory: AdviceRow): HTMLElement {
  const box = make("span", "advice-split");
  const writing = !!view.writingKeys[advisory.key];
  box.appendChild(
    button(
      "snooze 1 h",
      function () {
        snoozeOrDismiss(advisory, 1);
      },
      { title: "hide it for 1 h of play time; it comes back sooner if it gets worse", label: "snooze " + ADVICE_WORD[advisory.kind] + " for 1 h of play", disabled: writing }
    )
  );
  const open = view.openMenuKey === advisory.key;
  const more = button(
    "▾",
    function () {
      view.openMenuKey = open ? "" : advisory.key;
      view.focusMenuOnRender = !open;
      notifyAdviceListeners();
    },
    { title: "snooze for longer, or until it gets worse", label: "more ways to hide " + ADVICE_WORD[advisory.kind], disabled: writing }
  );
  more.classList.add("advice-caret");
  more.setAttribute("aria-haspopup", "menu");
  more.setAttribute("aria-expanded", String(open));
  more.setAttribute("data-ctl", "adv-more:" + advisory.key);
  box.appendChild(more);
  if (!open) return box;
  const menu = make("div", "advice-menu");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "hide for");
  SNOOZES.forEach(function (snooze) {
    const item = make("button", "advice-menu-item", snooze[1]);
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.onclick = function (event) {
      event.stopPropagation();
      snoozeOrDismiss(advisory, snooze[0]);
    };
    menu.appendChild(item);
  });
  menu.onkeydown = function (event) {
    handleMenuKey(menu, event);
  };
  box.appendChild(menu);
  return box;
}

/* ----------------------------------------------------------------- the rows */

function mapAction(advisory: AdviceRow): HTMLElement | null {
  const spots = advisory.machines;
  if (!spots.length) return null;
  const layers = advisory.reveal;
  const one = spots.length === 1 ? spots[0]! : null;
  return button(
    "map",
    function () {
      goToMapThen(function () {
        if (one && one.instance && one.x_m !== null && one.y_m !== null) {
          showMachine(one.instance, one.name, one.x_m, one.y_m, { layers: layers });
          return;
        }
        const bbox = advisory.bbox_m;
        if (!bbox) return;
        const pad = 30;
        showBox([bbox[0]! - pad, bbox[1]! - pad, bbox[2]! + pad, bbox[3]! + pad], { layers: layers });
      });
    },
    { title: "fly the map to " + (one ? "it" : "them"), map: true, label: "show " + ADVICE_WORD[advisory.kind] + " " + advisory.subject + " on the map" }
  );
}

function actions(advisory: AdviceRow): HTMLElement {
  const box = make("span", "dash-acts advice-acts");
  const map = mapAction(advisory);
  if (map) box.appendChild(map);
  const seed = advisory.seed;
  if (seed && TRACED.indexOf(advisory.kind) >= 0) {
    const from = seed;
    box.appendChild(
      button(
        "trace",
        function () {
          goToMapThen(function () {
            startTrace(from, "up");
          });
        },
        { title: "draw what feeds it on the map", label: "trace what feeds " + advisory.subject }
      )
    );
  }
  if (advisory.kind === "power" || advisory.kind === "headroom") box.appendChild(link("power", "power", "btn"));
  if (advisory.plan) box.appendChild(link("planner/" + advisory.plan, "open plan", "btn"));
  if (advisory.state === "active") {
    box.appendChild(askButton({ kind: "advice", label: advisory.text, ref: advisory.key }, "adv-" + advisory.id));
    box.appendChild(snoozeSplit(advisory));
  } else {
    box.appendChild(
      button(
        "restore",
        function () {
          restore(advisory);
        },
        { title: "show it again", label: "restore " + ADVICE_WORD[advisory.kind] + ": " + advisory.text, disabled: !!view.writingKeys[advisory.key] }
      )
    );
  }
  return box;
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

function rowView(advisory: AdviceRow): HTMLElement {
  const li = make("li", "advice-row" + (advisory.state === "active" ? "" : " advice-hidden"));
  const open = !!view.expandedKeys[advisory.key];
  const main = make("button", "advice-main");
  main.type = "button";
  main.setAttribute("aria-expanded", String(open));
  main.setAttribute("data-ctl", "adv:" + advisory.key);
  main.title = open ? "hide the evidence" : "show the evidence";
  main.appendChild(chip(ADVICE_WORD[advisory.kind] || advisory.kind, advisory.tone as "blocked" | "mid" | "muted"));
  if (view.newIds[advisory.id] && advisory.state === "active") main.appendChild(chip("new", "muted", "not on this page before"));
  if (advisory.back) main.appendChild(chip("back", "muted", "back: worse than when it was hidden"));
  main.appendChild(make("span", "advice-text", advisory.text));
  main.onclick = function () {
    view.expandedKeys[advisory.key] = !open;
    notifyAdviceListeners();
  };
  li.appendChild(main);
  li.appendChild(actions(advisory));
  if (open) {
    const lines = make("ul", "advice-lines");
    const facts = advisory.lines.slice();
    if (advisory.back) facts.unshift("back: worse than when it was hidden");
    if (advisory.state !== "active") facts.unshift(hiddenWords(advisory));
    if (!facts.length) facts.push("no more to it than the line above");
    facts.forEach(function (line) {
      lines.appendChild(make("li", "", line));
    });
    li.appendChild(lines);
  }
  return li;
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
  return kinds
    .map(function (kind) {
      return (perKind[kind]! > 1 ? perKind[kind] + " " : "") + ADVICE_WORD[kind];
    })
    .join(", ");
}

function textLink(text: string, action: () => void, expanded: boolean): HTMLButtonElement {
  const toggle = make("button", "advice-link", text);
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return toggle;
}

export interface AdviceFilter {
  factory: string;
}

function renderActiveAdvice(card: HTMLElement, active: AdviceRow[], filter?: AdviceFilter): void {
  if (!active.length) {
    empty(card, filter ? "nothing worth a look in this factory" : "nothing worth a look in this save");
    return;
  }
  const cut = shownRows(active);
  const list = make("ul", "advice-list");
  cut.shown.forEach(function (advisory) {
    list.appendChild(rowView(advisory));
  });
  card.appendChild(list);
  const line = make("p", "advice-foot");
  if (cut.rest.length) {
    line.appendChild(
      textLink("+" + cut.rest.length + " more (" + moreWords(cut.rest) + ")", function () {
        view.showAllActive = true;
        notifyAdviceListeners();
      }, false)
    );
  } else if (view.showAllActive && active.length > VISIBLE) {
    line.appendChild(
      textLink("show fewer", function () {
        view.showAllActive = false;
        notifyAdviceListeners();
      }, true)
    );
  }
  if (line.childNodes.length) card.appendChild(line);
}

function renderHiddenAdvice(card: HTMLElement, hidden: AdviceRow[]): void {
  if (!hidden.length) return;
  const foot = make("p", "advice-foot");
  foot.appendChild(
    textLink(hidden.length + " hidden · " + (view.showHidden ? "close" : "show"), function () {
      view.showHidden = !view.showHidden;
      notifyAdviceListeners();
    }, view.showHidden)
  );
  card.appendChild(foot);
  if (!view.showHidden) return;
  const list = make("ul", "advice-list");
  hidden.forEach(function (advisory) {
    list.appendChild(rowView(advisory));
  });
  card.appendChild(list);
}

function restoreAdviceFocus(card: HTMLElement): void {
  if (!view.openMenuKey || !view.focusMenuOnRender) return;
  const first = card.querySelector<HTMLElement>(".advice-menu [role=menuitem]");
  if (!first) return;
  view.focusMenuOnRender = false;
  first.focus({ preventScroll: true });
}

export function adviceCard(parent: HTMLElement, filter?: AdviceFilter): void {
  const card = make("section", "dash-card advice-card");
  const bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", WORDS.worthALook));
  card.appendChild(bar);
  parent.appendChild(card);
  if (askBarOwnerId().indexOf(ASK_PREFIX) === 0) renderAskBar(card);
  const data = view.world === state.world ? view.data : null;
  if (!data) {
    if (view.loadError) error(card, "the advisories", view.loadError, refetchAdvice);
    else loading(card, "advisories");
    return;
  }
  const inScope = function (advisory: AdviceRow): boolean {
    return !filter || (advisory.subject_kind === "factory" && advisory.subject === filter.factory);
  };
  const active = data.active.filter(inScope);
  const hidden = data.hidden.filter(inScope);
  bar.appendChild(make("span", "dash-muted advice-count", active.length + " · " + hidden.length + " hidden"));
  renderActiveAdvice(card, active, filter);
  renderHiddenAdvice(card, hidden);
  settleAskFocus(card);
  restoreAdviceFocus(card);
}

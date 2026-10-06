/* Advisors: the "worth a look" card on the Overview and on a factory's page, the snooze split
 * button, restore, and the store behind them. See docs/advisors_contract.md §8. */

import { get, latest, send } from "../api/client";
import { askButton, askOpener, onAsks, renderAskBar, settleAskFocus } from "./asks";
import { button, chip, empty, error, link, loading } from "../kit/dashkit";
import { make } from "../kit/dom";
import { onMap } from "../app/nav";
import { showBox, showMachine } from "../map/map-highlight";
import { registerFetch } from "../app/registry";
import { onSetting, spoilerQuery } from "../app/settings";
import { state } from "../app/state";
import { fail, friendly, note } from "../kit/toast";
import { startTrace } from "../map/tools/trace";
import { ADVICE_WORD, W } from "../kit/words";

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
  problem: "",
  world: "",
  fresh: {} as Record<string, boolean>,
  open: {} as Record<string, boolean>,
  more: false,
  showHidden: false,
  menu: "",
  menuEnter: false,
  busy: {} as Record<string, boolean>,
};
var listeners: Array<() => void> = [];

export function onAdvice(listener: () => void): void {
  listeners.push(listener);
}

function notify(): void {
  listeners.forEach(function (listener) {
    listener();
  });
}

function remembered(): Record<string, boolean> | null {
  try {
    var raw = localStorage.getItem(SEEN_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : null;
  } catch (ignored) {
    return null;
  }
}

function remember(ids: string[]): void {
  var all: Record<string, boolean> = {};
  ids.forEach(function (id) {
    all[id] = true;
  });
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(all));
  } catch (ignored) {
    /* storage is a convenience */
  }
}

function accept(body: AdviceResponse): void {
  var ids = body.active.concat(body.hidden).map(function (r) {
    return r.id;
  });
  var seen = remembered();
  if (!view.data || view.world !== state.world) {
    view.fresh = {};
    if (seen)
      body.active.forEach(function (r) {
        if (!seen![r.id]) view.fresh[r.id] = true;
      });
  } else {
    body.active.forEach(function (r) {
      if (!view.data!.active.concat(view.data!.hidden).some(function (o) { return o.id === r.id; })) view.fresh[r.id] = true;
    });
  }
  remember(ids);
  view.data = body;
  view.problem = "";
  view.world = state.world;
  notify();
}

function query(): string {
  return spoilerQuery();
}

export function refetchAdvice(): void {
  var ticket = latest("advice");
  get<AdviceResponse>(`${PATH}?${query()}`)
    .then(function (body) {
      if (ticket.fresh()) accept(body);
    })
    .catch(function (reason) {
      if (!ticket.fresh()) return;
      view.problem = friendly(reason);
      notify();
    });
}

export function onActivity(entry: { world: string; kind: string }): void {
  if (entry.world === state.world && entry.kind.indexOf("advice.") === 0) refetchAdvice();
}

registerFetch<AdviceResponse>({
  wave: "live",
  rank: 45,
  path: PATH,
  query: query,
  label: "advisories",
  clears: [],
  refilters: false,
  draw: accept,
  failed: function () {
    view.problem = "the save could not be read";
    notify();
  },
});

onSetting(refetchAdvice);

var mineBefore = false;
onAsks(function () {
  var mine = askOpener().indexOf(ASK_PREFIX) === 0;
  if (mine || mineBefore) notify();
  mineBefore = mine;
});

/* -------------------------------------------------------------------- writes */

function stale(reason: unknown, what: string): void {
  var err = reason as StatusError;
  if (err && err.status === 409) {
    var body = err.body as AdviceStaleResponse | undefined;
    fail((body && body.error) || "that advisory changed since you read it");
  } else fail(what + ": " + friendly(reason));
  refetchAdvice();
}

function playWords(hours: number): string {
  return hours < 1 ? Math.round(hours * 60) + " min" : hours + " h";
}

function hide(row: AdviceRow, hours: number | null): void {
  if (view.busy[row.key]) return;
  view.busy[row.key] = true;
  view.menu = "";
  var body = hours === null ? { key: row.key, mode: "dismiss", rev: row.rev } : { key: row.key, mode: "snooze", hours: hours, rev: row.rev };
  notify();
  send<AdviceRow & ApiError>("POST", HIDE, body, undefined, query())
    .then(function () {
      note(hours === null ? "hidden until it gets worse" : "snoozed for " + playWords(hours) + " of play");
      refetchAdvice();
    })
    .catch(function (reason) {
      stale(reason, "not hidden");
    })
    .then(function () {
      delete view.busy[row.key];
      notify();
    });
}

function restore(row: AdviceRow): void {
  if (view.busy[row.key]) return;
  view.busy[row.key] = true;
  notify();
  send<AdviceRestored>("DELETE", RESTORE, { rev: row.rev }, row.id)
    .then(function () {
      note("restored " + ADVICE_WORD[row.kind] + ": " + row.text);
      refetchAdvice();
    })
    .catch(function (reason) {
      stale(reason, "not restored");
    })
    .then(function () {
      delete view.busy[row.key];
      notify();
    });
}

/* ---------------------------------------------------------------- the menu */

function closeMenu(focusBack: boolean): void {
  var key = view.menu;
  if (!key) return;
  view.menu = "";
  notify();
  if (!focusBack) return;
  var opener = document.querySelector<HTMLElement>('[data-ctl="adv-more:' + CSS.escape(key) + '"]');
  if (opener) opener.focus({ preventScroll: true });
}

document.addEventListener("click", function (event) {
  if (!view.menu) return;
  var target = event.target as HTMLElement | null;
  if (target && target.closest && target.closest(".advice-menu, .advice-split")) return;
  closeMenu(false);
});

function moveIn(menu: HTMLElement, event: KeyboardEvent): void {
  var items = Array.prototype.slice.call(menu.querySelectorAll("[role=menuitem]")) as HTMLElement[];
  var at = items.indexOf(document.activeElement as HTMLElement);
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeMenu(true);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    var step = event.key === "ArrowDown" ? 1 : -1;
    var next = items[(at + step + items.length) % items.length];
    if (next) next.focus();
  } else if (event.key === "Tab") closeMenu(false);
}

function snoozeSplit(row: AdviceRow): HTMLElement {
  var box = make("span", "advice-split");
  var busy = !!view.busy[row.key];
  box.appendChild(
    button(
      "snooze 1 h",
      function () {
        hide(row, 1);
      },
      { title: "hide it for 1 h of play time; it comes back sooner if it gets worse", label: "snooze " + ADVICE_WORD[row.kind] + " for 1 h of play", disabled: busy }
    )
  );
  var open = view.menu === row.key;
  var more = button(
    "▾",
    function () {
      view.menu = open ? "" : row.key;
      view.menuEnter = !open;
      notify();
    },
    { title: "snooze for longer, or until it gets worse", label: "more ways to hide " + ADVICE_WORD[row.kind], disabled: busy }
  );
  more.classList.add("advice-caret");
  more.setAttribute("aria-haspopup", "menu");
  more.setAttribute("aria-expanded", String(open));
  more.setAttribute("data-ctl", "adv-more:" + row.key);
  box.appendChild(more);
  if (!open) return box;
  var menu = make("div", "advice-menu");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "hide for");
  SNOOZES.forEach(function (s) {
    var item = make("button", "advice-menu-item", s[1]);
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.onclick = function (event) {
      event.stopPropagation();
      hide(row, s[0]);
    };
    menu.appendChild(item);
  });
  menu.onkeydown = function (event) {
    moveIn(menu, event);
  };
  box.appendChild(menu);
  return box;
}

/* ----------------------------------------------------------------- the rows */

function mapAction(row: AdviceRow): HTMLElement | null {
  var spots = row.machines;
  if (!spots.length) return null;
  var layers = row.reveal;
  var one = spots.length === 1 ? spots[0]! : null;
  return button(
    "map",
    function () {
      onMap(function () {
        if (one && one.instance && one.x_m !== null && one.y_m !== null) {
          showMachine(one.instance, one.name, one.x_m, one.y_m, { layers: layers });
          return;
        }
        var b = row.bbox_m;
        if (!b) return;
        var pad = 30;
        showBox([b[0]! - pad, b[1]! - pad, b[2]! + pad, b[3]! + pad], { layers: layers });
      });
    },
    { title: "fly the map to " + (one ? "it" : "them"), map: true, label: "show " + ADVICE_WORD[row.kind] + " " + row.subject + " on the map" }
  );
}

function actions(row: AdviceRow): HTMLElement {
  var box = make("span", "dash-acts advice-acts");
  var map = mapAction(row);
  if (map) box.appendChild(map);
  var seed = row.seed;
  if (seed && TRACED.indexOf(row.kind) >= 0) {
    var from = seed;
    box.appendChild(
      button(
        "trace",
        function () {
          onMap(function () {
            startTrace(from, "up");
          });
        },
        { title: "draw what feeds it on the map", label: "trace what feeds " + row.subject }
      )
    );
  }
  if (row.kind === "power" || row.kind === "headroom") box.appendChild(link("power", "power", "btn"));
  if (row.plan) box.appendChild(link("planner/" + row.plan, "open plan", "btn"));
  if (row.state === "active") {
    box.appendChild(askButton({ kind: "advice", label: row.text, ref: row.key }, "adv-" + row.id));
    box.appendChild(snoozeSplit(row));
  } else {
    box.appendChild(
      button(
        "restore",
        function () {
          restore(row);
        },
        { title: "show it again", label: "restore " + ADVICE_WORD[row.kind] + ": " + row.text, disabled: !!view.busy[row.key] }
      )
    );
  }
  return box;
}

function hiddenWords(row: AdviceRow): string {
  var who = row.by === "chat" ? " by chat" : "";
  var d = view.data;
  if (row.state === "snoozed" && row.until_play_s !== null && d) {
    var left = Math.max(0, row.until_play_s - d.play_s) / 3600;
    return "snoozed" + who + ", " + (left < 1 ? Math.max(1, Math.round(left * 60)) + " min" : Math.round(left * 10) / 10 + " h") + " of play left";
  }
  return "hidden" + who + " until it gets worse";
}

function rowView(row: AdviceRow): HTMLElement {
  var li = make("li", "advice-row" + (row.state === "active" ? "" : " advice-hidden"));
  var open = !!view.open[row.key];
  var main = make("button", "advice-main");
  main.type = "button";
  main.setAttribute("aria-expanded", String(open));
  main.setAttribute("data-ctl", "adv:" + row.key);
  main.title = open ? "hide the evidence" : "show the evidence";
  main.appendChild(chip(ADVICE_WORD[row.kind] || row.kind, row.tone as "blocked" | "mid" | "muted"));
  if (view.fresh[row.id] && row.state === "active") main.appendChild(chip("new", "muted", "not on this page before"));
  if (row.back) main.appendChild(chip("back", "muted", "back: worse than when it was hidden"));
  main.appendChild(make("span", "advice-text", row.text));
  main.onclick = function () {
    view.open[row.key] = !open;
    notify();
  };
  li.appendChild(main);
  li.appendChild(actions(row));
  if (open) {
    var lines = make("ul", "advice-lines");
    var facts = row.lines.slice();
    if (row.back) facts.unshift("back: worse than when it was hidden");
    if (row.state !== "active") facts.unshift(hiddenWords(row));
    if (!facts.length) facts.push("no more to it than the line above");
    facts.forEach(function (line) {
      lines.appendChild(make("li", "", line));
    });
    li.appendChild(lines);
  }
  return li;
}

function shownRows(rows: AdviceRow[]): { shown: AdviceRow[]; rest: AdviceRow[] } {
  if (view.more) return { shown: rows, rest: [] };
  var per: Record<string, number> = {};
  var shown: AdviceRow[] = [];
  var rest: AdviceRow[] = [];
  rows.forEach(function (row) {
    per[row.kind] = (per[row.kind] || 0) + 1;
    if (per[row.kind]! <= PER_KIND && shown.length < VISIBLE) shown.push(row);
    else rest.push(row);
  });
  return { shown: shown, rest: rest };
}

function moreWords(rest: AdviceRow[]): string {
  var kinds: string[] = [];
  var n: Record<string, number> = {};
  rest.forEach(function (r) {
    if (!n[r.kind]) kinds.push(r.kind);
    n[r.kind] = (n[r.kind] || 0) + 1;
  });
  return kinds
    .map(function (k) {
      return (n[k]! > 1 ? n[k] + " " : "") + ADVICE_WORD[k];
    })
    .join(", ");
}

function textLink(text: string, action: () => void, expanded: boolean): HTMLButtonElement {
  var b = make("button", "advice-link", text);
  b.type = "button";
  b.setAttribute("aria-expanded", String(expanded));
  b.onclick = function (event) {
    event.stopPropagation();
    action();
  };
  return b;
}

export interface AdviceFilter {
  factory: string;
}

export function adviceCard(parent: HTMLElement, filter?: AdviceFilter): void {
  var card = make("section", "dash-card advice-card");
  var bar = make("div", "dash-title");
  bar.appendChild(make("h2", "dash-h", W.worthALook));
  card.appendChild(bar);
  parent.appendChild(card);
  if (askOpener().indexOf(ASK_PREFIX) === 0) renderAskBar(card);
  var d = view.world === state.world ? view.data : null;
  if (!d) {
    if (view.problem) error(card, "the advisories", view.problem, refetchAdvice);
    else loading(card, "advisories");
    return;
  }
  var mine = function (r: AdviceRow): boolean {
    return !filter || (r.subject_kind === "factory" && r.subject === filter.factory);
  };
  var active = d.active.filter(mine);
  var hidden = d.hidden.filter(mine);
  bar.appendChild(make("span", "dash-muted advice-count", active.length + " · " + hidden.length + " hidden"));
  if (!active.length) empty(card, filter ? "nothing worth a look in this factory" : "nothing worth a look in this save");
  else {
    var cut = shownRows(active);
    var list = make("ul", "advice-list");
    cut.shown.forEach(function (row) {
      list.appendChild(rowView(row));
    });
    card.appendChild(list);
    var line = make("p", "advice-foot");
    if (cut.rest.length) {
      line.appendChild(
        textLink("+" + cut.rest.length + " more (" + moreWords(cut.rest) + ")", function () {
          view.more = true;
          notify();
        }, false)
      );
    } else if (view.more && active.length > VISIBLE) {
      line.appendChild(
        textLink("show fewer", function () {
          view.more = false;
          notify();
        }, true)
      );
    }
    if (line.childNodes.length) card.appendChild(line);
  }
  if (hidden.length) {
    var foot = make("p", "advice-foot");
    foot.appendChild(
      textLink(hidden.length + " hidden · " + (view.showHidden ? "close" : "show"), function () {
        view.showHidden = !view.showHidden;
        notify();
      }, view.showHidden)
    );
    card.appendChild(foot);
    if (view.showHidden) {
      var shut = make("ul", "advice-list");
      hidden.forEach(function (row) {
        shut.appendChild(rowView(row));
      });
      card.appendChild(shut);
    }
  }
  settleAskFocus(card);
  if (view.menu && view.menuEnter) {
    var first = card.querySelector<HTMLElement>(".advice-menu [role=menuitem]");
    if (first) {
      view.menuEnter = false;
      first.focus({ preventScroll: true });
    }
  }
}

/* The Planner tab: mounting, following chat, and the focus heartbeat. See
 * docs/planner_slice_contract.md §12. */

import { get, send } from "./api";
import { loading } from "./dashkit";
import { keepFocus, make } from "./dom";
import { go } from "./nav";
import { onVitals } from "./panel";
import { renderBench } from "./planner-bench";
import { actorWord, bench, changed, followHead, forgetSolves, inbox, loadItems, onBench, openPlan, redoLast, reset, resyncHead, sav, undoLast } from "./planner-core";
import { loadList, planTitle, renderList } from "./planner-list";
import { choice, onSetting } from "./settings";
import { state } from "./state";
import { note, offer } from "./toast";

import type { ActivityResponse, FocusResponse } from "./api-shapes";
import type { ActivityEvent, PlansEvent, Selection } from "./planner-core";

var FOCUS_DEBOUNCE_MS = 1000;
var HEARTBEAT_MS = 15000;

var root = make("div", "plan-root");
var mounted: string | null = null;
var held: Array<() => void> = [];
var heldDraw = false;
var seen: Record<string, boolean> = {};
var heard = 0;
var focusTimer = 0;
var drawTimer = 0;
var pressed = false;

function subject(): string | null {
  var dash = state.dash;
  if (dash === "planner") return "";
  if (dash.indexOf("planner/") === 0) return dash.slice("planner/".length);
  return null;
}

function typing(): boolean {
  var active = document.activeElement as HTMLInputElement | null;
  if (!active || !/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return false;
  if (!root.contains(active)) return true;
  return active.tagName !== "SELECT" && active.value !== active.defaultValue;
}

function hint(): void {
  var line = root.querySelector(".plan-held");
  if (line) line.textContent = held.length || heldDraw ? "an update is waiting: finish the edit to see it" : "";
}

function whenIdle(action: () => void): void {
  if (typing()) {
    held.push(action);
    hint();
  } else action();
}

function flush(): void {
  if (typing()) return;
  var actions = held;
  held = [];
  actions.forEach(function (action) {
    action();
  });
  if (heldDraw) draw();
}

function select(s: Selection): void {
  bench.selection = s;
  changed();
  scheduleFocus();
}

function later(): void {
  clearTimeout(drawTimer);
  drawTimer = window.setTimeout(draw, 0);
}

function draw(): void {
  if (!root.isConnected) return;
  if (pressed || (typing() && root.contains(document.activeElement))) {
    heldDraw = true;
    if (!pressed) hint();
    return;
  }
  heldDraw = false;
  keepFocus(root, function () {
    root.textContent = "";
    if (mounted) renderBench(root, select);
    else renderList(root);
  });
  if (held.length && !typing()) flush();
}

export function renderPlanner(body: HTMLElement, key: string): void {
  if (!state.world) {
    body.textContent = "";
    loading(body, "the world");
    return;
  }
  if (root.parentNode !== body) {
    body.textContent = "";
    body.appendChild(root);
    mounted = null;
  }
  loadItems();
  if (bench.world !== state.world) {
    reset(bench.key);
    mounted = null;
  }
  if (key !== mounted) {
    mounted = key;
    if (key) openPlan(key);
    else {
      reset("");
      loadList();
    }
    scheduleFocus();
  }
  draw();
}

function focusBody(): Record<string, unknown> {
  var at = subject();
  var planner = at !== null;
  var cut = state.dash.indexOf("/");
  return {
    view: state.dash === "" ? "map" : planner ? "planner" : "dashboard",
    dash: state.dash,
    plan: planner && at && bench.key === at ? at : null,
    rev: planner && at && bench.plan ? bench.plan.rev : null,
    tab: planner ? (at ? "workbench" : "list") : cut < 0 ? state.dash : state.dash.slice(0, cut),
    selection: planner && at ? bench.selection : null,
    follow: choice("follow"),
    sav: sav(),
  };
}

function sendFocus(): void {
  if (!state.world) return;
  send<FocusResponse>("PUT", "/api/ui/focus", focusBody()).catch(function () {});
}

function scheduleFocus(): void {
  clearTimeout(focusTimer);
  focusTimer = window.setTimeout(sendFocus, FOCUS_DEBOUNCE_MS);
}

function news(ts: number): boolean {
  return ts * 1000 >= state.opened - 2000;
}

export function onPlansEvent(event: PlansEvent): void {
  if (event.world !== state.world) return;
  whenIdle(function () {
    if (subject() === "") loadList();
    followHead(event);
  });
}

function openFromChat(entry: ActivityEvent): void {
  inbox.card = entry;
  if (entry.plan && !planTitle(entry.plan)) loadList();
  var at = subject();
  if (at) changed();
  else go("planner");
}

export function onActivityEvent(entry: ActivityEvent): void {
  if (entry.world !== state.world || entry.actor.kind === "page" || seen[entry.id]) return;
  seen[entry.id] = true;
  heard = Math.max(heard, entry.ts);
  if (!news(entry.ts)) return;
  var mode = choice("follow");
  if (mode === "off") return;
  var who = actorWord(entry.actor);
  if (entry.kind === "plan.solve") {
    if (mode === "toasts") {
      offer(who + " solved: " + entry.text, "open", function () {
        openFromChat(entry);
      });
    } else {
      whenIdle(function () {
        openFromChat(entry);
      });
    }
  } else if (entry.kind === "plan.view" && entry.plan) {
    var key = entry.plan;
    if (mode === "toasts") {
      offer(who + ": " + entry.text, "open", function () {
        go("planner/" + key);
      });
    } else if (subject() !== key) {
      whenIdle(function () {
        note(who + " opened “" + (entry.name || planTitle(key) || "a plan") + "” (Settings, follow chat)");
        go("planner/" + key);
      });
    }
  }
}

export function resyncPlanner(): void {
  if (subject() === "") loadList();
  resyncHead();
  var since = Math.max(heard, state.opened / 1000 - 2);
  get<ActivityResponse>(`/api/activity?since=${since}`)
    .then(function (body) {
      body.entries.forEach(function (row) {
        if (row.source === "journal") onActivityEvent({ ...row, world: state.world });
      });
    })
    .catch(function () {});
}

export function onSaveEvent(): void {
  if (bench.plan) forgetSolves();
}

function keys(event: KeyboardEvent): void {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (!subject() || !bench.plan) return;
  var active = document.activeElement as HTMLElement | null;
  if (active && (/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName) || active.isContentEditable)) return;
  event.preventDefault();
  if (event.shiftKey) redoLast();
  else undoLast();
}

function wire(): void {
  onBench(later);
  onVitals(function () {
    if (mounted) later();
  });
  document.addEventListener("pointerdown", function () {
    pressed = true;
  });
  document.addEventListener("pointerup", function () {
    setTimeout(function () {
      pressed = false;
      if (heldDraw) draw();
    }, 0);
  });
  onSetting(scheduleFocus);
  document.addEventListener("keydown", keys);
  document.addEventListener("focusout", function () {
    setTimeout(flush, 0);
  });
  window.addEventListener("hashchange", scheduleFocus);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") scheduleFocus();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") sendFocus();
  }, HEARTBEAT_MS);
}

wire();

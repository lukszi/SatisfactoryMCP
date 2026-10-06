/* The Planner tab: mounting, following chat, and the focus heartbeat. See
 * docs/planner_slice_contract.md §12. */

import { get, onToken, send } from "../../api/client";
import { askOpen, closeBar, onAsks, renderAskBar, settleAskFocus } from "../../chat/asks";
import { loading } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { keepFocus } from "../../kit/focus";
import { onReload } from "../../app/load";
import { dashParts, go } from "../../app/nav";
import { onVitals } from "../../app/vitals";
import { renderBench } from "./planner-bench";
import { trackDash } from "./planner-address";
import { dropFeeders, forgetSolves, hideAlternates, loadAlternates, loadTrack, openRevision, saveToken, showAlternates, stageHeadroom } from "./planner-reads";
import { actorWord, bench, changed, inbox, loadItems, onBench, pendingFocus, resetBench } from "./planner-state";
import { followHead, openPlan, redoLast, resyncHead, undoLast } from "./planner-writes";
import { loadActivity } from "./planner-history";
import { loadList, planTitle, renderList } from "./planner-list";
import { onPins } from "../../chat/pins";
import { clearPick } from "./planner-result";
import { showGhost } from "./planner-site";
import { focusStartup, revealStage, settleTrackFocus } from "./planner-track";
import { onBiomass } from "../power-ledger";
import { onSelect, selected, selectionRef } from "../../app/selection";
import { onSetting, settingChoice, settingNumber, settingOn } from "../../app/settings";
import { state } from "../../app/state";
import { notify, offer } from "../../kit/toast";
import { objectiveText } from "../../kit/words";

import type { ActivityResponse, FocusResponse, FocusSelection, PlansResponse } from "../../api/shapes";
import type { ActivityEvent, PlansEvent } from "./planner-state";

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
var refocus = "";
var focusSig = "";
var viewSent: string | null = null;
var startupFocus = false;
var stageReveal = 0;

interface Address {
  key: string;
  view: number;
  alt: string;
  track: boolean;
  site: boolean;
  stage: number;
}

function powerDefaults(): string {
  return settingNumber("paybackHours") + "|" + String(settingOn("overclockLast"));
}

function parts(at: string): Address {
  var rest = dashParts("planner/" + at).rest;
  var key = rest[0] || "";
  var m = /^v(\d+)$/.exec(rest[1] || "");
  var alt = rest[1] === "alt" && rest[2] ? rest.slice(2).join("/") : "";
  var track = rest[1] === "track";
  var stage = track && /^\d+$/.test(rest[2] || "") ? Number(rest[2]) : 0;
  return { key: key, view: m ? Number(m[1]) : 0, alt: alt, track: track, site: rest[1] === "site", stage: stage };
}

function syncTab(wanted: Address): void {
  if (wanted.alt) return;
  if (wanted.track) {
    var entering = bench.tab !== "track";
    bench.tab = "track";
    bench.track.stage = wanted.stage;
    if (entering) loadTrack();
  } else if (wanted.site) bench.tab = "site";
  else if (bench.tab === "track" || bench.tab === "site") bench.tab = "build list";
}

function trackShowing(): boolean {
  return !!subject() && bench.tab === "track" && !!bench.plan && !bench.viewedRev;
}

function subject(): string | null {
  var at = dashParts();
  if (at.tab !== "planner") return null;
  return parts(at.subject).key;
}

function altDash(key: string, item: string): string {
  return "planner/" + key + "/alt/" + item;
}

function goAlt(plan: string, at: string): void {
  var switching = subject() === plan && dashParts().rest[1] === "alt";
  if (!switching) bench.alternatesCloseGoesBack = false;
  go(at, switching);
}

function closeAlternates(): void {
  var back = bench.alternatesCloseGoesBack && dashParts().rest[1] === "alt";
  refocus = hideAlternates();
  if (back) history.back();
  else go(bench.tab === "track" ? trackDash(bench.key, bench.track.stage) : "planner/" + bench.key, true);
  changed();
}

function typing(): boolean {
  var active = document.activeElement as HTMLInputElement | null;
  if (!active || !/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return false;
  if (!root.contains(active)) return true;
  return active.tagName !== "SELECT" && active.value !== active.defaultValue;
}

function inField(): boolean {
  var active = document.activeElement;
  return !!active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
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

function select(s: FocusSelection): void {
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
    renderAskBar(root);
    if (mounted) renderBench(root, select, closeAlternates);
    else renderList(root);
  });
  settleAskFocus(root);
  if (startupFocus && trackShowing() && focusStartup(root)) startupFocus = false;
  settleTrackFocus(root);
  if (stageReveal && trackShowing() && bench.track.data && !bench.track.asked) {
    if (!inField() && bench.track.stage === stageReveal) revealStage(root, stageReveal);
    stageReveal = 0;
  }
  var alt = bench.alternates;
  var shut = alt && alt.enter && parts(dashParts().subject).alt === alt.item ? root.querySelector<HTMLElement>('[data-ctl="alt-close"]') : null;
  if (alt && shut) {
    alt.enter = false;
    shut.focus();
  }
  var target = pendingFocus.ctl ? root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(pendingFocus.ctl) + '"]') : null;
  if (target || Date.now() > pendingFocus.until) pendingFocus.ctl = "";
  if (target) target.focus({ preventScroll: true });
  if (refocus) {
    var back = root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(refocus) + '"]');
    if (back) {
      refocus = "";
      back.focus({ preventScroll: true });
    }
  }
  var sig = JSON.stringify([bench.tab, bench.alternates ? bench.alternates.item : "", bench.selection, bench.plan ? bench.plan.rev : null]);
  if (sig !== focusSig) {
    focusSig = sig;
    scheduleFocus();
  }
  if (held.length && !typing()) flush();
}

export function renderPlanner(body: HTMLElement, at: string): void {
  var wanted = parts(at);
  var key = wanted.key;
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
    resetBench(bench.key);
    mounted = null;
  }
  if (key !== mounted) {
    mounted = key;
    if (key) openPlan(key);
    else {
      resetBench("");
      loadList();
      loadActivity();
    }
    scheduleFocus();
  }
  if (key) syncTab(wanted);
  if (key && bench.viewedRev !== wanted.view) openRevision(wanted.view);
  if (key && wanted.alt) showAlternates(wanted.alt);
  else if (bench.alternates) refocus = hideAlternates();
  draw();
}

function shared(): FocusSelection | null {
  var s = selected();
  return s ? { kind: s.kind, label: s.label, ref: selectionRef(s) } : null;
}

function altSelection(): FocusSelection | null {
  var alt = bench.alternates;
  if (!alt) return null;
  return { kind: "item", label: alt.data ? alt.data.name : alt.item, ref: alt.item };
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
    tab: planner ? (at ? (bench.tab === "graph" || bench.tab === "track" || bench.tab === "site" ? bench.tab : "workbench") : "list") : cut < 0 ? state.dash : state.dash.slice(0, cut),
    selection: planner && at ? altSelection() || bench.selection : shared(),
    follow: settingChoice("follow"),
    sav: state.saveToken || saveToken(),
  };
}

function sendFocus(): void {
  if (!state.world) return;
  send<FocusResponse>("PUT", "/api/ui/focus", focusBody()).catch(function () {});
}

export function viewFocus(): void {
  if (!state.world || state.dash === viewSent) return;
  viewSent = state.dash;
  clearTimeout(focusTimer);
  sendFocus();
}

function scheduleFocus(): void {
  clearTimeout(focusTimer);
  focusTimer = window.setTimeout(sendFocus, FOCUS_DEBOUNCE_MS);
}

function news(ts: number): boolean {
  return ts * 1000 >= state.openedAtMs - 2000;
}

export function onPlansEvent(event: PlansEvent): void {
  if (event.world !== state.world) return;
  whenIdle(function () {
    if (subject() === "") {
      loadList();
      loadActivity();
    }
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

function named(key: string, given: string | null | undefined, then: (name: string) => void): void {
  var known = given || (bench.key === key && bench.plan ? bench.plan.name : "") || planTitle(key);
  if (known) {
    then(known);
    return;
  }
  get<PlansResponse>("/api/plans").then(
    function (data) {
      var row = data.index.filter(function (r) {
        return r.key === key;
      })[0];
      then(row ? row.name : "a plan");
    },
    function () {
      then("a plan");
    }
  );
}

export function onActivityEvent(entry: ActivityEvent): void {
  if (entry.world !== state.world || entry.actor.kind === "page" || seen[entry.id]) return;
  seen[entry.id] = true;
  if (subject() === "") loadActivity();
  heard = Math.max(heard, entry.ts);
  if (!news(entry.ts)) return;
  var mode = settingChoice("follow");
  if (mode === "off") return;
  var who = actorWord(entry.actor);
  var args = entry.args || {};
  if (entry.kind === "plan.view" && entry.plan && args.view === "track") {
    var tracked = entry.plan;
    var stage = typeof args.stage === "number" && args.stage >= 1 ? Math.floor(args.stage) : 0;
    var there = trackDash(tracked, stage);
    var startup = args.section === "startup";
    var open = function () {
      startupFocus = startup;
      stageReveal = startup ? 0 : stage;
      if (state.dash === there) changed();
      else go(there);
    };
    if (mode === "toasts") {
      named(tracked, entry.name, function (called) {
        offer(who + " looked at the track of “" + called + "”", "open", open);
      });
    } else {
      whenIdle(function () {
        var moving = state.dash !== there;
        open();
        if (moving) {
          named(tracked, entry.name, function (called) {
            notify(who + " opened the track of “" + called + "” (Settings, follow chat)");
          });
        }
      });
    }
    return;
  }
  if (entry.kind === "plan.view" && entry.plan && args.view === "site") {
    var sited = entry.plan;
    var spot = "planner/" + sited + "/site";
    var look = function () {
      showGhost(sited, args, who);
      if (state.dash !== spot) go(spot);
    };
    if (mode === "toasts") {
      named(sited, entry.name, function (called) {
        offer(who + " looked at a spot for “" + called + "”", "open", look);
      });
    } else whenIdle(look);
    return;
  }
  if (entry.kind === "plan.view" && entry.plan && args.view === "alternates" && typeof args.item === "string") {
    var plan = entry.plan;
    var at = altDash(plan, args.item);
    if (mode === "toasts") {
      offer(who + " " + entry.text, "open", function () {
        goAlt(plan, at);
      });
    } else if (state.dash !== at) {
      whenIdle(function () {
        goAlt(plan, at);
      });
    }
    return;
  }
  if (entry.kind === "plan.solve") {
    if (mode === "toasts") {
      offer(who + " " + objectiveText(entry.text), "open", function () {
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
        notify(who + " opened “" + (entry.name || planTitle(key) || "a plan") + "” (Settings, follow chat)");
        go("planner/" + key);
      });
    }
  }
}

export function resyncPlanner(replay: (entries: ActivityEvent[]) => void): void {
  if (subject() === "") {
    loadList();
    loadActivity();
  }
  resyncHead();
  if (trackShowing()) loadTrack();
  var since = Math.max(heard, state.openedAtMs / 1000);
  get<ActivityResponse>(`/api/activity?since=${since}`)
    .then(function (body) {
      replay(
        body.entries
          .filter(function (row) {
            return row.source === "journal";
          })
          .map(function (row) {
            return { ...row, world: state.world };
          })
      );
    })
    .catch(function () {});
}

export function onSaveEvent(): void {
  if (bench.plan) forgetSolves();
  if (bench.alternates) loadAlternates();
  dropFeeders();
  if (trackShowing()) loadTrack();
}

onReload(function () {
  scheduleFocus();
  if (bench.world !== state.world) return;
  bench.track.data = null;
  onSaveEvent();
});

export function onNotesEvent(): void {
  if (trackShowing() && bench.plan && bench.plan.factory) loadTrack();
}

function escape(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !root.isConnected || subject() === null) return;
  if (askOpen()) {
    event.preventDefault();
    closeBar();
    return;
  }
  if (!subject()) return;
  if (bench.alternates) {
    event.preventDefault();
    closeAlternates();
  } else if (clearPick()) event.preventDefault();
}

function keys(event: KeyboardEvent): void {
  var active = document.activeElement as HTMLElement | null;
  if (active && (/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName) || active.isContentEditable)) return;
  if (event.key === "Escape") {
    escape(event);
    return;
  }
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (!subject() || !bench.plan) return;
  event.preventDefault();
  if (event.shiftKey) redoLast();
  else undoLast();
}

function wire(): void {
  onBench(later);
  onPins(later);
  onAsks(later);
  onBiomass(function () {
    dropFeeders();
    if (trackShowing()) loadTrack();
  });
  var powerWas = powerDefaults();
  onSetting(function () {
    if (powerDefaults() === powerWas) return;
    powerWas = powerDefaults();
    if (bench.plan) forgetSolves();
  });
  var headroomWas = stageHeadroom();
  onSetting(function () {
    if (stageHeadroom() === headroomWas) return;
    headroomWas = stageHeadroom();
    if (trackShowing()) loadTrack();
  });
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
  onToken(scheduleFocus);
  onSetting(scheduleFocus);
  onSelect(scheduleFocus);
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

/* The Planner tab: mounting, redrawing without stepping on an edit, and following chat. See
 * docs/planner_slice_contract.md §12. */

import { get } from "../../api/client";
import { isAskBarOpen, closeBar, onAsks, renderAskBar, settleAskFocus } from "../../chat/asks";
import { loading } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { keepFocus } from "../../kit/focus";
import { onReload } from "../../app/load";
import { dashParts, go } from "../../app/nav";
import { onVitals } from "../../app/vitals";
import { renderBench } from "./planner-bench";
import { altDash, openPlanKey, parsePlannerAddress, trackDash } from "./planner-address";
import { dropFeeders, forgetSolves, hideAlternates, loadAlternates, loadTrack, openRevision, showAlternates, stageHeadroom } from "./planner-reads";
import { actorWord, bench, changed, inbox, loadItems, onBench, pendingFocus, resetBench } from "./planner-state";
import { followHead, openPlan, redoLast, resyncHead, undoLast } from "./planner-writes";
import { scheduleFocus, wireFocusReports } from "./planner-focus";
import { loadActivity } from "./planner-history";
import { renderList } from "./planner-list";
import { loadList, planTitle } from "./planner-plan-index";
import { onPins } from "../../chat/pins";
import { clearPick } from "./planner-result";
import { showGhost } from "./planner-site";
import { focusStartup, revealStage, settleTrackFocus } from "./planner-track";
import { onBiomass } from "../power-ledger";
import { onSetting, settingChoice, settingNumber, settingOn } from "../../app/settings";
import { isSincePageOpened, state } from "../../app/state";
import { notify, offer } from "../../kit/toast";
import { objectiveText } from "../../kit/words";

import type { ActivityResponse, FocusSelection, PlansResponse } from "../../api/shapes";
import type { PlannerAddress } from "./planner-address";
import type { ActivityEvent, PlansEvent } from "./planner-state";

var FORM_FIELD = /^(INPUT|TEXTAREA|SELECT)$/;

var root = make("div", "plan-root");
var mountedKey: string | null = null;
var heldActions: Array<() => void> = [];
var drawPending = false;
var seenActivityIds: Record<string, boolean> = {};
var lastActivityTs = 0;
var drawTimer = 0;
var pointerDown = false;
var refocusCtl = "";
var lastFocusSignature = "";
var focusStartupPending = false;
var stageToReveal = 0;

/** One chat activity the page may follow, and whether the follow setting only offers it. */
interface Followed {
  entry: ActivityEvent;
  who: string;
  args: Record<string, unknown>;
  toastsOnly: boolean;
}

function powerDefaults(): string {
  return settingNumber("paybackHours") + "|" + String(settingOn("overclockLast"));
}

function syncTab(wanted: PlannerAddress): void {
  if (wanted.alternatesItem) return;
  if (wanted.track) {
    var entering = bench.tab !== "track";
    bench.tab = "track";
    bench.track.stage = wanted.stage;
    if (entering) loadTrack();
  } else if (wanted.site) bench.tab = "site";
  else if (bench.tab === "track" || bench.tab === "site") bench.tab = "build list";
}

function trackShowing(): boolean {
  return !!openPlanKey() && bench.tab === "track" && !!bench.plan && !bench.viewedRev;
}

function goToAlternates(plan: string, at: string): void {
  var switching = openPlanKey() === plan && dashParts().rest[1] === "alt";
  if (!switching) bench.alternatesCloseGoesBack = false;
  go(at, switching);
}

function closeAlternates(): void {
  var back = bench.alternatesCloseGoesBack && dashParts().rest[1] === "alt";
  refocusCtl = hideAlternates();
  if (back) history.back();
  else go(bench.tab === "track" ? trackDash(bench.key, bench.track.stage) : "planner/" + bench.key, true);
  changed();
}

function isFormField(el: Element | null): boolean {
  return !!el && (FORM_FIELD.test(el.tagName) || (el as HTMLElement).isContentEditable);
}

/** An edit is open: a field elsewhere has focus, or a planner field holds an unsaved value. */
function typing(): boolean {
  var active = document.activeElement as HTMLInputElement | null;
  if (!active || !FORM_FIELD.test(active.tagName)) return false;
  if (!root.contains(active)) return true;
  return active.tagName !== "SELECT" && active.value !== active.defaultValue;
}

function inField(): boolean {
  return isFormField(document.activeElement);
}

function showHeldHint(): void {
  var line = root.querySelector(".plan-held");
  if (line) line.textContent = heldActions.length || drawPending ? "an update is waiting: finish the edit to see it" : "";
}

function whenIdle(action: () => void): void {
  if (typing()) {
    heldActions.push(action);
    showHeldHint();
  } else action();
}

function runHeldActions(): void {
  if (typing()) return;
  var actions = heldActions;
  heldActions = [];
  actions.forEach(function (action) {
    action();
  });
  if (drawPending) draw();
}

function selectInPlan(selection: FocusSelection): void {
  bench.selection = selection;
  changed();
  scheduleFocus();
}

function scheduleDraw(): void {
  clearTimeout(drawTimer);
  drawTimer = window.setTimeout(draw, 0);
}

function revealTrackTargets(): void {
  if (focusStartupPending && trackShowing() && focusStartup(root)) focusStartupPending = false;
  settleTrackFocus(root);
  if (stageToReveal && trackShowing() && bench.track.data && !bench.track.asked) {
    if (!inField() && bench.track.stage === stageToReveal) revealStage(root, stageToReveal);
    stageToReveal = 0;
  }
}

function focusCtl(ctl: string): HTMLElement | null {
  return root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(ctl) + '"]');
}

function restoreFocus(): void {
  var drawer = bench.alternates;
  var shut = drawer && drawer.enter && parsePlannerAddress(dashParts().subject).alternatesItem === drawer.item ? root.querySelector<HTMLElement>('[data-ctl="alt-close"]') : null;
  if (drawer && shut) {
    drawer.enter = false;
    shut.focus();
  }
  var target = pendingFocus.ctl ? focusCtl(pendingFocus.ctl) : null;
  if (target || Date.now() > pendingFocus.until) pendingFocus.ctl = "";
  if (target) target.focus({ preventScroll: true });
  if (refocusCtl) {
    var back = focusCtl(refocusCtl);
    if (back) {
      refocusCtl = "";
      back.focus({ preventScroll: true });
    }
  }
}

function reportFocusIfChanged(): void {
  var signature = JSON.stringify([bench.tab, bench.alternates ? bench.alternates.item : "", bench.selection, bench.plan ? bench.plan.rev : null]);
  if (signature === lastFocusSignature) return;
  lastFocusSignature = signature;
  scheduleFocus();
}

function draw(): void {
  if (!root.isConnected) return;
  if (pointerDown || (typing() && root.contains(document.activeElement))) {
    drawPending = true;
    if (!pointerDown) showHeldHint();
    return;
  }
  drawPending = false;
  keepFocus(root, function () {
    root.textContent = "";
    renderAskBar(root);
    if (mountedKey) renderBench(root, selectInPlan, closeAlternates);
    else renderList(root);
  });
  settleAskFocus(root);
  revealTrackTargets();
  restoreFocus();
  reportFocusIfChanged();
  if (heldActions.length && !typing()) runHeldActions();
}

export function renderPlanner(body: HTMLElement, at: string): void {
  var wanted = parsePlannerAddress(at);
  var key = wanted.key;
  if (!state.world) {
    body.textContent = "";
    loading(body, "the world");
    return;
  }
  if (root.parentNode !== body) {
    body.textContent = "";
    body.appendChild(root);
    mountedKey = null;
  }
  loadItems();
  if (bench.world !== state.world) {
    resetBench(bench.key);
    mountedKey = null;
  }
  if (key !== mountedKey) {
    mountedKey = key;
    if (key) openPlan(key);
    else {
      resetBench("");
      loadList();
      loadActivity();
    }
    scheduleFocus();
  }
  if (key) syncTab(wanted);
  if (key && bench.viewedRev !== wanted.viewedRev) openRevision(wanted.viewedRev);
  if (key && wanted.alternatesItem) showAlternates(wanted.alternatesItem);
  else if (bench.alternates) refocusCtl = hideAlternates();
  draw();
}

/* ------------------------------------------------------------- following chat */

export function onPlansEvent(event: PlansEvent): void {
  if (event.world !== state.world) return;
  whenIdle(function () {
    if (openPlanKey() === "") {
      loadList();
      loadActivity();
    }
    followHead(event);
  });
}

function openFromChat(entry: ActivityEvent): void {
  inbox.card = entry;
  if (entry.plan && !planTitle(entry.plan)) loadList();
  if (openPlanKey()) changed();
  else go("planner");
}

function withPlanName(key: string, given: string | null | undefined, then: (name: string) => void): void {
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

function followTrackView(plan: string, followed: Followed): void {
  var args = followed.args;
  var stage = typeof args.stage === "number" && args.stage >= 1 ? Math.floor(args.stage) : 0;
  var there = trackDash(plan, stage);
  var startup = args.section === "startup";
  var open = function () {
    focusStartupPending = startup;
    stageToReveal = startup ? 0 : stage;
    if (state.dash === there) changed();
    else go(there);
  };
  if (followed.toastsOnly) {
    withPlanName(plan, followed.entry.name, function (called) {
      offer(followed.who + " looked at the track of “" + called + "”", "open", open);
    });
    return;
  }
  whenIdle(function () {
    var moving = state.dash !== there;
    open();
    if (!moving) return;
    withPlanName(plan, followed.entry.name, function (called) {
      notify(followed.who + " opened the track of “" + called + "” (Settings, follow chat)");
    });
  });
}

function followSiteView(plan: string, followed: Followed): void {
  var spot = "planner/" + plan + "/site";
  var look = function () {
    showGhost(plan, followed.args, followed.who);
    if (state.dash !== spot) go(spot);
  };
  if (followed.toastsOnly) {
    withPlanName(plan, followed.entry.name, function (called) {
      offer(followed.who + " looked at a spot for “" + called + "”", "open", look);
    });
  } else whenIdle(look);
}

function followAlternatesView(plan: string, item: string, followed: Followed): void {
  var at = altDash(plan, item);
  if (followed.toastsOnly) {
    offer(followed.who + " " + followed.entry.text, "open", function () {
      goToAlternates(plan, at);
    });
  } else if (state.dash !== at) {
    whenIdle(function () {
      goToAlternates(plan, at);
    });
  }
}

function followSolve(followed: Followed): void {
  var entry = followed.entry;
  if (followed.toastsOnly) {
    offer(followed.who + " " + objectiveText(entry.text), "open", function () {
      openFromChat(entry);
    });
  } else {
    whenIdle(function () {
      openFromChat(entry);
    });
  }
}

function followPlanView(plan: string, followed: Followed): void {
  var entry = followed.entry;
  if (followed.toastsOnly) {
    offer(followed.who + ": " + entry.text, "open", function () {
      go("planner/" + plan);
    });
  } else if (openPlanKey() !== plan) {
    whenIdle(function () {
      notify(followed.who + " opened “" + (entry.name || planTitle(plan) || "a plan") + "” (Settings, follow chat)");
      go("planner/" + plan);
    });
  }
}

export function onActivityEvent(entry: ActivityEvent): void {
  if (entry.world !== state.world || entry.actor.kind === "page" || seenActivityIds[entry.id]) return;
  seenActivityIds[entry.id] = true;
  if (openPlanKey() === "") loadActivity();
  lastActivityTs = Math.max(lastActivityTs, entry.ts);
  if (!isSincePageOpened(entry.ts)) return;
  var mode = settingChoice("follow");
  if (mode === "off") return;
  var followed: Followed = { entry: entry, who: actorWord(entry.actor), args: entry.args || {}, toastsOnly: mode === "toasts" };
  if (entry.kind === "plan.solve") {
    followSolve(followed);
    return;
  }
  var plan = entry.plan;
  if (entry.kind !== "plan.view" || !plan) return;
  var view = followed.args.view;
  var item = followed.args.item;
  if (view === "track") followTrackView(plan, followed);
  else if (view === "site") followSiteView(plan, followed);
  else if (view === "alternates" && typeof item === "string") followAlternatesView(plan, item, followed);
  else followPlanView(plan, followed);
}

export function resyncPlanner(replay: (entries: ActivityEvent[]) => void): void {
  if (openPlanKey() === "") {
    loadList();
    loadActivity();
  }
  resyncHead();
  if (trackShowing()) loadTrack();
  var since = Math.max(lastActivityTs, state.openedAtMs / 1000);
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

/* ------------------------------------------------------------------- keys */

function onEscape(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !root.isConnected || openPlanKey() === null) return;
  if (isAskBarOpen()) {
    event.preventDefault();
    closeBar();
    return;
  }
  if (!openPlanKey()) return;
  if (bench.alternates) {
    event.preventDefault();
    closeAlternates();
  } else if (clearPick()) event.preventDefault();
}

function onPlannerKeydown(event: KeyboardEvent): void {
  if (isFormField(document.activeElement)) return;
  if (event.key === "Escape") {
    onEscape(event);
    return;
  }
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  if (!openPlanKey() || !bench.plan) return;
  event.preventDefault();
  if (event.shiftKey) redoLast();
  else undoLast();
}

function wire(): void {
  onBench(scheduleDraw);
  onPins(scheduleDraw);
  onAsks(scheduleDraw);
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
    if (mountedKey) scheduleDraw();
  });
  document.addEventListener("pointerdown", function () {
    pointerDown = true;
  });
  document.addEventListener("pointerup", function () {
    setTimeout(function () {
      pointerDown = false;
      if (drawPending) draw();
    }, 0);
  });
  wireFocusReports();
  document.addEventListener("keydown", onPlannerKeydown);
  document.addEventListener("focusout", function () {
    setTimeout(runHeldActions, 0);
  });
}

wire();

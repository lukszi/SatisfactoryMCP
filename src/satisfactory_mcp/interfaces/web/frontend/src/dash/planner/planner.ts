/* The Planner tab: mounting the plans list or a plan's page, landing a bare plan address on its
 * tab, redrawing without stepping on an edit, and following chat. See
 * docs/planner_slice_contract.md §12. */

import { get } from "../../api/client";
import { isAskBarOpen, closeBar, onAsks, renderAskBar, settleAskFocus } from "../../chat/asks";
import { loading } from "../../kit/dashkit";
import { make } from "../../kit/dom";
import { keepFocus } from "../../kit/focus";
import { onReload } from "../../app/load";
import { dashParts, go, replaceDash } from "../../app/nav";
import { onVitals } from "../../app/vitals";
import { renderPlanPage } from "./plan/page";
import { landingTab } from "./plan/model";
import { altDash, buildDash, openPlanKey, parsePlannerAddress, planDash, plannerDraft } from "./address";
import { forgetSolves, hideAlternates, loadAlternates, loadTrack, loadVersions, openRevision, showAlternates, stageHeadroom } from "./reads";
import { actorWord, bench, changed, inbox, loadItems, onBench, pendingFocus, resetBench } from "./state";
import { followHead, openPlan, redoLast, resyncHead, undoLast } from "./writes";
import { scheduleFocus, wireFocusReports } from "./focus";
import { renderPlansPage } from "./plans/page";
import { prefillNewPlan } from "./plans/cards";
import { loadActivity } from "./plans/timeline";
import { loadList, planIndex, planTitle } from "./plan-index";
import { builtOf } from "./plans/model";
import { onPins } from "../../chat/pins";
import { showGhost } from "./site";
import { onBiomass } from "../power/readings";
import { onSetting, settingChoice, settingNumber, settingOn } from "../../app/settings";
import { isSincePageOpened, state } from "../../app/state";
import { notify, offer } from "../../kit/toast";
import { objectiveText } from "../../kit/words";

import type { ActivityResponse, PlansResponse } from "../../api/shapes";
import type { PlannerAddress } from "./address";
import type { ActivityEvent, PlansEvent } from "./state";

const FORM_FIELD = /^(INPUT|TEXTAREA|SELECT)$/;

const root = make("div", "plan-root");
let mountedKey: string | null = null;
let heldActions: Array<() => void> = [];
let drawPending = false;
const seenActivityIds: Record<string, boolean> = {};
let lastActivityTs = 0;
let drawTimer = 0;
let pointerDown = false;
let refocusCtl = "";
let lastFocusSignature = "";

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
  bench.place = wanted.place;
  if (!wanted.tab) return;
  const entering = bench.tab !== wanted.tab;
  bench.tab = wanted.tab;
  if (wanted.tab === "build") {
    bench.track.stage = wanted.stage;
    if (entering || !bench.track.data) loadTrack();
  } else if (wanted.tab === "versions" && entering) loadVersions();
}

/** A bare `planner/<key>` lands on the plan's current step once the plan and its built
 *  figure are read; the address then names that tab. */
function settleLanding(): void {
  const at = parsePlannerAddress(dashParts().subject);
  if (!at.key || at.tab || at.place || bench.key !== at.key || !bench.plan) return;
  const sited = !!bench.plan.siting;
  const named = bench.plan.factory && bench.plan.factory[0] !== "/" ? bench.plan.factory : "";
  const listed = !!planIndex.data && planIndex.world === state.world;
  const row = listed ? planIndex.data!.index.filter((r) => r.key === at.key)[0] : undefined;
  const waiting = listed ? !!row && !planIndex.builtDone : !planIndex.error;
  if ((sited || named) && waiting) return;
  const built = row ? builtOf(row, planIndex.built[row.key]) : ({ kind: "pending" } as const);
  replaceDash(planDash(at.key, landingTab(sited, named, built)));
  syncTab(parsePlannerAddress(dashParts().subject));
}

function trackShowing(): boolean {
  return !!openPlanKey() && bench.tab === "build" && !!bench.plan && !bench.viewedRev;
}

function goToAlternates(plan: string, at: string): void {
  const switching = openPlanKey() === plan && dashParts().rest[1] === "alt";
  if (!switching) bench.alternatesCloseGoesBack = false;
  go(at, switching);
}

function closeAlternates(): void {
  const back = bench.alternatesCloseGoesBack && dashParts().rest[1] === "alt";
  refocusCtl = hideAlternates();
  if (back) history.back();
  else go(planDash(bench.key, "machines"), true);
  changed();
}

function isFormField(el: Element | null): boolean {
  return !!el && (FORM_FIELD.test(el.tagName) || (el as HTMLElement).isContentEditable);
}

/** An edit is open: a field elsewhere has focus, or a planner field holds an unsaved value. */
function typing(): boolean {
  const active = document.activeElement as HTMLInputElement | null;
  if (!active || !FORM_FIELD.test(active.tagName)) return false;
  if (!root.contains(active)) return true;
  return active.tagName !== "SELECT" && active.value !== active.defaultValue;
}

function showHeldHint(): void {
  const line = root.querySelector(".plan-held");
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
  const actions = heldActions;
  heldActions = [];
  actions.forEach(function (action) {
    action();
  });
  if (drawPending) draw();
}

function scheduleDraw(): void {
  clearTimeout(drawTimer);
  drawTimer = window.setTimeout(draw, 0);
}

function focusCtl(ctl: string): HTMLElement | null {
  return root.querySelector<HTMLElement>('[data-ctl="' + CSS.escape(ctl) + '"]');
}

function restoreFocus(): void {
  const drawer = bench.alternates;
  const shut = drawer && drawer.enter && parsePlannerAddress(dashParts().subject).alternatesItem === drawer.item ? root.querySelector<HTMLElement>('[data-ctl="alt-close"]') : null;
  if (drawer && shut) {
    drawer.enter = false;
    shut.focus();
  }
  const target = pendingFocus.ctl ? focusCtl(pendingFocus.ctl) : null;
  if (target || Date.now() > pendingFocus.until) pendingFocus.ctl = "";
  if (target) target.focus({ preventScroll: true });
  if (refocusCtl) {
    const back = focusCtl(refocusCtl);
    if (back) {
      refocusCtl = "";
      back.focus({ preventScroll: true });
    }
  }
}

function reportFocusIfChanged(): void {
  const signature = JSON.stringify([bench.tab, bench.alternates ? bench.alternates.item : "", bench.selection, bench.plan ? bench.plan.rev : null]);
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
  if (mountedKey) settleLanding();
  keepFocus(root, function () {
    root.textContent = "";
    renderAskBar(root);
    if (mountedKey) renderPlanPage(root, parsePlannerAddress(dashParts().subject), closeAlternates);
    else renderPlansPage(root);
  });
  settleAskFocus(root);
  restoreFocus();
  reportFocusIfChanged();
  if (heldActions.length && !typing()) runHeldActions();
}

export function renderPlanner(body: HTMLElement, at: string): void {
  const draft = plannerDraft(at);
  if (draft) {
    prefillNewPlan(draft);
    replaceDash("planner");
    at = "";
  }
  const wanted = parsePlannerAddress(at);
  const key = wanted.key;
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
    if (key) {
      openPlan(key);
      loadList();
    } else {
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
    } else if (openPlanKey() === event.key) loadList();
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
  const known = given || (bench.key === key && bench.plan ? bench.plan.name : "") || planTitle(key);
  if (known) {
    then(known);
    return;
  }
  get<PlansResponse>("/api/plans").then(
    function (data) {
      const row = data.index.filter(function (r) {
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
  const args = followed.args;
  const stage = typeof args.stage === "number" && args.stage >= 1 ? Math.floor(args.stage) : 0;
  const there = buildDash(plan, stage);
  const open = function () {
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
    const moving = state.dash !== there;
    open();
    if (!moving) return;
    withPlanName(plan, followed.entry.name, function (called) {
      notify(followed.who + " opened the track of “" + called + "” (Settings, follow chat)");
    });
  });
}

function followSiteView(plan: string, followed: Followed): void {
  const spot = planDash(plan, "place");
  const look = function () {
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
  const at = altDash(plan, item);
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
  const entry = followed.entry;
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
  const entry = followed.entry;
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
  const mode = settingChoice("follow");
  if (mode === "off") return;
  const followed: Followed = { entry: entry, who: actorWord(entry.actor), args: entry.args || {}, toastsOnly: mode === "toasts" };
  if (entry.kind === "plan.solve") {
    followSolve(followed);
    return;
  }
  const plan = entry.plan;
  if (entry.kind !== "plan.view" || !plan) return;
  const view = followed.args.view;
  const item = followed.args.item;
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
  const since = Math.max(lastActivityTs, state.openedAtMs / 1000);
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
  if (trackShowing()) loadTrack();
}

onReload(function () {
  scheduleFocus();
  if (bench.world !== state.world) return;
  bench.track.data = null;
  onSaveEvent();
});

export function onNotesEvent(): void {
  if (trackShowing() && bench.plan?.factory) loadTrack();
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
  } else if (bench.picked) {
    event.preventDefault();
    bench.picked = "";
    bench.selection = null;
    changed();
  }
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
    if (trackShowing()) loadTrack();
  });
  let powerWas = powerDefaults();
  onSetting(function () {
    if (powerDefaults() === powerWas) return;
    powerWas = powerDefaults();
    if (bench.plan) forgetSolves();
  });
  let headroomWas = stageHeadroom();
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

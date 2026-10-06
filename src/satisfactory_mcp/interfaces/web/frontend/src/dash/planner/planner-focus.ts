/* What the page tells chat it is looking at: PUT /api/ui/focus, debounced after each change and
 * repeated as a heartbeat while the tab is visible. See docs/planner_slice_contract.md §12. */

import { onToken, send } from "../../api/client";
import { onSelect, selected, selectionRef } from "../../app/selection";
import { onSetting, settingChoice } from "../../app/settings";
import { state } from "../../app/state";
import { openPlanKey } from "./planner-address";
import { saveToken } from "./planner-reads";
import { bench } from "./planner-state";

import type { FocusResponse, FocusSelection } from "../../api/shapes";

var FOCUS_DEBOUNCE_MS = 1000;
var HEARTBEAT_MS = 15000;

var focusTimer = 0;
var viewSent: string | null = null;

function mapSelectionForFocus(): FocusSelection | null {
  var picked = selected();
  return picked ? { kind: picked.kind, label: picked.label, ref: selectionRef(picked) } : null;
}

function altSelection(): FocusSelection | null {
  var drawer = bench.alternates;
  if (!drawer) return null;
  return { kind: "item", label: drawer.data ? drawer.data.name : drawer.item, ref: drawer.item };
}

function focusTab(planKey: string | null): string {
  if (planKey === null) {
    var cut = state.dash.indexOf("/");
    return cut < 0 ? state.dash : state.dash.slice(0, cut);
  }
  if (!planKey) return "list";
  return bench.tab === "graph" || bench.tab === "track" || bench.tab === "site" ? bench.tab : "workbench";
}

function focusBody(): Record<string, unknown> {
  var planKey = openPlanKey();
  var planner = planKey !== null;
  return {
    view: state.dash === "" ? "map" : planner ? "planner" : "dashboard",
    dash: state.dash,
    plan: planner && planKey && bench.key === planKey ? planKey : null,
    rev: planner && planKey && bench.plan ? bench.plan.rev : null,
    tab: focusTab(planKey),
    selection: planner && planKey ? altSelection() || bench.selection : mapSelectionForFocus(),
    follow: settingChoice("follow"),
    sav: state.saveToken || saveToken(),
  };
}

function sendFocus(): void {
  if (!state.world) return;
  send<FocusResponse>("PUT", "/api/ui/focus", focusBody()).catch(function () {});
}

/** Reports a new dashboard address at once rather than after the debounce. */
export function viewFocus(): void {
  if (!state.world || state.dash === viewSent) return;
  viewSent = state.dash;
  clearTimeout(focusTimer);
  sendFocus();
}

export function scheduleFocus(): void {
  clearTimeout(focusTimer);
  focusTimer = window.setTimeout(sendFocus, FOCUS_DEBOUNCE_MS);
}

export function wireFocusReports(): void {
  onToken(scheduleFocus);
  onSetting(scheduleFocus);
  onSelect(scheduleFocus);
  window.addEventListener("hashchange", scheduleFocus);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") scheduleFocus();
  });
  setInterval(function () {
    if (document.visibilityState === "visible") sendFocus();
  }, HEARTBEAT_MS);
}

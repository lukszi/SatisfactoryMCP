/* The live loop: one EventSource, and what a save write means.
 *
 * Small and separate because the failure it exists to prevent is specific -- a page quietly
 * presenting stale data as live.
 */

import { el } from "../kit/dom";
import { loadLive, loadOne } from "./load";
import { onActivity as onAdviceActivity, refetchAdvice } from "../chat/advice";
import { onActivity as onAskActivity, refetchAsks } from "../chat/asks";
import { onActivity as onPinActivity, onPlanChange, refetchPins } from "../chat/pins";
import { onActivityEvent, onNotesEvent, onPlansEvent, onSaveEvent, resyncPlanner } from "../dash/planner/planner";
import { onRenameActivity } from "../dash/factories/rename";
import { fetchMaps, onMapsEvent } from "./map-types";
import { onSettingsEvent, refetchSharedSettings } from "./shared-settings";
import { state } from "./state";
import { fail } from "../kit/toast";
import { onFindActivity } from "../dash/world/world";
import { refreshWorlds } from "./world-picker";

import type { SettingsResponse } from "../api/shapes";
import type { MapsEvent } from "./map-types";
import type { ActivityEvent, PlansEvent } from "../dash/planner/planner-core";

/* The stream replays the newest event of every kind to each new subscriber, so the first one
 * usually describes a write that happened BEFORE this page opened: not news, and refetching
 * on it would double-load what boot has just loaded. Shared by both listeners, because the
 * replay is a property of the stream rather than of what any one event means. */
function isNews(event: MessageEvent): boolean {
  var payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch (ignored) {
    /* a malformed event is treated as news, the safe direction */
  }
  var at = payload && (payload.mtime || payload.ts);
  return !(at && at * 1000 < state.openedAtMs - 2000);
}

function parsed<T>(event: MessageEvent): T | null {
  try {
    return JSON.parse(event.data) as T;
  } catch (ignored) {
    return null;
  }
}

/* Every activity entry reaches every listener once, live or replayed after a reconnect. A
 * replay can hold several finds; only the newest may move the page, the older ones are history. */
var dispatched: Record<string, boolean> = {};

function dispatchActivity(entries: ActivityEvent[]): void {
  var fresh = entries.filter(function (entry) {
    return !dispatched[entry.id];
  });
  var lastFind = -1;
  fresh.forEach(function (entry, i) {
    if (entry.kind === "world.find" && entry.actor.kind !== "page") lastFind = i;
  });
  fresh.forEach(function (entry, i) {
    dispatched[entry.id] = true;
    onPinActivity(entry);
    onAskActivity(entry);
    onAdviceActivity(entry);
    if (entry.kind !== "world.find" || i === lastFind) onFindActivity(entry);
    onRenameActivity(entry);
    onActivityEvent(entry);
  });
}

function showLive(kind: string, text: string, title: string): void {
  var live = el("live");
  live.className = "live" + (kind ? " " + kind : "");
  live.title = title;
  var words = live.querySelector(".live-text");
  if (!words) return;
  if (words.textContent !== text) words.textContent = text;
  words.classList.toggle("dk-hidden", kind === "on");
}

/* One EventSource for the process; a write is an edge trigger and the response is a refetch
 * of what that kind of write can change. The grey dot means connecting, retrying or dead, so
 * its text says which, and losing an ESTABLISHED connection also says so in a toast. */
export function listen() {
  var source: EventSource | null = null;
  var wasOpen = false;
  var missed = false;
  window.addEventListener("pagehide", function () {
    if (!source) return;
    source.close();
    source = null;
    wasOpen = false;
  });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted || source) return;
    missed = true;
    connect();
  });
  connect();

  function connect() {
    showLive("", "connecting…", "connecting to the save watcher…");
    source = new EventSource(`/api/events?since=${state.openedAtMs / 1000}`);
    wire(source);
  }

  function wire(es: EventSource) {
    es.onopen = function () {
      if (missed) resync();
      missed = false;
      wasOpen = true;
      showLive("on", "live", "live: watching for save writes");
    };
    es.onerror = function () {
      var dropped = wasOpen;
      missed = missed || dropped;
      wasOpen = false;
      if (missed) showLive("lost", "offline", "live connection lost; retrying (is the server still running?)");
      else showLive("", "connecting…", "connecting to the save watcher…");
      if (dropped) fail("live updates lost; what is on screen may be stale");
    };
    var resync = function () {
      refreshWorlds();
      if (!state.save) {
        loadLive();
        onSaveEvent();
      }
      loadOne("/api/factories");
      loadOne("/api/factories/health");
      loadOne("/api/power/circuits");
      loadOne("/api/plans");
      refetchPins();
      refetchAsks();
      refetchAdvice();
      refetchSharedSettings();
      fetchMaps();
      resyncPlanner(dispatchActivity);
    };
    var blink = function () {
      var live = el("live");
      live.classList.add("hit");
      setTimeout(function () {
        live.classList.remove("hit");
      }, 800);
    };
    es.addEventListener("save", function (event) {
      if (!isNews(event)) return;
      blink();
      refreshWorlds();
      // A pinned save is pinned: the point of the picker is to hold a view while the game
      // autosaves over the newest. The dot still blinks so the write is not invisible.
      if (!state.save) {
        loadLive();
        onSaveEvent();
      }
    });
    /* The other write: a factory label or a stored plan, which the MCP tools put on disk while
     * the page is open and no autosave goes near. These two paths are the payloads built from
     * those files and no others -- and a pinned save does not pin either, because a label and
     * a siting belong to the world rather than to one file in it. */
    es.addEventListener("notes", function (event) {
      if (!isNews(event)) return;
      blink();
      loadOne("/api/factories");
      loadOne("/api/factories/health");
      loadOne("/api/power/circuits");
      loadOne("/api/plans");
      refetchPins();
      refetchAdvice();
      onNotesEvent();
    });
    es.addEventListener("plans", function (event) {
      var data = parsed<PlansEvent>(event);
      if (!data || !isNews(event)) return;
      blink();
      loadOne("/api/plans");
      refetchAdvice();
      onPlanChange(data);
      onPlansEvent(data);
    });
    es.addEventListener("activity", function (event) {
      var data = parsed<ActivityEvent>(event);
      if (!data || !isNews(event)) return;
      dispatchActivity([data]);
    });
    es.addEventListener("settings", function (event) {
      var data = parsed<SettingsResponse>(event);
      if (data) onSettingsEvent(data);
    });
    /* State rather than news: the replay of the newest one is how a reload sees a running job. */
    es.addEventListener("maps", function (event) {
      var data = parsed<MapsEvent>(event);
      if (data) onMapsEvent(data);
    });
  }
}

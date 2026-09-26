/* The live loop: one EventSource, and what a save write means.
 *
 * Small and separate because the failure it exists to prevent is specific -- a page quietly
 * presenting stale data as live.
 */

import { el } from "./dom";
import { loadLive, loadOne } from "./load";
import { state } from "./state";
import { fail } from "./toast";
import { refreshWorlds } from "./worlds";

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
  return !(payload && payload.mtime && payload.mtime * 1000 < state.opened - 2000);
}

/* One EventSource for the process; a write is an edge trigger and the response is a refetch
 * of what that kind of write can change. The grey dot means connecting, retrying or dead, so
 * its title says which, and losing an ESTABLISHED connection also says so in a toast. */
export function listen() {
  var source = new EventSource("/api/events");
  var dot = el("live");
  var wasOpen = false;
  dot.title = "connecting to the save watcher…";
  source.onopen = function () {
    wasOpen = true;
    dot.className = "dot on";
    dot.title = "live: watching for save writes";
  };
  source.onerror = function () {
    var lost = wasOpen;
    wasOpen = false;
    dot.className = "dot";
    dot.title = lost
      ? "live connection lost — is the server still running? Retrying…"
      : "connecting to the save watcher…";
    if (lost) fail("live updates lost — what is on screen may be stale");
  };
  var blink = function () {
    dot.className = "dot hit";
    setTimeout(function () {
      dot.className = "dot on";
    }, 800);
  };
  source.addEventListener("save", function (event) {
    if (!isNews(event)) return;
    blink();
    refreshWorlds();
    // A pinned save is pinned: the point of the picker is to hold a view while the game
    // autosaves over the newest. The dot still blinks so the write is not invisible.
    if (!state.save) loadLive();
  });
  /* The other write: a factory label or a stored plan, which the MCP tools put on disk while
   * the page is open and no autosave goes near. These two paths are the payloads built from
   * those files and no others -- and a pinned save does not pin either, because a label and
   * a siting belong to the world rather than to one file in it. */
  source.addEventListener("notes", function (event) {
    if (!isNews(event)) return;
    blink();
    loadOne("/api/factories");
    loadOne("/api/factories/health");
    loadOne("/api/plans");
  });
}

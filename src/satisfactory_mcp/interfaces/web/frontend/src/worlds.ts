/* The two pickers in the header: which world, and which of its saves.
 *
 * Apart from the loaders because the interesting part is not fetching -- it is that a rescan
 * must never wipe a working picker, that a pinned save stays visible after it stops being
 * listed, and that two worlds can share a session name. All three are about the SELECTION
 * surviving things happening underneath it, which is a different job from drawing what was
 * selected.
 */

import { render } from "./dashboard";
import { el } from "./dom";
import { loadOne, reload } from "./load";
import { writeHash } from "./map";
import { W } from "./words";
import { BOOT, currentWorld, pinnedPath, state } from "./state";
import { fail, friendly, note } from "./toast";

import type { WorldRow, WorldsResponse } from "./api-shapes";

function worldOption(w: WorldRow, dupes: Record<string, number>): HTMLOptionElement {
  var option = document.createElement("option");
  option.value = w.world_id;
  var hours = Math.round((w.play_duration_s || 0) / 3600);
  var label = w.session_name + " (" + w.saves.length + " saves, " + hours + " h)";
  // Two worlds can share a session name -- one id-keyed, one a legacy grouping of saves
  // too old to carry a world id. A save count alone cannot tell them apart.
  if ((dupes[w.session_name] ?? 0) > 1 && w.world_id.indexOf("session:") === 0) {
    label += ": old saves without a world id";
  }
  option.textContent = label;
  option.title = "world id: " + w.world_id;
  return option;
}

function fillWorldPicker(preserve: boolean): void {
  var picker = el<HTMLSelectElement>("world");
  var dupes: Record<string, number> = {};
  state.worlds.forEach(function (w) {
    dupes[w.session_name] = (dupes[w.session_name] || 0) + 1;
  });
  picker.innerHTML = "";
  state.worlds.forEach(function (w) {
    picker.appendChild(worldOption(w, dupes));
  });
  if (preserve && currentWorld()) picker.value = state.world;
  picker.disabled = !state.worlds.length;
}

function fillSavePicker(): void {
  var picker = el<HTMLSelectElement>("save");
  var w = currentWorld();
  picker.innerHTML = "";
  var newest = document.createElement("option");
  newest.value = "";
  newest.textContent = "newest save";
  newest.title = "follow the newest save, refetching as the game writes new ones";
  picker.appendChild(newest);
  var saves = ((w && w.saves) || []).slice().sort(function (a, b) {
    return (b.mtime_ns || 0) - (a.mtime_ns || 0);
  });
  saves.forEach(function (s) {
    var option = document.createElement("option");
    option.value = s.path || s.filename;
    option.textContent = s.filename;
    picker.appendChild(option);
  });
  if (state.save) {
    picker.value = state.save;
    if (picker.value !== state.save) {
      // The pinned save is no longer in the listing (deleted, or the world changed
      // under it). Keep the pin visible rather than silently unpinning.
      var pinned = document.createElement("option");
      pinned.value = state.save;
      pinned.textContent = "(pinned save no longer listed)";
      picker.appendChild(pinned);
      picker.value = state.save;
    }
  } else {
    picker.value = "";
  }
  picker.disabled = !saves.length;
  picker.onchange = function () {
    state.save = picker.value;
    var chosen = picker.selectedOptions[0];
    reload(state.save ? "opening " + (chosen ? chosen.textContent : "save") + "…" : "back to the newest save…");
  };
}

/* Both pickers, re-pointed at whatever `state.world` and `state.save` now say.
 *
 * Exported for the one caller that changes the selection without touching a <select>: the
 * fragment listener in fragment.ts, where the gesture is typing in the address bar. Both
 * pickers are rebuilt unconditionally, which is cheaper than a code path deciding which of
 * them moved. */
export function syncPickers(): void {
  fillWorldPicker(true);
  fillSavePicker();
}

function substituted(world: boolean, save: boolean): void {
  if (!world && !save) return;
  var shown = currentWorld();
  var name = shown ? "“" + shown.session_name + "”" : "another world";
  if (world) note("the linked world is not in the save folder; showing " + name + (BOOT.save ? " and its newest save" : ""));
  else note("the linked save “" + BOOT.save + "” is not in this world; showing the newest save");
}

export function loadWorlds(): Promise<void> {
  return fetch("/api/worlds")
    .then(function (r) {
      return r.json() as Promise<WorldsResponse>;
    })
    .then(function (body) {
      if (body.error) throw new Error(body.error);
      // No `|| []`: the endpoint sends `worlds` and `unsupported` together or sends `error`
      // instead, and the line above is what tells the two apart.
      state.worlds = body.worlds;
      var picker = el<HTMLSelectElement>("world");
      fillWorldPicker(false);
      picker.onchange = function () {
        state.world = picker.value;
        state.save = "";
        fillSavePicker();
        var chosen = picker.selectedOptions[0];
        reload("switching to " + (chosen ? chosen.textContent : "world") + "…");
      };

      if (!state.worlds.length) {
        // The one state that must NOT end as a healthy-looking blank page: no world at
        // all. The server may still know exactly why each file was rejected, and that
        // diagnosis belongs on screen, permanently -- not in a toast that self-erases.
        var reasons = body.unsupported
          .map(function (u) {
            return u.filename + ": " + u.reason;
          })
          .join(" · ");
        var text =
          "no readable saves found" +
          (reasons ? ": " + reasons : "") +
          " (set SATISFACTORY_SAVES if they live elsewhere)";
        el("summary").textContent = W.noSaves;
        el("summary").title = text;
        state.noSaves = true;
        render();
        // Geography needs no save, so the node table still draws -- the same table the
        // right-click inspector reads, so the two surfaces agree even with no world.
        //
        // Through the registry and not a fetch of its own, for the epoch guard: "no readable
        // saves" is the state a player fixes while the tab is open, `refreshWorlds` then
        // adopts the world and reloads, and this fetch outlives that switch. An unguarded late
        // reply would land on the new world's table with every dot's occupancy back to "no
        // extractor known here".
        loadOne("/api/nodes");
        return;
      }

      var known = !!BOOT.world && state.worlds.some(function (w) { return w.world_id === BOOT.world; });
      state.world = known ? BOOT.world! : state.worlds[0]!.world_id;
      picker.value = state.world;
      // The fragment names a save by FILENAME; the pin is a path. Same conversion the
      // hashchange path makes, which is why it is one function in state.ts.
      state.save = known || !BOOT.world ? pinnedPath(BOOT.save || "", currentWorld()) : "";
      fillSavePicker();
      substituted(!!BOOT.world && !known, !!BOOT.save && !state.save);
      writeHash();
    })
    .catch(function (e) {
      el("summary").textContent = "the world list could not be loaded";
      fail("worlds: " + friendly(e));
    });
}

/* The picker is not a snapshot: a session started or a save written while the tab is
 * open updates the counts and can add a world. Selection and pin are preserved; a scan
 * hiccup (transient error, empty answer) must never wipe a working picker mid-session. */
export function refreshWorlds(): void {
  fetch("/api/worlds")
    .then(function (r) {
      return r.json() as Promise<WorldsResponse>;
    })
    .then(function (body) {
      // An empty list is the scan hiccup this function exists to survive. What is guarded is
      // the CONTENT: the endpoint either sends the list or sends `error`.
      if (body.error || !body.worlds.length) return;
      state.worlds = body.worlds;
      fillWorldPicker(true);
      if (!state.world) {
        // The page opened with no world at all and one has appeared: adopt it.
        state.world = state.worlds[0]!.world_id;
        state.noSaves = false;
        el<HTMLSelectElement>("world").value = state.world;
        fillSavePicker();
        reload("world found, loading…");
        return;
      }
      if (!currentWorld()) return; // the selected world vanished; keep showing it as-is
      fillSavePicker();
    })
    .catch(function () {
      /* refreshed on the next save event */
    });
}

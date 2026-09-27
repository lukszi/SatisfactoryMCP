/* The page's identity line, and the one dot that is not a thing the player built.
 *
 * ITS OWN MODULE BECAUSE /api/summary HAS TWO CONSUMERS: the header text, and the player's
 * last known position, which is a mark on the map. The registry gives one entry one draw and
 * `get()` has no dedupe, so one entry per consumer would be two requests for one answer on
 * every autosave. The two consumers meet here instead, and the registry stays one entry per
 * request.
 *
 * `settles: true`: the dimmed map and the "loading…" line are cleared when this reply lands,
 * either way, because this is the reply that replaces the words the switch put there.
 */

import { el } from "./dom";
import { mw, phaseText } from "./format";
import { drawPlayer } from "./markers";
import { registerFetch } from "./registry";

import type { SummaryResponse } from "./api-shapes";

function drawHeader(s: SummaryResponse): void {
  drawPlayer(s.player);
  var power = s.power;
  // No fallback to the nameplate: `PowerReport` starts the measured figure at 0.0 and charges
  // an unmonitored machine in full, so it is always a number -- and a nameplate total printed
  // under the word "drawn" is the opposite of what the split exists to say.
  var measured = power.measured_draw_mw;
  var parts = [s.header.session_name];
  var phase = phaseText(s.progression.game_phase);
  if (phase) parts.push(phase);
  // The measured figure, labelled: the nameplate total alone reads as "one factory
  // from a brown-out" on a base that is mostly idle. Both live in the tooltip.
  parts.push(mw(measured) + " drawn / " + mw(power.generation_mw) + " generation");
  var span = el("summary");
  span.textContent = parts.join(" · ");
  // Set in the same breath as the text, and so is the failure branch below: a branch that
  // only touches `textContent` leaves the PREVIOUS world's tooltip -- three specific power
  // figures -- hanging off the new world's header. worlds.ts and reload() do the pair too.
  span.title =
    parts.join(" · ") +
    "\n" +
    s.age_note +
    "\npower: " +
    mw(measured) +
    " measured draw; " +
    mw(power.draw_mw) +
    " nameplate if every machine ran at once; " +
    mw(power.generation_mw) +
    " generation";
}

function wireSearchToggle(): void {
  el("search-open").onclick = function () {
    el<HTMLInputElement>("search-q").focus();
  };
}

/* A failure leaves a statement, not a blank that reads as "everything is fine, there is just
 * nothing here". Tooltip included: the previous world's power figures hovering over the words
 * "could not be read" is worse than the blank, because it is an answer. */
var UNREADABLE = "this world's save could not be read";

registerFetch<SummaryResponse>({
  wave: "live",
  rank: 30,
  path: "/api/summary",
  label: "summary",
  // The player dot and nothing else: everything else this draws is text, and text is
  // replaced by `failed` below rather than emptied.
  clears: ["player"],
  // Neither the header nor the you-are-here dot is a thing a storey contains.
  refilters: false,
  settles: true,
  draw: drawHeader,
  failed: function () {
    el("summary").textContent = UNREADABLE;
    el("summary").title = UNREADABLE;
  },
});

wireSearchToggle();

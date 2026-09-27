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
import { loadOne } from "./load";
import { drawPlayer } from "./markers";
import { biomassLine, biomassQuery, onBiomass, ratedSummary, readGeneration, readMeasured } from "./powerview";
import { registerFetch } from "./registry";
import { W } from "./words";

import type { SummaryResponse } from "./api-shapes";

function drawHeader(s: SummaryResponse): void {
  drawPlayer(s.player);
  var r = ratedSummary(s);
  var measured = readMeasured(r);
  var generation = readGeneration(r);
  var parts = [s.header.session_name];
  var phase = phaseText(s.progression.game_phase);
  if (phase) parts.push(phase);
  parts.push(measured.value + " " + W.measuredDraw + " / " + generation.value + " " + W.generation);
  var span = el("summary");
  span.textContent = parts.join(" · ");
  var power = [measured.why || measured.value + " " + W.measuredDraw, mw(s.power.draw_mw) + " " + W.nameplateDraw, generation.value + " " + W.generation];
  if (biomassLine(s.power)) power.push(biomassLine(s.power));
  span.title = parts.join(" · ") + "\n" + s.age_note + "\npower: " + power.join("; ");
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
  query: biomassQuery,
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

onBiomass(function () {
  loadOne("/api/summary");
});

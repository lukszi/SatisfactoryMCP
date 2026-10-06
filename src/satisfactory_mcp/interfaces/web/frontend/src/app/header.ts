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

import { holdToken } from "../api/client";
import { el } from "../kit/dom";
import { mw, phaseText } from "../kit/format";
import { loadOne } from "./load";
import { drawPlayer } from "../map/drawn/markers";
import { biomassLine, biomassQuery, onBiomass, ratedSummary, readGeneration, readMeasuredDraw } from "../dash/power-ledger";
import { registerFetch } from "./registry";
import { WORDS } from "../kit/words";

import type { SummaryResponse } from "../api/shapes";

function drawHeader(summary: SummaryResponse): void {
  holdToken(summary.save_token);
  drawPlayer(summary.player);
  const rated = ratedSummary(summary);
  const measured = readMeasuredDraw(rated);
  const generation = readGeneration(rated);
  const parts = [summary.header.session_name];
  const phase = phaseText(summary.progression.game_phase);
  if (phase) parts.push(phase);
  parts.push(measured.value + " " + WORDS.measuredDraw + " / " + generation.value + " " + WORDS.generation);
  const span = el("summary");
  span.textContent = parts.join(" · ");
  const power = [measured.why || measured.value + " " + WORDS.measuredDraw, mw(summary.power.draw_mw) + " " + WORDS.nameplateDraw, generation.value + " " + WORDS.generation];
  const biomass = biomassLine(summary.power);
  if (biomass) power.push(biomass);
  span.title = parts.join(" · ") + "\n" + summary.age_note + "\npower: " + power.join("; ");
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

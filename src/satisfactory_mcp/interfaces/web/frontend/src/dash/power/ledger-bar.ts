/* The compact ledger bar as a DOM element, for the map's side panel;
 * the Power pages draw supply.ts instead. */

import { make } from "../../kit/dom";
import { mw } from "../../kit/format";
import { WORDS } from "../../kit/words";

import type { Figures } from "./readings";

/* Nameplate and measured draw as fills, generation as a cap mark, on one scale. */
export function ledgerBar(ledger: Figures): HTMLElement {
  const track = make("div", "panel-bar dash-bar");
  const cap = Math.max(ledger.generation_mw, ledger.draw_mw, 1);
  const nameplate = make("span", "panel-bar-nameplate");
  nameplate.style.width = Math.min(100, (ledger.draw_mw / cap) * 100) + "%";
  track.appendChild(nameplate);
  if (ledger.monitored) {
    const measured = make("span", "panel-bar-measured");
    measured.style.width = Math.min(100, (ledger.measured_draw_mw / cap) * 100) + "%";
    track.appendChild(measured);
  }
  const generation = make("span", "panel-bar-cap");
  generation.style.left = Math.min(100, (ledger.generation_mw / cap) * 100) + "%";
  track.appendChild(generation);
  const measured = ledger.monitored ? mw(ledger.measured_draw_mw) + " " + WORDS.measuredDraw : "no machine measured";
  track.title = measured + ", " + mw(ledger.draw_mw) + " " + WORDS.nameplateDraw + ", against " + mw(ledger.generation_mw) + " " + WORDS.generation;
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", track.title);
  return track;
}

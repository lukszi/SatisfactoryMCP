/* The old Overview's power tiles, kept on dashkit until Overview is
 * ported; the Power page itself is dash/power/. */

import { tile } from "../kit/dashkit";
import { mw } from "../kit/format";
import { counted, WORDS } from "../kit/words";
import { biomassLine, readGeneration, readHeadroomFull, readHeadroomNow, unrated, unratedTitle, type Rated } from "./power/readings";

export function headroomTiles(rated: Rated, href: string): HTMLElement[] {
  const ledger = rated.ledger;
  const nowReading = readHeadroomNow(rated);
  const fullReading = readHeadroomFull(rated);
  const now = tile(WORDS.headroomNow, nowReading.value, nowReading.why || mw(ledger.measured_draw_mw) + " " + WORDS.measuredDraw, nowReading.bad, href);
  const full = tile(WORDS.headroomFull, fullReading.value, fullReading.why || mw(ledger.draw_mw) + " " + WORDS.nameplateDraw, fullReading.bad, href);
  if (unrated(rated)) now.title = full.title = unratedTitle(rated);
  return [now, full];
}

export function generationTile(rated: Rated, sub: string, starved: boolean, href: string): HTMLElement {
  const reading = readGeneration(rated);
  const extra = reading.why ? "" : biomassLine(rated.ledger);
  const box = tile(WORDS.generation, reading.value, reading.why || (extra ? sub + " · " + extra : sub), reading.bad || (!reading.why && starved), href);
  if (extra) box.title = extra + ": " + counted(rated.ledger.biomass_generators, "burner") + ", hand-fed; Settings can count them";
  return box;
}

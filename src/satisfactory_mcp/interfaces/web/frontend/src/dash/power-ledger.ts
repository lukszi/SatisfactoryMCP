/* How a power ledger and a circuit are read, drawn and named, on every surface that shows one.
 * See docs/frontend_vision.md §8. */

import { make } from "../kit/dom";
import { mw, regionLine } from "../kit/format";
import { onSetting, settingOn } from "../app/settings";
import { WORDS } from "../kit/words";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, SummaryResponse } from "../api/shapes";

export const NONE = "–";

const UNRATED = "generation not modelled";

const UNMEASURED = "no machine measured";

export type Figures = Pick<
  Ledger,
  "generation_mw" | "draw_mw" | "measured_draw_mw" | "headroom_mw" | "measured_headroom_mw" | "monitored" | "unmonitored" | "biomass_mw" | "biomass_generators"
>;

export interface Rated {
  ledger: Figures;
  unmodellable: string[];
  dark: boolean;
}

export interface Reading {
  value: string;
  bad: boolean;
  why: string;
}

export function biomassQuery(): string {
  return "biomass=" + (settingOn("biomass") ? "include" : "exclude");
}

export function onBiomass(listener: () => void): void {
  let last = biomassQuery();
  onSetting(function () {
    const now = biomassQuery();
    if (now === last) return;
    last = now;
    listener();
  });
}

/* Consumers and no generator at all, burners included: the circuit draws from nothing. */
export function circuitDark(row: CircuitRow): boolean {
  return row.ledger.generation_mw <= 0 && row.ledger.biomass_generators === 0 && row.consumers > 0;
}

export function ratedCircuit(row: CircuitRow): Rated {
  return { ledger: row.ledger, unmodellable: row.unmodellable, dark: circuitDark(row) };
}

export function ratedWorld(data: CircuitsResponse): Rated {
  return { ledger: data.world, unmodellable: [], dark: false };
}

export function ratedSummary(data: SummaryResponse): Rated {
  return { ledger: data.power, unmodellable: [], dark: false };
}

export function unrated(rated: Rated): boolean {
  return rated.unmodellable.length > 0;
}

function unmeasured(rated: Rated): boolean {
  return rated.ledger.monitored === 0;
}

export function unratedTitle(rated: Rated): string {
  return "game data cannot rate " + rated.unmodellable.join(", ") + ", so this circuit's output is unknown";
}

export function headroom(value: number): string {
  return mw(value, { signed: true });
}

export function readGeneration(rated: Rated): Reading {
  if (unrated(rated)) return { value: NONE, bad: false, why: UNRATED };
  if (rated.dark) return { value: WORDS.noGenerator, bad: true, why: "" };
  return { value: mw(rated.ledger.generation_mw), bad: false, why: "" };
}

export function readMeasuredDraw(rated: Rated): Reading {
  if (unmeasured(rated)) return { value: NONE, bad: false, why: UNMEASURED };
  return { value: mw(rated.ledger.measured_draw_mw), bad: false, why: "" };
}

export function readHeadroomNow(rated: Rated): Reading {
  if (unrated(rated)) return { value: NONE, bad: false, why: UNRATED };
  if (unmeasured(rated)) return { value: NONE, bad: false, why: UNMEASURED + "; see " + WORDS.headroomFull };
  return { value: headroom(rated.ledger.measured_headroom_mw), bad: rated.ledger.measured_headroom_mw < 0, why: "" };
}

export function readHeadroomFull(rated: Rated): Reading {
  if (unrated(rated)) return { value: NONE, bad: false, why: UNRATED };
  return { value: headroom(rated.ledger.headroom_mw), bad: rated.ledger.headroom_mw < 0, why: "" };
}

export function biomassLine(ledger: Figures): string {
  return ledger.biomass_generators && ledger.biomass_mw > 0 ? mw(ledger.biomass_mw, { signed: true }) + " " + WORDS.biomassNotCounted : "";
}

export interface Where {
  where: string;
  dash: string;
}

/* A machine's place as the tables name it: its factory, else its circuit, else its region. */
export function whereOf(machine: MachineRef): Where {
  if (machine.factory) return { where: machine.factory, dash: "factories/" + machine.factory };
  if (machine.circuit !== null) return { where: "circuit " + (machine.circuit + 1), dash: "power/" + (machine.circuit + 1) };
  return { where: machine.region ? regionLine(machine.region) : "", dash: "" };
}

function legendKey(parent: HTMLElement, className: string, text: string): void {
  const item = make("span", "bar-key-item");
  item.appendChild(make("span", "bar-key-swatch " + className));
  item.appendChild(document.createTextNode(text));
  parent.appendChild(item);
}

/* Nameplate and measured draw as fills, generation as a cap mark, on one scale. */
export function ledgerBar(ledger: Figures, legend?: boolean): HTMLElement {
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
  track.title =
    (ledger.monitored ? mw(ledger.measured_draw_mw) + " " + WORDS.measuredDraw : UNMEASURED) +
    ", " +
    mw(ledger.draw_mw) +
    " " +
    WORDS.nameplateDraw +
    ", against " +
    mw(ledger.generation_mw) +
    " " +
    WORDS.generation;
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", track.title);
  if (!legend) return track;
  const wrap = make("div", "bar-keyed");
  wrap.appendChild(track);
  const keys = make("div", "bar-key");
  keys.setAttribute("aria-hidden", "true");
  legendKey(keys, "panel-bar-measured", WORDS.measuredDraw);
  legendKey(keys, "panel-bar-nameplate", WORDS.nameplateDraw);
  legendKey(keys, "panel-bar-cap", WORDS.generation);
  wrap.appendChild(keys);
  return wrap;
}

// The first three factories on a circuit, and how many more there are.
function factoriesOn(row: CircuitRow): string {
  const more = row.factory_count > 3 ? " +" + (row.factory_count - 3) : "";
  return row.factories.slice(0, 3).join(", ") + more;
}

export function circuitName(row: CircuitRow): string {
  const name = "circuit " + (row.index + 1);
  return row.factories.length ? name + " · " + factoriesOn(row) : name;
}

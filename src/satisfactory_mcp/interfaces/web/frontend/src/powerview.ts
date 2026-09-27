/* How a power ledger and a circuit are read, drawn and named, on every surface that shows one.
 * See docs/frontend_vision.md §8. */

import { make } from "./dom";
import { mw, regionLine } from "./format";
import { onSetting, setting } from "./settings";
import { W } from "./words";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, SummaryResponse } from "./api-shapes";

export var LEDGER = {
  generation: W.generation,
  measuredDraw: W.measuredDraw,
  nameplateDraw: W.nameplateDraw,
  headroomNow: W.headroomNow,
  headroomFull: W.headroomFull,
  poles: W.polesAndTowers,
};

export var NONE = "–";

export var UNRATED = "generation not modelled";

export var UNMEASURED = "no machine measured";

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
  return "biomass=" + (setting("biomass") ? "include" : "exclude");
}

export function onBiomass(listener: () => void): void {
  var last = biomassQuery();
  onSetting(function () {
    var now = biomassQuery();
    if (now === last) return;
    last = now;
    listener();
  });
}

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

export function unrated(r: Rated): boolean {
  return r.unmodellable.length > 0;
}

export function unmeasured(r: Rated): boolean {
  return r.ledger.monitored === 0;
}

export function unratedTitle(r: Rated): string {
  return "game data cannot rate " + r.unmodellable.join(", ") + ", so this circuit's output is unknown";
}

export function headroom(value: number): string {
  return mw(value, { signed: true });
}

export function readGeneration(r: Rated): Reading {
  if (unrated(r)) return { value: NONE, bad: false, why: UNRATED };
  if (r.dark) return { value: W.noGenerator, bad: true, why: "" };
  return { value: mw(r.ledger.generation_mw), bad: false, why: "" };
}

export function readMeasured(r: Rated): Reading {
  if (unmeasured(r)) return { value: NONE, bad: false, why: UNMEASURED };
  return { value: mw(r.ledger.measured_draw_mw), bad: false, why: "" };
}

export function readNow(r: Rated): Reading {
  if (unrated(r)) return { value: NONE, bad: false, why: UNRATED };
  if (unmeasured(r)) return { value: NONE, bad: false, why: UNMEASURED + "; see " + LEDGER.headroomFull };
  return { value: headroom(r.ledger.measured_headroom_mw), bad: r.ledger.measured_headroom_mw < 0, why: "" };
}

export function readFull(r: Rated): Reading {
  if (unrated(r)) return { value: NONE, bad: false, why: UNRATED };
  return { value: headroom(r.ledger.headroom_mw), bad: r.ledger.headroom_mw < 0, why: "" };
}

export function biomassLine(ledger: Figures): string {
  return ledger.biomass_generators && ledger.biomass_mw > 0 ? mw(ledger.biomass_mw, { signed: true }) + " " + W.biomassNotCounted : "";
}

export interface Where {
  where: string;
  dash: string;
}

export function whereOf(m: MachineRef): Where {
  if (m.factory) return { where: m.factory, dash: "factories/" + m.factory };
  if (m.circuit !== null) return { where: "circuit " + (m.circuit + 1), dash: "power/" + (m.circuit + 1) };
  return { where: m.region ? regionLine(m.region) : "", dash: "" };
}

function key(parent: HTMLElement, className: string, text: string): void {
  var item = make("span", "bar-key-item");
  item.appendChild(make("span", "bar-key-swatch " + className));
  item.appendChild(document.createTextNode(text));
  parent.appendChild(item);
}

export function bar(ledger: Figures, legend?: boolean): HTMLElement {
  var track = make("div", "panel-bar dash-bar");
  var cap = Math.max(ledger.generation_mw, ledger.draw_mw, 1);
  var nameplate = make("span", "panel-bar-nameplate");
  nameplate.style.width = Math.min(100, (ledger.draw_mw / cap) * 100) + "%";
  track.appendChild(nameplate);
  if (ledger.monitored) {
    var measured = make("span", "panel-bar-measured");
    measured.style.width = Math.min(100, (ledger.measured_draw_mw / cap) * 100) + "%";
    track.appendChild(measured);
  }
  var generation = make("span", "panel-bar-cap");
  generation.style.left = Math.min(100, (ledger.generation_mw / cap) * 100) + "%";
  track.appendChild(generation);
  track.title =
    (ledger.monitored ? mw(ledger.measured_draw_mw) + " " + LEDGER.measuredDraw : UNMEASURED) +
    ", " +
    mw(ledger.draw_mw) +
    " " +
    LEDGER.nameplateDraw +
    ", against " +
    mw(ledger.generation_mw) +
    " " +
    LEDGER.generation;
  track.setAttribute("role", "img");
  track.setAttribute("aria-label", track.title);
  if (!legend) return track;
  var wrap = make("div", "bar-keyed");
  wrap.appendChild(track);
  var keys = make("div", "bar-key");
  keys.setAttribute("aria-hidden", "true");
  key(keys, "panel-bar-measured", LEDGER.measuredDraw);
  key(keys, "panel-bar-nameplate", LEDGER.nameplateDraw);
  key(keys, "panel-bar-cap", LEDGER.generation);
  wrap.appendChild(keys);
  return wrap;
}

export function circuitName(row: CircuitRow): string {
  return "circuit " + (row.index + 1) + (row.factories.length ? " · " + row.factories.slice(0, 3).join(", ") + (row.factory_count > 3 ? " +" + (row.factory_count - 3) : "") : "");
}

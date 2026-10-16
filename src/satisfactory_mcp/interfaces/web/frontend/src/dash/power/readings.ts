/* How a power ledger and a circuit are read and named, on every surface that shows one.
 * See docs/frontend_vision.md §8. */

import { count, mw, regionLine } from "../../kit/format";
import { onSetting, settingOn } from "../../app/settings";
import { counted, WORDS } from "../../kit/words";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef, StarvedGenerator, SummaryResponse } from "../../api/shapes";

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

/* A machine's place as the lists name it: its factory, else its circuit, else its region. */
export function whereOf(machine: Pick<MachineRef, "factory" | "circuit" | "region">): Where {
  if (machine.factory) return { where: machine.factory, dash: "factories/" + machine.factory };
  if (machine.circuit !== null) return { where: "circuit " + (machine.circuit + 1), dash: "power/" + (machine.circuit + 1) };
  return { where: machine.region ? regionLine(machine.region) : "", dash: "" };
}

export interface Faults {
  unwired: MachineRef[];
  noGenerator: MachineRef[];
  starved: StarvedGenerator[];
}

export function faultsOf(data: CircuitsResponse): Faults {
  return { unwired: data.unwired, noGenerator: data.no_generator, starved: data.starved };
}

export function faultCount(faults: Faults): number {
  return faults.unwired.length + faults.noGenerator.length + faults.starved.length;
}

export function faultWords(faults: Faults): string {
  return [
    count(faults.unwired.length) + " " + WORDS.noWire,
    count(faults.noGenerator.length) + " " + WORDS.noGenerator,
    counted(faults.starved.length, WORDS.starvedGenerator),
  ].join(" · ");
}

/* The first three factories on a circuit, and how many more there are. */
export function factoriesOn(row: CircuitRow): string {
  const more = row.factory_count > 3 ? " +" + (row.factory_count - 3) : "";
  return row.factories.slice(0, 3).join(", ") + more;
}

export function circuitName(row: CircuitRow): string {
  const name = "circuit " + (row.index + 1);
  return row.factories.length ? name + " · " + factoriesOn(row) : name;
}

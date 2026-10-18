/* What the Power and circuit pages show, worked out from /api/power/circuits: the problems
 * grouped, the circuits worst first, the shared bar scale and the "not counted" chips. Pure. */

import { count, mw } from "../../kit/format";
import { counted, WORDS } from "../../kit/words";
import { circuitDark, ratedCircuit, readHeadroomNow, whereOf, type Where } from "./readings";

import type { CircuitRow, CircuitsResponse, GeneratorCandidate, Ledger, MachineRef } from "../../api/shapes";

export interface MachinePlace {
  instance: string;
  x_m: number | null;
  y_m: number | null;
}

/** Machines of one building with one fault at one place; `fine` ones go last, muted. */
export interface Problem {
  kind: string;
  building: string;
  icon: string | null;
  cause: string;
  where: Where;
  fine: boolean;
  generator: boolean;
  refs: MachinePlace[];
}

export const GENERATOR_UNWIRED = "generator on no wire";

type Placed = Pick<MachineRef, "instance" | "name" | "factory" | "circuit" | "region" | "icon" | "x_m" | "y_m">;

function add(groups: Problem[], kind: string, row: Placed, cause: string, flags: { fine?: boolean; generator?: boolean }): void {
  const where = whereOf(row);
  const same = groups.find((g) => g.kind === kind && g.building === row.name && g.where.where === where.where && g.cause === cause);
  if (same) same.refs.push(row);
  else groups.push({ kind, building: row.name, icon: row.icon, cause, where, fine: !!flags.fine, generator: !!flags.generator, refs: [row] });
}

function rank(p: Problem): number {
  if (p.fine) return 2;
  return p.kind === WORDS.starvedGenerator ? 1 : 0;
}

/** Every power problem, or those on one circuit; worst first, then most machines. */
export function problemsOf(data: CircuitsResponse, circuit?: number): Problem[] {
  const on = (row: { circuit: number | null }) => circuit === undefined || row.circuit === circuit;
  const groups: Problem[] = [];
  data.unwired.filter(on).forEach((row) => add(groups, WORDS.noWire, row, "", {}));
  data.no_generator.filter(on).forEach((row) => add(groups, WORDS.noGenerator, row, "", {}));
  data.starved.filter(on).forEach((row) => add(groups, WORDS.starvedGenerator, row, row.cause, { generator: true }));
  if (circuit === undefined) data.unwired_generators.forEach((row) => add(groups, GENERATOR_UNWIRED, row, "", { fine: true, generator: true }));
  return groups.sort((a, b) => rank(a) - rank(b) || b.refs.length - a.refs.length);
}

function circuitRank(row: CircuitRow): number {
  if (circuitDark(row)) return 0;
  if (readHeadroomNow(ratedCircuit(row)).bad) return 1;
  return row.starved.length ? 2 : 3;
}

function slack(row: CircuitRow): number {
  const cap = row.ledger.capacity_mw;
  return cap > 0 ? (cap - row.ledger.measured_draw_mw) / cap : 0;
}

/** No generator, short now, a starved generator, then least headroom per MW of production capacity. */
export function worstFirst(rows: CircuitRow[]): CircuitRow[] {
  return rows.slice().sort((a, b) => circuitRank(a) - circuitRank(b) || slack(a) - slack(b) || a.index - b.index);
}

/** The circuit bars' shared scale, as a multiple of each circuit's production capacity: at least 1,
 *  at most 1.5, rounded up to a tenth, so draw above capacity shows on every bar alike. */
export function utilScale(rows: CircuitRow[]): number {
  let most = 1;
  rows.forEach(function (row) {
    const cap = row.ledger.capacity_mw;
    if (cap > 0) most = Math.max(most, row.ledger.draw_mw / cap, row.ledger.measured_draw_mw / cap);
  });
  return Math.ceil(Math.min(most, 1.5) * 10 - 1e-9) / 10;
}

/** A "not counted" chip: its text and its tooltip. */
export type Uncounted = [string, string];

function ledgerChips(ledger: Ledger, paused: number, unmodellable: string[]): Uncounted[] {
  const out: Uncounted[] = [];
  if (ledger.biomass_generators && ledger.biomass_mw > 0)
    out.push(["biomass " + mw(ledger.biomass_mw, { signed: true }), counted(ledger.biomass_generators, "biomass burner") + ", hand-fed; Settings can count them"]);
  if (ledger.starved_generation_mw > 0)
    out.push([mw(ledger.starved_generation_mw) + " starved", WORDS.capacity + " leaves out " + mw(ledger.starved_generation_mw) + " of starved generators, hatched in the bar; they count again once fed"]);
  if (ledger.unmonitored)
    out.push([count(ledger.unmonitored) + " unmeasured", counted(ledger.unmonitored, "machine") + " keep no monitor; " + WORDS.consumption + " charges them in full"]);
  if (paused) out.push([count(paused) + " " + WORDS.paused, counted(paused, "paused building") + ", left out of both sides"]);
  if (unmodellable.length) out.push([counted(unmodellable.length, "generator type") + " unrated", "game data cannot rate " + unmodellable.join(", ") + ", so their output is left out"]);
  return out;
}

export function worldUncounted(data: CircuitsResponse): Uncounted[] {
  const out = ledgerChips(data.world, data.paused, data.unmodellable);
  const off = data.off_grid;
  if (off.consumers || off.generators) {
    const parts = [counted(off.consumers, "machine") + " rated " + mw(off.draw_mw)];
    if (off.generators) parts.push(counted(off.generators, "generator") + " rated " + mw(off.generation_mw));
    out.push([count(off.consumers + off.generators) + " on no wire", "on no wire, so in no figure: " + parts.join(", ")]);
  }
  return out;
}

export function circuitUncounted(row: CircuitRow): Uncounted[] {
  return ledgerChips(row.ledger, row.ledger.paused, row.unmodellable);
}

/** One slice of a circuit's draw: a named factory, or (name null) what stands outside them,
 *  or (`off`) what stands behind a power switch standing off. */
export interface DrawSlice {
  name: string | null;
  off?: boolean;
  /** Power consumption, measured. */
  ms: number;
  /** Maximum consumption. */
  np: number;
}

/** A circuit's named factories by maximum consumption, most first, then the remainder of its
 *  draw when there is any worth drawing. */
export function circuitDraws(row: CircuitRow): DrawSlice[] {
  const named: DrawSlice[] = row.factories.map((f) => ({ name: f.name, ms: f.measured_draw_mw, np: f.draw_mw }));
  named.sort((a, b) => b.np - a.np || b.ms - a.ms);
  const ms = row.ledger.measured_draw_mw - named.reduce((sum, x) => sum + x.ms, 0);
  const off = row.ledger.switched_off_draw_mw;
  const np = row.ledger.draw_mw - off - named.reduce((sum, x) => sum + x.np, 0);
  if (ms > 0.5 || np > 0.5) named.push({ name: null, ms: Math.max(0, ms), np: Math.max(0, np) });
  if (off > 0) named.push({ name: null, off: true, ms: 0, np: off });
  return named;
}

/** Whether a circuit's draw passes its rated generation, so a build row may follow. */
export function isShort(row: CircuitRow): boolean {
  return !row.unmodellable.length && Math.max(row.ledger.draw_mw, row.ledger.measured_draw_mw) > row.ledger.generation_mw;
}

export interface BuildOut {
  /** MW the larger consumption passes rated generation by, starved generators fed. */
  short: number;
  /** Whether power consumption, not just maximum consumption, passes it. */
  now: boolean;
  pick: GeneratorCandidate;
  n: number;
}

/** What to build when consumption passes what the circuit makes once its starved generators
 *  are fed: the first runnable generator type, enough of them at 100%. */
export function buildOut(row: CircuitRow, candidates: GeneratorCandidate[]): BuildOut | null {
  if (!isShort(row)) return null;
  const gen = row.ledger.generation_mw;
  const short = Math.max(row.ledger.draw_mw, row.ledger.measured_draw_mw) - gen;
  const pick = candidates.find((c) => c.runnable);
  if (!pick || pick.mw <= 0) return null;
  return { short, now: row.ledger.measured_draw_mw > gen, pick, n: Math.ceil(short / pick.mw - 1e-9) };
}

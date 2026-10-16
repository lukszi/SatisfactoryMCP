/* What the Power and circuit pages show, worked out from /api/power/circuits: the problems
 * grouped, the circuits worst first, the shared bar scale and the "not counted" chips. Pure. */

import { count, mw } from "../../kit/format";
import { counted, WORDS } from "../../kit/words";
import { circuitDark, ratedCircuit, readHeadroomNow, whereOf, type Where } from "./readings";

import type { CircuitRow, CircuitsResponse, Ledger, MachineRef } from "../../api/shapes";

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
  const gen = row.ledger.generation_mw;
  return gen > 0 ? (gen - row.ledger.measured_draw_mw) / gen : 0;
}

/** No generator, short now, a starved generator, then least headroom per MW generated. */
export function worstFirst(rows: CircuitRow[]): CircuitRow[] {
  return rows.slice().sort((a, b) => circuitRank(a) - circuitRank(b) || slack(a) - slack(b) || a.index - b.index);
}

/** The circuit bars' shared scale, as a multiple of each circuit's generation: at least 1,
 *  at most 1.5, rounded up to a tenth, so draw above generation shows on every bar alike. */
export function utilScale(rows: CircuitRow[]): number {
  let most = 1;
  rows.forEach(function (row) {
    const gen = row.ledger.generation_mw;
    if (gen > 0) most = Math.max(most, row.ledger.draw_mw / gen, row.ledger.measured_draw_mw / gen);
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
    out.push([mw(ledger.starved_generation_mw) + " starved", "generation includes " + mw(ledger.starved_generation_mw) + " from starved generators, hatched in the bar; they make nothing until fed"]);
  if (ledger.unmonitored)
    out.push([count(ledger.unmonitored) + " unmeasured", counted(ledger.unmonitored, "machine") + " keep no monitor; " + WORDS.drawingNow + " charges them in full"]);
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

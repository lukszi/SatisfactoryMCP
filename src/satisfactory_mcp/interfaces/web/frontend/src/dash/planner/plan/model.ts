/* The plan page's pure parts: which tab a bare address lands on, the build rows' words and
 * order, the balance groups and the timeline's days. docs/frontend_kit.md, "Plan page". */

import { formatNumber } from "../../../kit/format";
import { dayLabel, type Built } from "../plans/model";

import type { SolveRow } from "../../../api/shapes";
import type { ItemBalance } from "../../prodgraph/balance";
import type { PlanTab } from "../routes";

/** The tab for the plan's current step: no site → machines; built → flows; else to build. */
export function landingTab(sited: boolean, factory: string, built: Built): PlanTab {
  if (!sited && !factory) return "machines";
  if (built.kind === "figure" && built.done) return "flows";
  return "build";
}

/** "8 × 100%", and "+ 1 × 62.5%" for a last machine on its own clock. */
export function clockWords(row: Pick<SolveRow, "machines" | "clock" | "last_clock">): [string, string] {
  const pct = (f: number) => formatNumber(f * 100, 1) + "%";
  if (row.last_clock === null || row.last_clock === undefined) return [row.machines + " × " + pct(row.clock), ""];
  if (row.machines === 1) return ["1 × " + pct(row.last_clock), ""];
  return [row.machines - 1 + " × 100%", "+ 1 × " + pct(row.last_clock)];
}

export type BuildSort = "chain" | "building" | "machines" | "power";

export const BUILD_SORTS: [BuildSort, string][] = [
  ["chain", "chain order"],
  ["building", "building"],
  ["machines", "machines"],
  ["power", "power"],
];

/** Rows in the chosen order; chain order comes back grouped by step (`depth`). */
export function orderRows(rows: SolveRow[], by: BuildSort): { step: number | null; rows: SolveRow[] }[] {
  if (by !== "chain") {
    const out = rows.slice();
    if (by === "building") out.sort((a, b) => a.building.localeCompare(b.building) || a.recipe.localeCompare(b.recipe));
    else if (by === "machines") out.sort((a, b) => b.machines - a.machines);
    else out.sort((a, b) => a.mw - b.mw);
    return [{ step: null, rows: out }];
  }
  const steps: Record<number, SolveRow[]> = {};
  rows.forEach((r) => (steps[r.depth] = steps[r.depth] || []).push(r));
  return Object.keys(steps)
    .map(Number)
    .sort((a, b) => a - b)
    .map((d) => ({ step: d, rows: steps[d]! }));
}

export type BalanceKind = "in" | "out" | "leftover" | "internal";

export const BALANCE_KINDS: BalanceKind[] = ["in", "out", "leftover", "internal"];

export function balanceKind(b: ItemBalance): BalanceKind {
  if (b.supplied > 0) return "in";
  if (b.exported > 0) return "out";
  if (b.surplus > 0) return "leftover";
  return "internal";
}

/** Rows by the day of their `ts`, newest day first as given. */
export function byDayOf<T extends { ts: number }>(rows: T[], nowMs: number): { day: string; rows: T[] }[] {
  const out: { day: string; rows: T[] }[] = [];
  rows.forEach(function (row) {
    const day = dayLabel(row.ts, nowMs);
    const last = out[out.length - 1];
    if (last && last.day === day) last.rows.push(row);
    else out.push({ day, rows: [row] });
  });
  return out;
}

/* What the Overview's blocks show, worked out from the reads: factory cells worst first, the
 * "do next" problems across power and factories, the gate and plans as held-of-owed figures.
 * Pure. docs/frontend_kit.md, "Overview". */

import { WORDS } from "../../kit/words";
import { issueGroups } from "../machine-health";
import { actionTone, BLOCKED } from "../machine-states";
import { factoryDash } from "../factories/address";
import { problemsOf } from "../power/model";

import type { CircuitsResponse, FactoryHealthRow, GateView, PlanBuiltRow, PlanIndexRow } from "../../api/shapes";

/** Rows the "do next" block and the power block show before "+N more". */
export const SHOWN = 3;

function toneRank(row: FactoryHealthRow): number {
  const tone = actionTone(row.states);
  return tone === "bad" ? 0 : tone === "blocked" ? 1 : 2;
}

/** Need action first (blocked after the rest), most machines first, then lowest uptime. */
export function cellOrder(rows: FactoryHealthRow[]): FactoryHealthRow[] {
  return rows.slice().sort((a, b) => toneRank(a) - toneRank(b) || b.actionable - a.actionable || (a.uptime ?? 2) - (b.uptime ?? 2) || a.name.localeCompare(b.name));
}

export function machineTotals(rows: FactoryHealthRow[]): { machines: number; need: number } {
  return rows.reduce((t, row) => ({ machines: t.machines + row.machines, need: t.need + row.actionable }), { machines: 0, need: 0 });
}

/** One "do next" row: what is wrong, with which building, where, how many. */
export interface Todo {
  rank: number;
  tone: "bad" | "blocked";
  icon: string | null;
  what: string;
  sub: string;
  where: string;
  whereDash: string;
  n: number;
  /** The page that lists it in full. */
  dash: string;
  power: boolean;
}

/* A power problem goes to its factory's power aspect, else its circuit, else Power. */
function powerDash(whereDash: string): string {
  if (whereDash.indexOf("factories/") === 0) return whereDash + "/power";
  return whereDash || "power";
}

/** Power problems that stop machines first, then machines needing action (blocked last), with
 *  starved generators between; most machines first within a rank. */
export function todoOf(circuits: CircuitsResponse | null, rows: FactoryHealthRow[] | null): Todo[] {
  const out: Todo[] = [];
  if (circuits) {
    problemsOf(circuits)
      .filter((p) => !p.fine)
      .forEach((p) =>
        out.push({ rank: p.kind === WORDS.starvedGenerator ? 2 : 0, tone: "bad", icon: p.icon, what: p.kind, sub: p.building, where: p.where.where, whereDash: p.where.dash, n: p.refs.length, dash: powerDash(p.where.dash), power: true })
      );
  }
  if (rows) {
    issueGroups(rows).forEach(function (g) {
      const blocked = g.state === BLOCKED;
      out.push({ rank: blocked ? 3 : 1, tone: blocked ? "blocked" : "bad", icon: null, what: g.state, sub: g.what + (g.cause ? " · " + g.cause : ""), where: g.factory, whereDash: factoryDash(g.factory, ""), n: g.issues.length, dash: factoryDash(g.factory, "machines"), power: false });
    });
  }
  return out.sort((a, b) => a.rank - b.rank || b.n - a.n);
}

/** Where "+N more" goes: Factories when a factory row is left out, else Power. */
export function moreDash(rest: Todo[]): string {
  return rest.some((t) => !t.power) ? "factories" : "power";
}

export interface Tally {
  done: number;
  of: number;
}

/** The gate's owed parts as one figure: held (up to what is owed) of owed. */
export function gateTally(view: GateView): Tally {
  return view.parts.reduce((t, p) => ({ done: t.done + Math.min(p.held, p.owed), of: t.of + p.owed }), { done: 0, of: 0 });
}

/** Machines built of planned, over the plans whose built figure counts machines at this version. */
export function plansTally(rows: PlanIndexRow[], built: Record<string, PlanBuiltRow>): Tally {
  return rows.reduce(
    function (t, row) {
      const b = built[row.key];
      if (!b || b.rev !== row.rev || b.figure === "?" || b.built === null) return t;
      return { done: t.done + Math.min(b.built, b.total), of: t.of + b.total };
    },
    { done: 0, of: 0 }
  );
}

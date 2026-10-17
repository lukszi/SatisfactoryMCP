/* The Planner page's pure parts: what a plan card shows and how cards sort, and the activity
 * timeline's runs and day groups. docs/frontend_kit.md, "Planner". */

import type { ActivityRow, PlanBuiltRow, PlanIndexRow } from "../../../api/shapes";

export type PlanSort = "last" | "name" | "built";

export const SORTS: [PlanSort, string][] = [
  ["last", "last change"],
  ["name", "name"],
  ["built", "built"],
];

export interface ExportItem {
  name: string;
  rate: number | null;
}

/** What the plan exports: the rated items first, then the rest; "MW" is not an item. */
export function exportItems(row: PlanIndexRow): ExportItem[] {
  const out: ExportItem[] = Object.keys(row.rates).map((name) => ({ name, rate: row.rates[name]! }));
  row.exports.forEach(function (name) {
    if (name !== "MW" && !(name in row.rates)) out.push({ name, rate: null });
  });
  if (!out.length && row.target_item) out.push({ name: row.target_item, rate: null });
  return out;
}

/** The item the card's icon defaults to: the first export. */
export function productItem(row: PlanIndexRow): string | null {
  return exportItems(row)[0]?.name ?? null;
}

/** The plan's icon: the one chosen ("" none), else the first export's item. */
export function planIcon(row: PlanIndexRow, itemClass: (name: string) => string | null): string | null {
  if (row.icon !== null) return row.icon || null;
  const item = productItem(row);
  return item ? itemClass(item) : null;
}

/** A factory name the plan is tied to, or "" for the automatic, world, none and cluster scopes. */
export function factoryOf(row: PlanIndexRow): string {
  const f = row.factory || "";
  return !f || f[0] === "/" || f.indexOf("cluster:") === 0 ? "" : f;
}

export type Built =
  | { kind: "pending" }
  | { kind: "nosite" }
  | { kind: "ask"; text: string }
  | { kind: "nosolve"; text: string }
  | { kind: "figure"; figure: string; pct: number; percent: number | null; done: boolean; text: string };

/** The card's built line; `built` is the row of /api/plan/built for this version, if any. */
export function builtOf(row: PlanIndexRow, built: PlanBuiltRow | undefined): Built {
  if (!built || built.rev !== row.rev) return { kind: "pending" };
  if (built.figure === "?") return built.mode ? { kind: "ask", text: built.text } : { kind: "nosolve", text: built.text };
  if (built.built === null) return row.sited ? { kind: "ask", text: built.text } : { kind: "nosite" };
  const pct = built.percent ?? (built.total ? (100 * built.built) / built.total : 0);
  return { kind: "figure", figure: built.figure, pct, percent: built.percent, done: built.total > 0 && built.built >= built.total, text: built.text };
}

/** Built share for sorting: unplaced plans last. */
export function builtShare(row: PlanIndexRow, built: PlanBuiltRow | undefined): number {
  const b = builtOf(row, built);
  return b.kind === "figure" ? b.pct : -1;
}

export interface Status {
  text: string;
  tone: "mid" | "muted";
  title: string;
}

/** At most one chip: what moved under the plan wins over "done", which stays uncoloured. */
export function statusOf(row: PlanIndexRow, built: PlanBuiltRow | undefined): Status | null {
  if (row.status.length) return { text: row.status[0]!, tone: "mid", title: row.status.join(" · ") };
  const b = builtOf(row, built);
  if (b.kind === "figure" && b.done) return { text: "done", tone: "muted", title: "every planned machine stands" };
  return null;
}

export function sortPlans(rows: PlanIndexRow[], built: Record<string, PlanBuiltRow>, by: PlanSort): PlanIndexRow[] {
  const out = rows.slice();
  if (by === "name") out.sort((a, b) => a.name.localeCompare(b.name));
  else if (by === "built") out.sort((a, b) => builtShare(b, built[b.key]) - builtShare(a, built[a.key]) || a.name.localeCompare(b.name));
  else out.sort((a, b) => b.last.ts - a.last.ts);
  return out;
}

/* ------------------------------------------------------------------ activity */

export type Who = "all" | "you" | "chat";

export function whoOf(row: ActivityRow): "you" | "chat" | "other" {
  return row.actor.kind === "page" ? "you" : row.actor.kind === "chat" ? "chat" : "other";
}

function sameRun(a: ActivityRow, b: ActivityRow): boolean {
  if (a.kind !== b.kind || a.actor.kind !== b.actor.kind) return false;
  if (a.kind === "world.find") return a.actor.pid === b.actor.pid;
  return a.kind === "plan.view" && a.plan === b.plan && a.text === b.text;
}

/** Newest first, the given filter applied, repeated finds and views folded into one line. */
export function activityLines(entries: ActivityRow[], who: Who): ActivityRow[] {
  const out: ActivityRow[] = [];
  entries
    .filter((row) => who === "all" || whoOf(row) === who)
    .slice()
    .reverse()
    .forEach(function (row) {
      const last = out[out.length - 1];
      if (last && sameRun(last, row)) out[out.length - 1] = { ...last, count: last.count + row.count };
      else out.push(row);
    });
  return out;
}

function dayStart(ms: number): number {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** "today", "yesterday", else the weekday and date in the browser's zone. */
export function dayLabel(ts: number, nowMs: number): string {
  const days = Math.round((dayStart(nowMs) - dayStart(ts * 1000)) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return new Date(ts * 1000).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

export function byDay(lines: ActivityRow[], nowMs: number): { day: string; rows: ActivityRow[] }[] {
  const out: { day: string; rows: ActivityRow[] }[] = [];
  lines.forEach(function (row) {
    const day = dayLabel(row.ts, nowMs);
    const last = out[out.length - 1];
    if (last && last.day === day) last.rows.push(row);
    else out.push({ day, rows: [row] });
  });
  return out;
}

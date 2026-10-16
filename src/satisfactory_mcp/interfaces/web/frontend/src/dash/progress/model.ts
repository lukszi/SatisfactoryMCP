/* The Progress page's pure parts: its address, the roadmap's stops, the step and buy wording,
 * the alternates' groups. docs/frontend_kit.md, "Progress". */

import { formatNumber } from "../../kit/format";
import { slug } from "../items/address";

import type { AlternateRow, MamRow, MilestoneRow, NextStep } from "../../api/shapes";

/** Section keys in page order; `progress/<key>` scrolls to one. */
export const SECTIONS = ["gate", "next", "alternates", "buy", "reference"] as const;
export type Section = (typeof SECTIONS)[number];

export const HORIZONS: [string, string][] = [
  ["10", "10 h"],
  ["20", "20 h"],
  ["50", "50 h"],
];
export const DEFAULT_HORIZON = "20";

export type AltSort = "machines" | "power" | "raw";
export const ALT_SORTS: [AltSort, string][] = [
  ["machines", "machines"],
  ["power", "power"],
  ["raw", "raw"],
];

/** Rows per alternates group. */
export const ALT_ROWS = 5;

export function progressSection(subject: string): Section | "" {
  const s = subject.split(/[/?]/)[0] || "";
  return (SECTIONS as readonly string[]).indexOf(s) >= 0 ? (s as Section) : "";
}

const MOVED: Record<string, string> = {
  milestones: "progress/reference",
  mam: "progress/reference",
  elevator: "progress/gate",
  drives: "progress/alternates",
  shards: "items?view=held&item=power-shard",
  sloops: "items?view=held&item=somersloop",
};

/** Where an old Progress section lands: `progress/<old>` or `progress-<old>`; null when
 *  the address is current. */
export function legacyProgressDash(head: string, subject: string): string | null {
  if (head === "progress") return MOVED[subject.split(/[/?]/)[0] || ""] || null;
  if (head.indexOf("progress-") === 0) {
    const key = head.slice(9);
    return MOVED[key] || ((SECTIONS as readonly string[]).indexOf(key) >= 0 ? "progress/" + key : "progress");
  }
  return null;
}

/** The highest HUB tier each delivered phase opens (game 1.0): phase N opens tiers up to it. */
const TIERS_OPENED = [2, 4, 6, 8, 9];

export function openingPhase(tier: number): number {
  const i = TIERS_OPENED.findIndex((top) => top >= tier);
  return i < 0 ? TIERS_OPENED.length - 1 : i;
}

export function phaseNumber(raw: string | null | undefined): number | null {
  const m = /_(\d+)$/.exec(raw || "");
  return m ? Number(m[1]) : null;
}

export interface TierSeg {
  tier: number;
  done: number;
  total: number;
}

export interface Stop {
  phase: number;
  state: "done" | "on" | "ahead";
  tiers: TierSeg[];
}

export interface Road {
  stops: Stop[];
  /** Phases past the target folded into "+N later" while spoilers are off. */
  later: number;
  delivered: number;
  target: number | null;
}

/** Start (phase 0) and every phase up to the last shown, each with the tiers it opens. */
export function roadOf(delivered: number, target: number | null, total: number, tiers: TierSeg[], spoilers: boolean): Road {
  const last = spoilers || target === null ? Math.max(total, delivered) : Math.min(total, target);
  const stops: Stop[] = [];
  for (let p = 0; p <= last; p++) {
    stops.push({
      phase: p,
      state: p <= delivered ? "done" : p === target ? "on" : "ahead",
      tiers: tiers.filter((t) => openingPhase(t.tier) === p),
    });
  }
  return { stops, later: Math.max(0, total - last), delivered, target };
}

export function hoursMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h >= 100) return formatNumber(h) + " h";
  return (h ? h + " h" : "") + (m || !h ? (h ? " " : "") + m + " min" : "");
}

/** How long `rate` per minute takes to make `short`: "covered", a time, or null with no rate. */
export function coverTime(short: number, rate: number): string | null {
  if (short <= 0) return "covered";
  if (rate <= 0) return null;
  return hoursMinutes(short / rate);
}

/** A rate per minute at a readable precision. */
export function rate1(v: number): string {
  if (v >= 10) return formatNumber(Math.round(v));
  if (v >= 0.1) return formatNumber(Math.round(v * 10) / 10);
  return formatNumber(Math.round(v * 100) / 100);
}

export interface StepWords {
  what: string;
  tag: string;
  why: string;
  go: string;
}

/** One step's line, its reason chip, its tooltip and its link word. */
export function stepWords(s: NextStep): StepWords {
  const parts = s.parts.join(", ");
  if (s.kind === "unlock") {
    const short = s.short.map((x) => formatNumber(x.short) + " " + x.name).join(", ");
    const tag = s.after.length ? "after " + s.after.join(", ") : s.status === "READY" ? "affordable" : short ? "short " + (s.short.length > 1 ? s.short.length + " items" : short) : "";
    const why = (s.status === "READY" ? "affordable now" : short ? "short " + short : "") + "; opens " + parts;
    return { what: "unlock " + s.name, tag, why, go: s.system || "unlock" };
  }
  if (s.kind === "make") {
    const n = s.for_unlocks.length;
    const tag = n > 1 ? "for " + n + " unlocks" : s.inputs_held ? "inputs held" : "nothing makes it";
    const why = "for " + s.for_unlocks.join(", ") + "; nothing makes it" + (s.inputs_held ? "; inputs held" : "") + "; opens " + parts;
    return { what: "make " + formatNumber(s.amount) + " " + s.name, tag, why, go: "plan it" };
  }
  if (s.kind === "grow") {
    const tag = rate1(s.made_per_min) + " of " + rate1(s.per_min) + " /min";
    return { what: "make more " + s.name, tag, why: "made at " + rate1(s.made_per_min) + " /min, " + rate1(s.per_min) + " /min delivers it in time", go: "plan it" };
  }
  return { what: "build " + (/^[aeiou]/i.test(s.name) ? "an " : "a ") + s.name + " line", tag: "nothing makes it", why: "unlocked, nothing makes it; " + rate1(s.per_min) + " /min delivers it in time", go: "plan it" };
}

/** The Planner address that opens the new-plan card filled with this step's item and rate. */
export function planHref(s: NextStep, horizonH: number): string {
  const rate = s.per_min > 0 ? s.per_min : s.amount / (horizonH * 60);
  return "planner?item=" + encodeURIComponent(s.name) + "&rate=" + encodeURIComponent(String(Math.round(rate * 1000) / 1000 || 1));
}

const SORT_KEY: Record<AltSort, (r: AlternateRow) => number> = {
  machines: (r) => r.machines,
  power: (r) => r.mw,
  raw: (r) => r.raw,
};

/** Drives first (a choice is open there), then not on a drive yet, then unlocked already;
 *  each sorted by the chosen cut, at most `ALT_ROWS` rows, rows that cut nothing of it left out. */
export function altGroups(rows: AlternateRow[], sort: AltSort): [string, AlternateRow[]][] {
  const key = SORT_KEY[sort];
  const groups: [string, string][] = [
    ["drive", "offered by a drive on hand"],
    ["pool", "not on a drive yet"],
    ["unlocked", "unlocked already"],
  ];
  return groups
    .map(([source, label]): [string, AlternateRow[]] => [
      label,
      rows
        .filter((r) => r.source === source && key(r) < 0)
        .sort((a, b) => key(a) - key(b) || a.name.localeCompare(b.name))
        .slice(0, ALT_ROWS),
    ])
    .filter((g) => g[1].length > 0);
}

export interface Buy {
  name: string;
  system: "milestone" | "MAM";
  group: string;
  unlocks: string[];
  /** The one item an "almost" row is short of. */
  short: { name: string; item: string; amount: number } | null;
  key: string;
}

function milestoneBuy(m: MilestoneRow): Buy {
  const s = m.short[0];
  return { name: m.name, system: "milestone", group: "tier " + m.tier, unlocks: m.unlock_names, short: m.status === "short" && s ? { name: s.name, item: s.item, amount: s.amount } : null, key: slug(m.name) };
}

function mamBuy(r: MamRow): Buy {
  const s = r.short[0];
  return { name: r.name, system: "MAM", group: (r.tree || "MAM").toLowerCase(), unlocks: r.unlock_names, short: r.status === "short" && s ? { name: s.name, item: s.item, amount: s.amount } : null, key: slug(r.name) };
}

/** Affordable now, and short of exactly one item: milestones in open tiers and MAM nodes in
 *  open trees, mixed, milestones first. */
export function buyLists(milestones: MilestoneRow[], mam: MamRow[]): { now: Buy[]; almost: Buy[] } {
  const open = milestones.filter((m) => m.opens_at === null && !m.blocked_by.length);
  const nodes = mam.filter((r) => !r.blocked_by.length);
  const now = [...open.filter((m) => m.status === "READY").map(milestoneBuy), ...nodes.filter((r) => r.status === "READY").map(mamBuy)];
  const almost = [...open.filter((m) => m.status === "short" && m.short.length === 1).map(milestoneBuy), ...nodes.filter((r) => r.status === "short" && r.short.length === 1).map(mamBuy)];
  return { now, almost };
}

export interface TreeRow {
  tree: string;
  done: number;
  total: number;
  open: string[];
  running: { name: string; pct: number | null } | null;
}

/** MAM trees with what is done, the nodes still open and the one running, by tree name. */
export function mamTrees(rows: MamRow[]): TreeRow[] {
  const by: Record<string, TreeRow> = {};
  rows.forEach(function (r) {
    const name = (r.tree || "other").toLowerCase();
    const t = (by[name] = by[name] || { tree: name, done: 0, total: 0, open: [], running: null });
    t.total += 1;
    if (r.status === "DONE") t.done += 1;
    else if (r.status === "RUNNING") t.running = { name: r.name, pct: r.elapsed_pct };
    else t.open.push(r.name);
  });
  return Object.keys(by)
    .sort()
    .map((k) => by[k]!);
}

/** Owed parts still short, of all owed parts. */
export function shortCount(parts: { short: number }[]): number {
  return parts.filter((p) => p.short > 0).length;
}

/* Where the player put nodes: shown on the page at once and stored as `place` ops, one per node,
 * which never conflict. The plan state's `node_positions` is the stored set; the page's own
 * places only bridge the time until a reply carries them. */

import { applyOps } from "../writes";

import type { Op } from "../state";

export type Positions = Record<string, [number, number]>;

interface Pending {
  at: [number, number];
  /** The plan's head when the place was made; any later head has seen it. */
  rev: number;
}

const local: Record<string, Record<string, Pending>> = {};

export function placeOp(id: string, at: [number, number] | null): Op {
  return { op: "place", node: id, value: at ? [Math.round(at[0]), Math.round(at[1])] : null };
}

export function resetLayoutOp(): Op {
  return { op: "reset_layout" };
}

/** The stored places with this page's newer ones laid over them. */
export function positionsFor(plan: string, stored: Positions): Positions {
  const out: Positions = { ...stored };
  const mine = local[plan] || {};
  Object.keys(mine).forEach(function (id) {
    out[id] = mine[id]!.at;
  });
  return out;
}

/** Drops the page's places a newer head has seen, so an undo there shows. */
export function settle(plan: string, head: number): void {
  const mine = local[plan];
  if (!mine) return;
  Object.keys(mine).forEach(function (id) {
    if (head > mine[id]!.rev) delete mine[id];
  });
}

export function moveNode(plan: string, head: number, id: string, at: [number, number], serverStores: boolean): void {
  const rounded: [number, number] = [Math.round(at[0]), Math.round(at[1])];
  (local[plan] = local[plan] || {})[id] = { at: rounded, rev: serverStores ? head : Infinity };
  if (serverStores) applyOps([placeOp(id, rounded)]);
}

export function resetLayout(plan: string, serverStores: boolean): void {
  delete local[plan];
  if (serverStores) applyOps([resetLayoutOp()]);
}

export function hasPlaced(plan: string, stored: Positions): boolean {
  return Object.keys(positionsFor(plan, stored)).length > 0;
}

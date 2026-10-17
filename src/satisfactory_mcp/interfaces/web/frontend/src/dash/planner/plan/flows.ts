/* The flows tab: the factory page's production graph, drawn from the solve's graph (inputs
 * dashed left, recipe cards, exports and leftovers right), cards placed by dragging as plan
 * ops; the balance per item under it. A card click opens machines with its row picked. */

import { go } from "../../../app/nav";
import { make } from "../../../kit/dom";
import { formatNumber } from "../../../kit/format";
import { html, type Safe } from "../../../kit/html";
import { group, list } from "../../../kit/ui/entry";
import { loadingLine } from "../../../kit/ui/states";
import { dashH, emptyLine } from "../../../kit/ui/text";
import { WORDS } from "../../../kit/words";
import { processPinsByRecipe } from "../../../chat/pins";
import { GRAPH_HINT } from "../../graph-frame";
import { itemBalances, type ItemBalance } from "../../prodgraph/balance";
import { canvasFrame, newCanvas, settleFrame, useView } from "../../prodgraph/canvas";
import { nodeForPick, planGraphOf, readPositions } from "../graph/model";
import { hasPlaced, moveNode, positionsFor, resetLayout, settle } from "../graph/positions";
import { planDash } from "../routes";
import { bench, changed } from "../state";
import { BALANCE_KINDS, balanceKind } from "./model";
import { itemIcon, lookupOf, rateUnit, sgn } from "./parts";
import { pickRow } from "./machines";

import type { CanvasHost } from "../../prodgraph/canvas";
import type { NodeMarks } from "../../prodgraph/draw";
import type { ProdGraph, ProdNode } from "../../prodgraph/model";

const canvas = newCanvas("card");

/** Whether the server keeps node places: the plan state then carries them. */
function serverStores(): boolean {
  return !!bench.plan && "node_positions" in bench.plan;
}

function stored(graph: ProdGraph): Record<string, [number, number]> {
  return serverStores() ? readPositions(bench.plan!.node_positions) : graph.positions;
}

function marks(graph: ProdGraph): NodeMarks {
  const pins = processPinsByRecipe(bench.key);
  const out: NodeMarks = { badges: {}, flash: [], tones: {} };
  graph.nodes.forEach(function (n) {
    const words: string[] = [];
    if (n.row && bench.chatChangedRows[n.row]) words.push(WORDS.actorChat);
    if (n.recipeId && pins[n.recipeId]) words.push(pins[n.recipeId]!.id);
    if (words.length) out.badges[n.id] = words;
  });
  return out;
}

function host(graph: ProdGraph): CanvasHost {
  return {
    key: bench.key,
    positions: () => positionsFor(bench.key, stored(graph)),
    marks: () => marks(graph),
    pick: function (node: ProdNode) {
      if (node.kind !== "process" || !node.row) return;
      if (bench.picked !== node.row) pickRow(node.row);
      go(planDash(bench.key, "machines"));
    },
    clear: function () {},
    moved: (id, at) => moveNode(bench.key, bench.plan ? bench.plan.rev : 0, id, at, serverStores()),
    changed: changed,
  };
}

function balanceRow(b: ItemBalance): Safe {
  const look = lookupOf(bench.result);
  const unit = rateUnit(look, b.name);
  const num = (v: number, k: string) => html`<div class="metric">${v ? html`<b>${formatNumber(v, 1)}</b>` : html`<em class="na">–</em>`}<span>${k}</span></div>`;
  return html`<div class="entry" data-item="${b.name}" tabindex="0">${itemIcon(look, b.name)}<div class="id"><span class="name">${b.name}</span></div>${num(b.produced, "made")}${num(b.consumed, "used")}<div class="metric"><b>${sgn(b.produced - b.consumed, 1)}</b><span>${unit}</span></div></div>`;
}

function balance(graph: ProdGraph): Safe {
  const rows = itemBalances(graph);
  const inner = BALANCE_KINDS.map(function (kind) {
    const xs = rows.filter((b) => balanceKind(b) === kind).sort((a, b) => Math.abs(b.produced - b.consumed) - Math.abs(a.produced - a.consumed) || a.name.localeCompare(b.name));
    return xs.length ? html`${group(kind, xs.length)}${xs.map(balanceRow)}` : "";
  });
  return html`${dashH("balance per item", rows.length)}${list("fflow", "stacked", inner)}`;
}

export function flowsTab(): Safe {
  const data = bench.result;
  if (!data) return bench.solveError ? emptyLine("the result could not be read", bench.solveError) : loadingLine("the result");
  if (!data.feasible) return emptyLine("not solvable: " + (data.cause || data.headline), "the machines tab shows the last solvable version");
  const graph = planGraphOf(data);
  if (!graph.nodes.length) return emptyLine("nothing to draw");
  const reset = hasPlaced(bench.key, stored(graph)) && !bench.gone ? html`<button type="button" class="btn" data-pf="reset" title="put every moved card back where the layout puts it">reset layout</button>` : "";
  return html`${dashH("production graph")}${reset}<div class="fgslot"></div>${balance(graph)}`;
}

/** Puts the graph into its slot once the tab's markup is mounted. */
export function mountFlows(root: HTMLElement): void {
  const slot = root.querySelector<HTMLElement>(".fgslot");
  const data = bench.result;
  if (!slot || !data) return;
  if (bench.plan) settle(bench.key, bench.plan.rev);
  const graph = planGraphOf(data);
  useView(canvas, graph);
  const box = make("div", "fgraph");
  const wrap = canvasFrame(canvas, host(graph));
  box.appendChild(wrap);
  box.appendChild(make("div", "fg-key", "dashed: input · dashed right: leftover · line width: rate · drag a card to place it · " + GRAPH_HINT));
  slot.appendChild(box);
  const picked = nodeForPick(graph, bench.picked);
  settleFrame(canvas, wrap, picked ? picked.id : "");
}

/** Lights an item's lines, boxes, makers and users in the graph, and its balance row. */
export function lightItem(root: HTMLElement, item: string | null): void {
  const svg = root.querySelector<SVGSVGElement>(".fgraph svg.graph");
  root.querySelectorAll(".pgx .hl").forEach((el) => el.classList.remove("hl"));
  if (svg) svg.classList.toggle("has-hl", !!item);
  const data = bench.result;
  if (!item || !data) return;
  const ends: Record<string, boolean> = {};
  planGraphOf(data).edges.forEach(function (e) {
    if (e.itemName !== item) return;
    ends[e.source] = true;
    ends[e.target] = true;
  });
  const q = (s: string) => s.replace(/["\\]/g, "\\$&");
  root.querySelectorAll('.pgx [data-item="' + q(item) + '"]').forEach((el) => el.classList.add("hl"));
  Object.keys(ends).forEach((id) => root.querySelector('.pgx [data-node="' + q(id) + '"]')?.classList.add("hl"));
}

export function flowsClick(target: Element): boolean {
  if (!target.closest('[data-pf="reset"]')) return false;
  resetLayout(bench.key, serverStores());
  canvas.drawn = "";
  changed();
  return true;
}

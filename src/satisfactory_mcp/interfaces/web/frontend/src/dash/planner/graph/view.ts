/* The result's graph tab: the plan graph laid out once per distinct shape, redrawn when the
 * solve or the placed nodes change, with the picked node's card and the lists under it. The
 * frame and its scroll survive the panel's redraws. See docs/planner_vision.md §4.2. */

import { button } from "../../../kit/dashkit";
import { WORDS } from "../../../kit/words";
import { processPinsByRecipe } from "../../../chat/pins";
import { make } from "../../../kit/dom";
import { graphCardFrame, GRAPH_HINT, zoomableFrame } from "../../graph-frame";
import { bench, changed } from "../state";
import { drawPlanSvg, layoutInput } from "./draw";
import { showChain, wireGround, wireNodes } from "./interact";
import { computeLayout, orderOf, placeNodes } from "./layout";
import { elkGraph } from "./layout-input";
import { renderLists } from "./lists";
import { nodeForPick, planGraphOf, readPositions } from "./model";
import { nodeCard } from "./node-card";
import { hasPlaced, moveNode, positionsFor, resetLayout, settle } from "./positions";

import type { Positions } from "./positions";

import type { FocusSelection, SolveResponse } from "../../../api/shapes";
import type { FrameView } from "../../frame-fit";
import type { NodeMarks } from "./draw";
import type { LiveGraph } from "./interact";
import type { Layout, LayoutIn } from "./layout-input";
import type { PlanGraphView, PlanNode } from "./model";

interface GraphCache {
  data: SolveResponse | null;
  view: PlanGraphView | null;
  serial: number;
  sig: string;
  base: Layout | null;
  pending: string;
  drawn: string;
  live: LiveGraph | null;
  wrap: HTMLElement | null;
  x: number;
  y: number;
  zoom: FrameView;
}

const cache: GraphCache = { data: null, view: null, serial: 0, sig: "", base: null, pending: "", drawn: "", live: null, wrap: null, x: 0, y: 0, zoom: { zoom: 1 } };
const orders: Record<string, Record<string, number>> = {};
const flashed: Record<string, number> = {};
const FLASH_MS = 4000;
let select: (s: FocusSelection) => void = function () {};

/** Everything the layout depends on but the previous order. */
export function shapeKey(input: LayoutIn): string {
  return JSON.stringify([
    input.nodes.map(function (n) {
      return [n.id, n.rank, n.w, n.h, n.portY, n.below ? n.below.w : 0];
    }),
    input.edges.map(function (e) {
      return [e.id, e.source, e.target, e.labelW];
    }),
  ]);
}

/** The chat-changed rows still inside their flash window, counted from when each was first seen. */
function flashingRows(): string[] {
  const now = Date.now();
  Object.keys(flashed).forEach(function (id) {
    if (!bench.chatChangedRows[id]) delete flashed[id];
  });
  Object.keys(bench.chatChangedRows).forEach(function (id) {
    if (!flashed[id]) flashed[id] = now;
  });
  return Object.keys(flashed).filter(function (id) {
    return now - flashed[id]! < FLASH_MS;
  });
}

function nodeMarks(view: PlanGraphView): NodeMarks {
  const pins = processPinsByRecipe(bench.key);
  const rows = flashingRows();
  const marks: NodeMarks = { badges: {}, flash: [] };
  view.nodes.forEach(function (n) {
    if (!n.row) return;
    const words: string[] = [];
    if (bench.chatChangedRows[n.row]) words.push(WORDS.actorChat);
    if (n.recipeId && pins[n.recipeId]) words.push(pins[n.recipeId]!.id);
    if (words.length) marks.badges[n.id] = words;
    if (rows.indexOf(n.row) >= 0) marks.flash.push(n.id);
  });
  return marks;
}

/** Whether the server keeps node places: its plan state then carries them. */
function serverStores(): boolean {
  return !!bench.plan && "node_positions" in bench.plan;
}

/** The stored places: the plan state's, which a layout-only version updates without a new solve. */
function stored(view: PlanGraphView): Positions {
  return serverStores() ? readPositions(bench.plan!.node_positions) : view.positions;
}

function pick(node: PlanNode): void {
  bench.picked = node.row || node.id;
  if (node.row && cache.data) {
    const row = cache.data.rows.filter(function (r) {
      return r.id === node.row;
    })[0];
    if (row) {
      select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
      return;
    }
  }
  select({ kind: "item", label: node.itemName || node.label, ref: node.itemId || "" });
}

function clear(): void {
  if (!bench.picked) return;
  bench.picked = "";
  changed();
}

function wrapper(): HTMLElement {
  if (cache.wrap) return cache.wrap;
  const wrap = make("div", "pg-wrap");
  wireGround(wrap, clear);
  wrap.addEventListener(
    "scroll",
    function (event) {
      const frame = event.target as HTMLElement;
      if (!frame.classList || !frame.classList.contains("graph-frame")) return;
      cache.x = frame.scrollLeft;
      cache.y = frame.scrollTop;
    },
    true
  );
  cache.wrap = wrap;
  return wrap;
}

function draw(view: PlanGraphView, input: LayoutIn, key: string, marks: NodeMarks): void {
  const positions = positionsFor(bench.key, stored(view));
  const layout = placeNodes(input, cache.base!, positions);
  const handle = drawPlanSvg(view, layout, marks, redrawAll);
  const live: LiveGraph = { handle: handle, view: view, input: input, layout: layout };
  const frame = zoomableFrame(handle.root, layout.width, layout.height, { fitAll: true, view: cache.zoom });
  wrapper().replaceChildren(frame);
  wireNodes(live, cache.wrap!, {
    pick: pick,
    moved: function (id, at) {
      moveNode(bench.key, bench.plan ? bench.plan.rev : 0, id, at, serverStores());
      cache.drawn = drawKey(view, marks);
      changed();
    },
  });
  cache.live = live;
  cache.drawn = key;
}

/** Draws again even though nothing the draw key covers changed, e.g. once icons are known. */
function redrawAll(): void {
  cache.drawn = "";
  changed();
}

function layOut(input: LayoutIn, sig: string): void {
  if (cache.pending === sig) return;
  cache.pending = sig;
  const plan = bench.key;
  const t0 = performance.now();
  computeLayout(input, function () {
    return elkGraph(input);
  }).then(function (base) {
    if (cache.pending !== sig) return;
    cache.base = base;
    cache.sig = sig;
    cache.zoom = { zoom: 1 };
    cache.x = 0;
    cache.y = 0;
    orders[plan] = orderOf(base);
    if (import.meta.env.DEV) console.debug("plan graph laid out in " + Math.round(performance.now() - t0) + " ms, " + input.nodes.length + " nodes");
    changed();
  });
}

function drawKey(view: PlanGraphView, marks: NodeMarks): string {
  return cache.serial + "|" + JSON.stringify([positionsFor(bench.key, stored(view)), marks]);
}

function currentView(data: SolveResponse): PlanGraphView {
  if (cache.data !== data || !cache.view) {
    cache.data = data;
    cache.view = planGraphOf(data);
    cache.serial++;
  }
  return cache.view;
}

/** The graph's wrap, drawn or re-used; it keeps the last drawing while a new shape lays out. */
function graphFrame(view: PlanGraphView): HTMLElement {
  const input = layoutInput(view, orders[bench.key] || {});
  const sig = shapeKey(input);
  const marks = nodeMarks(view);
  const key = drawKey(view, marks);
  const wrap = wrapper();
  if (cache.base && cache.sig === sig) {
    if (cache.drawn !== key) {
      draw(view, input, key, marks);
      if (marks.flash.length) setTimeout(changed, FLASH_MS);
    }
  } else {
    layOut(input, sig);
    wrap.classList.add("pg-waiting");
    if (!cache.live) wrap.replaceChildren(make("p", "dash-note", "laying the graph out…"));
    return wrap;
  }
  wrap.classList.remove("pg-waiting");
  return wrap;
}

function tools(view: PlanGraphView): HTMLElement {
  const bar = make("div", "dash-acts pg-tools");
  if (hasPlaced(bench.key, stored(view)) && !bench.gone) {
    bar.appendChild(
      button(
        "reset layout",
        function () {
          resetLayout(bench.key, serverStores());
          cache.drawn = "";
          changed();
        },
        { title: "put every moved node back where the layout puts it" }
      )
    );
  }
  return bar;
}

function restoreView(wrap: HTMLElement): void {
  const frame = wrap.querySelector<HTMLElement>(".graph-frame");
  if (!frame) return;
  frame.scrollLeft = cache.x;
  frame.scrollTop = cache.y;
}

export function planGraphTab(parent: HTMLElement, data: SolveResponse, onSelect: (s: FocusSelection) => void): void {
  select = onSelect;
  if (bench.plan) settle(bench.key, bench.plan.rev);
  const card = graphCardFrame("production graph", true);
  parent.appendChild(card);
  const view = currentView(data);
  if (!view.nodes.length) {
    card.appendChild(make("p", "dash-note", "nothing to draw"));
    return;
  }
  card.appendChild(tools(view));
  const wrap = graphFrame(view);
  card.appendChild(wrap);
  card.appendChild(make("p", "dash-note", "click a node for its chain and card · drag a node to place it · " + GRAPH_HINT));
  restoreView(wrap);
  const picked = nodeForPick(view, bench.picked);
  if (cache.live) showChain(cache.live, picked ? picked.id : "");
  if (picked) nodeCard(parent, data, view, picked);
  renderLists(parent, data, view, changed);
}

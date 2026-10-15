/* The result's graph tab: the plan's production graph on the shared canvas (dash/prodgraph/),
 * with places stored as plan ops, chat and pin tags on its nodes, the picked node's card and the
 * lists under it. See docs/planner_vision.md §4.2. */

import { button } from "../../../kit/dashkit";
import { WORDS } from "../../../kit/words";
import { processPinsByRecipe } from "../../../chat/pins";
import { make } from "../../../kit/dom";
import { graphCardFrame, GRAPH_HINT } from "../../graph-frame";
import { canvasFrame, newCanvas, settleFrame, useView } from "../../prodgraph/canvas";
import { renderLists } from "../../prodgraph/lists";
import { powerTotals } from "../../prodgraph/balance";
import { bench, changed } from "../state";
import { nodeForPick, planGraphOf, readPositions } from "./model";
import { nodeCard } from "./node-card";
import { hasPlaced, moveNode, positionsFor, resetLayout, settle } from "./positions";

import type { Positions } from "./positions";

import type { FocusSelection, SolveResponse } from "../../../api/shapes";
import type { CanvasHost } from "../../prodgraph/canvas";
import type { NodeMarks } from "../../prodgraph/draw";
import type { PlanGraphView, PlanNode } from "./model";

const canvas = newCanvas();
let data: SolveResponse | null = null;
const flashed: Record<string, number> = {};
const FLASH_MS = 4000;
let select: (s: FocusSelection) => void = function () {};

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
  if (node.row && data) {
    const row = data.rows.filter(function (r) {
      return r.id === node.row;
    })[0];
    if (row) {
      select({ kind: "process", label: row.building + " · " + row.recipe, ref: row.recipe_id || row.recipe });
      return;
    }
  }
  select({ kind: "item", label: node.itemName || node.label, ref: node.itemId || "" });
}

function host(view: PlanGraphView): CanvasHost {
  return {
    key: bench.key,
    positions: function () {
      return positionsFor(bench.key, stored(view));
    },
    marks: function () {
      return nodeMarks(view);
    },
    pick: pick,
    clear: function () {
      if (!bench.picked) return;
      bench.picked = "";
      changed();
    },
    moved: function (id, at) {
      moveNode(bench.key, bench.plan ? bench.plan.rev : 0, id, at, serverStores());
    },
    changed: changed,
  };
}

function tools(view: PlanGraphView): HTMLElement {
  const bar = make("div", "dash-acts pg-tools");
  if (hasPlaced(bench.key, stored(view)) && !bench.gone) {
    bar.appendChild(
      button(
        "reset layout",
        function () {
          resetLayout(bench.key, serverStores());
          canvas.drawn = "";
          changed();
        },
        { title: "put every moved node back where the layout puts it" }
      )
    );
  }
  return bar;
}

function powerLine(result: SolveResponse, view: PlanGraphView): { draw: number; made: number; net: number } {
  const own = powerTotals(view);
  const draw = result.mw_draw ?? own.draw;
  const made = result.mw_generated ?? own.made;
  return { draw: draw, made: made, net: result.mw_net ?? made - draw };
}

export function planGraphTab(parent: HTMLElement, result: SolveResponse, onSelect: (s: FocusSelection) => void): void {
  select = onSelect;
  data = result;
  if (bench.plan) settle(bench.key, bench.plan.rev);
  const card = graphCardFrame("production graph", true);
  parent.appendChild(card);
  const view = planGraphOf(result);
  if (!view.nodes.length) {
    card.appendChild(make("p", "dash-note", "nothing to draw"));
    return;
  }
  useView(canvas, view);
  card.appendChild(tools(view));
  const wrap = canvasFrame(canvas, host(view), FLASH_MS);
  card.appendChild(wrap);
  card.appendChild(make("p", "dash-note", "click a node for its chain and card · drag a node to place it · " + GRAPH_HINT));
  const picked = nodeForPick(view, bench.picked);
  settleFrame(canvas, wrap, picked ? picked.id : "");
  if (picked) nodeCard(parent, result, view, picked);
  renderLists(parent, view, powerLine(result, view), changed);
}
